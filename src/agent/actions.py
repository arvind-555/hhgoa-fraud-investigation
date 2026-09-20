"""Deterministic next-best-action engine (recommendations only; nothing is executed).

The action table below follows Fraud Policy v1.0 (README, spec section 8). It uses EVIDENCE STRENGTH, never a probability (calibration failed its validation gates, so no
probability exists). Policy thresholds phrased as probabilities are applied through evidence classes (docs/agent_orchestration.md). Routes follow spec 8.1 exactly. The LLM never chooses actions, routes, exposure or rule ids.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fraud_tools.policy import BLOCK_CARD_L1_MAX_EXPOSURE  # noqa: E402

from .schema import Action, Uncertainty

SHARED_ORIGIN = {"S01", "S02a", "S02b"}


def route_of(action, exposure):
    if action == "BLOCK_CARD":
        return "L1" if exposure <= BLOCK_CARD_L1_MAX_EXPOSURE else "L2"
    if action in ("DECLINE_TRANSACTION",):
        return "L1"
    if action in ("BLOCK_ALL_CARDS", "FILE_REPORT"):
        return "L2"
    return "auto"


def act(action, exposure, reason, rules, ev=()):
    r = route_of(action, exposure)
    return Action(action=action, route=r, reason=reason, rules=list(rules), evidence_ids=list(ev), executed=False, requires_approval=(r != "auto"))


def file_report_condition(exposure, shared_origin, other_customer_fraud, undocumented):
    return exposure > 1000 or shared_origin or other_customer_fraud or undocumented


def initial_actions(unc: Uncertainty, ctx):
    """ctx: dict(exposure, fired, channel, trigger_type, model_band, connected_card_ids, evidence_ids_by_signal)."""
    ex, fired, ev = ctx["exposure"], set(ctx["fired"]), ctx["evidence_ids_by_signal"]
    e = lambda *sids: [i for s in sids for i in ev.get(s, [])]      # noqa: E731
    out = []
    shared = bool(fired & SHARED_ORIGIN)
    online = ctx["channel"] == "online"
    if unc.evidence_strength == "strong":
        if "S01" in fired and not (fired & {"S02a", "S02b", "S06"}):
            out += [act("CREATE_CASE", ex, "R9: coordinated device-sharing pattern across customers, no documented pattern fits", ["R9"], e("S01")),
                    act("ESCALATE_TO_ANALYST", ex, "R9: undocumented coordinated pattern", ["R9"], e("S01")),
                    act("FILE_REPORT", ex, "R9/R6: coordinated activity across customers", ["R9", "R6"], e("S01"))]
        else:
            out += [act("CREATE_CASE", ex, "evidence strong enough to open a case (policy 3a)", ["3a"], e(*fired)),
                    act("DECLINE_TRANSACTION", ex, "shared-origin evidence with >=2 independent sources; approval required", ["R6"], e(*fired))]
            if shared:
                out += [act("FILE_REPORT", ex, "R6: activity connects to a shared device / another customer's fraud", ["R6"], e("S02a", "S02b", "S01"))]
        if ctx["connected_card_ids"]:
            out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the eligible device", ["R6"], e("S01", "S02a", "S02b")))
        out.append(act("VERIFY_WITH_CUSTOMER", ex, "confirm with the cardholder before any block (R1 spirit; block requires approval)", ["R1"]))
    elif unc.evidence_strength == "moderate":
        if "S06" in fired:
            out += [act("DECLINE_TRANSACTION", ex, "R5: card-testing sequence then a larger purchase", ["R5"], e("S06")),
                    act("STEP_UP_AUTH", ex, "R5", ["R5"], e("S06"))]
        else:
            out += [act("STEP_UP_AUTH" if online else "VERIFY_WITH_CUSTOMER", ex, "R1: single independent source (not strong): verify before any block", ["R1"], e(*fired))]
        out += [act("CREATE_CASE", ex, "evidence requested / moderate evidence: open a case (policy 3a)", ["3a"], e(*fired)), act("MONITOR_CARD", ex, "watch the card while evidence is pending", ["R1"])]
        if fired & SHARED_ORIGIN and ctx["connected_card_ids"]:
            out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the eligible device", ["R6"], e("S02b")))
    elif unc.evidence_strength == "weak" or ctx["trigger_type"] != "risk_score" or ctx["model_band"] == "high":
        out += [act("STEP_UP_AUTH" if online else "VERIFY_WITH_CUSTOMER", ex, "R1: only weak/context signals or the model score; verify before any block", ["R1"]),
                act("MONITOR_CARD", ex, "watch the card while evidence is pending", ["R1"])]
        if ctx["trigger_type"] in ("customer_complaint", "customer_report"):
            out.append(act("CREATE_CASE", ex, "customer dispute always opens a case (3a)", ["3a"]))
    else:
        out.append(act("ALLOW_TRANSACTION", ex, "no validated evidence and the model alert is not high", ["R1"]))
    if unc.conflicts and ex > 500 and unc.level != "low":
        out.append(act("ESCALATE_TO_ANALYST", ex, "R8: conflicting evidence and exposure > $500", ["R8"]))
    elif unc.level == "high" and ex > 500:
        out.append(act("ESCALATE_TO_ANALYST", ex, "R8: uncertain and exposure > $500", ["R8"]))
    return _dedupe(out)


def final_actions(initial, unc: Uncertainty, ctx, outcome):
    """Actions after the (assumed) response to the evidence request. `outcome` in pending|confirmed|denied|no_reply."""
    ex, fired = ctx["exposure"], set(ctx["fired"])
    if outcome == "pending":
        return initial, "pending"
    if outcome == "denied":
        out = [act("BLOCK_CARD", ex, "R2: customer denies the transaction", ["R2"]), act("CREATE_CASE", ex, "R2", ["R2"])]
        if file_report_condition(ex, bool(fired & SHARED_ORIGIN), False, "S01" in fired and not fired & {"S02a", "S02b", "S06"}):
            out.append(act("FILE_REPORT", ex, "R2: exposure > $1,000 or shared-origin / coordinated evidence", ["R2", "R6"]))
        if ctx["connected_card_ids"]:
            out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the eligible device", ["R6"]))
        return _dedupe(out), "denied"
    if outcome == "confirmed":
        out = [act("CLOSE_NO_FRAUD", ex, "R3: customer confirms the transaction (confirmation noted in the case)", ["R3"])]
        if unc.evidence_strength == "strong":       # conflict between a customer confirmation and strong shared-origin evidence
            out = [act("ESCALATE_TO_ANALYST", ex, "R8: customer confirmed but strong shared-origin evidence remains", ["R8", "R3"])]
        return out, "confirmed"
    if outcome == "passed":
        if unc.evidence_strength == "strong":
            return [act("ESCALATE_TO_ANALYST", ex, "R8: simulated step-up passed but strong shared-origin evidence remains", ["R8"])], "passed"
        return [act("ALLOW_TRANSACTION", ex, "simulated step-up authentication passed and no validated evidence contradicts it", ["R1"]),
                act("MONITOR_CARD", ex, "watch the card after a passed step-up", ["R1"])], "passed"
    if outcome == "failed":
        out = [act("DECLINE_TRANSACTION", ex, "simulated step-up authentication failed", ["R1"]), act("CREATE_CASE", ex, "evidence requested and failed", ["3a"]),
               act("VERIFY_WITH_CUSTOMER", ex, "confirm with the cardholder before any block (R1)", ["R1"]), act("MONITOR_CARD", ex, "failed step-up", ["R1"])]
        if fired & SHARED_ORIGIN and ctx["connected_card_ids"]:
            out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the evidence device", ["R6"]))
        if ex > 500:
            out.append(act("ESCALATE_TO_ANALYST", ex, "exposure > $500 after a failed step-up", ["R8"]))
        return _dedupe(out), "failed"
    out = [act("MONITOR_CARD", ex, "R4: no reply within 24 h", ["R4"]), act("DECLINE_TRANSACTION", ex, "R4: pending authorisations", ["R4"])]
    if ex > 500:
        out.append(act("ESCALATE_TO_ANALYST", ex, "R4: no reply and exposure > $500", ["R4"]))
    return _dedupe(out), "no_reply"


def _dedupe(acts):
    seen, out = set(), []
    for a in acts:
        if a.action not in seen:
            seen.add(a.action)
            out.append(a)
    return out
