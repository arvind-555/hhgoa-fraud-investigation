"""Offline tests: SAR narrative generation and the README answer-file assembler/validator (in-memory fake graph)."""
import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import answer_file as AF  # noqa: E402
from agent import sar as SAR  # noqa: E402
from agent.case_writer import CaseWriter  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from test_case_write import FakeIO, make_record, rec_copy  # noqa: E402
from test_simulator_exposure import AS_OF, Forced  # noqa: E402

README_TOP = ["case_id", "case", "evidence_requests", "next_best_actions", "sar", "stop_reason", "tool_calls", "tokens", "latency_s"]
README_CASE = ["status", "verdict", "fraud_probability", "pattern", "pattern_description", "affected_txn_ids", "first_suspicious_txn_id", "connected_card_ids",
               "connected_device_profiles", "exposure_usd", "evidence", "similar_prior_cases", "summary", "written_to_graph", "graph_case_id"]


class SarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ring = make_record()
        cls.legit = make_record(ring=False, responder=Forced("passed"))
        cls.denied = make_record(responder=Forced("denied_or_unrecognized"))

    def test_narrative_covers_who_what_when_where_how_why(self):
        n = self.ring["sar"]["narrative"]
        for label in ("Who:", "What:", "When:", "Where:", "How:", "Why suspicious:"):
            self.assertIn(label, n)
        self.assertTrue(6 <= len(SAR.sentences(n)) <= 12)

    def test_sar_fields_follow_the_readme(self):
        s = self.ring["sar"]
        self.assertTrue(s["file"])
        self.assertEqual(sorted(s), sorted(["file", "reason", "narrative", "subjects", "total_amount_usd", "activity_dates"]))
        self.assertEqual(s["total_amount_usd"], self.ring["case"]["exposure_usd"])
        self.assertTrue(all(re.match(r"^\d{4}-\d{2}-\d{2}$", d) for d in s["activity_dates"]))
        self.assertLessEqual(s["activity_dates"][0], s["activity_dates"][1])
        self.assertIn("C00001", s["subjects"])
        self.assertIn("C00001-K1", s["subjects"])
        self.assertIn("D_ring", s["subjects"])
        self.assertIn("R9", s["reason"])

    def test_template_passes_its_own_fact_check_and_states_no_probability(self):
        f = {"txns": [{"txn_id": 3000001, "amount": 100.0}, {"txn_id": 2001, "amount": 200.0}], "exposure": 370.0, "flagged_txn_id": 3000001, "first_suspicious": "2001", "simulated": []}
        n = self.ring["sar"]["narrative"]
        self.assertNotRegex(n, r"(?i)probab")
        self.assertEqual(SAR.check_narrative("One. Two.", f, []), ["narrative has 2 sentences (need 6-12)"] + [])

    def test_fact_check_rejects_unknown_ids_amounts_and_probability(self):
        f = {"txns": [{"txn_id": 3000001, "amount": 100.0}], "exposure": 100.0, "flagged_txn_id": 3000001, "first_suspicious": "", "simulated": ["x"]}
        base = " ".join(["Sentence number %d is fine." % i for i in range(7)])
        self.assertEqual(SAR.check_narrative(base + " Simulated response noted.", f, ["C00001"]), [])
        self.assertTrue(any("unknown identifier" in x for x in SAR.check_narrative(base + " Card C09999-K1 was used. Simulated.", f, ["C00001"])))
        self.assertTrue(any("amount not in the facts" in x for x in SAR.check_narrative(base + " A loss of $777.77 occurred. Simulated.", f, ["C00001"])))
        self.assertTrue(any("probability" in x for x in SAR.check_narrative(base + " The probability of fraud is high. Simulated.", f, ["C00001"])))
        self.assertTrue(any("simulated" in x for x in SAR.check_narrative(base, f, ["C00001"])))

    def test_no_report_means_blank_sar_with_a_reason(self):
        s = self.legit["sar"]
        self.assertEqual((s["file"], s["narrative"], s["subjects"], s["total_amount_usd"], s["activity_dates"]), (False, "", [], 0, []))
        self.assertIn("No report", s["reason"])

    def test_simulated_customer_response_is_labelled_in_the_narrative(self):
        self.assertTrue(self.denied["sar"]["file"])
        self.assertIn("simulated", self.denied["sar"]["narrative"].lower())

    def test_sar_never_names_other_benchmark_cases(self):
        self.assertNotRegex(json.dumps(self.ring["sar"]), r"HHG-\d")


class AnswerFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.io = FakeIO()
        cls.writer = CaseWriter(AS_OF, cls.io)
        cls.rec = make_record(case="TEST-A1", writer=cls.writer)
        cls.ans = AF.assemble(cls.rec)

    def errs(self, mutate, measured=None, graph=True):
        a = copy.deepcopy(self.ans)
        mutate(a)
        e, w = AF.validate_answer(a, AS_OF, self.io if graph else None, measured)
        return e

    def has(self, mutate, needle, **kw):
        e = self.errs(mutate, **kw)
        self.assertTrue(any(needle in x for x in e), f"{needle!r} not in {e}")

    def test_exact_readme_structure(self):
        self.assertEqual(list(self.ans), README_TOP)
        self.assertEqual(list(self.ans["case"]), README_CASE)
        self.assertTrue(all(sorted(x) == ["claim", "entity_ids", "ref", "source"] for x in self.ans["case"]["evidence"]))
        self.assertTrue(all(sorted(x) == ["action", "reason", "route"] for x in self.ans["next_best_actions"]["initial"]))
        self.assertEqual(sorted(self.ans["sar"]), sorted(["file", "reason", "narrative", "subjects", "total_amount_usd", "activity_dates"]))

    def test_valid_answer_passes_with_only_the_null_probability_warning(self):
        e, w = AF.validate_answer(self.ans, AS_OF, self.io)
        self.assertEqual(e, [])
        self.assertEqual(len(w), 1)
        self.assertIn("fraud_probability is null", w[0])
        self.assertIsNone(self.ans["case"]["fraud_probability"])

    def test_probability_policy_null_vs_number(self):
        e, w = AF.validate_answer(self.ans, AS_OF, self.io, probability_policy="number")
        self.assertTrue(any("must be a number" in x for x in e))
        self.has(lambda a: a["case"].update(fraud_probability=0.7), "none is calibrated")

    def test_summary_is_clean_and_short(self):
        s = self.ans["case"]["summary"]
        self.assertNotRegex(s, r"\[E\d+\]")
        self.assertTrue(2 <= len(SAR.sentences(s)) <= 6)
        i1, i2, i3 = s.index("Evidence strength"), s.index("Investigation uncertainty"), s.index("Calibrated probability")
        self.assertTrue(i1 < i2 < i3)

    def test_written_to_graph_is_resolved_against_the_graph(self):
        self.assertTrue(self.ans["case"]["written_to_graph"])
        self.assertEqual(self.ans["case"]["graph_case_id"], "CASE-TEST-A1")
        self.has(lambda a: a["case"].update(graph_case_id="CASE-NOPE"), "does not exist")
        self.has(lambda a: a["case"].update(written_to_graph=False), "must agree")

    def test_schema_violations(self):
        self.has(lambda a: a.pop("sar"), "top-level keys", graph=False)
        self.has(lambda a: a["case"].pop("summary"), "case keys", graph=False)
        self.has(lambda a: a["case"].update(status="nope"), "status", graph=False)
        self.has(lambda a: a["case"]["evidence"][0].update(source="rumour"), "evidence item", graph=False)
        self.has(lambda a: a["evidence_requests"].append({"type": "carrier_pigeon", "asked_after_step": 1, "assumed_response": "x"}), "evidence_requests", graph=False)
        self.has(lambda a: a["case"].update(affected_txn_ids=[3000001]), "list of strings", graph=False)
        self.has(lambda a: a.update(tool_calls="7"), "tool_calls", graph=False)

    def test_rule_violations(self):
        self.has(lambda a: a["case"].update(affected_txn_ids=a["case"]["affected_txn_ids"] + [a["case"]["affected_txn_ids"][0]]), "duplicates", graph=False)
        self.has(lambda a: a["case"].update(first_suspicious_txn_id="999"), "first_suspicious", graph=False)
        self.has(lambda a: a["case"].update(pattern_description=""), "pattern_description", graph=False)
        self.has(lambda a: a["next_best_actions"].update(what_changed="changed"), "what_changed", graph=False)
        self.has(lambda a: a["next_best_actions"]["final"][0].update(route="L2" if a["next_best_actions"]["final"][0]["route"] == "auto" else "auto"), "route for", graph=False)
        self.has(lambda a: a["next_best_actions"]["final"][0].update(reason="because"), "must cite a policy rule", graph=False)
        self.has(lambda a: a["sar"].update(file=False), "sar.file", graph=False)
        self.has(lambda a: a["sar"].update(narrative="Short."), "6-12 sentences", graph=False)
        self.has(lambda a: a["evidence_requests"].append({"type": "step_up_auth", "asked_after_step": 1, "assumed_response": "passed"}), "SIMULATED", graph=False)
        self.has(lambda a: a["case"].update(summary=a["case"]["summary"] + " See [E1]."), "internal evidence citations", graph=False)
        self.has(lambda a: a["case"].update(summary="One sentence only."), "summary must have 2-6", graph=False)
        self.has(lambda a: a["case"].update(summary=a["case"]["summary"] + " Also HHG-014."), "other case ids", graph=False)

    def test_legitimate_answer_rules(self):
        rec = make_record(ring=False, responder=Forced("passed"), case="TEST-A2")
        a = AF.assemble(rec)
        self.assertEqual(AF.validate_answer(a, AS_OF, None)[0], [])
        a["case"]["affected_txn_ids"] = ["3000001"]
        self.assertTrue(any("legitimate =>" in x for x in AF.validate_answer(a)[0]))

    def test_graph_checks(self):
        self.has(lambda a: a["case"]["affected_txn_ids"].append("4444444"), "does not exist in the graph")
        self.has(lambda a: (a["case"].update(exposure_usd=a["case"]["exposure_usd"] + 3), a["sar"].update(total_amount_usd=a["sar"]["total_amount_usd"] + 3)), "graph sum")
        self.has(lambda a: a["case"].update(similar_prior_cases=["CC-9"]), "does not exist in the graph")
        io = FakeIO(txn_epoch_shift=1_000_000)
        e, _ = AF.validate_answer(self.ans, AS_OF, io)
        self.assertTrue(any("after as_of" in x for x in e))
        io = FakeIO(closed_close=AS_OF + 5)
        e, _ = AF.validate_answer(self.ans, AS_OF, io)
        self.assertTrue(any("not visible at as_of" in x for x in e))

    def test_exposure_uses_deduplicated_ids(self):
        e = self.errs(lambda a: a["case"]["affected_txn_ids"].append(a["case"]["affected_txn_ids"][-1]))
        self.assertTrue(any("duplicates" in x for x in e))

    def test_measurements_must_match_the_runner(self):
        m = {"tool_calls": self.ans["tool_calls"], "tokens": self.ans["tokens"], "latency_s": self.ans["latency_s"]}
        self.assertEqual(self.errs(lambda a: None, measured=m), [])
        self.has(lambda a: a.update(tool_calls=a["tool_calls"] + 1), "tool_calls must equal", measured=m)
        self.has(lambda a: a.update(tokens=999), "tokens must equal", measured=m)
        self.has(lambda a: a.update(latency_s=a["latency_s"] + 3), "latency_s must equal", measured=m)

    def test_simulated_evidence_stays_marked_in_the_answer(self):
        rec = make_record(case="TEST-A3", responder=Forced("denied_or_unrecognized"))
        a = AF.assemble(rec)
        cust = [x for x in a["case"]["evidence"] if x["source"] == "customer"]
        self.assertTrue(cust and all(x["claim"].startswith("[SIMULATED]") for x in cust))
        self.assertTrue(a["evidence_requests"][0]["assumed_response"].startswith("[SIMULATED]"))
        self.assertEqual(a["evidence_requests"][0]["asked_after_step"], rec["evidence_requests"][0]["asked_after_step"])

    def test_write_answer_writes_valid_and_refuses_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            path, warns = AF.write_answer(self.ans, d, AS_OF, self.io)
            self.assertEqual(path.name, "TEST-A1.json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.ans)
            bad = copy.deepcopy(self.ans)
            bad["case_id"] = "TEST-A9"
            bad["case"]["exposure_usd"] += 1
            bad["sar"]["total_amount_usd"] += 1
            with self.assertRaises(AF.AnswerValidationError):
                AF.write_answer(bad, d, AS_OF, self.io)
            self.assertFalse((Path(d) / "TEST-A9.json").exists())

    def test_reasons_cite_policy_rules_for_every_action(self):
        for grp in ("initial", "final"):
            for x in self.ans["next_best_actions"][grp]:
                self.assertRegex(x["reason"], r"\b(R\d{1,2}|3a|3b)\b", x)


if __name__ == "__main__":
    unittest.main()
