"""Signal validation tables (docs/signal_validation.md sections 0.1, 2, 3, 4).

Everything is point-in-time and restricted to July-October; metrics use Aug 1 - Oct 31 (July is warm-up).
Outputs: output/07_signal_validation.json (+ printed tables) and PASS/FAIL against the numbers in the docs.
Usage: python 07_signal_validation.py [--skip-links]
  --skip-links  skips the (slow) region/email link tables
"""
import argparse

import pandas as pd
from asof import device_pit
from common import EVAL_START_EPOCH, HIST_END_EPOCH, Checks, build_master, load_closed, save_json
from signals import auc, card_pit, link_signal, summarize

ap = argparse.ArgumentParser()
ap.add_argument("--skip-links", action="store_true")
a = ap.parse_args()
pd.set_option("display.width", 250)
pd.set_option("display.max_columns", 40)
m = build_master()
m = m[m["epoch"] < HIST_END_EPOCH]                       # NOTHING after Oct 31 in this script
cc = load_closed()
ck = Checks("07_signal_validation")
out = {}

# ------------------------------------------------------------ A. device sharing by as-of degree (S02)
d = device_pit(m, label_time="closed")
ev = d[d["epoch"] >= EVAL_START_EPOCH]
base_dev = ev["fraud"].mean()
rows = []
for name, lo, hi in [("<=5", 1, 5), ("6-20", 6, 20), ("21-100", 21, 100), (">100", 101, 10**9)]:
    for nk_name, nkm in [("nk>=3", ev["nk"] >= 3), ("nk<3", ev["nk"] < 3)]:
        mk = (ev["fc"] >= 1) & ev["cum_cust"].between(lo, hi) & nkm
        y = ev[mk]
        rows.append({"deg": name, "n_known": nk_name, "flagged": len(y), "fraud": int(y["fraud"].sum()),
                     "cleared": int(y["cleared"].sum()), "precision_pct": round(y["fraud"].mean() * 100, 1),
                     "lift": round(y["fraud"].mean() / base_dev, 2)})
S02 = pd.DataFrame(rows)
print("\nS02 device sharing with visible confirmed fraud, by as-of degree (base fraud %.1f%%)\n" % (base_dev * 100),
      S02.to_string(index=False))
nullp = ev[ev["nk"] == 0]
out["S02"] = S02.to_dict("records")
out["null_profile"] = {"txns": len(nullp), "fraud_pct": round(nullp["fraud"].mean() * 100, 1)}
g = S02.set_index(["deg", "n_known"])
ck.check("S02 deg<=5,nk>=3: 175 flagged / 87 fraud", (g.loc[("<=5", "nk>=3"), "flagged"], g.loc[("<=5", "nk>=3"), "fraud"]) == (175, 87))
ck.check("S02 deg 6-20,nk>=3: 743 flagged / 202 fraud", (g.loc[("6-20", "nk>=3"), "flagged"], g.loc[("6-20", "nk>=3"), "fraud"]) == (743, 202))
ck.check("S02 21-100 and >100 show no lift (<1.15)", bool((S02[S02.deg.isin(["21-100", ">100"])]["lift"] < 1.15).all()))
ck.check("null profile precision below base", nullp["fraud"].mean() < base_dev,
         f"{nullp['fraud'].mean()*100:.1f}% vs {base_dev*100:.1f}%")

# ------------------------------------------------------------ B. card-level PIT signals (channel-specific bases)
t = card_pit(m, cc)
evc = t[t["epoch"] >= EVAL_START_EPOCH]
onl, inp = t["channel"] == "online", t["channel"] == "in_person"
b_on = evc[evc["channel"] == "online"]["fraud"].mean()
b_ip = evc[evc["channel"] == "in_person"]["fraud"].mean()
K_A4 = "A4 online & customer has visible prior confirmed case"
K_A5 = "A5 online & customer has NO prior case"
K_B1 = "B1 R5: >=3 prior <$5 online in 1h, this >=$20 online"
K_B3 = "B3 >=1 prior <$5 online in 1h, this >=$20 online"
K_B6 = "B6 burst family: C, $400-500, >=1 prior same card in 1h"
K_C1 = "C1 in-person region share<=5% (>=30 prior)"
K_C3 = "C3 in-person region never seen (>=30 prior)"
K_C4 = "C4 in-person away from PIT modal region"
K_D1 = "D1 online amount z>2 (>=20 prior)"
K_D2 = "D2 online new product (>=20 prior)"
K_E1 = "E1 online id_15=New"
sig = {
    K_A4: (onl & (t["prior_cust_cases"] >= 1), b_on),
    K_A5: (onl & (t["prior_cust_cases"] == 0), b_on),
    K_B1: ((t["small1h"] >= 3) & (t["TransactionAmt"] >= 20) & onl, b_on),
    K_B3: ((t["small1h"] >= 1) & (t["TransactionAmt"] >= 20) & onl, b_on),
    K_B6: ((t["ProductCD"] == "C") & t["TransactionAmt"].between(400, 500) & (t["fam1h"] >= 1), b_on),
    K_C1: (inp & (t["ip_hist"] >= 30) & (t["reg_share"] <= .05), b_ip),
    K_C3: (inp & (t["ip_hist"] >= 30) & (t["reg_share"] == 0), b_ip),
    K_C4: (t["away_c"], b_ip),
    K_D1: (onl & (t["z"] > 2) & (t["hist"] >= 20), b_on),
    K_D2: (onl & t["new_prod"], b_on),
    K_E1: (onl & (t["id_15"] == "New"), b_on),
    "E2 online proxy anonymous/hidden": (onl & t["id_23"].isin(["IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN"]), b_on),
    "E3 online no identity record": (onl & ~t["has_identity"], b_on),
    "E4 online addr1 missing": (onl & t["addr1"].isna(), b_on),
    "E5 online New & proxy": (onl & (t["id_15"] == "New") & t["id_23"].notna(), b_on),
    "F online risk>=0.7": (onl & (t["risk_score"] >= .7), b_on),
    "F in-person risk>=0.7": (inp & (t["risk_score"] >= .7), b_ip),
    "F online risk>=0.81": (onl & (t["risk_score"] >= .81), b_on),
}
rows = []
for k, (mk, base) in sig.items():
    r = summarize(t, mk, base)
    r["signal"] = k
    rows.append(r)
B = pd.DataFrame(rows).set_index("signal")
print("\nCard-level PIT signals (lift vs channel base; online %.1f%%, in-person %.1f%%)\n" % (b_on * 100, b_ip * 100), B.to_string())
out["card_signals"] = B.reset_index().to_dict("records")
ck.check("E1 id_15=New: 22,041 flagged and lift < 1", B.loc[K_E1, "flagged"] == 22041 and B.loc[K_E1, "lift"] < 1)
ck.check("R5 rule: 7 flagged", B.loc[K_B1, "flagged"] == 7)
ck.check("burst family: 33 flagged / 15 fraud / 0 cleared", (B.loc[K_B6, "flagged"], B.loc[K_B6, "fraud"], B.loc[K_B6, "cleared"]) == (33, 15, 0))
ck.check("D1 amount z>2: 251 flagged / 56 fraud", (B.loc[K_D1, "flagged"], B.loc[K_D1, "fraud"]) == (251, 56))
ck.check("C1 in-person region share<=5%: lift ~1.1 (weak)", 0.9 < B.loc[K_C1, "lift"] < 1.3)
away = t[(t["epoch"] >= EVAL_START_EPOCH) & (t["n_ip_c"] >= 30)]
away_stats = {"oor_away": round(float(away[away["pattern"] == "out_of_region_use"]["away_c"].mean()), 3),
              "ato_away": round(float(away[away["pattern"] == "account_takeover"]["away_c"].mean()), 3),
              "nonfraud_away": round(float(away[~away["fraud"] & ~away["cleared"]]["away_c"].mean()), 3),
              "cleared_away": round(float(away[away["cleared"]]["away_c"].mean()), 3)}
print("away-from-modal-region rates:", away_stats)
out["away_from_modal"] = away_stats
ck.check("cleared alerts are 'away' more often than non-fraud (region novelty cannot separate them)",
         away_stats["cleared_away"] > away_stats["nonfraud_away"])

# card testing coverage
ct = t[t["fraud"] & (t["pattern"] == "card_testing")]
r5 = ct[(ct["small1h"] >= 3) & (ct["TransactionAmt"] >= 20)]["case_id"].nunique()
loose = ct[(ct["small1h"] >= 1) & (ct["TransactionAmt"] >= 20)]["case_id"].nunique()
print(f"card_testing labelled cases: {ct['case_id'].nunique()}; satisfy R5 exactly: {r5}; looser: {loose}")
out["card_testing"] = {"cases": int(ct["case_id"].nunique()), "R5_exact": int(r5), "looser": int(loose)}
ck.check("R5 matches only 1 of 16 labelled card_testing cases", (ct["case_id"].nunique(), r5) == (16, 1))

# ------------------------------------------------------------ C. undocumented
und = t[t["pattern"] == "undocumented"]
ck.check("30 undocumented labelled txns", len(und) == 30)
out["undocumented"] = {"txns": len(und), "share_z_gt_2": round(float((und["z"] > 2).mean()), 3),
                       "share_risk_ge_0.5": round(float((und["risk_score"] >= .5).mean()), 3)}
print("undocumented:", out["undocumented"])

# ------------------------------------------------------------ D. within-channel AUC stability
sep1 = int((pd.Timestamp("2016-09-01") - pd.Timestamp("2016-07-02")).total_seconds())
res = []
feat = ["V93", "V52", "V79", "V51", "V94", "V258", "V264", "V217", "V308", "V307", "V280",
        "C1", "C2", "C4", "C8", "C10", "D2", "D3", "D5", "risk_score"]
for ch, pats in [("online", ["card_not_present_fraud", "card_not_present_new_device"]),
                 ("in_person", ["out_of_region_use", "account_takeover"])]:
    for nm, lo, hi in [("Jul-Aug", 0, sep1), ("Sep-Oct", sep1, HIST_END_EPOCH)]:
        x = t[(t["epoch"] >= lo) & (t["epoch"] < hi) & (t["channel"] == ch)]
        f, b = x[x["fraud"] & x["pattern"].isin(pats)], x[~x["fraud"] & ~x["cleared"]]
        for c in feat:
            res.append((ch, nm, c, round(auc(f[c], b[c]), 3)))
AU = pd.DataFrame(res, columns=["channel", "half", "feature", "auc"]).pivot_table(
    index=["channel", "feature"], columns="half", values="auc")
print("\nWithin-channel AUC (fraud vs unlabeled), two halves\n", AU.round(3).to_string())
out["auc"] = AU.reset_index().to_dict("records")
ck.check("V93 within-channel AUC (online) < 0.6 in both halves (earlier 0.93 was a channel artifact)",
         bool((AU.loc[("online", "V93")] < 0.6).all()))
ck.check("risk_score AUC stable across halves (within 0.05)",
         abs(AU.loc[("online", "risk_score"), "Jul-Aug"] - AU.loc[("online", "risk_score"), "Sep-Oct"]) < 0.05)

# ------------------------------------------------------------ E. region / email links
if not a.skip_links:
    bins = [("<=20", 1, 20), ("21-100", 21, 100), ("101-299", 101, 299), (">=300", 300, 10**9)]
    L = link_signal(t, "addr1", 14, bins) + link_signal(t, "R_emaildomain", 30, bins) + link_signal(t, "P_emaildomain", 30, bins)
    LK = pd.DataFrame(L)
    print("\nRegion / email links by as-of degree\n", LK.to_string(index=False))
    out["links"] = L
    hi_ = LK[LK["deg"].isin(["21-100", "101-299", ">=300"]) & (LK["flagged"] > 200)]
    ck.check("no consistent lift for entities with degree > 20 (median lift < 1.6)", float(hi_["lift"].median()) < 1.6,
             f"median {hi_['lift'].median():.2f}")

# ------------------------------------------------------------ F. hub snapshot drift (Sep-1 snapshot -> Oct-31)
drift = {}
for col, name in [("dev", "device"), ("addr1", "region"), ("R_emaildomain", "R_email"), ("P_emaildomain", "P_email")]:
    s0 = m[(m["epoch"] < sep1) & m[col].notna()].groupby(col)["customer_id"].nunique()
    s1 = m[m[col].notna()].groupby(col)["customer_id"].nunique()
    j = pd.concat([s0.rename("snap"), s1.rename("end")], axis=1)
    old = j.dropna(subset=["snap"])
    drift[name] = {"entities_at_snapshot": len(old), "crossed_20": int(((old["snap"] <= 20) & (old["end"] > 20)).sum()),
                   "crossed_100": int(((old["snap"] <= 100) & (old["end"] > 100)).sum()),
                   "new_after_snapshot": int(j["snap"].isna().sum())}
print("\nSnapshot drift (Sep-1 -> Oct-31):", drift)
out["snapshot_drift"] = drift
dv = drift["device"]
ck.check("devices: > 30% of Oct-31 devices did not exist at the snapshot (2,460 new vs 4,977 old = +49%)",
         dv["new_after_snapshot"] / (dv["entities_at_snapshot"] + dv["new_after_snapshot"]) > 0.3,
         f"{dv['new_after_snapshot']} new vs {dv['entities_at_snapshot']} old")
ck.check("devices: some low-degree devices cross 20 after the snapshot", dv["crossed_20"] > 0, str(dv["crossed_20"]))

save_json("07_signal_validation", out)
ck.finish()
