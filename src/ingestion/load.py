"""Load staged CSVs into FraudInvestigation (and ONLY FraudInvestigation) and verify the result.

Usage:
  python src/ingestion/load.py --stage data/staged/validation --phase validation     # small batch: requires an EMPTY graph
  python src/ingestion/load.py --stage data/staged/full --phase full                 # full load (upserts; validation rows are simply overwritten)
  python src/ingestion/load.py --stage data/staged/validation --verify-only          # re-run the read-back checks

Safety: graph fixed to FraudInvestigation; Transaction_Fraud + global schema fingerprinted before/after and must be identical;
every chunk's parse statistics must show zero rejected/invalid objects; stops at the first problem (nothing is dropped).
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.parse
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
import tg  # noqa: E402
from common import T0  # noqa: E402
from stage import LAYOUT, TXN  # noqa: E402

GRAPH, JOB = tg.GRAPH, "fi_load"
# (file, kind, type, ends) in the spec's load order
ORDER = [("customer", "V", "Customer"), ("card", "V", "Card"), ("region", "V", "BillingRegion"), ("email", "V", "EmailDomain"), ("device", "V", "DeviceProfile"),
         ("transaction", "V", "Transaction"),
         ("owns", "E", "OWNS"), ("made", "E", "MADE"), ("next", "E", "NEXT"), ("from_device", "E", "FROM_DEVICE"), ("seen_on", "E", "SEEN_ON"),
         ("purchaser_email", "E", "PURCHASER_EMAIL"), ("recipient_email", "E", "RECIPIENT_EMAIL"), ("billed_in", "E", "BILLED_IN"),
         ("closed_case", "V", "ClosedCase"), ("closed_on_card", "E", "CLOSED_ON_CARD"), ("closed_on_customer", "E", "CLOSED_ON_CUSTOMER"),
         ("closed_involves", "E", "CLOSED_INVOLVES"), ("closed_connected_to", "E", "CLOSED_CONNECTED_TO")]
CHUNK = 200_000
USING = 'USING SEPARATOR=",", EOL="\\n", QUOTE="double", HEADER="false"'

ap = argparse.ArgumentParser()
ap.add_argument("--stage", required=True)
ap.add_argument("--phase", choices=["validation", "full"])
ap.add_argument("--verify-only", action="store_true")
ap.add_argument("--chunk", type=int, default=CHUNK)
args = ap.parse_args()
STAGE = Path(args.stage)
manifest = json.loads((STAGE / "manifest.json").read_text(encoding="utf-8"))
RES = {"loads": [], "checks": []}


def stop(msg):
    print(f"\n!! STOPPED: {msg}\nNothing was dropped or rolled back; graph left as is for inspection.", flush=True)
    json.dump(RES | {"status": "stopped", "reason": msg}, open(STAGE / "load_result.json", "w"), indent=1, default=str)
    sys.exit(2)


def chk(name, ok, detail=""):
    RES["checks"].append({"check": name, "ok": bool(ok), "detail": str(detail)})
    print(f"[{'OK ' if ok else 'NO '}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    return bool(ok)


def job_ddl():
    lines = [f"USE GRAPH {GRAPH}", f"CREATE LOADING JOB {JOB} FOR GRAPH {GRAPH} {{"] + [f"  DEFINE FILENAME f_{f};" for f, _, _ in ORDER]
    for f, kind, typ in ORDER:
        vals = ", ".join(f"${i}" for i in range(len(LAYOUT[f])))
        if kind == "V":
            lines.append(f"  LOAD f_{f} TO VERTEX {typ} VALUES ({vals}) {USING};")
        else:
            lines.append(f'  LOAD f_{f} TO EDGE {typ} VALUES ({vals}) {USING}, VERTEX_MUST_EXIST="true";')
    lines.append("}")
    return "\n".join(lines)


def chunks(path, size):
    with open(path, "r", encoding="utf-8", newline="") as fh:
        buf, n = [], 0
        for line in fh:
            buf.append(line)
            n += 1
            if n == size:
                yield "".join(buf), n
                buf, n = [], 0
        if buf:
            yield "".join(buf), n


def load_all():
    s, e, m = tg.gsql(f"USE GRAPH {GRAPH}\nDROP JOB {JOB}")            # our own job only; ignore if absent
    s, e, m = tg.gsql(job_ddl())
    if e or "Successfully created loading jobs" not in m:
        stop(f"CREATE LOADING JOB failed: {tg.red(m)[:400]}")
    print(f"[OK ] loading job {JOB} created ({len(ORDER)} files)", flush=True)
    for f, kind, typ in ORDER:
        total, t_file = 0, 0.0
        for body, n in chunks(STAGE / f"{f}.csv", args.chunk):
            ok, dt, st = tg.upload(JOB, f"f_{f}", body)
            if not ok:
                stop(f"upload of {f} failed: {st}")
            probs = tg.stat_problems(st)
            valid = st.get("fileLevel", {}).get("validLine")
            objs = [o for k in ("vertex", "edge") for o in st.get("objectLevel", {}).get(k, [])]
            valid_obj = sum(o.get("validObject", 0) for o in objs if o.get("typeName") == typ)
            if valid != n or probs or valid_obj != n:
                stop(f"{f}: sent {n} lines, validLine={valid}, validObject({typ})={valid_obj}, problems={probs}")
            total += n
            t_file += dt
            RES["loads"].append({"file": f, "rows": n, "seconds": round(dt, 2)})
        print(f"[OK ] load {f:22s} {total:>9,} rows -> {typ:20s} {t_file:6.1f}s   (0 rejected, validObject == rows)", flush=True)


VMAP = {"Customer": "customer", "Card": "card", "Transaction": "transaction", "DeviceProfile": "device", "EmailDomain": "email", "BillingRegion": "region", "ClosedCase": "closed_case"}
EMAP = {"OWNS": "owns", "MADE": "made", "NEXT": "next", "FROM_DEVICE": "from_device", "SEEN_ON": "seen_on", "PURCHASER_EMAIL": "purchaser_email",
        "RECIPIENT_EMAIL": "recipient_email", "BILLED_IN": "billed_in", "CLOSED_ON_CARD": "closed_on_card", "CLOSED_ON_CUSTOMER": "closed_on_customer",
        "CLOSED_INVOLVES": "closed_involves", "CLOSED_CONNECTED_TO": "closed_connected_to"}


def get_vertex(vtype, vid):
    s, t = tg.req("GET", f"/restpp/graph/{GRAPH}/vertices/{vtype}/{urllib.parse.quote(str(vid), safe='')}")
    j = json.loads(t)
    return j["results"][0]["attributes"] if j.get("results") else None


def edge_list(stype, sid, etype):
    s, t = tg.req("GET", f"/restpp/graph/{GRAPH}/edges/{stype}/{urllib.parse.quote(str(sid), safe='')}/{etype}")
    j = json.loads(t)
    return j.get("results", [])


def interpret(body):
    s, e, m = tg.gsql(f"USE GRAPH {GRAPH}\nINTERPRET QUERY () FOR GRAPH {GRAPH} {{\n{body}\n}}")
    try:
        return json.loads(m[m.index("{"):])["results"]
    except Exception:
        return {"error": tg.red(m)[:300]}


def same(a, b, kind):
    if kind == "f":
        return math.isclose(float(a), float(b), rel_tol=1e-6, abs_tol=1e-5)
    return str(a) == str(b)


def verify():
    F = {n: pd.read_csv(STAGE / f"{n}.csv", header=None, names=cols, dtype=str, keep_default_na=False, na_values=[]) for n, cols in LAYOUT.items()}
    tx = F["transaction"]
    exp = manifest["rows"]
    # ---- counts (stat_* lags, polled until stable)
    c = tg.stable_counts()
    full_now = args.phase == "full" or STAGE.name == "full"
    bad = {t: (c.get(t), exp[f]) for t, f in {**VMAP, **EMAP}.items() if c.get(t) != exp[f]}
    chk("vertex/edge counts equal the staged manifest (forward types, polled until stable)", not bad, bad if bad else {t: c.get(t) for t in list(VMAP) + list(EMAP)})
    chk("no vertices of other types (TextChunk empty, no FI_Case yet)", c.get("TextChunk", 0) == 0 and c.get("FI_Case", 0) == 0, {k: c.get(k) for k in ("TextChunk", "FI_Case")})
    # ---- attribute-level read-back of random transactions
    samp = tx.sample(min(60, len(tx)), random_state=11)
    fl = {"amount", "risk_score", "dist1", "dist2", "c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15", "v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308"}
    ints = {"txn_id", "epoch", "addr1", "addr2"}
    mism = []
    for _, r in samp.iterrows():
        a = get_vertex("Transaction", r["txn_id"])
        if a is None:
            mism.append((r["txn_id"], "missing vertex")); continue
        for col in TXN:
            exp_v = r[col]
            got = a.get(col)
            if col == "has_identity":
                got = str(got).lower()
            elif col in fl:
                ok = same(got, exp_v, "f")
            elif col in ints:
                ok = int(got) == int(exp_v)
            else:
                ok = str(got) == exp_v
            if col not in fl and col not in ints:
                ok = str(got) == exp_v
            if not ok:
                mism.append((r["txn_id"], col, exp_v, got))
    chk(f"{len(samp)} random transactions read back: all 49 attributes equal the staged values (M1-M9, D4, dist2, DeviceType, sentinels)", not mism, mism[:4])
    # missing-value sentinels really stored
    miss = tx[pd.to_numeric(tx["dist1"]) == manifest["sentinels"]["dist1"]["sentinel"]].head(3)
    got = [get_vertex("Transaction", i)["dist1"] for i in miss["txn_id"]]
    chk("missing dist1 stored as the sentinel -1 (not 0)", all(float(g) == -1.0 for g in got), got)
    # ---- adjacency: MADE / NEXT per card
    cards = F["card"].sample(min(12, len(F["card"])), random_state=3)["card_id"].tolist()
    if "C13487-K1" in set(F["card"]["card_id"]):
        cards.append("C13487-K1")
    bad_adj = []
    for cid in cards:
        n_tx = int((tx["card_id"] == cid).sum())
        made = len(edge_list("Card", cid, "MADE"))
        nxt_total = int(F["next"]["txn_from"].isin(set(tx.loc[tx["card_id"] == cid, "txn_id"])).sum())
        if made != n_tx:
            bad_adj.append((cid, "MADE", made, n_tx))
        first = (tx[tx["card_id"] == cid].assign(_e=lambda d: d["epoch"].astype("int64"), _t=lambda d: d["txn_id"].astype("int64")).sort_values(["_e", "_t"]).iloc[0]["txn_id"]) if n_tx else None
        if first is not None:
            nx = len(edge_list("Transaction", first, "NEXT"))
            if n_tx > 1 and nx != 1:
                bad_adj.append((cid, "NEXT from first txn", nx, 1))
    chk(f"MADE degree == staged txns per card, NEXT chain starts correctly ({len(cards)} cards)", not bad_adj, bad_adj[:3])
    # ---- device edges (reverse edge DEVICE_OF) incl. the ring profile when present
    fd = F["from_device"]
    dev_ids = fd["device_id"].value_counts()
    probe_dev = ["D_2c2006f554"] if "D_2c2006f554" in dev_ids.index else []
    probe_dev += dev_ids.index[[0, len(dev_ids) // 2]].tolist()
    bad_dev = []
    for d in probe_dev:
        n = len(edge_list("DeviceProfile", d, "DEVICE_OF"))
        if n != int(dev_ids[d]):
            bad_dev.append((d, n, int(dev_ids[d])))
    chk(f"reverse edge DEVICE_OF degree == staged FROM_DEVICE rows ({len(probe_dev)} devices, ring profile included: {'D_2c2006f554' in probe_dev})", not bad_dev, bad_dev)
    # ---- closed cases: quoted notes with commas round-trip, involves/edges
    cc = F["closed_case"]
    withc = cc[cc["analyst_notes"].str.contains(",")].head(5)
    bad_n = []
    for _, r in withc.iterrows():
        a = get_vertex("ClosedCase", r["case_id"])
        if a is None or a.get("analyst_notes") != r["analyst_notes"] or a.get("device_text") != r["device_text"]:
            bad_n.append(r["case_id"])
    chk(f"{len(withc)} closed cases whose notes contain commas round-trip exactly (QUOTE handling)", not bad_n and len(withc) > 0, bad_n)
    one = cc.iloc[0]["case_id"]
    inv_n = int((F["closed_involves"]["case_id"] == one).sum())
    chk("CLOSED_INVOLVES degree of a case == staged rows", len(edge_list("ClosedCase", one, "CLOSED_INVOLVES")) == inv_n, f"{inv_n}")
    a = get_vertex("ClosedCase", one)
    chk("ClosedCase timestamps/epochs read back", a is not None and str(a["closed_at"]) == cc.iloc[0]["closed_at"] and int(a["close_epoch"]) == int(cc.iloc[0]["close_epoch"]), a and (a["closed_at"], a["close_epoch"]))
    # ---- primary keys readable in queries (primary_id_as_attribute)
    tid = tx.iloc[len(tx) // 2]["txn_id"]
    r1 = interpret(f"S = {{Transaction.*}};\nR = SELECT s FROM S:s WHERE s.txn_id == {tid};\nPRINT R.size();")
    r2 = interpret(f'S = {{Customer.*}};\nR = SELECT s FROM S:s WHERE s.customer_id == "{F["customer"].iloc[0]["customer_id"]}";\nPRINT R.size();')
    chk("PRIMARY_ID attributes usable in WHERE (txn_id, customer_id)", isinstance(r1, list) and r1[0].get("R.size()") == 1 and isinstance(r2, list) and r2[0].get("R.size()") == 1, (r1, r2))
    # ---- temporal edge filter at a benchmark as_of
    card = "C13487-K1" if "C13487-K1" in set(F["card"]["card_id"]) else cards[0]
    as_of = int((pd.Timestamp("2016-11-22 20:11:00") - T0).total_seconds())
    exp_n = int(((tx["card_id"] == card) & (tx["epoch"].astype("int64") <= as_of)).sum())
    r3 = interpret(f'SumAccum<INT> @@n;\nS = {{Card.*}};\nT = SELECT t FROM S:s -(MADE:e)-> Transaction:t WHERE s.card_id == "{card}" AND e.epoch <= {as_of} ACCUM @@n += 1;\nPRINT @@n;')
    chk(f"as-of filter on MADE.epoch for {card} at 2016-11-22 20:11:00 == staged count", isinstance(r3, list) and r3[0].get("@@n") == exp_n, (r3, exp_n))
    if "D_2c2006f554" in dev_ids.index:
        exp_c = fd[(fd["device_id"] == "D_2c2006f554") & (fd["epoch"].astype("int64") <= as_of)]["customer_id"].nunique()
        r4 = interpret(f'SetAccum<STRING> @@c;\nS = {{DeviceProfile.*}};\nT = SELECT t FROM S:s -(DEVICE_OF:e)-> Transaction:t WHERE s.device_id == "D_2c2006f554" AND e.epoch <= {as_of} ACCUM @@c += e.customer_id;\nPRINT @@c.size();')
        chk("ring device: distinct customers as-of 2016-11-22 20:11:00 equals the staged subset value", isinstance(r4, list) and r4[0].get("@@c.size()") == exp_c, (r4, exp_c))
    # ---- graph hygiene
    snap = tg.snapshot()
    chk("Transaction_Fraud description byte-identical", snap["tf"] == BEFORE["tf"] and bool(BEFORE["tf"]))
    chk("global schema byte-identical", snap["global"] == BEFORE["global"])
    chk("graph list unchanged (Transaction_Fraud, FraudInvestigation)", sorted(snap["graphs"]) == ["FraudInvestigation", "Transaction_Fraud"], snap["graphs"])
    bad_c = [x for x in RES["checks"] if not x["ok"]]
    return not bad_c


BEFORE = tg.snapshot()
print("graphs:", BEFORE["graphs"])
if GRAPH not in BEFORE["graphs"]:
    stop(f"{GRAPH} does not exist")
cur = tg.counts()
nonzero = {k: v for k, v in cur.items() if v}
print("current graph contents:", nonzero or "empty")
if args.verify_only:
    ok = verify()
    print("\nVERIFICATION:", "ALL CHECKS PASSED" if ok else "FAILED")
    sys.exit(0 if ok else 1)
if args.phase == "validation" and nonzero:
    stop("validation phase requires an EMPTY graph")
t0 = time.perf_counter()
load_all()
print(f"\nall files loaded in {time.perf_counter() - t0:.1f}s; polling counts ...", flush=True)
ok = verify()
RES["status"] = "ok" if ok else "verify_failed"
json.dump(RES, open(STAGE / "load_result.json", "w"), indent=1, default=str)
print("\nVERIFICATION:", "ALL CHECKS PASSED" if ok else "FAILED")
sys.exit(0 if ok else 1)
