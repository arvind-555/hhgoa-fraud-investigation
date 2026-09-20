"""Device degree / device state as of a timestamp (Findings 5, Signal validation 2 & 6-T1).

Two modes
  --device D --as-of "2016-11-22 20:11:00"   print the device state (oracle) for one device profile string or id
  (default)                                   self-test: incremental device_pit() vs brute-force oracle on 400 random
                                              (device, time) points, plus degree drift tests
Usage:
  python 02_device_degree_asof.py
  python 02_device_degree_asof.py --device "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080" --as-of "2016-11-22 20:11:00"
"""
import argparse
import numpy as np
from asof import dev_stats_oracle, device_pit
from common import RING_PROFILE, Checks, build_master, device_id, save_json, to_epoch

ap = argparse.ArgumentParser()
ap.add_argument("--device")
ap.add_argument("--as-of")
ap.add_argument("--samples", type=int, default=400)
a = ap.parse_args()
m = build_master()

if a.device:
    dev = a.device
    if dev.startswith("D_"):
        cand = {device_id(x): x for x in m["dev"].dropna().unique()}
        dev = cand[dev]
    s = dev_stats_oracle(m, dev, to_epoch(a.as_of))
    s.update(device=dev, device_id=device_id(dev), as_of=a.as_of)
    print(s)
    raise SystemExit(0)

ck = Checks("02_device_degree_asof")
d = device_pit(m, max_epoch=to_epoch("2016-11-01"))
dup = d.duplicated(["dev", "epoch"], keep=False)
cand = d[~dup & (d["epoch"] >= to_epoch("2016-08-15"))].sample(a.samples, random_state=3)
bad = 0
for r in cand.itertuples():
    o = dev_stats_oracle(m, r.dev, r.epoch, exclude_customer=r.customer_id)
    ok = (o["cust"] == r.cum_cust and abs(o["new"] - r.new_share) < 1e-5 and abs(o["proxy"] - r.proxy_share) < 1e-5
          and o["fraud_cust"] == r.fc)
    bad += not ok
ck.check("incremental == oracle at random (device, time) points", bad == 0, f"{a.samples} checked, {bad} mismatches")

# degree is non-decreasing in time per device and never counts future customers
mono = d.groupby("dev")["cum_cust"].apply(lambda s: bool((np.diff(s.values) >= 0).all())).all()
ck.check("as-of degree monotone non-decreasing per device", bool(mono))

# ring profile degree at the two dates used in the docs (data through those dates only)
res = {}
for label, ts in [("2016-11-12 00:46:24", "HHG-017 opened"), ("2016-11-22 20:11:00", "HHG-014 opened")]:
    s = dev_stats_oracle(m, RING_PROFILE, to_epoch(label))
    res[ts] = s
    print(ts, s)
ck.check("ring degree as-of HHG-014 opened_at == 44", res["HHG-014 opened"]["cust"] == 44, str(res["HHG-014 opened"]))
ck.check("ring degree as-of 2016-11-12 == 24 (before Nov wave grows) or close", res["HHG-017 opened"]["cust"] >= 24)
full = dev_stats_oracle(m, RING_PROFILE, 10**9)
ck.check("ring degree with ALL data == 52 (proves as-of differs from full)", full["cust"] == 52 and res["HHG-014 opened"]["cust"] < 52, str(full))
save_json("02_device_degree_asof", {"ring_asof": res, "ring_full": full})
ck.finish()
