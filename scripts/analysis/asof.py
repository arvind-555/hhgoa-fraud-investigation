"""As-of (point-in-time) primitives.

Two independent implementations of the same semantics:
  * device_pit()          incremental, vectorised: one row per identity-bearing txn with device state *at that txn*
  * *_oracle() / view()   brute force on a filtered view; the reference the incremental code (and later GSQL) must match

Visibility contract (docs/implementation_spec.md section 4):
  txn / edge visible at as_of      iff epoch <= as_of
  closed case + its labels         iff close_ep <= as_of      (NOT open_ep)
"""
import numpy as np
import pandas as pd


# ---------------------------------------------------------------- views (oracle side)
def view_txns(m, as_of):
    return m[m["epoch"] <= as_of]


def label_visible(df, as_of):
    """Rows whose case outcome may be used as of `as_of`."""
    return df["case_id"].notna() & (df["close_ep"] <= as_of)


def view_cases(cc, as_of):
    return cc[cc["close_ep"] <= as_of]


def assert_no_future(df, as_of, col="epoch"):
    mx = df[col].max() if len(df) else -1
    if mx > as_of:
        raise AssertionError(f"future data: max {col}={mx} > as_of={as_of}")
    return mx


def dev_stats_oracle(m, dev, as_of, exclude_customer=None):
    """Device state at as_of from a filtered view. `fraud_cust` counts customers (other than
    exclude_customer) with a confirmed-fraud txn on the device whose case is closed by as_of."""
    v = m[(m["dev"] == dev) & (m["epoch"] <= as_of)]
    n = len(v)
    fr = v[label_visible(v, as_of) & v["fraud"]]
    fc = set(fr["customer_id"]) - ({exclude_customer} if exclude_customer else set())
    return {
        "cust": int(v["customer_id"].nunique()),
        "txn": n,
        "new": float((v["id_15"] == "New").mean()) if n else 0.0,
        "proxy": float(v["id_23"].notna().mean()) if n else 0.0,
        "fraud_cust": len(fc),
    }


# ---------------------------------------------------------------- incremental device state
def device_pit(m, label_time="closed", max_epoch=None):
    """Device state at each identity-bearing transaction (inclusive of that txn).

    cum_txn, cum_cust (as-of degree), new_share, proxy_share, tpc, nk (known fields 0-4),
    fc = number of OTHER customers with a confirmed-fraud txn on the device whose case is visible
         (label_time='closed' -> close_ep <= epoch  [correct];  'opened' -> open_ep <= epoch  [leaky, for the leak test]).
    """
    d = m[m["dev"].notna()]
    if max_epoch is not None:
        d = d[d["epoch"] < max_epoch]
    d = d.sort_values(["dev", "epoch", "TransactionID"]).copy()
    d["nk"] = 4 - d["dev"].str.count(r"\?")
    g = d.groupby("dev", sort=False)
    d["cum_txn"] = g.cumcount() + 1
    d["_new"] = (d["id_15"] == "New").astype(int)
    d["_px"] = d["id_23"].notna().astype(int)
    d["_fc"] = (~d.duplicated(["dev", "customer_id"])).astype(int)
    d["cum_new"] = d.groupby("dev")["_new"].cumsum()
    d["cum_px"] = d.groupby("dev")["_px"].cumsum()
    d["cum_cust"] = d.groupby("dev")["_fc"].cumsum()
    d["new_share"] = d["cum_new"] / d["cum_txn"]
    d["proxy_share"] = d["cum_px"] / d["cum_txn"]
    d["tpc"] = d["cum_txn"] / d["cum_cust"]
    col = "close_ep" if label_time == "closed" else "open_ep"
    F = d[d["fraud"] & d[col].notna()].groupby(["dev", "customer_id"])[col].min().reset_index()
    Fg = {k: v for k, v in F.groupby("dev")}
    fc = np.zeros(len(d), dtype=int)
    pos = {lab: i for i, lab in enumerate(d.index)}
    for dev, idx in d.groupby("dev", sort=False).groups.items():
        if dev not in Fg:
            continue
        f = Fg[dev]
        cl = np.sort(f[col].values)
        rows = np.array([pos[i] for i in idx])
        ep = d["epoch"].values[rows]
        cnt = np.searchsorted(cl, ep, side="right")
        own = d.loc[idx, "customer_id"].map(dict(zip(f["customer_id"], f[col])))
        cnt = cnt - ((own.notna()) & (own <= d.loc[idx, "epoch"])).astype(int).values
        fc[rows] = cnt
    d["fc"] = fc
    return d.drop(columns=["_new", "_px", "_fc"])


def ring_flag(d, min_cust=3, new_share=0.9, proxy_share=0.5, min_known=None):
    """Compound device-anomaly rule evaluated per txn on a device_pit() frame. No labels, no risk score."""
    f = (d["cum_cust"] >= min_cust) & (d["new_share"] >= new_share) & (d["proxy_share"] >= proxy_share)
    if min_known is not None:
        f &= d["nk"] >= min_known
    return f


def device_state_at(d, dev, as_of):
    """Convenience: device state at as_of taken from a device_pit() frame (last row with epoch <= as_of)."""
    x = d[(d["dev"] == dev) & (d["epoch"] <= as_of)]
    return None if x.empty else x.iloc[-1]
