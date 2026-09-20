"""SAR wording must be supported by the case's own evidence: 'coordinated use of one device across many customers' may appear only when a shared-origin device ring
(S01) is part of the case. Regression for HHG-006, a single-customer same-card burst that was labelled 'undocumented' and wrongly described as a multi-customer device ring."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from agent.sar import reason_text  # noqa: E402

FACTS = {"final_actions": ["BLOCK_CARD", "FILE_REPORT"], "exposure": 1906.07}


class ReasonTextTests(unittest.TestCase):
    def test_undocumented_burst_is_not_described_as_a_multi_customer_ring(self):
        t = reason_text({**FACTS, "fired": ["S07", "S10"], "s07": {"id": "S07"}}, True, "undocumented")
        self.assertNotIn("across customers", t)
        self.assertNotIn("R9", t)
        self.assertIn("repeated same-card purchases within one hour", t)
        self.assertIn("exposure above $1,000", t)

    def test_a_real_ring_still_cites_the_coordinated_pattern(self):
        t = reason_text({**FACTS, "fired": ["S01"], "s07": None}, True, "undocumented")
        self.assertIn("R9", t)
        self.assertIn("across customers", t)
        self.assertIn("R6", t)


class FiledReportsMatchTheirEvidence(unittest.TestCase):
    def test_every_filed_sar_claims_a_device_ring_only_with_ring_evidence(self):
        filed = 0
        for f in sorted((ROOT / "cases").glob("HHG-*.json")):
            a = json.loads(f.read_text(encoding="utf-8"))
            if not a["sar"]["file"]:
                continue
            filed += 1
            text = a["sar"]["narrative"] + " " + a["sar"]["reason"]
            ring = any(e["claim"].startswith("S01 ") for e in a["case"]["evidence"])
            if "across many customers" in text or "across customers" in text:
                self.assertTrue(ring and a["case"]["connected_device_profiles"], f.name)
        self.assertGreaterEqual(filed, 1)


if __name__ == "__main__":
    unittest.main()
