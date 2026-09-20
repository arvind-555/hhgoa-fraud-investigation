"""B3 calibration frame: one row per closed case (Jul-Oct history), with the agent's evidence strength recomputed POINT-IN-TIME at the case's flagged transaction.

Point-in-time rules (spec section 4):
* every feature of a case is computed as of the flagged transaction's own epoch (as_of = epoch): only earlier/equal transactions and only closed cases with
  close_epoch <= that epoch contribute (Phase-4 `card_pit` frame + the device oracle below);
* the case's OWN label never enters its own features (its close_epoch is after its flagged transaction);
* labels are used only as the training/evaluation target, never as a feature.

The strength mapping replicates `agent.orchestrator._assess` / `fraud_tools.patterns.independence` (tier 1-5 sources counted once per source name; low-context
sources are one bucket). Device signals S02a/S02b are computed here; the S01 ring does not exist in Jul-Oct, so no calibration data exists for it.
`tests/test_calibration_live.py` cross-checks this oracle against the live agent on sampled transactions.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from common import HIST_END_EPOCH, build_master, load_closed  # noqa: E402
from signals import card_pit  # noqa: E402

STRENGTHS = ["none", "weak", "moderate", "strong"]
IDENTITY_PROXY = ("IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN")


def _device_flags(m, cc):
    """S02a/S02b per transaction: as-of distinct customers on the device (own included) and another customer's confirmed-fraud txn on it visible by epoch."""
    dev_meta = pd.read_csv(ROOT / "data" / "staged" / "full" / "device.csv", header=None, dtype=str, keep_default_na=False,
                           names=["device_id", "profile_str", "device_info", "os", "browser", "screen", "n_known", "is_null_profile", "first_epoch", "snap_customers", "snap_class"])
    blocked = set(dev_meta.loc[dev_meta["snap_class"].isin(["NULL", "HUB"]), "profile_str"])
    d = m[m["dev"].notna()][["TransactionID", "dev", "customer_id", "epoch", "fraud", "close_ep"]].sort_values(["epoch", "TransactionID"])
    first = d.drop_duplicates(["dev", "customer_id"])
    first_ep = {k: np.sort(v["epoch"].values) for k, v in first.groupby("dev")}
    fr = d[d["fraud"]]
    fraud_ev = {k: v[["close_ep", "customer_id"]].values for k, v in fr.groupby("dev")}
    deg, s02 = np.zeros(len(d), int), np.zeros(len(d), bool)
    for pos, (dv, ep, cu) in enumerate(zip(d["dev"].values, d["epoch"].values, d["customer_id"].values)):
        deg[pos] = np.searchsorted(first_ep[dv], ep, side="right")
        ev = fraud_ev.get(dv)
        if ev is not None:
            s02[pos] = any((c <= ep) and (u != cu) for c, u in ev)
    out = d[["TransactionID", "dev"]].copy()
    out["dev_degree"], out["other_fraud_visible"] = deg, s02
    out["dev_blocked"] = out["dev"].isin(blocked) | out["dev"].str.startswith("? |")
    return out.set_index("TransactionID")


def strength_from_flags(f):
    """Vectorised replica of the agent's evidence strength. `f` is a DataFrame with the boolean signal columns below."""
    tier15 = pd.DataFrame({"card_sequence": f["S06"] | f["S07"], "amount_history": f["S08"], "device_graph": f["S02a"] | f["S02b"]})
    n = tier15.sum(axis=1)
    low = f["S05"] | f["S09"] | f["S10"] | f["S11"] | f["S12"] | f["S13"] | f["S14"] | f["S15"]
    strong = f["S02a"] | (n >= 2)
    return np.where(strong, "strong", np.where(n == 1, "moderate", np.where(low, "weak", "none")))


def strength_table():
    """Evidence strength (and signal flags) for EVERY Jul-Oct transaction, point-in-time at its own epoch. Labels are not used."""
    m = build_master()
    jo = m[m["epoch"] < HIST_END_EPOCH]
    cc_all = load_closed()
    r = card_pit(jo, cc_all)                                        # PIT gating of prior cases uses close_ep <= epoch inside card_pit
    dv = _device_flags(jo, cc_all)
    r = r.merge(dv.reset_index()[["TransactionID", "dev_degree", "other_fraud_visible", "dev_blocked"]], on="TransactionID", how="left")
    online = r["channel"].eq("online")
    ok_dev = r["dev"].notna() & ~r["dev_blocked"].astype("boolean").fillna(True).astype(bool)
    other = r["other_fraud_visible"].astype("boolean").fillna(False).astype(bool)
    f = pd.DataFrame(index=r.index)
    f["S02a"] = ok_dev & other & (r["dev_degree"] <= 5)
    f["S02b"] = ok_dev & other & r["dev_degree"].between(6, 20)
    f["S01"] = False                                                # no ring exists in the labelled history
    f["S06"] = online & (r["small1h"] >= 3) & (r["TransactionAmt"] >= 20)
    f["S07"] = (r["ProductCD"] == "C") & r["TransactionAmt"].between(400, 500) & (r["fam1h"] >= 1)
    f["S08"] = online & (r["hist"] >= 20) & (r["z"] > 2)
    f["S05"] = (r["prior_card_cases"] + r["prior_cust_cases"]) > 0
    f["S09"] = online & r["new_prod"].astype("boolean").fillna(False).astype(bool)
    f["S10"] = online & r["id_15"].eq("New")
    f["S11"] = online & r["id_23"].isin(IDENTITY_PROXY)
    f["S12"] = online & r["id_15"].eq("New") & r["id_23"].notna()
    f["S13"] = online & r["addr1"].isna()
    f["S14"] = online & ~r["has_identity"]
    f["S15"] = (~online) & r["addr1"].notna() & r["away_c"].astype("boolean").fillna(False).astype(bool)
    out = r[["TransactionID", "epoch", "channel", "risk_score", "customer_id", "card_id"]].copy()
    out["strength"] = strength_from_flags(f)
    for c in f.columns:
        out[c] = f[c].values
    return out


def build_frame():
    """Closed-case frame: one row per case known at the calibration horizon (close_epoch <= Nov 1), strength at its flagged transaction."""
    st = strength_table().set_index("TransactionID")
    cc = load_closed()
    cc = cc[cc["close_ep"] <= HIST_END_EPOCH].copy()             # labels known at the calibration horizon; later closures are excluded
    cc["flag_txn"] = cc["first_fraud_txn_id"].fillna(0).astype("int64")
    cc.loc[cc["flag_txn"] == 0, "flag_txn"] = cc["txn_ids"].str.split("|").str[0].astype("int64")
    r = st.loc[cc["flag_txn"].values].reset_index()
    r["case_id"], r["label"] = cc["case_id"].values, (cc["outcome"] == "confirmed_fraud").astype(int).values
    r["case_close_ep"] = cc["close_ep"].values
    return r
