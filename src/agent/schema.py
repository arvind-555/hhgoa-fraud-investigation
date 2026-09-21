"""Evidence object, uncertainty representation, states and the case record. Plain dataclasses (stdlib only)."""
from dataclasses import dataclass, field, asdict
from enum import Enum


class State(str, Enum):
    TRIGGER = "TRIGGER"
    INITIAL_INVESTIGATION = "INITIAL_INVESTIGATION"
    EVIDENCE_SYNTHESIS = "EVIDENCE_SYNTHESIS"
    UNCERTAINTY_ASSESSMENT = "UNCERTAINTY_ASSESSMENT"
    ADDITIONAL_EVIDENCE_REQUEST = "ADDITIONAL_EVIDENCE_REQUEST"
    NEXT_BEST_ACTION = "NEXT_BEST_ACTION"
    EXPLANATION = "EXPLANATION"
    CASE_WRITE = "CASE_WRITE"
    DONE = "DONE"


# legal transitions (ADDITIONAL_EVIDENCE_REQUEST is optional; it loops back through synthesis/uncertainty once)
TRANSITIONS = {
    State.TRIGGER: {State.INITIAL_INVESTIGATION},
    State.INITIAL_INVESTIGATION: {State.EVIDENCE_SYNTHESIS},
    State.EVIDENCE_SYNTHESIS: {State.UNCERTAINTY_ASSESSMENT},
    State.UNCERTAINTY_ASSESSMENT: {State.ADDITIONAL_EVIDENCE_REQUEST, State.NEXT_BEST_ACTION},
    State.ADDITIONAL_EVIDENCE_REQUEST: {State.NEXT_BEST_ACTION},
    State.NEXT_BEST_ACTION: {State.EXPLANATION},
    State.EXPLANATION: {State.CASE_WRITE},
    State.CASE_WRITE: {State.DONE},
}


@dataclass(frozen=True)
class Trigger:
    """What the runner tells the agent. Deliberately NO as_of / opened_at / case pack row: time is enforced by the tool gateway."""
    case_id: str
    txn_id: str
    trigger_type: str           # README values: risk_score | customer_report | analyst_request (customer_complaint is accepted as an alias of customer_report)
    model_alert_band: str = ""  # "" | "high" | "medium" | "low" (coarse; the raw score is never given to the agent)


@dataclass(frozen=True)
class Evidence:
    id: str                     # E1, E2, ... (the explanation may cite only these ids)
    source_tool: str
    kind: str                   # signal | context | link | prior_case | customer
    summary: str
    rating: str                 # HIGH | MEDIUM | LOW | CONTEXT
    signal_id: str = ""
    tier: str = ""
    independent_source: str = ""     # signals sharing a source count once (spec 6.2)
    direction: str = "supports_fraud"   # supports_fraud | contradicts | context
    refs: dict = field(default_factory=dict)   # txn_ids, card_ids, customer_ids, device_ids, case_ids
    max_epoch: int = 0          # latest epoch this evidence rests on (must be <= as_of; checked by the gateway)
    simulated: bool = False     # True only for evidence produced by the evidence simulator (never graph evidence)


@dataclass
class Uncertainty:
    evidence_strength: str      # none | weak | moderate | strong
    independent_sources: int
    conflicts: list
    missing: list
    level: str                  # low | medium | high
    needs_more_evidence: bool
    calibrated_probability: dict    # {value: None unless calibrated, calibrated, source, method, status, gates_failed, gate_notes}
    reasons: list
    pattern_defining: list = field(default_factory=list)     # signals that DEFINE the pattern (e.g. S10/S11/S12 inside an S01 ring): never counted as corroboration
    corroborating: list = field(default_factory=list)        # weak context signals: reported, never counted as independent evidence
    customer_statement: str = ""                           # "denial" when the trigger is a customer report (immutable, real trigger evidence)


@dataclass
class EvidenceRequest:
    type: str                   # customer_validation | step_up_auth | analyst_info
    asked_after_step: int
    reason: str
    outcome: str = "pending"    # simulator vocabulary: verified_legitimate | denied_or_unrecognized | passed | failed | no_response (legacy: confirmed | denied | no_reply | pending)
    assumed_response: str = ""
    request_id: str = ""        # "<case_id>:REQ<n>", issued by the agent; the simulator answers only issued requests


@dataclass
class Action:
    action: str
    route: str                  # auto | L1 | L2
    reason: str
    rules: list = field(default_factory=list)
    evidence_ids: list = field(default_factory=list)
    executed: bool = False      # Phase 9B never executes anything
    requires_approval: bool = False


def to_dict(x):
    return asdict(x)
