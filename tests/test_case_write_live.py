"""Live tests against FraudInvestigation: graph-backed case validation and the real FI_Case write path (test cases only; cleaned up afterwards).
Never writes anything except FI_Case vertices with ids starting CASE-TEST- and their case edges. Run: python -m unittest tests.test_case_write_live -v"""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

from agent.case_validator import CaseValidationError, validate  # noqa: E402
from agent.case_writer import CaseWriter, graph_case_id_for  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.graphio import GraphIO, GraphIOError  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from agent.simulator import EvidenceSimulator  # noqa: E402
from calibration.runtime import Calibrator  # noqa: E402
from fraud_tools.qclient import QueryClient  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402

G = {}
RING = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"


def setUpModule():
    if not (ROOT / ".env").exists():
        raise unittest.SkipTest(".env missing")
    from common import build_master
    try:
        G["io"] = GraphIO()
        G["io"].get_vertex("Card", "C00001-K1")
        G["client"] = QueryClient()
    except Exception as e:
        raise unittest.SkipTest(f"graph not reachable: {e}")
    G["m"] = build_master()
    rg = G["m"][G["m"]["dev"] == RING].sort_values(["epoch", "TransactionID"])
    seen = []
    for _, r in rg.iterrows():
        if r["customer_id"] not in seen:
            seen.append(r["customer_id"])
            if len(seen) == 30:
                G["row"] = r
                break
    G["created"] = []


def tearDownModule():
    io = G.get("io")
    for gid in G.get("created", []):
        try:
            io.delete_test_case(gid)
        except Exception as e:                                   # report but do not hide leftovers
            print("cleanup failed for", gid, e)


def bump(r, d):
    r["case"]["exposure_usd"] = round(r["case"]["exposure_usd"] + d, 2)
    if r["sar"]["file"]:
        r["sar"]["total_amount_usd"] = round(r["sar"]["total_amount_usd"] + d, 2)


def run_agent(case_id, responder=None, writer=None):
    row = G["row"]
    as_of = int(row["epoch"])
    gw = ToolGateway(InvestigationSession(as_of, G["client"]))
    a = Agent(gw, Trigger(case_id, str(int(row["TransactionID"])), "risk_score"), responder=responder or PendingResponder(),
              calibrator=Calibrator(as_of), writer=writer)
    return a.run(), as_of


class LiveValidation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec, cls.as_of = run_agent("TEST-LIVE-V1")

    def test_valid_ring_case_passes_graph_validation(self):
        io = GraphIO()
        self.assertEqual(validate(self.rec, self.as_of, io), [])
        self.assertGreater(len(self.rec["case"]["affected_txn_ids"]), 3)

    def test_graph_amounts_match_exposure_and_pandas(self):
        m = G["m"]
        ids = [int(i) for i in self.rec["case"]["affected_txn_ids"]]
        self.assertAlmostEqual(self.rec["case"]["exposure_usd"], round(float(m[m["TransactionID"].isin(ids)]["TransactionAmt"].abs().sum()), 2), places=2)

    def test_unknown_id_is_rejected(self):
        r = copy.deepcopy(self.rec)
        r["case"]["affected_txn_ids"].append("9999999")
        v = validate(r, self.as_of, GraphIO())
        self.assertTrue(any("Transaction 9999999 does not exist" in x for x in v), v)

    def test_future_transaction_is_rejected(self):
        m = G["m"]
        later = m[(m["dev"] == RING) & (m["epoch"] > self.as_of)].iloc[0]
        r = copy.deepcopy(self.rec)
        r["case"]["affected_txn_ids"].append(str(int(later["TransactionID"])))
        bump(r, abs(float(later["TransactionAmt"])))
        v = validate(r, self.as_of, GraphIO())
        self.assertTrue(any("> as_of" in x for x in v), v)

    def test_wrong_exposure_is_rejected(self):
        r = copy.deepcopy(self.rec)
        bump(r, 1.0)
        self.assertTrue(any(x.startswith("G3") for x in validate(r, self.as_of, GraphIO())))

    def test_transaction_not_on_evidence_device_is_rejected(self):
        m = G["m"]
        other = m[(m["dev"] != RING) & m["dev"].notna() & (m["epoch"] < self.as_of)].iloc[5]
        r = copy.deepcopy(self.rec)
        r["case"]["affected_txn_ids"].append(str(int(other["TransactionID"])))
        bump(r, abs(float(other["TransactionAmt"])))
        v = validate(r, self.as_of, GraphIO())
        self.assertTrue(any("not on a cited evidence device" in x for x in v), v)

    def test_closed_case_must_be_visible_at_as_of_by_close_time(self):
        from common import load_closed
        cc = load_closed()
        late = cc.sort_values("close_ep").iloc[-1]                     # opened long before, closes last
        as_of = int(late["close_ep"]) - 1
        self.assertLess(int(late["open_ep"]), as_of)
        r = copy.deepcopy(self.rec)
        r["case"]["similar_prior_cases"] = [late["case_id"]]
        v = validate(r, as_of, GraphIO())
        self.assertTrue(any("G4 closed case" in x for x in v), v)
        r["case"]["similar_prior_cases"] = [late["case_id"]]
        self.assertFalse(any("G4 closed case" in x for x in validate(r, int(late["close_ep"]), GraphIO())))     # boundary: close == as_of is visible


class LiveWrite(unittest.TestCase):
    def setUp(self):
        self.io = GraphIO()

    def _write(self, case_id, responder=None, sim=None):
        gid = graph_case_id_for(case_id)
        G["created"].append(gid)
        row = G["row"]
        as_of = int(row["epoch"])
        w = CaseWriter(as_of, self.io, simulator=sim)
        rec, _ = run_agent(case_id, responder, writer=w)
        return rec, w, gid, as_of

    def test_create_idempotent_update_and_source_facts_untouched(self):
        row = G["row"]
        facts = [("Transaction", str(int(row["TransactionID"]))), ("Card", row["card_id"]), ("Customer", row["customer_id"])]
        before = [self.io.get_vertex(*f) for f in facts]
        rec, w, gid, as_of = self._write("TEST-LIVE-W1")
        self.assertTrue(rec["case"]["written_to_graph"])
        self.assertEqual((rec["graph_write"]["action"], rec["graph_write"]["revision"]), ("created", 1))
        v = self.io.get_vertex("FI_Case", gid)
        self.assertEqual((v["source_case_id"], v["as_of_epoch"], v["revision"]), ("TEST-LIVE-W1", as_of, 1))
        self.assertEqual(v["pattern"], "undocumented")
        edges = self.io.case_edge_counts(gid)
        self.assertEqual([e[1] for e in edges["CASE_ON_CARD"]], [row["card_id"]])
        self.assertEqual({e[1] for e in edges["CASE_TXN"]}, {str(i) for i in rec["case"]["affected_txn_ids"]} | {str(int(row["TransactionID"]))})
        self.assertEqual({e[1] for e in edges["CASE_CONNECTED_TO"]}, set(rec["case"]["connected_card_ids"]))
        self.assertGreaterEqual(len(edges["CASE_CITES_DEVICE"]), 1)
        self.assertEqual(json.loads(v["final_actions"]), rec["next_best_actions"]["final"])
        # idempotent: same content again writes nothing new and keeps the revision
        rec2, _ = run_agent("TEST-LIVE-W1", writer=w)
        self.assertEqual((rec2["graph_write"]["action"], rec2["graph_write"]["revision"]), ("unchanged", 1))
        self.assertEqual(self.io.get_vertex("FI_Case", gid)["revision"], 1)
        # changed content (a simulated response) -> revision 2, one vertex, edges replaced not duplicated
        rec3, _ = run_agent("TEST-LIVE-W1", responder=_Fixed("denied_or_unrecognized"), writer=w)
        self.assertEqual((rec3["graph_write"]["action"], rec3["graph_write"]["revision"]), ("updated", 2))
        self.assertEqual(self.io.case_edge_counts(gid)["CASE_ON_CARD"], edges["CASE_ON_CARD"])
        self.assertEqual(len(self.io.case_edge_counts(gid)["CASE_TXN"]), len(edges["CASE_TXN"]))
        # source facts never modified
        self.assertEqual(before, [self.io.get_vertex(*f) for f in facts])

    def test_simulated_evidence_is_marked_in_the_graph(self):
        sim = EvidenceSimulator("cardholder_denies")
        rec, w, gid, as_of = self._write("TEST-LIVE-W2", responder=sim, sim=sim)
        v = self.io.get_vertex("FI_Case", gid)
        ev = json.loads(v["evidence_json"])
        self.assertEqual(ev["provenance"]["simulator"]["scenario"], "cardholder_denies")
        self.assertTrue(all(r["simulated"] for r in json.loads(v["evidence_requests_json"])))
        for e in ev["evidence"]:
            if e["source"] == "customer":
                self.assertTrue(e["simulated"] and e["claim"].startswith("[SIMULATED]"))
        self.assertFalse(ev["probability"]["calibrated"])

    def test_invalid_case_writes_nothing(self):
        gid = graph_case_id_for("TEST-LIVE-W3")
        G["created"].append(gid)
        rec, as_of = run_agent("TEST-LIVE-W3")
        bad = copy.deepcopy(rec)
        bump(bad, 5)
        with self.assertRaises(CaseValidationError):
            CaseWriter(as_of, self.io).write(bad)
        self.assertIsNone(self.io.get_vertex("FI_Case", gid))

    def test_writer_cannot_touch_source_types(self):
        with self.assertRaises(GraphIOError):
            self.io.upsert_case("CASE-TEST-X", {}, {"MADE": [("C00001-K1", {})]})
        with self.assertRaises(GraphIOError):
            self.io.delete_test_case("CASE-HHG-001")


class _Fixed:
    def __init__(self, o):
        self.o = o

    def respond(self, request, ctx):
        return self.o, "[SIMULATED] test double"


class CalibrationOracleAgreement(unittest.TestCase):
    def test_pandas_strength_oracle_matches_the_live_agent(self):
        from calibration.frame import strength_table
        from fraud_tools import patterns
        st = strength_table().set_index("TransactionID")
        m = G["m"]
        pool = st.sample(30, random_state=5).index.tolist()
        hits = st[(st["S06"] | st["S07"] | st["S08"] | st["S02a"] | st["S02b"])].sample(10, random_state=5).index.tolist()
        ids, agree, bad = pool + hits, 0, []
        for tid in ids:
            ep = int(m.loc[m["TransactionID"] == tid, "epoch"].iloc[0])
            det = InvestigationSession(ep, G["client"]).detect_fraud_patterns(str(int(tid)))
            ind = det["independence"]
            fired = set(det["fired"])
            n, low = ind["n_tier_1_to_5_sources"], ind["low_context_sources"]
            live = "strong" if ("S01" in fired or "S02a" in fired or n >= 2) else "moderate" if n == 1 else "weak" if low else "none"
            if live == st.loc[tid, "strength"]:
                agree += 1
            else:
                bad.append((int(tid), live, st.loc[tid, "strength"], sorted(fired)))
        rate = agree / len(ids)
        print(f"oracle/live strength agreement: {agree}/{len(ids)} = {rate:.3f}; mismatches: {bad}")
        self.assertGreaterEqual(rate, 0.95)


if __name__ == "__main__":
    unittest.main()
