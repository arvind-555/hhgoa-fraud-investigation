"""Offline unit tests (no network): sentinels, leak guard, client whitelist, validated signal rules, policy data.
Run: python -m unittest discover -s tests -v"""
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fraud_tools import patterns, policy  # noqa: E402
from fraud_tools.guards import LeakError, ToolError, assert_visible  # noqa: E402
from fraud_tools.qclient import STR_ALLOWED, SIGNATURES, QueryClient  # noqa: E402
from fraud_tools.sentinels import SENTINELS, clean_value  # noqa: E402


def T(epoch, amount, channel="online", product="W", tid=None, **kw):
    return {"txn_id": tid or epoch, "epoch": epoch, "amount": amount, "channel": channel, "product_cd": product, "id_15": None, "proxy_type": None,
            "addr1": None, "has_identity": True, **kw}


class SentinelTests(unittest.TestCase):
    def test_table_matches_staged_manifest(self):
        m = ROOT / "data" / "staged" / "full" / "manifest.json"
        if not m.exists():
            self.skipTest("staged manifest not present")
        man = json.loads(m.read_text())["sentinels"]
        for col, meta in man.items():
            if meta["missing_rows"] > 0:
                self.assertEqual(SENTINELS[col], meta["sentinel"], col)
        self.assertEqual(SENTINELS["d4"], -123.0)
        self.assertEqual(SENTINELS["d15"], -84.0)

    def test_clean_value(self):
        self.assertIsNone(clean_value("dist1", -1.0))
        self.assertIsNone(clean_value("d4", -123.0))
        self.assertEqual(clean_value("d4", -1.0), -1.0)          # -1 is a REAL d4 value; only -123 is missing
        self.assertIsNone(clean_value("addr1", -1))
        self.assertEqual(clean_value("addr1", 264), 264)
        self.assertIsNone(clean_value("card2", -1.0))
        self.assertIsNone(clean_value("id_15", ""))
        self.assertEqual(clean_value("amount", 0.0), 0.0)


class GuardTests(unittest.TestCase):
    def test_future_epoch_is_a_leak(self):
        with self.assertRaises(LeakError):
            assert_visible({"a": [{"close_epoch": 101}]}, 100)
        with self.assertRaises(LeakError):
            assert_visible({"x": {"epoch": 101}}, 100)
        with self.assertRaises(LeakError):
            assert_visible({"own_last": 5000}, 100)

    def test_visible_and_exempt_keys(self):
        assert_visible({"as_of": 10 ** 9, "epoch": 100, "close_epoch": 99, "hour_hist": {5: 3}, "amount": 10 ** 9}, 100)   # amount is not an epoch key


class ClientWhitelistTests(unittest.TestCase):
    def test_only_fi_queries(self):
        for bad in ("fi_hack", "q_hub", "SHOW GRAPH *", "../gsql/v1/statements", "fi_txn_context/../x"):
            with self.assertRaises(ToolError):
                QueryClient.check(bad, {})

    def test_exact_parameters_and_ids(self):
        p = QueryClient.check("fi_txn_context", {"txn": "3478561", "as_of": 12420660})
        self.assertIn("/restpp/query/FraudInvestigation/fi_txn_context?", p)
        self.assertNotIn("Transaction_Fraud", p)
        for params in ({"txn": "3478561"}, {"txn": "3478561", "as_of": 1, "x": 2}, {"txn": "abc", "as_of": 1}, {"txn": "3478561", "as_of": "1"},
                       {"txn": "3478561", "as_of": True}, {"txn": "3478561", "as_of": -5}):
            with self.assertRaises(ToolError):
                QueryClient.check("fi_txn_context", params)

    def test_bounds(self):
        ok = {"card": "C13487-K1", "as_of": 100, "days": 30, "max_customers": 100}
        QueryClient.check("fi_shared_devices", ok)
        with self.assertRaises(ToolError):
            QueryClient.check("fi_shared_devices", {**ok, "days": 365})
        with self.assertRaises(ToolError):
            QueryClient.check("fi_shared_devices", {**ok, "card": "C13487-K1; DROP"})

    def test_signatures_have_no_free_text(self):
        for name, sig in SIGNATURES.items():
            self.assertTrue(name.startswith("fi_"))
            for k, kind in sig.items():
                ok = kind == "int" or (isinstance(kind, tuple) and kind[0] == "vertex") or (kind == "str" and k in STR_ALLOWED)   # strings only from closed allow-lists
                self.assertTrue(ok, (name, k))


class SharingAndRingTests(unittest.TestCase):
    def test_tiers(self):
        self.assertEqual(patterns.sharing_tier(5), "strong")
        self.assertEqual(patterns.sharing_tier(6), "moderate")
        self.assertEqual(patterns.sharing_tier(20), "moderate")
        self.assertEqual(patterns.sharing_tier(21), "none")
        self.assertEqual(patterns.sharing_tier(2, blocked=True), "blocked")
        self.assertEqual(patterns.sharing_tier(2, is_null=True), "blocked")

    def test_ring_rule_boundaries(self):
        a = 10 ** 7
        self.assertTrue(patterns.ring_suspect(3, 0.9, 0.5, a))
        self.assertFalse(patterns.ring_suspect(2, 1.0, 1.0, a))
        self.assertFalse(patterns.ring_suspect(3, 0.89, 1.0, a))
        self.assertFalse(patterns.ring_suspect(3, 1.0, 0.49, a))
        self.assertFalse(patterns.ring_suspect(50, 1.0, 1.0, a, blocked=True))
        self.assertFalse(patterns.ring_suspect(50, 1.0, 1.0, a, is_null=True))
        self.assertFalse(patterns.ring_suspect(3, 1.0, 1.0, 29 * 86400), "suppressed during the 30-day warm-up")
        self.assertTrue(patterns.ring_suspect(3, 1.0, 1.0, 31 * 86400))


class SequenceTests(unittest.TestCase):
    def test_r5_needs_three_small_online_in_an_hour_then_20(self):
        base = 10_000
        small = [T(base + i * 600, 1.0, tid=100 + i) for i in range(3)]           # 3 tiny online auths, 10 min apart
        big = T(base + 3000, 259.0, tid=999)
        n, ids = patterns.r5_hit(big, small)
        self.assertEqual(n, 3)
        self.assertEqual(sorted(ids), [100, 101, 102])
        n2, _ = patterns.r5_hit(big, small[:2])
        self.assertEqual(n2, 2)                                                    # two are not enough
        self.assertEqual(patterns.r5_hit(T(base + 3000, 19.99, tid=998), small)[0], 0)   # follow-up must be >= 20
        self.assertEqual(patterns.r5_hit(T(base + 3000, 259.0, channel="in_person", tid=997), small)[0], 0)
        old = [T(base - 4000 + i, 1.0, tid=200 + i) for i in range(3)]             # older than 1h
        self.assertEqual(patterns.r5_hit(T(base, 100.0, tid=996), old)[0], 0)
        notsmall = [T(base + i * 60, 5.0, tid=300 + i) for i in range(3)]          # $5.00 is not "< $5"
        self.assertEqual(patterns.r5_hit(big, notsmall)[0], 0)

    def test_family_burst(self):
        rows = [T(1000, 470.0, product="C", tid=1)]
        self.assertEqual(patterns.family_hit(T(1900, 480.0, product="C", tid=2), rows)[0], 1)
        self.assertEqual(patterns.family_hit(T(1900, 399.99, product="C", tid=2), rows)[0], 0)
        self.assertEqual(patterns.family_hit(T(1900, 480.0, product="W", tid=2), rows)[0], 0)
        self.assertEqual(patterns.family_hit(T(1000 + 3601, 480.0, product="C", tid=2), rows)[0], 0)      # > 1h apart

    def test_amount_z_matches_definition(self):
        # 30 prior txns of $20 -> mu = ln 21, var 0 -> z = (ln(1+a)-mu)/sqrt(0.25)
        n, la = 30, math.log1p(20.0)
        base = {"n_base": n, "sum_la": n * la, "sum_la2": n * la * la}
        z = patterns.amount_z(500.0, base)
        self.assertAlmostEqual(z, (math.log1p(500.0) - la) / 0.5, places=9)
        self.assertGreater(z, 2)
        self.assertIsNone(patterns.amount_z(500.0, {**base, "n_base": 19}))            # needs >= 20 prior txns


class EvaluateTests(unittest.TestCase):
    def _dev(self, **kw):
        d = {"device_id": "D1", "customers": 44, "new_share": 1.0, "proxy_share": 1.0, "neighbours": [], "ring_suspect": True, "sharing_tier": "none",
             "fraud_customers": [], "fraud_cases": [], "blocked": False}
        d.update(kw)
        return d

    def ev(self, txn, rows=(), baseline=None, devices=(), prior=(), as_of=10 ** 7):
        return {s["id"]: s for s in patterns.evaluate(txn, list(rows), baseline or {"n_base": 0}, list(devices), list(prior), as_of)}

    def test_ring_fires_without_labels_or_score(self):
        txn = T(5_000_000, 74.96, product="C", tid=5, device_id="D1", id_15="New", proxy_type="IP_PROXY:ANONYMOUS")
        s = self.ev(txn, devices=[self._dev()])
        self.assertTrue(s["S01"]["fired"])
        self.assertFalse(s["S02a"]["fired"] or s["S02b"]["fired"])
        self.assertEqual(s["S01"]["rating"], "HIGH")
        self.assertNotIn("risk", json.dumps(s).lower())

    def test_s02_tiers_need_visible_confirmed_fraud(self):
        txn = T(5_000_000, 50.0, tid=5, device_id="D1")
        strong = self._dev(customers=4, sharing_tier="strong", ring_suspect=False, fraud_customers=["C9"], fraud_cases=["CC-1"])
        self.assertTrue(self.ev(txn, devices=[strong])["S02a"]["fired"])
        self.assertFalse(self.ev(txn, devices=[{**strong, "fraud_customers": []}])["S02a"]["fired"])
        mod = self._dev(customers=15, sharing_tier="moderate", ring_suspect=False, fraud_customers=["C9"])
        s = self.ev(txn, devices=[mod])
        self.assertTrue(s["S02b"]["fired"] and not s["S02a"]["fired"])
        hub = self._dev(customers=300, sharing_tier="none", ring_suspect=False, fraud_customers=["C9"])
        s = self.ev(txn, devices=[hub])
        self.assertFalse(s["S02a"]["fired"] or s["S02b"]["fired"], "degree > 20 is never sharing evidence")

    def test_blocked_device_never_evidence(self):
        txn = T(5_000_000, 50.0, tid=5, device_id="D1")
        blocked = self._dev(blocked=True, sharing_tier="blocked", ring_suspect=False, fraud_customers=["C9"])
        s = self.ev(txn, devices=[blocked])
        self.assertFalse(any(s[k]["fired"] for k in ("S01", "S02a", "S02b")))

    def test_low_signals_share_one_independence_bucket(self):
        txn = T(5_000_000, 50.0, tid=5, id_15="New", proxy_type="IP_PROXY:ANONYMOUS", has_identity=True)
        sig = patterns.evaluate(txn, [], {"n_base": 0}, [], [], 10 ** 7)
        ind = patterns.independence(sig)
        self.assertEqual(ind["n_tier_1_to_5_sources"], 0)
        self.assertEqual(ind["low_context_sources"], ["identity_flags"])          # S10, S11, S12 -> ONE source
        fired = [s["id"] for s in sig if s["fired"]]
        self.assertTrue({"S10", "S11", "S12"} <= set(fired))

    def test_s15_only_in_person_with_history(self):
        base = {"n_base": 100, "in_person_region_hist": {264: 40, 300: 5}}
        away = T(5_000_000, 50.0, channel="in_person", tid=5, addr1=444)
        self.assertTrue(self.ev(away, baseline=base)["S15"]["fired"])
        home = T(5_000_000, 50.0, channel="in_person", tid=5, addr1=264)
        self.assertFalse(self.ev(home, baseline=base)["S15"]["fired"])
        thin = {"n_base": 100, "in_person_region_hist": {264: 10}}
        self.assertFalse(self.ev(away, baseline=thin)["S15"]["fired"])

    def test_s05_needs_confirmed_case_on_card_or_customer(self):
        txn = T(5_000_000, 50.0, tid=5)
        cases = [{"case_id": "CC-1", "outcome": "confirmed_fraud", "match_reasons": ["same_customer"]}]
        self.assertTrue(self.ev(txn, prior=cases)["S05"]["fired"])
        self.assertFalse(self.ev(txn, prior=[{**cases[0], "outcome": "cleared"}])["S05"]["fired"])
        self.assertFalse(self.ev(txn, prior=[{**cases[0], "match_reasons": ["shared_device:D1"]}])["S05"]["fired"])


class PolicyTests(unittest.TestCase):
    def test_rules_actions_routes(self):
        self.assertEqual(sorted(policy.RULES), sorted(f"R{i}" for i in range(1, 11)))
        self.assertEqual(len(policy.ACTIONS), 14)
        self.assertEqual(policy.BLOCK_CARD_L1_MAX_EXPOSURE, 2500.0)
        self.assertIn("FILE_REPORT (always)", policy.ROUTES["L2"])
        self.assertIn("DECLINE_TRANSACTION", policy.ROUTES["L1"])
        self.assertIn("BLOCK_ALL_CARDS (always)", policy.ROUTES["L2"])

    def test_policy_is_not_evidence_and_decides_nothing(self):
        pc = policy.policy_context(["S01"])
        self.assertEqual(pc["kind"], "policy")
        self.assertIn("not evidence", pc["notice"])
        rules = {p["rule"] for p in pc["potentially_relevant"]}
        self.assertTrue({"R6", "R9", "R1"} <= rules)
        for p in pc["potentially_relevant"]:
            self.assertEqual(p["type"], "policy_pointer_not_a_decision")
        for p in pc["potentially_relevant"]:
            self.assertEqual(set(p), {"rule", "because_signals", "still_required", "type"})      # pointers carry no action list and no probability field
        self.assertEqual(policy.policy_context([])["potentially_relevant"], [])

    def test_r7_flagged_as_not_evaluable(self):
        self.assertIn("NO merchant", policy.RULES["R7"]["note"])


if __name__ == "__main__":
    unittest.main()
