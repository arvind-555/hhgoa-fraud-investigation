"""Offline tests of the OpenAI provider (GPT-5 family) behind the existing provider boundary. A fake transport stands in for the API: no network, no key."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agent.llm import LLMUnavailable  # noqa: E402
from agent.providers import OpenAIHTTP, OpenRouterHTTP, ProviderFailure, make_client  # noqa: E402

KEY = "sk-" + "test-" + "not-a-real-key-000000"          # built at runtime so no key-shaped literal sits in the source


class Fake:
    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def __call__(self, url, headers, body, timeout):
        self.sent.append((url, headers, json.loads(body)))
        return self.replies.pop(0)


def ok(msg, i=100, o=20, reasoning=0):
    return 200, json.dumps({"model": "gpt-5-mini-2025-08-07", "choices": [{"message": msg}], "usage": {"prompt_tokens": i, "completion_tokens": o, "completion_tokens_details": {"reasoning_tokens": reasoning}}})


TOOL = {"name": "get_transaction_context", "description": "d", "input_schema": {"type": "object", "properties": {"txn_id": {"type": "string"}}}}
CALL = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_transaction_context", "arguments": "{\"txn_id\": \"3000001\"}"}}]}


class OpenAITests(unittest.TestCase):
    def client(self, replies, **kw):
        f = Fake(replies)
        return OpenAIHTTP(api_key=KEY, transport=f, sleep=lambda s: None, **kw), f

    def test_request_shape_is_gpt5_compatible_and_carries_no_openrouter_fields(self):
        c, f = self.client([ok(CALL)])
        r = c.chat("sys", [{"role": "user", "content": "go"}], [TOOL], 700, {"type": "any"})
        url, headers, body = f.sent[0]
        self.assertEqual(url, "https://api.openai.com/v1/chat/completions")
        self.assertEqual(body["model"], "gpt-5-mini")
        self.assertEqual((body["max_completion_tokens"], body["reasoning_effort"], body["tool_choice"]), (700, "minimal", "required"))
        for absent in ("temperature", "max_tokens", "provider"):
            self.assertNotIn(absent, body)
        self.assertEqual(body["tools"][0]["function"]["name"], "get_transaction_context")
        self.assertEqual(r["stop_reason"], "tool_use")
        self.assertEqual(r["content"][0], {"type": "tool_use", "id": "c1", "name": "get_transaction_context", "input": {"txn_id": "3000001"}})
        self.assertEqual(r["model"], "gpt-5-mini-2025-08-07")                # the model id the API returned is recorded
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)          # sent to the API only

    def test_model_effort_and_prices_are_configurable(self):
        c, f = self.client([ok({"role": "assistant", "content": "x"})], model="gpt-5", reasoning_effort="low", price_in=1.25, price_out=10.0)
        c.complete("s", "u", 50)
        self.assertEqual((f.sent[0][2]["model"], f.sent[0][2]["reasoning_effort"]), ("gpt-5", "low"))
        self.assertAlmostEqual(c.spend(), 100 * 1.25 / 1e6 + 20 * 10.0 / 1e6, places=9)
        with self.assertRaises(LLMUnavailable):
            OpenAIHTTP(api_key=KEY, reasoning_effort="extreme")
        with self.assertRaises(LLMUnavailable):
            OpenAIHTTP(api_key=KEY, model="gpt 5; rm -rf")

    def test_usage_and_spend_include_reasoning_tokens_as_output(self):
        c, _ = self.client([ok(CALL, i=1000, o=300, reasoning=250)])
        c.chat("s", [{"role": "user", "content": "x"}], [TOOL], 700)
        self.assertEqual((c.usage_in, c.usage_out, c.usage_reasoning), (1000, 300, 250))
        self.assertAlmostEqual(c.spend(), 1000 * 0.25 / 1e6 + 300 * 2.0 / 1e6, places=9)

    def test_hard_spend_cap_stops_the_next_call_before_it_is_sent(self):
        c, f = self.client([ok(CALL, i=2000, o=1000), ok(CALL)], max_spend_usd=0.001)
        c.chat("s", [{"role": "user", "content": "x"}], [TOOL], 700)          # spends 0.0025 > cap
        with self.assertRaises(ProviderFailure) as cm:
            c.chat("s", [{"role": "user", "content": "x"}], [TOOL], 700)
        self.assertEqual(cm.exception.category, "token_budget")
        self.assertEqual(len(f.sent), 1)                                       # nothing further was sent

    def test_key_never_appears_in_repr_errors_or_failure_metadata(self):
        c, f = self.client([(401, "{\"error\": {\"message\": \"bad key " + KEY + "\"}}")])
        with self.assertRaises(ProviderFailure) as cm:
            c.complete("s", "u", 10)
        blob = repr(c) + str(cm.exception) + json.dumps(c.failures)
        self.assertNotIn(KEY, blob)
        self.assertIn("OpenAI provider error", str(cm.exception))
        self.assertNotIn("OpenRouter", str(cm.exception))

    def test_a_missing_key_fails_cleanly_and_names_only_the_variable(self):
        c = OpenAIHTTP(api_key="", transport=Fake([]))
        with self.assertRaises(ProviderFailure) as cm:
            c.complete("s", "u", 10)
        self.assertIn("OPENAI_API_KEY not set", str(cm.exception))

    def test_openai_is_selected_only_explicitly_and_never_by_default(self):
        env = {"OPENAI_API_KEY": KEY}
        self.assertIsNone(make_client(environ=env, env_path=str(ROOT / "nonexistent.env")))                     # a key alone does not enable a paid provider
        cl = make_client(environ={**env, "HHG_LLM_PROVIDER": "openai", "HHG_LLM_MODEL": "gpt-5-nano"}, env_path=str(ROOT / "nonexistent.env"))
        self.assertIsInstance(cl, OpenAIHTTP)
        self.assertEqual(cl._requested, "gpt-5-nano")
        self.assertIsNone(make_client(environ={"HHG_LLM_PROVIDER": "openai"}, env_path=str(ROOT / "nonexistent.env")))     # explicit but no key: deterministic fallback
        self.assertIsInstance(make_client(environ={"OPENROUTER_API_KEY": "x" * 20, "HHG_LLM_PROVIDER": "openrouter"}, env_path=str(ROOT / "nonexistent.env")), OpenRouterHTTP)   # OpenRouter kept

    def test_parallel_tool_calls_rejection_falls_back_once(self):
        c, f = self.client([(400, "{}"), ok(CALL)])
        c.chat("s", [{"role": "user", "content": "x"}], [TOOL], 700)
        self.assertIn("parallel_tool_calls", f.sent[0][2])
        self.assertNotIn("parallel_tool_calls", f.sent[1][2])


if __name__ == "__main__":
    unittest.main()
