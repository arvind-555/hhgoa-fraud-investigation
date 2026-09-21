"""Offline tests of the evidence-driven decision matrix (src/agent/actions.py) and its integration: verdicts never come from a random draw, a customer report is
immutable, the strong-ring branch keeps its R6/R9/3a actions, the SAR decision is explained from case facts, and pattern-defining flags are not corroboration."""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import actions as A  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.orchestrator import Agent, InvariantError  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.sar import reason_text  # noqa: E402
from agent.schema import Trigger, Uncertainty  # noqa: E402
from agent.simulator import EvidenceSimulator  # noqa: E402
from test_simulator_exposure import FakeSession, Forced  # noqa: E402

NAMES = lambda acts: [a.action for a in acts]      # noqa: E731


def unc(strength, level="medium", n=1, conflicts=()):
    return Uncertainty(strength, n, list(conflicts), [], level, level != "low", {"calibrated": False}, [])


def ctx(**k):
    d = {"exposure": 100.0, "fired": [], "channel": "online", "trigger_type": "risk_score", "model_band": "high", "connected_card_ids": [], "evidence_ids_by_signal": {}}
    d.update(k)
    return d


def run(trigger="risk_score", responder=None, ring=False, **kw):
    return Agent(ToolGateway(FakeSession(ring=ring)), Trigger("SYN-M", "3000001", trigger), responder=responder or PendingResponder(), **kw).run()


class ClassTests(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(A.decision_class(unc("strong"), ctx()), "A")
        self.assertEqual(A.decision_class(unc("moderate"), ctx()), "B")
        self.assertEqual(A.decision_class(unc("weak"), ctx()), "C")
        self.assertEqual(A.decision_class(unc("none"), ctx()), "C")
        self.assertEqual(A.decision_class(unc("moderate"), ctx(trigger_type="customer_report")), "T+")
        self.assertEqual(A.decision_class(unc("weak"), ctx(trigger_type="customer_report")), "T")
        self.assertEqual(A.decision_class(unc("strong"), ctx(trigger_type="customer_report")), "A")


class MatrixTests(unittest.TestCase):
    """Rows of the matrix: verdict and the required actions for each evidence class and each verification state."""

    def decide(self, u, c, outcome):
        init = A.initial_actions(u, c)
        fin, o = A.final_actions(init, u, c, outcome)
        v, st = A.decide_verdict(u, c, o, fin)
        return init, fin, v, st

    def test_A_strong_ring_keeps_r6_r9_3a_after_every_response(self):
        c = ctx(fired=["S01"], exposure=3778.14, connected_card_ids=["C2", "C3"], evidence_ids_by_signal={"S01": ["E1"]})
        for outcome in ("no_reply", "passed", "confirmed", "failed", "denied"):
            init, fin, v, st = self.decide(unc("strong"), c, outcome)
            names = set(NAMES(fin))
            self.assertTrue({"CREATE_CASE", "FILE_REPORT", "MONITOR_CONNECTED_CARDS"} <= names, (outcome, names))
            self.assertEqual(v, "fraud", outcome)
            self.assertEqual(A.policy_gaps(unc("strong"), c, outcome, v, init, fin), [], outcome)
        for outcome in ("passed", "confirmed", "no_reply"):
            self.assertIn("ESCALATE_TO_ANALYST", NAMES(self.decide(unc("strong"), c, outcome)[1]), outcome)
        f = {a.action: a.route for a in self.decide(unc("strong"), c, "passed")[1]}
        self.assertEqual((f["FILE_REPORT"], f["CREATE_CASE"], f["MONITOR_CONNECTED_CARDS"]), ("L2", "auto", "auto"))

    def test_T_plus_customer_report_with_one_validated_source_is_r2_without_a_request(self):
        c = ctx(fired=["S07"], trigger_type="customer_report", exposure=1906.07, channel="online")
        init, fin, v, st = self.decide(unc("moderate", "low"), c, "pending")
        self.assertEqual(NAMES(init), ["BLOCK_CARD", "CREATE_CASE", "FILE_REPORT"])
        self.assertEqual({a.action: a.route for a in init}, {"BLOCK_CARD": "L1", "CREATE_CASE": "auto", "FILE_REPORT": "L2"})
        self.assertEqual((v, st), ("fraud", "closed_fraud"))
        small = ctx(fired=["S07"], trigger_type="customer_report", exposure=400.0)
        self.assertNotIn("FILE_REPORT", NAMES(A.initial_actions(unc("moderate", "low"), small)))          # exposure <= $1,000 and no shared origin
        big = ctx(fired=["S07"], trigger_type="customer_report", exposure=2600.0)
        self.assertEqual({a.action: a.route for a in A.initial_actions(unc("moderate", "low"), big)}["BLOCK_CARD"], "L2")

    def test_T_customer_report_only_verifies_and_is_never_closed_by_a_confirmation(self):
        c = ctx(trigger_type="customer_report", exposure=60.0)
        init, fin, v, st = self.decide(unc("weak"), c, "no_reply")
        self.assertEqual(NAMES(init), ["VERIFY_WITH_CUSTOMER", "MONITOR_CARD", "CREATE_CASE"])
        self.assertEqual(NAMES(fin), ["MONITOR_CARD", "DECLINE_TRANSACTION", "CREATE_CASE"])      # R4
        self.assertEqual(v, "uncertain")
        init, fin, v, st = self.decide(unc("weak"), c, "confirmed")                                  # cannot happen with the real simulator; must not close anyway
        self.assertNotIn("CLOSE_NO_FRAUD", NAMES(fin))
        self.assertEqual((NAMES(fin), v), (["ESCALATE_TO_ANALYST", "CREATE_CASE"], "uncertain"))
        init, fin, v, st = self.decide(unc("weak"), c, "denied")
        self.assertEqual((NAMES(fin)[:2], v), (["BLOCK_CARD", "CREATE_CASE"], "fraud"))

    def test_B_and_C_follow_r1_r3_r4(self):
        for strength, fired in (("moderate", ["S08"]), ("weak", ["S05"])):
            c = ctx(fired=fired, exposure=200.0)
            init, fin, v, st = self.decide(unc(strength), c, "no_reply")
            self.assertEqual(NAMES(init)[0], "STEP_UP_AUTH")
            self.assertIn("CREATE_CASE", NAMES(init))                                                # 3a: evidence requested
            self.assertNotIn("BLOCK_CARD", NAMES(init))
            self.assertEqual((NAMES(fin), v), (["MONITOR_CARD", "DECLINE_TRANSACTION", "CREATE_CASE"], "uncertain"))
            self.assertEqual(NAMES(self.decide(unc(strength), c, "confirmed")[1]), ["CLOSE_NO_FRAUD"])
            self.assertEqual(self.decide(unc(strength), c, "confirmed")[2], "legitimate")
            self.assertEqual(self.decide(unc(strength), c, "passed")[2], "legitimate")
            self.assertEqual(self.decide(unc(strength), c, "denied")[2], "fraud")
            f = self.decide(unc(strength), c, "failed")
            self.assertEqual(f[2], "uncertain")
            self.assertNotIn("BLOCK_CARD", NAMES(f[1]))
        big = ctx(fired=["S08"], exposure=800.0)
        self.assertIn("ESCALATE_TO_ANALYST", NAMES(self.decide(unc("moderate"), big, "no_reply")[1]))     # R4: escalate above $500

    def test_no_evidence_no_alert_allows(self):
        init, fin, v, st = self.decide(unc("none", "low"), ctx(model_band="low"), "pending")
        self.assertEqual((NAMES(fin), v), (["ALLOW_TRANSACTION"], "legitimate"))

    def test_the_decision_is_a_pure_function_of_the_evidence(self):
        c = ctx(fired=["S08"], exposure=350.0)
        first = [(NAMES(i), NAMES(f), v, s) for i, f, v, s in (self.decide(unc("moderate"), c, o) for o in ("no_reply", "passed", "failed", "confirmed", "denied"))]
        for _ in range(50):
            again = [(NAMES(i), NAMES(f), v, s) for i, f, v, s in (self.decide(unc("moderate"), c, o) for o in ("no_reply", "passed", "failed", "confirmed", "denied"))]
            self.assertEqual(first, again)

    def test_policy_gap_guard_flags_dropped_required_actions(self):
        c = ctx(fired=["S01"], exposure=3778.14, connected_card_ids=["C2"], evidence_ids_by_signal={"S01": ["E1"]})
        init = A.initial_actions(unc("strong"), c)
        only_escalate = [A.act("ESCALATE_TO_ANALYST", 3778.14, "x", ["R8"])]
        gaps = A.policy_gaps(unc("strong"), c, "passed", "fraud", init, only_escalate)
        self.assertTrue(any("FILE_REPORT" in g for g in gaps))
        self.assertTrue(any("MONITOR_CONNECTED_CARDS" in g for g in gaps))
        self.assertTrue(any("CREATE_CASE" in g for g in gaps))
        self.assertEqual(A.policy_gaps(unc("strong"), c, "passed", "fraud", init, A.final_actions(init, unc("strong"), c, "passed")[0]), [])


class IntegrationTests(unittest.TestCase):
    def test_default_simulator_never_decides_a_verdict(self):
        """With the default assumption (no reply) an uncorroborated alert is UNCERTAIN, whatever the case id; a strong ring is fraud from the evidence alone."""
        verdicts = set()
        for i in range(40):
            a = Agent(ToolGateway(FakeSession(ring=False)), Trigger(f"SYN-{i:03d}", "3000001", "risk_score"), responder=EvidenceSimulator())
            rec = a.run()
            verdicts.add((rec["case"]["verdict"], tuple(x["action"] for x in rec["next_best_actions"]["final"])))
            self.assertEqual(rec["evidence_requests"][0]["outcome"], "no_response")
            self.assertEqual(rec["simulated_evidence_ids"], [])
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(next(iter(verdicts))[0], "uncertain")
        ring = {run(ring=True, responder=EvidenceSimulator())["case"]["verdict"] for _ in range(3)}
        self.assertEqual(ring, {"fraud"})

    def test_request_type_matches_the_recommended_verification(self):
        for trig, ring in (("risk_score", False), ("customer_report", False), ("risk_score", True)):
            rec = run(trig, EvidenceSimulator(), ring=ring)
            first = next(a["action"] for a in rec["next_best_actions"]["initial"] if a["action"] in ("STEP_UP_AUTH", "VERIFY_WITH_CUSTOMER"))
            self.assertEqual(rec["evidence_requests"][0]["type"], "step_up_auth" if first == "STEP_UP_AUTH" else "customer_validation", trig)

    def test_customer_report_case_is_open_uncertain_r4_never_closed_legitimate(self):
        rec = run("customer_report", EvidenceSimulator())
        fin = [x["action"] for x in rec["next_best_actions"]["final"]]
        self.assertEqual(rec["case"]["verdict"], "uncertain")
        self.assertIn("CREATE_CASE", fin)
        self.assertIn("DECLINE_TRANSACTION", fin)
        self.assertNotIn("CLOSE_NO_FRAUD", fin)
        self.assertEqual(rec["case"]["status"], "open")

    def test_ring_case_sar_is_decided_from_case_facts_not_from_the_actions(self):
        rec = run(ring=True, responder=EvidenceSimulator("cardholder_confirms"))
        fin = {a["action"] for a in rec["next_best_actions"]["final"]}
        self.assertTrue({"CREATE_CASE", "FILE_REPORT", "MONITOR_CONNECTED_CARDS", "ESCALATE_TO_ANALYST"} <= fin)
        self.assertTrue(rec["sar"]["file"])
        rec2 = run("customer_report", EvidenceSimulator())
        self.assertFalse(rec2["sar"]["file"])
        self.assertNotIn("final actions", rec2["sar"]["reason"])
        self.assertIn("verdict is uncertain", rec2["sar"]["reason"])

    def test_no_report_reason_names_the_unmet_policy_conditions(self):
        facts = {"exposure": 300.0, "fired": ["S08"], "s07": None, "verdict": "fraud", "strength": "moderate", "final_actions": ["BLOCK_CARD"]}
        r = reason_text(facts, False, "card_not_present_fraud")
        self.assertIn("none of the policy 3a qualifying conditions", r)
        self.assertNotIn("BLOCK_CARD", r)
        facts["verdict"] = "uncertain"
        self.assertIn("verdict is uncertain", reason_text(facts, False, "none"))

    def test_evidence_independence_pattern_defining_flags_are_not_corroboration(self):
        """FakeSession fires only S01; add the identity flags that DEFINE the ring rule and check they are not counted as a second independent source."""
        class RingWithIdentityFlags(FakeSession):
            def detect_fraud_patterns(self, txn_id):
                d = super().detect_fraud_patterns(txn_id)
                for sid in ("S10", "S11", "S12"):
                    d["signals"].append({"id": sid, "name": sid, "tier": 6, "rating": "LOW", "source": "identity_flags", "fired": True, "detail": {}, "entity_ids": []})
                    d["fired"].append(sid)
                d["independence"]["low_context_sources"] = ["identity_flags"]
                return d
        rec = Agent(ToolGateway(RingWithIdentityFlags(ring=True)), Trigger("SYN-I", "3000001", "risk_score"), responder=EvidenceSimulator()).run()
        u = rec["uncertainty"]
        self.assertEqual(u["evidence_strength"], "strong")
        self.assertEqual(u["independent_sources"], 1)                           # S01 only: it was 2 when identity flags were counted
        self.assertEqual(u["pattern_defining"], ["S10", "S11", "S12"])
        self.assertIn("pattern-defining", rec["case"]["summary"])
        self.assertIn("1 validated independent source", rec["case"]["summary"])
        self.assertNotIn("2 independent", rec["case"]["summary"])

    def test_invariant_blocks_a_final_list_that_drops_required_actions(self):
        import agent.actions as act_mod
        real = act_mod.final_actions

        def bad(initial, unc_, ctx_, outcome):
            return [act_mod.act("ESCALATE_TO_ANALYST", ctx_["exposure"], "x", ["R8"])], outcome
        act_mod.final_actions = bad
        try:
            with self.assertRaises(InvariantError):
                run(ring=True, responder=Forced("passed"))
        finally:
            act_mod.final_actions = real

    def test_no_hash_or_random_anywhere_in_the_decision_path(self):
        for name in ("actions.py", "simulator.py", "orchestrator.py", "sar.py", "reasoner.py"):
            src = (ROOT / "src" / "agent" / name).read_text(encoding="utf-8")
            code = re.sub(r'""".*?"""', "", "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#")), flags=re.S)
            for banned in ("hashlib", "import random", "random.", "sha256", "secrets."):
                self.assertNotIn(banned, code, f"{name}: {banned}")


if __name__ == "__main__":
    unittest.main()
