"""Phase 8 step 0: throwaway-graph compatibility checks (docs/implementation_spec.md section 2.0, E2/E3/E12, N1-N3).

Strict safety (enforced in code):
  * every GSQL text is rejected if it mentions the protected graph name;
  * all writes go to ONE temporary local-schema graph (TMP); it is dropped with CASCADE in `finally`;
  * `Transaction_Fraud` and the global schema are hashed before/after and must be identical;
  * secrets/tokens are never printed (redaction on every line);
  * `FraudInvestigation` is never created.
Synthetic data only (no dataset rows are uploaded).
Usage: python scripts/tigergraph/probe_phase8_step0.py
"""
import hashlib
import json
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TMP = "hhg_p8_tmp"
PROTECTED = "transaction_fraud"

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
    return re.sub(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", "<jwt>", x)


def http(method, path, data=None, headers=None, timeout=900):
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
    except Exception as e:
        return None, red(e)


def auth():
    return {"Authorization": "Bearer " + TOKEN}


def gsql(text, timeout=900):
    assert PROTECTED not in text.lower(), "refusing: statement mentions the protected graph"
    s, t = http("POST", "/gsql/v1/statements", text, {**auth(), "Content-Type": "text/plain"}, timeout)
    try:
        j = json.loads(t)
        msg, err = j.get("message", t), bool(j.get("error"))
    except Exception:
        msg, err = t, s != 200
    return s, err or (s or 0) >= 400, red(msg)


BAD = re.compile(r"(?i)error|fail|not (?:supported|found|valid)|syntax|semantic|exception|cannot|could not|invalid|unexpected|encountered|draft|already|conflict|reserved|keyword")


def bad(msg):
    return bool(BAD.search(re.sub(r"(?i)failed:\s*0", "", msg)))


R = []          # (name, ok, detail)


def rec(name, ok, detail=""):
    R.append((name, ok, detail))
    print(f"[{'OK ' if ok else 'NO '}] {name}" + (f"  -- {red(detail)[:330]}" if detail else ""), flush=True)
    return ok


def job(name, body, timeout=300):
    s, err, msg = gsql(f"USE GRAPH {TMP}\nCREATE SCHEMA_CHANGE JOB {name} FOR GRAPH {TMP} {{\n{body}\n}}\nRUN SCHEMA_CHANGE JOB {name}", timeout)
    ok = (not err) and (not bad(msg)) and "Successfully" in msg
    return ok, msg


def snapshot():
    _, _, g = gsql("SHOW GRAPH *")
    _, _, v = gsql("USE GLOBAL\nSHOW VERTEX *")
    _, _, e = gsql("USE GLOBAL\nSHOW EDGE *")
    tf = "".join(l for l in g.split("\n") if PROTECTED in l.lower()) if False else "\n".join(l for l in g.split("\n") if l.strip().startswith("- Graph ") and "transaction_fraud" in l.lower())
    return {"graphs": re.findall(r"Graph\s+(\w+)\(", g), "tf": tf, "global": hashlib.sha256((v + "|" + e).encode()).hexdigest()[:12]}


# ------------------------------------------------------------------ auth
s, t = http("POST", "/gsql/v1/tokens", {"secret": SECRET})
try:
    TOKEN = json.loads(t).get("token", "")
except Exception:
    TOKEN = ""
if not TOKEN:
    sys.exit("token mint failed")
before = snapshot()
print("graphs before:", before["graphs"])
assert "FraudInvestigation" not in before["graphs"]
if TMP in before["graphs"]:
    print("leftover temp graph from an interrupted run:", gsql(f"DROP GRAPH {TMP} CASCADE")[2].strip()[:60])
    before = snapshot()

created = False
try:
    s, err, m = gsql(f"CREATE GRAPH {TMP} ()")
    created = rec("0 create temporary local-schema graph", not err and not bad(m), m[:100])
    if not created:
        raise SystemExit

    # ============================================================ A. name collisions (one job per type)
    V = {   # name -> attribute list (spec section 2.1)
        "Customer": "PRIMARY_ID customer_id STRING, card1 INT, n_cards INT, first_epoch INT",
        "Card": "PRIMARY_ID card_id STRING, customer_id STRING, card_k INT, card4 STRING, card6 STRING, card2 DOUBLE, card3 DOUBLE, card5 DOUBLE, is_null_type BOOL, first_epoch INT",
        "Transaction": ("PRIMARY_ID txn_id UINT, epoch INT, ts DATETIME, amount DOUBLE, product_cd STRING, channel STRING, risk_score FLOAT, card_id STRING, customer_id STRING, "
                        "addr1 INT DEFAULT -1, addr2 INT DEFAULT -1, dist1 DOUBLE, dist2 DOUBLE, has_identity BOOL, id_15 STRING, proxy_type STRING, device_type STRING, p_email STRING, r_email STRING, "
                        "c1 DOUBLE, c2 DOUBLE, c4 DOUBLE, c8 DOUBLE, c10 DOUBLE, d1 DOUBLE, d2 DOUBLE, d3 DOUBLE, d4 DOUBLE, d5 DOUBLE, d10 DOUBLE, d15 DOUBLE, "
                        "m1 STRING, m2 STRING, m3 STRING, m4 STRING, m5 STRING, m6 STRING, m7 STRING, m8 STRING, m9 STRING, "
                        "v51 DOUBLE, v52 DOUBLE, v79 DOUBLE, v93 DOUBLE, v94 DOUBLE, v217 DOUBLE, v258 DOUBLE, v264 DOUBLE, v308 DOUBLE"),
        "DeviceProfile": "PRIMARY_ID device_id STRING, profile_str STRING, device_info STRING, os STRING, browser STRING, screen STRING, n_known INT, is_null_profile BOOL, first_epoch INT, snap_customers INT, snap_class STRING",
        "EmailDomain": "PRIMARY_ID domain STRING, is_anonymizer BOOL, snap_customers INT, snap_class STRING",
        "BillingRegion": "PRIMARY_ID addr1 UINT, country INT, snap_customers INT, snap_class STRING",
        "ClosedCase": "PRIMARY_ID case_id STRING, customer_id STRING, card_id STRING, opened_at DATETIME, closed_at DATETIME, open_epoch INT, close_epoch INT, outcome STRING, pattern STRING, n_txns INT, exposure_usd DOUBLE, report_filed BOOL, actions_taken STRING, first_fraud_txn_id INT DEFAULT 0, analyst_notes STRING, device_text STRING",
        "Case": "PRIMARY_ID graph_case_id STRING, source_case_id STRING, as_of_epoch INT, as_of DATETIME, status STRING, verdict STRING, fraud_probability DOUBLE, pattern STRING, pattern_description STRING, first_suspicious_txn_id INT DEFAULT 0, exposure_usd DOUBLE, summary STRING, initial_actions STRING, final_actions STRING, what_changed STRING, evidence_json STRING, evidence_requests_json STRING, sar_file BOOL, sar_json STRING, stop_reason STRING, revision INT, created_at DATETIME, updated_at DATETIME",
        "TextChunk": "PRIMARY_ID chunk_id STRING, doc_type STRING, source_ref STRING, text STRING, valid_from_epoch INT, pattern STRING, outcome STRING, channel STRING",
    }
    name = {}                # logical -> actual
    for lv, attrs in V.items():
        ok, m = job(f"j_v_{lv.lower()}", f"  ADD VERTEX {lv} ({attrs}) WITH primary_id_as_attribute=\"true\";")
        if ok:
            name[lv] = lv
            rec(f"A vertex {lv}", True)
        else:
            ok2, m2 = job(f"j_v_fi_{lv.lower()}", f"  ADD VERTEX FI_{lv} ({attrs}) WITH primary_id_as_attribute=\"true\";")
            name[lv] = f"FI_{lv}" if ok2 else None
            rec(f"A vertex {lv} (plain name FAILED: {m.strip()[-120:]!r}); FI_{lv} fallback", ok2)

    def n(x):
        return name[x]

    E = [   # logical, ddl body builder
        ("OWNS", f"(FROM {n('Customer')}, TO {n('Card')}) WITH REVERSE_EDGE=\"OWNED_BY\""),
        ("MADE", f"(FROM {n('Card')}, TO {n('Transaction')}, epoch INT, amount DOUBLE, channel STRING) WITH REVERSE_EDGE=\"MADE_BY\""),
        ("NEXT", f"(FROM {n('Transaction')}, TO {n('Transaction')}, gap_s INT) WITH REVERSE_EDGE=\"PREV\""),
        ("FROM_DEVICE", f"(FROM {n('Transaction')}, TO {n('DeviceProfile')}, epoch INT, id_15 STRING, proxy_type STRING, customer_id STRING) WITH REVERSE_EDGE=\"DEVICE_OF\""),
        ("SEEN_ON", f"(FROM {n('DeviceProfile')}, TO {n('Card')}, first_epoch INT) WITH REVERSE_EDGE=\"SEEN_BY\""),
        ("PURCHASER_EMAIL", f"(FROM {n('Transaction')}, TO {n('EmailDomain')}, epoch INT) WITH REVERSE_EDGE=\"PURCHASER_OF\""),
        ("RECIPIENT_EMAIL", f"(FROM {n('Transaction')}, TO {n('EmailDomain')}, epoch INT) WITH REVERSE_EDGE=\"RECIPIENT_OF\""),
        ("BILLED_IN", f"(FROM {n('Transaction')}, TO {n('BillingRegion')}, epoch INT) WITH REVERSE_EDGE=\"BILLED_HERE\""),
        ("CLOSED_ON_CARD", f"(FROM {n('ClosedCase')}, TO {n('Card')}) WITH REVERSE_EDGE=\"CARD_HAS_CLOSED\""),
        ("CLOSED_ON_CUSTOMER", f"(FROM {n('ClosedCase')}, TO {n('Customer')}) WITH REVERSE_EDGE=\"CUSTOMER_HAS_CLOSED\""),
        ("CLOSED_INVOLVES", f"(FROM {n('ClosedCase')}, TO {n('Transaction')}, role STRING, is_first BOOL) WITH REVERSE_EDGE=\"INVOLVED_IN_CLOSED\""),
        ("CLOSED_CONNECTED_TO", f"(FROM {n('ClosedCase')}, TO {n('Card')}) WITH REVERSE_EDGE=\"CONNECTED_FROM_CLOSED\""),
        ("CASE_ON_CARD", f"(FROM {n('Case')}, TO {n('Card')}) WITH REVERSE_EDGE=\"CARD_HAS_CASE\""),
        ("CASE_TXN", f"(FROM {n('Case')}, TO {n('Transaction')}, role STRING) WITH REVERSE_EDGE=\"TXN_IN_CASE\""),
        ("CASE_CONNECTED_TO", f"(FROM {n('Case')}, TO {n('Card')}, via STRING) WITH REVERSE_EDGE=\"CONNECTED_FROM_CASE\""),
        ("CASE_CITES_DEVICE", f"(FROM {n('Case')}, TO {n('DeviceProfile')}) WITH REVERSE_EDGE=\"DEVICE_CITED_BY\""),
        # B. multi-pair + reverse edge
        ("SIMILAR_CASE", f"(FROM {n('Case')}, TO {n('ClosedCase')} | FROM {n('Case')}, TO {n('Case')}, score DOUBLE, method STRING, reason STRING) WITH REVERSE_EDGE=\"SIMILAR_FROM\""),
        ("DESCRIBES", f"(FROM {n('TextChunk')}, TO {n('ClosedCase')} | FROM {n('TextChunk')}, TO {n('Case')}) WITH REVERSE_EDGE=\"DESCRIBED_BY\""),
    ]
    ename = {}
    for le, body in E:
        ok, m = job(f"j_e_{le.lower()}", f"  ADD DIRECTED EDGE {le} {body};")
        if ok:
            ename[le] = le
            rec(f"A/B edge {le}", True, "multi-pair + reverse edge" if le in ("SIMILAR_CASE", "DESCRIBES") else "")
        else:
            ok2, m2 = job(f"j_e_fi_{le.lower()}", f"  ADD DIRECTED EDGE FI_{le} {body};")
            ename[le] = f"FI_{le}" if ok2 else None
            rec(f"A/B edge {le} (plain FAILED: {m.strip()[-150:]!r}); FI_{le} fallback", ok2)
    # multi-pair WITHOUT reverse edge, in case reverse on multi-pair failed
    if ename.get("SIMILAR_CASE", "").startswith("FI_") or ename.get("SIMILAR_CASE") is None:
        ok, m = job("j_multi_norev", f"  ADD DIRECTED EDGE MP_TEST (FROM {n('Case')}, TO {n('ClosedCase')} | FROM {n('Case')}, TO {n('Case')}, score DOUBLE);")
        rec("B multi-pair edge WITHOUT reverse edge", ok, m.strip()[-150:])
    s, err, m = gsql(f"USE GRAPH {TMP}\nSHOW EDGE SIMILAR_CASE")
    rec("B multi-pair edge definition readable", not err and "SIMILAR_CASE" in m, m.strip()[:220].replace("\n", " "))

    # ============================================================ C. primary-key attribute readability
    ok, m = job("j_pk", "  ADD VERTEX PK1 (PRIMARY_ID id STRING, x INT);\n  ADD VERTEX PK2 (PRIMARY_ID id STRING, x INT) WITH primary_id_as_attribute=\"true\";")
    rec("C ADD VERTEX ... WITH primary_id_as_attribute=\"true\" accepted", ok, m.strip()[-160:] if not ok else "")
    if ok:
        http("POST", f"/restpp/graph/{TMP}", {"vertices": {"PK1": {"a": {"x": {"value": 1}}}, "PK2": {"a": {"x": {"value": 1}}}}}, auth())
        for label, vt in (("without option", "PK1"), ("with option", "PK2")):
            q = f"USE GRAPH {TMP}\nINTERPRET QUERY () FOR GRAPH {TMP} {{\n S = {{{vt}.*}};\n R = SELECT s FROM S:s WHERE s.id == \"a\";\n PRINT R.size();\n}}"
            s, err, m = gsql(q, 120)
            rec(f"C read PRIMARY_ID as attribute `s.id` in a query, vertex {vt} ({label})", "R.size()" in m and "Type Check Error" not in m, re.sub(r"\s+", " ", m)[:160])
        # the spec's real key names
        q = f"USE GRAPH {TMP}\nINTERPRET QUERY () FOR GRAPH {TMP} {{\n S = {{{n('Customer')}.*}};\n R = SELECT s FROM S:s WHERE s.customer_id == \"C1\";\n PRINT R.size();\n}}"
        s, err, m = gsql(q, 120)
        rec("C read `customer_id` on the real Customer vertex (created WITH primary_id_as_attribute)", "R.size()" in m and "Type Check Error" not in m, re.sub(r"\s+", " ", m)[:160])

    # ============================================================ D/E. indexes + load performance
    ok, m = job("j_idx_epoch", f"  ALTER VERTEX {n('Transaction')} ADD INDEX idx_txn_epoch ON (epoch);", timeout=600)
    rec("D index Transaction.epoch (INT) created (job)", ok, m.strip()[-120:] if not ok else "")
    for tt in ("j_idx_cc", "j_idx_case", "j_idx_chunk", "j_idx_dev"):
        spec = {"j_idx_cc": (n("ClosedCase"), "idx_cc_close", "close_epoch"), "j_idx_case": (n("Case"), "idx_case_asof", "as_of_epoch"),
                "j_idx_chunk": (n("TextChunk"), "idx_chunk_valid", "valid_from_epoch"), "j_idx_dev": (n("DeviceProfile"), "idx_dev_profile", "profile_str")}[tt]
        ok, m = job(tt, f"  ALTER VERTEX {spec[0]} ADD INDEX {spec[1]} ON ({spec[2]});", timeout=600)
        rec(f"D index {spec[0]}.{spec[2]}", ok, m.strip()[-120:] if not ok else "")
    # does any metadata endpoint expose indexes?
    for path in (f"/gsql/v1/schema/graphs/{TMP}", f"/gsql/v1/schema/graphs/{TMP}?json=true"):
        s, t = http("GET", path, headers=auth(), timeout=60)
        rec(f"D metadata endpoint {path.split('/gsql/v1')[1]} mentions indexes", s == 200 and "idx_txn_epoch" in t, f"HTTP {s}, {len(t)} bytes; idx_cc_close present: {'idx_cc_close' in t}")
    s, err, m = gsql(f"USE GRAPH {TMP}\nLS")
    rec("D `LS` lists indexes", "idx_txn_epoch" in m, f"{len(m)} chars; idx_cc_close present: {'idx_cc_close' in m}")

    # ---- loading jobs
    TCOLS = ["txn_id", "epoch", "ts", "amount", "product_cd", "channel", "risk_score", "card_id", "customer_id", "addr1", "addr2", "dist1", "dist2", "has_identity",
             "id_15", "proxy_type", "device_type", "p_email", "r_email", "c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15",
             "m1", "m2", "m3", "m4", "m5", "m6", "m7", "m8", "m9", "v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308"]
    tv = ", ".join(f"${i}" for i in range(len(TCOLS)))
    load_def = f"""USE GRAPH {TMP}
CREATE LOADING JOB lj FOR GRAPH {TMP} {{
  DEFINE FILENAME ftxn; DEFINE FILENAME fcard; DEFINE FILENAME fmade; DEFINE FILENAME fdev; DEFINE FILENAME ffd;
  LOAD ftxn TO VERTEX {n('Transaction')} VALUES ({tv}) USING SEPARATOR=",", EOL="\\n", HEADER="false";
  LOAD fcard TO VERTEX {n('Card')} VALUES ($0, $1, $2, $3, $4, $5, $6, $7, $8, $9) USING SEPARATOR=",", EOL="\\n", HEADER="false";
  LOAD fmade TO EDGE {ename['MADE']} VALUES ($0, $1, $2, $3, $4) USING SEPARATOR=",", EOL="\\n", HEADER="false", VERTEX_MUST_EXIST="true";
  LOAD fdev TO VERTEX {n('DeviceProfile')} VALUES ($0, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10) USING SEPARATOR=",", EOL="\\n", HEADER="false";
  LOAD ffd TO EDGE {ename['FROM_DEVICE']} VALUES ($0, $1, $2, $3, $4, $5) USING SEPARATOR=",", EOL="\\n", HEADER="false", VERTEX_MUST_EXIST="true";
}}"""
    s, err, m = gsql(load_def)
    rec("E loading job for the real 49-column Transaction + Card/MADE/DeviceProfile/FROM_DEVICE", not err and not bad(m), m.strip()[-160:])

    rnd = random.Random(1)
    NCARD = 2000

    def cards_csv():
        return "\n".join(f"C{c:05d}-K1,C{c:05d},1,visa,debit,111,150,226,false,{c * 10}" for c in range(NCARD)) + "\n"

    def txn_rows(lo, hi):
        out = []
        for i in range(lo, hi):
            c = i % NCARD
            ep = 1000 + i * 7
            out.append(",".join([str(3000000 + i), str(ep), "2016-07-02 00:00:00", f"{rnd.uniform(1, 500):.2f}", "W" if i % 3 else "C", "in_person" if i % 3 else "online",
                                 f"{rnd.random():.2f}", f"C{c:05d}-K1", f"C{c:05d}", str(200 + i % 50), "87", "", "", "false", "", "", "", "gmail.com", "",
                                 "1", "1", "0", "1", "1", "", "", "", "", "", "", "", "T", "F", "T", "M0", "", "F", "", "", "",
                                 "1.0", "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0", "9.0"]))
        return "\n".join(out) + "\n"

    def load(filename, body):
        t0 = time.perf_counter()
        s, t = http("POST", f"/restpp/ddl/{TMP}?tag=lj&filename={filename}&sep=%2C&eol=%0A", body, {**auth(), "Content-Type": "text/plain"}, 1800)
        dt = time.perf_counter() - t0
        okk = s == 200 and '"error":true' not in t.replace(" ", "")
        valid = re.search(r'"validLine":(\d+)', t)
        return okk, dt, (int(valid.group(1)) if valid else None), t[:160]

    okk, dt, v, _ = load("fcard", cards_csv())
    rec("E load 2,000 cards", okk, f"{dt:.1f}s valid={v}")
    total = 0
    perf = []
    for size in (10_000, 50_000, 100_000, 200_000):
        body = txn_rows(total, total + size)
        mb = len(body) / 1e6
        okk, dt, v, info = load("ftxn", body)
        perf.append((size, mb, dt, okk))
        rec(f"E load {size:,} Transaction rows ({mb:.1f} MB) in one POST", okk and v == size, f"{dt:.1f}s, {size / dt:,.0f} rows/s, validLines={v}" + ("" if okk else " " + red(info)))
        if okk:
            total += size
        else:
            break
    print(f"   transactions loaded so far: {total:,}")
    # edges MADE for the first `total` txns
    mb_rows = "\n".join(f"C{i % NCARD:05d}-K1,{3000000 + i},{1000 + i * 7},{rnd.uniform(1, 500):.2f},online" for i in range(total)) + "\n"
    okk, dt, v, info = load("fmade", mb_rows)
    rec(f"E load {total:,} MADE edges ({len(mb_rows) / 1e6:.1f} MB)", okk and v == total, f"{dt:.1f}s, {total / dt:,.0f} edges/s")
    s, t = http("POST", f"/restpp/builtins/{TMP}", {"function": "stat_vertex_number", "type": "*"}, auth())
    counts = {r["v_type"]: r["count"] for r in json.loads(t).get("results", [])} if s == 200 else {}
    rec("E stat_vertex_number", counts.get(n("Transaction")) == total, str(counts))
    if perf and perf[-1][3]:
        rate = perf[-1][0] / perf[-1][2]
        print(f"   extrapolation: 590,742 Transaction rows at {rate:,.0f} rows/s = {590742 / rate / 60:.1f} min (vertices only)")

    # ---- index effectiveness (indirect verification)
    q_eq = f"""USE GRAPH {TMP}
CREATE OR REPLACE QUERY q_eq_epoch(INT v) FOR GRAPH {TMP} {{ S = {{{n('Transaction')}.*}}; R = SELECT t FROM S:t WHERE t.epoch == v; PRINT R.size(); }}
CREATE OR REPLACE QUERY q_eq_amount(DOUBLE v) FOR GRAPH {TMP} {{ S = {{{n('Transaction')}.*}}; R = SELECT t FROM S:t WHERE t.amount == v; PRINT R.size(); }}
CREATE OR REPLACE QUERY q_rng_epoch(INT lo, INT hi) FOR GRAPH {TMP} {{ S = {{{n('Transaction')}.*}}; R = SELECT t FROM S:t WHERE t.epoch >= lo AND t.epoch <= hi; PRINT R.size(); }}
INSTALL QUERY q_eq_epoch, q_eq_amount, q_rng_epoch"""
    s, err, m = gsql(q_eq, 1200)
    inst = (not err) and "failed: 0" in m
    rec("D install index-probe queries", inst, m.strip()[-140:].replace("\n", " "))
    if inst and total:
        target_ep = 1000 + (total // 2) * 7
        amt = rnd.uniform(1, 500)
        # an amount value that exists: read one vertex back
        s, t = http("GET", f"/restpp/graph/{TMP}/vertices/{n('Transaction')}/{3000000 + total // 2}", headers=auth())
        try:
            amt = json.loads(t)["results"][0]["attributes"]["amount"]
        except Exception:
            pass

        def timed(path, reps=7):
            ts = []
            last = None
            for _ in range(reps):
                t0 = time.perf_counter()
                s, t = http("GET", path, headers=auth(), timeout=300)
                ts.append(time.perf_counter() - t0)
                last = t
            return statistics.median(ts), s, last
        te, s1, o1 = timed(f"/restpp/query/{TMP}/q_eq_epoch?v={target_ep}")
        ta, s2, o2 = timed(f"/restpp/query/{TMP}/q_eq_amount?v={amt}")
        tr, s3, o3 = timed(f"/restpp/query/{TMP}/q_rng_epoch?lo={target_ep}&hi={target_ep + 700}")
        rec("D equality on INDEXED epoch vs NON-indexed amount (median of 7)", s1 == 200 and s2 == 200,
            f"epoch(indexed) {te * 1000:.0f} ms vs amount(scan) {ta * 1000:.0f} ms over {total:,} vertices; range(indexed) {tr * 1000:.0f} ms; index appears "
            f"{'EFFECTIVE' if te < 0.6 * ta else 'NOT distinguishable from a scan'}")

    # ============================================================ F. hub pre-count performance
    devs = [("D_hub_null", 3648, 1001), ("D_hub_20k", 20000, 1500), ("D_hub_100k", min(100000, total), 2000), ("D_ring", 80, 44)]
    dev_rows = "\n".join(f"{d},profile {d},,,,,{4 if d == 'D_ring' else 0},{'false' if d == 'D_ring' else 'true'},0,0,LOW" for d, _, _ in devs) + "\n"
    okk, dt, v, info = load("fdev", dev_rows)
    rec("F load 4 hub-test DeviceProfile vertices", okk and v == len(devs), info if not okk else "")
    for d, ne, nc in devs:
        ne = min(ne, total)
        body = "\n".join(f"{3000000 + i},{d},{1000 + i * 7},{'New' if i % 2 else 'Found'},{'IP_PROXY:ANONYMOUS' if i % 5 == 0 else ''},C{i % nc:05d}" for i in range(ne)) + "\n"
        okk, dt, v, info = load("ffd", body)
        rec(f"F load {ne:,} FROM_DEVICE edges for {d} ({nc} customers)", okk and v == ne, f"{dt:.1f}s")
    q_hub = f"""USE GRAPH {TMP}
CREATE OR REPLACE QUERY q_hub(VERTEX<{n('DeviceProfile')}> dv, INT as_of, INT max_c) FOR GRAPH {TMP} {{
  SetAccum<STRING> @@custs;
  SumAccum<INT> @@n_edges;
  SumAccum<INT> @@n_new;
  SumAccum<INT> @@n_proxy;
  S = {{dv}};
  T = SELECT t FROM S:s -({ename['FROM_DEVICE'] and 'DEVICE_OF'}:e)-> {n('Transaction')}:t
      WHERE e.epoch <= as_of
      ACCUM @@custs += e.customer_id, @@n_edges += 1,
            CASE WHEN e.id_15 == "New" THEN @@n_new += 1 END,
            CASE WHEN e.proxy_type != "" THEN @@n_proxy += 1 END;
  PRINT @@custs.size() AS customers, @@n_edges AS txns, @@n_new AS new_txns, @@n_proxy AS proxy_txns, (@@custs.size() > max_c) AS is_hub;
}}
INSTALL QUERY q_hub"""
    s, err, m = gsql(q_hub, 1200)
    inst = (not err) and "failed: 0" in m
    rec("F install two-step pre-count query (device -> DEVICE_OF edges, as-of filter)", inst, m.strip()[-160:].replace("\n", " "))
    if inst:
        for d, ne, nc in devs:
            ts = []
            out = ""
            for _ in range(5):
                t0 = time.perf_counter()
                s, t = http("GET", f"/restpp/query/{TMP}/q_hub?dv={d}&dv.type={n('DeviceProfile')}&as_of=99999999&max_c=100", headers=auth(), timeout=300)
                ts.append(time.perf_counter() - t0)
                out = t
            try:
                r = json.loads(out)["results"][0]
                res = f"customers={r['customers']} txns={r['txns']} is_hub={r['is_hub']}"
            except Exception:
                res = red(out)[:120]
            rec(f"F pre-count on {d} ({min(ne, total):,} edges)", s == 200, f"median {statistics.median(ts) * 1000:.0f} ms, max {max(ts) * 1000:.0f} ms; {res}")
        # as-of cut on the ring-like device
        s, t = http("GET", f"/restpp/query/{TMP}/q_hub?dv=D_ring&dv.type={n('DeviceProfile')}&as_of={1000 + 40 * 7}&max_c=100", headers=auth())
        try:
            r = json.loads(t)["results"][0]
            rec("F as-of filter on D_ring (first 41 edges only)", r["txns"] == 41, f"txns={r['txns']} customers={r['customers']}")
        except Exception:
            rec("F as-of filter on D_ring", False, red(t)[:120])

finally:
    if created:
        s, err, m = gsql(f"DROP GRAPH {TMP} CASCADE")
        rec("Z drop temporary graph (CASCADE)", not err and "dropped" in m.lower(), m.strip()[:80])
    after = snapshot()
    rec("Z graphs after cleanup", after["graphs"] == before["graphs"], str(after["graphs"]))
    rec("Z Transaction_Fraud description byte-identical", after["tf"] == before["tf"] and bool(before["tf"]))
    rec("Z global vertex+edge schema byte-identical", after["global"] == before["global"])
    rec("Z FraudInvestigation not created", "FraudInvestigation" not in after["graphs"])
    ok = sum(1 for r in R if r[1])
    print(f"\nphase 8 step 0: {ok}/{len(R)} checks OK")
    json.dump([{"check": a, "ok": b, "detail": red(c)} for a, b, c in R], open(ROOT / "scripts/tigergraph/.step0_results.json", "w"), indent=1)
