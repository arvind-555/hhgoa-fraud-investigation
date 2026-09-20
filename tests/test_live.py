"""Integration tests against the LIVE FraudInvestigation graph, compared with the independent pandas oracles in scripts/analysis.
Skipped automatically when .env or the installed queries are unavailable. Read-only. Run: python -m unittest tests.test_live -v"""
import json
import math
import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
sys.path.insert(0, str(ROOT / "src" / "ingestion"))

from fraud_tools.guards import FutureTransactionError, LeakError, ToolError  # noqa: E402
from fraud_tools.tools import InvestigationSession, epoch_of  # noqa: E402

G = {}
RING = "SM-G935F Build/NRD90M | Android 7.0 | chrome 62.0 for android | 1920x1080"
END = epoch_of("2016-12-31 23:59:59")


def setUpModule():
    if not (ROOT / ".env").exists():
        raise unittest.SkipTest(".env missing")
    from fraud_tools.qclient import QueryClient
    from common import HIST_END_EPOCH, build_master, load_closed
    from signals import card_pit
    try:
        G["client"] = QueryClient()
        InvestigationSession(END, G["client"]).get_policy_context()
        G["client"].run("fi_txn_context", txn="3478561", as_of=END)
    except Exception as e:
        raise unittest.SkipTest(f"graph/queries not reachable: {e}")
    G["m"] = build_master()
    G["cc"] = load_closed()
    mjo = G["m"][G["m"]["epoch"] < HIST_END_EPOCH]
    G["t"] = card_pit(mjo, G["cc"])                         # Jul-Oct point-in-time frame (validated oracle)
    G["dev"] = pd.read_csv(ROOT / "data" / "staged" / "full" / "device.csv", header=None,
                           names=["device_id", "profile_str", "device_info", "os", "browser", "screen", "n_known", "is_null_profile", "first_epoch", "snap_customers", "snap_class"],
                           dtype=str, keep_default_na=False)


def S(as_of):
    return InvestigationSession(int(as_of), G["client"])


class TemporalTests(unittest.TestCase):
    def test_future_transaction_hidden_and_boundary_inclusive(self):
        row = G["m"][G["m"]["channel"] == "online"].iloc[1000]
        tid, ep = str(int(row["TransactionID"])), int(row["epoch"])
        with self.assertRaises(FutureTransactionError):
            S(ep - 1).get_transaction_context(tid)
        c = S(ep).get_transaction_context(tid)
        self.assertEqual(c["transaction"]["epoch"], ep)
        self.assertLessEqual(c["max_epoch_seen"], ep)

    def test_next_txn_only_when_visible(self):
        m = G["m"]
        card = "C13487-K1"
        rows = m[m["card_id"] == card].sort_values("epoch")
        a, b = rows.iloc[10], rows.iloc[11]
        c = S(int(b["epoch"]) - 1).get_transaction_context(str(int(a["TransactionID"])))
        self.assertIsNone(c["temporal"]["next_txn_visible_at_as_of"])
        c = S(int(b["epoch"])).get_transaction_context(str(int(a["TransactionID"])))
        self.assertEqual(c["temporal"]["next_txn_visible_at_as_of"]["txn_id"], int(b["TransactionID"]))

    def test_card_history_counts_equal_oracle_at_several_as_of(self):
        m = G["m"]
        rng = pd.Series(m["card_id"].unique()).sample(6, random_state=5).tolist()
        for card in rng:
            eps = m.loc[m["card_id"] == card, "epoch"].sort_values().tolist()
            for as_of in {eps[len(eps) // 2], eps[-1], eps[0]}:
                r = S(as_of).get_card_history(card, hours=48, max_rows=500)
                self.assertEqual(r["n_txns_total_visible"], sum(1 for e in eps if e <= as_of), (card, as_of))
                self.assertTrue(all(x["epoch"] <= as_of for x in r["rows"]))
                self.assertNotIn("risk_score", json.dumps(r["rows"]))

    def test_closed_case_visibility_uses_close_epoch_not_open(self):
        cc = G["cc"]
        one = cc.groupby("card_id").filter(lambda d: len(d) == 1)
        one = one[(one["close_ep"] > 20 * 86400)].sample(4, random_state=3)
        for _, c in one.iterrows():
            def ids(as_of):
                return {x["case_id"] for x in S(as_of).find_prior_cases(c["card_id"])["cases"]}
            self.assertNotIn(c["case_id"], ids(c["close_ep"] - 1), "closed_at - 1s: not visible")
            self.assertIn(c["case_id"], ids(c["close_ep"]), "closed_at: visible")
            mid = (c["open_ep"] + c["close_ep"]) // 2
            self.assertNotIn(c["case_id"], ids(mid), "between opened_at and closed_at: NOT visible (opened_at must not gate visibility)")
            self.assertLess(c["open_ep"], mid)

    def test_customer_history_hides_open_cases(self):
        c = G["cc"].sample(1, random_state=9).iloc[0]
        before = S(c["close_ep"] - 1).get_customer_history(c["customer_id"])["visible_closed_cases"]
        after = S(c["close_ep"]).get_customer_history(c["customer_id"])["visible_closed_cases"]
        n_same = int(((G["cc"]["customer_id"] == c["customer_id"]) & (G["cc"]["close_ep"] == c["close_ep"])).sum())
        self.assertEqual(after["total_visible"], before["total_visible"] + n_same)
        self.assertNotIn(c["case_id"], {x["case_id"] for x in before["cases"]})
        self.assertIn(c["case_id"], {x["case_id"] for x in after["cases"]})


class DeviceTests(unittest.TestCase):
    def test_ring_stats_equal_oracle_at_hhg014_time(self):
        from asof import dev_stats_oracle
        as_of = epoch_of("2016-11-22 20:11:00")
        o = dev_stats_oracle(G["m"], RING, as_of, exclude_customer="C13487")
        r = S(as_of).find_shared_devices("C13487-K1")
        d = next(x for x in r["devices"] if x["profile_str"] == RING)
        self.assertEqual((d["customers"], d["txns"]), (o["cust"], o["txn"]))
        self.assertEqual((o["cust"], o["fraud_cust"]), (44, 4))
        self.assertAlmostEqual(d["new_share"], o["new"], places=4)
        self.assertAlmostEqual(d["proxy_share"], o["proxy"], places=4)
        self.assertEqual(len(d["fraud_customers"]), o["fraud_cust"])
        self.assertTrue(d["ring_suspect"])
        self.assertEqual(d["sharing_tier"], "none")                # 44 customers: not sharing evidence, but ring rule fires

    def test_ring_first_flag_moment_three_customers(self):
        m = G["m"]
        rg = m[m["dev"] == RING].sort_values(["epoch", "TransactionID"])
        seen, second, third = [], None, None
        for _, r in rg.iterrows():
            if r["customer_id"] not in seen:
                seen.append(r["customer_id"])
                if len(seen) == 2:
                    second = r
                if len(seen) == 3:
                    third = r
                    break
        for row, expect_ring, n in ((second, False, 2), (third, True, 3)):
            r = S(int(row["epoch"])).find_shared_devices(row["card_id"])
            d = next(x for x in r["devices"] if x["profile_str"] == RING)
            self.assertEqual(d["customers"], n)
            self.assertEqual(d["ring_suspect"], expect_ring)
        dp = S(int(third["epoch"])).detect_fraud_patterns(str(int(third["TransactionID"])))
        self.assertIn("S01", dp["fired"])

    def test_null_profile_and_hub_are_skipped_never_linked(self):
        m, dv = G["m"], G["dev"]
        null_dev = m[m["dev"] == "? | ? | ? | ?"].iloc[0]
        r = S(END).find_shared_devices(null_dev["card_id"])
        nd = next(x for x in r["devices"] if x["profile_str"] == "? | ? | ? | ?")
        self.assertTrue(nd["blocked"])
        self.assertEqual(nd["blocked_reason"], "snapshot_class_NULL")
        self.assertEqual(nd["neighbours"], [])
        self.assertEqual(nd["sharing_tier"], "blocked")
        self.assertFalse(nd["ring_suspect"])
        self.assertIn(nd["device_id"], [s["entity_id"] for s in r["skipped_hubs"]])
        ce = S(END).find_connected_entities(null_dev["card_id"])
        self.assertNotIn(nd["device_id"], [k["entity_id"] for k in ce["links"] if k["link_type"] == "device"])
        hub = dv[dv["snap_class"] == "HUB"].iloc[3]
        hrow = m[m["dev"] == hub["profile_str"]].iloc[0]
        r = S(END).find_shared_devices(hrow["card_id"])
        hd = next(x for x in r["devices"] if x["device_id"] == hub["device_id"])
        self.assertEqual((hd["blocked_reason"], hd["neighbours"], hd["ring_suspect"]), ("snapshot_class_HUB", [], False))

    def test_as_of_degree_over_max_is_skipped_even_when_snapshot_class_is_low(self):
        m, dv = G["m"], G["dev"]
        deg = m[m["dev"].notna()].groupby("dev")["customer_id"].nunique()
        cand = dv[dv["snap_class"].isin(["UNSEEN", "LOW", "MID"]) & dv["profile_str"].map(deg).fillna(0).gt(100)]
        if cand.empty:
            self.skipTest("no non-HUB snapshot device exceeds 100 customers by year end")
        c = cand.iloc[0]
        row = m[m["dev"] == c["profile_str"]].sort_values("epoch").iloc[-1]
        r = S(END).find_shared_devices(row["card_id"])
        d = next(x for x in r["devices"] if x["device_id"] == c["device_id"])
        self.assertEqual(d["blocked_reason"], "asof_degree_over_max")
        self.assertEqual(d["neighbours"], [])
        self.assertGreater(d["customers"], 100)

    def test_neighbours_only_within_window_and_as_of(self):
        as_of = epoch_of("2016-11-22 20:11:00")
        r = S(as_of).find_shared_devices("C13487-K1", days=7)
        d = next(x for x in r["devices"] if x["profile_str"] == RING)
        for n in d["neighbours"]:
            self.assertLessEqual(n["last_epoch"], as_of)
            self.assertGreaterEqual(n["first_epoch"], as_of - 7 * 86400)
            self.assertNotEqual(n["customer_id"], "C13487")


class SignalOracleTests(unittest.TestCase):
    def _hits(self, mask, n):
        t = G["t"]
        return t[mask].sort_values("epoch").head(n)

    def test_r5_signal_matches_validated_hits(self):
        t = G["t"]
        on = t["channel"] == "online"
        hits = self._hits((t["small1h"] >= 3) & (t["TransactionAmt"] >= 20) & on, 7)
        self.assertEqual(len(hits), 7)
        for _, r in hits.iterrows():
            dp = S(int(r["epoch"])).detect_fraud_patterns(str(int(r["TransactionID"])))
            self.assertIn("S06", dp["fired"], r["TransactionID"])
        neg = t[on & (t["TransactionAmt"] >= 20) & (t["small1h"] == 0) & (t["epoch"] > 40 * 86400)].sample(3, random_state=1)
        for _, r in neg.iterrows():
            self.assertNotIn("S06", S(int(r["epoch"])).detect_fraud_patterns(str(int(r["TransactionID"])))["fired"])

    def test_burst_family_signal_matches_oracle(self):
        t = G["t"]
        fam = (t["ProductCD"] == "C") & t["TransactionAmt"].between(400, 500)
        pos = t[fam & (t["fam1h"] >= 1) & (t["epoch"] > 30 * 86400)].sort_values("epoch").head(4)
        neg = t[fam & (t["fam1h"] == 0) & (t["epoch"] > 30 * 86400)].head(3)
        self.assertGreaterEqual(len(pos), 3)
        for _, r in pos.iterrows():
            self.assertIn("S07", S(int(r["epoch"])).detect_fraud_patterns(str(int(r["TransactionID"])))["fired"])
        for _, r in neg.iterrows():
            self.assertNotIn("S07", S(int(r["epoch"])).detect_fraud_patterns(str(int(r["TransactionID"])))["fired"])

    def test_amount_z_equals_oracle(self):
        t = G["t"]
        pos = t[(t["channel"] == "online") & (t["z"] > 2) & (t["hist"] >= 20) & (t["epoch"] > 30 * 86400)].sample(4, random_state=2)
        neg = t[(t["channel"] == "online") & (t["z"] < 1) & (t["hist"] >= 20) & (t["epoch"] > 30 * 86400)].sample(2, random_state=2)
        for _, r in pd.concat([pos, neg]).iterrows():
            dp = S(int(r["epoch"])).detect_fraud_patterns(str(int(r["TransactionID"])))
            s08 = next(s for s in dp["signals"] if s["id"] == "S08")
            self.assertAlmostEqual(s08["detail"]["z"], r["z"], places=2)
            self.assertEqual(s08["fired"], r["z"] > 2)

    def test_detect_never_returns_probability_or_risk(self):
        r = G["t"].sample(1, random_state=4).iloc[0]
        dp = S(int(r["epoch"])).detect_fraud_patterns(str(int(r["TransactionID"])))
        blob = json.dumps(dp).lower()
        self.assertNotIn('"risk_score"', blob)                    # no data field (the documentation text in not_evaluated may mention it)
        self.assertNotIn('"model_score"', blob)
        self.assertNotIn('"fraud_probability"', blob)
        self.assertIn("no_probability", dp)


class SentinelAndPrivacyTests(unittest.TestCase):
    def test_missing_values_are_null_and_real_minus_one_is_kept(self):
        from stage import LAYOUT
        tx = pd.read_csv(ROOT / "data" / "staged" / "full" / "transaction.csv", header=None, names=LAYOUT["transaction"], dtype=str, keep_default_na=False, nrows=200_000)
        miss = tx[(tx["dist1"] == "-1.0") & (tx["d4"] == "-123.0")].iloc[0]
        c = S(int(miss["epoch"])).get_transaction_context(miss["txn_id"])
        self.assertIsNone(c["transaction"]["dist1"])
        self.assertIsNone(c["model_features"]["values"]["d4"])
        real = tx[tx["d4"] == "-1.0"]
        if len(real):
            r0 = real.iloc[0]
            c = S(int(r0["epoch"])).get_transaction_context(r0["txn_id"])
            self.assertEqual(c["model_features"]["values"]["d4"], -1.0)
        nul = tx[tx["addr1"] == "-1"].iloc[0]
        self.assertIsNone(S(int(nul["epoch"])).get_transaction_context(nul["txn_id"])["transaction"]["addr1"])

    def test_no_snapshot_classes_or_labels_in_context_outputs(self):
        as_of = epoch_of("2016-11-22 20:11:00")
        s = S(as_of)
        blob = json.dumps([s.get_transaction_context("3478561"), s.get_card_history("C13487-K1"), s.get_customer_history("C13487")]).lower()
        for banned in ("snap_class", "snap_customers", "hhg-", "n_cards", "risk_score"):
            self.assertNotIn(banned, blob, banned)

    def test_policy_tool_static(self):
        p = S(END).get_policy_context(["S01"])
        self.assertEqual(p["kind"], "policy")
        self.assertEqual(sorted(p["rules"]), sorted(f"R{i}" for i in range(1, 11)))


class LatencyTests(unittest.TestCase):
    def test_every_tool_within_budget(self):
        s = S(epoch_of("2016-11-22 20:11:00"))
        calls = [("get_transaction_context", lambda: s.get_transaction_context("3478561")), ("get_customer_history", lambda: s.get_customer_history("C13487")),
                 ("get_card_history", lambda: s.get_card_history("C13487-K1")), ("find_shared_devices", lambda: s.find_shared_devices("C13487-K1")),
                 ("find_connected_entities", lambda: s.find_connected_entities("C13487-K1")), ("find_prior_cases", lambda: s.find_prior_cases("C13487-K1")),
                 ("detect_fraud_patterns", lambda: s.detect_fraud_patterns("3478561")), ("get_policy_context", lambda: s.get_policy_context([]))]
        for name, fn in calls:
            r = fn()
            self.assertLess(r["latency_ms"], 15_000, name)
            self.assertEqual(r["as_of"], s.as_of)


if __name__ == "__main__":
    unittest.main()
