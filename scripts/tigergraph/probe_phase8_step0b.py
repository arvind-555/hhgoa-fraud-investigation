"""Phase 8 step 0b: resolves the two inconclusive results of step 0 on a fresh throwaway graph.
  (1) exact vertex counts after a large load (stat_vertex_number lag vs real loss) + largest accepted single POST
  (2) index effectiveness using SERVER-SIDE timing (timestamp() inside the query), removing REST latency
Same safety rules as step 0: throwaway graph only, dropped in `finally`, no Transaction_Fraud, no secret printing."""
import hashlib, json, random, re, statistics, sys, time, urllib.request, urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = "hhg_p8_tmp2"
env = {}
for ln in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^([A-Z_]+)=(.*)$", ln.strip())
    if m:
        env[m.group(1)] = m.group(2)
HOST, SECRET = env["TG_HOST"].rstrip("/"), env["TG_SECRET"]
TOKEN = ""


def red(x):
    x = str(x)
    for s in (SECRET, TOKEN):
        if s:
            x = x.replace(s, "<redacted>")
    return x


def http(method, path, data=None, headers=None, timeout=1800):
    h = dict(headers or {})
    if isinstance(data, dict):
        data = json.dumps(data).encode(); h["Content-Type"] = "application/json"
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


def auth():
    return {"Authorization": "Bearer " + TOKEN}


def gsql(text, timeout=1200):
    assert "transaction_fraud" not in text.lower()
    s, t = http("POST", "/gsql/v1/statements", text, {**auth(), "Content-Type": "text/plain"}, timeout)
    try:
        j = json.loads(t); return red(j.get("message", t))
    except Exception:
        return red(t)


def out(msg):
    print(msg, flush=True)


s, t = http("POST", "/gsql/v1/tokens", {"secret": SECRET})
TOKEN = json.loads(t)["token"]


def snap():
    g = gsql("SHOW GRAPH *")
    v = gsql("USE GLOBAL\nSHOW VERTEX *") + gsql("USE GLOBAL\nSHOW EDGE *")
    return re.findall(r"Graph\s+(\w+)\(", g), "\n".join(l for l in g.split("\n") if "Transaction_Fraud" in l), hashlib.sha256(v.encode()).hexdigest()[:12]


gb, tfb, glb = snap()
out(f"graphs before: {gb}")
created = False
try:
    if TMP in gb:
        out(gsql(f"DROP GRAPH {TMP} CASCADE").strip()[:60])
    out(gsql(f"CREATE GRAPH {TMP} ()").strip()); created = True
    txn = ("PRIMARY_ID txn_id UINT, epoch INT, amount DOUBLE, card_id STRING, customer_id STRING, product_cd STRING, "
           "c1 DOUBLE, d1 DOUBLE, m1 STRING, v51 DOUBLE")
    m = gsql(f"USE GRAPH {TMP}\nCREATE SCHEMA_CHANGE JOB s1 FOR GRAPH {TMP} {{ ADD VERTEX Transaction ({txn}) WITH primary_id_as_attribute=\"true\"; }}\nRUN SCHEMA_CHANGE JOB s1", 600)
    out("schema: " + ("ok" if "Successfully" in m else m[-200:]))
    m = gsql(f"USE GRAPH {TMP}\nCREATE SCHEMA_CHANGE JOB s2 FOR GRAPH {TMP} {{ ALTER VERTEX Transaction ADD INDEX idx_txn_epoch ON (epoch); }}\nRUN SCHEMA_CHANGE JOB s2", 600)
    out("index job: " + ("ok" if "Successfully" in m else m[-200:]))
    m = gsql(f"USE GRAPH {TMP}\nCREATE LOADING JOB lj FOR GRAPH {TMP} {{ DEFINE FILENAME f; LOAD f TO VERTEX Transaction VALUES ($0,$1,$2,$3,$4,$5,$6,$7,$8,$9) USING SEPARATOR=\",\", EOL=\"\\n\", HEADER=\"false\"; }}")
    out("loading job: " + ("ok" if "Successfully" in m else m[-200:]))
    rnd = random.Random(2)

    def rows(lo, hi):
        return "\n".join(f"{3000000 + i},{1000 + i * 7},{round(rnd.uniform(1, 500), 2)},C{i % 2000:05d}-K1,C{i % 2000:05d},{'W' if i % 3 else 'C'},1,0,T,1.5" for i in range(lo, hi)) + "\n"

    def load(body):
        t0 = time.perf_counter()
        s, t = http("POST", f"/restpp/ddl/{TMP}?tag=lj&filename=f&sep=%2C&eol=%0A", body, {**auth(), "Content-Type": "text/plain"}, 3600)
        v = re.search(r'"validLine":(\d+)', t)
        return s, time.perf_counter() - t0, (int(v.group(1)) if v else None), red(t[:200])

    total = 0
    for size in (200_000, 600_000):
        body = rows(total, total + size)
        s, dt, v, info = load(body)
        good = s == 200 and v == size
        out(f"load {size:,} rows in ONE POST ({len(body) / 1e6:.0f} MB): HTTP {s}, {dt:.1f}s, validLines={v}" + ("" if good else f"  !! {info}"))
        if good:
            total += size
        else:
            break
    out(f"rows accepted in total: {total:,}")

    def stat():
        s, t = http("POST", f"/restpp/builtins/{TMP}", {"function": "stat_vertex_number", "type": "*"}, auth())
        try:
            return json.loads(t)["results"][0]["count"]
        except Exception:
            return None
    seq = []
    for k in range(9):                      # does the stat converge?
        seq.append(stat()); time.sleep(10)
    out(f"stat_vertex_number over 90 s: {seq}")

    q = f"""USE GRAPH {TMP}
CREATE OR REPLACE QUERY q_count() FOR GRAPH {TMP} {{ S = {{Transaction.*}}; PRINT S.size(); }}
CREATE OR REPLACE QUERY q_t_epoch(INT v) FOR GRAPH {TMP} {{
  INT t0 = 0; INT t1 = 0;
  SumAccum<INT> @@n;
  t0 = timestamp();
  S = {{Transaction.*}};
  R = SELECT t FROM S:t WHERE t.epoch == v ACCUM @@n += 1;
  t1 = timestamp();
  PRINT @@n AS hits, (t1 - t0) AS server_ms;
}}
CREATE OR REPLACE QUERY q_t_amount(DOUBLE v) FOR GRAPH {TMP} {{
  INT t0 = 0; INT t1 = 0;
  SumAccum<INT> @@n;
  t0 = timestamp();
  S = {{Transaction.*}};
  R = SELECT t FROM S:t WHERE t.amount == v ACCUM @@n += 1;
  t1 = timestamp();
  PRINT @@n AS hits, (t1 - t0) AS server_ms;
}}
CREATE OR REPLACE QUERY q_t_range(INT lo, INT hi) FOR GRAPH {TMP} {{
  INT t0 = 0; INT t1 = 0;
  SumAccum<INT> @@n;
  t0 = timestamp();
  S = {{Transaction.*}};
  R = SELECT t FROM S:t WHERE t.epoch >= lo AND t.epoch <= hi ACCUM @@n += 1;
  t1 = timestamp();
  PRINT @@n AS hits, (t1 - t0) AS server_ms;
}}
INSTALL QUERY q_count, q_t_epoch, q_t_amount, q_t_range"""
    m = gsql(q, 1800)
    ok = "failed: 0" in m
    out("query install: " + ("ok" if ok else m[-300:]))
    if ok:
        s, t = http("GET", f"/restpp/query/{TMP}/q_count", headers=auth())
        exact = json.loads(t)["results"][0]["S.size()"]
        out(f"EXACT vertex count via installed query: {exact:,} (loaded {total:,}) -> {'MATCH' if exact == total else 'LOSS'}")
        mid = total // 2
        s, t = http("GET", f"/restpp/graph/{TMP}/vertices/Transaction/{3000000 + mid}", headers=auth())
        amt = json.loads(t)["results"][0]["attributes"]["amount"]
        ep = 1000 + mid * 7

        def srv(path, reps=7):
            ms, hits = [], None
            for _ in range(reps):
                s, t = http("GET", path, headers=auth())
                r = json.loads(t)["results"][0]
                ms.append(r["server_ms"]); hits = r["hits"]
            return statistics.median(ms), min(ms), hits
        e = srv(f"/restpp/query/{TMP}/q_t_epoch?v={ep}")
        a = srv(f"/restpp/query/{TMP}/q_t_amount?v={amt}")
        r = srv(f"/restpp/query/{TMP}/q_t_range?lo={ep}&hi={ep + 700}")
        out(f"SERVER-SIDE ms over {total:,} vertices (median, min): epoch==v INDEXED {e[0]:.0f},{e[1]}  hits={e[2]} | amount==v NO INDEX {a[0]:.0f},{a[1]}  hits={a[2]} | epoch range INDEXED {r[0]:.0f},{r[1]} hits={r[2]}")
        out("index verdict: " + ("EFFECTIVE (indexed equality >= 3x faster than scan)" if e[0] * 3 <= a[0] else "NOT effective / indistinguishable from a scan"))
finally:
    if created:
        out(gsql(f"DROP GRAPH {TMP} CASCADE").strip()[:70])
    ga, tfa, gla = snap()
    out(f"graphs after: {ga}; Transaction_Fraud identical: {tfa == tfb and bool(tfb)}; global schema identical: {gla == glb}; FraudInvestigation absent: {'FraudInvestigation' not in ga}")
