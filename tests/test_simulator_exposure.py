"""Offline tests: deterministic evidence simulator (B4) and ring/connected-card exposure. No network (fake session implementing the 8 tools)."""
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from agent.gateway import ToolGateway  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import EvidenceRequest, Trigger  # noqa: E402
from agent.simulator import DISTRIBUTION, EvidenceSimulator, SimulatorError, TEXT  # noqa: E402
from fraud_tools.guards import LeakError, ToolError  # noqa: E402

AS_OF = 5_100_000
T0 = 5_000_000                       # flagged transaction epoch
D1 = "D_ring"


def req(case, typ, n=1):
    return EvidenceRequest(type=typ, asked_after_step=1, reason="t", request_id=f"{case}:REQ{n}")


class SimulatorTests(unittest.TestCase):
    def test_same_seed_same_answers_across_instances(self):
        a, b = EvidenceSimulator("seed-x"), EvidenceSimulator("seed-x")
        for i in range(200):
            c = f"SYN-{i:03d}"
            for typ in DISTRIBUTION:
                self.assertEqual(a.respond(req(c, typ), {"case_id": c, "request_type": typ}), b.respond(req(c, typ), {"case_id": c, "request_type": typ}))

    def test_known_answers_are_frozen(self):
        """Regression: sha256-based draws must never change silently (reproducible benchmark runs)."""
        s = EvidenceSimulator()
        got = [s.outcome_for(f"REG-{i:03d}", t) for i in (1, 2, 3) for t in ("customer_validation", "step_up_auth")]
        self.assertEqual(got, FROZEN)

    def test_seed_changes_answers(self):
        a, b = EvidenceSimulator("s1"), EvidenceSimulator("s2")
        diff = sum(a.outcome_for(f"SYN-{i}", "step_up_auth") != b.outcome_for(f"SYN-{i}", "step_up_auth") for i in range(300))
        self.assertGreater(diff, 50)

    def test_vocabulary_and_marking(self):
        s = EvidenceSimulator()
        for typ, dist in DISTRIBUTION.items():
            seen = {s.outcome_for(f"C-{i}", typ) for i in range(400)}
            self.assertEqual(seen, {n for n, _ in dist})
        for out, text in TEXT.items():
            self.assertTrue(text.startswith("[SIMULATED]"), out)

    def test_distribution_matches_the_declared_policy(self):
        s = EvidenceSimulator()
        for typ, dist in DISTRIBUTION.items():
            n = 4000
            c = Counter(s.outcome_for(f"K-{i}", typ) for i in range(n))
            for name, p in dist:
                self.assertAlmostEqual(c[name] / n, p, delta=0.04, msg=f"{typ}/{name}")

    def test_answers_only_requests_the_agent_issued(self):
        s = EvidenceSimulator()
        with self.assertRaises(SimulatorError):
            s.respond(req("SYN-1", "step_up_auth"), {"case_id": "SYN-2", "request_type": "step_up_auth"})            # other case
        with self.assertRaises(SimulatorError):
            s.respond(EvidenceRequest("step_up_auth", 1, "t", request_id=""), {"case_id": "SYN-1", "request_type": "step_up_auth"})
        with self.assertRaises(SimulatorError):
            s.respond(req("SYN-1", "step_up_auth"), {"case_id": "SYN-1", "request_type": "customer_validation"})     # type mismatch
        with self.assertRaises(SimulatorError):
            s.respond(req("SYN-1", "analyst_info"), {"case_id": "SYN-1", "request_type": "analyst_info"})           # nothing simulated for analysts
        with self.assertRaises(SimulatorError):
            s.respond(EvidenceRequest("step_up_auth", 1, "t", request_id="garbage"), {"case_id": "SYN-1", "request_type": "step_up_auth"})

    def test_no_access_to_data_by_construction(self):
        src = (ROOT / "src" / "agent" / "simulator.py").read_text(encoding="utf-8")
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        body = re.sub(r'""".*?"""', "", code, flags=re.S)
        imports = re.findall(r"^\s*(?:import|from)\s+([\w\.]+)", body, re.M)
        self.assertEqual(sorted(set(imports)), ["hashlib", "re"])
        for banned in ("fraud_tools", "pandas", "case_pack", "closed_cases", "risk_score", "TransactionDT", "qclient", "open("):
            self.assertNotIn(banned, body, banned)
        self.assertEqual(EvidenceSimulator().describe()["uses_labels_or_outcomes"], False)


FROZEN = ['no_response', 'passed', 'no_response', 'passed', 'no_response', 'no_response']


class FakeSession:
    """Implements the 8 session tools with canned RAW payloads (the gateway sanitises them). Also lets tests inject a leaking row."""
    def __init__(self, ring=True, customers=3, leak=False, nb_total=None, extra=0, fail=(), trunc=(), score=0.83):
        self.as_of, self.timings, self.cache_hits = AS_OF, [], 0
        self.nb_total, self.extra, self.fail, self.trunc = nb_total, extra, set(fail), set(trunc)
        self.score = score
        self.ring, self.customers, self.leak = ring, customers, leak
        self.closed_txn = 9001
        self.card_hist_calls = []
        self.hist = {
            "C00001-K1": [self.row(3000001, T0, 100.0, D1 if ring else "D_own"), self.row(3000000, T0 - 3600, 20.0, D1 if ring else "D_own")],
            "C00002-K1": [self.row(2001, 4_900_000, 200.0, D1), self.row(2002, 2_000_000, 999.0, D1),            # 2002: outside the 30-day window
                          self.row(2003, 4_800_000, 300.0, "D_other"), self.row(self.closed_txn, 4_950_000, 400.0, D1)],  # 2003: other device; 9001: in a visible closed case
            "C00003-K1": [self.row(3001, 5_050_000, 50.0, D1)],
        }
        for i in range(extra):                                      # additional in-window neighbour cards (no relevant transactions needed)
            self.hist[f"C1{i:04d}-K1"] = []
        if leak:
            self.hist["C00003-K1"].append(self.row(3002, AS_OF + 500, 70.0, D1))

    @staticmethod
    def row(tid, ep, amt, dev):
        return {"txn_id": tid, "epoch": ep, "amount": amt, "product_cd": "C", "channel": "online", "device_id": dev}

    def get_transaction_context(self, txn_id):
        return {"transaction": {"txn_id": 3000001, "epoch": T0, "amount": 100.0, "card_id": "C00001-K1", "customer_id": "C00001", "channel": "online"},
                "device": {"device_id": D1}, "temporal": {"seconds_before_as_of": AS_OF - T0}, "model_score": {"value": self.score}, "max_epoch_seen": T0, "as_of": AS_OF}

    def get_customer_history(self, customer_id, lookback_days=200):
        return {"customer_id": customer_id}

    def get_card_history(self, card_id, hours=48, max_rows=100):
        self.card_hist_calls.append(card_id)
        if card_id in self.fail:
            raise ToolError("simulated failure")
        return {"rows": self.hist.get(card_id, []), "truncated": card_id in self.trunc, "as_of": AS_OF}

    def find_shared_devices(self, card_id, days=30):
        nb = [{"card_id": "C00002-K1", "customer_id": "C00002", "n_txns": 3, "first_epoch": 1, "last_epoch": 2},
              {"card_id": "C00003-K1", "customer_id": "C00003", "n_txns": 1, "first_epoch": 1, "last_epoch": 2}] if self.ring else []
        nb += [{"card_id": f"C1{i:04d}-K1", "customer_id": f"C1{i:04d}", "n_txns": 1, "first_epoch": 1, "last_epoch": 2} for i in range(self.extra)] if self.ring else []
        total = self.nb_total if self.nb_total is not None else len(nb)
        return {"devices": [{"device_id": D1 if self.ring else "D_own", "profile_str": "SM | Android | chrome | 1920x1080", "blocked": False, "customers": self.customers,
                             "sharing_tier": "none", "ring_suspect": self.ring, "neighbours": nb,
                             "neighbours_total": total, "neighbours_truncated": total > len(nb)}], "skipped_hubs": []}

    def find_connected_entities(self, card_id, days=30):
        return {"connected_cards": []}

    def find_prior_cases(self, card_id):
        return {"cases": [{"case_id": "CC-1", "outcome": "confirmed_fraud", "match_reasons": ["shared_device:" + D1], "transaction_ids": [self.closed_txn], "close_epoch": 1,
                           "open_epoch": 1}]}

    def detect_fraud_patterns(self, txn_id):
        fired = self.ring
        sig = {"id": "S01", "name": "ring", "tier": 2, "rating": "HIGH", "source": "device_graph", "fired": fired, "detail": {}, "entity_ids": [D1] if fired else []}
        return {"signals": [sig], "fired": ["S01"] if fired else [], "independence": {"n_tier_1_to_5_sources": 1 if fired else 0, "tier_1_to_5_sources": ["device_graph"] if fired else [],
                                                                                        "low_context_sources": []}}

    def get_policy_context(self, fired_signals=None):
        return {"kind": "policy"}

    def find_similar_cases(self, txn_id, k=10):
        hit = {"chunk_id": "CH-CC-1", "case_id": "CC-1", "label": "linked", "score": 1.0, "pattern": "card_not_present_fraud", "outcome": "confirmed_fraud",
               "channel": "online", "close_epoch": 1, "match_reasons": ["shared_device:" + D1], "snippet": "note"}
        return {"txn_id": 3000001, "hits": [hit] if self.ring else [], "summary": {}}

    def retrieve_policy(self, rule_ids):
        return {"kind": "policy_text_not_evidence", "requested": list(rule_ids), "missing": [],
                "chunks": [{"source_ref": f"policy:{r}", "chunk_id": f"TX-policy-{r}", "text": f"{r}: Title. When: something applies. Actions: do it."} for r in rule_ids]}


def run_agent(session, responder=None, case="SYN-900", trig="risk_score"):
    gw = ToolGateway(FakeSession() if session is None else session)
    return Agent(gw, Trigger(case, "3000001", trig), responder=responder or PendingResponder()), gw


class Forced:
    def __init__(self, out):
        self.out = out

    def respond(self, request, ctx):
        return self.out, TEXT.get(self.out, "")


class ExposureTests(unittest.TestCase):
    def test_other_card_transactions_on_the_evidence_device_are_included(self):
        a, gw = run_agent(FakeSession())
        rec = a.run()
        c = rec["case"]
        ids = set(c["affected_txn_ids"])
        self.assertTrue({"3000001", "2001", "3001"} <= ids)
        self.assertFalse({"2002", "2003", "9001", "3002"} & ids)             # outside window / other device / closed case
        self.assertEqual(c["exposure_usd"], 100.0 + 200.0 + 50.0 + 20.0 * ("3000000" in ids))
        self.assertEqual(rec["exposure_scope"]["other_card_txns"], 2)
        self.assertEqual(rec["exposure_scope"]["other_card_exposure_usd"], 250.0)
        self.assertEqual(sorted(rec["exposure_scope"]["other_cards_with_txns"]), ["C00002-K1", "C00003-K1"])
        self.assertTrue(rec["exposure_scope"]["complete"])

    def test_every_affected_transaction_is_visible_at_as_of(self):
        a, gw = run_agent(FakeSession())
        rec = a.run()
        epoch = {**{i: T0 for i in (3000001,)}, 2001: 4_900_000, 3001: 5_050_000, 3000000: T0 - 3600}
        for i in rec["case"]["affected_txn_ids"]:
            self.assertLessEqual(epoch[int(i)], AS_OF)

    def test_leaking_row_from_a_connected_card_is_discarded(self):
        a, gw = run_agent(FakeSession(leak=True))
        with self.assertRaises(LeakError):
            a.run()

    def test_pre_window_historical_cards_do_not_make_the_expansion_incomplete(self):
        """44 customers were ever seen on the device but only 2 were active in the discovery window: the other 41 are irrelevant to this window."""
        a, gw = run_agent(FakeSession(customers=44))
        rec = a.run()
        sc = rec["exposure_scope"]
        self.assertTrue(sc["complete"])
        self.assertEqual(sc["cards_not_expanded"], 0)
        self.assertEqual(sc["historical_cards_outside_window"], 41)
        self.assertEqual(sc["incomplete_reasons"], [])
        self.assertNotIn("incomplete", " ".join(e["claim"] for e in rec["case"]["evidence"]))
        base, _ = run_agent(FakeSession(customers=3))
        self.assertEqual(rec["case"]["exposure_usd"], base.run()["case"]["exposure_usd"])   # the exposure itself is unaffected by the flag

    def test_relevant_cards_dropped_by_the_tool_list_cap_make_it_incomplete(self):
        a, gw = run_agent(FakeSession(customers=3, nb_total=30))          # 30 cards active in the window, only 2 listed
        sc = a.run()["exposure_scope"]
        self.assertFalse(sc["complete"])
        self.assertEqual(sc["cards_not_expanded"], 28)
        self.assertEqual(sc["historical_cards_outside_window"], 0)
        self.assertTrue(any("capped by the tool" in r for r in sc["incomplete_reasons"]))

    def test_historical_and_relevant_are_counted_separately(self):
        a, gw = run_agent(FakeSession(customers=44, nb_total=30))
        sc = a.run()["exposure_scope"]
        self.assertEqual((sc["complete"], sc["cards_not_expanded"], sc["historical_cards_outside_window"]), (False, 28, 13))

    def test_own_expansion_cap_makes_it_incomplete(self):
        a, gw = run_agent(FakeSession(customers=60, extra=45))           # 47 relevant neighbour cards, expansion cap is 40
        sc = a.run()["exposure_scope"]
        self.assertFalse(sc["complete"])
        self.assertEqual(sc["cards_expanded"], 40)
        self.assertEqual(sc["cards_not_expanded"], 7)
        self.assertTrue(any("expansion cap" in r for r in sc["incomplete_reasons"]))

    def test_failed_or_truncated_card_history_makes_it_incomplete(self):
        a, gw = run_agent(FakeSession(fail=("C00003-K1",)))
        rec = a.run()
        sc = rec["exposure_scope"]
        self.assertFalse(sc["complete"])
        self.assertEqual(sc["cards_not_expanded"], 1)
        self.assertNotIn("3001", rec["case"]["affected_txn_ids"])
        a, gw = run_agent(FakeSession(trunc=("C00002-K1",)))
        sc = a.run()["exposure_scope"]
        self.assertFalse(sc["complete"])
        self.assertTrue(any("truncated" in r for r in sc["incomplete_reasons"]))
        self.assertEqual(sc["cards_not_expanded"], 0)                     # expanded, but its rows may be partial

    def test_discovery_window_gap_is_reported_but_is_not_a_missing_card(self):
        a, gw = run_agent(FakeSession())
        sc = a.run()["exposure_scope"]
        self.assertEqual(sc["discovery_window_gap_s"], AS_OF - T0)
        self.assertTrue(sc["complete"])

    def test_no_shared_origin_evidence_means_no_expansion(self):
        s = FakeSession(ring=False)
        a, gw = run_agent(s)
        rec = a.run()
        self.assertEqual(s.card_hist_calls, ["C00001-K1"])
        self.assertEqual(rec["exposure_scope"]["other_card_txns"], 0)
        self.assertNotIn("2001", rec["case"]["affected_txn_ids"])

    def test_exposure_equals_sum_of_affected_amounts(self):
        a, gw = run_agent(FakeSession())
        rec = a.run()
        amt = {"3000001": 100.0, "3000000": 20.0, "2001": 200.0, "3001": 50.0}
        self.assertAlmostEqual(rec["case"]["exposure_usd"], sum(amt[i] for i in rec["case"]["affected_txn_ids"]), places=2)


class SimulatedEvidenceTests(unittest.TestCase):
    def go(self, out, ring=False):
        a, gw = run_agent(FakeSession(ring=ring), Forced(out))
        return a.run(), a

    def test_customer_outcomes_become_simulated_evidence(self):
        # in-person / complaint would use customer_validation; force the type through the trigger
        a, gw = run_agent(FakeSession(ring=False), Forced("verified_legitimate"), trig="customer_complaint")
        rec = a.run()
        sim = [e for e in rec["case"]["evidence"] if e["simulated"]]
        self.assertEqual(len(sim), 1)
        self.assertTrue(sim[0]["claim"].startswith("[SIMULATED]"))
        self.assertEqual(rec["evidence_requests"][0]["type"], "customer_validation")
        self.assertTrue(rec["evidence_requests"][0]["simulated"])
        self.assertEqual(rec["case"]["verdict"], "legitimate")
        self.assertEqual([x["action"] for x in rec["next_best_actions"]["final"]], ["CLOSE_NO_FRAUD"])
        a, gw = run_agent(FakeSession(ring=False), Forced("denied_or_unrecognized"), trig="customer_complaint")
        rec = a.run()
        self.assertEqual(rec["case"]["verdict"], "fraud")
        self.assertIn("BLOCK_CARD", [x["action"] for x in rec["next_best_actions"]["final"]])

    def test_step_up_passed_and_failed(self):
        rec, a = self.go("passed")
        self.assertEqual(rec["evidence_requests"][0]["type"], "step_up_auth")
        self.assertEqual(rec["case"]["verdict"], "legitimate")
        self.assertIn("ALLOW_TRANSACTION", [x["action"] for x in rec["next_best_actions"]["final"]])
        rec, a = self.go("failed")
        fin = [x["action"] for x in rec["next_best_actions"]["final"]]
        self.assertTrue({"DECLINE_TRANSACTION", "VERIFY_WITH_CUSTOMER"} <= set(fin))
        self.assertNotIn("BLOCK_CARD", fin)
        self.assertEqual(rec["case"]["verdict"], "uncertain")
        self.assertEqual([e["simulated"] for e in rec["case"]["evidence"] if e["source"] == "customer"], [True])

    def test_no_response_keeps_evidence_pending(self):
        rec, a = self.go("no_response")
        self.assertEqual(rec["simulated_evidence_ids"], [])
        self.assertEqual(rec["case"]["status"], "open")
        self.assertEqual(rec["next_best_actions"]["what_changed"], "nothing")
        self.assertIn("pending", rec["stop_reason"])

    def test_simulated_evidence_never_overwrites_graph_evidence(self):
        base, a0 = self.go("no_response", ring=True)
        for out in ("passed", "failed"):
            rec, a = self.go(out, ring=True)
            core = lambda ev: [e for e in ev if e.kind != "document"]           # policy-text documents depend on the chosen actions; they are not graph evidence   # noqa: E731
            graph_before = [e for e in core(a0.evidence) if not e.simulated]
            graph_after = [e for e in core(a.evidence) if not e.simulated]
            self.assertEqual(graph_before, graph_after)                    # identical graph evidence objects (frozen dataclasses)
            self.assertTrue(all(e.simulated for e in core(a.evidence)[len(graph_after):]))
            self.assertEqual(rec["uncertainty"]["evidence_strength"], base["uncertainty"]["evidence_strength"])
            self.assertEqual(rec["case"]["connected_card_ids"], base["case"]["connected_card_ids"])

    def test_strong_graph_evidence_is_not_closed_by_a_passed_step_up(self):
        rec, a = self.go("passed", ring=True)
        self.assertEqual([x["action"] for x in rec["next_best_actions"]["final"]], ["ESCALATE_TO_ANALYST"])
        self.assertNotEqual(rec["case"]["verdict"], "legitimate")

    def test_end_to_end_with_the_real_simulator_is_reproducible(self):
        outs = []
        for _ in range(2):
            a, gw = run_agent(FakeSession(ring=False), EvidenceSimulator("repro"), case="REG-777")
            rec = a.run()
            outs.append((rec["evidence_requests"][0]["outcome"], rec["case"]["verdict"], [x["action"] for x in rec["next_best_actions"]["final"]]))
        self.assertEqual(outs[0], outs[1])

    def test_no_leak_strings_in_records(self):
        rec, a = self.go("failed", ring=True)
        blob = json.dumps(rec)
        for b in ("risk_score\":", "model_score", "snap_class", "\"as_of\""):
            self.assertNotIn(b, blob)


if __name__ == "__main__":
    unittest.main()
