"""R7 assessment (blocker B6): can 'a disputed charge matches the customer's own recurring pattern (same merchant, same amount, monthly)' be evaluated from this dataset?

There is NO merchant field. This script measures whether a merchant-free PROXY (same card + same ProductCD + identical amount, recurring at a ~monthly gap of 27-34 days,
point-in-time: only earlier transactions) (a) exists at all, (b) is common enough, and (c) separates cleared alerts from confirmed fraud in the closed-case history.
Everything is point-in-time (prior transactions only) and label-free on the feature side; labels are used only to evaluate the proxy. Output: output/08_r7_assessment.json
Run: python scripts/analysis/08_r7_assessment.py
"""
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import HIST_END_EPOCH, OUT, build_master, load_closed  # noqa: E402

DAY = 86400
LO, HI = 27 * DAY, 34 * DAY


def wilson(k, n, z=1.96):
    if n == 0:
        return [0.0, 1.0]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0, c - h), 4), round(min(1, c + h), 4)]


def monthly_repeat(t):
    """True where an EARLIER txn with the same card, ProductCD and exact amount exists 27-34 days before (point-in-time)."""
    t = t.sort_values(["epoch", "TransactionID"]).reset_index(drop=True)
    key = t["card_id"] + "|" + t["ProductCD"] + "|" + t["TransactionAmt"].round(2).astype(str)
    out = np.zeros(len(t), bool)
    same = np.zeros(len(t), bool)
    for _, idx in t.groupby(key).indices.items():
        if len(idx) < 2:
            continue
        ep = t["epoch"].values[idx]
        same[idx[1:]] = True                                      # any earlier identical txn (order is by epoch)
        for j in range(1, len(idx)):
            lo = np.searchsorted(ep, ep[j] - HI, side="left")
            hi = np.searchsorted(ep, ep[j] - LO, side="right")
            out[idx[j]] = hi > lo
    t["monthly_repeat"], t["identical_prior"] = out, same
    return t


def main():
    m = build_master()
    jo = m[m["epoch"] < HIST_END_EPOCH]
    t = monthly_repeat(jo).set_index("TransactionID")
    cc = load_closed()
    cc = cc[cc["close_ep"] <= HIST_END_EPOCH].copy()
    flag = cc["first_fraud_txn_id"].fillna(0).astype("int64")
    flag[flag == 0] = cc["txn_ids"].str.split("|").str[0].astype("int64")[flag == 0]
    r = t.loc[flag.values].reset_index()
    r["label"] = (cc["outcome"] == "confirmed_fraud").astype(int).values
    res = {"merchant_field_exists": False, "proxy": "same card + ProductCD + identical amount, 27-34 days earlier", "history_days": round(HIST_END_EPOCH / DAY),
           "all_txns": {"n": int(len(t)), "monthly_repeat": int(t["monthly_repeat"].sum()), "identical_prior": int(t["identical_prior"].sum())}}
    for col in ("monthly_repeat", "identical_prior"):
        fr, cl = r[r["label"] == 1], r[r["label"] == 0]
        kf, kc = int(fr[col].sum()), int(cl[col].sum())
        rf, rc = kf / max(len(fr), 1), kc / max(len(cl), 1)
        res[col] = {"fraud_cases": {"n": int(len(fr)), "hits": kf, "rate": round(rf, 4), "wilson": wilson(kf, len(fr))},
                    "cleared_cases": {"n": int(len(cl)), "hits": kc, "rate": round(rc, 4), "wilson": wilson(kc, len(cl))},
                    "lift_cleared_vs_fraud": round(rc / rf, 2) if rf else None,
                    "precision_of_proxy_for_cleared": round(kc / (kc + kf), 4) if (kc + kf) else None}
    # in-person vs online split of the monthly-repeat proxy among closed cases
    res["monthly_repeat_by_channel_closed_cases"] = {ch: {"n": int((r["channel"] == ch).sum()), "hits": int(r[(r["channel"] == ch)]["monthly_repeat"].sum())} for ch in ("online", "in_person")}
    res["gate"] = {"needed_for_a_signal": "monthly_repeat must (1) cover >= 30 cleared AND >= 30 fraud closed cases, (2) have non-overlapping Wilson intervals between cleared and fraud rates, (3) reproduce out of time",
                   "coverage_ok": bool(res["monthly_repeat"]["cleared_cases"]["hits"] >= 30 and res["monthly_repeat"]["fraud_cases"]["hits"] >= 30),
                   "separates": bool(res["monthly_repeat"]["cleared_cases"]["wilson"][0] > res["monthly_repeat"]["fraud_cases"]["wilson"][1])}
    (OUT).mkdir(exist_ok=True)
    (OUT / "08_r7_assessment.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
