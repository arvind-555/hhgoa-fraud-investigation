"""Card ID reconstruction (Findings section 1).

rule: card_id = customer_id + "-K" + rank(card6, NaN first) among the customer's distinct card6 values.
Validates against 14,955 closed-case txns (5,565 cases) and the 20 case-pack flagged txns, and shows that the
tempting alternatives (first-seen / frequency order of card2..card6 combinations) do not reproduce the IDs.
Usage: python 01_card_id.py
"""
import numpy as np
import pandas as pd
from common import Checks, build_master, load_casepack, load_closed, save_json

m = build_master()
cc, cp = load_closed(), load_casepack()
ck = Checks("01_card_id")

# closed-case txns
lab = cc.assign(tid=cc["txn_ids"].str.split("|")).explode("tid")
lab["tid"] = lab["tid"].astype("int64")
j = lab.merge(m[["TransactionID", "card_id"]], left_on="tid", right_on="TransactionID", suffixes=("", "_derived"))
match = int((j["card_id"] == j["card_id_derived"]).sum())
case_ok = int((j["card_id"] == j["card_id_derived"]).groupby(j["case_id"]).all().sum())
ck.check("closed-case txns reproduce card_id", match == len(j) == 14955, f"{match}/{len(j)}")
ck.check("closed cases fully reproduce card_id", case_ok == len(cc) == 5565, f"{case_ok}/{len(cc)}")
multi = j[j["card_id"].str.endswith(("K2", "K3", "K4"))]["case_id"].nunique()
ck.check("discriminating cases (K>=2) present and correct", multi > 2000, f"{multi} cases with K>=2")

# case pack
f = cp.merge(m[["TransactionID", "card_id"]], left_on="flagged_txn_id", right_on="TransactionID", suffixes=("", "_d"))
ck.check("case-pack flagged txns reproduce card_id", int((f["card_id"] == f["card_id_d"]).sum()) == 20, "20/20")

# alternatives
t = m[["customer_id", "card2", "card3", "card4", "card5", "card6", "epoch", "card_id"]].copy()
t["combo"] = t[["card2", "card3", "card4", "card5", "card6"]].astype(str).agg("|".join, axis=1)
tid_to_combo = pd.Series(t["combo"].values, index=m["TransactionID"].values)
first_txn = lab.drop_duplicates("case_id").copy()
first_txn["combo"] = tid_to_combo.loc[first_txn["tid"].values].values
order_first = t.sort_values("epoch").drop_duplicates(["customer_id", "combo"])
order_first["k"] = order_first.groupby("customer_id").cumcount() + 1
freq = t.groupby(["customer_id", "combo"]).size().reset_index(name="n").sort_values(["customer_id", "n"], ascending=[True, False])
freq["k"] = freq.groupby("customer_id").cumcount() + 1
multi_cust = set(t.groupby("customer_id")["combo"].nunique().loc[lambda s: s > 1].index)
sub = first_txn[first_txn["customer_id"].isin(multi_cust)]
res = {}
for name, o in [("first_seen_combo", order_first), ("frequency_combo", freq)]:
    mp = {(r.customer_id, r.combo): r.k for r in o.itertuples()}
    pred = np.array([mp[(c, cb)] for c, cb in zip(sub["customer_id"], sub["combo"])])
    true = sub["card_id"].str[-1].astype(int).values
    res[name] = round(float((pred == true).mean()), 3)
print("alternative orderings (multi-combo customers):", res)
ck.check("alternatives do NOT reproduce IDs (<0.5)", all(v < 0.5 for v in res.values()), str(res))

st = {"derived_cards": int(m["card_id"].nunique()),
      "customers": int(m["customer_id"].nunique()),
      "null_card6_txns": int(m["card6"].isna().sum()),
      "cards_with_null_type": int(m.loc[m["card6"].isna(), "card_id"].nunique()),
      "alternatives": res}
print(st)
save_json("01_card_id", st)
ck.check("derived card count", st["derived_cards"] == 14317, str(st["derived_cards"]))
ck.finish()
