"""Phase 9B runner (trusted side). Runs the agent on HISTORICAL, NON-BENCHMARK transactions only.

The runner (not the agent) picks the transaction, fixes as_of = the transaction's own epoch, builds the gateway and hands the agent a Trigger with a synthetic
case id. Historical group labels (confirmed fraud / cleared / unlabeled) are used ONLY in this runner's report to sanity-check behaviour; the agent never sees them.
Synthetic evidence responders (test doubles, cycled independent of any label) exercise the additional-evidence loop.

Usage: python src/agent/run_phase9b.py [n_fraud n_cleared n_other]
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from agent import answer_file as AF  # noqa: E402
from agent.casestore import LocalCaseStore  # noqa: E402
from agent.graphio import GraphIO  # noqa: E402
from calibration.runtime import Calibrator  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from agent.simulator import DEFAULT_SEED, EvidenceSimulator  # noqa: E402
from fraud_tools.qclient import QueryClient  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402

OUT = ROOT / "data" / "runs" / "phase9b"


class ScriptedResponder:
    """Test double: returns a fixed outcome. NOT the customer simulator (blocker B4) and never reads labels."""
    def __init__(self, outcome):
        self.outcome = outcome

    def respond(self, request, case_ctx):
        return self.outcome, {"denied": "Customer states they did not make the purchase.", "confirmed": "Customer confirms the purchase.",
                              "no_reply": "No reply received within 24 hours.", "pending": ""}[self.outcome]


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def main(n_fraud=10, n_cleared=5, n_other=10):
    from common import EVAL_START_EPOCH, HIST_END_EPOCH, build_master
    m = build_master()
    jo = m[(m["epoch"] >= EVAL_START_EPOCH) & (m["epoch"] < HIST_END_EPOCH)]
    fr = jo[jo["fraud"]].sort_values("epoch").groupby("case_id").first().reset_index().sample(n_fraud, random_state=7)
    cl = jo[jo["cleared"]].sample(n_cleared, random_state=7)
    ot = jo[(~jo["fraud"]) & (~jo["cleared"])].sample(n_other, random_state=7)
    groups = [("confirmed_fraud_first_txn", fr), ("cleared_alert", cl), ("unlabeled", ot)]
    client = QueryClient()
    io = GraphIO()
    store = LocalCaseStore(OUT / "cases")
    sim = EvidenceSimulator(DEFAULT_SEED)      # deterministic, seeded; answers only requests issued by the agent; never reads labels
    rows, k = [], 0
    for g, df in groups:
        for _, r in df.iterrows():
            k += 1
            as_of = int(r["epoch"])
            sess = InvestigationSession(as_of, client)
            gw = ToolGateway(sess)
            trig = Trigger(case_id=f"SYN-{k:03d}", txn_id=str(int(r["TransactionID"])), trigger_type="risk_score")
            t0 = time.perf_counter()
            try:
                rec = Agent(gw, trig, responder=sim, store=store, calibrator=Calibrator(as_of)).run()
            except Exception as e:
                rows.append({"case": trig.case_id, "group": g, "error": f"{type(e).__name__}: {str(e)[:120]}"})
                print(rows[-1], flush=True)
                continue
            dt = time.perf_counter() - t0
            ans = AF.assemble(rec)
            aerr, awarn = AF.validate_answer(ans, as_of, io, {"tool_calls": rec["tool_calls"], "tokens": rec["tokens"], "latency_s": rec["latency_s"]})
            if not aerr:
                AF.write_answer(ans, OUT / "answers", as_of, io)
            blob = json.dumps(rec)
            c = rec["case"]
            rows.append({"case": trig.case_id, "group": g, "strength": rec["uncertainty"]["evidence_strength"], "unc": rec["uncertainty"]["level"], "verdict": c["verdict"],
                         "p": c["fraud_probability"], "status": c["status"], "pattern": c["pattern"], "req": (rec["evidence_requests"] or [{}])[0].get("type", "-"),
                         "responder": (rec["evidence_requests"] or [{}])[0].get("outcome", "-"), "initial": "|".join(a["action"] for a in rec["next_best_actions"]["initial"]),
                         "final": "|".join(a["action"] for a in rec["next_best_actions"]["final"]), "exposure": c["exposure_usd"], "other_card_txns": rec["exposure_scope"]["other_card_txns"], "scope_complete": rec["exposure_scope"]["complete"], "tool_calls": rec["tool_calls"],
                         "graph_q": rec["gateway"]["graph_queries_executed"], "cache_hits": rec["gateway"]["cache_hits"], "latency_s": round(dt, 2),
                         "answer_errors": len(aerr), "sar": rec["sar"]["file"], "docs": sum(e["source"] == "document" for e in rec["case"]["evidence"]), "leak": any(x in blob for x in ("risk_score\":", "HHG-", "snap_class", "model_score"))})
            print(rows[-1], flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "summary.csv", index=False)
    ok = df[df.get("error").isna()] if "error" in df else df
    print("\ncases:", len(df), "errors:", len(df) - len(ok))
    print("latency_s p50/p90/max:", pct(ok["latency_s"].tolist(), .5), pct(ok["latency_s"].tolist(), .9), ok["latency_s"].max())
    print("graph queries per case p50/max:", pct(ok["graph_q"].tolist(), .5), ok["graph_q"].max(), "| logical tool calls p50:", pct(ok["tool_calls"].tolist(), .5))
    print("strength by group:\n", ok.groupby(["group", "strength"]).size().to_string())
    print("initial first action:", dict(Counter(x.split("|")[0] for x in ok["initial"])))
    print("leaks:", int(ok["leak"].sum()))


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:4]]
    main(*a)
