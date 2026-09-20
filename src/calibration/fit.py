"""B3 calibration pipeline (deterministic; no randomness). Usage:  python src/calibration/fit.py            -> writes config/calibration_v1.json and a report.

What is calibrated: the mapping  evidence strength (none|weak|moderate|strong)  ->  P(confirmed fraud), from the closed-case history only.

Permitted data: closed_cases_history.csv (labels, gated by close_epoch <= the calibration horizon = 2016-11-01) and the transactions/identity facts needed to
recompute each case's strength point-in-time (frame.py). Never used: case_pack.csv, benchmark outcomes, anything after the horizon, risk_score as a predictor.

Validity gates. A bucket may be reported calibrated=true at runtime only if ALL of them hold:
  G1 horizon        the runtime as_of >= the label horizon (every training label was closed before the case's as_of);
  G2 support        >= MIN_TRAIN cases in the training period and >= MIN_TEST cases in the out-of-time test period;
  G3 reliability    the training estimate lies inside the Wilson 95% interval of the out-of-time test rate (train <= 2016-09-15 < test);
  G4 identification the bucket's Manski bounds for P(fraud | alert) have width <= MAX_BOUND_WIDTH. The closed cases are the *investigated* alerts; most high-risk
                    transactions never became a case (unlabeled != legitimate), so P(fraud | any alert) is only partially identified: the interval
                    [labelled fraud / alerts, (labelled fraud + unlabeled) / alerts] contains it whatever the unlabeled ones are. A wide interval means the
                    closed-case rate cannot be transferred to new alerts, so the probability is NOT calibrated for them.
G4 is the gate the current data fail; the pipeline records that instead of hiding it.
"""
import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from calibration.frame import STRENGTHS, build_frame, strength_table  # noqa: E402
from common import HIST_END_EPOCH, build_master, load_closed  # noqa: E402

VERSION = "b3-v1"
CUT_EPOCH = int((pd.Timestamp("2016-09-15") - pd.Timestamp("2016-07-02")).total_seconds())     # train <= cut < test
SHRINK = 10.0                       # empirical-Bayes strength toward the training base rate
MIN_TRAIN, MIN_TEST = 50, 30
MAX_BOUND_WIDTH = 0.20
ALERT_MIN_RISK = 0.5                # generic "alert-like" population definition (not tuned on the benchmark)
OUT = ROOT / "config" / "calibration_v1.json"


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def shrunk(k, n, base):
    return (k + SHRINK * base) / (n + SHRINK)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fit():
    fr = build_frame()
    train = fr[fr["case_close_ep"] <= CUT_EPOCH]
    test = fr[fr["epoch"] > CUT_EPOCH]                       # opened (flagged) after the cut: features PIT, labels never used for fitting
    base_tr = float(train["label"].mean())
    m = build_master()
    jo = m[m["epoch"] < HIST_END_EPOCH][["TransactionID", "epoch", "risk_score", "fraud", "cleared", "close_ep"]]
    st = strength_table()[["TransactionID", "strength"]]
    al = jo[jo["risk_score"] >= ALERT_MIN_RISK].merge(st, on="TransactionID")
    lab_fraud = al["fraud"] & (al["close_ep"] <= HIST_END_EPOCH)
    lab_clear = al["cleared"] & (al["close_ep"] <= HIST_END_EPOCH)
    unl = ~(lab_fraud | lab_clear)
    buckets, report = {}, {}
    for s in STRENGTHS:
        tr, te, full = train[train["strength"] == s], test[test["strength"] == s], fr[fr["strength"] == s]
        p_tr = shrunk(int(tr["label"].sum()), len(tr), base_tr)
        lo, hi = wilson(int(te["label"].sum()), len(te))
        a = al["strength"] == s
        n_a = int(a.sum())
        b_lo = float(lab_fraud[a].sum() / n_a) if n_a else 0.0
        b_hi = float((lab_fraud[a].sum() + unl[a].sum()) / n_a) if n_a else 1.0
        reasons = []
        g2 = len(tr) >= MIN_TRAIN and len(te) >= MIN_TEST
        if not g2:
            reasons.append(f"G2 support: train n={len(tr)} (<{MIN_TRAIN}) or test n={len(te)} (<{MIN_TEST})")
        g3 = g2 and lo <= p_tr <= hi
        if g2 and not g3:
            reasons.append(f"G3 reliability: training estimate {p_tr:.3f} outside the out-of-time Wilson interval [{lo:.3f}, {hi:.3f}]")
        width = b_hi - b_lo
        g4 = width <= MAX_BOUND_WIDTH
        if not g4:
            reasons.append(f"G4 identification: P(fraud|alert) only bounded to [{b_lo:.3f}, {b_hi:.3f}] (width {width:.3f} > {MAX_BOUND_WIDTH}); "
                           f"{int(unl[a].sum())} of {n_a} alert transactions in this bucket are unlabeled")
        p_final = shrunk(int(full["label"].sum()), len(full), float(fr["label"].mean()))
        buckets[s] = {"p_closed_case_population": round(p_final, 4), "validated_in_population": bool(g2 and g3), "identified_for_alerts": bool(g4),
                      "calibrated_ok": bool(g2 and g3 and g4), "reasons": reasons}
        report[s] = {"n_train": len(tr), "k_train": int(tr["label"].sum()), "n_test": len(te), "k_test": int(te["label"].sum()), "p_train": round(p_tr, 4),
                     "test_rate": round(float(te["label"].mean()), 4) if len(te) else None, "test_wilson": [round(lo, 4), round(hi, 4)], "n_all": len(full),
                     "p_final": round(p_final, 4), "alert_n": n_a, "alert_bounds": [round(b_lo, 4), round(b_hi, 4)]}
    # out-of-time scoring of the bucket model against the constant baseline (in-population only)
    pm = test["strength"].map({s: shrunk(int(train[train["strength"] == s]["label"].sum()), int((train["strength"] == s).sum()), base_tr) for s in STRENGTHS}).astype(float)
    y = test["label"].astype(float)
    brier, brier0 = float(((pm - y) ** 2).mean()), float(((base_tr - y) ** 2).mean())
    art = {"version": VERSION, "method": "empirical-Bayes bucket rates by evidence strength; out-of-time validation; Manski identification gate",
           "label_horizon_epoch": HIST_END_EPOCH, "train_cut_epoch": CUT_EPOCH, "shrink": SHRINK,
           "gates": {"min_train": MIN_TRAIN, "min_test": MIN_TEST, "max_bound_width": MAX_BOUND_WIDTH, "alert_min_risk": ALERT_MIN_RISK},
           "inputs_sha256": {"closed_cases_history.csv": sha(ROOT / "closed_cases_history.csv")},
           "buckets": buckets,
           "notes": ["S01 ring has no labelled history (first ring transaction is after the horizon): strong evidence from S01 is uncalibrated by construction",
                     "risk_score is not a feature; alert population uses it only to define which transactions count as alerts for gate G4"]}
    rep = {"version": VERSION, "base_rate_train": round(base_tr, 4), "n_cases": len(fr), "buckets": report,
           "out_of_time": {"n": len(test), "brier_bucket_model": round(brier, 5), "brier_constant_baseline": round(brier0, 5)}}
    return art, rep


def main():
    art, rep = fit()
    OUT.write_text(json.dumps(art, indent=1, sort_keys=True), encoding="utf-8")
    rp = ROOT / "data" / "runs" / "calibration"
    rp.mkdir(parents=True, exist_ok=True)
    (rp / "report.json").write_text(json.dumps(rep, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps(rep, indent=1, sort_keys=True))
    print("calibrated_ok:", {s: b["calibrated_ok"] for s, b in art["buckets"].items()})


if __name__ == "__main__":
    main()
