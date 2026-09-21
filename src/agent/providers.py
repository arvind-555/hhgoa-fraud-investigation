"""LLM providers behind ONE boundary: `complete()` (wording calls) and `chat()` (tool-use turns), both returning the normalised shape the rest of the agent already consumes
(content blocks of type text / tool_use, input_tokens / output_tokens, model). Nothing above this boundary changes (investigator, orchestrator, gateway, permissions, validators).

* `AnthropicHTTP` (agent/llm.py) - unchanged apart from reading its key through `env_get`.
* `OpenRouterHTTP` - OpenAI-compatible chat/completions with function calling. Anthropic-style blocks are translated at this edge:
    system prompt        -> a `system` message
    tool definitions     -> {"type": "function", "function": {name, description, parameters}}
    assistant tool_use   -> assistant message with `tool_calls` (arguments as a JSON string)
    user tool_result     -> one `role: "tool"` message per result
    tool_choice "any"    -> "required" (one reminder retry if the model still answers without a tool call)
  and back: `tool_calls` -> tool_use blocks (malformed argument JSON -> empty input, which the gateway refuses like any invalid call).

Guarantees
* FREE MODELS ONLY: an OpenRouter model id must end with ":free"; there is no paid model, no paid fallback list, no credits logic. Anything else is refused at construction.
* the prompt guard (`llm.guard_payload`) and token budget run in `llm._chat/_call` BEFORE any provider call, so OpenRouter receives exactly what Anthropic would: nothing new is sent.
* the API key is read from the environment or .env, kept only in a private attribute, never logged, never in repr/exception text; request/response bodies are never logged or stored
  (only the response cache in llm.py, which is keyed by a hash and keeps the model reply, as before).
* the model id RETURNED by OpenRouter is recorded per call (`models_seen`) and in every result's `model` field.

Environment (process environment first, then the git-ignored .env; only these names are read):
  HHG_LLM_PROVIDER       openrouter | anthropic        (default: openrouter if OPENROUTER_API_KEY is set, else anthropic if ANTHROPIC_API_KEY is set, else none)
  OPENROUTER_API_KEY     key
  OPENROUTER_BASE_URL    default https://openrouter.ai/api/v1
  HHG_LLM_MODEL          model override for the selected provider (OpenRouter: must end with :free)
  HHG_LLM_TIMEOUT_S      default 90
  HHG_LLM_PARALLEL_TOOL_CALLS   1 (default) | 0 : send OpenAI-compatible parallel_tool_calls on OpenRouter tool-use turns; if the provider rejects it the client retries once without it
  OPENROUTER_HTTP_REFERER / OPENROUTER_APP_TITLE   optional attribution headers
  OpenAI (explicit only: HHG_LLM_PROVIDER=openai; never selected automatically because it is a PAID provider):
  OPENAI_API_KEY         key (process environment or .env only; never printed)
  OPENAI_BASE_URL        default https://api.openai.com/v1
  HHG_LLM_MODEL          default gpt-5-mini
  HHG_LLM_REASONING_EFFORT   minimal (default) | low | medium | high   (reasoning tokens are billed as output tokens)
  HHG_LLM_PRICE_IN_PER_M / HHG_LLM_PRICE_OUT_PER_M   USD per million tokens used ONLY for the spend estimate (defaults: gpt-5-mini 0.25 / 2.00)
  HHG_LLM_MAX_SPEND_USD  hard cap on the estimated spend of one client (default 0.30): once reached, every further call fails cleanly before it is sent
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from .llm import LLMUnavailable

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_ENV = re.compile(r"^(HHG_LLM_[A-Z_]+|OPENROUTER_[A-Z_]+|OPENAI_[A-Z_]+|ANTHROPIC_API_KEY)$")
DEFAULT_OPENROUTER_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"       # verified free, tool calling + multi-turn (see docs/agent_orchestration.md)
DEFAULT_BASE = "https://openrouter.ai/api/v1"
CHARS_PER_TOKEN = 3.5
REQUIRED_REMINDER = "You must respond by calling exactly one of the provided tools. Do not answer in plain text."


CATEGORIES = ("provider_http_error", "provider_timeout", "provider_empty_response", "provider_malformed_response", "provider_rate_limit", "token_budget", "tool_call_validation", "unknown")


class ProviderFailure(LLMUnavailable):
    """A provider failure described ONLY by a sanitized category, the HTTP status and the retry outcome: never the API key, a request body or a provider error body."""
    def __init__(self, category, status=None, detail="", retry_attempted=False, retry_succeeded=None):
        self.category, self.status, self.retry_attempted, self.retry_succeeded = category, status, retry_attempted, retry_succeeded
        retry = "not_attempted" if not retry_attempted else ("attempted_succeeded" if retry_succeeded else "attempted_failed")
        super().__init__(f"[{category}{'' if status is None else f' http={status}'} retry={retry}] {detail}")

    def meta(self):
        return {"category": self.category, "http_status": self.status, "retry_attempted": self.retry_attempted, "retry_succeeded": self.retry_succeeded}


def env_get(name, env_path=None, environ=None):
    """Process environment first, then the git-ignored .env. Only whitelisted variable names are ever read; values are never printed by this module."""
    if not ALLOWED_ENV.match(name):
        raise ValueError(f"environment variable not allowed: {name}")
    environ = os.environ if environ is None else environ
    if environ.get(name):
        return environ[name].strip()
    p = Path(env_path) if env_path else ROOT / ".env"
    try:
        for k, v in re.findall(r"^([A-Z_]+)=(.*)$", p.read_text(encoding="utf-8"), re.M):
            if k == name and v.strip():
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def is_free_model(model):
    return isinstance(model, str) and bool(re.match(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+:free$", model))


class OpenRouterHTTP:
    provider = "openrouter"

    def __init__(self, model=None, api_key=None, transport=None, timeout=None, base_url=None, sleep=time.sleep, parallel_tool_calls=None):
        requested = model or DEFAULT_OPENROUTER_MODEL
        if not is_free_model(requested):
            raise LLMUnavailable("only OpenRouter models whose id ends with ':free' are permitted (no paid models)")
        self._requested = requested
        if parallel_tool_calls is None:
            parallel_tool_calls = (env_get("HHG_LLM_PARALLEL_TOOL_CALLS") or "1").lower() not in ("0", "false", "no", "off")
        self.parallel_requested = bool(parallel_tool_calls)                    # OpenAI-compatible parallel_tool_calls, sent only on tool-use turns of THIS provider
        self._parallel_disabled = False                                         # set if the provider/model rejects the parameter: sequential behaviour from then on
        self.parallel_events, self._last_sent_parallel = [], False
        self.model = "openrouter:" + requested + ("|parallel" if self.parallel_requested else "")      # part of the response-cache key (never mixed with another provider/setting)
        self._key = api_key if api_key is not None else env_get("OPENROUTER_API_KEY")
        self._base = (base_url or env_get("OPENROUTER_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.timeout = int(timeout or (env_get("HHG_LLM_TIMEOUT_S") or 90))
        self._transport, self._sleep = transport or self._urllib, sleep
        self._extra = {k: v for k, v in (("HTTP-Referer", env_get("OPENROUTER_HTTP_REFERER")), ("X-Title", env_get("OPENROUTER_APP_TITLE"))) if v}
        self.models_seen, self.calls = [], 0
        self.failures, self.retry_events = [], []                          # sanitized failure metadata / retry outcomes, read by the evaluation harness

    def __repr__(self):
        return f"OpenRouterHTTP(model={self._requested!r})"                   # never includes the key

    @staticmethod
    def _urllib(url, headers, body, timeout):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            reason = getattr(e, "reason", None)
            if isinstance(e, TimeoutError) or isinstance(reason, TimeoutError):
                raise ProviderFailure("provider_timeout", None, "request timed out")
            raise ProviderFailure("provider_http_error", None, f"connection error: {type(e).__name__}")

    def pop_models(self):
        seen, self.models_seen = self.models_seen, []
        return seen

    def pop_failures(self):
        f, self.failures = self.failures, []
        return f

    def pop_parallel_events(self):
        e, self.parallel_events = self.parallel_events, []
        return e

    def pop_retry_events(self):
        r, self.retry_events = self.retry_events, []
        return r

    @staticmethod
    def _bad_200(text):
        """HTTP 200 whose JSON body is an error object or has no choices (never unparseable text: that stays provider_malformed_response). The body itself is never kept."""
        try:
            j = json.loads(text)
        except Exception:
            return False
        return not isinstance(j, dict) or bool(j.get("error")) or not j.get("choices")

    def _fail(self, category, status, detail, retried, retry_ok):
        e = ProviderFailure(category, status, detail, retried, retry_ok)
        self.failures.append(e.meta())
        return e

    # ------------------------------------------------------------------------------------------ transport
    def _post(self, body):
        if not self._key:
            raise self._fail("unknown", None, getattr(self, "_key_name", "OPENROUTER_API_KEY") + " not set", False, None)
        headers = {"Authorization": "Bearer " + self._key, "Content-Type": "application/json", **self._extra}
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        retried, first_status = False, None
        for attempt in (0, 1):                                                # free endpoints rate-limit: one short retry, then fail to the deterministic fallback
            try:
                status, text = self._transport(self._base + "/chat/completions", headers, payload, self.timeout)
            except LLMUnavailable as e:
                raise self._fail(getattr(e, "category", "provider_http_error"), None, "transport failure", retried, False if retried else None)
            if attempt == 0 and status in (400, 404, 422) and body.get("parallel_tool_calls"):
                body = {k: v for k, v in body.items() if k != "parallel_tool_calls"}      # the provider/model rejected the parameter: clean sequential fallback,
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")           # using this request's one retry (no extra retries, no backoff)
                self._parallel_disabled = True
                self.parallel_events.append({"event": "rejected", "http_status": status})
                retried, first_status = True, status
                continue
            if attempt == 0 and (status in (429, 502, 503, 504) or (status == 200 and self._bad_200(text))):
                retried, first_status = True, status                    # exactly one retry, same backoff; a 200 carrying an error object / no choices counts as a provider error
                self._sleep(3)
                continue
            break
        self.calls += 1
        self._last_sent_parallel = bool(body.get("parallel_tool_calls"))
        ok_after_retry = None if not retried else False
        try:
            j = json.loads(text)
        except Exception:
            raise self._fail("provider_malformed_response", status, "response is not JSON", retried, ok_after_retry)
        if status != 200:
            raise self._fail("provider_rate_limit" if status == 429 else "provider_http_error", status, (getattr(self, "_label", "OpenRouter") + (" rate limited" if status == 429 else " provider error")),
                             retried, ok_after_retry)
        if not isinstance(j, dict) or not j.get("choices"):
            raise self._fail("provider_http_error", status, "error object in a 200 response" if (isinstance(j, dict) and j.get("error")) else "response has no choices",
                             retried, ok_after_retry)
        if retried:
            self.retry_events.append({"first_status": first_status, "retry_attempted": True, "retry_succeeded": True})
        return j

    @staticmethod
    def _usage(j, sent_chars, out_chars):
        u = j.get("usage") or {}
        if u.get("prompt_tokens") is None or u.get("completion_tokens") is None:
            return int(sent_chars / CHARS_PER_TOKEN) + 1, int(out_chars / CHARS_PER_TOKEN) + 1, True
        return int(u["prompt_tokens"]), int(u["completion_tokens"]), False

    def _record(self, j):
        m = j.get("model") or self._requested
        self.models_seen.append(m)
        return m

    def _body(self, messages, max_tokens, tools=None, tool_choice=None):
        b = {"model": self._requested, "messages": messages, "temperature": 0, "max_tokens": int(max_tokens),
             "provider": {"require_parameters": True}}                          # route only to providers that support every parameter we send (tools, tool_choice)
        if tools:
            b["tools"] = tools
            if self.parallel_requested and not self._parallel_disabled:
                b["parallel_tool_calls"] = True
        if tool_choice:
            b["tool_choice"] = tool_choice
        return b

    # ------------------------------------------------------------------------------------------ complete(): plain wording call
    def complete(self, system, user, max_tokens):
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        j = self._post(self._body(msgs, max_tokens))
        text = (j["choices"][0]["message"].get("content") or "").strip()
        i, o, est = self._usage(j, len(system) + len(user), len(text))
        return {"text": text, "input_tokens": i, "output_tokens": o, "model": self._record(j), "usage_estimated": est}

    # ------------------------------------------------------------------------------------------ chat(): tool-use turn
    @staticmethod
    def to_openai_messages(system, messages):
        out = [{"role": "system", "content": system}]
        for m in messages:
            c = m["content"]
            if isinstance(c, str):
                out.append({"role": m["role"], "content": c})
                continue
            if m["role"] == "assistant":
                text = "".join(b.get("text", "") for b in c if b.get("type") == "text")
                calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                         for b in c if b.get("type") == "tool_use"]
                msg = {"role": "assistant", "content": text}
                if calls:
                    msg["tool_calls"] = calls
                out.append(msg)
            else:
                for b in c:
                    if b.get("type") == "tool_result":
                        out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])})
                    elif b.get("type") == "text":
                        out.append({"role": "user", "content": b["text"]})
        return out

    @staticmethod
    def to_openai_tools(tools):
        return [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""), "parameters": t["input_schema"]}} for t in tools]

    @staticmethod
    def from_openai_message(msg):
        blocks = []
        if (msg.get("content") or "").strip():
            blocks.append({"type": "text", "text": msg["content"].strip()})
        for k, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
                if not isinstance(args, dict):
                    args = {}
            except Exception:
                args = {}                                                       # malformed arguments: an empty call, refused by the gateway like any invalid call
            blocks.append({"type": "tool_use", "id": tc.get("id") or f"call_{k}", "name": fn.get("name", ""), "input": args})
        return blocks

    def chat(self, system, messages, tools, max_tokens, tool_choice=None):
        oa_msgs = self.to_openai_messages(system, messages)
        oa_tools = self.to_openai_tools(tools)
        choice = "required" if (tool_choice or {}).get("type") == "any" else None
        j = self._post(self._body(oa_msgs, max_tokens, oa_tools, choice))
        msg = j["choices"][0]["message"]
        i, o, est = self._usage(j, len(json.dumps(oa_msgs)) + len(json.dumps(oa_tools)), len(json.dumps(msg)))
        models = [self._record(j)]
        if choice == "required" and not (msg.get("tool_calls") or []):          # some free models ignore tool_choice: one explicit reminder, usage of both calls is charged
            j2 = self._post(self._body(oa_msgs + [{"role": "user", "content": REQUIRED_REMINDER}], max_tokens, oa_tools, choice))
            msg = j2["choices"][0]["message"]
            i2, o2, est2 = self._usage(j2, len(json.dumps(oa_msgs)) + len(json.dumps(oa_tools)), len(json.dumps(msg)))
            i, o, est = i + i2, o + o2, est or est2
            models.append(self._record(j2))
        blocks = self.from_openai_message(msg)
        n_calls = sum(1 for b in blocks if b["type"] == "tool_use")
        self.parallel_events.append({"event": "turn", "requested": self.parallel_requested, "sent": self._last_sent_parallel, "n_tool_calls": n_calls})
        stop = "tool_use" if n_calls else "end_turn"
        return {"content": blocks, "stop_reason": stop, "input_tokens": i, "output_tokens": o, "model": models[-1], "models": models, "usage_estimated": est}


class OpenAIHTTP(OpenRouterHTTP):
    """OpenAI chat/completions with function calling (GPT-5 family). Same translation edge as OpenRouterHTTP; differences: paid model (explicit opt-in), `max_completion_tokens`,
    `reasoning_effort`, no temperature, and a HARD SPEND CAP computed from the usage the API reports. The key is read from the environment / .env only and never printed."""
    provider = "openai"
    _key_name, _label = "OPENAI_API_KEY", "OpenAI"

    def __init__(self, model=None, api_key=None, transport=None, timeout=None, base_url=None, sleep=time.sleep, parallel_tool_calls=None, reasoning_effort=None,
                 price_in=None, price_out=None, max_spend_usd=None):
        requested = model or "gpt-5-mini"
        if not re.match(r"^[A-Za-z0-9_.\-]+$", requested):
            raise LLMUnavailable("invalid OpenAI model id")
        self._requested = requested
        if parallel_tool_calls is None:
            parallel_tool_calls = (env_get("HHG_LLM_PARALLEL_TOOL_CALLS") or "1").lower() not in ("0", "false", "no", "off")
        self.parallel_requested, self._parallel_disabled = bool(parallel_tool_calls), False
        self.parallel_events, self._last_sent_parallel = [], False
        self.model = "openai:" + requested + ("|parallel" if self.parallel_requested else "")
        self._key = api_key if api_key is not None else env_get("OPENAI_API_KEY")
        self._base = (base_url or env_get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
        self.timeout = int(timeout or (env_get("HHG_LLM_TIMEOUT_S") or 90))
        self._transport, self._sleep, self._extra = transport or self._urllib, sleep, {}
        self.models_seen, self.calls = [], 0
        self.failures, self.retry_events = [], []
        self.reasoning_effort = (reasoning_effort or env_get("HHG_LLM_REASONING_EFFORT") or "minimal").lower()
        if self.reasoning_effort not in ("minimal", "low", "medium", "high"):
            raise LLMUnavailable("invalid reasoning effort")
        self.price_in = float(price_in if price_in is not None else (env_get("HHG_LLM_PRICE_IN_PER_M") or 0.25))
        self.price_out = float(price_out if price_out is not None else (env_get("HHG_LLM_PRICE_OUT_PER_M") or 2.00))
        self.max_spend = float(max_spend_usd if max_spend_usd is not None else (env_get("HHG_LLM_MAX_SPEND_USD") or 0.30))
        self.usage_in = self.usage_out = self.usage_reasoning = 0

    def __repr__(self):
        return f"OpenAIHTTP(model={self._requested!r})"

    def spend(self):
        """Estimated spend in USD from the token usage the API reported (input at price_in, output incl. reasoning at price_out)."""
        return round(self.usage_in * self.price_in / 1e6 + self.usage_out * self.price_out / 1e6, 6)

    def _body(self, messages, max_tokens, tools=None, tool_choice=None):
        b = {"model": self._requested, "messages": messages, "max_completion_tokens": int(max_tokens), "reasoning_effort": self.reasoning_effort}
        if tools:
            b["tools"] = tools
            if self.parallel_requested and not self._parallel_disabled:
                b["parallel_tool_calls"] = True
        if tool_choice:
            b["tool_choice"] = tool_choice
        return b

    def _post(self, body):
        if self.spend() >= self.max_spend:                                    # checked BEFORE the request: the cap can only be exceeded by the last completed call
            raise self._fail("token_budget", None, f"spend cap reached (${self.spend():.4f} of ${self.max_spend:.2f})", False, None)
        j = super()._post(body)
        u = j.get("usage") or {}
        self.usage_in += int(u.get("prompt_tokens") or 0)
        self.usage_out += int(u.get("completion_tokens") or 0)
        self.usage_reasoning += int(((u.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
        return j


def make_client(environ=None, env_path=None, transport=None):
    """Provider factory. Returns a client or None (no key: the caller runs the deterministic pipeline). Raises LLMUnavailable for a forbidden (paid) OpenRouter model."""
    g = lambda n: env_get(n, env_path, environ)   # noqa: E731
    provider = (g("HHG_LLM_PROVIDER") or "").lower()
    if not provider:
        provider = "openrouter" if g("OPENROUTER_API_KEY") else "anthropic" if g("ANTHROPIC_API_KEY") else ""
    if provider == "openrouter":
        if not g("OPENROUTER_API_KEY"):
            return None
        return OpenRouterHTTP(model=g("HHG_LLM_MODEL") or None, api_key=g("OPENROUTER_API_KEY"), transport=transport, timeout=int(g("HHG_LLM_TIMEOUT_S") or 90),
                              base_url=g("OPENROUTER_BASE_URL") or None,
                              parallel_tool_calls=(g("HHG_LLM_PARALLEL_TOOL_CALLS") or "1").lower() not in ("0", "false", "no", "off"))
    if provider == "openai":
        if not g("OPENAI_API_KEY"):
            return None
        return OpenAIHTTP(model=g("HHG_LLM_MODEL") or None, api_key=g("OPENAI_API_KEY"), transport=transport, timeout=int(g("HHG_LLM_TIMEOUT_S") or 90), base_url=g("OPENAI_BASE_URL") or None)
    if provider == "anthropic":
        from .llm import AnthropicHTTP
        if not g("ANTHROPIC_API_KEY"):
            return None
        return AnthropicHTTP(model=g("HHG_LLM_MODEL") or None, api_key=g("ANTHROPIC_API_KEY"), transport=transport)
    if provider:
        raise LLMUnavailable(f"unknown HHG_LLM_PROVIDER {provider!r}")
    return None
