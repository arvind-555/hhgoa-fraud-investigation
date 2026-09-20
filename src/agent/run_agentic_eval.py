"""Evaluation harness for the LLM-orchestrated investigation on HISTORICAL, non-benchmark transactions against the live graph.

Modes (same runner-controlled as_of, same tools, same deterministic authority):
  deterministic   the fixed pipeline (baseline for the authority check and latency)
  llm             the real model via the configured provider (agent/providers.py: OpenRouter free model or Anthropic; skipped with a clear message if no key is configured).
                  The model id actually returned by the provider is recorded for every case.
  scripted:<name> a scripted stand-in for the model (thorough | smart | minimal | erratic). It exercises the harness, the guards and the measurements; its "tokens" are an
                  ESTIMATE (len/4), so scripted numbers must never be read as real-model figures.

Reported per mode: tool calls and order, unnecessary calls (duplicates + refused + calls whose result nothing used), evidence coverage (what the model itself covered of the
required evidence vs what the deterministic layer had to force), tokens, latency, fallback rate, and whether the authoritative case (verdict, affected ids, exposure, actions/routes,
SAR decision) is identical to the deterministic baseline. Usage: python src/agent/run_agentic_eval.py [n_cases] [modes comma separated] [limit: run only the first K selected cases]
"""
import json
import os
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from agent import llm as L  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.investigator import LLMInvestigator  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.providers import make_client  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from agent.simulator import EvidenceSimulator  # noqa: E402
from fraud_tools.qclient import QueryClient  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402

OUT = ROOT / "data" / "runs" / "agentic_eval"
LLM_CLIENT = {"c": None}


def use(i, name, **inp):
    return {"type": "tool_use", "id": f"tu_{i}_{name}", "name": name, "input": inp}


class ScriptedChat:
    def __init__(self, name, txn):
        self.name, self.txn, self.turns = name, txn, 0
        self.model = f"scripted-{name}"          # part of the response-cache key: scripted policies must never share cached replies

    def _ctx(self, messages):
        for m in messages:
            if m["role"] == "user" and isinstance(m["content"], list):
                for b in m["content"]:
                    if b.get("type") == "tool_result" and '"card_id"' in b["content"] and '"customer_id"' in b["content"]:
                        try:
                            return json.loads(b["content"])
                        except Exception:
                            return None
        return None

    def chat(self, system, messages, tools, max_tokens, tool_choice=None):
        self.turns += 1
        names = {t["name"] for t in tools}
        est = len(json.dumps([system, messages, tools])) // 4
        if "request_evidence" in names:
            view = json.loads(messages[0]["content"].split("\n")[1])
            blocks = [use(0, "request_evidence", type="step_up_auth" if view["channel"] == "online" else "customer_validation", reason="verify")] if view["policy_requires_verification"] \
                else [use(0, "no_further_evidence", reason="evidence is conclusive or a request could not change the decision")]
            return {"content": blocks, "stop_reason": "tool_use", "input_tokens": est, "output_tokens": 50, "model": "scripted"}
        step = sum(1 for m in messages if m["role"] == "assistant")
        ctx = self._ctx(messages)
        t = self.txn
        if self.name == "thorough":
            if step == 0:
                b = [use(0, "get_transaction_context", txn_id=t), use(1, "detect_fraud_patterns", txn_id=t)]
            elif step == 1 and ctx:
                c, u = ctx["card_id"], ctx["customer_id"]
                b = [use(2, "get_card_history", card_id=c, hours=720, max_rows=300), use(3, "find_shared_devices", card_id=c), use(4, "find_prior_cases", card_id=c),
                     use(5, "find_connected_entities", card_id=c), use(6, "get_customer_history", customer_id=u), use(7, "find_similar_cases", txn_id=t)]
            else:
                b = [use(9, "finish_investigation", sufficient=True, rationale="all evidence classes checked")]
        elif self.name == "smart":       # plausible economical investigator: signals first, dig deeper only when something fired
            if step == 0:
                b = [use(0, "get_transaction_context", txn_id=t), use(1, "detect_fraud_patterns", txn_id=t)]
            elif step == 1 and ctx and any('"fired":[' in x["content"] and '"fired":[]' not in x["content"] for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                                            for x in m["content"] if x.get("type") == "tool_result"):
                c = ctx["card_id"]
                b = [use(2, "get_card_history", card_id=c, hours=720, max_rows=300), use(3, "find_shared_devices", card_id=c), use(4, "find_prior_cases", card_id=c), use(7, "find_similar_cases", txn_id=t)]
            else:
                b = [use(9, "finish_investigation", sufficient=True, rationale="no further evidence would change the recommendation")]
        elif self.name == "minimal":
            b = [use(0, "get_transaction_context", txn_id=t)] if step == 0 else [use(9, "finish_investigation", sufficient=True, rationale="context checked")]
        else:                            # erratic
            b = [use(step, "run_gsql", query="x"), use(step + 10, "get_transaction_context", txn_id=t, as_of=5), use(step + 20, "get_card_history", card_id="bad"),
                 use(step + 30, "get_transaction_context", txn_id=t)]
        return {"content": b, "stop_reason": "tool_use", "input_tokens": est, "output_tokens": 60, "model": "scripted"}


def classify_fallback(text, failures):
    """Sanitized fallback category + status + retry outcome. Provider failures carry their own metadata (agent/providers.py ProviderFailure); the rest is mapped from the
    fixed strings of the budget guard and the decision protocol. Nothing here ever contains a key, a request body or a provider error body."""
    if not text:
        return {"category": None, "http_status": None, "retry_attempted": None, "retry_succeeded": None}
    if failures:
        f = failures[-1]
        return {k: f[k] for k in ("category", "http_status", "retry_attempted", "retry_succeeded")}
    if "BudgetExceeded" in text:
        return {"category": "token_budget", "http_status": None, "retry_attempted": None, "retry_succeeded": None}
    if "no valid evidence decision" in text:
        return {"category": "tool_call_validation", "http_status": None, "retry_attempted": None, "retry_succeeded": None}
    return {"category": "unknown", "http_status": None, "retry_attempted": None, "retry_succeeded": None}


def parallel_status(requested, events):
    """not_requested | rejected_then_sequential | honored (some turn returned >= 2 tool calls) | accepted_not_used (accepted, but every turn returned <= 1 call)."""
    if not requested:
        return "not_requested"
    if any(e.get("event") == "rejected" for e in events):
        return "rejected_then_sequential"
    turns = [e for e in events if e.get("event") == "turn"]
    if any(e["n_tool_calls"] >= 2 for e in turns):
        return "honored"
    return "accepted_not_used" if turns else "no_model_turn_completed"


def authority(rec):
    c = rec["case"]
    return {"verdict": c["verdict"], "status": c["status"], "affected": c["affected_txn_ids"], "exposure": c["exposure_usd"], "pattern": c["pattern"],
            "initial": [(a["action"], a["route"]) for a in rec["next_best_actions"]["initial"]], "final": [(a["action"], a["route"]) for a in rec["next_best_actions"]["final"]],
            "sar": rec["sar"]["file"], "connected": c["connected_card_ids"]}


def pick_cases(n):
    from common import EVAL_START_EPOCH, HIST_END_EPOCH, build_master
    m = build_master()
    jo = m[(m["epoch"] >= EVAL_START_EPOCH) & (m["epoch"] < HIST_END_EPOCH)]
    per = max(1, n // 3)
    fr = jo[jo["fraud"]].sort_values("epoch").groupby("case_id").first().reset_index().sample(per, random_state=21)
    cl = jo[jo["cleared"]].sample(per, random_state=21)
    ot = jo[(~jo["fraud"]) & (~jo["cleared"])].sample(n - 2 * per, random_state=21)
    ring = m[m["dev"] == "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"].sort_values(["epoch", "TransactionID"])
    seen, r0 = [], None
    for _, r in ring.iterrows():
        if r["customer_id"] not in seen:
            seen.append(r["customer_id"])
            if len(seen) == 12:
                r0 = r
                break
    rows = [r for _, df in (("f", fr), ("c", cl), ("o", ot)) for _, r in df.iterrows()] + [r0]
    return rows


def run_one(client, row, mode, k):
    as_of = int(row["epoch"])
    txn = str(int(row["TransactionID"]))
    gw = ToolGateway(InvestigationSession(as_of, client))
    budget = L.TokenBudget()
    inv = None
    if mode.startswith("scripted:"):
        L.CACHE_DIR = OUT / "scripted_cache"      # never mix scripted replies with the real-model cache
        inv = LLMInvestigator(ScriptedChat(mode.split(":")[1], txn), budget)
    elif mode == "llm":
        inv = LLMInvestigator(LLM_CLIENT["c"], budget)
    t0 = time.perf_counter()
    rec = Agent(gw, Trigger(f"SYN-{k:03d}", txn, "risk_score"), responder=EvidenceSimulator(), investigator=inv, budget=budget if inv else None).run()
    return rec, time.perf_counter() - t0, inv


def main(n=9, modes=("deterministic", "scripted:smart", "scripted:thorough", "scripted:minimal", "scripted:erratic"), limit=None):
    if "llm" in modes:
        LLM_CLIENT["c"] = make_client()
        if LLM_CLIENT["c"] is None:
            print("mode 'llm' skipped: no LLM provider key is configured (no real-model measurement is possible)")
            modes = tuple(m for m in modes if m != "llm")
        else:
            L.CACHE_DIR = OUT / f"llm_cache_{int(time.time())}"          # fresh cache: every reply is a real call, so latency and tokens are measured, not replayed
            print("llm provider:", getattr(LLM_CLIENT["c"], "provider", "anthropic"), "| requested model:", getattr(LLM_CLIENT["c"], "_requested", getattr(LLM_CLIENT["c"], "model", "?")))
    client = QueryClient()
    rows = pick_cases(n)[:limit] if limit else pick_cases(n)
    OUT.mkdir(parents=True, exist_ok=True)
    base = {}
    results = {}
    for mode in modes:
        res = []
        for k, row in enumerate(rows, 1):
            try:
                rec, dt, inv = run_one(client, row, mode, k)
            except Exception as e:
                res.append({"case": k, "error": f"{type(e).__name__}: {str(e)[:100]}"})
                print(mode, k, "ERROR", res[-1]["error"], flush=True)
                continue
            ag = rec["agentic"]
            models = LLM_CLIENT["c"].pop_models() if mode == "llm" and hasattr(LLM_CLIENT["c"], "pop_models") else []
            fails = LLM_CLIENT["c"].pop_failures() if mode == "llm" and hasattr(LLM_CLIENT["c"], "pop_failures") else []
            retries = LLM_CLIENT["c"].pop_retry_events() if mode == "llm" and hasattr(LLM_CLIENT["c"], "pop_retry_events") else []
            pev = LLM_CLIENT["c"].pop_parallel_events() if mode == "llm" and hasattr(LLM_CLIENT["c"], "pop_parallel_events") else []
            per_turn = Counter(x["step"] for x in (inv.log["steps"] if inv is not None else []))
            turns_n = [per_turn[k] for k in sorted(per_turn)]
            par = parallel_status(getattr(LLM_CLIENT["c"], "parallel_requested", False) if mode == "llm" else False, pev)
            fb = classify_fallback(ag["fallback"], fails)
            if mode == "deterministic":
                base[k] = authority(rec)
            res.append({"case": k, "latency_s": round(dt, 2), "tool_calls": rec["tool_calls"], "graph_q": rec["gateway"]["graph_queries_executed"], "tokens": rec["tokens"],
                        "mode": ag["mode"], "fallback": bool(ag["fallback"]), "llm_calls": ag.get("llm_tool_calls"), "forced": len(ag["forced_calls"]), "invalid": ag.get("invalid_calls"),
                        "duplicate": ag.get("duplicate_calls"), "unnecessary": ag.get("unnecessary_calls"), "cov_llm": ag.get("coverage_required_by_llm"),
                        "order": ",".join(x["tool"].replace("get_", "").replace("find_", "") + ("*" if x["source"] == "forced" else "") for x in ag.get("tool_order", [])),
                        "same_as_baseline": authority(rec) == base.get(k) if k in base else None, "request": ag["request_decision"]["source"] if ag.get("request_decision") else None,
                        "models_returned": sorted(set(models)), "provider_calls": len(models), "fallback_reason": ag["fallback"], "fallback_category": fb["category"],
                        "http_status": fb["http_status"], "retry_attempted": fb["retry_attempted"], "retry_succeeded": fb["retry_succeeded"], "retry_events": retries,
                        "steps_before_end": [x["tool"] for x in (inv.log["steps"] if inv is not None else [])],
                        "parallel_status": par, "calls_per_turn": turns_n, "max_calls_in_one_turn": max(turns_n) if turns_n else 0,
                        "turns": [{"turn": t["turn"], "in": t["input_tokens"], "out": t["output_tokens"], "est_split": t["estimated_input_split"]} for t in (inv.log["turns"] if inv is not None else [])],
                        "views_truncated": inv.log["views_truncated"] if inv is not None else None, "finish_called": bool(inv is not None and (inv.log.get("finish") or {}).get("sufficient") is not None), "llm_status": {"explanation": rec["llm"]["explanation"].get("reason"), "sar": rec["llm"]["sar"].get("reason")}})
            print(mode, k, res[-1]["latency_s"], res[-1]["order"], res[-1]["same_as_baseline"], flush=True)
        results[mode] = res
    summary = {}
    for mode, res in results.items():
        ok = [r for r in res if "error" not in r]
        lat = [r["latency_s"] for r in ok]
        summary[mode] = {"cases": len(res), "errors": len(res) - len(ok), "latency_s_p50": round(statistics.median(lat), 2) if lat else None, "latency_s_max": max(lat) if lat else None,
                         "tool_calls_mean": round(statistics.mean(r["tool_calls"] for r in ok), 2) if ok else None,
                         "llm_calls_mean": round(statistics.mean(r["llm_calls"] or 0 for r in ok), 2) if ok else None,
                         "forced_mean": round(statistics.mean(r["forced"] for r in ok), 2) if ok else None,
                         "unnecessary_mean": round(statistics.mean(r["unnecessary"] or 0 for r in ok), 2) if ok else None,
                         "coverage_by_model_mean": round(statistics.mean(r["cov_llm"] for r in ok if r["cov_llm"] is not None), 3) if any(r["cov_llm"] is not None for r in ok) else None,
                         "tokens_mean": round(statistics.mean(r["tokens"] for r in ok), 0) if ok else None,
                         "fallback_rate": round(sum(r["fallback"] for r in ok) / len(ok), 3) if ok else None,
                         "same_authoritative_case_as_baseline": f"{sum(1 for r in ok if r['same_as_baseline'])}/{len(ok)}" if mode != "deterministic" else "n/a",
                         "order_patterns": dict(Counter(r["order"] for r in ok).most_common(4)),
                         "models_returned": sorted({m for r in ok for m in (r.get("models_returned") or [])}), "provider_calls_mean": round(statistics.mean(r.get("provider_calls") or 0 for r in ok), 2) if ok else None}
    (OUT / "summary.json").write_text(json.dumps({"summary": summary, "cases": results}, indent=1), encoding="utf-8")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 9
    m = tuple(sys.argv[2].split(",")) if len(sys.argv) > 2 else ("deterministic", "scripted:smart", "scripted:thorough", "scripted:minimal", "scripted:erratic")
    main(n, m, int(sys.argv[3]) if len(sys.argv) > 3 else None)          # optional 3rd argument: run only the first K selected cases
