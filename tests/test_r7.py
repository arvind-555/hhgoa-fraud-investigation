"""R7 is documented as not evaluable; the agent must not act on it."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from test_case_write import make_record  # noqa: E402
from test_simulator_exposure import Forced  # noqa: E402


class R7Tests(unittest.TestCase):
    def test_assessment_result_is_recorded_and_negative(self):
        p = ROOT / "scripts" / "analysis" / "output" / "08_r7_assessment.json"
        if not p.exists():
            self.skipTest("assessment output missing")
        r = json.loads(p.read_text(encoding="utf-8"))
        self.assertFalse(r["merchant_field_exists"])
        self.assertFalse(r["gate"]["coverage_ok"])
        self.assertFalse(r["gate"]["separates"])
        self.assertLess(r["monthly_repeat"]["lift_cleared_vs_fraud"], 1)

    def test_every_case_declares_r7_not_evaluable_and_no_action_cites_it(self):
        for kw in ({}, {"ring": False, "responder": Forced("verified_legitimate"), "trigger": "customer_complaint"}):
            rec = make_record(**kw)
            self.assertTrue(any("R7" in m and "cannot be evaluated" in m for m in rec["uncertainty"]["missing"]))
            for a in rec["next_best_actions"]["initial"] + rec["next_best_actions"]["final"]:
                self.assertNotIn("R7", a["rules"])
                self.assertNotIn("R7", a["reason"])
            self.assertIn("R7", rec["case"]["summary"])            # stated as unresolved in the explanation


if __name__ == "__main__":
    unittest.main()
