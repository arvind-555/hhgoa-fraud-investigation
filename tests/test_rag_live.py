"""Live GraphRAG + answer-file tests against FraudInvestigation (read-only except CASE-TEST- vertices, cleaned up). Run: python -m unittest tests.test_rag_live -v"""
import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from agent import answer_file as AF  # noqa: E402
from agent.case_writer import CaseWriter  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.graphio import GraphIO  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from agent.simulator import EvidenceSimulator  # noqa: E402
try:
    from calibration.runtime import Calibrator  # noqa: E402
except ImportError:                       # the calibration package is a separate component; the agent runs without a calibrator (probability stays null)
    Calibrator = None
from fraud_tools.qclient import QueryClient  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402

G = {"created": []}
RING = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"


def setUpModule():
    if not (ROOT / ".env").exists():
        raise unittest.SkipTest(".env missing")
    from common import build_master, load_closed
    try:
        G["c"] = QueryClient()
        G["io"] = GraphIO()
        G["c"].run("fi_text_chunks", as_of=10 ** 9, doc_type="policy", channel="", max_chunks=100)
    except Exception as e:
        raise unittest.SkipTest(f"graph/query not reachable: {e}")
    G["m"], G["cc"] = build_master(), load_closed()


def tearDownModule():
    for gid in G["created"]:
        try:
            G["io"].delete_test_case(gid)
        except Exception as e:
            print("cleanup failed", gid, e)


def chunks(as_of, dt="closed_case", channel=""):
    return G["c"].run("fi_text_chunks", as_of=as_of, doc_type=dt, channel=channel, max_chunks=8000)


class VisibilityAndDescribes(unittest.TestCase):
    def test_every_closed_case_chunk_is_linked_by_describes(self):
        r = chunks(10 ** 9)
        rows = r["S"]
        self.assertEqual(len(rows), 5565)
        self.assertTrue(all(x["case_id"] == x["source_ref"] and x["close_ep"] == x["valid_from_epoch"] for x in rows))
        cc = G["cc"].set_index("case_id")
        sample = rows[::200]
        self.assertTrue(all(x["close_ep"] == int(cc.loc[x["case_id"], "close_ep"]) for x in sample))

    def test_visibility_boundary_uses_close_epoch_not_open_epoch(self):
        cc = G["cc"]
        late = cc.sort_values("close_ep").iloc[1000]
        x = int(late["close_ep"])
        self.assertLess(int(late["open_ep"]), x)
        before = {r["case_id"] for r in chunks(x - 1)["S"]}
        at = {r["case_id"] for r in chunks(x)["S"]}
        self.assertNotIn(late["case_id"], before)                     # opened before as_of but not closed yet: invisible
        self.assertIn(late["case_id"], at)                            # close == as_of is visible

    def test_no_row_after_as_of_at_any_time(self):
        for as_of in (3_000_000, 6_500_000, 9_000_000):
            r = chunks(as_of)
            self.assertTrue(all(x["valid_from_epoch"] <= as_of and x["close_ep"] <= as_of for x in r["S"]))
            self.assertLessEqual(r["max_epoch_seen"], as_of)

    def test_channel_filter(self):
        r = chunks(10 ** 9, channel="in_person")
        self.assertTrue(r["S"] and all(x["channel"] == "in_person" for x in r["S"]))

    def test_no_benchmark_material_is_stored_in_the_graph(self):
        for dt in ("closed_case", "policy", "pattern", "format"):
            blob = " ".join(x["text"] + x["source_ref"] for x in chunks(10 ** 9, dt)["S"])
            self.assertNotRegex(blob, r"HHG-\d")
            self.assertNotIn("The 20 Cases", blob)


class LiveTools(unittest.TestCase):
    def test_find_similar_cases_is_visible_labelled_and_fast(self):
        m = G["m"]
        pick = m[(m["epoch"] > 8_000_000) & (m["epoch"] < 10_400_000)].sample(4, random_state=11)
        for _, r in pick.iterrows():
            as_of = int(r["epoch"])
            s = InvestigationSession(as_of, G["c"])
            t0 = time.perf_counter()
            out = s.find_similar_cases(str(int(r["TransactionID"])))
            dt = time.perf_counter() - t0
            prior = {c["case_id"] for c in s.find_prior_cases(r["card_id"])["cases"] if c["match_reasons"]}
            self.assertTrue(all(h["close_epoch"] <= as_of for h in out["hits"]))
            self.assertTrue(all((h["label"] == "linked") == (h["case_id"] in prior) for h in out["hits"]))
            self.assertLessEqual(len(out["hits"]), 10)
            self.assertNotRegex(json.dumps(out), r"HHG-\d|risk_score")
            self.assertLess(dt, 25)

    def test_similar_cases_at_an_early_time_only_sees_early_closures(self):
        m = G["m"]
        r = m[(m["epoch"] > 1_200_000) & (m["channel"] == "online")].iloc[0]
        out = InvestigationSession(int(r["epoch"]), G["c"]).find_similar_cases(str(int(r["TransactionID"])))
        self.assertLessEqual(max((h["close_epoch"] for h in out["hits"]), default=0), int(r["epoch"]))
        self.assertLess(out["n_visible_case_chunks"], 400)

    def test_retrieve_policy_by_exact_reference(self):
        s = InvestigationSession(12_000_000, G["c"])
        out = s.retrieve_policy(["R7", "R1", "3a"])
        self.assertEqual({c["source_ref"] for c in out["chunks"]}, {"policy:R7", "policy:R1", "policy:3a"})
        self.assertIn("merchant", next(c for c in out["chunks"] if c["source_ref"] == "policy:R7")["text"])
        self.assertEqual(out["missing"], [])


class LiveAgentAnswer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        m = G["m"]
        rg = m[m["dev"] == RING].sort_values(["epoch", "TransactionID"])
        seen, row = [], None
        for _, r in rg.iterrows():
            if r["customer_id"] not in seen:
                seen.append(r["customer_id"])
                if len(seen) == 30:
                    row = r
                    break
        cls.row, cls.as_of = row, int(row["epoch"])
        sim = EvidenceSimulator("live-answer-seed")
        cls.gid = "CASE-TEST-LIVE-ANS"
        G["created"].append(cls.gid)
        gw = ToolGateway(InvestigationSession(cls.as_of, G["c"]))
        cls.rec = Agent(gw, Trigger("TEST-LIVE-ANS", str(int(row["TransactionID"])), "risk_score"), responder=sim, calibrator=Calibrator(cls.as_of) if Calibrator else None,
                        writer=CaseWriter(cls.as_of, G["io"], simulator=sim)).run()
        cls.ans = AF.assemble(cls.rec)

    def test_graphrag_evidence_present(self):
        ev = self.ans["case"]["evidence"]
        self.assertTrue(any(e["source"] == "document" for e in ev))
        self.assertTrue(any(e["ref"] == "tool:find_similar_cases" for e in ev))

    def test_similar_prior_cases_are_visible_closed_cases(self):
        cc = G["cc"].set_index("case_id")
        for k in self.ans["case"]["similar_prior_cases"]:
            self.assertLessEqual(int(cc.loc[k, "close_ep"]), self.as_of)

    def test_answer_file_validates_against_the_live_graph(self):
        measured = {"tool_calls": self.rec["tool_calls"], "tokens": self.rec["tokens"], "latency_s": self.rec["latency_s"]}
        errs, warns = AF.validate_answer(self.ans, self.as_of, G["io"], measured)
        self.assertEqual(errs, [])
        self.assertEqual(len(warns), 1)
        self.assertTrue(self.ans["case"]["written_to_graph"])

    def test_case_vertex_stored_similar_case_edges(self):
        edges = G["io"].case_edge_counts(self.gid)
        self.assertEqual({e[1] for e in edges["SIMILAR_CASE"]}, set(self.rec["case"]["similar_prior_cases"]))
        methods = {json.loads(e[2]).get("method") for e in edges["SIMILAR_CASE"]}
        self.assertTrue(methods <= {"prior_cases_tool", "graphrag_bm25"})

    def test_answer_file_is_written_and_readable(self):
        with tempfile.TemporaryDirectory() as d:
            path, _ = AF.write_answer(self.ans, d, self.as_of, G["io"])
            back = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(back["case_id"], "TEST-LIVE-ANS")
            self.assertIsNone(back["case"]["fraud_probability"])
            filed = any(a["action"] == "FILE_REPORT" for a in back["next_best_actions"]["final"])
            self.assertEqual(back["sar"]["file"], filed)                      # the seeded simulator's response decides whether the final actions include a report
            if filed:
                self.assertTrue(6 <= len(re.split(r"(?<=[.!?])\s+(?=[A-Z\[])", back["sar"]["narrative"])) <= 12)
            else:
                self.assertEqual((back["sar"]["narrative"], back["sar"]["subjects"]), ("", []))

    def test_tampered_answer_is_rejected_live(self):
        bad = json.loads(json.dumps(self.ans))
        bad["case"]["affected_txn_ids"].append("9999999")
        errs, _ = AF.validate_answer(bad, self.as_of, G["io"])
        self.assertTrue(any("does not exist" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
