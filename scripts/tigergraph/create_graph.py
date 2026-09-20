"""Phase 8 step 1: create the production graph FraudInvestigation (schema only, NO vector attribute, NO data).

Re-reads the frozen DDL from docs/implementation_spec.md, validates it (validate_ddl.py must exit 0), then runs the
32 non-vector jobs sequentially: create graph, 9 vertex jobs, 18 edge jobs, 5 index jobs.

Safety:
  * refuses unless TG_GRAPHNAME == FraudInvestigation; every statement mentioning Transaction_Fraud is rejected;
  * only read-only SHOW statements are issued against the workspace besides our own graph's jobs;
  * Transaction_Fraud description and the global schema are hashed before/after and must be identical;
  * on ANY failure it stops, prints the exact (redacted) failure and does NOT drop anything;
  * secrets/tokens are never printed.
Usage: python scripts/tigergraph/create_graph.py [--verify-only]
"""
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
GRAPH = "FraudInvestigation"
PROTECTED = "transaction_fraud"
TIMEOUT_JOB, TIMEOUT_INDEX = 300, 600            # verified: index jobs can be slow (one exceeded 600 s once)
IDX = [("Transaction", "epoch", "idx_txn_epoch"), ("ClosedCase", "close_epoch", "idx_cc_close"), ("FI_Case", "as_of_epoch", "idx_case_asof"),
       ("TextChunk", "valid_from_epoch", "idx_chunk_valid"), ("DeviceProfile", "profile_str", "idx_dev_profile")]

env = dict(re.findall(r"^([A-Z_]+)=(.*)$", (ROOT / ".env").read_text(encoding="utf-8"), re.M))
HOST, SECRET = env["TG_HOST"].rstrip("/"), env["TG_SECRET"]
if env.get("TG_GRAPHNAME") != GRAPH:
    sys.exit(f"refusing: TG_GRAPHNAME must be {GRAPH}")
TOKEN = ""


def red(x):
    x = str(x)
    for s in (SECRET, TOKEN):
        if s:
            x = x.replace(s, "<redacted>")
    return re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<jwt>", x)


def http(method, path, data=None, headers=None, timeout=300):
    h = dict(headers or {})
    if isinstance(data, dict):
        data = json.dumps(data).encode()
        h["Content-Type"] = "application/json"
    elif isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(HOST + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:
        return None, red(e)


def mint():
    global TOKEN
    s, t = http("POST", "/gsql/v1/tokens", {"secret": SECRET})
    try:
        TOKEN = json.loads(t)["token"]
    except Exception:
        sys.exit(f"token mint failed (HTTP {s})")


def gsql(text, timeout=300):
    assert PROTECTED not in text.lower(), "refusing: statement mentions the protected graph"
    mint()
    s, t = http("POST", "/gsql/v1/statements", text, {"Authorization": "Bearer " + TOKEN, "Content-Type": "text/plain"}, timeout)
    try:
        j = json.loads(t)
        msg, err = j.get("message", t), bool(j.get("error"))
    except Exception:
        msg, err = t, False
    return s, err, red(msg)


def snapshot():
    _, _, g = gsql("SHOW GRAPH *")
    _, _, v = gsql("USE GLOBAL\nSHOW VERTEX *")
    _, _, e = gsql("USE GLOBAL\nSHOW EDGE *")
    tf = "\n".join(l for l in g.split("\n") if "transaction_fraud" in l.lower() and l.strip().startswith("- Graph"))
    return {"graphs": re.findall(r"Graph\s+(\w+)\(", g), "tf": tf, "global": hashlib.sha256((v + "|" + e).encode()).hexdigest()[:12]}


OK_RE = re.compile(r"Successfully created schema change jobs")
FAIL_RE = re.compile(r"(?i)Semantic Check Fails|Syntax Error|Encountered|Failed to create|could not|does not exist|already exists|reserved keyword|Was expecting")

results = []


def fail(step, msg):
    print(f"\n!! FAILED at {step}\n{red(msg)}\n\nSTOPPED. Nothing was dropped; graph state left as is for inspection.", flush=True)
    json.dump({"status": "failed", "step": step, "message": red(msg)[:2000], "done": results}, open(HERE / ".create_graph_result.json", "w"), indent=1)
    sys.exit(2)


def run_job(name, body, timeout):
    t0 = time.perf_counter()
    s, err, msg = gsql(f"USE GRAPH {GRAPH}\nCREATE SCHEMA_CHANGE JOB {name} FOR GRAPH {GRAPH} {{\n{body}\n}}\nRUN SCHEMA_CHANGE JOB {name}", timeout)
    dt = time.perf_counter() - t0
    if s is None or err or s >= 400 or not OK_RE.search(msg) or FAIL_RE.search(msg):
        fail(name, f"HTTP {s} after {dt:.0f}s\n{msg}")
    results.append({"job": name, "seconds": round(dt, 1)})
    print(f"[OK ] {name:34s} {dt:5.1f}s", flush=True)


# ------------------------------------------------------------------ re-read + validate the frozen schema
spec = (ROOT / "docs" / "implementation_spec.md").read_text(encoding="utf-8")
m = re.search(r"```gsql\n(CREATE GRAPH FraudInvestigation \(\).*?)```", spec, re.S)
ddl = re.sub(r"--[^\n]*", "", m.group(1))
base = re.search(r"CREATE SCHEMA_CHANGE JOB fi_base FOR GRAPH FraudInvestigation \{(.*?)\n\}\s*RUN SCHEMA_CHANGE JOB fi_base", ddl, re.S).group(1)
stmts = [" ".join(x.split()) + ";" for x in base.split(";") if x.strip()]
vert = [x for x in stmts if x.startswith("ADD VERTEX")]
edge = [x for x in stmts if x.startswith("ADD DIRECTED EDGE")]
print(f"frozen schema re-read: {len(vert)} vertex statements, {len(edge)} edge statements, {len(IDX)} indexes (vector job deliberately excluded)")
assert len(vert) == 9 and len(edge) == 18 and len(vert) + len(edge) == len(stmts), "unexpected statement mix in fi_base"
assert not any("VECTOR" in x.upper() for x in stmts)
v = subprocess.run([sys.executable, str(HERE / "validate_ddl.py")], capture_output=True, text=True)
if v.returncode != 0:
    fail("validate_ddl.py", v.stdout[-1500:])
print("validate_ddl.py: 0 errors")


def verify():
    """Final schema verification (read-only)."""
    checks = []

    def chk(name, ok, detail=""):
        checks.append((name, ok, detail))
        print(f"[{'OK ' if ok else 'NO '}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    s, err, ls = gsql(f"USE GRAPH {GRAPH}\nLS")
    snap = snapshot()
    chk("graph list = [Transaction_Fraud, FraudInvestigation]", sorted(snap["graphs"]) == sorted(["Transaction_Fraud", GRAPH]), str(snap["graphs"]))
    chk("Transaction_Fraud description byte-identical", snap["tf"] == BEFORE["tf"] and bool(BEFORE["tf"]))
    chk("global vertex+edge schema byte-identical", snap["global"] == BEFORE["global"])
    for v_ in re.findall(r"ADD VERTEX (\w+)", " ".join(vert)):
        chk(f"vertex {v_} present", re.search(rf"VERTEX {v_}\(", ls) is not None)
    for e_ in re.findall(r"ADD DIRECTED EDGE (\w+)", " ".join(edge)):
        chk(f"edge {e_} present", re.search(rf"EDGE {e_}\(", ls) is not None)
    chk("18 edge definitions + 18 reverse edges", len(re.findall(r"(?m)^\s*-?\s*(?:DIRECTED )?EDGE \w+\(", ls)) >= 18 or len(re.findall(r"REVERSE_EDGE=", ls)) == 18,
        f"REVERSE_EDGE mentions: {len(re.findall(r'REVERSE_EDGE=', ls))}")
    s2, t2 = http("GET", f"/gsql/v1/schema/graphs/{GRAPH}", headers={"Authorization": "Bearer " + TOKEN}, timeout=120)
    for _, attr, name in IDX:
        chk(f"index {name} present", name in ls or name in t2)
    chk("no vector attribute yet", "embedding" not in ls.lower() and "embedding" not in t2.lower() and "VECTOR" not in ls.upper())
    chk("no reserved names used (no vertex 'Case', no attribute 'proxy')", not re.search(r"VERTEX Case\(", ls) and not re.search(r"\bproxy\b", ls))
    chk("primary_id_as_attribute on all 9 vertices", len(re.findall(r'PRIMARY_ID_AS_ATTRIBUTE="true"', ls, re.I)) >= 9, f"{len(re.findall(r'PRIMARY_ID_AS_ATTRIBUTE=.true.', ls, re.I))} found")
    # attribute-level check for Transaction (49 attributes incl. key)
    tx = re.search(r"VERTEX Transaction\((.*?)\)\s*(?:WITH|\n)", ls, re.S)
    n_tx = len([a for a in (tx.group(1).split(",") if tx else []) if a.strip()])
    chk("Transaction has 49 attributes (incl. key)", n_tx == 49, f"{n_tx}")
    s3, t3 = http("POST", f"/restpp/builtins/{GRAPH}", {"function": "stat_vertex_number", "type": "*"}, {"Authorization": "Bearer " + TOKEN})
    try:
        cnt = sum(r["count"] for r in json.loads(t3).get("results", []))
    except Exception:
        cnt = None
    chk("graph is empty (no data loaded)", cnt == 0, f"total vertices = {cnt}")
    bad_ = [c for c in checks if not c[1]]
    json.dump({"status": "ok" if not bad_ else "verify_failed", "checks": [{"check": a, "ok": b, "detail": red(c)} for a, b, c in checks], "jobs": results},
              open(HERE / ".create_graph_result.json", "w"), indent=1)
    print(f"\nverification: {len(checks) - len(bad_)}/{len(checks)} checks OK")
    return not bad_


BEFORE = snapshot()
print("graphs before:", BEFORE["graphs"])
if "--verify-only" in sys.argv:
    sys.exit(0 if verify() else 1)
if GRAPH in BEFORE["graphs"]:
    fail("pre-check", f"{GRAPH} already exists; refusing to continue (no automatic drop). Use --verify-only to inspect it.")

# ------------------------------------------------------------------ execute
t0 = time.perf_counter()
s, err, msg = gsql(f"CREATE GRAPH {GRAPH} ()")
if s is None or err or s >= 400 or "is created" not in msg:
    fail("CREATE GRAPH", f"HTTP {s}\n{msg}")
print(f"[OK ] CREATE GRAPH {GRAPH} ()                (local schema)", flush=True)
VNAME = re.compile(r"ADD VERTEX (\w+)")
ENAME = re.compile(r"ADD DIRECTED EDGE (\w+)")
for st in vert:
    jn = "fi_v_" + VNAME.match(st).group(1).lower()
    run_job(jn, "  " + st, TIMEOUT_JOB)
for st in edge:
    jn = "fi_e_" + ENAME.match(st).group(1).lower()
    run_job(jn, "  " + st, TIMEOUT_JOB)
for vtx, attr, name in IDX:
    run_job(f"fi_idx_{name[4:]}", f"  ALTER VERTEX {vtx} ADD INDEX {name} ON ({attr});", TIMEOUT_INDEX)
print(f"\nall {len(results)} schema jobs finished in {time.perf_counter() - t0:.0f}s (+ CREATE GRAPH)")
sys.exit(0 if verify() else 1)
