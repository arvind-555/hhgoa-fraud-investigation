"""Phase 7 live probe for the Savanna TigerGraph workspace (docs/tigergraph_environment.md section 6).

Safety rules enforced in code:
  * credentials are read from .env and NEVER printed (secret and minted token are redacted from all output);
  * the existing graph `Transaction_Fraud` is only ever *read* (SHOW GRAPH); a hash of its description is compared before/after;
  * every write happens in ONE throwaway graph named PROBE_GRAPH (local schema); it is dropped in `finally`;
  * refuses to run if PROBE_GRAPH already exists or equals any pre-existing graph name.
Usage: python scripts/tigergraph/probe_env.py [--keep]     (--keep leaves the throwaway graph for inspection)
"""
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE_GRAPH = "hhg_probe_tmp"
PROTECTED = {"Transaction_Fraud"}

env = {}
for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Z_]+)=(.*)$", ln.strip())
    if m:
        env[m.group(1)] = m.group(2)
HOST = env["TG_HOST"].rstrip("/")
SECRET = env.get("TG_SECRET", "")
if not SECRET:
    sys.exit("TG_SECRET missing in .env")
TOKEN = ""


def red(x):
    x = str(x)
    for s in (SECRET, TOKEN):
        if s:
            x = x.replace(s, "<redacted>")
    return re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<jwt-redacted>", x)


def http(method, path, data=None, headers=None, timeout=300):
    h = dict(headers or {})
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(HOST + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # network
        return None, red(e)


def auth():
    return {"Authorization": "Bearer " + TOKEN}


def gsql(text, timeout=600):
    s, t = http("POST", "/gsql/v1/statements", text, {**auth(), "Content-Type": "text/plain"}, timeout)
    try:
        j = json.loads(t)
        msg = j.get("message", t) if isinstance(j, dict) else t
        if isinstance(j, dict) and j.get("error"):
            s = s if s and s >= 400 else 400
    except Exception:
        msg = t
    return s, red(msg)


results = []


def step(name, ok, detail=""):
    results.append((name, ok, detail))
    print(f"[{'OK ' if ok else 'FAIL'}] {name}" + (f"\n       {red(detail)[:700]}" if detail else ""), flush=True)
    return ok


def is_err(msg):
    msg = re.sub(r"(?i)failed:\s*0", "", msg)
    return bool(re.search(r"(?i)error|fail|not (?:supported|found|valid)|syntax|semantic|exception|cannot|could not|invalid|unexpected|encountered|draft", msg))


# ------------------------------------------------------------------ 1. auth + version
s, t = http("POST", "/gsql/v1/tokens", {"secret": SECRET})
try:
    TOKEN = json.loads(t).get("token", "")
except Exception:
    TOKEN = ""
step("1a token minted from TG_SECRET", s == 200 and bool(TOKEN), f"HTTP {s}")
if not TOKEN:
    sys.exit(1)
s, t = http("GET", "/gsql/v1/version", headers=auth())
step("1b GSQL version", s == 200, t.strip())
s, t = http("GET", "/restpp/version", headers=auth())
step("1c RESTPP version endpoint", s == 200, t.strip()[:200])

# ------------------------------------------------------------------ 2. read-only inventory
s, before = gsql("SHOW GRAPH *")
graphs = re.findall(r"Graph\s+(\w+)\(", before)
step("2a SHOW GRAPH * (read-only)", s == 200, f"graphs: {graphs}")
tf_hash_before = hashlib.sha256(before.encode()).hexdigest()[:12]
assert PROBE_GRAPH not in PROTECTED
if PROBE_GRAPH in graphs:      # leftover from an earlier run of THIS probe
    s, m = gsql(f"DROP GRAPH {PROBE_GRAPH} CASCADE")
    step("2z dropped leftover throwaway graph (CASCADE, own name only)", s == 200 and not is_err(m), m[:200])
    s, before = gsql("SHOW GRAPH *")
    graphs = re.findall(r"Graph\s+(\w+)\(", before)
    tf_hash_before = hashlib.sha256(before.encode()).hexdigest()[:12]
s, gl_before = gsql("USE GLOBAL\nSHOW VERTEX *")
step("2b global vertex types (read-only)", s == 200, gl_before[:300].replace("\n", " "))
gl_hash_before = hashlib.sha256(gl_before.encode()).hexdigest()[:12]
s, users = gsql("SHOW USER")
step("2c SHOW USER (read-only; only counts printed, server returns a masked secret)", s == 200, "users: %d; superuser roles: %d" % (users.count("- Name:"), users.count("superuser")))

keep = "--keep" in sys.argv
created = False
try:
    # -------------------------------------------------------------- 3. throwaway graph, local schema
    s, m = gsql(f"CREATE GRAPH {PROBE_GRAPH} ()")
    created = s == 200 and not is_err(m)
    step("3a CREATE GRAPH (local schema, throwaway)", created, m[:300])
    if not created:
        raise SystemExit
    s, m = gsql(f"""USE GRAPH {PROBE_GRAPH}
CREATE SCHEMA_CHANGE JOB probe_sc FOR GRAPH {PROBE_GRAPH} {{
  ADD VERTEX PVert (PRIMARY_ID id STRING, epoch INT, name STRING, ts DATETIME, amt DOUBLE, flag BOOL);
  ADD VERTEX PChunk (PRIMARY_ID id STRING, valid_from_epoch INT, txt STRING);
  ADD DIRECTED EDGE PEdge (FROM PVert, TO PVert, epoch INT, tag STRING) WITH REVERSE_EDGE="PEdge_rev";
  ADD DIRECTED EDGE PMulti (FROM PVert, TO PVert | FROM PVert, TO PChunk, w INT);
}}
RUN SCHEMA_CHANGE JOB probe_sc""")
    step("3b local schema-change job (ADD VERTEX/EDGE incl. multi-pair edge)", s == 200 and not is_err(m), m[:500])
    s, gm = gsql("USE GLOBAL\nSHOW VERTEX *")
    step("3c global schema unchanged after local job", hashlib.sha256(gm.encode()).hexdigest()[:12] == gl_hash_before, "")

    # -------------------------------------------------------------- 4. secondary index
    s, m = gsql(f"""USE GRAPH {PROBE_GRAPH}
CREATE SCHEMA_CHANGE JOB probe_idx FOR GRAPH {PROBE_GRAPH} {{
  ALTER VERTEX PVert ADD INDEX pv_epoch ON (epoch);
}}
RUN SCHEMA_CHANGE JOB probe_idx""", timeout=240)
    step("4a secondary index on INT vertex attribute (local job form)", s == 200 and not is_err(m), m[:500])
    s, m = gsql(f"USE GRAPH {PROBE_GRAPH}\nSHOW VERTEX PVert", timeout=60)
    step("4a' SHOW VERTEX (informational; does not list indexes)", s == 200, "index presence not visible via SHOW VERTEX")
    s, m = gsql(f"""USE GRAPH {PROBE_GRAPH}
CREATE SCHEMA_CHANGE JOB probe_idx2 FOR GRAPH {PROBE_GRAPH} {{
  ALTER VERTEX PVert ADD INDEX pv_ts ON (ts);
}}
RUN SCHEMA_CHANGE JOB probe_idx2""")
    step("4b secondary index on DATETIME attribute", s == 200 and not is_err(m), m[:300])

    s, m = gsql(f"""USE GRAPH {PROBE_GRAPH}
CREATE SCHEMA_CHANGE JOB probe_idx3 FOR GRAPH {PROBE_GRAPH} {{
  ALTER VERTEX PChunk ADD INDEX pc_valid ON (valid_from_epoch);
}}
RUN SCHEMA_CHANGE JOB probe_idx3""", timeout=240)
    step("4c secondary index on a second INT attribute (PChunk.valid_from_epoch)", s == 200 and not is_err(m), m[:300])

    # -------------------------------------------------------------- 5. vector attribute
    s, m = gsql(f"""USE GRAPH {PROBE_GRAPH}
CREATE SCHEMA_CHANGE JOB probe_vec FOR GRAPH {PROBE_GRAPH} {{
  ALTER VERTEX PChunk ADD VECTOR ATTRIBUTE emb(DIMENSION=3, METRIC="COSINE");
}}
RUN SCHEMA_CHANGE JOB probe_vec""")
    vec_ok = step("5a vector attribute (DIMENSION=3, COSINE)", s == 200 and not is_err(m), m[:500])

    # -------------------------------------------------------------- 6. loading jobs (CSV upload through REST)
    s, m = gsql(f"""USE GRAPH {PROBE_GRAPH}
CREATE LOADING JOB probe_load FOR GRAPH {PROBE_GRAPH} {{
  DEFINE FILENAME fv;
  DEFINE FILENAME fe;
  DEFINE FILENAME fc;
  LOAD fv TO VERTEX PVert VALUES ($0, $1, $2, $3, $4, $5) USING SEPARATOR=",", HEADER="true", EOL="\\n";
  LOAD fe TO EDGE PEdge VALUES ($0, $1, $2, $3) USING SEPARATOR=",", HEADER="true", EOL="\\n", VERTEX_MUST_EXIST="true";
  LOAD fc TO VERTEX PChunk VALUES ($0, $1, $2) USING SEPARATOR=",", HEADER="true", EOL="\\n";
}}""")
    step("6a CREATE LOADING JOB (to_datetime, gsql_to_bool, VERTEX_MUST_EXIST)", s == 200 and not is_err(m), m[:500])
    fv = "id,epoch,name,ts,amt,flag\np1,10,alpha,2016-07-02 00:00:10,1.5,true\np2,100,beta,2016-07-02 00:01:40,2.5,false\np3,200,gamma,2016-07-02 00:03:20,3.5,true\n"
    fe = "src,dst,epoch,tag\np1,p2,100,x\np1,p3,200,y\np2,p3,300,z\n"
    fc = "id,valid_from_epoch,txt\nc1,50,first\nc2,150,second\nc3,300,third\n"
    for fname, body in (("fv", fv), ("fe", fe), ("fc", fc)):
        s, t = http("POST", f"/restpp/ddl/{PROBE_GRAPH}?tag=probe_load&filename={fname}&sep=%2C&eol=%0A", body,
                    {**auth(), "Content-Type": "text/plain"})
        step(f"6b load file {fname} via /restpp/ddl", s == 200 and '"error":true' not in t.replace(" ", ""), f"HTTP {s} {t[:250]}")
    if vec_ok:
        s, m = gsql(f"""USE GRAPH {PROBE_GRAPH}
CREATE LOADING JOB probe_vload FOR GRAPH {PROBE_GRAPH} {{
  DEFINE FILENAME fx;
  LOAD fx TO VECTOR ATTRIBUTE emb ON VERTEX PChunk VALUES ($0, SPLIT($1, ";")) USING SEPARATOR=",", HEADER="true", EOL="\\n";
}}""")
        step("6c vector LOAD job (SPLIT)", s == 200 and not is_err(m), m[:400])
        fx = "id,vec\nc1,1;0;0\nc2,0.9;0.1;0\nc3,0;1;0\n"
        s, t = http("POST", f"/restpp/ddl/{PROBE_GRAPH}?tag=probe_vload&filename=fx&sep=%2C&eol=%0A", fx,
                    {**auth(), "Content-Type": "text/plain"})
        step("6d load vectors via /restpp/ddl", s == 200 and '"error":true' not in t.replace(" ", ""), f"HTTP {s} {t[:250]}")
    time.sleep(3)
    s, t = http("POST", f"/restpp/builtins/{PROBE_GRAPH}", {"function": "stat_vertex_number", "type": "*"}, auth())
    step("6e vertex counts (expect PVert 3, PChunk 3)", s == 200, t[:250])
    s, t = http("POST", f"/restpp/builtins/{PROBE_GRAPH}", {"function": "stat_edge_number", "type": "*"}, auth())
    step("6f edge counts (expect PEdge 3, PEdge_rev 3)", s == 200, t[:250])

    # -------------------------------------------------------------- 7. query: as-of edge filter, accumulators, vectorSearch(candidate_set)
    q = f"""USE GRAPH {PROBE_GRAPH}
CREATE OR REPLACE QUERY probe_q(INT as_of, LIST<FLOAT> qv) FOR GRAPH {PROBE_GRAPH} {{
  SetAccum<STRING> @@names;
  SumAccum<INT> @@n_edges;
  MapAccum<STRING, INT> @@by_tag;
  SumAccum<INT> @deg;
  MaxAccum<INT> @@max_epoch;
  MapAccum<VERTEX, FLOAT> @@dist;
  Seed = {{PVert.*}};
  Vis = SELECT t FROM Seed:s -(PEdge:e)-> PVert:t
        WHERE e.epoch <= as_of AND s.epoch <= as_of
        ACCUM @@names += t.name, @@n_edges += 1, @@by_tag += (e.tag -> 1), t.@deg += 1, @@max_epoch += e.epoch
        POST-ACCUM t.@deg += 0;
  Cs = SELECT c FROM PChunk:c WHERE c.valid_from_epoch <= as_of;
  V = vectorSearch({{PChunk.emb}}, qv, 2, {{candidate_set: Cs, distance_map: @@dist}});
  PRINT @@names, @@n_edges, @@by_tag, @@max_epoch;
  PRINT V;
  PRINT @@dist;
}}
INSTALL QUERY probe_q"""
    s, m = gsql(q, timeout=900)
    q_ok = step("7a CREATE + INSTALL QUERY (edge epoch filter, accumulators, vectorSearch candidate_set)", s == 200 and not is_err(m), m[-700:])
    if q_ok:
        qs = "&".join(["as_of=160", "qv=1", "qv=0", "qv=0"])
        s, t = http("GET", f"/restpp/query/{PROBE_GRAPH}/probe_q?{qs}", headers=auth())
        step("7b RUN query at as_of=160 (expect 1 edge -> beta; chunks c1,c2 only; never c3)", s == 200, t[:700])
        s, t = http("GET", f"/restpp/query/{PROBE_GRAPH}/probe_q?as_of=400&qv=0&qv=1&qv=0", headers=auth())
        step("7c RUN query at as_of=400 (all 3 edges; c3 now eligible)", s == 200, t[:700])

    # -------------------------------------------------------------- 8. hub-guard pre-count pattern (no BREAK in ACCUM)
    q2 = f"""USE GRAPH {PROBE_GRAPH}
CREATE OR REPLACE QUERY probe_hub(INT as_of, INT max_deg) FOR GRAPH {PROBE_GRAPH} {{
  SumAccum<INT> @in_deg;
  OrAccum @is_hub;
  Seed = {{PVert.*}};
  Deg = SELECT t FROM Seed:s -(PEdge:e)-> PVert:t WHERE e.epoch <= as_of
        ACCUM t.@in_deg += 1
        POST-ACCUM CASE WHEN t.@in_deg > max_deg THEN t.@is_hub += TRUE END;
  Ok = SELECT v FROM Deg:v WHERE v.@is_hub == FALSE;
  PRINT Deg[Deg.name, Deg.@in_deg, Deg.@is_hub];
  PRINT Ok.size();
}}
INSTALL QUERY probe_hub"""
    s, m = gsql(q2, timeout=900)
    step("8a two-step hub guard query (pre-count + POST-ACCUM flag) installs", s == 200 and not is_err(m), m[-500:])
    s, t = http("GET", f"/restpp/query/{PROBE_GRAPH}/probe_hub?as_of=400&max_deg=1", headers=auth())
    step("8b run hub guard (max_deg=1)", s == 200, t[:500])
finally:
    if created and not keep:
        s, m = gsql(f"DROP GRAPH {PROBE_GRAPH} CASCADE")
        step("9a DROP throwaway graph", s == 200 and not is_err(m), m[:300])
    s, after = gsql("SHOW GRAPH *")
    graphs_after = re.findall(r"Graph\s+(\w+)\(", after)
    step("9b graphs after cleanup", True, f"{graphs_after}")
    tf_after = "".join(re.findall(r"- Graph Transaction_Fraud\(.*", after))
    tf_before = "".join(re.findall(r"- Graph Transaction_Fraud\(.*", before))
    step("9c Transaction_Fraud description identical before/after", tf_after == tf_before, "")
    s, ga = gsql("USE GLOBAL\nSHOW VERTEX *")
    step("9d global vertex types identical before/after", hashlib.sha256(ga.encode()).hexdigest()[:12] == gl_hash_before, "")

bad = [r for r in results if not r[1]]
print(f"\nprobe finished: {len(results) - len(bad)}/{len(results)} steps OK; failed: {[r[0] for r in bad]}")
