"""The ONLY door from the agent-side code to TigerGraph: whitelisted, parameter-checked GETs of installed fi_* queries.

* no /gsql endpoint, no schema/loader/admin call, no arbitrary GSQL, no free-form path;
* graph fixed to FraudInvestigation (Transaction_Fraud is unreachable by construction);
* the secret/token never appears in results or errors;
* every query has a fixed parameter signature: name -> type ('int' | ('vertex', TypeName)).
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .guards import ToolError

ROOT = Path(__file__).resolve().parents[2]
GRAPH = "FraudInvestigation"

SIGNATURES = {
    "fi_txn_context": {"txn": ("vertex", "Transaction"), "as_of": "int"},
    "fi_card_history": {"card": ("vertex", "Card"), "as_of": "int", "before_epoch": "int", "baseline_lookback_s": "int", "row_window_s": "int", "max_rows": "int"},
    "fi_customer_history": {"cust": ("vertex", "Customer"), "as_of": "int", "lookback_s": "int", "max_cases": "int"},
    "fi_shared_devices": {"card": ("vertex", "Card"), "as_of": "int", "days": "int", "max_customers": "int"},
    "fi_connected_entities": {"card": ("vertex", "Card"), "as_of": "int", "days": "int", "max_degree": "int"},
    "fi_prior_cases": {"card": ("vertex", "Card"), "as_of": "int", "max_cases": "int", "max_degree": "int"},
    "fi_text_chunks": {"as_of": "int", "doc_type": "str", "channel": "str", "max_chunks": "int"},
}
STR_RE = re.compile(r"^[a-z_]{0,20}$")
STR_ALLOWED = {"doc_type": {"closed_case", "policy", "pattern", "format"}, "channel": {"", "online", "in_person"}}
ID_RE = {"Transaction": re.compile(r"^\d{7}$"), "Card": re.compile(r"^C\d{5}-K\d$"), "Customer": re.compile(r"^C\d{5}$")}
INT_LIMITS = {"as_of": (0, 10 ** 9), "before_epoch": (0, 10 ** 9), "baseline_lookback_s": (0, 20_000_000), "row_window_s": (0, 20_000_000),
              "max_rows": (1, 500), "lookback_s": (0, 20_000_000), "max_cases": (1, 200), "days": (1, 60), "max_customers": (1, 200), "max_degree": (1, 200)}
INT_LIMITS["max_chunks"] = (1, 8000)


class QueryClient:
    def __init__(self):
        env = dict(re.findall(r"^([A-Z_]+)=(.*)$", (ROOT / ".env").read_text(encoding="utf-8"), re.M))
        if env.get("TG_GRAPHNAME") != GRAPH:
            raise ToolError(f"TG_GRAPHNAME must be {GRAPH}")
        self._host, self._secret = env["TG_HOST"].rstrip("/"), env["TG_SECRET"]
        self._tok, self._t0 = "", 0.0
        self.calls = 0

    # -- internals ---------------------------------------------------------------------------------
    def _red(self, x):
        x = str(x)
        for s in (self._secret, self._tok):
            if s:
                x = x.replace(s, "<redacted>")
        return re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<jwt>", x)

    def _http(self, method, path, body=None, headers=None, timeout=120):
        req = urllib.request.Request(self._host + path, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:
            raise ToolError(self._red(f"network error: {e}"))

    def _token(self):
        if not self._tok or time.time() - self._t0 > 1500:
            s, t = self._http("POST", "/gsql/v1/tokens", json.dumps({"secret": self._secret}).encode(), {"Content-Type": "application/json"})
            try:
                self._tok, self._t0 = json.loads(t)["token"], time.time()
            except Exception:
                raise ToolError(f"authentication failed (HTTP {s})")
        return self._tok

    @staticmethod
    def check(name, params):
        """Validate name + parameters against the whitelist and return the request path (no network; also used by unit tests)."""
        if name not in SIGNATURES:
            raise ToolError(f"query not allowed: {name!r}")
        sig = SIGNATURES[name]
        if set(params) != set(sig):
            raise ToolError(f"{name}: parameters must be exactly {sorted(sig)}, got {sorted(params)}")
        q = []
        for k, kind in sig.items():
            v = params[k]
            if isinstance(kind, tuple):
                vt = kind[1]
                if not isinstance(v, str) or not ID_RE[vt].match(v):
                    raise ToolError(f"{name}.{k}: not a valid {vt} id")
                q += [(k, v), (f"{k}.type", vt)]
            elif kind == "str":
                if not isinstance(v, str) or not STR_RE.match(v) or v not in STR_ALLOWED[k]:
                    raise ToolError(f"{name}.{k}: value not allowed")
                q.append((k, v))
            else:
                if isinstance(v, bool) or not isinstance(v, int):
                    raise ToolError(f"{name}.{k}: must be an integer")
                lo, hi = INT_LIMITS[k]
                if not lo <= v <= hi:
                    raise ToolError(f"{name}.{k}: {v} outside [{lo}, {hi}]")
                q.append((k, str(v)))
        return f"/restpp/query/{GRAPH}/{name}?" + urllib.parse.urlencode(q)

    # -- public --------------------------------------------------------------------------------------
    def run(self, name, **params):
        path = self.check(name, params)
        s, t = self._http("GET", path, headers={"Authorization": "Bearer " + self._token()})
        self.calls += 1
        try:
            j = json.loads(t)
        except Exception:
            raise ToolError(f"{name}: unparseable response (HTTP {s})")
        if s != 200 or j.get("error"):
            raise ToolError(self._red(f"{name}: {j.get('message', '')[:300]} (HTTP {s})"))
        return flatten(j["results"])


def flatten(results):
    """Merge the list of PRINT blocks into one dict; vertex-set blocks become lists of plain dicts (alias/prefix stripped from attribute names)."""
    out = {}
    for block in results:
        for k, v in block.items():
            if isinstance(v, list) and v and isinstance(v[0], dict) and "attributes" in v[0]:
                out[k] = [{a.split(".", 1)[1] if "." in a else a: val for a, val in row["attributes"].items()} for row in v]
            else:
                out[k] = v
    return out
