"""Offline tests for the controlled parallel_tool_calls experiment (OpenRouter provider only; clean sequential fallback; one retry at most)."""
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
from agent.investigator import LLMInvestigator  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.providers import OpenRouterHTTP, make_client  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.run_agentic_eval import parallel_status  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from test_agentic import CARD, TXN, core  # noqa: E402
from test_providers import KEY, TOOLS, resp, tc  # noqa: E402
from test_simulator_exposure import FakeSession  # noqa: E402


class Seq:
    """Transport double with a per-call script: callable(body_dict, call_index) -> (status, text)."""
    def __init__(self, fn):
        self.fn, self.bodies = fn, []

    def __call__(self, url, headers, body, timeout):
        b = json.loads(body)
        self.bodies.append(b)
        return self.fn(b, len(self.bodies) - 1)


def cl(fn, **kw):
    t = Seq(fn)
    return OpenRouterHTTP(api_key=KEY, transport=t, sleep=lambda s: None, **kw), t


def chat(c):
    return c.chat("S", [{"role": "user", "content": "t"}], TOOLS, 50)


class RequestTests(unittest.TestCase):
    def test_flag_is_sent_on_tool_use_turns_by_default(self):
        c, t = cl(lambda b, i: resp(tool_calls=[tc("get_thing", {"thing_id": "1"})]))
        chat(c)
        self.assertIs(t.bodies[0]["parallel_tool_calls"], True)
        self.assertTrue(c.parallel_requested)

    def test_flag_is_not_sent_on_plain_wording_calls(self):
        c, t = cl(lambda b, i: resp("text"))
        c.complete("s", "u", 10)
        self.assertNotIn("parallel_tool_calls", t.bodies[0])
        self.assertNotIn("tools", t.bodies[0])

    def test_switch_off_by_argument_and_by_environment(self):
        c, t = cl(lambda b, i: resp(tool_calls=[tc("get_thing", {"thing_id": "1"})]), parallel_tool_calls=False)
        chat(c)
        self.assertNotIn("parallel_tool_calls", t.bodies[0])
        self.assertFalse(c.model.endswith("|parallel"))
        for off in ("0", "false", "off"):
            client = make_client({"OPENROUTER_API_KEY": KEY, "HHG_LLM_PARALLEL_TOOL_CALLS": off}, Path("/nonexistent/.env"), transport=lambda *a: resp("x"))
            self.assertFalse(client.parallel_requested, off)
        on = make_client({"OPENROUTER_API_KEY": KEY}, Path("/nonexistent/.env"))
        self.assertTrue(on.parallel_requested)

    def test_setting_is_part_of_the_cache_key_model_string(self):
        self.assertNotEqual(OpenRouterHTTP(api_key="k", parallel_tool_calls=True).model, OpenRouterHTTP(api_key="k", parallel_tool_calls=False).model)

    def test_anthropic_requests_are_unaffected(self):
        from agent.llm import AnthropicHTTP
        seen = {}

        def tr(url, headers, body, timeout):
            seen["body"] = json.loads(body)
            return 200, json.dumps({"content": [{"type": "text", "text": "x"}], "usage": {"input_tokens": 1, "output_tokens": 1}, "model": "m"})
        AnthropicHTTP(api_key="k", model="claude-haiku-4-5-20251001", transport=tr).chat("S", [{"role": "user", "content": "t"}], TOOLS, 10)
        self.assertNotIn("parallel_tool_calls", seen["body"])

    def test_permission_set_and_safety_fields_are_untouched(self):
        c, t = cl(lambda b, i: resp(tool_calls=[tc("get_thing", {"thing_id": "1"})]))
        chat(c)
        b = t.bodies[0]
        self.assertEqual({x["function"]["name"] for x in b["tools"]}, {x["name"] for x in TOOLS})
        self.assertEqual(b["provider"], {"require_parameters": True})
        self.assertEqual((b["temperature"], b["model"]), (0, "nvidia/nemotron-3-super-120b-a12b:free"))
        self.assertNotIn("models", b)


class HonouredOrIgnoredTests(unittest.TestCase):
    def test_multiple_calls_in_one_turn_are_recorded_as_honoured(self):
        c, t = cl(lambda b, i: resp(tool_calls=[tc("get_thing", {"thing_id": "1"}, 0), tc("get_thing", {"thing_id": "2"}, 1), tc("finish_investigation", {"rationale": "x"}, 2)]))
        r = chat(c)
        self.assertEqual(sum(1 for b in r["content"] if b["type"] == "tool_use"), 3)
        ev = c.pop_parallel_events()
        self.assertEqual(ev, [{"event": "turn", "requested": True, "sent": True, "n_tool_calls": 3}])
        self.assertEqual(parallel_status(True, ev), "honored")

    def test_accepted_but_ignored_when_every_turn_has_one_call(self):
        c, t = cl(lambda b, i: resp(tool_calls=[tc("get_thing", {"thing_id": "1"})]))
        chat(c)
        chat(c)
        ev = c.pop_parallel_events()
        self.assertEqual([e["n_tool_calls"] for e in ev], [1, 1])
        self.assertEqual(parallel_status(True, ev), "accepted_not_used")
        self.assertEqual(len(t.bodies), 2)                                                # ignoring is not an error: no retry, no extra call

    def test_status_labels(self):
        self.assertEqual(parallel_status(False, []), "not_requested")
        self.assertEqual(parallel_status(True, []), "no_model_turn_completed")
        self.assertEqual(parallel_status(True, [{"event": "rejected", "http_status": 400}, {"event": "turn", "n_tool_calls": 3}]), "rejected_then_sequential")


class RejectionFallbackTests(unittest.TestCase):
    def rejecting(self, status):
        def fn(b, i):
            if "parallel_tool_calls" in b:
                return status, json.dumps({"error": {"message": f"unsupported parameter parallel_tool_calls {KEY} SECRET-BODY"}})
            return resp(tool_calls=[tc("get_thing", {"thing_id": "1"})])
        return fn

    def test_rejection_falls_back_to_sequential_with_exactly_one_retry(self):
        for status in (400, 404, 422):
            c, t = cl(self.rejecting(status))
            r = chat(c)
            self.assertEqual(r["content"][0]["name"], "get_thing")                          # the turn still succeeded
            self.assertEqual(len(t.bodies), 2)                                              # original + one retry, nothing more
            self.assertIn("parallel_tool_calls", t.bodies[0])
            self.assertNotIn("parallel_tool_calls", t.bodies[1])
            self.assertEqual(t.bodies[0]["messages"], t.bodies[1]["messages"])
            ev = c.pop_parallel_events()
            self.assertEqual(ev[0], {"event": "rejected", "http_status": status})
            self.assertEqual(ev[1], {"event": "turn", "requested": True, "sent": False, "n_tool_calls": 1})
            self.assertEqual(parallel_status(True, ev), "rejected_then_sequential")
            self.assertEqual(c.pop_retry_events(), [{"first_status": status, "retry_attempted": True, "retry_succeeded": True}])

    def test_later_turns_stay_sequential_without_more_rejections(self):
        c, t = cl(self.rejecting(400))
        chat(c)
        chat(c)
        chat(c)
        sent_flags = ["parallel_tool_calls" in b for b in t.bodies]
        self.assertEqual(sent_flags, [True, False, False, False])                          # one rejection, then never sent again
        self.assertEqual(len(t.bodies), 4)

    def test_rejection_retry_that_also_fails_stops_there(self):
        def fn(b, i):
            return 400, json.dumps({"error": {"message": "nope"}}) if "parallel_tool_calls" in b else (500, "{}")
        c, t = cl(lambda b, i: (400, "{}") if "parallel_tool_calls" in b else (500, "{}"))
        with self.assertRaises(L.LLMUnavailable) as cm:
            chat(c)
        self.assertEqual(len(t.bodies), 2)                                                  # no third call: the single retry was used by the fallback
        self.assertIn("provider_http_error", str(cm.exception))
        self.assertIn("http=500", str(cm.exception))

    def test_unrelated_400_without_the_flag_is_not_retried(self):
        c, t = cl(lambda b, i: (400, "{}"), parallel_tool_calls=False)
        with self.assertRaises(L.LLMUnavailable):
            chat(c)
        self.assertEqual(len(t.bodies), 1)

    def test_rate_limit_still_uses_the_existing_retry_only(self):
        sleeps = []
        t = Seq(lambda b, i: (429, "{}") if i == 0 else resp(tool_calls=[tc("get_thing", {"thing_id": "1"})]))
        c = OpenRouterHTTP(api_key=KEY, transport=t, sleep=sleeps.append)
        chat(c)
        self.assertEqual((len(t.bodies), sleeps), (2, [3]))
        self.assertIn("parallel_tool_calls", t.bodies[1])                                    # a rate limit says nothing about the parameter

    def test_no_secret_or_error_body_leaks_through_the_rejection_path(self):
        c, t = cl(self.rejecting(400))
        chat(c)
        blob = json.dumps(c.pop_parallel_events()) + json.dumps(c.pop_retry_events()) + json.dumps(c.pop_failures()) + repr(c)
        for secret in (KEY, "SECRET-BODY", "unsupported parameter"):
            self.assertNotIn(secret, blob)


class ThroughTheStackTests(unittest.TestCase):
    def setUp(self):
        self._old, self._tmp = L.CACHE_DIR, tempfile.TemporaryDirectory()
        L.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        L.CACHE_DIR = self._old
        self._tmp.cleanup()

    def run_case(self, fn, **kw):
        L.CACHE_DIR = Path(tempfile.mkdtemp(dir=self._tmp.name))
        c, t = cl(fn, **kw)
        b = L.TokenBudget()
        inv = LLMInvestigator(c, b)
        rec = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-PAR", TXN, "risk_score"), responder=PendingResponder(), investigator=inv, budget=b).run()
        return rec, inv, c, t, b

    @staticmethod
    def parallel_model(b, i):
        names = {x["function"]["name"] for x in b["tools"]}
        if "request_evidence" in names:
            return resp(tool_calls=[tc("no_further_evidence", {"reason": "conclusive"})], usage=(400, 30))
        if not any(m["role"] == "tool" for m in b["messages"]):
            return resp(tool_calls=[tc("get_transaction_context", {"txn_id": TXN}, 0), tc("detect_fraud_patterns", {"txn_id": TXN}, 1),
                                    tc("get_card_history", {"card_id": CARD, "hours": 720, "max_rows": 300}, 2), tc("find_shared_devices", {"card_id": CARD}, 3),
                                    tc("find_prior_cases", {"card_id": CARD}, 4)], usage=(1000, 150))
        return resp(tool_calls=[tc("finish_investigation", {"sufficient": True, "rationale": "done"})], usage=(1500, 30))

    def test_parallel_turn_executes_all_calls_in_order_within_one_turn(self):
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-PAR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, inv, c, t, b = self.run_case(self.parallel_model)
        steps = inv.log["steps"]
        self.assertEqual([s["tool"] for s in steps], ["get_transaction_context", "detect_fraud_patterns", "get_card_history", "find_shared_devices", "find_prior_cases"])
        self.assertEqual({s["step"] for s in steps}, {0})                                    # all five in ONE turn
        self.assertEqual(rec["agentic"]["mode"], "llm_orchestrated")
        self.assertEqual(rec["agentic"]["forced_calls"], [])
        self.assertEqual(core(rec), core(base))                                               # authority unchanged
        self.assertEqual(parallel_status(True, c.pop_parallel_events()), "honored")
        self.assertEqual(b.used, 1000 + 150 + 1500 + 30 + 400 + 30)                           # 2 investigation turns + decision instead of 6+

    def test_sequential_model_behaviour_is_unchanged_and_ignoring_is_harmless(self):
        def sequential(b, i):
            names = {x["function"]["name"] for x in b["tools"]}
            if "request_evidence" in names:
                return resp(tool_calls=[tc("no_further_evidence", {"reason": "x"})])
            order = [("get_transaction_context", {"txn_id": TXN}), ("detect_fraud_patterns", {"txn_id": TXN}), ("get_card_history", {"card_id": CARD, "hours": 720, "max_rows": 300}),
                     ("find_shared_devices", {"card_id": CARD}), ("find_prior_cases", {"card_id": CARD})]
            n = sum(1 for m in b["messages"] if m["role"] == "tool")
            return resp(tool_calls=[tc(*order[n])]) if n < len(order) else resp(tool_calls=[tc("finish_investigation", {"sufficient": True, "rationale": "done"})])
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-PAR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, inv, c, t, b = self.run_case(sequential)
        self.assertEqual(core(rec), core(base))
        self.assertEqual(parallel_status(True, c.pop_parallel_events()), "accepted_not_used")
        self.assertEqual(len({s["step"] for s in inv.log["steps"]}), 5)

    def test_rejected_parameter_gives_the_same_case_through_the_full_stack(self):
        def fn(b, i):
            if "parallel_tool_calls" in b:
                return 400, json.dumps({"error": {"message": "parameter not supported"}})
            return self.parallel_model(b, i)
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-PAR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, inv, c, t, b = self.run_case(fn)
        self.assertEqual(rec["agentic"]["mode"], "llm_orchestrated")
        self.assertIsNone(rec["agentic"]["fallback"])
        self.assertEqual(core(rec), core(base))
        self.assertEqual(sum(1 for x in t.bodies if "parallel_tool_calls" in x), 1)          # the rejected attempt only; never again

    def test_fallback_behaviour_is_unchanged_when_the_provider_fails(self):
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-PAR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, inv, c, t, b = self.run_case(lambda b_, i: (200, json.dumps({"error": {"message": f"x {KEY}"}})))
        self.assertEqual(rec["agentic"]["mode"], "deterministic_fallback")
        self.assertIn("[provider_http_error http=200 retry=attempted_failed]", rec["agentic"]["fallback"])
        self.assertEqual(len(t.bodies), 2)
        self.assertEqual(core(rec), core(base))
        self.assertNotIn(KEY, json.dumps(rec["agentic"]["fallback"]))

    def test_nothing_new_or_forbidden_is_sent(self):
        rec, inv, c, t, b = self.run_case(self.parallel_model)
        for body in t.bodies:
            blob = json.dumps(body)
            L.guard_payload(blob)
            self.assertEqual(set(body) - {"parallel_tool_calls"}, {"model", "messages", "temperature", "max_tokens", "provider", "tools", "tool_choice"} & set(body) | ({"tools"} & set(body)))
            for banned in ("as_of", "risk", "HHG-", KEY, "Bearer", "snap_"):
                self.assertNotIn(banned, blob, banned)


if __name__ == "__main__":
    unittest.main()
