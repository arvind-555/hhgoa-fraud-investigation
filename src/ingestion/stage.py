"""Stage the raw CSVs into headerless, load-ready CSV files for the FraudInvestigation graph (spec section 3).

Reads transactions.csv / identity.csv / closed_cases_history.csv DIRECTLY (the analysis cache lacks M1-M9, D4, dist2,
DeviceType). Reuses scripts/analysis/common.py for the card-id rule, device id, and epoch conventions.

Conventions (spec E8/E9, section 3):
  * headerless CSV, comma separated, minimal double-quote quoting, LF line endings;
  * timestamps 'YYYY-MM-DD HH:MM:SS', booleans true/false, missing strings = empty;
  * missing INT addr1/addr2 = -1; missing DOUBLE = a per-column SENTINEL (TigerGraph has no null): -1 when the column's minimum
    is >= 0, else floor(min)-1. Sentinels are recorded in manifest.json and must be treated as "missing" by every query;
  * NO label columns on facts; labels only in closed_case / closed_involves.
Usage: python src/ingestion/stage.py [--out data/staged/full]
"""
import argparse
import csv
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from common import HIST_END_EPOCH, T0, derive_card_ids, device_id  # noqa: E402

# ---------------------------------------------------------------- output layouts (order == loading-job column order)
TXN = (["txn_id", "epoch", "ts", "amount", "product_cd", "channel", "risk_score", "card_id", "customer_id", "addr1", "addr2", "dist1", "dist2",
        "has_identity", "id_15", "proxy_type", "device_type", "p_email", "r_email", "c1", "c2", "c4", "c8", "c10",
        "d1", "d2", "d3", "d4", "d5", "d10", "d15"] + [f"m{i}" for i in range(1, 10)] + ["v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308"])
LAYOUT = {
    "customer": ["customer_id", "card1", "n_cards", "first_epoch"],
    "card": ["card_id", "customer_id", "card_k", "card4", "card6", "card2", "card3", "card5", "is_null_type", "first_epoch"],
    "region": ["addr1", "country", "snap_customers", "snap_class"],
    "email": ["domain", "is_anonymizer", "snap_customers", "snap_class"],
    "device": ["device_id", "profile_str", "device_info", "os", "browser", "screen", "n_known", "is_null_profile", "first_epoch", "snap_customers", "snap_class"],
    "transaction": TXN,
    "owns": ["customer_id", "card_id"],
    "made": ["card_id", "txn_id", "epoch", "amount", "channel"],
    "next": ["txn_from", "txn_to", "gap_s"],
    "from_device": ["txn_id", "device_id", "epoch", "id_15", "proxy_type", "customer_id"],
    "seen_on": ["device_id", "card_id", "first_epoch"],
    "purchaser_email": ["txn_id", "domain", "epoch"],
    "recipient_email": ["txn_id", "domain", "epoch"],
    "billed_in": ["txn_id", "addr1", "epoch"],
    "closed_case": ["case_id", "customer_id", "card_id", "opened_at", "closed_at", "open_epoch", "close_epoch", "outcome", "pattern", "n_txns",
                    "exposure_usd", "report_filed", "actions_taken", "first_fraud_txn_id", "analyst_notes", "device_text"],
    "closed_on_card": ["case_id", "card_id"],
    "closed_on_customer": ["case_id", "customer_id"],
    "closed_involves": ["case_id", "txn_id", "role", "is_first"],
    "closed_connected_to": ["case_id", "card_id"],
}
# raw column -> staged transaction column
DOUBLE_COLS = ["amount", "risk_score", "dist1", "dist2", "c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15",
               "v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308"]
RAW_TXN_NUMERIC = {"TransactionAmt": "amount", "risk_score": "risk_score", "dist1": "dist1", "dist2": "dist2",
                   **{c.upper(): c for c in ["c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15"]},
                   **{c.upper(): c for c in ["v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308"]}}
RAW_TXN_COLS = (["TransactionID", "TransactionDT", "ts", "ProductCD", "channel", "customer_id", "card1", "card2", "card3", "card4", "card5", "card6",
                 "addr1", "addr2", "P_emaildomain", "R_emaildomain"] + list(RAW_TXN_NUMERIC) + [f"M{i}" for i in range(1, 10)])
RAW_ID_COLS = ["TransactionID", "id_15", "id_23", "id_30", "id_31", "id_33", "DeviceInfo", "DeviceType"]


def sentinel_for(series):
    mn = float(series.min())
    return -1.0 if mn >= 0 else float(math.floor(mn) - 1)


def write(df, name, out):
    cols = LAYOUT[name]
    assert list(df.columns) == cols, f"{name}: columns {list(df.columns)} != {cols}"
    df.to_csv(out / f"{name}.csv", header=False, index=False, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
    return len(df)


def tf(b):
    return b.map({True: "true", False: "false"})


def snap_class_count(n, kind):
    if kind == "device":
        return "UNSEEN" if n == 0 else "HUB" if n >= 100 else "MID" if n >= 21 else "LOW"
    if kind == "region":
        return "UNSEEN" if n == 0 else "MEGA" if n >= 300 else "HUB" if n >= 100 else "MID" if n >= 21 else "LOW"
    return "GENERIC" if n >= 50 else "RARE"


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    print("reading raw CSVs ...", flush=True)
    t = pd.read_csv(ROOT / "transactions.csv", usecols=RAW_TXN_COLS, low_memory=False)
    i = pd.read_csv(ROOT / "identity.csv", usecols=RAW_ID_COLS)
    cc = pd.read_csv(ROOT / "closed_cases_history.csv")
    manifest = {"rows": {}, "sentinels": {}, "source_columns": {}, "hist_end_epoch": HIST_END_EPOCH}

    t["epoch"] = t["TransactionDT"].astype("int64")
    t["card_id"] = derive_card_ids(t)
    i["profile_str"] = (i["DeviceInfo"].fillna("?") + " | " + i["id_30"].fillna("?") + " | " + i["id_31"].fillna("?") + " | " + i["id_33"].fillna("?"))
    t = t.merge(i, on="TransactionID", how="left")
    t["has_identity"] = t["profile_str"].notna()
    dev_ids = {p: device_id(p) for p in i["profile_str"].unique()}
    t["device_id"] = t["profile_str"].map(dev_ids)
    t = t.sort_values(["epoch", "TransactionID"]).reset_index(drop=True)
    snap = t["epoch"] < HIST_END_EPOCH

    # ------------------------------------------------ transaction vertex
    tx = pd.DataFrame({
        "txn_id": t["TransactionID"], "epoch": t["epoch"], "ts": t["ts"], "product_cd": t["ProductCD"], "channel": t["channel"],
        "card_id": t["card_id"], "customer_id": t["customer_id"],
        "addr1": t["addr1"].fillna(-1).astype("int64"), "addr2": t["addr2"].fillna(-1).astype("int64"),
        "has_identity": tf(t["has_identity"]), "id_15": t["id_15"].fillna(""), "proxy_type": t["id_23"].fillna(""), "device_type": t["DeviceType"].fillna(""),
        "p_email": t["P_emaildomain"].fillna(""), "r_email": t["R_emaildomain"].fillna(""),
        **{f"m{k}": t[f"M{k}"].fillna("") for k in range(1, 10)}})
    for raw, col in RAW_TXN_NUMERIC.items():
        s = t[raw]
        sent = sentinel_for(s.dropna())
        manifest["sentinels"][col] = {"sentinel": sent, "missing_rows": int(s.isna().sum()), "min": float(s.min())}
        tx[col] = s.fillna(sent)
    manifest["source_columns"] = {**{c: raw for raw, c in RAW_TXN_NUMERIC.items()}, **{f"m{k}": f"M{k}" for k in range(1, 10)},
                                  "proxy_type": "identity.id_23", "device_type": "identity.DeviceType", "id_15": "identity.id_15", "txn_id": "TransactionID", "epoch": "TransactionDT"}
    tx = tx[TXN]
    manifest["rows"]["transaction"] = write(tx, "transaction", out)

    # ------------------------------------------------ customer / card
    g = t.groupby("card_id")
    def mode_or(s, default=-1.0):
        m = s.dropna().mode()
        return float(m.iloc[0]) if len(m) else default
    card = pd.DataFrame({
        "customer_id": g["customer_id"].first(),
        "card4": g["card4"].first().fillna(""), "card6": g["card6"].first().fillna(""),
        "card2": g["card2"].agg(mode_or), "card3": g["card3"].agg(mode_or), "card5": g["card5"].agg(mode_or),
        "is_null_type": tf(g["card6"].first().isna()), "first_epoch": g["epoch"].min()}).reset_index()
    card["card_k"] = card["card_id"].str.extract(r"-K(\d+)$")[0].astype(int)
    card = card[LAYOUT["card"]]
    manifest["rows"]["card"] = write(card, "card", out)
    gc = t.groupby("customer_id")
    cust = pd.DataFrame({"card1": gc["card1"].first().astype("int64"), "n_cards": gc["card_id"].nunique(), "first_epoch": gc["epoch"].min()}).reset_index()
    manifest["rows"]["customer"] = write(cust[LAYOUT["customer"]], "customer", out)

    # ------------------------------------------------ device / region / email vertices with hub snapshot
    d = t[t["has_identity"]]
    dg = d.groupby("profile_str")
    dev = pd.DataFrame({"first_epoch": dg["epoch"].min()}).reset_index()
    parts = dg[["DeviceInfo", "id_30", "id_31", "id_33"]].first().reset_index()
    dev = dev.merge(parts, on="profile_str")
    dev["device_id"] = dev["profile_str"].map(dev_ids)
    dev["n_known"] = 4 - dev["profile_str"].str.count(r"\?")
    dev["is_null_profile"] = tf(dev["n_known"] == 0)
    snapc = d[d["epoch"] < HIST_END_EPOCH].groupby("profile_str")["customer_id"].nunique()
    dev["snap_customers"] = dev["profile_str"].map(snapc).fillna(0).astype(int)
    dev["snap_class"] = [("NULL" if nk == 0 else snap_class_count(n, "device")) for nk, n in zip(dev["n_known"], dev["snap_customers"])]
    dev = dev.rename(columns={"DeviceInfo": "device_info", "id_30": "os", "id_31": "browser", "id_33": "screen"})
    for c in ("device_info", "os", "browser", "screen"):
        dev[c] = dev[c].fillna("")
    manifest["rows"]["device"] = write(dev[LAYOUT["device"]], "device", out)

    r = t[t["addr1"].notna()]
    rg = r.groupby("addr1")
    reg = pd.DataFrame({"country": rg["addr2"].agg(lambda s: int(s.dropna().mode().iloc[0]) if s.notna().any() else -1)}).reset_index()
    rs = r[r["epoch"] < HIST_END_EPOCH].groupby("addr1")["customer_id"].nunique()
    reg["snap_customers"] = reg["addr1"].map(rs).fillna(0).astype(int)
    reg["snap_class"] = [snap_class_count(n, "region") for n in reg["snap_customers"]]
    reg["addr1"] = reg["addr1"].astype("int64")
    manifest["rows"]["region"] = write(reg[LAYOUT["region"]], "region", out)

    em = pd.concat([t[["P_emaildomain", "customer_id", "epoch"]].rename(columns={"P_emaildomain": "domain"}),
                    t[["R_emaildomain", "customer_id", "epoch"]].rename(columns={"R_emaildomain": "domain"})]).dropna(subset=["domain"])
    es = em[em["epoch"] < HIST_END_EPOCH].groupby("domain")["customer_id"].nunique()
    email = pd.DataFrame({"domain": sorted(em["domain"].unique())})
    email["is_anonymizer"] = tf(email["domain"] == "anonymous.com")
    email["snap_customers"] = email["domain"].map(es).fillna(0).astype(int)
    email["snap_class"] = [snap_class_count(n, "email") for n in email["snap_customers"]]
    manifest["rows"]["email"] = write(email[LAYOUT["email"]], "email", out)

    # ------------------------------------------------ edges
    manifest["rows"]["owns"] = write(card[["customer_id", "card_id"]], "owns", out)
    manifest["rows"]["made"] = write(pd.DataFrame({"card_id": t["card_id"], "txn_id": t["TransactionID"], "epoch": t["epoch"], "amount": tx["amount"], "channel": t["channel"]}), "made", out)
    o = t.sort_values(["card_id", "epoch", "TransactionID"])
    prev_id, prev_ep, prev_card = o["TransactionID"].shift(1), o["epoch"].shift(1), o["card_id"].shift(1)
    m = prev_card == o["card_id"]
    nxt = pd.DataFrame({"txn_from": prev_id[m].astype("int64"), "txn_to": o.loc[m, "TransactionID"], "gap_s": (o.loc[m, "epoch"] - prev_ep[m]).astype("int64")})
    manifest["rows"]["next"] = write(nxt, "next", out)
    fd = t[t["has_identity"]]
    manifest["rows"]["from_device"] = write(pd.DataFrame({"txn_id": fd["TransactionID"], "device_id": fd["device_id"], "epoch": fd["epoch"], "id_15": fd["id_15"].fillna(""),
                                                          "proxy_type": fd["id_23"].fillna(""), "customer_id": fd["customer_id"]}), "from_device", out)
    so = fd.groupby(["device_id", "card_id"])["epoch"].min().reset_index().rename(columns={"epoch": "first_epoch"})
    manifest["rows"]["seen_on"] = write(so[LAYOUT["seen_on"]], "seen_on", out)
    pe, re_, bi = t[t["P_emaildomain"].notna()], t[t["R_emaildomain"].notna()], t[t["addr1"].notna()]
    manifest["rows"]["purchaser_email"] = write(pd.DataFrame({"txn_id": pe["TransactionID"], "domain": pe["P_emaildomain"], "epoch": pe["epoch"]}), "purchaser_email", out)
    manifest["rows"]["recipient_email"] = write(pd.DataFrame({"txn_id": re_["TransactionID"], "domain": re_["R_emaildomain"], "epoch": re_["epoch"]}), "recipient_email", out)
    manifest["rows"]["billed_in"] = write(pd.DataFrame({"txn_id": bi["TransactionID"], "addr1": bi["addr1"].astype("int64"), "epoch": bi["epoch"]}), "billed_in", out)

    # ------------------------------------------------ closed cases (labels live ONLY here)
    ts = lambda s: (pd.to_datetime(s) - T0).dt.total_seconds().astype("int64")
    notes = cc["analyst_notes"].str.replace(r"[\r\n\t]+", " ", regex=True)
    dtxt = notes.str.extract(r"came from (?:a |an )?(.*?)\. ")[0].fillna("")
    ccs = pd.DataFrame({"case_id": cc["case_id"], "customer_id": cc["customer_id"], "card_id": cc["card_id"], "opened_at": cc["opened_at"], "closed_at": cc["closed_at"],
                        "open_epoch": ts(cc["opened_at"]), "close_epoch": ts(cc["closed_at"]), "outcome": cc["outcome"], "pattern": cc["pattern"], "n_txns": cc["n_txns"],
                        "exposure_usd": cc["exposure_usd"], "report_filed": tf(cc["report_filed"].eq("Yes")), "actions_taken": cc["actions_taken"],
                        "first_fraud_txn_id": cc["first_fraud_txn_id"].fillna(0).astype("int64"), "analyst_notes": notes, "device_text": dtxt})
    manifest["rows"]["closed_case"] = write(ccs[LAYOUT["closed_case"]], "closed_case", out)
    manifest["rows"]["closed_on_card"] = write(cc[["case_id", "card_id"]], "closed_on_card", out)
    manifest["rows"]["closed_on_customer"] = write(cc[["case_id", "customer_id"]], "closed_on_customer", out)
    inv = cc.assign(txn_id=cc["txn_ids"].str.split("|")).explode("txn_id")
    inv["txn_id"] = inv["txn_id"].astype("int64")
    inv["role"] = np.where(inv["outcome"].eq("confirmed_fraud"), "fraud", "alert")
    inv["is_first"] = tf(inv["txn_id"].eq(inv["first_fraud_txn_id"].fillna(-1).astype("int64")))
    manifest["rows"]["closed_involves"] = write(inv[["case_id", "txn_id", "role", "is_first"]], "closed_involves", out)
    con = cc.dropna(subset=["connected_card_ids"]).assign(card_id=lambda d_: d_["connected_card_ids"].str.split("|")).explode("card_id")
    manifest["rows"]["closed_connected_to"] = write(con[["case_id", "card_id"]], "closed_connected_to", out)

    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    print(json.dumps(manifest["rows"], indent=1))
    return manifest


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "staged" / "full"))
    main(ap.parse_args().out)
