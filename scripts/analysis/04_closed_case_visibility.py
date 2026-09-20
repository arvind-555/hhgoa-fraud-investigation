"""Closed-case (label) visibility (Signal validation 6-T3 and T4).

Rule: a closed case and its outcome become visible at closed_at, not opened_at.
Measures how much a wrong rule inflates the low-degree shared-device signal, how many cases are 'in flight',
and the effect of measuring device degree at end-of-window instead of as-of.
Usage: python 04_closed_case_visibility.py
"""
import numpy as np
import pandas as pd
from asof import device_pit
from common import RING_PROFILE, Checks, build_master, load_closed, load_casepack, save_json, to_epoch

m = build_master()
cc = load_closed()
cp = load_casepack()
ck = Checks("04_closed_case_visibility")
END = to_epoch("2016-11-01")
EV = to_epoch("2016-08-01")

ck.check("closed_at >= opened_at for every closed case", bool((cc["close_ep"] >= cc["open_ep"]).all()))
ck.check("every benchmark as_of is after the latest closed_at (all closed cases visible)",
         bool((cp["as_of"] > cc["close_ep"].max()).all()), f"max closed_at {cc['closed_at'].max()}, min benchmark opened_at {cp['opened_at'].min()}")

hrs = (cc["close_ep"] - cc["open_ep"]) / 3600
ep = np.arange(int(cc["open_ep"].min()) + 86400, int(cc["close_ep"].max()) - 86400, 6 * 3600)
inflight = [int(((cc["open_ep"] <= e) & (cc["close_ep"] > e)).sum()) for e in ep]
print(f"open->close hours: mean {hrs.mean():.1f} min {hrs.min():.1f} max {hrs.max():.1f}; cases in flight mean {np.mean(inflight):.1f} max {max(inflight)}")

d_ok = device_pit(m, label_time="closed", max_epoch=END)
d_lk = device_pit(m, label_time="opened", max_epoch=END)
def lab(x, mask):
    y = x[mask]
    return {"flagged": len(y), "fraud": int(y["fraud"].sum()), "cleared": int(y["cleared"].sum()),
            "precision_pct": round(y["fraud"].mean() * 100, 1) if len(y) else None}
ev_ok = d_ok[d_ok["epoch"] >= EV]
ev_lk = d_lk[d_lk["epoch"] >= EV]
good = lab(ev_ok, (ev_ok["fc"] >= 1) & (ev_ok["nk"] >= 3) & (ev_ok["cum_cust"] <= 20))
leak = lab(ev_lk, (ev_lk["fc"] >= 1) & (ev_lk["nk"] >= 3) & (ev_lk["cum_cust"] <= 20))
print("closed_at gating :", good)
print("opened_at gating :", leak)
ck.check("wrong (opened_at) rule flags more / looks better than the correct rule",
         leak["flagged"] > good["flagged"] and leak["precision_pct"] > good["precision_pct"])
ck.within("closed_at gated flagged (docs: 918)", good["flagged"], 918, 0)
ck.within("opened_at gated flagged (docs: 1,180)", leak["flagged"], 1180, 0)

# end-of-window degree
end_deg = d_ok.groupby("dev")["cum_cust"].max()
ev_ok = ev_ok.assign(end_deg=ev_ok["dev"].map(end_deg))
dis = float(((ev_ok["cum_cust"] <= 20) != (ev_ok["end_deg"] <= 20)).mean())
lk2 = lab(ev_ok, (ev_ok["fc"] >= 1) & (ev_ok["end_deg"] <= 20))
ok2 = lab(ev_ok, (ev_ok["fc"] >= 1) & (ev_ok["cum_cust"] <= 20))
print(f"end-of-window degree disagrees on {dis*100:.1f}% of txns; leaky {lk2} vs as-of {ok2}")
ck.check("end-of-window degree leaks (precision inflated)", lk2["precision_pct"] > ok2["precision_pct"])
save_json("04_closed_case_visibility", {"closed_gating": good, "opened_gating": leak, "in_flight_mean": float(np.mean(inflight)),
          "in_flight_max": max(inflight), "hours_mean": float(hrs.mean()), "degree_disagree_pct": dis * 100,
          "end_degree_leaky": lk2, "asof_degree": ok2})
ck.finish()
