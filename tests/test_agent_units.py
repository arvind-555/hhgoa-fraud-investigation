"""Offline tests for the Phase 9B agent layer: permissions, sanitising, as_of enforcement, state machine, actions, policy invariants. No network."""
import asyncio
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agent import actions as A  # noqa: E402
from agent.gateway import ToolGateway, band, sanitize  # noqa: E402
from agent.mcp_server import EXPOSED, build  # noqa: E402
from agent.orchestrator import Agent, InvariantError  # noqa: E402
from agent.permissions import FORBIDDEN_ARGS, PermissionDenied, TOOLS, validate_call  # noqa: E402
from agent.reasoner import check_citations  # noqa: E402
from agent.schema import State, TRANSITIONS, Trigger, Uncertainty  # noqa: E402
from fraud_tools.guards import LeakError  # noqa: E402
from fraud_tools.tools import InvestigationSession  # noqa: E402


class NoNet:
    """Client stub: any graph call is a test failure unless a fixture is provided."""
    def __init__(self, fixtures=None):
        self.fixtures, self.calls = fixtures or {}, []

    def run(self, name, **params):
        self.calls.append((name, params))
        if name in self.fixtures:
            return self.fixtures[name]
        raise RuntimeError("network not allowed in unit tests")


class PermissionTests(unittest.TestCase):
    def test_exactly_ten_tools(self):
        self.assertEqual(len(TOOLS), 10)
        self.assertEqual(set(TOOLS), set(EXPOSED))

    def test_no_tool_accepts_time_gsql_or_score(self):
        for name, spec in TOOLS.items():
            self.assertFalse(set(spec["args"]) & FORBIDDEN_ARGS, name)

    def test_forbidden_and_unknown_args_rejected(self):
        for bad in ({"txn_id": "3514030", "as_of": 5}, {"txn_id": "3514030", "gsql": "x"}, {"txn_id": "3514030", "risk_score": 0.5},
                    {"txn_id": "3514030", "graph": "Transaction_Fraud"}, {"txn_id": "3514030", "extra": 1}, {}):
            with self.assertRaises(PermissionDenied):
                validate_call("get_transaction_context", bad)

    def test_unknown_tools_rejected(self):
        for t in ("run_query", "tigergraph__gsql", "write_case", "leak_check", "drop_graph", "run_installed_query", "fi_txn_context"):
            with self.assertRaises(PermissionDenied):
                validate_call(t, {})

    def test_id_and_int_validation(self):
        with self.assertRaises(PermissionDenied):
            validate_call("get_card_history", {"card_id": "C12382-K1; DROP"})
        with self.assertRaises(PermissionDenied):
            validate_call("get_card_history", {"card_id": "C12382-K1", "hours": 99999})
        with self.assertRaises(PermissionDenied):
            validate_call("find_shared_devices", {"card_id": "C12382-K1", "days": True})
        self.assertEqual(validate_call("find_prior_cases", {"card_id": "C12382-K1"}), {"card_id": "C12382-K1"})

    def test_state_permissions(self):
        validate_call("detect_fraud_patterns", {"txn_id": "3514030"}, State.INITIAL_INVESTIGATION)
        with self.assertRaises(PermissionDenied):
            validate_call("detect_fraud_patterns", {"txn_id": "3514030"}, State.EXPLANATION)
        with self.assertRaises(PermissionDenied):
            validate_call("get_transaction_context", {"txn_id": "3514030"}, State.CASE_WRITE)


class GatewayTests(unittest.TestCase):
    def test_sanitize_replaces_raw_score_and_strips_internals(self):
        p = {"model_score": {"value": 0.8734}, "as_of": 1, "max_epoch_seen": 1, "latency_ms": 3, "x": [{"snap_class": "HUB", "keep": 1}]}
        out = sanitize(p)
        s = json.dumps(out)
        self.assertNotIn("0.8734", s)
        self.assertNotIn("snap_class", s)
        self.assertNotIn("as_of", s)
        self.assertEqual(out["model_alert"]["band"], "high")
        self.assertEqual(out["x"], [{"keep": 1}])

    def test_bands_coarse(self):
        self.assertEqual([band(v) for v in (0.1, 0.5, 0.7, 0.99)], ["low", "medium", "high", "high"])

    def test_benchmark_case_id_refused(self):
        with self.assertRaises(LeakError):
            sanitize({"analyst_notes": "see HHG-014"})

    def test_call_validates_before_touching_the_graph(self):
        c = NoNet()
        gw = ToolGateway(InvestigationSession(1000, c))
        with self.assertRaises(PermissionDenied):
            gw.call("get_transaction_context", {"txn_id": "3514030", "as_of": 10 ** 9})
        self.assertEqual(c.calls, [])

    def test_as_of_is_fixed_by_the_runner(self):
        c = NoNet({"fi_txn_context": {"visible": False}})
        gw = ToolGateway(InvestigationSession(123456, c))
        try:
            gw.call("get_transaction_context", {"txn_id": "3514030"})
        except Exception:
            pass
        self.assertEqual(c.calls[0][1]["as_of"], 123456)          # injected by the session, not supplied by the caller
        self.assertFalse(hasattr(gw, "as_of"))

    def test_leaking_result_is_discarded(self):
        c = NoNet({"fi_prior_cases": {"max_epoch_seen": 5000, "Top": [], "n_related_visible": 0}})
        gw = ToolGateway(InvestigationSession(1000, c))
        with self.assertRaises(LeakError):
            gw.call("find_prior_cases", {"card_id": "C12382-K1"})

    def test_identical_calls_are_memoised(self):
        c = NoNet({"fi_prior_cases": {"max_epoch_seen": 10, "Top": [], "n_related_visible": 0}})
        gw = ToolGateway(InvestigationSession(1000, c))
        a = gw.call("find_prior_cases", {"card_id": "C12382-K1"})
        b = gw.call("find_prior_cases", {"card_id": "C12382-K1"})
        self.assertIs(a, b)
        self.assertEqual(len(c.calls), 1)

    def test_session_single_flight_cache(self):
        c = NoNet({"fi_prior_cases": {"max_epoch_seen": 10, "Top": [], "n_related_visible": 0}})
        s = InvestigationSession(1000, c)
        s.find_prior_cases("C12382-K1")
        s.find_prior_cases("C12382-K1")
        self.assertEqual(len(c.calls), 1)
        self.assertEqual(s.cache_hits, 1)


class McpTests(unittest.TestCase):
    def test_server_exposes_only_the_eight_tools_without_time_or_gsql_params(self):
        mcp, _ = build(1000, NoNet())
        tools = asyncio.run(mcp.list_tools())
        self.assertEqual({t.name for t in tools}, set(EXPOSED))
        for t in tools:
            props = set((t.input_schema or {}).get("properties", {}))
            self.assertFalse(props & FORBIDDEN_ARGS, t.name)

    def test_model_supplied_as_of_is_dropped_by_the_schema_and_never_used(self):
        c = NoNet({"fi_txn_context": {"visible": False}})
        mcp, _ = build(1000, c)
        asyncio.run(mcp.call_tool("get_transaction_context", {"txn_id": "3514030", "as_of": 5}))
        self.assertEqual([p["as_of"] for _, p in c.calls], [1000])

    def test_server_returns_error_json_for_invalid_ids(self):
        c = NoNet()
        mcp, _ = build(1000, c)
        r = asyncio.run(mcp.call_tool("get_transaction_context", {"txn_id": "abc'; DROP"}))
        self.assertIn("PermissionDenied", json.dumps(r, default=str))
        self.assertEqual(c.calls, [])


class StateMachineTests(unittest.TestCase):
    def test_transition_table(self):
        self.assertEqual(TRANSITIONS[State.UNCERTAINTY_ASSESSMENT], {State.ADDITIONAL_EVIDENCE_REQUEST, State.NEXT_BEST_ACTION})
        self.assertEqual(TRANSITIONS[State.EXPLANATION], {State.CASE_WRITE})
        self.assertNotIn(State.TRIGGER, TRANSITIONS[State.CASE_WRITE])

    def test_illegal_transition_raises(self):
        a = Agent(ToolGateway(InvestigationSession(1000, NoNet())), Trigger("SYN-1", "3514030", "risk_score"))
        with self.assertRaises(InvariantError):
            a._go(State.CASE_WRITE)


def unc(strength, level="medium", n=1, conflicts=()):
    return Uncertainty(strength, n, list(conflicts), [], level, level != "low", {"low": 0, "point": 0.5, "high": 1, "calibrated": False, "basis": "t"}, [])


def ctx(**k):
    d = {"exposure": 100.0, "fired": [], "channel": "online", "trigger_type": "risk_score", "model_band": "high", "connected_card_ids": [], "evidence_ids_by_signal": {}}
    d.update(k)
    return d


class ActionTests(unittest.TestCase):
    def names(self, acts):
        return [a.action for a in acts]

    def test_routes_follow_the_policy_table(self):
        self.assertEqual(A.route_of("BLOCK_CARD", 2500.0), "L1")
        self.assertEqual(A.route_of("BLOCK_CARD", 2500.01), "L2")
        self.assertEqual(A.route_of("DECLINE_TRANSACTION", 1), "L1")
        self.assertEqual(A.route_of("FILE_REPORT", 1), "L2")
        self.assertEqual(A.route_of("VERIFY_WITH_CUSTOMER", 1e9), "auto")

    def test_r1_weak_signal_verifies_never_blocks(self):
        acts = A.initial_actions(unc("weak"), ctx(fired=["S13"]))
        self.assertIn("STEP_UP_AUTH", self.names(acts))
        self.assertFalse({"BLOCK_CARD", "BLOCK_ALL_CARDS"} & set(self.names(acts)))

    def test_no_evidence_low_alert_allows(self):
        acts = A.initial_actions(unc("none", "low"), ctx(model_band="low"))
        self.assertEqual(self.names(acts), ["ALLOW_TRANSACTION"])

    def test_r5_decline_and_step_up(self):
        acts = A.initial_actions(unc("moderate", "high"), ctx(fired=["S06"], evidence_ids_by_signal={"S06": ["E1"]}))
        self.assertEqual(set(self.names(acts)) & {"DECLINE_TRANSACTION", "STEP_UP_AUTH"}, {"DECLINE_TRANSACTION", "STEP_UP_AUTH"})

    def test_ring_only_is_r9(self):
        acts = A.initial_actions(unc("strong", "medium"), ctx(fired=["S01"], evidence_ids_by_signal={"S01": ["E1"]}, exposure=900))
        self.assertTrue({"CREATE_CASE", "ESCALATE_TO_ANALYST", "FILE_REPORT"} <= set(self.names(acts)))
        self.assertNotIn("BLOCK_CARD", self.names(acts))

    def test_denied_blocks_with_exposure_routed_and_reports_on_shared_origin(self):
        f, o = A.final_actions([], unc("strong"), ctx(fired=["S02a"], exposure=3000, connected_card_ids=["C00001-K1"]), "denied")
        d = {a.action: a for a in f}
        self.assertEqual(d["BLOCK_CARD"].route, "L2")
        self.assertIn("FILE_REPORT", d)
        self.assertIn("MONITOR_CONNECTED_CARDS", d)

    def test_denied_small_exposure_l1_no_report(self):
        f, _ = A.final_actions([], unc("weak"), ctx(fired=["S13"], exposure=200), "denied")
        d = {a.action: a for a in f}
        self.assertEqual(d["BLOCK_CARD"].route, "L1")
        self.assertNotIn("FILE_REPORT", d)

    def test_confirmed_closes_unless_strong_shared_origin(self):
        f, _ = A.final_actions([], unc("weak"), ctx(), "confirmed")
        self.assertEqual(self.names(f), ["CLOSE_NO_FRAUD"])
        f, _ = A.final_actions([], unc("strong"), ctx(fired=["S02a"]), "confirmed")
        self.assertEqual(self.names(f), ["ESCALATE_TO_ANALYST"])

    def test_no_reply_r4(self):
        f, _ = A.final_actions([], unc("weak"), ctx(exposure=800), "no_reply")
        self.assertEqual(self.names(f), ["MONITOR_CARD", "DECLINE_TRANSACTION", "ESCALATE_TO_ANALYST"])

    def test_pending_keeps_initial(self):
        init = A.initial_actions(unc("weak"), ctx())
        f, o = A.final_actions(init, unc("weak"), ctx(), "pending")
        self.assertIs(f, init)
        self.assertEqual(o, "pending")

    def test_nothing_is_ever_executed(self):
        for s in ("none", "weak", "moderate", "strong"):
            for a in A.initial_actions(unc(s), ctx(fired=["S01"] if s == "strong" else [])):
                self.assertFalse(a.executed)
                self.assertEqual(a.requires_approval, a.route != "auto")

    def test_block_all_cards_is_never_recommended(self):
        for o in ("pending", "denied", "confirmed", "no_reply"):
            for s in ("weak", "strong"):
                f, _ = A.final_actions(A.initial_actions(unc(s), ctx(fired=["S01"])), unc(s), ctx(fired=["S01"]), o)
                self.assertNotIn("BLOCK_ALL_CARDS", [a.action for a in f])

    def test_citation_check(self):
        self.assertTrue(check_citations("ok [E1] and [E2]", {"E1", "E2"}))
        self.assertFalse(check_citations("bad [E9]", {"E1"}))



class TriggerNameTests(unittest.TestCase):
    def test_readme_trigger_name_customer_report_is_handled_like_a_complaint(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_simulator_exposure import FakeSession as FS, Forced as FC
        for name in ("customer_report", "customer_complaint"):
            rec = Agent(ToolGateway(FS(ring=False)), Trigger("SYN-T", "3000001", name), responder=FC("verified_legitimate")).run()
            self.assertEqual(rec["evidence_requests"][0]["type"], "customer_validation", name)


if __name__ == "__main__":
    unittest.main()
