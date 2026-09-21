"""ToolGateway: the single object through which the agent (orchestrator or MCP client) reaches the investigation tools.

The RUNNER builds it with a fixed as_of; the agent side can only call whitelisted tools with id-only arguments.
The gateway (1) validates the call against permissions, (2) dispatches to InvestigationSession (Phase 9A, which already applies as_of,
hub guards and sentinels), (3) sanitises the result for the agent (raw risk_score -> coarse band, internal fields removed, benchmark-case
ids refused), (4) re-checks that nothing is later than as_of, (5) counts and times every call, and (6) can run independent calls in parallel.
"""
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from fraud_tools.guards import LeakError, assert_visible  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402

from .permissions import PermissionDenied, canonical_key, validate_call  # noqa: E402

STRIP_KEYS = {"as_of", "as_of_epoch", "max_epoch_seen", "latency_ms", "risk_score", "snap_class", "snap_customers", "snap_region_class", "n_cards", "queries_used"}
RENAME = {"seconds_before_as_of": "seconds_since_transaction", "next_txn_visible_at_as_of": "next_txn_visible_now"}   # the agent sees "now", never the term as_of
_BENCH = re.compile(r"HHG-\d")


def band(score):
    return "high" if score >= 0.7 else "medium" if score >= 0.4 else "low"


def sanitize(x):
    """Recursively remove agent-forbidden fields; replace the raw model score by a coarse band."""
    if isinstance(x, dict):
        out = {}
        for k, v in x.items():
            if k in STRIP_KEYS:
                continue
            if k == "model_score":
                out["model_alert"] = {"band": band(v["value"]) if isinstance(v, dict) else None,
                                      "note": "coarse band of the bank model score; an input, never a verdict; not usable to filter or rank"}
                continue
            out[RENAME.get(k, k)] = sanitize(v)
        return out
    if isinstance(x, list):
        return [sanitize(v) for v in x]
    if isinstance(x, str) and _BENCH.search(x):
        raise LeakError("benchmark case id in a tool result")
    return x


class ToolGateway:
    def __init__(self, session: InvestigationSession, max_workers=4):
        self._s = session                       # holds as_of; not reachable from agent-side code
        self._pool = ThreadPoolExecutor(max_workers)
        self._lock = threading.Lock()
        self.calls = []                         # logical tool calls: {tool, args, ms, state, ok}
        self._memo = {}

    # ------------------------------------------------------------------ introspection (runner side)
    @property
    def graph_queries(self):
        return len(self._s.timings)

    @property
    def cache_hits(self):
        return self._s.cache_hits

    def stats(self):
        ms = [c["ms"] for c in self.calls]
        return {"tool_calls": len(self.calls), "graph_queries_executed": self.graph_queries, "cache_hits": self.cache_hits,
                "tool_ms_total": round(sum(ms), 1), "tool_ms_max": max(ms) if ms else 0}

    # ------------------------------------------------------------------ calling
    def call(self, tool, args=None, state=None):
        args = validate_call(tool, args, state)
        key = canonical_key(tool, args)              # equivalent calls (argument order, defaults passed explicitly, list order) share one result; different queries never do
        with self._lock:
            if key in self._memo:
                return self._memo[key]              # identical logical call in the same case: no second round trip
        t0 = time.perf_counter()
        ok = False
        try:
            kw = {"fired_signals": args["signals"]} if tool == "get_policy_context" and "signals" in args else args
            raw = getattr(self._s, tool)(**kw)
            assert_visible(raw, self._s.as_of)      # belt and braces: the session already checks; the gateway checks again
            out = sanitize(raw)
            ok = True
        finally:
            with self._lock:
                self.calls.append({"tool": tool, "args": args, "ms": round((time.perf_counter() - t0) * 1000, 1), "state": getattr(state, "value", state), "ok": ok})
        with self._lock:
            self._memo[key] = out
        return out

    def has_result(self, tool, args=None):
        """True if this exact (validated) call was already made in this case (a repeat costs nothing)."""
        args = validate_call(tool, args)
        key = canonical_key(tool, args)
        with self._lock:
            return key in self._memo

    def call_many(self, calls, state=None):
        """Run independent calls concurrently; results keep input order. A tool error is returned as {'error': ...} for that call only."""
        def one(c):
            try:
                return self.call(c[0], c[1] if len(c) > 1 else {}, state)
            except (PermissionDenied, LeakError):
                raise
            except Exception as e:                  # FutureTransactionError, ToolError ...
                return {"error": type(e).__name__, "message": str(e)[:300]}
        futs = [self._pool.submit(one, c) for c in calls]
        return [f.result() for f in futs]

    def close(self):
        self._pool.shutdown(wait=False)
