"""Offline tests for GraphRAG: chunk builder, loader guards, BM25 retrieval, tool permissions, session tools with a fake client, orchestrator use."""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import answer_file as AF  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.permissions import PermissionDenied, validate_call  # noqa: E402
from agent.schema import State  # noqa: E402
from fraud_tools.guards import LeakError  # noqa: E402
from fraud_tools.qclient import QueryClient  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402
from rag import chunks as CH  # noqa: E402
from rag import retrieve as R  # noqa: E402
from rag.store import ATTRS, BATCH, ChunkStore  # noqa: E402
from test_case_write import FakeIO, make_record  # noqa: E402
from test_simulator_exposure import AS_OF  # noqa: E402


class ChunkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "closed_cases_history.csv").exists():
            raise unittest.SkipTest("local data missing")
        cls.all = CH.build_all()

    def test_counts_and_types(self):
        by = {}
        for c in self.all:
            by[c["doc_type"]] = by.get(c["doc_type"], 0) + 1
        self.assertEqual(by, {"closed_case": 5565, "policy": 21, "pattern": 6, "format": 2})

    def test_closed_case_chunks_are_visible_only_from_the_close_epoch(self):
        from common import load_closed
        cc = load_closed().set_index("case_id")
        for c in [x for x in self.all if x["doc_type"] == "closed_case"][:300]:
            self.assertEqual(c["valid_from_epoch"], int(cc.loc[c["source_ref"], "close_ep"]))
            self.assertNotEqual(c["valid_from_epoch"], int(cc.loc[c["source_ref"], "open_ep"]))
            self.assertIn(c["channel"], ("online", "in_person"))
        self.assertTrue(all(c["valid_from_epoch"] == 0 for c in self.all if c["doc_type"] != "closed_case"))

    def test_no_benchmark_material_in_any_chunk(self):
        self.assertTrue(CH.assert_clean(self.all))
        blob = " ".join(c["text"] for c in self.all)
        self.assertNotRegex(blob, r"HHG-\d")
        self.assertNotIn("The 20 Cases", blob)

    def test_assert_clean_rejects_leaks_and_duplicates(self):
        base = {"doc_type": "policy", "source_ref": "policy:1", "valid_from_epoch": 0, "pattern": "", "outcome": "", "channel": ""}
        with self.assertRaises(AssertionError):
            CH.assert_clean([{**base, "chunk_id": "a", "text": "see HHG-014"}])
        with self.assertRaises(AssertionError):
            CH.assert_clean([{**base, "chunk_id": "a", "text": "x"}, {**base, "chunk_id": "a", "text": "y"}])

    def test_policy_rules_are_addressable_by_exact_reference(self):
        refs = {c["source_ref"] for c in self.all if c["doc_type"] == "policy"}
        for r in [f"R{i}" for i in range(1, 11)] + ["3a", "3b", "routes"]:
            self.assertIn(f"policy:{r}", refs)
        r7 = next(c for c in self.all if c["source_ref"] == "policy:R7")
        self.assertIn("merchant", r7["text"])
        self.assertEqual(len([c for c in self.all if c["doc_type"] == "pattern" and c["source_ref"].startswith("pattern:") and c["source_ref"][8:].isdigit()]), 5)

    def test_static_chunks_read_the_preserved_spec_not_the_judge_facing_readme(self):
        """Regression: the README was rebuilt into a short judge-facing document (no pattern/policy/format sections); the original spec that
        static_chunks() depends on was deliberately preserved unchanged at docs/hackathon-spec.md, and _readme_sections() must read it from there."""
        pat = [c for c in self.all if c["doc_type"] == "pattern"]
        pol = [c for c in self.all if c["doc_type"] == "policy"]
        fmt = [c for c in self.all if c["doc_type"] == "format"]
        self.assertEqual((len(pat), len(pol), len(fmt)), (6, 21, 2))     # 5 numbered patterns + the "not exhaustive" note, per test_counts_and_types
        self.assertTrue(all(c["text"].strip() for c in pat + pol + fmt))
        src = (ROOT / "src" / "rag" / "chunks.py").read_text(encoding="utf-8")
        self.assertIn('"hackathon-spec.md"', src)
        self.assertNotIn('ROOT / "README.md"', src)


class StoreTests(unittest.TestCase):
    def test_only_textchunk_and_describes_are_written(self):
        calls = []

        def req(method, path, body):
            calls.append((method, path, body))
            return 200, json.dumps({"results": [{"accepted_vertices": 1}]})
        chunks = [{"chunk_id": f"CH-{i}", "doc_type": "closed_case", "source_ref": f"CC-{i}", "text": "t", "valid_from_epoch": i, "pattern": "p", "outcome": "o", "channel": "online"}
                  for i in range(BATCH + 5)] + [{"chunk_id": "TX-1", "doc_type": "policy", "source_ref": "policy:R1", "text": "t", "valid_from_epoch": 0, "pattern": "", "outcome": "", "channel": ""}]
        s = ChunkStore(req)
        self.assertEqual(s.upsert(chunks), BATCH + 6)
        self.assertEqual(len(calls), 2)
        for m, path, body in calls:
            self.assertEqual(m, "POST")
            self.assertEqual(set(body["vertices"]), {"TextChunk"})
            self.assertTrue(set(body["edges"]) <= {"TextChunk"})
            for cid, e in body["edges"].get("TextChunk", {}).items():
                self.assertEqual(set(e), {"DESCRIBES"})
                self.assertEqual(set(e["DESCRIBES"]), {"ClosedCase"})
            for v in body["vertices"]["TextChunk"].values():
                self.assertEqual(set(v), set(ATTRS))
        self.assertNotIn("TX-1", json.dumps(calls[1][2]["edges"]))                 # policy chunks describe no case

    def test_upsert_failure_raises(self):
        with self.assertRaises(Exception):
            ChunkStore(lambda *a: (500, json.dumps({"error": True, "message": "boom"}))).upsert(
                [{"chunk_id": "x", "doc_type": "policy", "source_ref": "policy:R1", "text": "t", "valid_from_epoch": 0, "pattern": "", "outcome": "", "channel": ""}])

    def test_no_other_type_is_referenced_in_the_module(self):
        src = (ROOT / "src" / "rag" / "store.py").read_text(encoding="utf-8")
        for banned in ("Transaction", "Customer", "\"Card\"", "FI_Case", "DELETE", "DROP"):
            self.assertNotIn(banned, src)


def rows(n=6):
    base = [("CC-1", "online", "card_not_present_new_device", "confirmed_fraud", 100, "online purchases new device proxy unrecognized activity"),
            ("CC-2", "online", "card_testing", "confirmed_fraud", 200, "card testing small authorizations then a larger online purchase"),
            ("CC-3", "online", "card_not_present_fraud", "cleared", 300, "one unusual online purchase verified with the customer cleared"),
            ("CC-4", "online", "card_not_present_new_device", "cleared", 400, "new device proxy customer confirmed travel cleared"),
            ("CC-5", "online", "card_not_present_fraud", "confirmed_fraud", 500, "burst of online purchases inconsistent history"),
            ("CC-6", "online", "account_takeover", "confirmed_fraud", 600, "mixed channel activity stolen credentials")]
    return [{"chunk_id": f"CH-{c}", "case_id": c, "channel": ch, "pattern": p, "outcome": o, "close_ep": ce, "text": t} for c, ch, p, o, ce, t in base[:n]]


class RetrievalTests(unittest.TestCase):
    def test_bm25_ranks_the_matching_text_first_and_is_deterministic(self):
        q = R.build_query("online", ["S06"])
        a, b = R.bm25(q, [(r["case_id"], r["text"]) for r in rows()]), R.bm25(q, [(r["case_id"], r["text"]) for r in rows()])
        self.assertEqual(a, b)
        self.assertEqual(max(a, key=a.get), "CC-2")

    def test_query_uses_no_score_or_label_terms(self):
        for fired in ([], ["S01"], ["S06", "S10", "S15"]):
            q = R.build_query("online", fired)
            self.assertNotRegex(q, r"(?i)risk|score|fraud confirmed|label|HHG")
        self.assertIn("card present", R.build_query("in_person", []))

    def test_linked_seeds_come_first_then_precedents_labelled_by_shared_entities_only(self):
        hits = R.similar_cases(rows(), {"CC-3": ["same_card"], "CC-6": ["shared_device:D1"]}, R.build_query("online", ["S10"]), k=5)
        self.assertEqual([h["label"] for h in hits[:2]], ["linked", "linked"])
        self.assertEqual({h["case_id"] for h in hits[:2]}, {"CC-3", "CC-6"})
        self.assertTrue(all(h["label"] == "precedent" for h in hits[2:]))
        self.assertTrue(all(h["match_reasons"] for h in hits[:2]) and all(not h["match_reasons"] for h in hits[2:]))
        self.assertLessEqual(len(hits), 5)

    def test_seed_not_visible_is_not_invented(self):
        hits = R.similar_cases(rows(3), {"CC-9": ["same_card"]}, "online", k=5)
        self.assertNotIn("CC-9", [h["case_id"] for h in hits])

    def test_k_is_capped_and_results_are_stable(self):
        rs = rows() * 1
        h1, h2 = R.similar_cases(rs, {}, "online purchases", k=50), R.similar_cases(rs, {}, "online purchases", k=50)
        self.assertEqual(h1, h2)
        self.assertLessEqual(len(h1), 20)

    def test_policy_lookup_by_exact_reference(self):
        prow = [{"source_ref": "policy:R7", "chunk_id": "TX-policy-R7", "text": "R7 text"}, {"source_ref": "policy:R1", "chunk_id": "TX-policy-R1", "text": "R1 text"}]
        got = R.policy_chunks(prow, ["R1", "R9"])
        self.assertEqual([g["source_ref"] for g in got], ["policy:R1"])


class PermissionTests(unittest.TestCase):
    def test_new_tools_are_whitelisted_with_strict_arguments(self):
        self.assertEqual(validate_call("find_similar_cases", {"txn_id": "3514030", "k": 5}), {"txn_id": "3514030", "k": 5})
        self.assertEqual(validate_call("retrieve_policy", {"rule_ids": ["R7", "3a"]}), {"rule_ids": ["R7", "3a"]})
        for bad in ({"rule_ids": []}, {"rule_ids": ["R11"]}, {"rule_ids": ["R1; DROP"]}, {"rule_ids": "R1"}, {"rule_ids": ["R1"] * 13}, {}, {"rule_ids": ["R1"], "as_of": 5}):
            with self.assertRaises(PermissionDenied):
                validate_call("retrieve_policy", bad)
        for bad in ({"txn_id": "3514030", "k": 99}, {"txn_id": "3514030", "channel": "online"}, {"txn_id": "3514030", "query": "x"}, {"txn_id": "abc"}):
            with self.assertRaises(PermissionDenied):
                validate_call("find_similar_cases", bad)

    def test_retrieve_policy_is_allowed_in_next_best_action_state_only_among_late_states(self):
        validate_call("retrieve_policy", {"rule_ids": ["R1"]}, State.NEXT_BEST_ACTION)
        for st in (State.EXPLANATION, State.CASE_WRITE, State.EVIDENCE_SYNTHESIS):
            with self.assertRaises(PermissionDenied):
                validate_call("retrieve_policy", {"rule_ids": ["R1"]}, st)
        with self.assertRaises(PermissionDenied):
            validate_call("find_similar_cases", {"txn_id": "3514030"}, State.NEXT_BEST_ACTION)

    def test_qclient_allows_only_closed_string_values(self):
        ok = dict(as_of=5, doc_type="closed_case", channel="online", max_chunks=100)
        QueryClient.check("fi_text_chunks", ok)
        for bad in ({"doc_type": "everything"}, {"channel": "mobile"}, {"doc_type": "policy'--"}, {"max_chunks": 99999}, {"doc_type": 5}):
            with self.assertRaises(Exception):
                QueryClient.check("fi_text_chunks", {**ok, **bad})


class FakeClientRows:
    def __init__(self, chunk_rows, max_epoch=0):
        self.rows, self.me, self.calls = chunk_rows, max_epoch, []

    def run(self, name, **p):
        self.calls.append((name, p))
        if name == "fi_text_chunks":
            return {"S": self.rows if p["doc_type"] == "closed_case" else [{"source_ref": "policy:R7", "chunk_id": "TX-policy-R7", "text": "R7: Disputed"}], "max_epoch_seen": self.me}
        if name == "fi_txn_context":
            return {"visible": True, "V": [{"txn_id": 3000001, "epoch": 50, "ts": "x", "amount": 10.0, "product_cd": "C", "channel": "online", "card_id": "C00001-K1",
                                            "customer_id": "C00001", "addr1": -1, "addr2": -1, "dist1": -1, "dist2": -1, "has_identity": True, "id_15": "New", "proxy_type": "",
                                            "device_type": "", "p_email": "", "r_email": "", "risk_score": 0.9, **{k: -1 for k in ("c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15", "v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308")},
                                            **{f"m{i}": "" for i in range(1, 10)}}], "max_epoch_seen": 50}
        raise RuntimeError("unexpected " + name)


class SessionToolTests(unittest.TestCase):
    def test_future_closed_case_hit_is_refused(self):
        rs = rows(2)
        rs[1]["close_ep"] = 10_000
        s = InvestigationSession(500, FakeClientRows(rs))
        s.detect_fraud_patterns = lambda t: {"fired": []}
        s.find_prior_cases = lambda c: {"cases": []}
        with self.assertRaises(LeakError):
            s.find_similar_cases("3000001")

    def test_tool_returns_hits_and_never_the_risk_score(self):
        s = InvestigationSession(500, FakeClientRows(rows(), 0))
        s.detect_fraud_patterns = lambda t: {"fired": ["S10"]}
        s.find_prior_cases = lambda c: {"cases": [{"case_id": "CC-3", "match_reasons": ["same_card"]}]}
        out = s.find_similar_cases("3000001", k=4)
        self.assertEqual(out["hits"][0]["case_id"], "CC-3")
        self.assertEqual(out["hits"][0]["label"], "linked")
        self.assertNotIn("risk", json.dumps(out).lower())
        self.assertIn("never a label", out["note"])

    def test_retrieve_policy_marks_policy_as_not_evidence(self):
        s = InvestigationSession(500, FakeClientRows(rows()))
        out = s.retrieve_policy(["R7", "R9"])
        self.assertEqual(out["kind"], "policy_text_not_evidence")
        self.assertEqual([c["source_ref"] for c in out["chunks"]], ["policy:R7"])
        self.assertEqual(out["missing"], ["R9"])


class ChunkCacheTests(unittest.TestCase):
    def sessions(self, as_ofs, frontier, cache):
        client = FakeClientRows(rows(), 0)
        out = []
        for a in as_ofs:
            s = InvestigationSession(a, client, chunk_cache=cache, chunk_frontier=frontier)
            s.detect_fraud_patterns = lambda t: {"fired": []}
            s.find_prior_cases = lambda c: {"cases": []}
            out.append(s)
        return client, out

    def test_closed_case_chunks_are_reused_only_when_every_chunk_is_visible(self):
        client, ss = self.sessions([1_000_000, 1_000_050, 1_000_100], 1_000_000, {})
        for s in ss:
            s.find_similar_cases("3000001")
        self.assertEqual(sum(1 for n, p in client.calls if n == "fi_text_chunks"), 1)
        client, ss = self.sessions([999_000, 999_100], 1_000_000, {})                       # before the frontier: server-side filtering every time
        for s in ss:
            s.find_similar_cases("3000001")
        self.assertEqual(sum(1 for n, p in client.calls if n == "fi_text_chunks"), 2)

    def test_no_frontier_no_cache_and_policy_chunks_always_cache(self):
        client, ss = self.sessions([500, 900], None, {})
        for s in ss:
            s.find_similar_cases("3000001")
            s.retrieve_policy(["R7"])
        kinds = [p["doc_type"] for n, p in client.calls if n == "fi_text_chunks"]
        self.assertEqual(kinds.count("closed_case"), 2)
        self.assertEqual(kinds.count("policy"), 1)

    def test_cached_rows_are_copies(self):
        cache = {}
        client, ss = self.sessions([2_000_000, 2_000_010], 1_000_000, cache)
        a = ss[0].find_similar_cases("3000001")
        a["hits"].clear()
        b = ss[1].find_similar_cases("3000001")
        self.assertTrue(b["hits"] or b["n_visible_case_chunks"] > 0)


class AgentUsesGraphRagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = make_record(case="TEST-R1")

    def test_similar_cases_and_policy_documents_become_evidence(self):
        ev = self.rec["case"]["evidence"]
        self.assertTrue(any(e["ref"] == "find_similar_cases" for e in ev))
        docs = [e for e in ev if e["source"] == "document"]
        self.assertTrue(docs)
        self.assertTrue(all(e["ref"].startswith("document:policy:") for e in docs))
        self.assertIn("CC-1", self.rec["case"]["similar_prior_cases"])

    def test_document_evidence_is_never_customer_or_simulated(self):
        for e in self.rec["case"]["evidence"]:
            if e["source"] == "document":
                self.assertFalse(e["simulated"])

    def test_answer_file_carries_document_refs_and_validates(self):
        a = AF.assemble(self.rec)
        d = [e for e in a["case"]["evidence"] if e["source"] == "document"]
        self.assertTrue(d and all(e["ref"].startswith("document:policy:") for e in d))
        self.assertEqual(AF.validate_answer(a, AS_OF, FakeIO())[0], [])

    def test_similar_case_edges_keep_method_and_score(self):
        refs = self.rec["graph_refs"]["similar_cases"]
        self.assertTrue(all("method" in x and "score" in x for x in refs))


if __name__ == "__main__":
    unittest.main()
