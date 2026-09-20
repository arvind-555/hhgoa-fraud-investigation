"""Live integration tests of the Phase 9B agent on HISTORICAL (non-benchmark) transactions. Read-only against FraudInvestigation.
Skipped when .env / installed queries are unavailable. Run: python -m unittest tests.test_agent_live -v"""
import asyncio
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))

from agent.gateway import ToolGateway  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402

G = {}
RING = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"
BAD = ("risk_score\":", "model_score", "snap_class", "HHG-", "as_of\":", "before_as_of")


class Fixed:
    def __init__(self, o):
        self.o = o

    def respond(self, request, ctx):
        return self.o, "test double"


def setUpModule():
    if not (ROOT / ".env").exists():
        raise unittest.SkipTest(".env missing")
    from fraud_tools.qclient import QueryClient
    from common import HIST_END_EPOCH, build_master
    try:
        G["client"] = QueryClient()
        InvestigationSession(10 ** 8, G["client"]).get_policy_context()
    except Exception as e:
        raise unittest.SkipTest(f"graph not reachable: {e}")
    G["m"] = build_master()
    G["hist_end"] = HIST_END_EPOCH


def agent_for(row, responder=None, k=1):
    gw = ToolGateway(InvestigationSession(int(row["epoch"]), G["client"]))
    return Agent(gw, Trigger(f"SYN-T{k}", str(int(row["TransactionID"])), "risk_score"), responder=responder)


def ring_txn_at_third_customer():
    rg = G["m"][G["m"]["dev"] == RING].sort_values(["epoch", "TransactionID"])
    seen = []
    for _, r in rg.iterrows():
        if r["customer_id"] not in seen:
            seen.append(r["customer_id"])
            if len(seen) == 4:            # a later ring transaction than the first-flag moment
                return r


class RingScenario(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.row = ring_txn_at_third_customer()
        cls.rec = agent_for(cls.row, PendingResponder()).run()

    def test_ring_detected_without_hardcoding(self):
        c = self.rec["case"]
        self.assertTrue(any(e["claim"].startswith("S01") for e in c["evidence"]))
        self.assertEqual(self.rec["uncertainty"]["evidence_strength"], "strong")
        self.assertEqual(c["pattern"], "undocumented")
        self.assertTrue(c["pattern_description"])

    def test_actions_follow_r9_and_are_not_executed(self):
        init = {a["action"]: a for a in self.rec["next_best_actions"]["initial"]}
        self.assertTrue({"CREATE_CASE", "ESCALATE_TO_ANALYST", "FILE_REPORT"} <= set(init))
        self.assertEqual(init["FILE_REPORT"]["route"], "L2")
        self.assertNotIn("BLOCK_CARD", init)
        self.assertTrue(all(not a["executed"] for a in init.values()))

    def test_connected_cards_only_from_the_evidence_device_and_all_visible(self):
        c = self.rec["case"]
        self.assertGreaterEqual(len(c["connected_card_ids"]), 1)
        self.assertNotIn(self.row["card_id"] if "card_id" in self.row else "", c["connected_card_ids"])

    def test_ring_exposure_includes_other_cards_and_is_visible_at_as_of(self):
        m = G["m"]
        as_of = int(self.row["epoch"])
        ids = [int(i) for i in self.rec["case"]["affected_txn_ids"]]
        aff = m[m["TransactionID"].isin(ids)]
        self.assertEqual(len(aff), len(ids))
        self.assertTrue((aff["epoch"] <= as_of).all())                                   # nothing after as_of
        sc = self.rec["exposure_scope"]
        self.assertGreater(sc["other_card_txns"], 0)
        other = aff[aff["customer_id"] != self.row["customer_id"]]
        self.assertEqual(len(other), sc["other_card_txns"])
        self.assertTrue((other["dev"] == RING).all())                                    # only on the evidence device
        self.assertTrue((other["epoch"] >= as_of - 30 * 86400).all())
        self.assertAlmostEqual(self.rec["case"]["exposure_usd"], round(float(aff["TransactionAmt"].abs().sum()), 2), places=1)
        # every other-card ring transaction visible in the window on the expanded cards is included (none silently dropped)
        win = m[(m["dev"] == RING) & (m["epoch"] <= as_of) & (m["epoch"] >= as_of - 30 * 86400) & m["customer_id"].isin(other["customer_id"].unique())]
        self.assertTrue(set(int(i) for i in other["TransactionID"]) <= set(int(i) for i in win["TransactionID"]))
        self.assertGreaterEqual(len(self.rec["case"]["connected_card_ids"]), 1)

    def test_ring_with_denial_blocks_and_reports(self):
        rec = agent_for(self.row, Fixed("denied"), 2).run()
        fin = {a["action"]: a for a in rec["next_best_actions"]["final"]}
        self.assertIn("BLOCK_CARD", fin)
        self.assertIn("FILE_REPORT", fin)
        self.assertIn("MONITOR_CONNECTED_CARDS", fin)
        self.assertEqual(rec["case"]["status"], "closed_fraud")
        self.assertNotEqual(rec["next_best_actions"]["what_changed"], "nothing")

    def test_no_leak_strings_and_epochs_within_as_of(self):
        blob = json.dumps(self.rec)
        for b in BAD:
            self.assertNotIn(b, blob)
        self.assertEqual(self.rec["tokens"], 0)
        self.assertFalse(self.rec["case"]["written_to_graph"])

    def test_only_expected_tools_called(self):
        calls = {t for s in self.rec["trace"] for t in s["tool_calls"]}
        self.assertTrue(calls <= {"get_transaction_context", "get_customer_history", "get_card_history", "find_shared_devices", "find_connected_entities",
                                  "find_prior_cases", "detect_fraud_patterns", "get_policy_context", "find_similar_cases", "retrieve_policy"})


class CardTestingScenario(unittest.TestCase):
    def test_r5_sequence_recommends_decline_and_step_up(self):
        from signals import card_pit
        from common import load_closed
        t = card_pit(G["m"][G["m"]["epoch"] < G["hist_end"]], load_closed())
        hit = t[(t["small1h"] >= 3) & (t["TransactionAmt"] >= 20) & (t["channel"] == "online")].sort_values("epoch").iloc[0]
        rec = agent_for(hit).run()
        self.assertEqual(rec["case"]["pattern"], "card_testing")
        acts = {a["action"] for a in rec["next_best_actions"]["initial"]}
        self.assertTrue({"DECLINE_TRANSACTION", "STEP_UP_AUTH"} <= acts)
        self.assertGreaterEqual(len(rec["case"]["affected_txn_ids"]), 4)
        self.assertAlmostEqual(rec["case"]["exposure_usd"], round(sum(abs(float(x)) for x in [G["m"].loc[G["m"]["TransactionID"] == int(i), "TransactionAmt"].iloc[0]
                                                                                              for i in rec["case"]["affected_txn_ids"]]), 2), places=2)


class HistoricalBatch(unittest.TestCase):
    def test_batch_is_clean_and_fast(self):
        jo = G["m"][(G["m"]["epoch"] < G["hist_end"]) & (G["m"]["channel"] == "online")].sample(4, random_state=3)
        lat = []
        for k, (_, r) in enumerate(jo.iterrows()):
            t0 = time.perf_counter()
            rec = agent_for(r, Fixed("no_reply"), k).run()
            lat.append(time.perf_counter() - t0)
            for b in BAD:
                self.assertNotIn(b, json.dumps(rec))
            self.assertLessEqual(rec["gateway"]["graph_queries_executed"], 9)
            self.assertIsNone(rec["case"]["fraud_probability"])
        self.assertLess(max(lat), 15.0)         # 9A: 8-14 s; 9B: 2-4 s; with GraphRAG chunk retrieval (3-9 s fetch) ~6-10 s per case

    def test_future_transaction_is_not_investigable(self):
        r = G["m"].iloc[5000]
        gw = ToolGateway(InvestigationSession(int(r["epoch"]) - 1, G["client"]))
        with self.assertRaises(Exception) as cm:
            Agent(gw, Trigger("SYN-F", str(int(r["TransactionID"])), "risk_score")).run()
        self.assertIn("FutureTransaction", str(type(cm.exception).__name__) + str(cm.exception))


class McpStdio(unittest.TestCase):
    def test_real_server_process_over_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        row = G["m"][G["m"]["channel"] == "online"].iloc[2000]
        tid, ep = str(int(row["TransactionID"])), int(row["epoch"])

        async def go():
            params = StdioServerParameters(command=sys.executable, args=["-m", "agent.mcp_server", "--as-of-epoch", str(ep)], cwd=str(ROOT / "src"))
            async with stdio_client(params) as (r, w):
                async with ClientSession(r, w) as s:
                    await s.initialize()
                    tools = await s.list_tools()
                    ok = await s.call_tool("get_transaction_context", {"txn_id": tid})
                    bad = await s.call_tool("get_transaction_context", {"txn_id": "9999999"})
                    return tools, ok, bad
        tools, ok, bad = asyncio.run(go())
        self.assertEqual(len(tools.tools), 10)
        text = json.dumps(ok.model_dump(), default=str) if hasattr(ok, "model_dump") else str(ok)
        self.assertIn(tid, text)
        for b in ("model_score", "risk_score", "as_of\\\"", "before_as_of"):
            self.assertNotIn(b, text)
        self.assertIn("band", text)
        self.assertIn("error", json.dumps(bad.model_dump(), default=str).lower())


if __name__ == "__main__":
    unittest.main()
