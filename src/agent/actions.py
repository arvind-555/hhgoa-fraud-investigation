"""Deterministic next-best-action engine and decision matrix (recommendations only; nothing is executed).

The action table follows Fraud Policy v1.0 (README, spec section 8). It uses EVIDENCE STRENGTH, never a probability (calibration failed its validation gates, so no
probability exists). Policy thresholds phrased as probabilities are applied through evidence classes (docs/agent_orchestration.md). Routes follow spec 8.1 exactly.
The LLM never chooses actions, routes, exposure, verdicts or rule ids. No randomness anywhere: the same evidence always yields the same decision.

DECISION MATRIX (every row is subordinate to R1-R10, 3a and 3b; no rule is invented)

 class  evidence at as_of                                            initial actions                                              CREATE_CASE  FILE_REPORT      MONITOR_CONNECTED
 A      strong graph evidence (S01 ring, S02a, or >=2 independent    S01 ring: CREATE_CASE, ESCALATE (R9), FILE_REPORT (R9/R6),    yes (R6/R9)  yes (R9/R6; else  yes if connected
        validated sources)                                           MONITOR_CONNECTED_CARDS (R6), VERIFY. Other strong: CREATE,   when shared origin or   cards exist (R6)
                                                                     DECLINE, FILE_REPORT if shared origin, MONITOR_CONNECTED       exposure > $1,000)
 T+     customer REPORT (immutable denial) + one validated source    R2: BLOCK_CARD, CREATE_CASE, FILE_REPORT if exposure > $1,000 yes (R2)     R2 conditions    yes if connected
                                                                     or shared origin, MONITOR_CONNECTED_CARDS                                                 cards exist
 T      customer REPORT, only weak / no graph evidence               R1: VERIFY_WITH_CUSTOMER, MONITOR_CARD, CREATE_CASE (3a)      yes (3a)     no               no
 B      one validated independent source (S06/S07/S08/S02b)          R1: STEP_UP_AUTH (online) / VERIFY_WITH_CUSTOMER, CREATE_CASE, yes (3a)      no               S02b + connected
                                                                     MONITOR_CARD (R5 sequence: DECLINE_TRANSACTION + STEP_UP_AUTH)
 C      weak context only                                            R1: STEP_UP_AUTH / VERIFY_WITH_CUSTOMER, MONITOR_CARD,        yes when     no               no
                                                                     CREATE_CASE whenever evidence is requested (3a); no evidence
                                                                     and no alert: ALLOW_TRANSACTION
 After the verification response (D positive / E negative / F no reply):
 F no reply (assumed absence; R4)   B/C/T: MONITOR_CARD, DECLINE_TRANSACTION (pending authorisations), CREATE_CASE kept, ESCALATE if exposure > $500; A: initial + R4
 D positive  customer confirms (R3): B/C -> CLOSE_NO_FRAUD; A -> initial actions kept + ESCALATE (R8: conflict; the flagged customer says nothing about the other cards);
             T -> ESCALATE + CREATE_CASE (a customer cannot recant an immutable report by simulation). Step-up passed: B/C -> ALLOW + MONITOR; A -> initial + ESCALATE
 E negative  customer denies (R2): BLOCK_CARD, CREATE_CASE, FILE_REPORT/MONITOR_CONNECTED as above. Step-up failed: DECLINE, CREATE_CASE, VERIFY, MONITOR, ESCALATE if > $500

VERDICT (evidence -> strength -> additional evidence -> policy -> decision): A: fraud; T+: fraud; denied: fraud; confirmed + CLOSE_NO_FRAUD: legitimate; passed + ALLOW:
legitimate; ALLOW only (no evidence, no alert): legitimate; everything else: UNCERTAIN. Evidence that is insufficient yields UNCERTAIN, never a guessed verdict.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fraud_tools.policy import BLOCK_CARD_L1_MAX_EXPOSURE  # noqa: E402

from .schema import Action, Uncertainty

SHARED_ORIGIN = {"S01", "S02a", "S02b"}
CUSTOMER_REPORTS = ("customer_report", "customer_complaint")


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


def is_report(ctx):
    return ctx["trigger_type"] in CUSTOMER_REPORTS


def decision_class(unc: Uncertainty, ctx):
    """A strong | T+ corroborated customer report | T customer report only | B one validated source | C weak / none."""
    if unc.evidence_strength == "strong":
        return "A"
    if is_report(ctx):
        return "T+" if unc.evidence_strength == "moderate" else "T"
    return "B" if unc.evidence_strength == "moderate" else "C"


def default_verification(unc, ctx):
    """The verification the policy recommends first (R1): the request the agent actually makes must match it."""
    if decision_class(unc, ctx) in ("A", "T") or ctx["channel"] != "online":
        return "customer_validation"
    return "step_up_auth"


def filing_conditions(exposure, fired):
    """Policy 3a: conditions of which at least one must hold (besides fraud confirmed / strongly suspected) for FILE_REPORT."""
    fired = set(fired)
    out = []
    if exposure > 1000:
        out.append("exposure above $1,000")
    if fired & SHARED_ORIGIN:
        out.append("activity connects to a shared device profile" + (" / another customer's confirmed fraud" if fired & {"S02a", "S02b"} else ""))
    if "S01" in fired:
        out.append("coordinated / undocumented pattern (R9)")
    return out


def file_report_condition(exposure, shared_origin, other_customer_fraud, undocumented):
    return exposure > 1000 or shared_origin or other_customer_fraud or undocumented


def _r2(ex, ctx, fired, ev, why):
    out = [act("BLOCK_CARD", ex, why, ["R2"]), act("CREATE_CASE", ex, "R2", ["R2"])]
    if filing_conditions(ex, fired):
        out.append(act("FILE_REPORT", ex, "R2: " + "; ".join(filing_conditions(ex, fired)), ["R2", "R6"] if set(fired) & SHARED_ORIGIN else ["R2"]))
    if ctx["connected_card_ids"]:
        out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the eligible device", ["R6"], ev("S01", "S02a", "S02b")))
    return out


def initial_actions(unc: Uncertainty, ctx, verify_with=None):
    """ctx: dict(exposure, fired, channel, trigger_type, model_band, connected_card_ids, evidence_ids_by_signal). verify_with: the request type the agent makes."""
    ex, fired, ev = ctx["exposure"], set(ctx["fired"]), ctx["evidence_ids_by_signal"]
    e = lambda *sids: [i for s in sids for i in ev.get(s, [])]      # noqa: E731
    k = decision_class(unc, ctx)
    vw = verify_with or default_verification(unc, ctx)
    verify = act("STEP_UP_AUTH", ex, "R1: verify before any block", ["R1"]) if vw == "step_up_auth" else act("VERIFY_WITH_CUSTOMER", ex, "R1: verify before any block", ["R1"])
    shared = bool(fired & SHARED_ORIGIN)
    out = []
    if k == "A":
        if "S01" in fired and not (fired & {"S02a", "S02b", "S06"}):
            out += [act("CREATE_CASE", ex, "R9: coordinated device-sharing pattern across customers, no documented pattern fits", ["R9"], e("S01")),
                    act("ESCALATE_TO_ANALYST", ex, "R9: undocumented coordinated pattern", ["R9"], e("S01")),
                    act("FILE_REPORT", ex, "R9/R6: coordinated activity across customers", ["R9", "R6"], e("S01"))]
        else:
            out += [act("CREATE_CASE", ex, "evidence strong enough to open a case (policy 3a)", ["3a"], e(*fired)),
                    act("DECLINE_TRANSACTION", ex, "shared-origin evidence with >=2 independent sources; approval required", ["R6"], e(*fired))]
            if shared or ex > 1000:
                out += [act("FILE_REPORT", ex, "R6/3a: " + "; ".join(filing_conditions(ex, fired)), ["R6"], e("S02a", "S02b", "S01"))]
        if ctx["connected_card_ids"]:
            out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the eligible device", ["R6"], e("S01", "S02a", "S02b")))
        out.append(verify if unc.needs_more_evidence else act("VERIFY_WITH_CUSTOMER", ex, "confirm with the cardholder before any block (R1 spirit; block requires approval)", ["R1"]))
    elif k == "T+":
        out += _r2(ex, ctx, fired, e, "R2: the customer report denies the transaction and validated evidence corroborates it")
    elif k == "T":
        out += [act("VERIFY_WITH_CUSTOMER", ex, "R1: the customer report is unverified and graph evidence is weak: verify before any block", ["R1"]),
                act("MONITOR_CARD", ex, "watch the card while the dispute is verified", ["R1"]),
                act("CREATE_CASE", ex, "customer dispute always opens a case (3a)", ["3a"])]
    elif k == "B":
        if "S06" in fired:
            out += [act("DECLINE_TRANSACTION", ex, "R5: card-testing sequence then a larger purchase", ["R5"], e("S06")), act("STEP_UP_AUTH", ex, "R5", ["R5"], e("S06"))]
        else:
            out += [verify]
        out += [act("CREATE_CASE", ex, "evidence requested / moderate evidence: open a case (policy 3a)", ["3a"], e(*fired)), act("MONITOR_CARD", ex, "watch the card while evidence is pending", ["R1"])]
        if fired & SHARED_ORIGIN and ctx["connected_card_ids"]:
            out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the eligible device", ["R6"], e("S02b")))
    elif unc.evidence_strength == "weak" or ctx["trigger_type"] != "risk_score" or ctx["model_band"] == "high":
        out += [verify, act("MONITOR_CARD", ex, "watch the card while evidence is pending", ["R1"])]
        if unc.needs_more_evidence:
            out.append(act("CREATE_CASE", ex, "evidence is requested: open a case (3a)", ["3a"]))
    else:
        out.append(act("ALLOW_TRANSACTION", ex, "no validated evidence and the model alert is not high", ["R1"]))
    if unc.conflicts and ex > 500 and unc.level != "low":
        out.append(act("ESCALATE_TO_ANALYST", ex, "R8: conflicting evidence and exposure > $500", ["R8"]))
    elif unc.level == "high" and ex > 500:
        out.append(act("ESCALATE_TO_ANALYST", ex, "R8: uncertain and exposure > $500", ["R8"]))
    return _dedupe(out)


def final_actions(initial, unc: Uncertainty, ctx, outcome):
    """Actions after the response to the evidence request. `outcome` in pending (nothing was requested) | no_reply | confirmed | denied | passed | failed."""
    ex, fired, ev = ctx["exposure"], set(ctx["fired"]), ctx["evidence_ids_by_signal"]
    k = decision_class(unc, ctx)
    has = lambda name: any(a.action == name for a in initial)      # noqa: E731
    if outcome == "pending":
        return initial, "pending"
    if outcome == "denied":
        r2 = _r2(ex, ctx, fired, lambda *s: [i for x in s for i in ev.get(x, [])], "R2: customer denies the transaction")
        return _dedupe(r2 + [a for a in initial if a.action not in ("VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "MONITOR_CARD")] if k == "A" else r2), "denied"
    if outcome == "confirmed":
        if k == "A":
            return _dedupe(list(initial) + [act("ESCALATE_TO_ANALYST", ex, "R8/R3: the flagged customer confirmed, but strong shared-origin evidence remains and concerns other cards", ["R8", "R3"])]), "confirmed"
        if k in ("T", "T+"):
            return _dedupe([act("ESCALATE_TO_ANALYST", ex, "R8: the customer's own report and a later confirmation conflict; a human must resolve it", ["R8"]),
                            act("CREATE_CASE", ex, "customer dispute always opens a case (3a)", ["3a"])]), "confirmed"
        return [act("CLOSE_NO_FRAUD", ex, "R3: customer confirms the transaction (confirmation noted in the case)", ["R3"])], "confirmed"
    if outcome == "passed":
        if k == "A":
            return _dedupe(list(initial) + [act("ESCALATE_TO_ANALYST", ex, "R8: step-up passed for the flagged card, but strong shared-origin evidence remains and concerns other cards", ["R8"])]), "passed"
        if k in ("T", "T+"):
            return initial, "passed"
        return [act("ALLOW_TRANSACTION", ex, "step-up authentication passed and no validated evidence contradicts it", ["R1"]),
                act("MONITOR_CARD", ex, "watch the card after a passed step-up", ["R1"])], "passed"
    if outcome == "failed":
        if k == "A":
            add = [act("DECLINE_TRANSACTION", ex, "step-up authentication failed", ["R1"])]
            if ex > 500:
                add.append(act("ESCALATE_TO_ANALYST", ex, "exposure > $500 after a failed step-up", ["R8"]))
            return _dedupe(list(initial) + add), "failed"
        out = [act("DECLINE_TRANSACTION", ex, "step-up authentication failed", ["R1"]), act("CREATE_CASE", ex, "evidence requested and failed", ["3a"]),
               act("VERIFY_WITH_CUSTOMER", ex, "confirm with the cardholder before any block (R1)", ["R1"]), act("MONITOR_CARD", ex, "failed step-up", ["R1"])]
        if fired & SHARED_ORIGIN and ctx["connected_card_ids"]:
            out.append(act("MONITOR_CONNECTED_CARDS", ex, "R6: cards share the evidence device", ["R6"]))
        if ex > 500:
            out.append(act("ESCALATE_TO_ANALYST", ex, "exposure > $500 after a failed step-up", ["R8"]))
        return _dedupe(out), "failed"
    # no_reply (R4): the assumption of absence. Nothing about the customer is claimed.
    r4 = [act("MONITOR_CARD", ex, "R4: no reply within 24 h", ["R4"]), act("DECLINE_TRANSACTION", ex, "R4: pending authorisations (if any)", ["R4"])]
    if ex > 500:
        r4.append(act("ESCALATE_TO_ANALYST", ex, "R4: no reply and exposure > $500", ["R4"]))
    if k == "A":
        return _dedupe(list(initial) + r4), "no_reply"
    keep = [a for a in initial if a.action in ("CREATE_CASE", "MONITOR_CONNECTED_CARDS", "FILE_REPORT", "BLOCK_CARD")]
    return _dedupe(r4 + keep), "no_reply"


def decide_verdict(unc: Uncertainty, ctx, outcome, final):
    """(verdict, status). Derived only from evidence class, the response (if any) and the policy actions: never from a random draw."""
    k = decision_class(unc, ctx)
    acts = {a.action for a in final}
    esc = "escalated" if "ESCALATE_TO_ANALYST" in acts else "open"
    if outcome == "denied":
        return "fraud", "closed_fraud"
    if k == "T+":
        return "fraud", "closed_fraud"
    if k == "A":
        return "fraud", esc
    if outcome == "confirmed" and "CLOSE_NO_FRAUD" in acts:
        return "legitimate", "closed_legitimate"
    if outcome == "passed" and "ALLOW_TRANSACTION" in acts:
        return "legitimate", "closed_legitimate"
    if acts == {"ALLOW_TRANSACTION"}:
        return "legitimate", "closed_legitimate"
    return "uncertain", esc


def policy_gaps(unc: Uncertainty, ctx, outcome, verdict, initial, final):
    """Actions the policy REQUIRES that are missing (empty list = conformant). Guard against silently dropping R6 / R9 / 3a actions."""
    fired, ex = set(ctx["fired"]), ctx["exposure"]
    fin, ini = {a.action for a in final}, {a.action for a in initial}
    gaps = []
    if verdict == "fraud" and filing_conditions(ex, fired) and "FILE_REPORT" not in fin:
        gaps.append("3a/R6/R9: FILE_REPORT required (fraud strongly suspected and " + "; ".join(filing_conditions(ex, fired)) + ")")
    if fired & SHARED_ORIGIN and ctx["connected_card_ids"] and "MONITOR_CONNECTED_CARDS" not in fin:
        gaps.append("R6: MONITOR_CONNECTED_CARDS required for every card that shares the evidence device")
    if (verdict == "fraud" or fired & SHARED_ORIGIN) and "CREATE_CASE" not in fin and "BLOCK_CARD" not in fin:
        gaps.append("3a/R6/R9: CREATE_CASE required")
    if outcome != "pending" and "CREATE_CASE" not in ini:
        gaps.append("3a: an evidence request must open a case (CREATE_CASE in the initial actions)")
    if is_report(ctx) and "CREATE_CASE" not in fin:
        gaps.append("3a: a customer dispute always opens a case")
    return gaps


def _dedupe(acts):
    seen, out = set(), []
    for a in acts:
        if a.action not in seen:
            seen.add(a.action)
            out.append(a)
    return out
