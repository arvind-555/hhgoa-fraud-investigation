"""Deterministic customer / step-up evidence SIMULATOR (policy b5-v1). Everything it returns is SIMULATED evidence.

The task supplies no customer or step-up replies (README section 5: "in this round those responses are not provided. Simulate them in your own system and state the assumption").
This simulator therefore makes ONE default assumption and offers explicit what-if scenarios. It has NO randomness and NO hash: a response is a pure function of
(scenario, request type, trigger type). The same request always gets the same answer, and the answer never depends on the case's hidden outcome.

Scenarios
  no_reply (default; the only one the benchmark run uses)
      "No reply within 24 hours." It represents ABSENCE of evidence, not testimony: the agent makes no claim about what the customer would have said.
      Policy R4 then applies (MONITOR_CARD, DECLINE_TRANSACTION for pending authorisations, escalate above $500).
  cardholder_confirms   what-if: the cardholder confirms the transaction (customer_validation -> verified_legitimate) or authenticates (step_up_auth -> passed).
  cardholder_denies     what-if: the cardholder does not recognise the transaction (customer_validation -> denied_or_unrecognized) or fails authentication (step_up_auth -> failed).
The two what-if scenarios exist so the decision matrix can be tested and demonstrated; a submission run never uses them.

Case consistency
  A customer REPORT ("I never made this purchase") is immutable trigger evidence. The simulator refuses to have that same customer confirm the transaction.

Vocabulary and meaning
  customer_validation : verified_legitimate (customer confirms it) | denied_or_unrecognized (customer does not recognise it) | no_response (no reply in 24 h)
  step_up_auth        : passed (cardholder authenticated)          | failed (authentication failed)                        | no_response (no reply in 24 h)
"""
import re

POLICY_VERSION = "b5-v1"
DEFAULT_SCENARIO = "no_reply"
SCENARIOS = {
    "no_reply": {"customer_validation": "no_response", "step_up_auth": "no_response"},
    "cardholder_confirms": {"customer_validation": "verified_legitimate", "step_up_auth": "passed"},
    "cardholder_denies": {"customer_validation": "denied_or_unrecognized", "step_up_auth": "failed"},
}
TEXT = {
    "verified_legitimate": "[SIMULATED] What-if scenario: the cardholder confirms they made the transaction.",
    "denied_or_unrecognized": "[SIMULATED] What-if scenario: the cardholder does not recognise the transaction.",
    "passed": "[SIMULATED] What-if scenario: step-up authentication passed.",
    "failed": "[SIMULATED] What-if scenario: step-up authentication failed.",
    "no_response": "[SIMULATED] No reply received within 24 hours (the task supplies no replies; this is an assumption of absence, not customer testimony).",
}
MEANING = {
    "no_response": "no evidence: the customer/step-up reply is assumed not to have arrived within 24 hours (policy R4)",
    "verified_legitimate": "what-if only: the cardholder confirms the transaction (policy R3)",
    "denied_or_unrecognized": "what-if only: the cardholder does not recognise the transaction (policy R2)",
    "passed": "what-if only: the cardholder authenticated",
    "failed": "what-if only: authentication failed",
}
CUSTOMER_REPORTS = ("customer_report", "customer_complaint")
_REQ = re.compile(r"^(?P<case>[A-Za-z0-9\-]+):REQ(?P<n>\d+)$")


class SimulatorError(Exception):
    pass


class EvidenceSimulator:
    simulated = True

    def __init__(self, scenario=DEFAULT_SCENARIO):
        if scenario not in SCENARIOS:
            raise SimulatorError(f"unknown scenario {scenario!r}; expected one of {sorted(SCENARIOS)}")
        self.scenario = scenario
        self._issued = {}                       # request_id -> outcome (idempotent answers)

    def outcome_for(self, request_type, trigger_type=""):
        if request_type not in SCENARIOS[self.scenario]:
            raise SimulatorError(f"no simulation defined for request type {request_type!r}")
        out = SCENARIOS[self.scenario][request_type]
        if trigger_type in CUSTOMER_REPORTS and out in ("verified_legitimate", "passed"):
            raise SimulatorError("scenario contradicts the trigger: a customer report is immutable evidence that the customer denies the transaction")
        return out

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
            out = self._issued[rid] = self.outcome_for(typ, (case_ctx or {}).get("trigger_type", ""))
        return out, TEXT[out]

    def describe(self):
        return {"policy_version": POLICY_VERSION, "scenario": self.scenario, "deterministic": True, "random": False, "simulated": True,
                "inputs": ["scenario", "request_type", "trigger_type", "request_id"], "uses_labels_or_outcomes": False, "meaning": dict(MEANING)}
