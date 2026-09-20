"""InvestigationSession — the eight read-only investigation tools (docs/investigation_tools.md).

The trusted runner constructs the session with `as_of` (= the case's opened_at as an epoch). The LLM-facing surface is the eight
public methods; they take ids and small bounded integers only. There is no as_of argument, no raw GSQL, no other graph.
Every result: sentinels -> None, snapshot hub classes removed (only shown inside `skipped_hubs` reasons), leak-checked against as_of.
"""
import copy
import threading
from concurrent.futures import Future, ThreadPoolExecutor
import math
import time
from datetime import datetime, timedelta

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from . import patterns, policy  # noqa: E402
from .guards import FutureTransactionError, LeakError, ToolError, assert_visible
from .qclient import QueryClient
from .sentinels import clean_value

T0 = datetime(2016, 7, 2)
MAX_CUSTOMERS = 100                       # spec section 5.1: expansion cut-off (as-of degree)
LINK_MAX_DEGREE = 20                      # email/region links and case-sharing devices: last tier with validated evidence
BASELINE_LOOKBACK_S = 200 * 86400         # covers the whole dataset (Jul 2 - Dec 31)
FEATURES = ["c1", "c2", "c4", "c8", "c10", "d1", "d2", "d3", "d4", "d5", "d10", "d15", "v51", "v52", "v79", "v93", "v94", "v217", "v258", "v264", "v308"]
MFLAGS = [f"m{i}" for i in range(1, 10)]


def _rag():
    """GraphRAG retrieval helpers (src/rag). Imported lazily so the deterministic layer has no hard dependency on that package."""
    from rag import retrieve
    return retrieve


def epoch_of(ts):
    """'YYYY-MM-DD HH:MM:SS' -> epoch seconds since 2016-07-02 00:00:00 (runner helper)."""
    return int((datetime.strptime(ts, "%Y-%m-%d %H:%M:%S") - T0).total_seconds())


def ts_of(epoch):
    return (T0 + timedelta(seconds=int(epoch))).strftime("%Y-%m-%d %H:%M:%S")


def _int_keys(m):
    return {int(k): v for k, v in (m or {}).items()}


class InvestigationSession:
    def __init__(self, as_of_epoch, client=None, chunk_cache=None, chunk_frontier=None):
        if not isinstance(as_of_epoch, int) or as_of_epoch <= 0:
            raise ToolError("as_of must be a positive integer epoch")
        self._as_of = as_of_epoch
        self._client = client or QueryClient()
        self.timings = []
        self.cache_hits = 0
        self._cache = {}                     # (query, params) -> Future; as_of is fixed per session and the graph is static, so results are safe to reuse
        self._lock = threading.Lock()
        # optional cross-case cache of text chunks: policy chunks (valid_from 0) always; closed-case chunks only when as_of >= chunk_frontier (the last chunk's valid_from,
        # a static property of the knowledge base): then every chunk is visible and the visible set cannot differ between cases.
        self._chunk_cache, self._chunk_frontier = chunk_cache, chunk_frontier

    # ------------------------------------------------------------------------------------------ plumbing
    @property
    def as_of(self):
        return self._as_of

    def _q(self, tool, name, **params):
        key = (name, tuple(sorted(params.items())))
        with self._lock:
            fut = self._cache.get(key)
            mine = fut is None
            if mine:
                fut = self._cache[key] = Future()
            else:
                self.cache_hits += 1
        if not mine:
            return copy.deepcopy(fut.result())          # concurrent identical calls wait for the first one (single flight)
        try:
            t0 = time.perf_counter()
            r = self._client.run(name, **params)
            dt = (time.perf_counter() - t0) * 1000
            mx = r.get("max_epoch_seen")
            if isinstance(mx, int) and mx > self._as_of:
                raise LeakError(f"{name}: max_epoch_seen {mx} > as_of {self._as_of}")
            with self._lock:
                self.timings.append({"tool": tool, "query": name, "ms": round(dt, 1)})
            fut.set_result(r)
        except BaseException as e:
            with self._lock:
                self._cache.pop(key, None)              # failures and leaks are never cached
            fut.set_exception(e)
            raise
        return copy.deepcopy(r)

    def _finish(self, tool, payload, t_start, max_epoch=None):
        payload = {"tool": tool, "as_of": self._as_of, "max_epoch_seen": max_epoch, **payload}
        assert_visible(payload, self._as_of)
        payload["latency_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
        return payload

    # ------------------------------------------------------------------------------------------ 1. transaction context
    def get_transaction_context(self, txn_id):
        t0 = time.perf_counter()
        r = self._q("get_transaction_context", "fi_txn_context", txn=str(txn_id), as_of=self._as_of)
        if not r.get("visible"):
            raise FutureTransactionError(f"transaction {txn_id} is not visible at the session's as_of (unknown or later)")
        v = r["V"][0]
        card = (r.get("C") or [None])[0]
        cust = (r.get("U") or [None])[0]
        dev = (r.get("D") or [None])[0]
        pe, re_, br = (r.get("PE") or [None])[0], (r.get("RE") or [None])[0], (r.get("BR") or [None])[0]
        ep = int(v["epoch"])
        prev = (r.get("PV") or [None])[0]
        nxt = (r.get("NX") or [None])[0]
        prev_gap = _int_keys(r.get("prev_gap"))
        next_gap = _int_keys(r.get("next_gap"))
        txn = {
            "txn_id": int(v["txn_id"]), "epoch": ep, "ts": v["ts"], "amount": float(v["amount"]), "product_cd": v["product_cd"], "channel": v["channel"],
            "card_id": v["card_id"], "customer_id": v["customer_id"],
            "addr1": clean_value("addr1", v["addr1"]), "addr2": clean_value("addr2", v["addr2"]),
            "dist1": clean_value("dist1", v["dist1"]), "dist2": clean_value("dist2", v["dist2"]),
            "has_identity": bool(v["has_identity"]), "id_15": clean_value("id_15", v["id_15"]), "proxy_type": clean_value("proxy_type", v["proxy_type"]),
            "device_type": clean_value("device_type", v["device_type"]), "purchaser_email": clean_value("p_email", v["p_email"]),
            "recipient_email": clean_value("r_email", v["r_email"]),
        }
        feats = {k: clean_value(k, v[k]) for k in FEATURES}
        feats.update({k: clean_value(k, v[k]) for k in MFLAGS})
        temporal = {
            "seconds_before_as_of": self._as_of - ep, "hour_of_day": (ep % 86400) // 3600, "weekday": (5 + ep // 86400) % 7,
            "seconds_since_previous_txn_on_card": (next(iter(prev_gap.values())) if prev_gap else None),
            "previous_txn": ({"txn_id": int(prev["txn_id"]), "epoch": int(prev["epoch"]), "amount": float(prev["amount"]), "product_cd": prev["product_cd"], "channel": prev["channel"]} if prev else None),
            "next_txn_visible_at_as_of": ({"txn_id": int(nxt["txn_id"]), "epoch": int(nxt["epoch"]), "amount": float(nxt["amount"]), "product_cd": nxt["product_cd"],
                                           "channel": nxt["channel"], "gap_s": next(iter(next_gap.values()), None)} if nxt else None),
        }
        payload = {
            "transaction": txn,
            "card": ({"card_id": card["card_id"], "customer_id": card["customer_id"], "card_k": card["card_k"], "card4": clean_value("card4", card["card4"]),
                      "card6": clean_value("card6", card["card6"]), "card2": clean_value("card2", card["card2"]), "card3": clean_value("card3", card["card3"]),
                      "card5": clean_value("card5", card["card5"]), "is_null_type": bool(card["is_null_type"]), "first_epoch": card["first_epoch"]} if card else None),
            "customer": ({"customer_id": cust["customer_id"], "first_epoch": cust["first_epoch"]} if cust else None),
            "device": ({"device_id": dev["device_id"], "profile_str": dev["profile_str"], "device_info": clean_value("x", dev["device_info"]), "os": clean_value("x", dev["os"]),
                        "browser": clean_value("x", dev["browser"]), "screen": clean_value("x", dev["screen"]), "n_known": dev["n_known"],
                        "is_null_profile": bool(dev["is_null_profile"])} if dev else None),
            "purchaser_email": ({"domain": pe["domain"], "is_anonymizer": bool(pe["is_anonymizer"])} if pe else None),
            "recipient_email": ({"domain": re_["domain"], "is_anonymizer": bool(re_["is_anonymizer"])} if re_ else None),
            "billing_region": ({"addr1": br["addr1"], "country": clean_value("addr2", br["country"])} if br else None),
            "temporal": temporal,
            "model_features": {"values": feats, "note": "unnamed model features (C/D/M/V); temporal provenance unknown (spec S16); context only"},
            "model_score": {"value": float(v["risk_score"]), "note": "bank model score: an input, never a verdict; not used to filter or rank anything"},
        }
        return self._finish("get_transaction_context", payload, t0, r.get("max_epoch_seen"))

    # ------------------------------------------------------------------------------------------ 2. customer history
    def get_customer_history(self, customer_id, lookback_days=200):
        t0 = time.perf_counter()
        r = self._q("get_customer_history", "fi_customer_history", cust=customer_id, as_of=self._as_of, lookback_s=int(lookback_days) * 86400, max_cases=50)
        n = r.get("n_txns", 0)
        cards = [{"card_id": c["card_id"], "card4": clean_value("card4", c["card4"]), "card6": clean_value("card6", c["card6"]), "is_null_type": bool(c["is_null_type"]),
                  "first_epoch": c["first_epoch"], "n_txns": c["n_txns"], "sum_amount": round(c["sum_amt"], 2), "max_amount": c["max_amt"] if c["n_txns"] else None,
                  "first_txn_epoch": c["first_txn_epoch"] if c["n_txns"] else None, "last_txn_epoch": c["last_txn_epoch"] if c["n_txns"] else None}
                 for c in r.get("Cd", [])]
        cases = [self._case(c) for c in r.get("Cs", [])]
        payload = {"customer_id": customer_id, "lookback_days": lookback_days,
                   "spend": {"n_txns": n, "sum_amount": round(r.get("sum_amt", 0.0), 2), "mean_amount": round(r["sum_amt"] / n, 2) if n else None,
                             "first_txn_epoch": r.get("first_epoch") if n else None, "last_txn_epoch": r.get("last_epoch") if n else None,
                             "product_hist": r.get("product_hist", {}), "channel_hist": r.get("channel_hist", {}), "n_devices": r.get("n_devices", 0)},
                   "cards": cards,
                   "visible_closed_cases": {"total_visible": r.get("n_cases_visible", 0), "confirmed_fraud": r.get("n_confirmed_visible", 0), "cleared": r.get("n_cleared_visible", 0),
                                            "returned": len(cases), "cases": cases,
                                            "note": "Closed cases are history/context visible by close_epoch <= as_of; they are not proof for a new alert."}}
        return self._finish("get_customer_history", payload, t0, r.get("max_epoch_seen"))

    @staticmethod
    def _case(c):
        return {"case_id": c["case_id"], "customer_id": c.get("customer_id"), "card_id": c["card_id"], "open_epoch": c["open_epoch"], "close_epoch": c["close_epoch"],
                "outcome": c["outcome"], "pattern": c["pattern"], "n_txns": c["n_txns"], "exposure_usd": c["exposure_usd"], "report_filed": bool(c["report_filed"]),
                "actions_taken": [a for a in c["actions_taken"].split("|") if a], "first_fraud_txn_id": (c["first_fraud_txn_id"] or None),
                "device_text": c.get("device_text") or None, "analyst_notes": c["analyst_notes"]}

    # ------------------------------------------------------------------------------------------ 3. card history
    def _card_history_raw(self, card_id, before_epoch, row_window_s, max_rows, tool="get_card_history"):
        return self._q(tool, "fi_card_history", card=card_id, as_of=self._as_of, before_epoch=before_epoch, baseline_lookback_s=BASELINE_LOOKBACK_S,
                       row_window_s=row_window_s, max_rows=max_rows)

    @staticmethod
    def _baseline(r):
        n = r.get("n_base", 0)
        mu = r["sum_la"] / n if n else None
        var = max(r["sum_la2"] / n - mu * mu, 0.0) if n else None
        ipr = _int_keys(r.get("in_person_region_hist"))
        modal, n_ip = patterns.modal_region({"in_person_region_hist": ipr})
        return {"n_base": n, "sum_la": r.get("sum_la", 0.0), "sum_la2": r.get("sum_la2", 0.0),
                "mean_amount": round(r["sum_amt"] / n, 2) if n else None, "min_amount": r["min_amt"] if n else None, "max_amount": r["max_amt"] if n else None,
                "mean_log_amount": mu, "sd_log_amount": math.sqrt(var) if n else None,
                "first_epoch": r["first_epoch"] if n else None, "last_epoch": r["last_epoch"] if n else None,
                "product_hist": r.get("product_hist", {}), "channel_hist": r.get("channel_hist", {}), "hour_hist": _int_keys(r.get("hour_hist")),
                "region_hist": _int_keys(r.get("region_hist")), "in_person_region_hist": ipr,
                "modal_in_person_region": modal, "modal_in_person_share": round(ipr[modal] / n_ip, 3) if modal is not None and n_ip else None,
                "prior_in_person_txns_with_region": n_ip, "devices_first_seen": r.get("device_first_seen", {})}

    def _rows(self, r):
        dev = {int(k): v for k, v in (r.get("row_device") or {}).items()}
        out = []
        for x in r.get("R", []):
            tid = int(x["txn_id"])
            out.append({"txn_id": tid, "epoch": int(x["epoch"]), "amount": float(x["amount"]), "product_cd": x["product_cd"], "channel": x["channel"],
                        "addr1": clean_value("addr1", x["addr1"]), "addr2": clean_value("addr2", x["addr2"]), "has_identity": bool(x["has_identity"]),
                        "id_15": clean_value("id_15", x["id_15"]), "proxy_type": clean_value("proxy_type", x["proxy_type"]), "device_type": clean_value("device_type", x["device_type"]),
                        "purchaser_email": clean_value("p_email", x["p_email"]), "recipient_email": clean_value("r_email", x["r_email"]), "device_id": dev.get(tid)})
        return out

    def get_card_history(self, card_id, hours=48, max_rows=100):
        t0 = time.perf_counter()
        r = self._card_history_raw(card_id, self._as_of + 1, int(hours) * 3600, int(max_rows))
        rows = self._rows(r)
        payload = {"card_id": card_id, "window_hours": hours, "n_txns_total_visible": r.get("n_total", 0), "n_txns_in_window": r.get("n_window", 0),
                   "rows_returned": len(rows), "truncated": r.get("n_window", 0) > len(rows), "rows": rows, "baseline": self._baseline(r),
                   "note": "rows are newest first; baseline covers all visible history of the card; sentinel values are null"}
        return self._finish("get_card_history", payload, t0, r.get("max_epoch_seen"))

    # ------------------------------------------------------------------------------------------ 4. shared devices
    def find_shared_devices(self, card_id, days=30):
        t0 = time.perf_counter()
        r = self._q("find_shared_devices", "fi_shared_devices", card=card_id, as_of=self._as_of, days=int(days), max_customers=MAX_CUSTOMERS)
        payload = self._shared_payload(card_id, days, r)
        return self._finish("find_shared_devices", payload, t0, r.get("max_epoch_seen"))

    def _shared_payload(self, card_id, days, r):
        skipped_reason = r.get("skipped_reason") or {}
        skipped_degree = r.get("skipped_degree") or {}
        fraud_c, fraud_k = r.get("fraud_customers") or {}, r.get("fraud_cases") or {}
        nb = {}
        for e in r.get("neighbours") or []:
            nb.setdefault(e["dev"], []).append({"card_id": e["nb_card"], "customer_id": e["nb_cust"], "n_txns": e["n"], "first_epoch": e["first_ep"], "last_epoch": e["last_ep"]})
        devices, skipped = [], []
        for d in r.get("Dv", []):
            did = d["device_id"]
            reason = skipped_reason.get(did)
            blocked = reason is not None
            customers = skipped_degree.get(did, d["customers"]) if blocked else d["customers"]
            txns = d["txns"]
            new_share = d["new_txns"] / txns if txns else 0.0
            proxy_share = d["proxy_txns"] / txns if txns else 0.0
            is_null = bool(d["is_null_profile"])
            tier = patterns.sharing_tier(customers, blocked=blocked, is_null=is_null)
            all_nb = sorted(nb.get(did, []), key=lambda x: (-x["n_txns"], x["card_id"]))
            neighbours = all_nb[:25]
            if blocked:
                skipped.append({"entity_type": "device", "entity_id": did, "reason": reason, "as_of_customers": skipped_degree.get(did)})
            devices.append({
                "device_id": did, "profile_str": d["profile_str"], "n_known": d["n_known"], "is_null_profile": is_null,
                "own_txns": d["own_txns"], "own_first_epoch": d["own_first"], "own_last_epoch": d["own_last"],
                "customers": customers if not blocked or did in skipped_degree else None, "txns": txns, "new_share": round(new_share, 4), "proxy_share": round(proxy_share, 4),
                "blocked": blocked, "blocked_reason": reason, "sharing_tier": tier,
                "ring_suspect": patterns.ring_suspect(customers or 0, new_share, proxy_share, self._as_of, blocked=blocked, is_null=is_null),
                "fraud_customers": sorted(fraud_c.get(did, [])), "fraud_cases": sorted(fraud_k.get(did, [])), "neighbours": neighbours,
                "neighbours_total": len(all_nb), "neighbours_truncated": len(all_nb) > len(neighbours),
                "neighbours_window_days": days})
        return {"card_id": card_id, "customer_id": r.get("own_customer"), "devices": devices, "skipped_hubs": skipped,
                "max_customers": MAX_CUSTOMERS,
                "note": "customers counts distinct customers as of as_of (including the card's own). Tiers: <=5 strong, 6-20 moderate, >20 none (no evidence); "
                        "NULL/HUB/over-degree devices are skipped and never linked."}

    # ------------------------------------------------------------------------------------------ 5. connected entities
    def find_connected_entities(self, card_id, days=30):
        t0 = time.perf_counter()
        rd = self._q("find_connected_entities", "fi_shared_devices", card=card_id, as_of=self._as_of, days=int(days), max_customers=MAX_CUSTOMERS)
        re_ = self._q("find_connected_entities", "fi_connected_entities", card=card_id, as_of=self._as_of, days=int(days), max_degree=LINK_MAX_DEGREE)
        sp = self._shared_payload(card_id, days, rd)
        links, cards = [], {}
        for d in sp["devices"]:
            if d["blocked"]:
                continue
            strength = "ring_suspect" if d["ring_suspect"] else {"strong": "strong", "moderate": "moderate", "none": "weak_not_evidence"}[d["sharing_tier"]]
            for n in d["neighbours"]:
                links.append({"link_type": "device", "entity_id": d["device_id"], "as_of_entity_customers": d["customers"], "strength": strength, **n})
        for e in re_.get("neighbours") or []:
            links.append({"link_type": e["kind"], "entity_id": e["ent"], "strength": "low_context", "card_id": e["nb_card"], "customer_id": e["nb_cust"],
                          "n_txns": e["n"], "first_epoch": e["first_ep"], "last_epoch": e["last_ep"]})
        for k in links:
            cards.setdefault(k["card_id"], {"card_id": k["card_id"], "customer_id": k["customer_id"], "links": set()})["links"].add(f"{k['link_type']}:{k['entity_id']}:{k['strength']}")
        connected = sorted(({**v, "links": sorted(v["links"])} for v in cards.values()), key=lambda x: (-len(x["links"]), x["card_id"]))[:100]
        skipped = list(sp["skipped_hubs"])
        for key, reason in (re_.get("skipped_reason") or {}).items():
            kind, ent = key.split(":", 1)
            skipped.append({"entity_type": kind, "entity_id": ent, "reason": reason, "as_of_customers": (re_.get("skipped_degree") or {}).get(key)})

        def ent_rows(rows, id_key, kind):
            return [{"entity_type": kind, "entity_id": str(x[id_key]), "as_of_customers": x["asof_customers"], "own_txns": x["own_txns"]} for x in rows or []]
        payload = {"card_id": card_id, "days": days, "connected_cards": connected, "links": links[:400],
                   "entities_touched": {"devices": [{"device_id": d["device_id"], "customers": d["customers"], "sharing_tier": d["sharing_tier"], "ring_suspect": d["ring_suspect"]} for d in sp["devices"]],
                                        "recipient_emails": ent_rows(re_.get("RE"), "domain", "recipient_email"),
                                        "purchaser_emails": ent_rows(re_.get("PE"), "domain", "purchaser_email"),
                                        "regions": ent_rows(re_.get("BR"), "addr1", "region")},
                   "skipped_hubs": skipped,
                   "note": "Bounded expansion; hub/generic entities are listed in skipped_hubs and never used as links. Email/region links are LOW context and need corroboration."}
        mx = max(x for x in (rd.get("max_epoch_seen"), re_.get("max_epoch_seen")) if x is not None)
        return self._finish("find_connected_entities", payload, t0, mx)

    # ------------------------------------------------------------------------------------------ 6. prior cases
    def find_prior_cases(self, card_id):
        t0 = time.perf_counter()
        r = self._q("find_prior_cases", "fi_prior_cases", card=card_id, as_of=self._as_of, max_cases=50, max_degree=LINK_MAX_DEGREE)
        ctx = r.get("case_txns") or {}
        dev = r.get("case_devices") or {}
        cases = []
        for c in r.get("Top", []):
            base = self._case(c)
            base.update({"customer_id": c["customer_id"], "match_reasons": sorted(c["match_reasons"]),
                         "transaction_ids": sorted(ctx.get(c["case_id"], [])), "device_ids": sorted(dev.get(c["case_id"], []))})
            cases.append(base)
        from collections import Counter
        payload = {"card_id": card_id, "n_related_visible": r.get("n_related_visible", 0), "returned": len(cases), "cases": cases,
                   "summary": {"by_outcome": dict(Counter(c["outcome"] for c in cases)), "by_pattern": dict(Counter(c["pattern"] for c in cases)),
                               "by_match": dict(Counter(m.split(":")[0] for c in cases for m in c["match_reasons"]))},
                   "note": "Only closed cases with close_epoch <= as_of. shared_device matches require an ELIGIBLE device (<= 20 as-of customers, not NULL/HUB). "
                           "Case history is context and memory, not proof."}
        return self._finish("find_prior_cases", payload, t0, r.get("max_epoch_seen"))

    # ------------------------------------------------------------------------------------------ 7. pattern detection
    def detect_fraud_patterns(self, txn_id):
        t0 = time.perf_counter()
        n0 = len(self.timings)
        ctx = self.get_transaction_context(txn_id)
        txn = ctx["transaction"]
        txn["device_id"] = (ctx["device"] or {}).get("device_id")
        card_id = txn["card_id"]
        window = (self._as_of - txn["epoch"]) + 3600 + 1
        with ThreadPoolExecutor(3) as ex:                # the three graph reads are independent
            fh = ex.submit(self._card_history_raw, card_id, txn["epoch"], window, 500, "detect_fraud_patterns")
            fs = ex.submit(self._q, "detect_fraud_patterns", "fi_shared_devices", card=card_id, as_of=self._as_of, days=30, max_customers=MAX_CUSTOMERS)
            fp = ex.submit(self.find_prior_cases, card_id)
            h, prior = fh.result(), fp.result()
            shared = self._shared_payload(card_id, 30, fs.result())
        rows, baseline = self._rows(h), self._baseline(h)
        signals = patterns.evaluate(txn, rows, baseline, shared["devices"], prior["cases"], self._as_of)
        notes = []
        if h.get("n_window", 0) > len(rows):
            notes.append("card history rows were truncated at 500; sequence signals may be incomplete")
        td = next((d for d in shared["devices"] if d["device_id"] == txn["device_id"]), None)
        if td and td["blocked"]:
            notes.append(f"device of the flagged txn is a skipped hub ({td['blocked_reason']}): ignored as evidence")
        if not txn["has_identity"] and txn["channel"] == "online":
            notes.append("online transaction without an identity record: no device evidence exists")
        payload = {"txn_id": txn["txn_id"], "card_id": card_id, "customer_id": txn["customer_id"], "signals": signals,
                   "fired": [s["id"] for s in signals if s["fired"]], "independence": patterns.independence(signals),
                   "skipped_hubs": shared["skipped_hubs"], "notes": notes,
                   "not_evaluated": ["S03/S04 region/email links (LOW, reported by find_connected_entities)", "S16 model features (context in get_transaction_context)",
                                     "S17 risk_score (input only, never a signal)", "S18 similar cases (GraphRAG, deferred)", "S19 customer response (simulated later)"],
                   "no_probability": "No probability or verdict is produced here: calibration is an open item (spec B3).",
                   "queries_used": len(self.timings) - n0}
        return self._finish("detect_fraud_patterns", payload, t0, max(h.get("max_epoch_seen") or 0, ctx["max_epoch_seen"] or 0))

    def _chunks(self, tool, doc_type, channel, max_chunks):
        cacheable = self._chunk_cache is not None and (doc_type != "closed_case" or (self._chunk_frontier is not None and self._as_of >= self._chunk_frontier))
        key = (doc_type, channel)
        if cacheable:
            with self._lock:
                hit = self._chunk_cache.get(key)
            if hit is not None:
                self.cache_hits += 1
                return copy.deepcopy(hit)
        r = self._q(tool, "fi_text_chunks", as_of=self._as_of, doc_type=doc_type, channel=channel, max_chunks=max_chunks)
        if cacheable:
            with self._lock:
                self._chunk_cache[key] = copy.deepcopy(r)
        return r

    # ------------------------------------------------------------------------------------------ 9. GraphRAG: similar closed cases
    def find_similar_cases(self, txn_id, k=10):
        """Closed cases that resemble this transaction: structured seeds (same card/customer/eligible device -> `linked`) plus lexical BM25 precedents.
        Only closed cases visible at as_of (close_epoch <= as_of). Context and memory, not proof; the query uses no score and no label."""
        t0 = time.perf_counter()
        rag = _rag()
        ctx = self.get_transaction_context(txn_id)
        txn = ctx["transaction"]
        with ThreadPoolExecutor(1) as ex:                    # the chunk fetch is the slow part: run it while the pattern/prior queries run
            fut = ex.submit(self._chunks, "find_similar_cases", "closed_case", txn["channel"], 8000)
            det = self.detect_fraud_patterns(txn_id)
            prior = self.find_prior_cases(txn["card_id"])
            r = fut.result()
        rows = r.get("S", [])
        seeds = {c["case_id"]: c["match_reasons"] for c in prior["cases"] if c["match_reasons"]}
        query = rag.build_query(txn["channel"], det["fired"])
        hits = rag.similar_cases(rows, seeds, query, int(k))
        payload = {"txn_id": txn["txn_id"], "channel": txn["channel"], "n_visible_case_chunks": len(rows), "query_terms": query, "hits": hits,
                   "summary": {"linked": sum(1 for h in hits if h["label"] == "linked"), "precedent": sum(1 for h in hits if h["label"] == "precedent")},
                   "note": "Closed-case notes are templated wording/base-rate context, never a label for this case. `linked` = shares the card, customer or an ELIGIBLE device; "
                           "`precedent` = textual resemblance only. Only cases closed by as_of are visible."}
        return self._finish("find_similar_cases", payload, t0, r.get("max_epoch_seen"))

    def retrieve_policy(self, rule_ids):
        """Fraud Policy / pattern text chunks by exact reference (R1-R10, 3a, 3b, routes). Policy text, not evidence."""
        t0 = time.perf_counter()
        rag = _rag()
        r = self._chunks("retrieve_policy", "policy", "", 100)
        chunks = rag.policy_chunks(r.get("S", []), list(rule_ids))
        payload = {"kind": "policy_text_not_evidence", "requested": list(rule_ids), "chunks": chunks, "missing": sorted(set(rule_ids) - {c["source_ref"].split(":")[1] for c in chunks})}
        return self._finish("retrieve_policy", payload, t0, r.get("max_epoch_seen"))

    # ------------------------------------------------------------------------------------------ 8. policy
    def get_policy_context(self, fired_signals=None):
        t0 = time.perf_counter()
        return self._finish("get_policy_context", policy.policy_context(fired_signals), t0)
