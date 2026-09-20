"""Card-level point-in-time signal features and evaluation helpers (Phase 4 logic)."""
import numpy as np
import pandas as pd
from scipy.stats import rankdata

from common import EVAL_START_EPOCH


def card_pit(m, cc):
    """Add PIT card/customer features. `m` must already be restricted to the analysis window (e.g. epoch < HIST_END).

    hist            prior txns on the card
    prior_card_cases / prior_cust_cases   confirmed cases with close_ep <= this txn's epoch (label gating by closed_at)
    small1h         prior online txns < $5 on the card in the previous hour
    on1h            prior online txns on the card in the previous hour
    fam1h           prior txns on the card in the previous hour with ProductCD=C and $400-500 (burst family)
    reg_share/ip_hist   share of the card's prior in-person txns in this txn's addr1, and count of prior in-person txns
    z               log-amount z-score vs the card's own history (>=20 prior)
    new_prod        first time this ProductCD on the card (>=20 prior)
    away_c          in-person txn whose addr1 differs from the customer's PIT modal in-person addr1 (>=30 prior)
    """
    t = m.sort_values(["card_id", "epoch", "TransactionID"]).reset_index(drop=True).copy()
    cid = t["card_id"].astype("category").cat.codes.values.astype(np.int64)
    key = cid * 10**9 + t["epoch"].values
    t["hist"] = t.groupby("card_id").cumcount()
    conf = cc[cc["outcome"] == "confirmed_fraud"]
    by_card = {k: np.sort(v["close_ep"].values) for k, v in conf.groupby("card_id")}
    by_cust = {k: np.sort(v["close_ep"].values) for k, v in conf.groupby("customer_id")}
    pc = np.zeros(len(t), int)
    pcu = np.zeros(len(t), int)
    ep = t["epoch"].values
    for card, idx in t.groupby("card_id").indices.items():
        if card in by_card:
            pc[idx] = np.searchsorted(by_card[card], ep[idx], side="right")
    for cu, idx in t.groupby("customer_id").indices.items():
        if cu in by_cust:
            pcu[idx] = np.searchsorted(by_cust[cu], ep[idx], side="right")
    t["prior_card_cases"], t["prior_cust_cases"] = pc, pcu
    onl = (t["channel"] == "online").values
    small = np.sort(key[onl & (t["TransactionAmt"].values < 5)])
    t["small1h"] = np.searchsorted(small, key, side="left") - np.searchsorted(small, key - 3600, side="left")
    onk = np.sort(key[onl])
    t["on1h"] = np.searchsorted(onk, key, side="left") - np.searchsorted(onk, key - 3600, side="left")
    fam = ((t["ProductCD"] == "C") & t["TransactionAmt"].between(400, 500)).values
    fk = np.sort(key[fam])
    t["fam1h"] = np.searchsorted(fk, key, side="left") - np.searchsorted(fk, key - 3600, side="left")
    ip = t[(t["channel"] == "in_person") & t["addr1"].notna()].copy()
    ip["prior_reg"] = ip.groupby(["card_id", "addr1"]).cumcount()
    ip["prior_ip"] = ip.groupby("card_id").cumcount()
    t["reg_share"] = np.nan
    t["ip_hist"] = np.nan
    t.loc[ip.index, "reg_share"] = ip["prior_reg"] / ip["prior_ip"].replace(0, np.nan)
    t.loc[ip.index, "ip_hist"] = ip["prior_ip"]
    la = np.log1p(t["TransactionAmt"])
    n = t["hist"].replace(0, np.nan)
    cs = la.groupby(t["card_id"]).cumsum() - la
    sq = (la ** 2).groupby(t["card_id"]).cumsum() - la ** 2
    mu = cs / n
    sd = np.sqrt(((sq / n) - mu ** 2).clip(lower=0) + 0.25)
    t["z"] = (la - mu) / sd
    t["new_prod"] = (t.groupby(["card_id", "ProductCD"]).cumcount() == 0) & (t["hist"] >= 20)
    # PIT modal in-person region per customer
    ipc = t[(t["channel"] == "in_person") & t["addr1"].notna()].sort_values(["epoch", "TransactionID"])
    cur, best, cnt = {}, {}, {}
    home = np.full(len(ipc), np.nan)
    nprior = np.zeros(len(ipc), int)
    for j, (c, a) in enumerate(zip(ipc["customer_id"].values, ipc["addr1"].values)):
        home[j] = best.get(c, np.nan)
        nprior[j] = cnt.get(c, 0)
        d = cur.setdefault(c, {})
        d[a] = d.get(a, 0) + 1
        cnt[c] = cnt.get(c, 0) + 1
        best[c] = max(d, key=d.get)
    t["home_c"] = np.nan
    t["n_ip_c"] = np.nan
    t.loc[ipc.index, "home_c"] = home
    t.loc[ipc.index, "n_ip_c"] = nprior
    t["away_c"] = (t["channel"] == "in_person") & (t["n_ip_c"] >= 30) & (t["addr1"] != t["home_c"])
    return t


def summarize(frame, mask, base_rate, eval_start=EVAL_START_EPOCH):
    x = frame[mask & (frame["epoch"] >= eval_start)]
    return {
        "flagged": int(len(x)),
        "fraud": int(x["fraud"].sum()),
        "cleared": int(x["cleared"].sum()),
        "unlabeled": int((~x["fraud"] & ~x["cleared"]).sum()),
        "precision_pct": round(x["fraud"].mean() * 100, 1) if len(x) else None,
        "base_pct": round(base_rate * 100, 1),
        "lift": round(x["fraud"].mean() / base_rate, 2) if len(x) else None,
        "cleared_pct": round(x["cleared"].mean() * 100, 2) if len(x) else None,
    }


def auc(a, b):
    a, b = a.dropna(), b.dropna()
    if len(a) < 50 or len(b) < 50:
        return np.nan
    r = rankdata(np.concatenate([a, b]))
    return float((r[:len(a)].sum() - len(a) * (len(a) + 1) / 2) / (len(a) * len(b)))


def link_signal(sub, col, win_days, bins):
    """Another customer's confirmed-fraud case on the same entity `col`, closed within the prior `win_days`,
    stratified by the entity's as-of degree (distinct customers seen so far incl. self)."""
    d = sub[sub[col].notna()].sort_values(["epoch", "TransactionID"]).copy()
    d["first"] = ~d.duplicated([col, "customer_id"])
    d["deg"] = d.groupby(col)["first"].cumsum()
    F = d[d["fraud"] & d["close_ep"].notna()].groupby([col, "case_id"]).agg(
        cust=("customer_id", "first"), ce=("close_ep", "min")).reset_index()
    win = win_days * 86400
    Fg = {k: v for k, v in F.groupby(col)}
    cnt = np.zeros(len(d), int)
    for e_, idx in d.groupby(col).indices.items():
        if e_ not in Fg:
            continue
        f = Fg[e_]
        cl = np.sort(f["ce"].values)
        ep = d["epoch"].values[idx]
        c = np.searchsorted(cl, ep, side="right") - np.searchsorted(cl, ep - win, side="right")
        own = np.zeros(len(idx), int)
        cu_arr = d["customer_id"].values[idx]
        for cu, g in f.groupby("cust"):
            ce = np.sort(g["ce"].values)
            mk = cu_arr == cu
            if mk.any():
                own[mk] = np.searchsorted(ce, ep[mk], side="right") - np.searchsorted(ce, ep[mk] - win, side="right")
        cnt[idx] = c - own
    d["fc"] = cnt
    x = d[d["epoch"] >= EVAL_START_EPOCH]
    rows = []
    for ch in ["online", "in_person"]:
        xc = x[x["channel"] == ch]
        if xc.empty:
            continue
        base = xc["fraud"].mean()
        for lab, lo, hi in bins:
            y = xc[(xc["fc"] >= 1) & (xc["deg"] >= lo) & (xc["deg"] <= hi)]
            rows.append({"entity": col, "channel": ch, "deg": lab, "flagged": int(len(y)), "fraud": int(y["fraud"].sum()),
                         "cleared": int(y["cleared"].sum()),
                         "precision_pct": round(y["fraud"].mean() * 100, 1) if len(y) else None,
                         "base_pct": round(base * 100, 1),
                         "lift": round(y["fraud"].mean() / base, 2) if len(y) else None})
    return rows
