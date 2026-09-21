"""Integration fixes v1: (1) equivalent tool calls reuse one result, different queries never do, forbidden / temporal arguments stay forbidden;
(2) the LLM can only SUGGEST additional evidence: a deterministic policy gate decides, and a policy-settled case (HHG-006 shape) is unchanged by a model suggestion."""
import inspect
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import llm as L  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.investigator import LLMInvestigator  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.permissions import DEFAULTS, FORBIDDEN_ARGS, PermissionDenied, canonical_key, validate_call  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from agent.simulator import EvidenceSimulator  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402
from test_agentic import TXN, ScriptedChat, core, thorough  # noqa: E402
from test_simulator_exposure import FakeSession  # noqa: E402

CARD = "C00001-K1"


def key(tool, **args):
    return canonical_key(tool, validate_call(tool, args))


class CanonicalKeyTests(unittest.TestCase):
    def test_defaults_table_matches_the_real_tool_signatures(self):
        for tool, d in DEFAULTS.items():
            params = inspect.signature(getattr(InvestigationSession, tool)).parameters
            for arg, val in d.items():
                self.assertEqual(params[arg].default, val, f"{tool}.{arg}")

    def test_equivalent_calls_share_one_key(self):
        self.assertEqual(key("find_shared_devices", card_id=CARD), key("find_shared_devices", card_id=CARD, days=30))           # default passed explicitly
        self.assertEqual(key("find_connected_entities", card_id=CARD), key("find_connected_entities", card_id=CARD, days=30))
        self.assertEqual(key("find_similar_cases", txn_id=TXN), key("find_similar_cases", txn_id=TXN, k=10))
        self.assertEqual(key("get_card_history", card_id=CARD, hours=48, max_rows=100), key("get_card_history", card_id=CARD))
        self.assertEqual(key("get_card_history", card_id=CARD, hours=720, max_rows=300), key("get_card_history", card_id=CARD, max_rows=300, hours=720))   # argument order
        self.assertEqual(key("retrieve_policy", rule_ids=["R2", "R1"]), key("retrieve_policy", rule_ids=["R1", "R2", "R2"]))                              # list order / repeats
        self.assertEqual(key("get_policy_context", signals=["S10", "S01"]), key("get_policy_context", signals=["S01", "S10"]))

    def test_materially_different_calls_never_share_a_key(self):
        self.assertNotEqual(key("find_similar_cases", txn_id=TXN, k=5), key("find_similar_cases", txn_id=TXN))                 # fewer results is a different result
        self.assertNotEqual(key("get_customer_history", customer_id="C00001", lookback_days=90), key("get_customer_history", customer_id="C00001"))
        self.assertNotEqual(key("get_card_history", card_id=CARD, hours=720, max_rows=300), key("get_card_history", card_id=CARD))
        self.assertNotEqual(key("find_shared_devices", card_id=CARD, days=7), key("find_shared_devices", card_id=CARD))
        self.assertNotEqual(key("find_shared_devices", card_id=CARD), key("find_shared_devices", card_id="C00002-K1"))            # another card
        self.assertNotEqual(key("find_shared_devices", card_id=CARD), key("find_connected_entities", card_id=CARD))               # another tool
        self.assertNotEqual(key("retrieve_policy", rule_ids=["R1"]), key("retrieve_policy", rule_ids=["R1", "R2"]))              # different set
        self.assertNotEqual(key("get_transaction_context", txn_id="3000001"), key("get_transaction_context", txn_id="3000002"))

    def test_forbidden_and_temporal_arguments_stay_forbidden(self):
        for bad in sorted(FORBIDDEN_ARGS):
            with self.assertRaises(PermissionDenied, msg=bad):
                validate_call("find_shared_devices", {"card_id": CARD, bad: 1})
        for tool, args in (("find_shared_devices", {"card_id": CARD, "days": 31}), ("find_shared_devices", {"card_id": CARD, "days": 0}),
                           ("get_customer_history", {"customer_id": "C00001", "lookback_days": 365}), ("get_card_history", {"card_id": CARD, "hours": 4801}),
                           ("find_similar_cases", {"txn_id": TXN, "k": 21}), ("find_shared_devices", {"card_id": CARD, "days": True}),
                           ("find_shared_devices", {"card_id": CARD, "days": "30"})):
            with self.assertRaises(PermissionDenied, msg=(tool, args)):
                validate_call(tool, args)                                    # out-of-range or mistyped values are refused, never "normalised" into a default
        # the canonical key is computed only from arguments that already passed validation, so as_of / epochs cannot enter it
        self.assertEqual(sorted(k for k in DEFAULTS.get("find_shared_devices", {})), ["days"])

    def test_gateway_reuses_equivalent_results_and_only_them(self):
        fs = FakeSession(ring=True)
        gw = ToolGateway(fs)
        gw.call("find_shared_devices", {"card_id": CARD})
        n = len(gw.calls)
        self.assertTrue(gw.has_result("find_shared_devices", {"card_id": CARD, "days": 30}))
        gw.call("find_shared_devices", {"card_id": CARD, "days": 30})
        self.assertEqual(len(gw.calls), n)                                   # the equivalent call was served from the same result: no second execution
        self.assertFalse(gw.has_result("find_shared_devices", {"card_id": CARD, "days": 7}))
        gw.call("find_shared_devices", {"card_id": CARD, "days": 7})
        self.assertEqual(len(gw.calls), n + 1)                               # a materially different call is executed
        with self.assertRaises(PermissionDenied):
            gw.call("find_shared_devices", {"card_id": CARD, "days": 30, "as_of": 1})
        with self.assertRaises(PermissionDenied):
            gw.has_result("find_shared_devices", {"card_id": CARD, "before_epoch": 1})


class T7Session(FakeSession):
    """A customer report on a card with one validated burst signal (S07): customer denial + one independent source = policy-settled (class T+, no request required)."""
    def detect_fraud_patterns(self, txn_id):
        sig = {"id": "S07", "name": "burst", "tier": 4, "rating": "MEDIUM", "source": "card_sequence", "fired": True, "detail": {}, "entity_ids": [3000000]}
        return {"signals": [sig], "fired": ["S07"], "independence": {"n_tier_1_to_5_sources": 1, "tier_1_to_5_sources": ["card_sequence"], "low_context_sources": []}}


class PolicyGateRegression(unittest.TestCase):
    def setUp(self):
        self._old, self._tmp = L.CACHE_DIR, tempfile.TemporaryDirectory()
        L.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        L.CACHE_DIR = self._old
        self._tmp.cleanup()

    def run_case(self, decide=None, llm=True, session=T7Session, trigger="customer_report"):
        b = L.TokenBudget()
        inv = LLMInvestigator(ScriptedChat(thorough, decide, None), b) if llm else None
        return Agent(ToolGateway(session(ring=False)), Trigger("SYN-G", TXN, trigger), responder=EvidenceSimulator(), investigator=inv, budget=b if llm else None).run()

    def test_hhg006_shape_a_step_up_suggestion_is_rejected_and_nothing_changes(self):
        base = self.run_case(llm=False)
        self.assertEqual(base["case"]["verdict"], "fraud")
        self.assertEqual([x["action"] for x in base["next_best_actions"]["final"]], ["BLOCK_CARD", "CREATE_CASE"])
        self.assertEqual(base["evidence_requests"], [])
        llm = self.run_case(decide=lambda v: ("request_evidence", {"type": "step_up_auth", "reason": "confirm"}))
        d = llm["agentic"]["request_decision"]
        self.assertEqual((d["policy_required"], d["policy_decision"], d["final"], d["source"]), (False, "REJECTED_NOT_REQUIRED", False, "none"))
        self.assertEqual(d["model_suggestion"], {"request": True, "type": "step_up_auth"})     # recorded for audit
        self.assertEqual(llm["evidence_requests"], [])                                          # never executed
        self.assertEqual(llm["simulated_evidence_ids"], [])
        self.assertEqual(core(llm)["initial"], core(base)["initial"])
        self.assertEqual({k: v for k, v in core(llm).items() if k != "scope"}, {k: v for k, v in core(base).items() if k != "scope"})   # verdict, actions, routes, SAR, exposure identical
        self.assertEqual(llm["case"]["evidence"], base["case"]["evidence"])
        self.assertEqual(llm["uncertainty"], base["uncertainty"])
        self.assertNotIn("DECLINE_TRANSACTION", [x["action"] for x in llm["next_best_actions"]["final"]])      # R4 was not triggered by the rejected suggestion

    def test_a_rejected_suggestion_is_never_a_case_action(self):
        llm = self.run_case(decide=lambda v: ("request_evidence", {"type": "step_up_auth", "reason": "x"}))
        acts = [x["action"] for x in llm["next_best_actions"]["initial"] + llm["next_best_actions"]["final"]]
        self.assertNotIn("STEP_UP_AUTH", acts)

    def test_when_policy_requires_verification_the_gate_allows_it(self):
        rec = Agent(ToolGateway(FakeSession(ring=False)), Trigger("SYN-G2", TXN, "risk_score"), responder=EvidenceSimulator(),
                    investigator=LLMInvestigator(ScriptedChat(thorough, lambda v: ("request_evidence", {"type": "customer_validation", "reason": "x"}), None), L.TokenBudget()),
                    budget=L.TokenBudget()).run()
        d = rec["agentic"]["request_decision"]
        self.assertEqual((d["policy_required"], d["policy_decision"], d["final"]), (True, "ALLOWED_POLICY_REQUIRED", True))
        self.assertTrue(rec["evidence_requests"])
        self.assertTrue(all(q["simulated"] for q in rec["evidence_requests"]))


class CanonicalSimilarCasesTests(unittest.TestCase):
    """The model always runs find_similar_cases with the canonical k=10, so its result equals the deterministic pipeline's result and is reused."""

    def test_schema_offers_only_the_canonical_k(self):
        from agent.investigator import tool_schemas
        k = next(s for s in tool_schemas() if s["name"] == "find_similar_cases")["input_schema"]["properties"]["k"]
        self.assertEqual(k, {"type": "integer", "enum": [10]})

    def test_a_model_supplied_k_is_replaced_and_the_result_is_the_pipelines(self):
        from agent.investigator import LLMInvestigator
        fs = FakeSession(ring=True)
        gw = ToolGateway(fs)
        inv = LLMInvestigator(None, L.TokenBudget())
        for k in (5, 10, 1, 20):
            inv._execute(gw, [{"id": f"u{k}", "name": "find_similar_cases", "input": {"txn_id": TXN, "k": k}}], None, 0)
        self.assertEqual({tuple(sorted(c["args"].items())) for c in gw.calls if c["tool"] == "find_similar_cases"}, {(("k", 10), ("txn_id", TXN))})
        self.assertEqual(sum(1 for c in gw.calls if c["tool"] == "find_similar_cases"), 1)          # all four collapsed to one executed canonical query
        self.assertTrue(gw.has_result("find_similar_cases", {"txn_id": TXN}))                        # the deterministic layer's canonical call reuses it

    def test_forbidden_arguments_still_cannot_be_supplied(self):
        from agent.investigator import LLMInvestigator
        gw = ToolGateway(FakeSession(ring=True))
        inv = LLMInvestigator(None, L.TokenBudget())
        for bad in ("as_of", "before_epoch", "risk_score", "snap_class", "max_epoch", "benchmark_id"):
            inv._execute(gw, [{"id": "u", "name": "find_similar_cases", "input": {"txn_id": TXN, bad: 1}}], None, 0)
        self.assertEqual(inv.log["invalid_calls"], 6)
        self.assertEqual([c for c in gw.calls if c["tool"] == "find_similar_cases"], [])


if __name__ == "__main__":
    unittest.main()
