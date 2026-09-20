"""Phase 9A runner (trusted side): exercises every tool on representative HISTORICAL data and on the 20 benchmark cases.

The runner reads case_pack.csv (trigger metadata only: opened_at + ids) and hands each investigation an InvestigationSession whose as_of is
opened_at. The tools cannot see the case pack, other cases, or any label; no verdicts/probabilities are produced. Outputs go to
data/runs/phase9a/ (git-ignored; they describe benchmark cases and must NOT be given to the agent).

Usage: python src/fraud_tools/run_phase9a.py benchmark | historical
"""
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from fraud_tools.qclient import QueryClient  # noqa: E402
from fraud_tools.tools import InvestigationSession, epoch_of  # noqa: E402

OUT = ROOT / "data" / "runs" / "phase9a"
OUT.mkdir(parents=True, exist_ok=True)


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def benchmark():
    client = QueryClient()
    cp = pd.read_csv(ROOT / "case_pack.csv", dtype=str)
    rows, all_ms = [], []
    for r in cp.itertuples():
        as_of = epoch_of(r.opened_at)
        s = InvestigationSession(as_of, client)
        t0 = time.perf_counter()
        out = {"txn": s.get_transaction_context(r.flagged_txn_id), "customer_history": s.get_customer_history(r.customer_id),
               "card_history": s.get_card_history(r.card_id), "shared_devices": s.find_shared_devices(r.card_id),
               "connected": s.find_connected_entities(r.card_id), "prior_cases": s.find_prior_cases(r.card_id),
               "patterns": s.detect_fraud_patterns(r.flagged_txn_id)}
        out["policy"] = s.get_policy_context(out["patterns"]["fired"])
        total = time.perf_counter() - t0
        blob = json.dumps(out)
        (OUT / f"{r.case_id}_tools.json").write_text(blob, encoding="utf-8")
        leaks = [k for k in ("HHG-", "risk_score\":") if k in blob]
        all_ms += [x["ms"] for x in s.timings]
        p = out["patterns"]
        rows.append({"case": r.case_id, "opened": r.opened_at, "ch": out["txn"]["transaction"]["channel"][:2], "fired": ",".join(p["fired"]) or "-",
                     "srcT1_5": ",".join(p["independence"]["tier_1_to_5_sources"]) or "-", "prior": out["prior_cases"]["n_related_visible"],
                     "conn_cards": len(out["connected"]["connected_cards"]), "skipped": len(out["connected"]["skipped_hubs"]),
                     "queries": len(s.timings), "detect_s": round(p["latency_ms"] / 1000, 1), "total_s": round(total, 1), "leak_strings": ",".join(leaks) or "none"})
        print(rows[-1], flush=True)
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "benchmark_summary.csv", index=False)
    print("\nbenchmark: 20 cases;", "queries:", len(all_ms), "| per-query ms p50/p90/max:", pct(all_ms, .5), pct(all_ms, .9), max(all_ms),
          "| total per case s p50/max:", pct(df["total_s"].tolist(), .5), df["total_s"].max(), "| leak strings found:", (df["leak_strings"] != "none").sum())
    print("signal frequency over the 20 cases:", dict(Counter(x for f in df["fired"] for x in f.split(",") if x != "-")))


def historical(n_fraud=20, n_cleared=10, n_other=20):
    from common import EVAL_START_EPOCH, HIST_END_EPOCH, build_master
    client = QueryClient()
    m = build_master()
    jo = m[(m["epoch"] >= EVAL_START_EPOCH) & (m["epoch"] < HIST_END_EPOCH)]
    fr = jo[jo["fraud"]].sort_values("epoch").groupby("case_id").first().reset_index().sample(n_fraud, random_state=1)
    cl = jo[jo["cleared"]].sample(n_cleared, random_state=1)
    ot = jo[(~jo["fraud"]) & (~jo["cleared"]) & (jo["channel"] == "online")].sample(n_other, random_state=1)
    groups = {"confirmed_fraud_first_txn": fr, "cleared_alert": cl, "unlabeled_online": ot}
    freq, ms, rows = defaultdict(Counter), [], []
    for g, df in groups.items():
        for _, r in df.iterrows():
            s = InvestigationSession(int(r["epoch"]), client)             # as_of = the transaction's own time: nothing later is visible
            p = s.detect_fraud_patterns(str(int(r["TransactionID"])))
            for f in p["fired"]:
                freq[g][f] += 1
            ms.append(p["latency_ms"])
            rows.append({"group": g, "txn": int(r["TransactionID"]), "fired": ",".join(p["fired"]), "src": ",".join(p["independence"]["tier_1_to_5_sources"])})
    pd.DataFrame(rows).to_csv(OUT / "historical_summary.csv", index=False)
    for g, df in groups.items():
        print(g, len(df), "->", dict(freq[g]))
    print("detect_fraud_patterns latency ms p50/p90/max:", pct(ms, .5), pct(ms, .9), max(ms))


if __name__ == "__main__":
    {"benchmark": benchmark, "historical": historical}[sys.argv[1]]()
