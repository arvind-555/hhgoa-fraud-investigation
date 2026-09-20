"""B3 calibration tests: reproducibility, point-in-time/leakage, gates, runtime output hygiene, agent integration. Uses local data files only (no TigerGraph)."""
import copy
import hashlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent.gateway import ToolGateway  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.reasoner import PendingResponder  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from calibration import fit as F  # noqa: E402
from calibration.runtime import Calibrator, DEFAULT_ARTIFACT  # noqa: E402
from test_simulator_exposure import FakeSession, Forced  # noqa: E402

GOOD = {"version": "t-v1", "label_horizon_epoch": 1000, "train_cut_epoch": 500,
        "buckets": {s: {"p_closed_case_population": p, "validated_in_population": True, "identified_for_alerts": True, "calibrated_ok": True, "reasons": []}
                    for s, p in (("none", 0.2), ("weak", 0.3), ("moderate", 0.6), ("strong", 0.9))}}


def art(**over):
    a = copy.deepcopy(GOOD)
    for k, v in over.items():
        a["buckets"]["weak"].update(v) if k == "weak" else a.update({k: v})
    return a


class GateTests(unittest.TestCase):
    def test_all_gates_pass_gives_calibrated_true(self):
        r = Calibrator(2000, GOOD).calibrate("weak")
        self.assertEqual((r["calibrated"], r["probability"], r["source"], r["gates_failed"]), (True, 0.3, "calibration_table", []))

    def test_horizon_gate_blocks_calibration_before_the_label_horizon(self):
        r = Calibrator(999, GOOD).calibrate("weak")
        self.assertFalse(r["calibrated"])
        self.assertIsNone(r["probability"])
        self.assertIn("G1", r["gates_failed"])
        self.assertTrue(Calibrator(1000, GOOD).calibrate("weak")["calibrated"])      # boundary: as_of == horizon is allowed

    def test_each_failed_gate_blocks_calibration(self):
        for gate in ("G2", "G3", "G4"):
            a = art(weak={"calibrated_ok": False, "reasons": [f"{gate} something 0.123"], "validated_in_population": gate == "G4", "identified_for_alerts": gate != "G4"})
            r = Calibrator(2000, a).calibrate("weak")
            self.assertFalse(r["calibrated"], gate)
            self.assertIsNone(r["probability"])
            self.assertIn(gate, r["gates_failed"])

    def test_unknown_bucket_is_not_calibrated(self):
        r = Calibrator(2000, GOOD).calibrate("mystery")
        self.assertFalse(r["calibrated"])
        self.assertEqual(r["gates_failed"], ["NOBUCKET"])

    def test_runtime_output_exposes_no_calibration_data(self):
        a = art(weak={"calibrated_ok": False, "reasons": ["G4 identification: bounded to [0.189, 0.969] width 0.78, 26936 alerts"]})
        r = Calibrator(2000, a).calibrate("weak")
        self.assertEqual(sorted(r), ["calibrated", "gate_notes", "gates_failed", "method", "probability", "source"])
        blob = json.dumps(r)
        self.assertFalse(re.search(r"\d\.\d{3}|26936|bounded", blob))
        self.assertNotIn("n_train", blob)


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (ROOT / "closed_cases_history.csv").exists() or not (ROOT / "data" / "staged" / "full" / "device.csv").exists():
            raise unittest.SkipTest("local data files missing")
        cls.a1, cls.r1 = F.fit()
        cls.a2, cls.r2 = F.fit()
        from calibration.frame import build_frame
        cls.frame = build_frame()

    def test_fit_is_reproducible(self):
        d = lambda x: hashlib.sha256(json.dumps(x, sort_keys=True).encode()).hexdigest()   # noqa: E731
        self.assertEqual(d(self.a1), d(self.a2))
        self.assertEqual(d(self.r1), d(self.r2))

    def test_committed_artifact_matches_a_fresh_fit(self):
        committed = json.loads(DEFAULT_ARTIFACT.read_text(encoding="utf-8"))
        self.assertEqual(committed, json.loads(json.dumps(self.a1)))

    def test_no_label_is_known_at_flag_time_and_none_after_the_horizon(self):
        f = self.frame
        self.assertTrue((f["epoch"] < f["case_close_ep"]).all())            # the flagged transaction precedes the case closure that labels it
        self.assertLessEqual(int(f["case_close_ep"].max()), F.HIST_END_EPOCH)
        self.assertEqual(len(f), f["case_id"].nunique())

    def test_temporal_split_is_out_of_time(self):
        f = self.frame
        train = f[f["case_close_ep"] <= F.CUT_EPOCH]
        test = f[f["epoch"] > F.CUT_EPOCH]
        self.assertFalse(set(train["case_id"]) & set(test["case_id"]))
        self.assertTrue((train["case_close_ep"] <= F.CUT_EPOCH).all())
        self.assertTrue((test["epoch"] > F.CUT_EPOCH).all())
        self.assertGreater(len(test), 500)

    def test_pipeline_never_reads_the_benchmark(self):
        for name in ("fit.py", "frame.py", "runtime.py"):
            code = (ROOT / "src" / "calibration" / name).read_text(encoding="utf-8")
            body = "\n".join(l for l in code.splitlines() if not l.lstrip().startswith("#"))
            self.assertNotIn("load_casepack", body)
            self.assertNotIn("case_pack", re.sub(r'""".*?"""', "", body, flags=re.S))
        self.assertEqual(sorted(self.a1["inputs_sha256"]), ["closed_cases_history.csv"])

    def test_risk_score_is_not_a_feature(self):
        from calibration import frame
        src = (ROOT / "src" / "calibration" / "frame.py").read_text(encoding="utf-8")
        body = src[src.index("def strength_from_flags"):]
        self.assertNotIn("risk_score\"]", body.split("out = r[")[0])          # no signal flag uses risk_score

    def test_current_data_do_not_support_calibrated_true(self):
        """The honest, documented outcome of B3 on the available labels: no bucket passes every gate (G4 fails everywhere)."""
        b = self.a1["buckets"]
        self.assertFalse(any(v["calibrated_ok"] for v in b.values()))
        self.assertTrue(all(not v["identified_for_alerts"] for v in b.values()))
        self.assertTrue(any("G4" in r for r in b["weak"]["reasons"]))
        self.assertIn("G2", " ".join(b["strong"]["reasons"]))               # too few labelled strong cases in the training period

    def test_real_artifact_yields_uncalibrated_probability_at_runtime(self):
        c = Calibrator(12_000_000)
        for s in ("none", "weak", "moderate", "strong"):
            r = c.calibrate(s)
            self.assertFalse(r["calibrated"], s)
            self.assertIsNone(r["probability"])              # no placeholder, no assumed prevalence


def run(calibrator, responder=None, ring=False):
    gw = ToolGateway(FakeSession(ring=ring))
    return Agent(gw, Trigger("SYN-CAL", "3000001", "risk_score"), responder=responder or PendingResponder(), calibrator=calibrator).run()


class AgentIntegrationTests(unittest.TestCase):
    def test_no_calibrator_means_uncalibrated_placeholder(self):
        c = run(None)["case"]
        self.assertFalse(c["probability_calibrated"])
        self.assertEqual(c["probability_source"], "unavailable")
        self.assertIsNone(c["fraud_probability"])

    def test_valid_calibration_is_marked_and_used(self):
        rec = run(Calibrator(5_100_000, art(label_horizon_epoch=1000)), ring=True)
        c = rec["case"]
        self.assertTrue(c["probability_calibrated"])
        self.assertEqual(c["probability_source"], "calibration_table")
        self.assertEqual(c["fraud_probability"], 0.9)                       # strong bucket of the synthetic artifact
        self.assertEqual(c["calibration"]["gates_failed"], [])

    def test_calibrated_flag_is_dropped_when_a_simulated_response_overrides_the_probability(self):
        rec = run(Calibrator(5_100_000, art(label_horizon_epoch=1000)), Forced("denied_or_unrecognized"), ring=False)
        c = rec["case"]
        self.assertFalse(c["probability_calibrated"])
        self.assertIsNone(c["fraud_probability"])
        self.assertEqual(c["probability_source"], "unavailable")

    def test_future_horizon_blocks_calibration_inside_the_agent(self):
        rec = run(Calibrator(5_100_000, art(label_horizon_epoch=9_999_999)), ring=True)
        c = rec["case"]
        self.assertFalse(c["probability_calibrated"])
        self.assertIsNone(c["fraud_probability"])
        self.assertIn("G1", c["calibration"]["gates_failed"])

    def test_record_contains_no_calibration_data(self):
        rec = run(Calibrator(5_100_000), ring=True)
        blob = json.dumps(rec)
        self.assertIsNone(rec["case"]["fraud_probability"])
        for banned in ("k_train", "n_train", "test_rate", "alert_bounds", "closed_case_population", "wilson", "bounded to"):
            self.assertNotIn(banned, blob)



class NullProbabilityPolicyTests(unittest.TestCase):
    """The calibration experiment is a failed validation gate: no probability is ever stated on the available data."""
    def test_real_artifact_never_yields_a_probability_in_any_case_record(self):
        for ring in (True, False):
            for resp in (None, Forced("denied_or_unrecognized"), Forced("passed"), Forced("verified_legitimate")):
                rec = run(Calibrator(12_000_000), resp, ring=ring)
                c = rec["case"]
                self.assertIsNone(c["fraud_probability"])
                self.assertFalse(c["probability_calibrated"])
                self.assertEqual(c["probability_source"], "unavailable")
                self.assertEqual(rec["uncertainty"]["calibrated_probability"]["status"], "unavailable_validation_gate_failed")
                self.assertIsNone(rec["uncertainty"]["calibrated_probability"]["value"])

    def test_explanation_separates_strength_uncertainty_and_probability(self):
        rec = run(Calibrator(12_000_000), ring=True)
        text = rec["case"]["summary"]
        i1, i2, i3 = text.index("Evidence strength:"), text.index("Investigation uncertainty:"), text.index("Calibrated probability:")
        self.assertTrue(i1 < i2 < i3)
        self.assertIn("no probability is stated", text)
        self.assertNotRegex(text, r"probability (is|of) (about )?\d|\d\d ?%")
        self.assertNotIn("provisional", text.lower())

    def test_calibrated_probability_would_still_be_reported_when_gates_pass(self):
        text = run(Calibrator(5_100_000, art(label_horizon_epoch=1000)), ring=True)["case"]["summary"]
        self.assertIn("Calibrated probability: 0.90", text)


if __name__ == "__main__":
    unittest.main()
