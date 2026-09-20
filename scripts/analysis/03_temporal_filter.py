"""Temporal filtering: future-corruption invariance (Signal validation 6-T2).

For random (device, as_of) pairs: permute device/customer/identity fields and randomise fraud labels on every
row AFTER as_of, and flip the labels of cases that close after as_of. The as-of view functions must return the
identical result. This is the oracle the GSQL queries and MCP wrapper will be tested against in the build phase.
Usage: python 03_temporal_filter.py [--trials 60]
"""
import argparse
import numpy as np
from asof import assert_no_future, dev_stats_oracle, view_txns
from common import Checks, build_master, device_id, to_epoch

ap = argparse.ArgumentParser()
ap.add_argument("--trials", type=int, default=60)
a = ap.parse_args()
rng = np.random.default_rng(7)
m = build_master()
ck = Checks("03_temporal_filter")


def corrupt(m, as_of):
    c = m.copy()
    fut = c.index[c["epoch"] > as_of]
    for col in ["dev", "customer_id", "id_15", "id_23"]:
        c.loc[fut, col] = c.loc[rng.permutation(fut), col].values
    c.loc[fut, "fraud"] = rng.random(len(fut)) < 0.5
    c.loc[fut, "close_ep"] = 0
    late = c["case_id"].notna() & (c["close_ep"] > as_of) & (c["epoch"] <= as_of)   # past txns whose case closes later
    c.loc[late, "fraud"] = ~c.loc[late, "fraud"]
    return c


from asof import device_pit
d = device_pit(m, max_epoch=to_epoch("2016-11-01"))
pairs = d[(d["epoch"] >= to_epoch("2016-08-20")) & (d["cum_cust"] >= 3)].sample(a.trials, random_state=11)[["dev", "epoch"]]
viol = 0
for dev, as_of in pairs.itertuples(index=False):
    base = dev_stats_oracle(m, dev, as_of)
    alt = dev_stats_oracle(corrupt(m, as_of), dev, as_of)
    viol += base != alt
ck.check("future corruption does not change as-of device state", viol == 0, f"{a.trials} trials, {viol} violations")

# a deliberately LEAKY variant must be caught by the same test (test-the-test)
def leaky_stats(m, dev, as_of):
    v = m[m["dev"] == dev]                                  # forgot the epoch filter
    return {"cust": int(v["customer_id"].nunique())}
lk = sum(leaky_stats(m, dev, as_of) != leaky_stats(corrupt(m, as_of), dev, as_of) for dev, as_of in pairs.head(10).itertuples(index=False))
ck.check("test detects a leaky implementation", lk > 0, f"{lk}/10 leaky trials differ under corruption")

# every 'view' really is <= as_of
ok = True
for as_of in [to_epoch("2016-11-12 00:46:24"), to_epoch("2016-12-29 07:53:54")]:
    assert_no_future(view_txns(m, as_of), as_of)
ck.check("view_txns never exceeds as_of", ok)
ck.finish()
