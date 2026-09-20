"""Shared loading / derivation code for the Phase 2-4 analyses.

Everything is rebuilt from the four raw CSVs; nothing depends on scratch files.
The first call builds a compact cache (parquet) under scripts/analysis/.cache/.

Conventions (see docs/implementation_spec.md):
  epoch      = TransactionDT  (seconds since 2016-07-02 00:00:00)
  as_of      = case opened_at converted to the same epoch units
  card_id    = customer_id + "-K" + rank(card6, NaN first) within the customer
  dev        = "DeviceInfo | id_30 | id_31 | id_33" with "?" for null fields
"""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]                                   # .../HHGOA_IEEE
DATA = Path(os.environ.get("HHG_DATA_DIR", ROOT))
CACHE = HERE / ".cache"
OUT = HERE / "output"
T0 = pd.Timestamp("2016-07-02 00:00:00")
EVAL_START_EPOCH = int((pd.Timestamp("2016-08-01") - T0).total_seconds())
HIST_END_EPOCH = int((pd.Timestamp("2016-11-01") - T0).total_seconds())   # end of labelled history
RING_PROFILE = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"

TXN_COLS = (
    ["TransactionID", "TransactionDT", "TransactionAmt", "ProductCD", "card2", "card3", "card4", "card5",
     "card6", "addr1", "addr2", "dist1", "P_emaildomain", "R_emaildomain", "customer_id", "ts", "channel",
     "risk_score"]
    + ["C1", "C2", "C4", "C8", "C9", "C10", "D1", "D2", "D3", "D5", "D10", "D15"]
    + ["V51", "V52", "V79", "V93", "V94", "V217", "V258", "V264", "V280", "V307", "V308"]
)
ID_COLS = ["TransactionID", "id_15", "id_23", "id_30", "id_31", "id_33", "DeviceInfo"]


def to_epoch(ts):
    """Timestamp or string -> integer seconds since T0."""
    return int((pd.Timestamp(ts) - T0).total_seconds())


def device_id(profile_str):
    return "D_" + hashlib.md5(profile_str.encode("utf-8")).hexdigest()[:10]


def derive_card_ids(t):
    """card_id = customer_id + '-K' + rank of card6 (NaN first, then alphabetical) among the customer's card6 values."""
    d = pd.DataFrame({"customer_id": t["customer_id"], "c6": t["card6"].fillna("")}).drop_duplicates()
    d = d.sort_values(["customer_id", "c6"])
    d["k"] = d.groupby("customer_id").cumcount() + 1
    m = pd.DataFrame({"customer_id": t["customer_id"], "c6": t["card6"].fillna("")}).merge(
        d, on=["customer_id", "c6"], how="left")
    return (m["customer_id"] + "-K" + m["k"].astype(str)).values


def load_closed():
    cc = pd.read_csv(DATA / "closed_cases_history.csv")
    cc["open_ep"] = (pd.to_datetime(cc["opened_at"]) - T0).dt.total_seconds().astype(int)
    cc["close_ep"] = (pd.to_datetime(cc["closed_at"]) - T0).dt.total_seconds().astype(int)
    return cc


def load_casepack():
    cp = pd.read_csv(DATA / "case_pack.csv")
    cp["as_of"] = (pd.to_datetime(cp["opened_at"]) - T0).dt.total_seconds().astype(int)
    return cp


def build_master(force=False):
    """One row per transaction with derived keys and *case labels kept apart from the txn facts*.

    Label columns (case_id, outcome, pattern, open_ep, close_ep, fraud, cleared) exist here only so that
    analyses can evaluate signals. They must be gated by close_ep <= as_of whenever used as evidence.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    p = CACHE / "master.parquet"
    if p.exists() and not force:
        return pd.read_parquet(p)
    t = pd.read_csv(DATA / "transactions.csv", usecols=TXN_COLS, low_memory=False)
    i = pd.read_csv(DATA / "identity.csv", usecols=ID_COLS)
    i["dev"] = (i["DeviceInfo"].fillna("?") + " | " + i["id_30"].fillna("?") + " | "
                + i["id_31"].fillna("?") + " | " + i["id_33"].fillna("?"))
    t = t.merge(i[["TransactionID", "id_15", "id_23", "dev"]], on="TransactionID", how="left")
    t["epoch"] = t["TransactionDT"].astype("int64")
    t["card_id"] = derive_card_ids(t)
    t["has_identity"] = t["dev"].notna()
    cc = load_closed()
    lab = cc.assign(tid=cc["txn_ids"].str.split("|")).explode("tid")
    lab["tid"] = lab["tid"].astype("int64")
    lab = lab[["tid", "case_id", "outcome", "pattern", "open_ep", "close_ep"]].rename(columns={"tid": "TransactionID"})
    t = t.merge(lab, on="TransactionID", how="left")
    t["fraud"] = t["outcome"].eq("confirmed_fraud")
    t["cleared"] = t["outcome"].eq("cleared")
    t = t.sort_values(["epoch", "TransactionID"]).reset_index(drop=True)
    t.to_parquet(p)
    return t


def save_json(name, obj):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


class Checks:
    """Collects PASS/FAIL acceptance checks; exit code = number of failures."""

    def __init__(self, name):
        self.name, self.rows = name, []

    def check(self, label, ok, detail=""):
        self.rows.append((label, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
        return ok

    def within(self, label, value, expected, tol):
        return self.check(label, abs(value - expected) <= tol, f"got {value}, expected {expected} +/- {tol}")

    def finish(self):
        fails = [r for r in self.rows if not r[1]]
        save_json(f"{self.name}_checks", [{"check": a, "pass": b, "detail": c} for a, b, c in self.rows])
        print(f"{self.name}: {len(self.rows) - len(fails)}/{len(self.rows)} checks passed")
        raise SystemExit(1 if fails else 0)
