"""Offline tests for the provider boundary (OpenRouter free models + Anthropic). No network, no key. Existing agentic/LLM tests are untouched and still cover the layers above the boundary."""
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
from agent.investigator import LLMInvestigator, tool_schemas  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.providers import DEFAULT_OPENROUTER_MODEL, OpenRouterHTTP, env_get, is_free_model, make_client  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from test_agentic import TXN, core  # noqa: E402
from test_simulator_exposure import FakeSession  # noqa: E402

KEY = "sk-or" + "-v1-TESTSECRET0123456789"          # fake; built at runtime so no secret scanner mistakes the source for a real key


def resp(content="", tool_calls=None, usage=(120, 30), model="nvidia/nemotron-3-super-120b-a12b:free", status=200):
    msg = {"role": "assistant", "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    body = {"id": "gen-1", "model": model, "choices": [{"message": msg, "finish_reason": "tool_calls" if tool_calls else "stop"}]}
    if usage:
        body["usage"] = {"prompt_tokens": usage[0], "completion_tokens": usage[1]}
    return status, json.dumps(body)


def tc(name, args, i=0):
    return {"id": f"call_{i}", "type": "function", "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}


class Recorder:
    """Transport double: returns queued responses, records what was sent (bodies stay in memory of the test only)."""
    def __init__(self, *responses):
        self.queue, self.sent = list(responses), []

    def __call__(self, url, headers, body, timeout):
        self.sent.append((url, headers, json.loads(body)))
        return self.queue.pop(0) if len(self.queue) > 1 else self.queue[0]


def client(*responses, model=None, **kw):
    r = Recorder(*responses)
    return OpenRouterHTTP(model=model, api_key=KEY, transport=r, sleep=lambda s: None, **kw), r


TOOLS = [{"name": "get_thing", "description": "d", "input_schema": {"type": "object", "properties": {"thing_id": {"type": "string"}}, "required": ["thing_id"], "additionalProperties": False}},
         {"name": "finish_investigation", "description": "f", "input_schema": {"type": "object", "properties": {"rationale": {"type": "string"}}, "required": ["rationale"]}}]


class FreeOnlyTests(unittest.TestCase):
    def test_only_free_model_ids_are_accepted(self):
        for ok in ("nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"):
            self.assertTrue(is_free_model(ok))
            OpenRouterHTTP(model=ok, api_key="k")
        for bad in ("openai/gpt-5", "anthropic/claude-sonnet-5", "openrouter/auto", "openrouter/free", "nvidia/x:free:extra", "x:free", "nvidia/nemotron:paid"):
            if bad is None:
                continue
            self.assertFalse(is_free_model(bad), bad)
            with self.assertRaises(L.LLMUnavailable):
                OpenRouterHTTP(model=bad, api_key="k")

    def test_default_model_is_the_verified_free_one(self):
        self.assertEqual(DEFAULT_OPENROUTER_MODEL, "nvidia/nemotron-3-super-120b-a12b:free")
        self.assertTrue(is_free_model(DEFAULT_OPENROUTER_MODEL))
        c, _ = client(resp("x"))
        self.assertEqual(c._requested, DEFAULT_OPENROUTER_MODEL)

    def test_no_fallback_model_list_or_paid_routing_is_ever_sent(self):
        c, rec = client(resp("hi"))
        c.complete("sys", "usr", 50)
        body = rec.sent[0][2]
        self.assertNotIn("models", body)
        self.assertNotIn("route", body)
        self.assertEqual(body["model"], DEFAULT_OPENROUTER_MODEL)
        self.assertEqual(body["provider"], {"require_parameters": True})
        self.assertEqual(body["temperature"], 0)


class TranslationTests(unittest.TestCase):
    def test_outgoing_messages_tools_and_choice(self):
        c, rec = client(resp(tool_calls=[tc("get_thing", {"thing_id": "1234567"})]))
        messages = [{"role": "user", "content": "task"},
                    {"role": "assistant", "content": [{"type": "text", "text": "thinking"}, {"type": "tool_use", "id": "u1", "name": "get_thing", "input": {"thing_id": "1234567"}}]},
                    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "u1", "content": "{\"v\":1}"}]}]
        c.chat("SYSTEM", messages, TOOLS, 100, {"type": "any"})
        body = rec.sent[0][2]
        m = body["messages"]
        self.assertEqual(m[0], {"role": "system", "content": "SYSTEM"})
        self.assertEqual(m[1], {"role": "user", "content": "task"})
        self.assertEqual(m[2]["role"], "assistant")
        self.assertEqual(m[2]["tool_calls"][0], {"id": "u1", "type": "function", "function": {"name": "get_thing", "arguments": "{\"thing_id\": \"1234567\"}"}})
        self.assertEqual(m[3], {"role": "tool", "tool_call_id": "u1", "content": "{\"v\":1}"})
        self.assertEqual(body["tools"][0], {"type": "function", "function": {"name": "get_thing", "description": "d", "parameters": TOOLS[0]["input_schema"]}})
        self.assertEqual(body["tool_choice"], "required")

    def test_no_tool_choice_unless_requested(self):
        c, rec = client(resp(tool_calls=[tc("get_thing", {"thing_id": "1"})]))
        c.chat("S", [{"role": "user", "content": "t"}], TOOLS, 50)
        self.assertNotIn("tool_choice", rec.sent[0][2])

    def test_incoming_tool_calls_become_tool_use_blocks(self):
        c, _ = client(resp("plan", [tc("get_thing", {"thing_id": "1234567"}, 0), tc("finish_investigation", {"rationale": "ok"}, 1)], usage=(200, 44)))
        r = c.chat("S", [{"role": "user", "content": "t"}], TOOLS, 50)
        kinds = [b["type"] for b in r["content"]]
        self.assertEqual(kinds, ["text", "tool_use", "tool_use"])
        self.assertEqual(r["content"][1], {"type": "tool_use", "id": "call_0", "name": "get_thing", "input": {"thing_id": "1234567"}})
        self.assertEqual((r["input_tokens"], r["output_tokens"], r["stop_reason"]), (200, 44, "tool_use"))
        self.assertFalse(r["usage_estimated"])

    def test_text_only_reply_has_no_tool_use(self):
        c, _ = client(resp("I am done."))
        r = c.chat("S", [{"role": "user", "content": "t"}], TOOLS, 50)
        self.assertEqual([b["type"] for b in r["content"]], ["text"])
        self.assertEqual(r["stop_reason"], "end_turn")

    def test_malformed_arguments_become_an_empty_input(self):
        for bad in ("{not json", "[1,2]", "", None):
            c, _ = client(resp(tool_calls=[{"id": "c9", "type": "function", "function": {"name": "get_thing", "arguments": bad}}]))
            r = c.chat("S", [{"role": "user", "content": "t"}], TOOLS, 50)
            self.assertEqual(r["content"][0]["input"], {}, repr(bad))

    def test_missing_usage_is_estimated_and_flagged(self):
        c, _ = client(resp("hello world", usage=None))
        r = c.complete("sys prompt", "user prompt", 50)
        self.assertTrue(r["usage_estimated"])
        self.assertGreater(r["input_tokens"], 0)
        self.assertGreater(r["output_tokens"], 0)

    def test_returned_model_is_recorded_for_every_call(self):
        c, _ = client(resp("a", model="nvidia/nemotron-3-super-120b-a12b:free"), resp("b", model="some/other-model:free"))
        c.complete("s", "u", 10)
        c.complete("s", "u", 10)
        self.assertEqual(c.models_seen, ["nvidia/nemotron-3-super-120b-a12b:free", "some/other-model:free"])
        self.assertEqual(c.pop_models(), ["nvidia/nemotron-3-super-120b-a12b:free", "some/other-model:free"])
        self.assertEqual(c.models_seen, [])

    def test_required_choice_gets_one_reminder_retry_and_charges_both_calls(self):
        c, rec = client(resp("plain text answer", usage=(100, 10)), resp(tool_calls=[tc("finish_investigation", {"rationale": "x"})], usage=(130, 20)))
        r = c.chat("S", [{"role": "user", "content": "decide"}], TOOLS, 50, {"type": "any"})
        self.assertEqual(len(rec.sent), 2)
        self.assertEqual(rec.sent[1][2]["messages"][-1]["content"].split(".")[0], "You must respond by calling exactly one of the provided tools")
        self.assertEqual(r["content"][0]["name"], "finish_investigation")
        self.assertEqual((r["input_tokens"], r["output_tokens"]), (230, 30))
        self.assertEqual(len(r["models"]), 2)

    def test_required_choice_still_failing_returns_text_so_the_deterministic_layer_decides(self):
        c, rec = client(resp("no tool"), resp("still no tool"))
        r = c.chat("S", [{"role": "user", "content": "decide"}], TOOLS, 50, {"type": "any"})
        self.assertEqual(len(rec.sent), 2)                                       # never a third call
        self.assertFalse(any(b["type"] == "tool_use" for b in r["content"]))


class ErrorAndSecretTests(unittest.TestCase):
    def test_rate_limit_retries_once_then_succeeds(self):
        c, rec = client((429, "{}"), resp("ok"))
        self.assertEqual(c.complete("s", "u", 10)["text"], "ok")
        self.assertEqual(len(rec.sent), 2)

    def test_rate_limit_twice_fails_over_to_the_deterministic_fallback(self):
        c, _ = client((429, json.dumps({"error": {"message": f"upstream said key {KEY} is bad"}})))
        with self.assertRaises(L.LLMUnavailable) as cm:
            c.complete("s", "u", 10)
        self.assertIn("rate limited", str(cm.exception))
        self.assertNotIn(KEY, str(cm.exception))
        self.assertNotIn("upstream said", str(cm.exception))                     # provider error bodies are never echoed

    def test_http_200_with_an_error_body_is_a_failure(self):
        c, _ = client((200, json.dumps({"error": {"message": "provider down"}})))
        with self.assertRaises(L.LLMUnavailable):
            c.complete("s", "u", 10)

    def test_unparseable_and_network_failures(self):
        c, _ = client((200, "<html>"))
        with self.assertRaises(L.LLMUnavailable):
            c.complete("s", "u", 10)

        def boom(*a):
            raise L.LLMUnavailable("network error: URLError")
        c = OpenRouterHTTP(api_key=KEY, transport=boom, sleep=lambda s: None)
        with self.assertRaises(L.LLMUnavailable):
            c.chat("s", [{"role": "user", "content": "t"}], TOOLS, 10)

    def test_missing_key_is_unavailable(self):
        c = OpenRouterHTTP(api_key="", transport=lambda *a: resp("x"))
        with self.assertRaises(L.LLMUnavailable):
            c.complete("s", "u", 10)

    def test_key_only_in_the_authorization_header_and_never_in_repr_or_body(self):
        c, rec = client(resp("x"))
        c.complete("system text", "user text", 10)
        url, headers, body = rec.sent[0]
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertNotIn(KEY, json.dumps(body))
        self.assertNotIn(KEY, repr(c))
        self.assertNotIn(KEY, str(vars(c).get("model")))
        self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")


class EnvAndFactoryTests(unittest.TestCase):
    def envfile(self, text):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = Path(d.name) / ".env"
        p.write_text(text, encoding="utf-8")
        return p

    def test_env_reader_prefers_process_env_and_reads_only_whitelisted_names(self):
        p = self.envfile("TG_SECRET=abc\nOPENROUTER_API_KEY=from-file\nHHG_LLM_MODEL=x/y:free\n")
        self.assertEqual(env_get("OPENROUTER_API_KEY", p, {}), "from-file")
        self.assertEqual(env_get("OPENROUTER_API_KEY", p, {"OPENROUTER_API_KEY": "from-env"}), "from-env")
        with self.assertRaises(ValueError):
            env_get("TG_SECRET", p, {})
        with self.assertRaises(ValueError):
            env_get("PATH", p, {})
        self.assertEqual(env_get("OPENROUTER_BASE_URL", p, {}), "")

    def test_env_reader_handles_quotes_blank_lines_and_missing_file(self):
        p = self.envfile('\nOPENROUTER_API_KEY="quoted-key"\n\n')
        self.assertEqual(env_get("OPENROUTER_API_KEY", p, {}), "quoted-key")
        self.assertEqual(env_get("OPENROUTER_API_KEY", Path("/nonexistent/.env"), {}), "")

    def test_factory_selection(self):
        p = self.envfile("")
        self.assertIsNone(make_client({}, p))
        c = make_client({"OPENROUTER_API_KEY": KEY}, p)
        self.assertEqual((c.provider, c._requested), ("openrouter", DEFAULT_OPENROUTER_MODEL))
        a = make_client({"ANTHROPIC_API_KEY": "sk-ant-x"}, p)
        self.assertEqual(type(a).__name__, "AnthropicHTTP")
        both = make_client({"OPENROUTER_API_KEY": KEY, "ANTHROPIC_API_KEY": "sk-ant-x"}, p)
        self.assertEqual(both.provider, "openrouter")                            # default preference
        forced = make_client({"OPENROUTER_API_KEY": KEY, "ANTHROPIC_API_KEY": "sk-ant-x", "HHG_LLM_PROVIDER": "anthropic"}, p)
        self.assertEqual(type(forced).__name__, "AnthropicHTTP")
        self.assertIsNone(make_client({"HHG_LLM_PROVIDER": "openrouter"}, p))     # provider chosen but no key -> deterministic fallback
        with self.assertRaises(L.LLMUnavailable):
            make_client({"HHG_LLM_PROVIDER": "mystery", "OPENROUTER_API_KEY": KEY}, p)

    def test_factory_refuses_a_paid_openrouter_model(self):
        with self.assertRaises(L.LLMUnavailable):
            make_client({"OPENROUTER_API_KEY": KEY, "HHG_LLM_MODEL": "openai/gpt-5"}, self.envfile(""))
        c = make_client({"OPENROUTER_API_KEY": KEY, "HHG_LLM_MODEL": "google/gemma-4-31b-it:free"}, self.envfile(""))
        self.assertEqual(c._requested, "google/gemma-4-31b-it:free")

    def test_factory_reads_the_key_from_dot_env_without_the_environment(self):
        c = make_client({}, self.envfile(f"OPENROUTER_API_KEY={KEY}\n"))
        self.assertEqual(c.provider, "openrouter")
        self.assertTrue(c._key)

    def test_provider_is_part_of_the_cache_key_model_string(self):
        from agent.llm import AnthropicHTTP
        self.assertNotEqual(OpenRouterHTTP(api_key="k").model, AnthropicHTTP(api_key="k", model="x").model)
        self.assertTrue(OpenRouterHTTP(api_key="k").model.startswith("openrouter:"))

    def test_anthropic_client_is_unchanged_in_behaviour(self):
        from agent.llm import AnthropicHTTP
        seen = {}

        def tr(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=json.loads(body))
            return 200, json.dumps({"content": [{"type": "tool_use", "id": "a", "name": "get_thing", "input": {"thing_id": "1"}}], "usage": {"input_tokens": 9, "output_tokens": 3}, "stop_reason": "tool_use", "model": "m"})
        r = AnthropicHTTP(model="claude-haiku-4-5-20251001", api_key="k", transport=tr).chat("S", [{"role": "user", "content": "t"}], TOOLS, 50, {"type": "any"})
        self.assertEqual(seen["url"], L.API_URL)
        self.assertEqual(seen["headers"]["x-api-key"], "k")
        self.assertEqual(seen["body"]["tool_choice"], {"type": "any"})
        self.assertEqual((r["input_tokens"], r["output_tokens"]), (9, 3))


class ThroughTheRealStackTests(unittest.TestCase):
    """OpenRouter client inside the unchanged investigator/orchestrator: same authority, same guards, nothing new sent."""
    def setUp(self):
        self._old, self._tmp = L.CACHE_DIR, tempfile.TemporaryDirectory()
        L.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        L.CACHE_DIR = self._old
        self._tmp.cleanup()

    def script(self):
        state = {"n": 0}

        def transport(url, headers, body, timeout):
            b = json.loads(body)
            names = {t["function"]["name"] for t in b["tools"]}
            state["n"] += 1
            if "request_evidence" in names:
                return resp(tool_calls=[tc("no_further_evidence", {"reason": "conclusive"})], usage=(400, 30))
            users_after_tools = sum(1 for m in b["messages"] if m["role"] == "tool")
            if users_after_tools == 0:
                return resp(tool_calls=[tc("get_transaction_context", {"txn_id": TXN}, 0), tc("detect_fraud_patterns", {"txn_id": TXN}, 1),
                                        tc("get_card_history", {"card_id": "C00001-K1", "hours": 720, "max_rows": 300}, 2), tc("find_shared_devices", {"card_id": "C00001-K1"}, 3),
                                        tc("find_prior_cases", {"card_id": "C00001-K1"}, 4)], usage=(900, 120))
            return resp(tool_calls=[tc("finish_investigation", {"sufficient": True, "rationale": "signals and device links checked"})], usage=(1500, 40))
        return transport

    def run_case(self, transport, ring=True):
        c = OpenRouterHTTP(api_key=KEY, transport=transport, sleep=lambda s: None)
        b = L.TokenBudget()
        rec = Agent(ToolGateway(FakeSession(ring=ring)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder(), investigator=LLMInvestigator(c, b), budget=b).run()
        return rec, c, b

    def test_case_runs_through_the_openai_format_and_matches_the_deterministic_authority(self):
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, c, b = self.run_case(self.script())
        self.assertEqual(rec["agentic"]["mode"], "llm_orchestrated")
        self.assertEqual(core(rec), core(base))
        self.assertEqual(b.used, 900 + 120 + 1500 + 40 + 400 + 30)               # charged from the provider's reported usage
        self.assertEqual(set(c.models_seen), {"nvidia/nemotron-3-super-120b-a12b:free"})
        self.assertEqual(rec["agentic"]["forced_calls"], [])

    def test_nothing_forbidden_is_ever_sent_to_openrouter(self):
        sent = []
        inner = self.script()

        def spy(url, headers, body, timeout):
            sent.append(body.decode("utf-8") if isinstance(body, bytes) else body)
            return inner(url, headers, body, timeout)
        self.run_case(spy)
        self.assertGreaterEqual(len(sent), 3)
        for blob in sent:
            L.guard_payload(blob)
            for banned in ("0.83", "risk", "model_score", "HHG-", "snap_", "as_of", "seed", KEY, "Bearer"):
                self.assertNotIn(banned, blob, banned)

    def test_tools_sent_are_exactly_the_permission_table_plus_finish(self):
        seen = {}
        inner = self.script()

        def spy(url, headers, body, timeout):
            b = json.loads(body)
            if "get_transaction_context" in {t["function"]["name"] for t in b["tools"]}:
                seen["tools"] = {t["function"]["name"] for t in b["tools"]}
            return inner(url, headers, body, timeout)
        self.run_case(spy)
        self.assertEqual(seen["tools"], {t["name"] for t in tool_schemas()})
        self.assertEqual(len(seen["tools"]), 11)                                  # 10 investigation tools + finish_investigation

    def test_forced_completion_still_applies_when_the_model_stops_early(self):
        def lazy(url, headers, body, timeout):
            b = json.loads(body)
            if "request_evidence" in {t["function"]["name"] for t in b["tools"]}:
                return resp(tool_calls=[tc("no_further_evidence", {"reason": "x"})])
            if not any(m["role"] == "tool" for m in b["messages"]):
                return resp(tool_calls=[tc("get_transaction_context", {"txn_id": TXN})])
            return resp(tool_calls=[tc("finish_investigation", {"sufficient": True, "rationale": "enough"})])
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, c, b = self.run_case(lazy)
        self.assertIn("detect_fraud_patterns", rec["agentic"]["forced_calls"])
        self.assertEqual(core(rec), core(base))

    def test_provider_failure_falls_back_to_the_deterministic_pipeline(self):
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, c, b = self.run_case(lambda *a: (429, "{}"))
        self.assertEqual(rec["agentic"]["mode"], "deterministic_fallback")
        self.assertTrue(rec["agentic"]["fallback_used"])
        self.assertIn("rate limited", rec["agentic"]["fallback"])
        self.assertEqual(core(rec), core(base))
        self.assertEqual(b.used, 0)

    def test_text_only_model_cannot_tool_call_so_the_pipeline_completes_it(self):
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, c, b = self.run_case(lambda *a: resp("I would look at the card history."))
        self.assertEqual(core(rec), core(base))                                   # no tool calls: everything the case needs is forced by the deterministic layer
        self.assertTrue(rec["agentic"]["forced_calls"])
        self.assertIn("without finish_investigation", rec["agentic"]["finish"]["rationale"])

    def test_budget_exceeded_falls_back_without_calling_the_provider(self):
        calls = []
        c = OpenRouterHTTP(api_key=KEY, transport=lambda *a: calls.append(1) or resp("x"), sleep=lambda s: None)
        b = L.TokenBudget(300)
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder(), investigator=LLMInvestigator(c, b), budget=b).run()
        self.assertEqual(rec["agentic"]["mode"], "deterministic_fallback")
        self.assertIn("BudgetExceeded", rec["agentic"]["fallback"])
        self.assertEqual(calls, [])
        self.assertEqual(core(rec), core(base))


class FailureCategoryTests(unittest.TestCase):
    """Sanitized failure metadata: category + HTTP status + retry outcome only (never the key, request body or provider error body)."""
    def fail(self, *responses, transport=None):
        c = OpenRouterHTTP(api_key=KEY, transport=transport or Recorder(*responses), sleep=lambda s: None)
        with self.assertRaises(L.LLMUnavailable) as cm:
            c.complete("SECRET-SYSTEM", "SECRET-USER", 10)
        return c, cm.exception

    def test_rate_limit_after_a_failed_retry(self):
        c, e = self.fail((429, json.dumps({"error": {"message": f"body with {KEY} and SECRET-USER"}})))
        self.assertEqual(c.failures, [{"category": "provider_rate_limit", "http_status": 429, "retry_attempted": True, "retry_succeeded": False}])
        self.assertIn("[provider_rate_limit http=429 retry=attempted_failed]", str(e))
        self.assertEqual(e.category, "provider_rate_limit")

    def test_retry_that_succeeds_is_recorded_without_a_failure(self):
        c = OpenRouterHTTP(api_key=KEY, transport=Recorder((429, "{}"), resp("ok")), sleep=lambda s: None)
        self.assertEqual(c.complete("s", "u", 10)["text"], "ok")
        self.assertEqual(c.pop_failures(), [])
        self.assertEqual(c.pop_retry_events(), [{"first_status": 429, "retry_attempted": True, "retry_succeeded": True}])
        self.assertEqual(c.retry_events, [])

    def test_other_http_errors(self):
        c, e = self.fail((500, "{}"))
        self.assertEqual(c.failures[0], {"category": "provider_http_error", "http_status": 500, "retry_attempted": False, "retry_succeeded": None})
        self.assertIn("retry=not_attempted", str(e))
        c, e = self.fail((503, "{}"))
        self.assertEqual(c.failures[0], {"category": "provider_http_error", "http_status": 503, "retry_attempted": True, "retry_succeeded": False})
        c, e = self.fail((401, "{}"))
        self.assertEqual((c.failures[0]["category"], c.failures[0]["http_status"], c.failures[0]["retry_attempted"]), ("provider_http_error", 401, False))

    def test_error_object_inside_a_200(self):
        c, e = self.fail((200, json.dumps({"error": {"message": "provider down SECRET-USER"}})))
        self.assertEqual(c.failures[0], {"category": "provider_http_error", "http_status": 200, "retry_attempted": True, "retry_succeeded": False})

    def test_no_choices_is_a_provider_http_error_with_one_retry(self):
        for body in ({"choices": []}, {"id": "x"}, {}):
            c, e = self.fail((200, json.dumps(body)))
            self.assertEqual(c.failures[0], {"category": "provider_http_error", "http_status": 200, "retry_attempted": True, "retry_succeeded": False}, body)

    def test_malformed_response(self):
        c, e = self.fail((200, "<html>bad gateway</html>"))
        self.assertEqual(c.failures[0], {"category": "provider_malformed_response", "http_status": 200, "retry_attempted": False, "retry_succeeded": None})

    def test_transport_timeout_and_connection_errors(self):
        def timeout(*a):
            raise __import__("agent.providers", fromlist=["ProviderFailure"]).ProviderFailure("provider_timeout", None, "request timed out")
        c, e = self.fail(transport=timeout)
        self.assertEqual(c.failures[0], {"category": "provider_timeout", "http_status": None, "retry_attempted": False, "retry_succeeded": None})

        def plain(*a):
            raise L.LLMUnavailable("network error: URLError")
        c, e = self.fail(transport=plain)
        self.assertEqual(c.failures[0]["category"], "provider_http_error")

    def test_real_urllib_transport_classifies_timeouts_and_resets(self):
        import urllib.error
        import urllib.request
        real = urllib.request.urlopen
        try:
            for exc, cat in ((TimeoutError("t"), "provider_timeout"), (urllib.error.URLError(TimeoutError("t")), "provider_timeout"), (ConnectionResetError("r"), "provider_http_error"),
                             (urllib.error.URLError(OSError("dns")), "provider_http_error")):
                def boom(*a, **k):
                    raise exc
                urllib.request.urlopen = boom
                with self.assertRaises(L.LLMUnavailable) as cm:
                    OpenRouterHTTP._urllib("https://x", {}, b"{}", 1)
                self.assertEqual(cm.exception.category, cat, repr(exc))
                self.assertNotIn("SECRET", str(cm.exception))
        finally:
            urllib.request.urlopen = real

    def test_no_secret_ever_reaches_messages_or_metadata(self):
        for resp_ in ((429, json.dumps({"error": {"message": f"{KEY} SECRET-USER SECRET-SYSTEM"}})), (500, f"{KEY} SECRET-USER"), (200, f"{KEY} not json")):
            c, e = self.fail(resp_)
            blob = str(e) + json.dumps(c.failures)
            for secret in (KEY, "SECRET-USER", "SECRET-SYSTEM", "Bearer"):
                self.assertNotIn(secret, blob)

    def test_failure_flows_into_the_existing_fallback_string_without_changing_behaviour(self):
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        c = OpenRouterHTTP(api_key=KEY, transport=lambda *a: (429, "{}"), sleep=lambda s: None)
        b = L.TokenBudget()
        rec = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder(), investigator=LLMInvestigator(c, b), budget=b).run()
        self.assertEqual(rec["agentic"]["mode"], "deterministic_fallback")
        self.assertIn("[provider_rate_limit http=429 retry=attempted_failed]", rec["agentic"]["fallback"])
        self.assertEqual(core(rec), core(base))
        self.assertEqual(c.pop_failures()[0]["http_status"], 429)


class Http200ErrorRetryTests(unittest.TestCase):
    """HTTP 200 carrying an error object / no choices: classified provider_http_error, retried exactly once, then either continues or falls back exactly as before."""
    def counting(self, *responses):
        calls, sleeps = [], []

        def transport(url, headers, body, timeout):
            calls.append(json.loads(body))
            return responses[min(len(calls) - 1, len(responses) - 1)]
        return transport, calls, sleeps

    def make(self, transport, sleeps):
        return OpenRouterHTTP(api_key=KEY, transport=transport, sleep=sleeps.append)

    def test_1_valid_200_is_not_retried(self):
        t, calls, sleeps = self.counting(resp("fine"))
        c = self.make(t, sleeps)
        self.assertEqual(c.complete("s", "u", 10)["text"], "fine")
        self.assertEqual((len(calls), sleeps, c.retry_events, c.failures), (1, [], [], []))

    def test_2_error_object_or_no_choices_is_retried_exactly_once(self):
        for bad in ((200, json.dumps({"error": {"message": f"upstream {KEY} SECRET-BODY"}})), (200, json.dumps({"choices": []})), (200, json.dumps({"id": "x"}))):
            t, calls, sleeps = self.counting(bad)
            c = self.make(t, sleeps)
            with self.assertRaises(L.LLMUnavailable):
                c.complete("s", "u", 10)
            self.assertEqual(len(calls), 2, bad)                                   # never a third call
            self.assertEqual(sleeps, [3])                                          # the existing backoff, once
            self.assertEqual(c.failures, [{"category": "provider_http_error", "http_status": 200, "retry_attempted": True, "retry_succeeded": False}])

    def test_unparseable_200_is_not_retried_and_stays_malformed(self):
        t, calls, sleeps = self.counting((200, "<html>"))
        c = self.make(t, sleeps)
        with self.assertRaises(L.LLMUnavailable):
            c.complete("s", "u", 10)
        self.assertEqual(len(calls), 1)
        self.assertEqual(c.failures[0]["category"], "provider_malformed_response")

    def test_3_retry_succeeds_and_is_recorded(self):
        t, calls, sleeps = self.counting((200, json.dumps({"error": {"message": "x"}})), resp("second time lucky"))
        c = self.make(t, sleeps)
        r = c.complete("s", "u", 10)
        self.assertEqual((r["text"], len(calls), sleeps), ("second time lucky", 2, [3]))
        self.assertEqual(c.retry_events, [{"first_status": 200, "retry_attempted": True, "retry_succeeded": True}])
        self.assertEqual(c.failures, [])
        self.assertEqual(calls[0], calls[1])                                       # the identical request is repeated

    def stack(self, transport, sleeps):
        c = OpenRouterHTTP(api_key=KEY, transport=transport, sleep=sleeps.append)
        b = L.TokenBudget()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = L.CACHE_DIR
        L.CACHE_DIR = Path(tmp.name)
        self.addCleanup(setattr, L, "CACHE_DIR", old)
        rec = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder(), investigator=LLMInvestigator(c, b), budget=b).run()
        return rec, c, b

    def test_3b_after_a_successful_retry_the_same_investigator_loop_continues(self):
        state = {"n": 0}
        sleeps = []

        def transport(url, headers, body, timeout):
            b = json.loads(body)
            state["n"] += 1
            names = {t["function"]["name"] for t in b["tools"]}
            if state["n"] == 1:
                return 200, json.dumps({"error": {"message": "upstream hiccup"}})            # first attempt of the FIRST turn fails, retry succeeds
            if "request_evidence" in names:
                return resp(tool_calls=[tc("no_further_evidence", {"reason": "conclusive"})])
            if not any(m["role"] == "tool" for m in b["messages"]):
                return resp(tool_calls=[tc("get_transaction_context", {"txn_id": TXN}, 0), tc("detect_fraud_patterns", {"txn_id": TXN}, 1)])
            return resp(tool_calls=[tc("finish_investigation", {"sufficient": True, "rationale": "done"})])
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, c, b = self.stack(transport, sleeps)
        self.assertEqual(rec["agentic"]["mode"], "llm_orchestrated")                        # no fallback: the loop went on with the retried reply
        self.assertIsNone(rec["agentic"]["fallback"])
        self.assertEqual(sleeps, [3])
        self.assertEqual(c.pop_retry_events(), [{"first_status": 200, "retry_attempted": True, "retry_succeeded": True}])
        self.assertEqual([x["tool"] for x in rec["agentic"]["tool_order"] if x["source"] == "llm"], ["get_transaction_context", "detect_fraud_patterns"])
        self.assertEqual(core(rec), core(base))

    def test_4_retry_fails_so_the_deterministic_fallback_applies_exactly_as_before(self):
        calls = []

        def transport(url, headers, body, timeout):
            calls.append(1)
            return 200, json.dumps({"error": {"message": f"boom {KEY} SECRET-BODY"}})
        base = Agent(ToolGateway(FakeSession(ring=True)), Trigger("SYN-OR", TXN, "risk_score"), responder=PendingResponder()).run()
        rec, c, b = self.stack(transport, [])
        self.assertEqual(len(calls), 2)                                                     # first attempt + exactly one retry, then stop
        ag = rec["agentic"]
        self.assertEqual((ag["mode"], ag["fallback_used"]), ("deterministic_fallback", True))
        self.assertIn("[provider_http_error http=200 retry=attempted_failed]", ag["fallback"])
        self.assertEqual(core(rec), core(base))
        self.assertEqual(b.used, 0)

    def test_5_key_and_error_body_never_leak(self):
        body = json.dumps({"error": {"message": f"token {KEY} SECRET-BODY SECRET-USER"}})
        t, calls, sleeps = self.counting((200, body))
        c = self.make(t, sleeps)
        with self.assertRaises(L.LLMUnavailable) as cm:
            c.complete("SECRET-SYSTEM", "SECRET-USER", 10)
        rec, c2, b = self.stack(lambda *a: (200, body), [])
        blob = str(cm.exception) + json.dumps(c.failures) + json.dumps(c2.failures) + rec["agentic"]["fallback"] + json.dumps(rec["agentic"]["request_decision"])
        for secret in (KEY, "SECRET-BODY", "SECRET-USER", "SECRET-SYSTEM", "Bearer", "upstream"):
            self.assertNotIn(secret, blob)


class HarnessClassificationTests(unittest.TestCase):
    def test_classify_fallback(self):
        from agent.run_agentic_eval import classify_fallback
        pf = [{"category": "provider_timeout", "http_status": None, "retry_attempted": False, "retry_succeeded": None}]
        self.assertEqual(classify_fallback("LLMUnavailable: [provider_timeout retry=not_attempted] request timed out", pf)["category"], "provider_timeout")
        self.assertEqual(classify_fallback("BudgetExceeded: estimated 1358 tokens would exceed the case budget", [])["category"], "token_budget")
        self.assertEqual(classify_fallback("request decision: no valid evidence decision returned", [])["category"], "tool_call_validation")
        self.assertEqual(classify_fallback("something else", [])["category"], "unknown")
        self.assertIsNone(classify_fallback(None, [])["category"])


if __name__ == "__main__":
    unittest.main()
