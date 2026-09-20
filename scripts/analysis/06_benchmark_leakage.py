"""Benchmark leakage audit (Signal validation 6-T6/T7).

For each of the 20 case-pack cases builds the as-of view (epoch <= opened_at; closed cases with closed_at <= opened_at)
and reports how much of the file lies in the future, plus device state as-of vs with all data.
No verdicts are produced. Writes output/06_benchmark_leakage.csv.
Usage: python 06_benchmark_leakage.py
"""
import pandas as pd
from asof import assert_no_future, dev_stats_oracle, view_cases, view_txns
from common import OUT, Checks, build_master, load_casepack, load_closed, save_json

m = build_master()
cc = load_closed()
cp = load_casepack()
ck = Checks("06_benchmark_leakage")
rows = []
for c in cp.itertuples():
    v = view_txns(m, c.as_of)
    vc = v[v["customer_id"] == c.customer_id]
    full = m[m["customer_id"] == c.customer_id]
    mx = assert_no_future(vc, c.as_of)
    f = m[m["TransactionID"] == c.flagged_txn_id].iloc[0]
    cvis = view_cases(cc[cc["customer_id"] == c.customer_id], c.as_of)
    r = {"case": c.case_id, "opened_at": c.opened_at, "flagged_epoch_ok": bool(f["epoch"] <= c.as_of),
         "card_id_matches": f["card_id"] == c.card_id, "view_txns": len(vc), "full_txns": len(full),
         "future_hidden": len(full) - len(vc), "closed_cases_visible": len(cvis),
         "closed_cases_all": int((cc["customer_id"] == c.customer_id).sum()),
         "flagged_in_closed_case": bool(pd.notna(f["case_id"]))}
    if pd.notna(f["dev"]):
        a = dev_stats_oracle(m, f["dev"], c.as_of, exclude_customer=c.customer_id)
        b = dev_stats_oracle(m, f["dev"], 10**9, exclude_customer=c.customer_id)
        r.update(device=f["dev"], dev_cust_asof=a["cust"], dev_cust_full=b["cust"], dev_new_asof=round(a["new"], 2),
                 dev_proxy_asof=round(a["proxy"], 2), dev_fraud_cust_asof=a["fraud_cust"])
    rows.append(r)
df = pd.DataFrame(rows)
OUT.mkdir(parents=True, exist_ok=True)
df.to_csv(OUT / "06_benchmark_leakage.csv", index=False)
print(df[["case", "view_txns", "future_hidden", "closed_cases_visible", "dev_cust_asof", "dev_cust_full"]].to_string())
ck.check("20 cases audited", len(df) == 20)
ck.check("no future rows in any customer view", True, "assert_no_future passed for 20 views")
ck.check("flagged txn is visible at as_of for all 20", bool(df["flagged_epoch_ok"].all()))
ck.check("derived card_id matches case pack for all 20", bool(df["card_id_matches"].all()))
ck.check("no flagged txn appears in a closed case", not df["flagged_in_closed_case"].any())
ck.check("all closed cases of the 20 customers are visible (closed before every as_of)", (df["closed_cases_visible"] == df["closed_cases_all"]).all())
ck.check("future txns are hidden (>0) for every case", bool((df["future_hidden"] > 0).all()), f"range {df['future_hidden'].min()}-{df['future_hidden'].max()}")
g = df.set_index("case")
ck.check("HHG-014 ring device: 44 customers as-of vs 52 full", (g.loc["HHG-014", "dev_cust_asof"], g.loc["HHG-014", "dev_cust_full"]) == (44, 52))
ck.check("HHG-013 device crosses hub threshold with future data: 59 vs 253", (g.loc["HHG-013", "dev_cust_asof"], g.loc["HHG-013", "dev_cust_full"]) == (59, 253))
ck.check("HHG-009 device is the all-null hub profile (>1,000 customers)", g.loc["HHG-009", "dev_cust_asof"] > 1000 and g.loc["HHG-009", "device"] == "? | ? | ? | ?")
save_json("06_benchmark_leakage", df.to_dict("records"))
ck.finish()
