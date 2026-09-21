"""Offline tests: case-write validator + CaseWriter (in-memory fake graph). The real GraphIO is only exercised for its guards (no network)."""
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent.case_validator import CaseValidationError, assert_valid, validate  # noqa: E402
from agent.case_writer import CaseWriteError, CaseWriter, content_hash, graph_case_id_for  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.graphio import CASE_EDGES, GraphIO, GraphIOError  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from agent.simulator import EvidenceSimulator  # noqa: E402
from test_simulator_exposure import AS_OF, FakeSession, Forced, T0  # noqa: E402


class FakeIO:
    """In-memory stand-in for GraphIO: source facts are read-only; FI_Case vertices/edges are written to separate dicts and every write is logged."""
    def __init__(self, txn_epoch_shift=0, card_owner=None, closed_close=1):
        self.v = {("Card", "C00001-K1"): {"customer_id": "C00001"}, ("Card", "C00002-K1"): {"customer_id": "C00002"}, ("Card", "C00003-K1"): {"customer_id": "C00003"},
                  ("Customer", "C00001"): {}, ("Customer", "C00002"): {}, ("Customer", "C00003"): {}, ("DeviceProfile", "D_ring"): {"profile_str": "SM | Android"},
                  ("ClosedCase", "CC-1"): {"open_epoch": 1, "close_epoch": closed_close},
                  ("Transaction", "3000001"): {"epoch": T0, "amount": 100.0, "card_id": "C00001-K1"},
                  ("Transaction", "3000000"): {"epoch": T0 - 3600, "amount": 20.0, "card_id": "C00001-K1"},
                  ("Transaction", "2001"): {"epoch": 4_900_000 + txn_epoch_shift, "amount": 200.0, "card_id": "C00002-K1"},
                  ("Transaction", "3001"): {"epoch": 5_050_000, "amount": 50.0, "card_id": "C00003-K1"}}
        if card_owner:
            self.v[("Card", "C00001-K1")]["customer_id"] = card_owner
        self.dev = {2001: ["D_ring"], 3001: ["D_ring"]}
        self.case_v, self.case_e, self.log, self.writes = {}, {}, [], 0

    def get_vertex(self, vt, vid):
        if vt == "FI_Case":
            return copy.deepcopy(self.case_v.get(str(vid)))
        return self.v.get((vt, str(vid)))

    def txn_devices(self, t):
        return self.dev.get(int(t), [])

    def upsert_case(self, gid, attrs, edges):
        self.writes += 1
        self.log.append(("vertex", "FI_Case"))
        for et, tg in edges.items():
            assert et in CASE_EDGES
            self.log.append(("edge", et))
        self.case_v[gid] = copy.deepcopy(attrs)
        self.case_e[gid] = {et: [(str(t), dict(a)) for t, a in tg] for et, tg in edges.items()}

    def delete_case_edges(self, gid):
        self.writes += 1
        self.log.append(("delete_edges", gid))
        self.case_e[gid] = {}

    def case_edge_counts(self, gid):
        return {et: [("x", t, json.dumps(a, sort_keys=True)) for t, a in self.case_e.get(gid, {}).get(et, [])] for et in CASE_EDGES}


def make_record(ring=True, responder=None, case="SYN-900", trigger="risk_score", **kw):
    a = Agent(ToolGateway(FakeSession(ring=ring)), Trigger(case, "3000001", trigger), responder=responder or PendingResponder(), **kw)
    return a.run()


def rec_copy(r):
    return copy.deepcopy(r)


def bump(r, d):
    """Change the exposure consistently in the case and in the SAR (so only the graph can expose the tamper)."""
    r["case"]["exposure_usd"] = round(r["case"]["exposure_usd"] + d, 2)
    if r["sar"]["file"]:
        r["sar"]["total_amount_usd"] = round(r["sar"]["total_amount_usd"] + d, 2)


class StructuralValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ring = make_record()
        cls.legit = make_record(ring=False, responder=Forced("passed"))

    def bad(self, mutate, needle, base=None):
        r = rec_copy(base or self.ring)
        mutate(r)
        v = validate(r, AS_OF)
        self.assertTrue(any(needle in x for x in v), f"{needle!r} not in {v}")

    def test_valid_records_pass(self):
        self.assertEqual(validate(self.ring, AS_OF), [])
        self.assertEqual(validate(self.legit, AS_OF), [])

    def test_enums_and_ranges(self):
        self.bad(lambda r: r["case"].update(status="done"), "S1 status")
        self.bad(lambda r: r["case"].update(verdict="maybe"), "S1 verdict")
        self.bad(lambda r: r["case"].update(pattern="weird"), "S1 pattern")
        self.bad(lambda r: r["case"].update(fraud_probability=1.4), "S1 fraud_probability")
        self.bad(lambda r: r["case"].update(exposure_usd=-1), "S1 exposure")
        self.bad(lambda r: r["next_best_actions"]["initial"][0].update(action="NUKE"), "S1 unknown action")
        self.bad(lambda r: r["next_best_actions"]["initial"][0].update(route="L9"), "S1 unknown route")

    def test_action_route_and_execution_rules(self):
        self.bad(lambda r: r["next_best_actions"]["initial"][0].update(route="L2" if r["next_best_actions"]["initial"][0]["route"] != "L2" else "auto"), "S3")
        self.bad(lambda r: r["next_best_actions"]["initial"][0].update(executed=True), "marked executed")
        self.bad(lambda r: r["next_best_actions"]["final"].append({"action": "BLOCK_ALL_CARDS", "route": "L2", "reason": "x", "rules": [], "executed": False}), "BLOCK_ALL_CARDS")

    def test_legitimate_consistency(self):
        self.bad(lambda r: r["case"].update(affected_txn_ids=["3000001"]), "S2 legitimate", self.legit)
        self.bad(lambda r: r["case"].update(exposure_usd=10.0), "S2 legitimate", self.legit)

    def test_pattern_description_and_first_suspicious(self):
        self.bad(lambda r: r["case"].update(pattern_description=""), "S2 pattern_description")
        self.bad(lambda r: r["case"].update(pattern="none"), "S2 pattern_description")
        self.bad(lambda r: r["case"].update(first_suspicious_txn_id="999"), "S2 first_suspicious")

    def test_duplicates_rejected(self):
        self.bad(lambda r: r["case"]["affected_txn_ids"].append(r["case"]["affected_txn_ids"][0]), "duplicates")

    def test_what_changed_consistency(self):
        self.bad(lambda r: r["next_best_actions"].update(what_changed="the response changed it"), "S4")
        self.bad(lambda r: r["next_best_actions"].update(what_changed="nothing", final=[]), "S4")

    def test_uncalibrated_probability_must_be_null(self):
        self.bad(lambda r: r["case"].update(fraud_probability=0.7), "S5 an uncalibrated probability")
        self.bad(lambda r: r["case"].update(probability_calibrated=True, probability_source="calibration_table", fraud_probability=None), "S5")
        self.assertIsNone(self.ring["case"]["fraud_probability"])

    def test_calibrated_flag_requires_calibration_source(self):
        self.bad(lambda r: r["case"].update(probability_calibrated=True, probability_source="provisional_placeholder"), "S5")

    def test_explanation_citations(self):
        self.bad(lambda r: r["case"].update(summary=r["case"]["summary"] + " See [E99]."), "S6 summary cites unknown")
        self.bad(lambda r: r["case"]["evidence"].append(dict(r["case"]["evidence"][0])), "S6 duplicate")

    def test_simulated_evidence_must_be_marked(self):
        rec = make_record(ring=False, responder=Forced("passed"))
        sim = [e for e in rec["case"]["evidence"] if e.get("simulated")]
        self.assertEqual(len(sim), 1)
        self.bad(lambda r: [e.update(simulated=False) for e in r["case"]["evidence"]], "S7", rec)
        self.bad(lambda r: [e.update(claim="Customer confirmed") for e in r["case"]["evidence"] if e.get("simulated")], "S7 simulated evidence", rec)
        self.bad(lambda r: r["evidence_requests"][0].update(simulated=False), "S7 evidence request", rec)
        self.bad(lambda r: r.update(simulated_evidence_ids=[]), "S7 simulated_evidence_ids", rec)

    def test_sar_rules(self):
        self.assertTrue(self.ring["sar"]["file"])
        self.bad(lambda r: r["sar"].update(file=False), "S9 sar.file must equal")
        self.bad(lambda r: r["sar"].update(narrative="Too short. Only two sentences."), "S9 sar.narrative has 2 sentences")
        self.bad(lambda r: r["sar"].update(activity_dates=["2016-13-1", "x"]), "S9 sar.activity_dates")
        self.bad(lambda r: r["sar"].update(activity_dates=["2016-08-02", "2016-08-01"]), "S9 sar.activity_dates")
        self.bad(lambda r: r["sar"].update(total_amount_usd=1.0), "S9 sar.total_amount_usd")
        self.bad(lambda r: r["sar"].update(subjects=[]), "S9 sar.subjects")
        self.bad(lambda r: r["sar"].update(narrative=r["sar"]["narrative"] + " The probability of fraud is 90%."), "S9 sar.narrative states a probability")
        self.bad(lambda r: r["sar"].update(reason=""), "S9 sar.reason")
        self.bad(lambda r: r.pop("sar"), "S9 sar missing")
        # not filed: everything empty
        self.bad(lambda r: r["sar"].update(narrative="x"), "S9", self.legit)
        self.assertFalse(self.legit["sar"]["file"])
        self.assertEqual((self.legit["sar"]["narrative"], self.legit["sar"]["subjects"], self.legit["sar"]["total_amount_usd"], self.legit["sar"]["activity_dates"]), ("", [], 0, []))

    def test_leaks_rejected(self):
        self.bad(lambda r: r["case"].update(summary="see HHG-014 " + r["case"]["summary"]), "S8 other case ids")
        self.bad(lambda r: r.update(risk_score=0.9), "S8 forbidden keys")
        self.bad(lambda r: r["case"].update(model_score=0.9), "S8 forbidden keys")
        ok = rec_copy(self.ring)
        ok["case_id"] = "HHG-001"                                            # its own id is allowed
        ok["case"]["summary"] = "HHG-001 " + ok["case"]["summary"]
        self.assertEqual(validate(ok, AS_OF), [])

    def test_malformed_records(self):
        self.assertTrue(validate({}, AS_OF))
        r = rec_copy(self.ring)
        del r["case"]["exposure_usd"]
        self.assertTrue(any("S1 case.exposure_usd missing" in x for x in validate(r, AS_OF)))


class GraphValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ring = make_record()

    def v(self, io, mutate=None):
        r = rec_copy(self.ring)
        if mutate:
            mutate(r)
        return validate(r, AS_OF, io)

    def test_valid_against_graph(self):
        self.assertEqual(self.v(FakeIO()), [])

    def test_unknown_ids_rejected(self):
        io = FakeIO()
        del io.v[("Transaction", "2001")]
        self.assertTrue(any("Transaction 2001 does not exist" in x for x in self.v(io)))
        io = FakeIO()
        del io.v[("DeviceProfile", "D_ring")]
        self.assertTrue(any("DeviceProfile D_ring does not exist" in x for x in self.v(io)))

    def test_card_customer_mismatch(self):
        self.assertTrue(any("does not belong to customer" in x for x in self.v(FakeIO(card_owner="C77777"))))

    def test_future_transaction_rejected_by_graph_epoch(self):
        self.assertTrue(any("G2 transaction 2001" in x and "> as_of" in x for x in self.v(FakeIO(txn_epoch_shift=1_000_000))))

    def test_exposure_is_recomputed_from_the_graph(self):
        v = self.v(FakeIO(), lambda r: bump(r, 5.0))
        self.assertTrue(any(x.startswith("G3") for x in v))

    def test_affected_transaction_must_exist_and_be_listed(self):
        v = self.v(FakeIO(), lambda r: r["case"]["affected_txn_ids"].append("4444444"))
        self.assertTrue(any("Transaction 4444444 does not exist" in x for x in v))

    def test_other_card_transaction_must_be_on_a_cited_device(self):
        io = FakeIO()
        io.dev[2001] = ["D_other"]
        self.assertTrue(any("G2 other-card transaction 2001" in x for x in self.v(io)))
        self.assertTrue(any("without a cited evidence device" in x for x in self.v(FakeIO(), lambda r: r["graph_refs"].update(device_ids=[]))))

    def test_closed_case_visibility_uses_close_time_not_open_time(self):
        io = FakeIO(closed_close=AS_OF + 10)                                 # opened long before as_of, closes after it
        self.assertTrue(any("G4 closed case CC-1" in x for x in self.v(io)))
        self.assertEqual(self.v(FakeIO(closed_close=AS_OF)), [])             # boundary: close == as_of is visible

    def test_structural_failure_short_circuits_graph_reads(self):
        io = FakeIO()
        io.get_vertex = lambda *a: (_ for _ in ()).throw(AssertionError("graph must not be read"))
        r = rec_copy(self.ring)
        r["case"]["status"] = "done"
        self.assertTrue(validate(r, AS_OF, io))


class WriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ring = make_record(case="TEST-W1")

    def writer(self, io=None, as_of=AS_OF, sim=None):
        return CaseWriter(as_of, io or FakeIO(), simulator=sim)

    def test_create_writes_one_vertex_and_the_expected_edges(self):
        io = FakeIO()
        rec = rec_copy(self.ring)
        res = self.writer(io).write(rec)
        gid = graph_case_id_for("TEST-W1")
        self.assertEqual((res["graph_case_id"], res["revision"], res["action"]), (gid, 1, "created"))
        a = io.case_v[gid]
        self.assertEqual((a["source_case_id"], a["as_of_epoch"], a["revision"], a["pattern"]), ("TEST-W1", AS_OF, 1, "undocumented"))
        self.assertEqual(a["created_at"], a["updated_at"])                   # case time, not wall clock
        e = io.case_e[gid]
        self.assertEqual([t for t, _ in e["CASE_ON_CARD"]], ["C00001-K1"])
        roles = dict(e["CASE_TXN"])
        self.assertIn("flagged", roles["3000001"]["role"])
        self.assertEqual({t for t, _ in e["CASE_TXN"]}, {str(i) for i in rec["case"]["affected_txn_ids"]} | {"3000001"})
        self.assertIn("first_suspicious", roles[rec["case"]["first_suspicious_txn_id"]]["role"])
        self.assertEqual({t for t, _ in e["CASE_CONNECTED_TO"]}, {"C00002-K1", "C00003-K1"})
        self.assertTrue(all(x[1]["via"] == "device:D_ring" for x in e["CASE_CONNECTED_TO"]))
        self.assertEqual([t for t, _ in e["CASE_CITES_DEVICE"]], ["D_ring"])
        self.assertEqual([t for t, _ in e["SIMILAR_CASE"]], ["CC-1"])
        self.assertEqual(json.loads(a["final_actions"]), rec["next_best_actions"]["final"])
        self.assertEqual(a["fraud_probability"], -1.0)                       # -1 = missing (E18); never a made-up number
        self.assertIsNone(json.loads(a["evidence_json"])["probability"]["value"])
        self.assertFalse(any(x["executed"] for x in json.loads(a["initial_actions"])))

    def test_only_fi_case_family_objects_are_written(self):
        io = FakeIO()
        self.writer(io).write(rec_copy(self.ring))
        kinds = {x for x in io.log if x[0] == "vertex"}
        self.assertEqual(kinds, {("vertex", "FI_Case")})
        self.assertTrue({e for k, e in io.log if k == "edge"} <= set(CASE_EDGES))
        self.assertEqual(len(io.v), 12)                                      # source facts untouched (same objects as before)

    def test_provenance_and_simulated_evidence_marking_are_stored(self):
        rec = make_record(ring=False, responder=EvidenceSimulator("cardholder_denies"), trigger="customer_complaint", case="TEST-W2")
        io = FakeIO()
        # the non-ring fake has no CC-1 links etc.; only structure matters here
        self.writer(io, sim=EvidenceSimulator("cardholder_denies")).write(rec)
        ev = json.loads(io.case_v["CASE-TEST-W2"]["evidence_json"])
        prov = ev["provenance"]
        self.assertEqual((prov["source_case_id"], prov["as_of_epoch"], prov["graph_facts_modified"]), ("TEST-W2", AS_OF, False))
        self.assertEqual(prov["content_sha256"], content_hash(rec))
        self.assertEqual((prov["simulator"]["scenario"], prov["simulator"]["random"]), ("cardholder_denies", False))
        req = json.loads(io.case_v["CASE-TEST-W2"]["evidence_requests_json"])
        self.assertTrue(all(r["simulated"] for r in req))
        for e in ev["evidence"]:
            if e["source"] == "customer":
                self.assertTrue(e["simulated"] and e["claim"].startswith("[SIMULATED]"))
        self.assertEqual(ev["simulated_evidence_ids"], rec["simulated_evidence_ids"])

    def test_write_is_idempotent(self):
        io = FakeIO()
        w = self.writer(io)
        r1 = w.write(rec_copy(self.ring))
        n = io.writes
        r2 = w.write(rec_copy(self.ring))
        self.assertEqual((r2["action"], r2["revision"], r2["content_sha256"]), ("unchanged", 1, r1["content_sha256"]))
        self.assertEqual(io.writes, n)                                       # nothing written the second time
        self.assertEqual(len(io.case_v), 1)

    def test_write_is_deterministic_across_independent_stores(self):
        a, b = FakeIO(), FakeIO()
        self.writer(a).write(rec_copy(self.ring))
        self.writer(b).write(rec_copy(self.ring))
        self.assertEqual(a.case_v, b.case_v)
        self.assertEqual(a.case_e, b.case_e)

    def test_changed_content_bumps_revision_and_replaces_edges(self):
        io = FakeIO()
        w = self.writer(io)
        w.write(rec_copy(self.ring))
        r = rec_copy(self.ring)
        # drop the connected card C00003-K1 and its transaction: exposure and affected ids must stay consistent with the graph
        r["case"]["affected_txn_ids"].remove("3001")
        bump(r, -50.0)
        r["graph_refs"]["connected_cards"].pop("C00003-K1")
        r["case"]["connected_card_ids"].remove("C00003-K1")
        res = w.write(r)
        self.assertEqual((res["action"], res["revision"]), ("updated", 2))
        gid = "CASE-TEST-W1"
        self.assertEqual(io.case_v[gid]["revision"], 2)
        self.assertNotIn("3001", {t for t, _ in io.case_e[gid]["CASE_TXN"]})
        self.assertEqual({t for t, _ in io.case_e[gid]["CASE_CONNECTED_TO"]}, {"C00002-K1"})
        self.assertEqual(len(io.case_v), 1)

    def test_same_case_with_a_different_as_of_is_refused(self):
        io = FakeIO()
        self.writer(io).write(rec_copy(self.ring))
        with self.assertRaises(CaseWriteError):
            self.writer(io, as_of=AS_OF + 100).write(rec_copy(self.ring))

    def test_invalid_case_is_rejected_before_any_write(self):
        io = FakeIO()
        r = rec_copy(self.ring)
        bump(r, 1)
        with self.assertRaises(CaseValidationError) as cm:
            self.writer(io).write(r)
        self.assertIn("G3", str(cm.exception))
        self.assertEqual((io.writes, io.case_v, io.log), (0, {}, []))

    def test_as_of_comes_from_the_runner(self):
        with self.assertRaises(CaseWriteError):
            CaseWriter(0, FakeIO())
        with self.assertRaises(CaseWriteError):
            CaseWriter("12345", FakeIO())

    def test_agent_uses_the_writer_and_reports_the_receipt(self):
        io = FakeIO()
        rec = make_record(case="TEST-W3", writer=CaseWriter(AS_OF, io))
        self.assertTrue(rec["case"]["written_to_graph"])
        self.assertEqual(rec["case"]["graph_case_id"], "CASE-TEST-W3")
        self.assertEqual(rec["graph_write"]["action"], "created")
        self.assertIn("CASE-TEST-W3", io.case_v)

    def test_agent_run_fails_closed_on_an_invalid_case(self):
        io = FakeIO(txn_epoch_shift=10_000_000)                              # the graph disagrees with the tools: the write is refused
        with self.assertRaises(CaseValidationError):
            make_record(case="TEST-W4", writer=CaseWriter(AS_OF, io))
        self.assertEqual(io.case_v, {})


class GraphIOGuardTests(unittest.TestCase):
    """Guards fire before any network call."""
    def test_only_case_edges_and_vertices_are_writable(self):
        io = GraphIO()
        with self.assertRaises(GraphIOError):
            io.upsert_case("CASE-X", {}, {"MADE": [("T1", {})]})
        with self.assertRaises(GraphIOError):
            io.upsert_case("CASE-X", {}, {"OWNS": [("C1", {})]})

    def test_reads_limited_to_known_types(self):
        with self.assertRaises(GraphIOError):
            GraphIO().get_vertex("Secret", "1")

    def test_delete_only_test_cases(self):
        with self.assertRaises(GraphIOError):
            GraphIO().delete_test_case("CASE-HHG-001")
        with self.assertRaises(GraphIOError):
            GraphIO().delete_test_case("CASE-SYN-001")

    def test_graph_is_fixed_and_protected_graph_refused(self):
        import agent.graphio as g
        self.assertEqual(g.GRAPH, "FraudInvestigation")
        with self.assertRaises(AssertionError):
            g.tg._raw("GET", "/restpp/graph/Transaction_Fraud/vertices/Transaction/1")


if __name__ == "__main__":
    unittest.main()
