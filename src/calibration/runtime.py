"""Runtime probability lookup used by the agent. Built by the RUNNER with the case's as_of; the agent only sees the returned dict.

The artifact holds label-derived numbers (calibration data). This module never returns counts, rates of other buckets, or numeric gate diagnostics:
only the probability, the calibrated flag, a method id and the NAMES of the failed gates. `calibrated` is True only when every gate passed for the bucket
and the case's as_of is at or after the label horizon (G1). Otherwise NO probability is returned (value None): no placeholder, no prevalence assumption.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT = ROOT / "config" / "calibration_v1.json"
GATE_TEXT = {"G1": "case time is before the calibration label horizon", "G2": "insufficient history for this evidence class",
             "G3": "out-of-time validation failed for this evidence class",
             "G4": "closed-case history cannot be transferred to new alerts (most alerts never became labelled cases)", "NOBUCKET": "no calibration for this evidence class"}


class Calibrator:
    def __init__(self, as_of_epoch, artifact=None):
        if not isinstance(as_of_epoch, int) or as_of_epoch <= 0:
            raise ValueError("as_of_epoch must be a positive int")
        self._as_of = as_of_epoch
        self._art = artifact if artifact is not None else json.loads(DEFAULT_ARTIFACT.read_text(encoding="utf-8"))
        self.version = self._art["version"]

    def calibrate(self, strength):
        """-> {probability, calibrated, source, method, gates_failed, gate_notes}. probability is None unless calibrated is True."""
        b = self._art["buckets"].get(strength)
        failed = []
        if self._as_of < self._art["label_horizon_epoch"]:
            failed.append("G1")
        if b is None:
            failed.append("NOBUCKET")
        else:
            failed += [r.split(" ")[0] for r in b["reasons"]]
            if not b["validated_in_population"] and not any(g in failed for g in ("G2", "G3")):
                failed.append("G3")
        failed = sorted(set(failed))
        if not failed and b is not None and b["calibrated_ok"]:
            return {"probability": b["p_closed_case_population"], "calibrated": True, "source": "calibration_table", "method": self.version, "gates_failed": [], "gate_notes": []}
        return {"probability": None, "calibrated": False, "source": "unavailable", "method": self.version, "gates_failed": failed,
                "gate_notes": [GATE_TEXT[g] for g in failed]}
