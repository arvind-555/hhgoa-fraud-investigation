"""Minimal, guarded TigerGraph REST client for the loading phase (stdlib only).
Guards: graph name is FIXED to FraudInvestigation; any statement/path that mentions Transaction_Fraud is refused;
secrets/tokens are redacted from everything returned or printed."""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GRAPH = "FraudInvestigation"
PROTECTED = "transaction_fraud"

def _load_env():
    """Process environment first (deployment), the git-ignored .env as the local fallback. Only the three TigerGraph names are taken from the environment."""
    f = ROOT / ".env"
    env = dict(re.findall(r"^([A-Z_]+)=(.*)$", f.read_text(encoding="utf-8"), re.M)) if f.exists() else {}
    for k in ("TG_HOST", "TG_SECRET", "TG_GRAPHNAME"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    if not env.get("TG_HOST") or not env.get("TG_SECRET"):
        raise RuntimeError("TigerGraph credentials are not configured (TG_HOST / TG_SECRET)")
    return env


_env = _load_env()
HOST, _SECRET = _env["TG_HOST"].rstrip("/"), _env["TG_SECRET"]
if _env.get("TG_GRAPHNAME") != GRAPH:
    raise SystemExit(f"refusing: TG_GRAPHNAME must be {GRAPH}")
_token = {"v": "", "t": 0.0}


def red(x):
    x = str(x)
    for s in (_SECRET, _token["v"]):
        if s:
            x = x.replace(s, "<redacted>")
    return re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<jwt>", x)


def _raw(method, path, data=None, headers=None, timeout=300):
    assert PROTECTED not in path.lower(), "refusing: path mentions the protected graph"
    h = dict(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, str):
        data = data.encode("utf-8")
    req = urllib.request.Request(HOST + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, red(e)


def token():
    if not _token["v"] or time.time() - _token["t"] > 1500:      # refresh well before expiry
        s, t = _raw("POST", "/gsql/v1/tokens", {"secret": _SECRET})
        try:
            _token["v"], _token["t"] = json.loads(t)["token"], time.time()
        except Exception:
            raise SystemExit(f"token mint failed (HTTP {s})")
    return _token["v"]


def req(method, path, data=None, headers=None, timeout=300):
    h = {"Authorization": "Bearer " + token(), **(headers or {})}
    s, t = _raw(method, path, data, h, timeout)
    return s, red(t)


def gsql(text, timeout=600):
    assert PROTECTED not in text.lower(), "refusing: statement mentions the protected graph"
    s, t = req("POST", "/gsql/v1/statements", text, {"Content-Type": "text/plain"}, timeout)
    try:
        j = json.loads(t)
        return s, bool(j.get("error")), j.get("message", t)
    except Exception:
        return s, False, t


def snapshot():
    """Read-only fingerprint of everything that must NOT change: Transaction_Fraud + global schema."""
    _, _, g = gsql("SHOW GRAPH *")
    _, _, v = gsql("USE GLOBAL\nSHOW VERTEX *")
    _, _, e = gsql("USE GLOBAL\nSHOW EDGE *")
    tf = "\n".join(l for l in g.split("\n") if PROTECTED in l.lower() and l.strip().startswith("- Graph"))
    return {"graphs": re.findall(r"Graph\s+(\w+)\(", g), "tf": tf, "global": hashlib.sha256((v + "|" + e).encode()).hexdigest()[:12]}


def counts():
    out = {}
    for kind, fn in (("v", "stat_vertex_number"), ("e", "stat_edge_number")):
        s, t = req("POST", f"/restpp/builtins/{GRAPH}", {"function": fn, "type": "*"})
        try:
            for r in json.loads(t)["results"]:
                out[r.get("v_type") or r.get("e_type")] = r["count"]
        except Exception:
            pass
    return out


def stable_counts(tries=30, wait=5):
    """stat_* lags after a load (spec E9): poll until two consecutive readings agree."""
    prev = None
    for _ in range(tries):
        cur = counts()
        if cur and cur == prev:
            return cur
        prev = cur
        time.sleep(wait)
    return prev or {}


def upload(job, filename, body, timeout=1800):
    """POST one chunk to an existing loading job; returns (ok, seconds, stats_dict)."""
    t0 = time.perf_counter()
    s, t = req("POST", f"/restpp/ddl/{GRAPH}?tag={job}&filename={filename}&sep=%2C&eol=%0A", body, {"Content-Type": "text/plain"}, timeout)
    dt = time.perf_counter() - t0
    try:
        j = json.loads(t)
    except Exception:
        return False, dt, {"http": s, "raw": t[:300]}
    if s != 200 or j.get("error"):
        return False, dt, {"http": s, "message": j.get("message", "")[:300]}
    st = j["results"][0]["statistics"]["parsingStatistics"]
    return True, dt, st


BAD_KEY = re.compile(r"(?i)invalid|noid|missing|reject|error|incorrect|mismatch|fail")


def stat_problems(st):
    """Any object-level failure counter > 0 (or rejected lines) is a problem."""
    probs = []
    fl = st.get("fileLevel", {})
    for k, v in fl.items():
        if BAD_KEY.search(k) and v:
            probs.append(f"{k}={v}")
    for kind in ("vertex", "edge"):
        for o in st.get("objectLevel", {}).get(kind, []):
            for k, v in o.items():
                if BAD_KEY.search(k) and isinstance(v, int) and v:
                    probs.append(f"{o.get('typeName')}.{k}={v}")
    return probs
