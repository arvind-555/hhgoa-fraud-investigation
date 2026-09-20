"""Shared-device ring detection (Signal validation section 1).

Rule (as-of, label-free, risk-score-free):  customers >= 3  AND  new_share >= 0.9  AND  proxy_share >= 0.5
Default --max-date 2016-11-01 uses ONLY July-October data (the validation setting).
Use --max-date 2017-01-01 to run the same scan over the full file (application to the benchmark period).
  --as-of "2016-11-22 20:11:00"  lists devices flagged at that instant (data <= as_of), which is what the agent's
                                 scan_suspicious_devices tool must return.
Usage:
  python 05_ring_detection.py
  python 05_ring_detection.py --as-of "2016-11-22 20:11:00"
"""
import argparse
import itertools
import pandas as pd
from asof import dev_stats_oracle, device_pit, ring_flag
from common import RING_PROFILE, Checks, build_master, device_id, save_json, to_epoch

ap = argparse.ArgumentParser()
ap.add_argument("--max-date", default="2016-11-01")
ap.add_argument("--as-of")
ap.add_argument("--min-cust", type=int, default=3)
ap.add_argument("--new-share", type=float, default=0.9)
ap.add_argument("--proxy-share", type=float, default=0.5)
a = ap.parse_args()
m = build_master()

if a.as_of:
    cut = to_epoch(a.as_of)
    d = device_pit(m, max_epoch=cut + 1)                    # nothing after as_of enters the computation
    end = d.groupby("dev").last()
    e = end[(end["cum_cust"] >= a.min_cust) & (end["new_share"] >= a.new_share) & (end["proxy_share"] >= a.proxy_share)]
    e = e.assign(device_id=[device_id(x) for x in e.index],
                 fraud_customers=[dev_stats_oracle(m, x, cut)["fraud_cust"] for x in e.index]).sort_values("cum_cust", ascending=False)
    print(f"devices ring-suspect at {a.as_of} (data <= as_of): {len(e)}")
    print(e[["device_id", "nk", "cum_cust", "cum_txn", "new_share", "proxy_share", "fraud_customers"]].to_string())
    raise SystemExit(0)

ck = Checks("05_ring_detection")
END = to_epoch(a.max_date)
d = device_pit(m, max_epoch=END)
d = d[d["epoch"] < END]
ring = d[d["dev"] == RING_PROFILE].sort_values("epoch")
print(f"ring in data < {a.max_date}: {len(ring)} txns, {ring['customer_id'].nunique()} customers")
f = ring_flag(d, a.min_cust, a.new_share, a.proxy_share)
fr = ring[f.loc[ring.index]]
first_flag = fr["epoch"].min()
first_label = int(ring.loc[ring["fraud"], "close_ep"].min())
res = {"ring_txns": len(ring), "ring_customers": int(ring["customer_id"].nunique()),
       "flag_on_arrival": len(fr), "first_flag": str(pd.Timestamp("2016-07-02") + pd.Timedelta(seconds=int(first_flag))),
       "customers_at_first_flag": int(fr["cum_cust"].iloc[0]),
       "first_label_visible": str(pd.Timestamp("2016-07-02") + pd.Timedelta(seconds=first_label)),
       "lead_days": round((first_label - first_flag) / 86400, 1),
       "ring_txns_between_flag_and_first_label": int((fr["epoch"] < first_label).sum())}
oth = d[f & (d["dev"] != RING_PROFILE)]
allfraud = d[d["fraud"] & (d["epoch"] >= to_epoch("2016-08-01")) & (d["pattern"] != "undocumented")]
res["other_confirmed_fraud_flagged"] = int(allfraud.index.isin(oth.index).sum())
res["cleared_flagged"] = int(d[d["cleared"]].index.isin(oth.index).sum() + d[d["cleared"] & (d["dev"] == RING_PROFILE)].index.isin(fr.index).sum())
print(res)
if a.max_date == "2016-11-01":
    ck.check("ring flagged from data <= Oct 31 only", len(fr) > 0)
    ck.check("flag active on arrival for 52 of 54 ring txns", res["flag_on_arrival"] == 52 and res["ring_txns"] == 54, f"{res['flag_on_arrival']}/{res['ring_txns']}")
    ck.check("first flag with only 3 customers on the device", res["customers_at_first_flag"] == 3)
    ck.check("flag precedes first visible label by >= 14 days", res["lead_days"] >= 14, str(res["lead_days"]))
    ck.check("rule flags no other confirmed-fraud txn and no cleared alert", res["other_confirmed_fraud_flagged"] == 0 and res["cleared_flagged"] == 0)
    # grid
    rows = []
    for mc, ns, ps in itertools.product([2, 3, 5], [0.7, 0.8, 0.9, 1.0], [0.3, 0.5, 0.7, 0.9]):
        g = ring_flag(d, mc, ns, ps)
        ever = d[g]["dev"].nunique()
        found = bool(g.loc[ring.index].any())
        rows.append({"min_cust": mc, "new_share": ns, "proxy_share": ps, "ever_flagged_devices": int(ever), "ring_found": found})
    grid = pd.DataFrame(rows)
    ck.check("ring found in all 48 threshold combinations", bool(grid["ring_found"].all()), f"{int(grid['ring_found'].sum())}/48")
    prop = grid[(grid.min_cust == 3) & (grid.new_share == 0.9) & (grid.proxy_share == 0.5)].iloc[0]
    ck.check("proposed config: 6 devices ever flagged", int(prop["ever_flagged_devices"]) == 6, str(int(prop["ever_flagged_devices"])))
    # ablation
    abl = {
        "new>=0.9 only": int(d[(d.cum_cust >= 3) & (d.new_share >= .9)]["dev"].nunique()),
        "proxy>=0.5 only": int(d[(d.cum_cust >= 3) & (d.proxy_share >= .5)]["dev"].nunique()),
        "new & proxy": int(d[ring_flag(d)]["dev"].nunique()),
        "new & proxy & nk>=3": int(d[ring_flag(d, min_known=3)]["dev"].nunique()),
        "label-only fraud>=2, deg<=100": int(d[(d.fc >= 2) & (d.cum_cust <= 100) & (d.nk >= 3)]["dev"].nunique()),
    }
    ring_by_label = bool(((d.fc >= 2) & (d.cum_cust <= 100) & (d.nk >= 3))[ring.index].any())
    print("ablation (devices ever flagged):", abl, "| label-only rule finds ring:", ring_by_label)
    ck.check("label-only variant does not find the ring", not ring_by_label)
    ck.check("both conditions needed (ablation counts 439 / 82 / 6)", (abl["new>=0.9 only"], abl["proxy>=0.5 only"], abl["new & proxy"]) == (439, 82, 6), str(abl))
    res.update(grid=grid.to_dict("records"), ablation=abl)
save_json("05_ring_detection", res)
ck.finish()
