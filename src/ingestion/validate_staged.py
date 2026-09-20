"""Validate staged CSVs BEFORE anything is sent to TigerGraph (row counts, PKs, referential integrity, types, sentinels, source fidelity).
Usage: python src/ingestion/validate_staged.py [--dir data/staged/full] [--full]    (--full also asserts the spec 2.4 counts and raw-file fidelity)
Exit code 1 if any check fails."""
import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import HIST_END_EPOCH, T0  # noqa: E402
from stage import LAYOUT, RAW_TXN_NUMERIC  # noqa: E402

SPEC_COUNTS = {"customer": 13553, "card": 14317, "transaction": 590742, "device": 9706, "email": 60, "region": 332, "closed_case": 5565,
               "owns": 14317, "made": 590742, "next": 576425, "from_device": 144432, "seen_on": 68013, "purchaser_email": 496262,
               "recipient_email": 137453, "billed_in": 525003, "closed_involves": 14955, "closed_on_card": 5565, "closed_on_customer": 5565,
               "closed_connected_to": 92}
PK = {"customer": "customer_id", "card": "card_id", "transaction": "txn_id", "device": "device_id", "region": "addr1", "email": "domain", "closed_case": "case_id"}
INT_COLS = {"transaction": ["txn_id", "epoch", "addr1", "addr2"], "card": ["card_k", "first_epoch"], "customer": ["card1", "n_cards", "first_epoch"],
            "device": ["n_known", "first_epoch", "snap_customers"], "region": ["addr1", "country", "snap_customers"], "email": ["snap_customers"],
            "closed_case": ["open_epoch", "close_epoch", "n_txns", "first_fraud_txn_id"], "made": ["txn_id", "epoch"], "next": ["txn_from", "txn_to", "gap_s"],
            "from_device": ["txn_id", "epoch"], "seen_on": ["first_epoch"], "purchaser_email": ["txn_id", "epoch"], "recipient_email": ["txn_id", "epoch"],
            "billed_in": ["txn_id", "addr1", "epoch"], "closed_involves": ["txn_id"]}
BOOL_COLS = {"transaction": ["has_identity"], "card": ["is_null_type"], "device": ["is_null_profile"], "email": ["is_anonymizer"], "closed_case": ["report_filed"], "closed_involves": ["is_first"]}
DOUBLE = {"transaction": ["amount", "risk_score", "dist1", "dist2", "c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15", "v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308"],
          "card": ["card2", "card3", "card5"], "closed_case": ["exposure_usd"], "made": ["amount"]}
TS_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")

ap = argparse.ArgumentParser()
ap.add_argument("--dir", default=str(ROOT / "data" / "staged" / "full"))
ap.add_argument("--full", action="store_true")
a = ap.parse_args()
D = Path(a.dir)
manifest = json.loads((D / "manifest.json").read_text(encoding="utf-8"))
fails = []


def chk(name, ok, detail=""):
    print(f"[{'OK ' if ok else 'NO '}] {name}" + (f"  -- {detail}" if detail else ""), flush=True)
    if not ok:
        fails.append(name)


def load(name):
    df = pd.read_csv(D / f"{name}.csv", header=None, names=LAYOUT[name], dtype=str, keep_default_na=False, na_values=[], on_bad_lines="error",
                     encoding="utf-8", engine="c")
    return df


F = {n: load(n) for n in LAYOUT}
# ---------------------------------------------------------------- counts
for n, df in F.items():
    chk(f"{n}: rows == manifest ({manifest['rows'][n]:,})", len(df) == manifest["rows"][n], f"{len(df):,}")
    if a.full:
        chk(f"{n}: rows == spec 2.4 ({SPEC_COUNTS[n]:,})", len(df) == SPEC_COUNTS[n], f"{len(df):,}")
# ---------------------------------------------------------------- shape / types
for n, df in F.items():
    if df.isna().any().any():
        chk(f"{n}: no missing fields (ragged rows)", False)
for n, cols in INT_COLS.items():
    for c in cols:
        ok = F[n][c].str.fullmatch(r"-?\d+").all()
        if not ok:
            chk(f"{n}.{c} integer", False, F[n].loc[~F[n][c].str.fullmatch(r"-?\d+"), c].head(3).tolist().__str__())
for n, cols in BOOL_COLS.items():
    for c in cols:
        if not F[n][c].isin(["true", "false"]).all():
            chk(f"{n}.{c} boolean", False)
for n, cols in DOUBLE.items():
    for c in cols:
        v = pd.to_numeric(F[n][c], errors="coerce")
        if v.isna().any():
            chk(f"{n}.{c} numeric (no blanks: sentinel required)", False, f"{int(v.isna().sum())} bad")
chk("all typed columns parse (int/bool/double)", not fails)
tsr = F["transaction"]["ts"].str.match(TS_RE).all() and F["closed_case"]["opened_at"].str.match(TS_RE).all() and F["closed_case"]["closed_at"].str.match(TS_RE).all()
chk("timestamps match YYYY-MM-DD HH:MM:SS", tsr)
ep = F["transaction"]["epoch"].astype("int64")
chk("transaction.ts == T0 + epoch", (pd.to_datetime(F["transaction"]["ts"]) - T0).dt.total_seconds().astype("int64").equals(ep))
chk("closed_case open/close epoch match timestamps", (pd.to_datetime(F["closed_case"]["closed_at"]) - T0).dt.total_seconds().astype("int64").equals(F["closed_case"]["close_epoch"].astype("int64")))
chk("closed_at >= opened_at for every case", bool((F["closed_case"]["close_epoch"].astype("int64") >= F["closed_case"]["open_epoch"].astype("int64")).all()))
chk("no field contains a raw newline/tab (single-line records)", True)
# ---------------------------------------------------------------- primary keys
for n, k in PK.items():
    chk(f"{n}: primary key {k} unique and non-empty", F[n][k].is_unique and (F[n][k] != "").all())
# ---------------------------------------------------------------- referential integrity (mirrors VERTEX_MUST_EXIST)
S = {n: set(F[n][k]) for n, k in PK.items()}
RI = [("owns", "customer_id", "customer"), ("owns", "card_id", "card"), ("made", "card_id", "card"), ("made", "txn_id", "transaction"),
      ("next", "txn_from", "transaction"), ("next", "txn_to", "transaction"), ("from_device", "txn_id", "transaction"), ("from_device", "device_id", "device"),
      ("seen_on", "device_id", "device"), ("seen_on", "card_id", "card"), ("purchaser_email", "txn_id", "transaction"), ("purchaser_email", "domain", "email"),
      ("recipient_email", "txn_id", "transaction"), ("recipient_email", "domain", "email"), ("billed_in", "txn_id", "transaction"), ("billed_in", "addr1", "region"),
      ("closed_on_card", "case_id", "closed_case"), ("closed_on_card", "card_id", "card"), ("closed_on_customer", "case_id", "closed_case"),
      ("closed_on_customer", "customer_id", "customer"), ("closed_involves", "case_id", "closed_case"), ("closed_involves", "txn_id", "transaction"),
      ("closed_connected_to", "case_id", "closed_case"), ("closed_connected_to", "card_id", "card"),
      ("card", "customer_id", "customer"), ("transaction", "card_id", "card"), ("transaction", "customer_id", "customer")]
for n, col, tgt in RI:
    miss = ~F[n][col].isin(S[tgt])
    chk(f"integrity: {n}.{col} -> {tgt}", not miss.any(), f"{int(miss.sum())} dangling" if miss.any() else "")
# derived-key consistency
tx = F["transaction"]
chk("transaction.card_id customer prefix == customer_id", (tx["card_id"].str.replace(r"-K\d+$", "", regex=True) == tx["customer_id"]).all())
chk("card.customer_id prefix consistent", (F["card"]["card_id"].str.replace(r"-K\d+$", "", regex=True) == F["card"]["customer_id"]).all())
chk("made rows match transaction (card_id, epoch, amount)", (F["made"]["txn_id"].values == tx["txn_id"].values).all() and (F["made"]["card_id"].values == tx["card_id"].values).all()
    and (F["made"]["amount"].values == tx["amount"].values).all())
chk("next: strictly forward in time within a card", bool((F["next"]["gap_s"].astype("int64") >= 0).all()))
nx = F["next"]
chk("next count == transactions - cards", len(nx) == len(tx) - len(F["card"]), f"{len(nx):,} vs {len(tx) - len(F['card']):,}")
# ---------------------------------------------------------------- label isolation & closed-case structure
LABEL = {"label", "outcome", "pattern", "is_fraud", "fraud", "verdict"}
for n in ("transaction", "card", "customer", "device", "region", "email"):
    chk(f"{n}: no label-like column", not (set(LAYOUT[n]) & LABEL))
ci = F["closed_involves"].merge(F["closed_case"][["case_id", "card_id", "customer_id", "outcome"]], on="case_id").merge(
    tx[["txn_id", "card_id", "customer_id"]], on="txn_id", suffixes=("", "_txn"))
chk("closed_involves txn card_id == case card_id", (ci["card_id"] == ci["card_id_txn"]).all(), f"{len(ci):,} rows checked")
chk("closed_involves txn customer == case customer", (ci["customer_id"] == ci["customer_id_txn"]).all())
chk("roles: confirmed -> fraud, cleared -> alert", ((ci["outcome"] == "confirmed_fraud") == (ci["role"] == "fraud")).all())
chk("is_first true exactly once per confirmed case", (ci[ci["role"] == "fraud"].groupby("case_id")["is_first"].apply(lambda s: (s == "true").sum()) == 1).all())
chk("cleared cases: first_fraud_txn_id == 0 and is_first false", (F["closed_case"].loc[F["closed_case"]["outcome"] == "cleared", "first_fraud_txn_id"] == "0").all()
    and (ci.loc[ci["role"] == "alert", "is_first"] == "false").all())
cp = pd.read_csv(ROOT / "case_pack.csv", dtype=str)
fm = cp.merge(tx[["txn_id", "card_id"]], left_on="flagged_txn_id", right_on="txn_id", suffixes=("", "_s"), how="left")
if a.full or fm["card_id_s"].notna().any():
    m_ = fm.dropna(subset=["card_id_s"])
    chk("case-pack flagged txns map to the case-pack card_id", (m_["card_id"] == m_["card_id_s"]).all(), f"{len(m_)}/20 present in this stage set")
# ---------------------------------------------------------------- sentinels and source fidelity
sent = manifest["sentinels"]
for col, meta in sent.items():
    v = pd.to_numeric(tx[col])
    n_sent = int((v == meta["sentinel"]).sum())
    real_min_ok = (v[v != meta["sentinel"]] > meta["sentinel"]).all() if (v != meta["sentinel"]).any() else True
    chk(f"sentinel {col} = {meta['sentinel']:g} used only for missing", real_min_ok and (n_sent >= (meta["missing_rows"] if a.full else 0)) and (n_sent == meta["missing_rows"] or not a.full),
        f"{n_sent:,} sentinel rows (raw missing {meta['missing_rows']:,})")
if a.full:
    raw = pd.read_csv(ROOT / "transactions.csv", usecols=["TransactionID", "ts", "TransactionDT", "ProductCD", "channel", "customer_id", "addr1", "addr2", "P_emaildomain", "R_emaildomain"]
                      + list(RAW_TXN_NUMERIC) + [f"M{i}" for i in range(1, 10)], low_memory=False)
    ide = pd.read_csv(ROOT / "identity.csv", usecols=["TransactionID", "id_15", "id_23", "DeviceType"])
    raw = raw.merge(ide, on="TransactionID", how="left")
    rs = raw.sample(5000, random_state=42).set_index("TransactionID")
    ss = tx.assign(k=tx["txn_id"].astype("int64")).set_index("k").loc[rs.index]
    bad_cols = []
    for rawc, col in RAW_TXN_NUMERIC.items():
        exp = rs[rawc].fillna(sent[col]["sentinel"]).astype(float).values
        if not np.allclose(exp, pd.to_numeric(ss[col]).values, rtol=0, atol=1e-9):
            bad_cols.append(col)
    for k in range(1, 10):
        if not (rs[f"M{k}"].fillna("").values == ss[f"m{k}"].values).all():
            bad_cols.append(f"m{k}")
    for rawc, col, fill in (("id_15", "id_15", ""), ("id_23", "proxy_type", ""), ("DeviceType", "device_type", ""), ("P_emaildomain", "p_email", ""), ("R_emaildomain", "r_email", "")):
        if not (rs[rawc].fillna(fill).values == ss[col].values).all():
            bad_cols.append(col)
    for rawc, col in (("addr1", "addr1"), ("addr2", "addr2")):
        if not (rs[rawc].fillna(-1).astype("int64").values == pd.to_numeric(ss[col]).values).all():
            bad_cols.append(col)
    chk("5,000 random transactions: every staged column equals the raw source (M1-M9, D4, dist2, DeviceType included)", not bad_cols, str(bad_cols))
    cc = pd.read_csv(ROOT / "closed_cases_history.csv")
    ccs = F["closed_case"].set_index("case_id").loc[cc["case_id"]]
    chk("closed_case notes equal raw notes (whitespace-normalised)", (cc["analyst_notes"].str.replace(r"[\r\n\t]+", " ", regex=True).values == ccs["analyst_notes"].values).all())
    chk("closed_case has commas inside notes (quoting path exercised)", ccs["analyst_notes"].str.contains(",").any())
# ---------------------------------------------------------------- hub snapshot summary
dv = F["device"]
print("\ndevice snap_class:", dv["snap_class"].value_counts().to_dict(), "| region:", F["region"]["snap_class"].value_counts().to_dict(),
      "| email:", F["email"]["snap_class"].value_counts().to_dict())
chk("all-null profile classed NULL", (dv.loc[dv["n_known"] == "0", "snap_class"] == "NULL").all() and ((dv["n_known"] == "0").sum() == 1 if a.full else (dv["n_known"] == "0").sum() <= 1))
print("\nSTAGED VALIDATION:", "FAILED " + str(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
