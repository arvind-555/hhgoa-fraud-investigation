"""Offline tests of the LLM-orchestrated investigation: the model chooses tools / evidence requests / when to stop; the deterministic layer stays the authority.
A scripted chat client stands in for the model (no key needed). It exercises the protocol, the guards and the fallbacks; it is NOT evidence of real-model quality."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import answer_file as AF  # noqa: E402
from agent import llm as L  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.investigator import LLMInvestigator, SYSTEM, tool_schemas  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.permissions import FORBIDDEN_ARGS, TOOLS  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from test_case_write import FakeIO  # noqa: E402
from test_simulator_exposure import AS_OF, FakeSession  # noqa: E402

TXN = "3000001"
CARD = "C00001-K1"


def use(i, name, **inp):
    return {"type": "tool_use", "id": f"tu_{i}_{name}", "name": name, "input": inp}


class ScriptedChat:
    """Stand-in for the model. `plan(step, messages)` returns the content blocks for an investigation turn; `decide(view)` returns (tool, input) for the evidence decision."""
    model = "scripted"

    def __init__(self, plan, decide=None, fail_at=None):
        self.plan, self.decide, self.fail_at, self.turns, self.sent = plan, decide, fail_at, 0, []

    def chat(self, system, messages, tools, max_tokens, tool_choice=None):
        self.sent.append(json.dumps({"s": system, "m": messages, "t": tools}))
        if self.fail_at is not None and self.turns == self.fail_at:
            raise L.LLMUnavailable("network error: scripted failure")
        names = {t["name"] for t in tools}
        if "request_evidence" in names:
            view = json.loads(messages[0]["content"].split("\n")[1])
            tool, inp = (self.decide or (lambda v: ("no_further_evidence", {"reason": "conclusive"})))(view)
            blocks = [use(self.turns, tool, **inp)]
        else:
            step = sum(1 for m in messages if m["role"] == "assistant")
            blocks = self.plan(step, messages)
        self.turns += 1
        return {"content": blocks, "stop_reason": "tool_use", "input_tokens": len(self.sent[-1]) // 4, "output_tokens": 60, "model": "scripted"}


def thorough(step, m):
    if step == 0:
        return [use(0, "get_transaction_context", txn_id=TXN), use(1, "detect_fraud_patterns", txn_id=TXN), use(2, "get_card_history", card_id=CARD, hours=720, max_rows=300),
                use(3, "find_shared_devices", card_id=CARD), use(4, "find_prior_cases", card_id=CARD), use(5, "find_similar_cases", txn_id=TXN)]
    return [use(9, "finish_investigation", sufficient=True, rationale="signals, device links and prior cases checked")]


def minimal(step, m):
    if step == 0:
        return [use(0, "get_transaction_context", txn_id=TXN)]
    return [use(9, "finish_investigation", sufficient=True, rationale="single context check is enough")]


def erratic(step, m):
    return [use(step, "run_gsql", query="MATCH (n) RETURN n"), use(step + 10, "get_transaction_context", txn_id=TXN, as_of=9_999_999), use(step + 20, "get_card_history", card_id="C1; DROP"),
            use(step + 30, "get_transaction_context", txn_id=TXN), use(step + 40, "get_customer_history", customer_id="C00001")]       # never finishes


def run(plan, decide=None, ring=True, responder=None, trigger="risk_score", budget=None, fail_at=None, score=0.83, agentic=True, case="SYN-AG", **kw):
    fs = FakeSession(ring=ring, score=score)
    b = budget or L.TokenBudget()
    client = ScriptedChat(plan, decide, fail_at)
    inv = LLMInvestigator(client, b) if agentic else None
    a = Agent(ToolGateway(fs), Trigger(case, TXN, trigger), responder=responder or PendingResponder(), investigator=inv, budget=b if agentic else None, **kw)
    return a.run(), client, fs


def core(rec):
    c = rec["case"]
    return {"status": c["status"], "verdict": c["verdict"], "pattern": c["pattern"], "affected": c["affected_txn_ids"], "exposure": c["exposure_usd"], "connected": c["connected_card_ids"],
            "similar": c["similar_prior_cases"], "initial": [(a["action"], a["route"]) for a in rec["next_best_actions"]["initial"]],
            "final": [(a["action"], a["route"]) for a in rec["next_best_actions"]["final"]], "sar_file": rec["sar"]["file"], "scope": rec["exposure_scope"]}


class Base(unittest.TestCase):
    def setUp(self):
        self._old, self._tmp = L.CACHE_DIR, tempfile.TemporaryDirectory()
        L.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        L.CACHE_DIR = self._old
        self._tmp.cleanup()


class ProtocolTests(Base):
    def test_tool_schemas_are_the_permission_table_and_nothing_else(self):
        tools = tool_schemas()
        self.assertEqual({t["name"] for t in tools}, set(TOOLS) | {"finish_investigation"})
        for t in tools:
            self.assertFalse(set(t["input_schema"]["properties"]) & FORBIDDEN_ARGS, t["name"])
            self.assertFalse(t["input_schema"]["additionalProperties"])
        blob = json.dumps(tools) + SYSTEM
        L.guard_payload(blob)

    def test_model_choices_are_recorded_in_order(self):
        rec, client, fs = run(thorough)
        ag = rec["agentic"]
        self.assertEqual(ag["mode"], "llm_orchestrated")
        self.assertEqual([x["tool"] for x in ag["tool_order"] if x["source"] == "llm"],
                         ["get_transaction_context", "detect_fraud_patterns", "get_card_history", "find_shared_devices", "find_prior_cases", "find_similar_cases"])
        self.assertEqual(ag["forced_calls"], [])
        self.assertEqual(ag["invalid_calls"], 0)
        self.assertEqual(ag["coverage_required_by_llm"], 1.0)
        self.assertEqual(ag["finish"]["sufficient"], True)
        self.assertIn("Investigator: sufficient", rec["stop_reason"])

    def test_no_forbidden_material_is_ever_sent_to_the_model(self):
        for plan, kw in ((thorough, {}), (minimal, {}), (erratic, {}), (thorough, {"ring": False, "trigger": "customer_report"})):
            rec, client, fs = run(plan, **kw)
            for blob in client.sent:
                L.guard_payload(blob)
                for banned in ("0.83", "risk", "model_score", "model alert band", "HHG-", "snap_", "as_of", "seed"):
                    self.assertNotIn(banned, blob, banned)

    def test_the_model_never_sees_the_bank_score_even_as_a_band(self):
        rec, client, fs = run(thorough, score=0.95)
        for blob in client.sent:
            self.assertNotIn("0.95", blob)
            self.assertNotRegex(blob, r"(?i)alert band|score")

    def test_tokens_are_charged_from_reported_usage_by_phase(self):
        b = L.TokenBudget()
        rec, client, fs = run(thorough, budget=b)
        self.assertEqual(rec["tokens"], b.used)
        self.assertGreater(rec["tokens"], 0)
        pt = rec["agentic"]["phase_tokens"]
        self.assertIn("investigation", pt)
        self.assertIn("request_decision", pt)
        self.assertEqual(sum(pt.values()), b.used)


class DeterministicAuthorityTests(Base):
    def test_every_model_behaviour_yields_the_same_authoritative_case(self):
        base, _, _ = run(None, agentic=False)
        want = core(base)
        for plan in (thorough, minimal, erratic):
            rec, client, fs = run(plan)
            self.assertEqual(core(rec), want, plan.__name__)
            self.assertEqual(rec["case"]["exposure_usd"], base["case"]["exposure_usd"])
            self.assertEqual(AF.validate_answer(AF.assemble(rec), AS_OF, FakeIO())[0], [], plan.__name__)

    def test_minimal_model_on_a_signal_case_gets_the_required_evidence_forced(self):
        rec, client, fs = run(minimal)
        ag = rec["agentic"]
        self.assertEqual(set(ag["forced_calls"]), {"detect_fraud_patterns", "get_card_history", "find_shared_devices", "find_prior_cases"})
        self.assertEqual(ag["coverage_required_by_llm"], 0.2)
        self.assertEqual(ag["coverage_required_final"], 1.0)
        self.assertIn("C00002-K1", rec["case"]["connected_card_ids"])          # the ring was still found

    def test_minimal_model_on_a_clean_case_is_not_forced_beyond_the_signal_engine(self):
        rec, client, fs = run(minimal, ring=False)
        self.assertEqual(rec["agentic"]["forced_calls"], ["detect_fraud_patterns"])
        self.assertEqual(rec["case"]["affected_txn_ids"], ["3000001"])          # flagged transaction only: exposure needs no history
        self.assertEqual(fs.card_hist_calls, [])                                  # the card history was genuinely not needed

    def test_erratic_model_is_contained(self):
        rec, client, fs = run(erratic, budget=L.TokenBudget(200000))
        ag = rec["agentic"]
        self.assertGreaterEqual(ag["invalid_calls"], 8)                            # run_gsql, as_of argument and injected id refused every turn
        self.assertGreaterEqual(ag["duplicate_calls"], 1)
        self.assertEqual(ag["finish"]["sufficient"], None)
        self.assertIn("no progress", ag["finish"]["rationale"])
        self.assertEqual(client.turns - 1, 3)                                     # cut after two consecutive stalled turns (+ the evidence decision)
        self.assertGreater(ag["unnecessary_calls"], 0)

    def test_the_model_cannot_set_time_or_run_gsql(self):
        rec, client, fs = run(erratic, budget=L.TokenBudget(200000))
        # forbidden arguments and unknown tools were refused, so no session call ever carried them; only canonical cards were queried
        self.assertTrue(set(fs.card_hist_calls) <= {CARD, "C00002-K1", "C00003-K1"})
        self.assertGreaterEqual(rec["agentic"]["invalid_calls"], 8)


class StepLimitTests(Base):
    def test_step_limit_ends_a_model_that_keeps_finding_new_things_to_call(self):
        def endless(step, m):
            return [use(step, "find_similar_cases", txn_id=TXN, k=step + 1)]           # always a NEW valid call, never finishes
        rec, client, fs = run(endless, budget=L.TokenBudget(200000))
        ag = rec["agentic"]
        self.assertEqual(ag["finish"]["rationale"], "step limit (8) reached")
        self.assertEqual(ag["llm_tool_calls"], 8)
        self.assertEqual(ag["mode"], "llm_orchestrated")

    def test_a_single_stalled_turn_is_tolerated(self):
        def once(step, m):
            if step == 0:
                return [use(0, "get_transaction_context", txn_id=TXN)]
            if step == 1:
                return [use(1, "get_transaction_context", txn_id=TXN)]                   # duplicate: stalled once
            if step == 2:
                return [use(2, "detect_fraud_patterns", txn_id=TXN)]                     # progress again
            return [use(9, "finish_investigation", sufficient=True, rationale="done")]
        rec, client, fs = run(once)
        self.assertEqual(rec["agentic"]["finish"]["sufficient"], True)


class DecisionTests(Base):
    def test_model_cannot_skip_a_verification_the_policy_requires(self):
        rec, _, _ = run(thorough, decide=lambda v: ("no_further_evidence", {"reason": "looks fine"}), ring=False)
        d = rec["agentic"]["request_decision"]
        self.assertTrue(d["policy_required"])
        self.assertEqual(d["source"], "policy_overrode_llm")
        self.assertTrue(rec["evidence_requests"])

    def test_model_may_request_more_evidence_than_the_policy_requires(self):
        rec, _, _ = run(thorough, decide=lambda v: ("request_evidence", {"type": "customer_validation", "reason": "confirm"}), ring=False, score=0.1)
        d = rec["agentic"]["request_decision"]
        self.assertFalse(d["policy_required"])
        self.assertEqual((d["source"], d["final"]), ("llm", True))
        self.assertEqual(rec["evidence_requests"][0]["type"], "customer_validation")

    def test_model_declining_when_nothing_is_required_makes_no_request(self):
        rec, _, _ = run(thorough, ring=False, score=0.1)
        self.assertEqual(rec["agentic"]["request_decision"]["source"], "none")
        self.assertEqual(rec["evidence_requests"], [])
        self.assertEqual([a["action"] for a in rec["next_best_actions"]["final"]], ["ALLOW_TRANSACTION"])

    def test_request_type_must_be_allowed_for_the_case(self):
        # in-person transactions cannot use step-up authentication: the choice is refused and the deterministic type is used
        fs = FakeSession(ring=False)
        orig = fs.get_transaction_context
        fs.get_transaction_context = lambda txn_id: {**orig(txn_id), "transaction": {**orig(txn_id)["transaction"], "channel": "in_person"}}
        b = L.TokenBudget()
        a = Agent(ToolGateway(fs), Trigger("SYN-AG", TXN, "risk_score"), responder=PendingResponder(), investigator=LLMInvestigator(
            ScriptedChat(thorough, lambda v: ("request_evidence", {"type": "step_up_auth", "reason": "x"})), b), budget=b)
        rec = a.run()
        self.assertEqual(rec["evidence_requests"][0]["type"], "customer_validation")
        self.assertIn("request decision", rec["agentic"]["fallback"])

    def test_customer_report_always_uses_customer_validation(self):
        rec, _, _ = run(thorough, decide=lambda v: ("request_evidence", {"type": "step_up_auth", "reason": "x"}), ring=False, trigger="customer_report")
        self.assertEqual(rec["evidence_requests"][0]["type"], "customer_validation")

    def test_actions_and_routes_do_not_depend_on_the_models_wording(self):
        a, _, _ = run(thorough, decide=lambda v: ("request_evidence", {"type": "customer_validation", "reason": "A"}), ring=False)
        b, _, _ = run(thorough, decide=lambda v: ("request_evidence", {"type": "customer_validation", "reason": "completely different words"}), ring=False)
        self.assertEqual(core(a), core(b))


class FallbackTests(Base):
    def test_api_failure_at_the_first_turn_falls_back_to_the_deterministic_pipeline(self):
        base, _, _ = run(None, agentic=False)
        rec, client, fs = run(thorough, fail_at=0)
        ag = rec["agentic"]
        self.assertEqual((ag["mode"], ag["fallback_used"]), ("deterministic_fallback", True))
        self.assertIn("LLMUnavailable", ag["fallback"])
        self.assertEqual(core(rec), core(base))

    def test_failure_mid_investigation_still_completes_and_reuses_finished_calls(self):
        def slow(step, m):
            return [use(0, "get_transaction_context", txn_id=TXN)] if step == 0 else [use(1, "get_customer_history", customer_id="C00001")]
        base, _, _ = run(None, agentic=False)
        rec, client, fs = run(slow, fail_at=1)
        self.assertEqual(rec["agentic"]["mode"], "deterministic_fallback")
        self.assertEqual(core(rec), core(base))

    def test_budget_too_small_falls_back_without_sending_anything(self):
        base, _, _ = run(None, agentic=False)
        rec, client, fs = run(thorough, budget=L.TokenBudget(300))
        self.assertEqual(rec["agentic"]["mode"], "deterministic_fallback")
        self.assertIn("BudgetExceeded", rec["agentic"]["fallback"])
        self.assertEqual(client.sent, [])
        self.assertEqual(core(rec), core(base))

    def test_no_investigator_is_the_unchanged_deterministic_pipeline(self):
        rec, _, _ = run(None, agentic=False)
        self.assertEqual(rec["agentic"]["mode"], "deterministic")
        self.assertFalse(rec["agentic"]["fallback_used"])


class MetricsTests(Base):
    def test_unnecessary_and_non_contributing_calls_are_measured(self):
        def chatty(step, m):
            if step == 0:
                return [use(0, "get_transaction_context", txn_id=TXN), use(1, "get_customer_history", customer_id="C00001"), use(2, "get_transaction_context", txn_id=TXN)]
            return [use(9, "finish_investigation", sufficient=True, rationale="done")]
        rec, _, _ = run(chatty, ring=False)
        ag = rec["agentic"]
        self.assertEqual(ag["duplicate_calls"], 1)
        self.assertIn("get_customer_history", ag["non_contributing_calls"])
        self.assertEqual(ag["unnecessary_calls"], ag["invalid_calls"] + ag["duplicate_calls"] + len(ag["non_contributing_calls"]))

    def test_sources_are_labelled_llm_or_forced(self):
        rec, _, _ = run(minimal)
        srcs = {x["source"] for x in rec["agentic"]["tool_order"]}
        self.assertEqual(srcs, {"llm", "forced"})

    def test_agentic_block_is_internal_and_never_in_the_answer_file(self):
        rec, _, _ = run(thorough)
        ans = AF.assemble(rec)
        self.assertNotIn("agentic", json.dumps(ans))
        self.assertEqual(AF.validate_answer(ans, AS_OF, FakeIO())[0], [])

    def test_case_write_still_validates_and_records_nothing_the_validator_would_reject(self):
        from agent.case_writer import CaseWriter
        io = FakeIO()
        rec, _, _ = run(thorough, case="TEST-AG", writer=CaseWriter(AS_OF, io))
        self.assertTrue(rec["case"]["written_to_graph"])
        self.assertIn("CASE-TEST-AG", io.case_v)


if __name__ == "__main__":
    unittest.main()
