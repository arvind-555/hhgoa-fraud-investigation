"""Build a small, referentially complete VALIDATION batch from the full staged files (card-closed subgraph).

Selection (deterministic): the flagged/ring/closed-case/null-type cards that exercise every code path, then random small cards
until ~6,000 transactions. Everything attached to those cards (customers, devices, regions, emails, edges, closed cases) is kept
if BOTH endpoints are inside the batch, so a load with VERTEX_MUST_EXIST must drop nothing.
Usage: python src/ingestion/subset.py [--src data/staged/full] [--out data/staged/validation] [--target 6000]"""
import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from stage import LAYOUT  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--src", default=str(ROOT / "data" / "staged" / "full"))
ap.add_argument("--out", default=str(ROOT / "data" / "staged" / "validation"))
ap.add_argument("--target", type=int, default=6000)
a = ap.parse_args()
SRC, OUT = Path(a.src), Path(a.out)
OUT.mkdir(parents=True, exist_ok=True)
F = {n: pd.read_csv(SRC / f"{n}.csv", header=None, names=cols, dtype=str, keep_default_na=False, na_values=[]) for n, cols in LAYOUT.items()}
tx = F["transaction"]
ntx = tx.groupby("card_id").size()
rng = np.random.default_rng(7)
sel, why = [], {}


def add(cards, reason, cap):
    for c in cards:
        if c in ntx.index and ntx[c] <= cap and c not in why:
            sel.append(c)
            why[c] = reason


cp = pd.read_csv(ROOT / "case_pack.csv", dtype=str)
add(["C13487-K1"], "HHG-014 flagged card (ring device)", 400)
RING = "D_2c2006f554"
ring_cards = tx[tx["txn_id"].isin(F["from_device"].loc[F["from_device"]["device_id"] == RING, "txn_id"])]["card_id"].unique()
add(sorted(ring_cards), "uses the suspicious shared device", 400)
cs = F["card"]
add(sorted(cs.loc[cs["is_null_type"] == "true", "card_id"])[:3], "null-card6 (unknown-type) card", 200)
cc = F["closed_case"]
for pat, g in cc.groupby("pattern"):
    add(sorted(g["card_id"].unique(), key=lambda c: ntx.get(c, 10**9))[:2], f"closed case: {pat}", 400)
conn = F["closed_connected_to"]
first_case = conn["case_id"].iloc[0]
add(sorted(conn.loc[conn["case_id"] == first_case, "card_id"]), f"connected card of {first_case}", 300)
add(sorted(cc.loc[cc["case_id"] == first_case, "card_id"]), f"card of {first_case}", 300)
have = sum(int(ntx[c]) for c in sel)
pool = ntx[(ntx <= 150) & ~ntx.index.isin(sel)].index.to_numpy()
rng.shuffle(pool)
for c in pool:
    if have >= a.target:
        break
    sel.append(c); why[c] = "random small card"; have += int(ntx[c])
S = set(sel)
print(f"selected {len(S)} cards, {have:,} transactions")
print(pd.Series(why).value_counts().to_string())

sub = {}
sub["card"] = cs[cs["card_id"].isin(S)]
sub["transaction"] = tx[tx["card_id"].isin(S)]
T = set(sub["transaction"]["txn_id"])
sub["customer"] = F["customer"][F["customer"]["customer_id"].isin(set(sub["card"]["customer_id"]))]
sub["owns"] = F["owns"][F["owns"]["card_id"].isin(S)]
sub["made"] = F["made"][F["made"]["txn_id"].isin(T)]
sub["next"] = F["next"][F["next"]["txn_from"].isin(T) & F["next"]["txn_to"].isin(T)]
sub["from_device"] = F["from_device"][F["from_device"]["txn_id"].isin(T)]
D = set(sub["from_device"]["device_id"])
sub["device"] = F["device"][F["device"]["device_id"].isin(D)]
sub["seen_on"] = F["seen_on"][F["seen_on"]["card_id"].isin(S) & F["seen_on"]["device_id"].isin(D)]
sub["purchaser_email"] = F["purchaser_email"][F["purchaser_email"]["txn_id"].isin(T)]
sub["recipient_email"] = F["recipient_email"][F["recipient_email"]["txn_id"].isin(T)]
E = set(sub["purchaser_email"]["domain"]) | set(sub["recipient_email"]["domain"])
sub["email"] = F["email"][F["email"]["domain"].isin(E)]
sub["billed_in"] = F["billed_in"][F["billed_in"]["txn_id"].isin(T)]
R = set(sub["billed_in"]["addr1"])
sub["region"] = F["region"][F["region"]["addr1"].isin(R)]
sub["closed_case"] = cc[cc["card_id"].isin(S)]
C = set(sub["closed_case"]["case_id"])
sub["closed_on_card"] = F["closed_on_card"][F["closed_on_card"]["case_id"].isin(C)]
sub["closed_on_customer"] = F["closed_on_customer"][F["closed_on_customer"]["case_id"].isin(C)]
sub["closed_involves"] = F["closed_involves"][F["closed_involves"]["case_id"].isin(C)]
sub["closed_connected_to"] = conn[conn["case_id"].isin(C) & conn["card_id"].isin(S)]
dropped = int(conn["case_id"].isin(C).sum() - len(sub["closed_connected_to"]))
manifest = {"rows": {}, "sentinels": json.loads((SRC / "manifest.json").read_text())["sentinels"], "notes": {"connected_rows_dropped_outside_batch": dropped},
            "selected_cards": why}
for n in LAYOUT:
    sub[n].to_csv(OUT / f"{n}.csv", header=False, index=False, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    manifest["rows"][n] = len(sub[n])
(OUT / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
print(json.dumps(manifest["rows"], indent=1))
print("closed_connected_to rows dropped because the connected card is outside the batch:", dropped)
