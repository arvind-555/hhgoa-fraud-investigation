"""Offline tests for B5: token budget, prompt guard, output validation, fallbacks, caching, orchestrator integration. No network, no API key needed."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent import llm as L  # noqa: E402
from agent.answer_file import assemble, validate_answer  # noqa: E402
from test_case_write import make_record  # noqa: E402
from test_simulator_exposure import AS_OF, Forced  # noqa: E402


class FakeClient:
    model = "fake-model"

    def __init__(self, reply=None, fn=None, usage=(300, 120), fail=None):
        self.reply, self.fn, self.usage, self.fail = reply, fn, usage, fail
        self.prompts = []

    def complete(self, system, user, max_tokens):
        self.prompts.append((system, user, max_tokens))
        if self.fail:
            raise self.fail
        text = self.fn(user) if self.fn else self.reply
        return {"text": text, "input_tokens": self.usage[0], "output_tokens": self.usage[1], "model": self.model}


def good_explanation(user):
    """A compliant rewrite: reuse the facts' own sentences (they already satisfy every rule)."""
    facts = user.split("<facts>\n")[1].split("\n</facts>")[0]
    return facts


class Base(unittest.TestCase):
    def setUp(self):
        self._old = L.CACHE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        L.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        L.CACHE_DIR = self._old
        self._tmp.cleanup()

    def agent_kwargs(self, client, max_total=4000):
        b = L.TokenBudget(max_total)
        return dict(explainer=L.LLMExplainer(client, b), sar_writer=L.LLMSarWriter(client, b), budget=b), b


class BudgetTests(Base):
    def test_budget_charges_reported_usage(self):
        b = L.TokenBudget(1000)
        b.charge(100, 50)
        b.charge(10, 5)
        self.assertEqual((b.used_in, b.used_out, b.used, b.calls), (110, 55, 165, 2))

    def test_call_refused_before_sending_when_the_estimate_exceeds_the_budget(self):
        c = FakeClient(reply="x")
        b = L.TokenBudget(500)
        with self.assertRaises(L.BudgetExceeded):
            L._call(c, b, "sys", "u" * 3000)
        self.assertEqual(c.prompts, [])                                      # nothing was sent
        self.assertEqual(b.used, 0)

    def test_max_output_is_capped_per_call(self):
        c = FakeClient(reply="ok")
        L._call(c, L.TokenBudget(4000), "sys", "user", max_tokens=99999)
        self.assertEqual(c.prompts[0][2], L.MAX_CALL_OUTPUT)

    def test_cache_makes_reruns_reproducible_and_charges_the_original_usage(self):
        c = FakeClient(reply="ok", usage=(11, 7))
        b1, b2 = L.TokenBudget(), L.TokenBudget()
        r1 = L._call(c, b1, "sys", "user")
        r2 = L._call(c, b2, "sys", "user")
        self.assertEqual(len(c.prompts), 1)
        self.assertEqual((r1["cached"], r2["cached"]), (False, True))
        self.assertEqual((b1.used, b2.used), (18, 18))


class GuardTests(Base):
    def test_prompt_guard_blocks_sensitive_material(self):
        for bad in ("see HHG-014", "risk_score 0.9", "model_score", "snap_class HUB", "as_of 123", "seed hhgoa-b4-sim-v1", "closed_case_population 0.94"):
            with self.assertRaises(L.LLMUnavailable):
                L.guard_payload(bad)
        L.guard_payload("Evidence strength: strong; validated evidence: device ring [E1].")

    def test_real_prompts_contain_no_sensitive_material(self):
        c = FakeClient(fn=good_explanation)
        kw, b = self.agent_kwargs(c)
        make_record(case="TEST-L1", responder=Forced("denied_or_unrecognized"), **kw)
        self.assertGreaterEqual(len(c.prompts), 2)
        for system, user, _ in c.prompts:
            L.guard_payload(system + user)
            for banned in ("HHG-", "risk_score", "hhgoa", "b4-v1", "0.4", "SIMULATED response probabilities"):
                self.assertNotIn(banned, user)

    def test_api_key_is_never_placed_in_errors(self):
        def bad_transport(url, headers, body, timeout):
            return 401, json.dumps({"type": "error", "error": {"message": "invalid key sk-secret-123"}})
        cl = L.AnthropicHTTP(api_key="sk-secret-123", transport=bad_transport)
        with self.assertRaises(L.LLMUnavailable) as cm:
            cl.complete("s", "u", 10)
        self.assertNotIn("sk-secret-123", str(cm.exception))

    def test_missing_key_is_unavailable_not_a_crash(self):
        with self.assertRaises(L.LLMUnavailable):
            L.AnthropicHTTP(api_key="", transport=lambda *a: (200, "{}")).complete("s", "u", 10)

    def test_http_request_shape(self):
        seen = {}

        def transport(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=json.loads(body))
            return 200, json.dumps({"content": [{"type": "text", "text": " hi "}], "usage": {"input_tokens": 5, "output_tokens": 2}, "model": "m"})
        r = L.AnthropicHTTP(model="claude-haiku-4-5-20251001", api_key="k", transport=transport).complete("sys", "usr", 50)
        self.assertEqual((r["text"], r["input_tokens"], r["output_tokens"]), ("hi", 5, 2))
        self.assertEqual(seen["url"], L.API_URL)
        self.assertEqual((seen["body"]["temperature"], seen["body"]["max_tokens"], seen["headers"]["anthropic-version"]), (0, 50, "2023-06-01"))


class ExplainerTests(Base):
    def run_with(self, reply_fn, **kw):
        c = FakeClient(fn=reply_fn, **kw)
        k, b = self.agent_kwargs(c)
        rec = make_record(case="TEST-L2", **k)
        return rec, c, b

    def test_valid_llm_output_is_used_and_tokens_are_measured(self):
        rec, c, b = self.run_with(good_explanation)
        self.assertTrue(rec["llm"]["explanation"]["used"])
        self.assertEqual(rec["tokens"], b.used)
        self.assertGreater(rec["tokens"], 0)
        self.assertEqual(rec["llm"]["calls"], b.calls)
        self.assertIn("Evidence strength:", rec["case"]["summary"])

    def test_output_with_unknown_citation_falls_back_to_the_template(self):
        rec, c, b = self.run_with(lambda u: good_explanation(u) + " See [E99].")
        self.assertFalse(rec["llm"]["explanation"]["used"])
        self.assertIn("cites unknown evidence id", rec["llm"]["explanation"]["reason"])
        self.assertNotIn("E99", rec["case"]["summary"])

    def test_output_stating_a_probability_is_rejected(self):
        def bad(u):
            return good_explanation(u).replace("so no probability is stated", "so the probability of fraud is 85%")
        rec, c, b = self.run_with(bad)
        self.assertFalse(rec["llm"]["explanation"]["used"])
        self.assertIn("states a probability", rec["llm"]["explanation"]["reason"])
        self.assertNotRegex(rec["case"]["summary"], r"85")

    def test_output_missing_the_three_parts_is_rejected(self):
        rec, c, b = self.run_with(lambda u: "It looks bad. Block it. Thanks.")
        self.assertFalse(rec["llm"]["explanation"]["used"])
        self.assertIn("labelled parts", rec["llm"]["explanation"]["reason"])

    def test_third_part_must_say_probability_not_available(self):
        def bad(u):
            t = good_explanation(u)
            i = t.index("Calibrated probability:")
            j = t.index("Recommended")
            return t[:i] + "Calibrated probability: high. " + t[j:]
        rec, c, b = self.run_with(bad)
        self.assertFalse(rec["llm"]["explanation"]["used"])

    def test_api_failure_falls_back_and_never_blocks_the_case(self):
        rec, c, b = self.run_with(None, fail=L.LLMUnavailable("network error: URLError"))
        self.assertFalse(rec["llm"]["explanation"]["used"])
        self.assertFalse(rec["llm"]["sar"]["used"])
        self.assertEqual(rec["tokens"], 0)
        self.assertIn("Evidence strength:", rec["case"]["summary"])
        self.assertTrue(rec["sar"]["file"])                                      # template SAR still produced

    def test_budget_exhaustion_falls_back(self):
        c = FakeClient(fn=good_explanation)
        k, b = self.agent_kwargs(c, max_total=200)
        rec = make_record(case="TEST-L3", **k)
        self.assertFalse(rec["llm"]["explanation"]["used"])
        self.assertIn("BudgetExceeded", rec["llm"]["explanation"]["reason"])
        self.assertEqual(c.prompts, [])
        self.assertEqual(rec["tokens"], 0)


class SarWriterTests(Base):
    def go(self, reply_fn):
        c = FakeClient(fn=reply_fn)
        k, b = self.agent_kwargs(c)
        return make_record(case="TEST-L4", **k), c

    def test_valid_llm_narrative_replaces_the_template(self):
        def reply(user):
            if "template_narrative" in user:
                return json.loads(user.split("<facts>\n")[1].split("\n</facts>")[0])["template_narrative"].replace("Who:", "The subject is")
            return good_explanation(user)
        rec, c = self.go(reply)
        self.assertTrue(rec["llm"]["sar"]["used"])
        self.assertIn("The subject is", rec["sar"]["narrative"])
        self.assertEqual(validate_answer(assemble(rec), None)[0], [])

    def test_narrative_with_invented_amount_or_id_is_rejected(self):
        def reply(user):
            if "template_narrative" in user:
                return json.loads(user.split("<facts>\n")[1].split("\n</facts>")[0])["template_narrative"] + " A further loss of $9,999.99 on card C09999-K1 was found."
            return good_explanation(user)
        rec, c = self.go(reply)
        self.assertFalse(rec["llm"]["sar"]["used"])
        self.assertNotIn("9,999.99", rec["sar"]["narrative"])

    def test_no_report_means_no_llm_call_for_the_sar(self):
        c = FakeClient(fn=good_explanation)
        k, b = self.agent_kwargs(c)
        rec = make_record(case="TEST-L5", ring=False, responder=Forced("verified_legitimate"), trigger="customer_complaint", **k)
        self.assertFalse(rec["sar"]["file"])
        self.assertEqual(rec["llm"]["sar"]["reason"], "not called")
        self.assertTrue(all("template_narrative" not in p[1] for p in c.prompts))

    def test_answer_file_carries_the_measured_tokens(self):
        rec, c = self.go(lambda u: good_explanation(u) if "template_narrative" not in u else json.loads(u.split("<facts>\n")[1].split("\n</facts>")[0])["template_narrative"])
        a = assemble(rec)
        self.assertEqual(a["tokens"], rec["tokens"])
        self.assertGreater(a["tokens"], 0)
        e, _ = validate_answer(a, None, measured={"tool_calls": rec["tool_calls"], "tokens": rec["tokens"], "latency_s": rec["latency_s"]})
        self.assertEqual(e, [])


if __name__ == "__main__":
    unittest.main()
