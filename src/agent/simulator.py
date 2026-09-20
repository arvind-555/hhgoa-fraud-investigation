"""Deterministic customer / step-up evidence SIMULATOR (blocker B4, policy version b4-v1). Everything it returns is SIMULATED evidence.

Design (spec 8.4):
* inputs: the evidence request issued by the agent (type, request_id) and the case id. Nothing else. It has no graph client, no dataframe, no case pack, no
  closed-case data, no transaction data, no labels, no risk score, no clock: it cannot access or infer outcomes, benchmark labels or future transactions.
* deterministic: outcome = f(seed, case_id, request_type) through SHA-256; the same seed reproduces the same responses on every run and machine.
* fixed a priori (not tuned on any case): the response probabilities below are neutral placeholders, independent of the case's content.
* it answers only requests that the agent issued (request_id "<case_id>:REQ<n>" for the same case and type); anything else raises.

Vocabulary
  customer_validation : verified_legitimate | denied_or_unrecognized | no_response
  step_up_auth        : passed | failed | no_response
  (no_response: the evidence stays pending; the orchestrator adds no evidence and keeps the case open)
"""
import hashlib
import re

POLICY_VERSION = "b4-v1"
DEFAULT_SEED = "hhgoa-b4-sim-v1"

DISTRIBUTION = {
    "customer_validation": [("verified_legitimate", 0.40), ("denied_or_unrecognized", 0.40), ("no_response", 0.20)],
    "step_up_auth": [("passed", 0.45), ("failed", 0.35), ("no_response", 0.20)],
}
TEXT = {
    "verified_legitimate": "[SIMULATED] Customer states they made the transaction.",
    "denied_or_unrecognized": "[SIMULATED] Customer states they did not make the transaction or do not recognise it.",
    "passed": "[SIMULATED] Step-up authentication passed.",
    "failed": "[SIMULATED] Step-up authentication failed.",
    "no_response": "[SIMULATED] No response received; evidence remains pending.",
}
_REQ = re.compile(r"^(?P<case>[A-Za-z0-9\-]+):REQ(?P<n>\d+)$")


class SimulatorError(Exception):
    pass


class EvidenceSimulator:
    simulated = True

    def __init__(self, seed=DEFAULT_SEED):
        self.seed = str(seed)
        self._issued = {}                       # request_id -> outcome (idempotent answers)

    @staticmethod
    def draw(seed, case_id, request_type):
        """Uniform value in [0, 1) from SHA-256(seed|case_id|request_type)."""
        h = hashlib.sha256(f"{seed}|{case_id}|{request_type}".encode("utf-8")).hexdigest()
        return int(h[:16], 16) / float(1 << 64)

    def outcome_for(self, case_id, request_type):
        if request_type not in DISTRIBUTION:
            raise SimulatorError(f"no simulation defined for request type {request_type!r}")
        u, acc = self.draw(self.seed, case_id, request_type), 0.0
        for name, p in DISTRIBUTION[request_type]:
            acc += p
            if u < acc:
                return name
        return DISTRIBUTION[request_type][-1][0]

    def respond(self, request, case_ctx):
        rid, typ, case_id = getattr(request, "request_id", ""), getattr(request, "type", ""), (case_ctx or {}).get("case_id", "")
        m = _REQ.match(rid or "")
        if not m or m.group("case") != case_id:
            raise SimulatorError("the simulator only answers requests issued by the agent for this case")
        if (case_ctx or {}).get("request_type") != typ:
            raise SimulatorError("request type mismatch")
        if rid in self._issued:
            out = self._issued[rid]
        else:
            out = self._issued[rid] = self.outcome_for(case_id, typ)
        return out, TEXT[out]

    def describe(self):
        return {"policy_version": POLICY_VERSION, "seed": self.seed, "distribution": DISTRIBUTION, "simulated": True,
                "inputs": ["case_id", "request_type", "request_id"], "uses_labels_or_outcomes": False}
