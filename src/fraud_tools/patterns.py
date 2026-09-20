"""The validated signals of docs/signal_validation.md as PURE functions (no I/O). No new fraud rules are invented here.

Inputs are the wrapper's cleaned dicts (sentinels already mapped to None). Nothing here reads risk_score, labels of the flagged txn,
or anything later than the session's as_of (the tools guarantee that upstream).
Ratings/tiers: S01 HIGH(2), S02a HIGH(3a), S02b MEDIUM(3b), S06 MEDIUM(4, policy-defined), S07 MEDIUM(4), S08 MEDIUM(5), S05/S09-S15 LOW(6).
"""
import math

HISTORY_START_EPOCH = 0                     # 2016-07-02 00:00:00 (first data)
WARMUP_S = 30 * 86400                       # S01 alerts suppressed during the first 30 days of graph history
RING_MIN_CUSTOMERS, RING_NEW_SHARE, RING_PROXY_SHARE = 3, 0.9, 0.5
STRONG_MAX_DEGREE, MODERATE_MAX_DEGREE = 5, 20
Z_THRESHOLD, Z_MIN_HIST, Z_VAR_FLOOR = 2.0, 20, 0.25
FAMILY_AMT = (400.0, 500.0)
IDENTITY_PROXY = ("IP_PROXY:ANONYMOUS", "IP_PROXY:HIDDEN")

META = {
    "S01": ("Shared-origin device ring (compound anomaly)", 2, "HIGH", "device_graph"),
    "S02a": ("Low-degree device (<=5 customers) shared with another customer's visible confirmed fraud", "3a", "HIGH", "device_graph"),
    "S02b": ("Device (6-20 customers) shared with another customer's visible confirmed fraud", "3b", "MEDIUM", "device_graph"),
    "S06": ("Policy R5 sequence: >=3 online txns < $5 within 1h before an online txn >= $20", 4, "MEDIUM", "card_sequence"),
    "S07": ("Burst family: ProductCD C, $400-500, >=1 prior same-card txn in 1h", 4, "MEDIUM", "card_sequence"),
    "S08": ("Amount anomaly: online z-score > 2 vs the card's own log-amount history (>=20 prior)", 5, "MEDIUM", "amount_history"),
    "S05": ("Repeat victim: visible prior confirmed case on the card or customer", 6, "LOW", "case_history"),
    "S09": ("New ProductCD for the card (>=20 prior txns)", 6, "LOW", "card_context"),
    "S10": ("Online id_15 = New", 6, "LOW", "identity_flags"),
    "S11": ("Online anonymous/hidden proxy", 6, "LOW", "identity_flags"),
    "S12": ("Online id_15 = New and a proxy", 6, "LOW", "identity_flags"),
    "S13": ("Online billing region (addr1) missing", 6, "LOW", "identity_flags"),
    "S14": ("Online with no identity record", 6, "LOW", "identity_flags"),
    "S15": ("In-person region differs from the card's modal in-person region (>=30 prior in-person)", 6, "LOW", "region_context"),
}


def _sig(sid, fired, detail, entity_ids=None):
    name, tier, rating, source = META[sid]
    return {"id": sid, "name": name, "tier": tier, "rating": rating, "source": source, "fired": bool(fired), "detail": detail, "entity_ids": entity_ids or []}


def sharing_tier(customers, blocked=False, is_null=False):
    """Validated degree tiers for sharing evidence (as-of distinct customers, incl. the card's own): <=5 strong, 6-20 moderate, >20 none."""
    if blocked or is_null:
        return "blocked"
    if customers <= STRONG_MAX_DEGREE:
        return "strong"
    if customers <= MODERATE_MAX_DEGREE:
        return "moderate"
    return "none"


def ring_suspect(customers, new_share, proxy_share, as_of, blocked=False, is_null=False):
    """S01 rule; label-free and risk-score-free. Suppressed while the graph history is younger than 30 days."""
    if blocked or is_null or as_of - HISTORY_START_EPOCH < WARMUP_S:
        return False
    return customers >= RING_MIN_CUSTOMERS and new_share >= RING_NEW_SHARE and proxy_share >= RING_PROXY_SHARE


def _later(a, b):
    """(epoch, txn_id) ordering: a strictly after b."""
    return (a["epoch"], a["txn_id"]) > (b["epoch"], b["txn_id"])


def r5_hit(txn, rows):
    """S06 evaluated at `txn`: online, amount >= 20, and >= 3 earlier online txns < $5 on the card within the previous 3600 s."""
    if txn["channel"] != "online" or txn["amount"] < 20:
        return 0, []
    small = [r for r in rows if r["channel"] == "online" and r["amount"] < 5 and _later(txn, r) and r["epoch"] >= txn["epoch"] - 3600]
    return len(small), [r["txn_id"] for r in small]


def family_hit(txn, rows):
    lo, hi = FAMILY_AMT
    if txn["product_cd"] != "C" or not lo <= txn["amount"] <= hi:
        return 0, []
    prior = [r for r in rows if r["product_cd"] == "C" and lo <= r["amount"] <= hi and _later(txn, r) and r["epoch"] >= txn["epoch"] - 3600]
    return len(prior), [r["txn_id"] for r in prior]


def amount_z(amount, baseline):
    """z = (ln(1+amt) - mu) / sqrt(var + 0.25) with mu/var from the card's PRIOR log-amounts (n >= 20); None otherwise."""
    n = baseline.get("n_base") or 0
    if n < Z_MIN_HIST:
        return None
    mu = baseline["sum_la"] / n
    var = max(baseline["sum_la2"] / n - mu * mu, 0.0)
    return (math.log1p(amount) - mu) / math.sqrt(var + Z_VAR_FLOOR)


def modal_region(baseline):
    h = baseline.get("in_person_region_hist") or {}
    if not h:
        return None, 0
    top = max(h.items(), key=lambda kv: (kv[1], -int(kv[0])))
    return int(top[0]), sum(h.values())


def evaluate(txn, rows, baseline, devices, prior_cases, as_of):
    """Evaluate every validated signal for the flagged `txn`. Returns the signal list (fired or not) — never a probability or verdict."""
    out = []
    # ---- S01 / S02 : device graph ---------------------------------------------------------------------------------
    txn_dev = txn.get("device_id")
    ring = [d for d in devices if d["ring_suspect"]]
    out.append(_sig("S01", bool(ring), {"devices": [{"device_id": d["device_id"], "customers": d["customers"], "new_share": round(d["new_share"], 3),
                                                       "proxy_share": round(d["proxy_share"], 3), "used_by_flagged_txn": d["device_id"] == txn_dev,
                                                       "other_cards": len(d["neighbours"])} for d in ring],
                                     "thresholds": {"customers>=": RING_MIN_CUSTOMERS, "new_share>=": RING_NEW_SHARE, "proxy_share>=": RING_PROXY_SHARE},
                                     "warmup_suppressed": as_of - HISTORY_START_EPOCH < WARMUP_S},
                    [d["device_id"] for d in ring]))
    dv = next((d for d in devices if d["device_id"] == txn_dev), None)
    fraud_c = dv["fraud_customers"] if dv else []
    for sid, tier in (("S02a", "strong"), ("S02b", "moderate")):
        fired = bool(dv) and dv["sharing_tier"] == tier and len(fraud_c) >= 1
        out.append(_sig(sid, fired, {"device_id": txn_dev, "customers": dv["customers"] if dv else None, "sharing_tier": dv["sharing_tier"] if dv else None,
                                     "other_customers_with_visible_confirmed_fraud": fraud_c, "cases": dv["fraud_cases"] if dv else []},
                        [txn_dev] if fired else []))
    # ---- S06 / S07 : sequences ------------------------------------------------------------------------------------
    n5, ids5 = r5_hit(txn, rows)
    later5 = []
    for r in rows:
        if _later(r, txn):
            n, ids = r5_hit(r, rows)
            if n >= 3:
                later5.append({"txn_id": r["txn_id"], "small_auths_in_prior_hour": n})
    out.append(_sig("S06", n5 >= 3 or bool(later5), {"flagged_txn_small_auths_in_prior_hour": n5, "small_txn_ids": ids5, "later_qualifying_txns_visible_by_as_of": later5,
                                                     "note": "policy-defined (R5); statistically unvalidated (7 historical hits)"}, ids5))
    nf, idsf = family_hit(txn, rows)
    out.append(_sig("S07", nf >= 1, {"prior_family_txns_in_prior_hour": nf, "txn_ids": idsf, "amount_band": list(FAMILY_AMT), "product_cd": "C"}, idsf))
    # ---- S08 : amount anomaly -------------------------------------------------------------------------------------
    z = amount_z(txn["amount"], baseline) if txn["channel"] == "online" else None
    out.append(_sig("S08", z is not None and z > Z_THRESHOLD, {"z": None if z is None else round(z, 3), "threshold": Z_THRESHOLD, "prior_txns": baseline.get("n_base"),
                                                              "applies_to": "online txns with >= 20 prior card txns"}))
    # ---- LOW context ----------------------------------------------------------------------------------------------
    confirmed = [c for c in prior_cases if c["outcome"] == "confirmed_fraud" and (set(c["match_reasons"]) & {"same_card", "same_customer"})]
    out.append(_sig("S05", bool(confirmed), {"cases": [c["case_id"] for c in confirmed], "note": "context: 47.8% of cleared alerts belong to customers that also have confirmed fraud"},
                    [c["case_id"] for c in confirmed]))
    online = txn["channel"] == "online"
    n_hist = baseline.get("n_base") or 0
    prods = baseline.get("product_hist") or {}
    out.append(_sig("S09", online and n_hist >= Z_MIN_HIST and txn["product_cd"] not in prods, {"product_cd": txn["product_cd"], "known_products": sorted(prods)}))
    new = txn.get("id_15") == "New"
    proxy = txn.get("proxy_type")
    out.append(_sig("S10", online and new, {"id_15": txn.get("id_15"), "note": "anti-informative alone (lift 0.74); 98% of cleared alerts are New"}))
    out.append(_sig("S11", online and proxy in IDENTITY_PROXY, {"proxy_type": proxy}))
    out.append(_sig("S12", online and new and bool(proxy), {"id_15": txn.get("id_15"), "proxy_type": proxy}))
    out.append(_sig("S13", online and txn.get("addr1") is None, {"addr1": txn.get("addr1")}))
    out.append(_sig("S14", online and not txn.get("has_identity"), {"has_identity": txn.get("has_identity")}))
    modal, n_ip = modal_region(baseline)
    out.append(_sig("S15", (not online) and txn.get("addr1") is not None and n_ip >= 30 and modal is not None and txn["addr1"] != modal,
                    {"addr1": txn.get("addr1"), "modal_in_person_region": modal, "prior_in_person_txns": n_ip,
                     "note": "76% of cleared alerts are also 'away' (travel); cannot separate them"}))
    return out


def independence(signals):
    """Independent evidence sources among FIRED signals (spec section 6.2): tier 1-5 sources are counted separately, LOW context is one bucket."""
    fired = [s for s in signals if s["fired"]]
    strong = sorted({s["source"] for s in fired if s["tier"] in (2, "3a", "3b", 4, 5)})
    low = sorted({s["source"] for s in fired if s["tier"] == 6})
    return {"tier_1_to_5_sources": strong, "n_tier_1_to_5_sources": len(strong), "low_context_sources": low,
            "note": "identity flags S10-S14 count as ONE source; low context alone must not raise probability above 0.50 (design guardrail)"}
