"""Local pre-flight validation of the frozen production DDL in docs/implementation_spec.md (section 2.1 / 2.5).
Pure parsing: nothing is executed against TigerGraph unless --check-workspace (a single read-only SHOW GRAPH *).
Usage: python scripts/tigergraph/validate_ddl.py [--check-workspace] [--json out.json]
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = (ROOT / "docs" / "implementation_spec.md").read_text(encoding="utf-8")
RESERVED_LIVE = {"case", "proxy"}                      # found reserved by the live scan (spec E12)
TYPES = {"STRING", "INT", "UINT", "DOUBLE", "FLOAT", "BOOL", "DATETIME"}
INDEXABLE = {"STRING", "INT", "UINT", "DATETIME"}
LABEL_NAMES = {"label", "is_fraud", "outcome", "fraud", "verdict"}
NO_LABEL_VERTICES = {"Transaction", "Card", "Customer", "DeviceProfile", "BillingRegion", "EmailDomain"}

res = {"errors": [], "warnings": [], "info": {}}
E, W = res["errors"].append, res["warnings"].append

# ---------------------------------------------------------------- extract the DDL block
m = re.search(r"```gsql\n(CREATE GRAPH FraudInvestigation \(\).*?)```", SPEC, re.S)
if not m:
    sys.exit("DDL block not found")
ddl = m.group(1)
if "Transaction_Fraud" in ddl or "(*)" in ddl or "LIST<FLOAT>" in ddl or "SYNTAX v3" in ddl or re.search(r"(?m)^\s*CREATE (VERTEX|DIRECTED EDGE)", ddl):
    E("forbidden construct in DDL block (Transaction_Fraud / (*) / LIST<FLOAT> / SYNTAX v3 / global CREATE VERTEX|EDGE)")
no_comments = re.sub(r"--[^\n]*", "", ddl)

# ---------------------------------------------------------------- vertices
vertices = {}
for name, body in re.findall(r'ADD VERTEX\s+(\w+)\s*\((.*?)\)\s*WITH primary_id_as_attribute="true";', no_comments, re.S):
    attrs = []
    for part in [p.strip() for p in body.replace("\n", " ").split(",")]:
        if not part:
            continue
        pm = re.match(r"(PRIMARY_ID\s+)?(\w+)\s+(\w+)(?:\s+DEFAULT\s+(-?\d+))?$", part)
        if not pm:
            E(f"vertex {name}: cannot parse attribute '{part}'")
            continue
        attrs.append({"pk": bool(pm.group(1)), "name": pm.group(2), "type": pm.group(3).upper(), "default": pm.group(4)})
    vertices[name] = attrs
n_add_vertex = len(re.findall(r"ADD VERTEX", no_comments))
if n_add_vertex != len(vertices):
    E(f"{n_add_vertex} ADD VERTEX statements but {len(vertices)} parsed with primary_id_as_attribute=\"true\" (every vertex must carry it)")

# ---------------------------------------------------------------- edges
edges = {}
for name, body, rev in re.findall(r'ADD DIRECTED EDGE\s+(\w+)\s*\((.*?)\)\s*WITH REVERSE_EDGE="(\w+)";', no_comments, re.S):
    body = " ".join(body.split())
    pairs = re.findall(r"FROM\s+(\w+)\s*,\s*TO\s+(\w+)", body)
    tail = re.sub(r"(\|\s*)?FROM\s+\w+\s*,\s*TO\s+\w+\s*\|?", "", body)
    attrs = []
    for part in [p.strip() for p in tail.split(",") if p.strip()]:
        pm = re.match(r"(\w+)\s+(\w+)$", part)
        if not pm:
            E(f"edge {name}: cannot parse attribute '{part}'")
        else:
            attrs.append({"name": pm.group(1), "type": pm.group(2).upper()})
    edges[name] = {"pairs": pairs, "attrs": attrs, "reverse": rev}
if len(re.findall(r"ADD DIRECTED EDGE", no_comments)) != len(edges):
    E("an ADD DIRECTED EDGE statement failed to parse (check WITH REVERSE_EDGE on every edge)")

# ---------------------------------------------------------------- name rules
seen = {}
for kind, names in (("vertex", list(vertices)), ("edge", list(edges)), ("reverse edge", [e["reverse"] for e in edges.values()])):
    for n in names:
        k = n.lower()
        if k in seen:
            E(f"name collision inside the graph: {n} ({kind}) vs {seen[k]}")
        seen[k] = f"{n} ({kind})"
        if k in RESERVED_LIVE:
            E(f"reserved word used as a type name: {n}")
for v, attrs in vertices.items():
    names = [a["name"] for a in attrs]
    if len(set(names)) != len(names):
        E(f"vertex {v}: duplicate attribute names")
    pks = [a for a in attrs if a["pk"]]
    if len(pks) != 1:
        E(f"vertex {v}: needs exactly one PRIMARY_ID (found {len(pks)})")
    for a in attrs:
        if a["type"] not in TYPES:
            E(f"vertex {v}.{a['name']}: unknown type {a['type']}")
        if a["name"].lower() in RESERVED_LIVE:
            E(f"vertex {v}: reserved attribute name {a['name']}")
        if a["default"] is not None and a["type"] not in {"INT", "UINT"}:
            W(f"vertex {v}.{a['name']}: DEFAULT on non-integer type")
        if v in NO_LABEL_VERTICES and a["name"].lower() in LABEL_NAMES:
            E(f"label-like attribute on fact vertex: {v}.{a['name']} (spec T7)")
for en, e in edges.items():
    names = [a["name"] for a in e["attrs"]]
    if len(set(names)) != len(names):
        E(f"edge {en}: duplicate attribute names")
    for a in e["attrs"]:
        if a["type"] not in TYPES:
            E(f"edge {en}.{a['name']}: unknown type {a['type']}")
        if a["name"].lower() in RESERVED_LIVE:
            E(f"edge {en}: reserved attribute name {a['name']}")
    for f, t in e["pairs"]:
        for endpoint in (f, t):
            if endpoint not in vertices:
                E(f"edge {en}: endpoint vertex '{endpoint}' is not defined (case-sensitive)")

# ---------------------------------------------------------------- temporal rules
EVIDENCE_EDGES = {"MADE": "epoch", "FROM_DEVICE": "epoch", "PURCHASER_EMAIL": "epoch", "RECIPIENT_EMAIL": "epoch", "BILLED_IN": "epoch", "SEEN_ON": "first_epoch"}
for en, key in EVIDENCE_EDGES.items():
    if en not in edges or key not in [a["name"] for a in edges[en]["attrs"]]:
        E(f"temporal rule: edge {en} must carry INT attribute {key}")
    else:
        t = [a["type"] for a in edges[en]["attrs"] if a["name"] == key][0]
        if t != "INT":
            E(f"temporal rule: {en}.{key} must be INT, found {t}")
if "gap_s" not in [a["name"] for a in edges.get("NEXT", {"attrs": []})["attrs"]]:
    E("NEXT must store gap_s (backward gap only)")
for v, key in (("Transaction", "epoch"), ("ClosedCase", "close_epoch"), ("FI_Case", "as_of_epoch"), ("TextChunk", "valid_from_epoch"),
               ("Customer", "first_epoch"), ("Card", "first_epoch"), ("DeviceProfile", "first_epoch")):
    tt = {a["name"]: a["type"] for a in vertices.get(v, [])}
    if tt.get(key) != "INT":
        E(f"temporal rule: {v}.{key} must exist as INT")
banned_live = {"n_txns_total", "last_seen", "last_epoch", "fraud_count", "total_amount", "degree"}
for v, attrs in vertices.items():
    for a in attrs:
        if a["name"] in banned_live:
            E(f"stored live aggregate {v}.{a['name']} violates T6")

# ---------------------------------------------------------------- indexes
idx_targets = re.search(r"--\s+(Transaction\.epoch,[^\n]*?)\s+\(existence", ddl)
planned_idx = []
if idx_targets:
    for tok in [t.strip() for t in idx_targets.group(1).split(",")]:
        v, a = tok.split(".")
        planned_idx.append((v, a))
else:
    E("index target list comment not found")
IDX_NAMES = {("Transaction", "epoch"): "idx_txn_epoch", ("ClosedCase", "close_epoch"): "idx_cc_close", ("FI_Case", "as_of_epoch"): "idx_case_asof",
             ("TextChunk", "valid_from_epoch"): "idx_chunk_valid", ("DeviceProfile", "profile_str"): "idx_dev_profile"}
for v, a in planned_idx:
    t = {x["name"]: x["type"] for x in vertices.get(v, [])}.get(a)
    if t is None:
        E(f"index {v}.{a}: attribute does not exist")
    elif t not in INDEXABLE:
        E(f"index {v}.{a}: type {t} not indexable")
    if (v, a) not in IDX_NAMES:
        E(f"index {v}.{a}: no agreed index name")
if len(planned_idx) != 5:
    E(f"expected 5 planned indexes, found {len(planned_idx)}")

# ---------------------------------------------------------------- vector
vec = re.search(r"ALTER VERTEX (\w+) ADD VECTOR ATTRIBUTE (\w+)\(DIMENSION=(\S+?),\s*METRIC=\"(\w+)\"\)", ddl)
vector = None
if not vec:
    E("vector attribute statement not found")
else:
    vector = {"vertex": vec.group(1), "name": vec.group(2), "dimension": vec.group(3), "metric": vec.group(4)}
    if vec.group(1) not in vertices:
        E("vector attribute vertex missing")
    if not vec.group(3).isdigit():
        W("vector DIMENSION is still the placeholder <d> (blocker B5: embedding model not chosen) -> the vector job cannot run yet")
    if vec.group(4) not in {"COSINE", "L2", "IP"}:
        E("bad vector metric")

# ---------------------------------------------------------------- source columns exist in the raw files
def header(fn):
    return set((ROOT / fn).open(encoding="utf-8").readline().strip().split(","))
try:
    tcols, icols = header("transactions.csv"), header("identity.csv")
    src = {"txn_id": "TransactionID", "epoch": "TransactionDT", "ts": "ts", "amount": "TransactionAmt", "product_cd": "ProductCD", "channel": "channel",
           "risk_score": "risk_score", "customer_id": "customer_id", "addr1": "addr1", "addr2": "addr2", "dist1": "dist1", "dist2": "dist2",
           "p_email": "P_emaildomain", "r_email": "R_emaildomain"}
    for a in [x["name"] for x in vertices.get("Transaction", [])]:
        if a in src:
            if src[a] not in tcols:
                E(f"Transaction.{a}: source column {src[a]} missing from transactions.csv")
        elif re.fullmatch(r"[cdmv]\d+", a):
            if a.upper() not in tcols:
                E(f"Transaction.{a}: source column {a.upper()} missing from transactions.csv")
        elif a in {"card_id", "has_identity"}:
            pass                                    # derived
        elif a in {"id_15", "proxy_type", "device_type"}:
            need = {"id_15": "id_15", "proxy_type": "id_23", "device_type": "DeviceType"}[a]
            if need not in icols:
                E(f"Transaction.{a}: source column {need} missing from identity.csv")
        else:
            W(f"Transaction.{a}: no source mapping known to the validator")
    for c in ("DeviceInfo", "id_30", "id_31", "id_33"):
        if c not in icols:
            E(f"DeviceProfile source column {c} missing from identity.csv")
    res["info"]["staging_note"] = ("Staging must read M1-M9, D4, dist2, DeviceType from the raw CSVs: they are NOT in scripts/analysis/common.py's cached "
                                   "TXN_COLS/ID_COLS (the analysis cache is not a sufficient source for the Transaction vertex).")
except FileNotFoundError:
    W("raw CSVs not found; source-column check skipped")

# ---------------------------------------------------------------- summary
tmp_attrs = lambda attrs: [a["name"] for a in attrs if "epoch" in a["name"] or a["type"] == "DATETIME" or a["name"] in {"gap_s"}]
res["info"].update({
    "vertices": {v: {"attributes": len(a), "primary_id": [x["name"] + ":" + x["type"] for x in a if x["pk"]][0], "temporal": tmp_attrs(a)} for v, a in vertices.items()},
    "edges": {e: {"pairs": d["pairs"], "attrs": [a["name"] for a in d["attrs"]], "reverse": d["reverse"], "temporal": tmp_attrs(d["attrs"])} for e, d in edges.items()},
    "indexes": [f"{IDX_NAMES.get((v, a))} on {v}({a})" for v, a in planned_idx],
    "vector": vector,
    "counts": {"vertex_types": len(vertices), "edge_types": len(edges), "edge_types_incl_reverse": 2 * len(edges)},
})
if "--check-workspace" in sys.argv:                  # single read-only statement
    import urllib.request, urllib.error
    env = dict(re.findall(r"^([A-Z_]+)=(.*)$", (ROOT / ".env").read_text(encoding="utf-8"), re.M))
    host, sec = env["TG_HOST"].rstrip("/"), env["TG_SECRET"]

    def call(path, data, headers):
        r = urllib.request.Request(host + path, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(r, timeout=60) as x:
            return x.read().decode()
    tok = json.loads(call("/gsql/v1/tokens", json.dumps({"secret": sec}).encode(), {"Content-Type": "application/json"}))["token"]
    raw = call("/gsql/v1/statements", b"SHOW GRAPH *", {"Authorization": "Bearer " + tok, "Content-Type": "text/plain"})
    try:
        out = json.loads(raw).get("message", raw)
    except ValueError:
        out = raw
    graphs = re.findall(r"Graph\s+(\w+)\(", out)
    res["info"]["workspace_graphs"] = graphs
    if "FraudInvestigation" in graphs:
        E("FraudInvestigation already exists in the workspace")

print(json.dumps(res["info"], indent=1))
print("\nERRORS:", len(res["errors"]))
for x in res["errors"]:
    print("  -", x)
print("WARNINGS:", len(res["warnings"]))
for x in res["warnings"]:
    print("  -", x)
if "--json" in sys.argv:
    Path(sys.argv[sys.argv.index("--json") + 1]).write_text(json.dumps(res, indent=1), encoding="utf-8")
sys.exit(1 if res["errors"] else 0)
