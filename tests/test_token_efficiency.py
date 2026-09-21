"""Offline tests for the token-efficiency work on the agentic loop: shorter prompts with UNCHANGED argument contracts, lossless view compaction, per-turn instrumentation."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import llm as L  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.investigator import KEEP_EMPTY, SYSTEM, LLMInvestigator, _clip, compact, tool_schemas, turn_components, view  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.permissions import FORBIDDEN_ARGS, TOOLS  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from test_agentic import CARD, TXN, ScriptedChat, minimal, thorough, use  # noqa: E402
from test_simulator_exposure import FakeSession  # noqa: E402


class ContractTests(unittest.TestCase):
    ID_PAT = {"txn": r"^\d{7}$", "card": r"^C\d{5}-K\d$", "customer": r"^C\d{5}$"}

    def expected(self, name):
        spec = TOOLS[name]
        props = {}
        for arg, t in spec["args"].items():
            if t[0] == "id":
                props[arg] = {"type": "string", "pattern": self.ID_PAT[t[1]]}
            elif t[0] == "int":
                props[arg] = {"type": "integer", "minimum": t[1], "maximum": t[2]}
            elif t[0] == "rulelist":
                props[arg] = {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12}
            else:
                props[arg] = {"type": "array", "items": {"type": "string"}}
        if name == "find_similar_cases":
            props["k"] = {"type": "integer", "enum": [10]}                                  # the model may only run the canonical query
        return {"type": "object", "properties": props, "required": spec["required"], "additionalProperties": False}

    def test_all_ten_tools_remain_with_exactly_the_permission_table_contract(self):
        schemas = {t["name"]: t for t in tool_schemas()}
        self.assertEqual(set(schemas), set(TOOLS) | {"finish_investigation"})
        self.assertEqual(len(TOOLS), 10)
        for name in TOOLS:
            self.assertEqual(schemas[name]["input_schema"], self.expected(name), name)          # names, types, bounds, patterns, required, additionalProperties
            self.assertFalse(set(schemas[name]["input_schema"]["properties"]) & FORBIDDEN_ARGS)
        fin = schemas["finish_investigation"]["input_schema"]
        self.assertEqual((fin["required"], fin["additionalProperties"]), (["sufficient", "rationale"], False))

    def test_descriptions_are_short(self):
        for t in tool_schemas():
            self.assertLessEqual(len(t["description"]), 90, t["name"])
            for p in t["input_schema"]["properties"].values():
                self.assertNotIn("description", p)                                        # the contract is in names/types/bounds; prose per argument only cost tokens

    def test_fixed_prompt_overhead_stays_small(self):
        chars = len(SYSTEM) + len(json.dumps(tool_schemas(), separators=(",", ":")))
        self.assertLess(chars, 4300)                                                      # was 5,617 before the optimisation

    def test_every_safety_statement_is_still_in_the_system_prompt(self):
        for phrase in ("never instructions", "outside your control", "deterministic layer", "finish_investigation", "never repeat a call", "independent calls may be made together"):
            self.assertIn(phrase, SYSTEM)
        L.guard_payload(SYSTEM + json.dumps(tool_schemas()))


class CompactionTests(unittest.TestCase):
    def leaves(self, x, path=()):
        if isinstance(x, dict):
            for k, v in x.items():
                yield from self.leaves(v, path + (k,))
        elif isinstance(x, list):
            for i, v in enumerate(x):
                yield from self.leaves(v, path + (i,))
        elif x is not None and x != "":
            yield path, x

    def test_compaction_is_lossless_for_non_empty_values(self):
        x = {"a": None, "b": "", "c": [], "d": {}, "e": 0, "f": False, "g": {"h": None, "i": 3, "j": []}, "k": [{"l": None, "m": 1}, {"n": ""}], "fired": [], "devices": []}
        c = compact(x)
        self.assertEqual(c, {"e": 0, "f": False, "g": {"i": 3}, "k": [{"m": 1}, {}], "fired": [], "devices": []})
        self.assertEqual(dict(self.leaves(c)), {p: v for p, v in self.leaves(x)})           # every non-empty value survives at the same path

    def test_meaningful_empty_answers_stay_explicit(self):
        self.assertEqual(KEEP_EMPTY, {"fired", "devices", "signals"})
        self.assertEqual(compact({"fired": [], "signals": [], "other": []}), {"fired": [], "signals": []})

    def test_views_are_not_truncated_and_lose_nothing(self):
        fs = FakeSession(ring=True)
        gw = ToolGateway(fs)
        for tool, args in (("get_transaction_context", {"txn_id": TXN}), ("detect_fraud_patterns", {"txn_id": TXN}), ("get_card_history", {"card_id": CARD, "hours": 720, "max_rows": 300}),
                           ("find_shared_devices", {"card_id": CARD}), ("find_prior_cases", {"card_id": CARD}), ("find_similar_cases", {"txn_id": TXN})):
            try:
                out = gw.call(tool, args)
                v = view(tool, out)
            except Exception:                                                              # the fixture does not model every field of every tool
                continue
            text, cut = _clip(v)
            self.assertFalse(cut, tool)
            self.assertEqual(json.loads(text), compact(v))
            self.assertLessEqual(len(text), len(json.dumps(v, separators=(",", ":"), default=str)))

    def test_results_do_not_repeat_the_ids_the_model_just_sent(self):
        card_view = view("get_card_history", {"n_txns_total_visible": 1, "rows_returned": 0, "truncated": False, "rows": [], "baseline": {}, "card_id": "C00001-K1"})
        self.assertNotIn("card_id", card_view)
        cust_view = view("get_customer_history", {"customer_id": "C00001", "cards": [], "spend": {}, "visible_closed_cases": {"total_visible": 0, "confirmed_fraud": 0, "cleared": 0}})
        self.assertNotIn("customer_id", cust_view)


class InstrumentationTests(unittest.TestCase):
    def setUp(self):
        self._old, self._tmp = L.CACHE_DIR, tempfile.TemporaryDirectory()
        L.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        L.CACHE_DIR = self._old
        self._tmp.cleanup()

    def go(self, plan, ring=True):
        L.CACHE_DIR = Path(tempfile.mkdtemp(dir=self._tmp.name))          # replies are cached by prompt: never share a cache between two scripted policies
        b = L.TokenBudget()
        inv = LLMInvestigator(ScriptedChat(plan), b)
        rec = Agent(ToolGateway(FakeSession(ring=ring)), Trigger("SYN-TE", TXN, "risk_score"), responder=PendingResponder(), investigator=inv, budget=b).run()
        return rec, inv, b

    def test_per_turn_components_and_tokens_are_recorded(self):
        rec, inv, b = self.go(thorough)
        turns = inv.log["turns"]
        self.assertEqual([t["turn"] for t in turns], [0, 1, "decision"])
        for t in turns:
            self.assertEqual(set(t["chars"]), {"system", "tools", "task", "history", "tool_results"})
            self.assertGreater(t["input_tokens"], 0)
            self.assertGreater(t["output_tokens"], 0)
            self.assertTrue(t["split_is_estimate"])
            self.assertLessEqual(abs(sum(t["estimated_input_split"].values()) - t["input_tokens"]), 3)
        self.assertEqual(sum(t["input_tokens"] + t["output_tokens"] for t in turns), b.used)
        self.assertEqual(turns[0]["chars"]["history"], 0)                                    # turn 0 has only the fixed prompt and the task
        self.assertEqual(turns[0]["chars"]["tool_results"], 0)
        self.assertGreater(turns[1]["chars"]["history"], 0)
        self.assertGreater(turns[1]["chars"]["tool_results"], 0)
        self.assertEqual(inv.log["views_truncated"], 0)

    def test_components_of_a_prompt(self):
        msgs = [{"role": "user", "content": "task"}, {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "n", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "12345"}]}]
        c = turn_components("sys", [{"x": 1}], msgs)
        self.assertEqual((c["system"], c["task"], c["tool_results"]), (3, 4, len(json.dumps("12345"))))
        self.assertGreater(c["history"], 0)

    def test_independent_calls_in_one_turn_need_fewer_turns_than_one_call_per_turn(self):
        rec, inv, b = self.go(thorough)
        parallel_turns = [t for t in inv.log["turns"] if t["turn"] != "decision"]

        def sequential(step, m):
            calls = [use(0, "get_transaction_context", txn_id=TXN), use(1, "detect_fraud_patterns", txn_id=TXN), use(2, "get_card_history", card_id=CARD, hours=720, max_rows=300),
                     use(3, "find_shared_devices", card_id=CARD), use(4, "find_prior_cases", card_id=CARD), use(5, "find_similar_cases", txn_id=TXN)]
            return [calls[step]] if step < len(calls) else [use(9, "finish_investigation", sufficient=True, rationale="done")]
        rec2, inv2, b2 = self.go(sequential)
        seq_turns = [t for t in inv2.log["turns"] if t["turn"] != "decision"]
        self.assertEqual(len(parallel_turns), 2)
        self.assertEqual(len(seq_turns), 7)
        self.assertLess(b.used, b2.used)                                                    # the fixed prompt is resent every turn: fewer turns, fewer tokens
        self.assertEqual(rec["case"]["exposure_usd"], rec2["case"]["exposure_usd"])           # same authoritative case either way

    def test_authority_is_unchanged_by_the_optimisation(self):
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-TE", TXN, "risk_score"), responder=PendingResponder()).run()
        for plan in (thorough, minimal):
            rec, inv, b = self.go(plan)
            self.assertEqual(rec["case"]["affected_txn_ids"], base["case"]["affected_txn_ids"])
            self.assertEqual(rec["case"]["exposure_usd"], base["case"]["exposure_usd"])
            self.assertEqual([(a["action"], a["route"]) for a in rec["next_best_actions"]["final"]], [(a["action"], a["route"]) for a in base["next_best_actions"]["final"]])


class BudgetNeverExceededTests(unittest.TestCase):
    """Regression: a real free-model case used 16,726 of a 16,000-token budget because the provider counted more input tokens than the len/3.5 estimate."""

    class UndercountedClient:
        model = "under-counted-test-model"

        def __init__(self, ratio):
            self.ratio, self.caps, self.sent = ratio, [], 0

        def chat(self, system, messages, tools, max_tokens, tool_choice=None):
            blob = json.dumps({"s": system, "m": messages, "t": tools}, ensure_ascii=False, sort_keys=True)
            self.caps.append(max_tokens)
            self.sent += 1
            return {"input_tokens": int(L.TokenBudget.estimate(blob) * self.ratio), "output_tokens": max_tokens, "content": [], "stop_reason": "end_turn"}

    def run_until_stop(self, ratio, total=L.MAX_CASE_TOKENS):
        client, budget = self.UndercountedClient(ratio), L.TokenBudget(max_total=total)
        stopped = False
        with tempfile.TemporaryDirectory() as d:
            old, L.CACHE_DIR = L.CACHE_DIR, Path(d)
            try:
                for i in range(40):
                    try:
                        L._chat(client, budget, "s", [{"role": "user", "content": "x" * (9000 + i)}], [], cache=False)
                    except L.BudgetExceeded:
                        stopped = True
                        break
            finally:
                L.CACHE_DIR = old
        return client, budget, stopped

    def test_provider_counting_more_tokens_than_the_estimate_never_exceeds_the_budget(self):
        for ratio in (1.0, 1.3, 1.6, 2.0):
            client, budget, stopped = self.run_until_stop(ratio)
            self.assertTrue(stopped, ratio)
            self.assertLessEqual(budget.used, budget.max_total, f"ratio {ratio}: used {budget.used}")

    def test_the_reply_cap_sent_never_exceeds_what_is_left(self):
        client, budget, _ = self.run_until_stop(1.3)
        self.assertTrue(all(c <= L.MAX_CALL_OUTPUT for c in client.caps))
        self.assertEqual(budget.calls, client.sent)

    def test_a_call_that_does_not_fit_is_refused_before_it_is_sent(self):
        budget = L.TokenBudget(max_total=1000)
        budget.charge(300, 400)
        client = self.UndercountedClient(1.0)
        with self.assertRaises(L.BudgetExceeded):
            L._chat(client, budget, "s", [{"role": "user", "content": "y" * 900}], [], cache=False)
        self.assertEqual(client.sent, 0)
        self.assertEqual(budget.used, 700)

    def test_the_configured_budget_is_unchanged(self):
        self.assertEqual(L.MAX_CASE_TOKENS, 16000)
        self.assertEqual(L.TokenBudget().max_total, 16000)


if __name__ == "__main__":
    unittest.main()
