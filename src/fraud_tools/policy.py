"""Fraud Policy v1.0 (README) as data: actions, approval routes, rules R1-R10, case-vs-report and stopping rules.

`get_policy_context` returns POLICY, clearly separated from EVIDENCE. It never decides an action and never estimates a probability
(calibration is an open Phase-9B/9C item). Text is a faithful condensation of the README policy; ids/route names are exact.
"""
ACTIONS = ["ALLOW_TRANSACTION", "DECLINE_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH",
           "BLOCK_CARD", "BLOCK_ALL_CARDS", "GENERATE_REPORT", "CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"]
ROUTES = {
    "auto": ["ALLOW_TRANSACTION", "MONITOR_CARD", "MONITOR_CONNECTED_CARDS", "WARN_CUSTOMER", "VERIFY_WITH_CUSTOMER", "STEP_UP_AUTH", "GENERATE_REPORT",
             "CREATE_CASE", "ESCALATE_TO_ANALYST", "CLOSE_NO_FRAUD"],
    "L1": ["DECLINE_TRANSACTION", "BLOCK_CARD (exposure <= $2,500)"],
    "L2": ["BLOCK_CARD (exposure > $2,500)", "BLOCK_ALL_CARDS (always)", "FILE_REPORT (always)"],
}
BLOCK_CARD_L1_MAX_EXPOSURE = 2500.0

RULES = {
    "R1": {"title": "Verify before you block on a weak signal",
           "when": "The case rests on a single signal (including a risk score alone) and the assessed fraud probability is below 0.70.",
           "actions": ["VERIFY_WITH_CUSTOMER or STEP_UP_AUTH before any block"], "note": "Blocking a legitimate customer on one signal is a policy breach."},
    "R2": {"title": "Customer denies the transaction", "when": "The customer denies the transaction.",
           "actions": ["BLOCK_CARD", "CREATE_CASE", "FILE_REPORT if exposure > $1,000 or the case connects to a shared device profile or another card's fraud"],
           "requires": "a customer response (simulated in this exercise)"},
    "R3": {"title": "Customer confirms the transaction", "when": "The customer confirms the transaction.", "actions": ["CLOSE_NO_FRAUD (note the confirmation)"],
           "requires": "a customer response"},
    "R4": {"title": "No reply within 24 hours", "when": "No customer reply within 24 hours.",
           "actions": ["MONITOR_CARD", "DECLINE_TRANSACTION for pending authorizations", "escalate if exposure > $500"], "requires": "a customer non-response"},
    "R5": {"title": "Card testing", "when": "Three or more small online authorizations on one card within an hour, followed by a larger purchase.",
           "actions": ["DECLINE_TRANSACTION", "STEP_UP_AUTH", "BLOCK_CARD if a purchase over $100 has already cleared"],
           "evidence_signal": "S06"},
    "R6": {"title": "Shared origin",
           "when": "Several cards show fraud from the same device profile, billing region or recipient email in one window.",
           "actions": ["name the shared element", "CREATE_CASE", "FILE_REPORT", "MONITOR_CONNECTED_CARDS for every card that shares it"],
           "evidence_signals": ["S01", "S02a", "S02b"], "note": "The shared element must pass the hub eligibility rules (spec section 5); hub links are never evidence."},
    "R7": {"title": "Disputed but legitimate",
           "when": "The customer disputes a charge that matches their own recurring pattern (same merchant, same amount, monthly).",
           "actions": ["CREATE_CASE", "VERIFY_WITH_CUSTOMER", "WARN_CUSTOMER", "do not block"],
           "note": "The dataset has NO merchant field: this rule cannot be evaluated from the graph (open blocker B6)."},
    "R8": {"title": "Escalate when uncertain and exposed", "when": "Verdict is uncertain and exposure exceeds $500, or the evidence conflicts.",
           "actions": ["ESCALATE_TO_ANALYST"]},
    "R9": {"title": "Undocumented patterns",
           "when": "Activity fits none of the known patterns but the evidence shows coordinated or repeated abuse across customers.",
           "actions": ["CREATE_CASE", "FILE_REPORT", "ESCALATE_TO_ANALYST", "describe the pattern in your own words"], "evidence_signals": ["S01"]},
    "R10": {"title": "Never BLOCK_ALL_CARDS",
            "when": "Unless at least two of the customer's cards show confirmed fraud or the customer's credentials are confirmed compromised.",
            "actions": ["BLOCK_ALL_CARDS only under that condition"]},
}
CASE_VS_REPORT = {
    "CREATE_CASE": "Open a case when fraud probability reaches 0.30, whenever evidence is requested, or whenever a customer disputes a charge.",
    "FILE_REPORT": "Only if fraud is confirmed or strongly suspected AND (exposure > $1,000 OR the activity connects to a shared device profile / shared region cluster / "
                   "another customer's fraud OR the pattern is coordinated/undocumented, R9). A report always has a case behind it.",
}
EXPOSURE = "Sum of the absolute amounts of every transaction identified as part of the fraud episode, including the flagged one."
STOPPING = ["Fraud probability >= 0.85 or <= 0.15, supported by at least two independent pieces of evidence",
            "A verification response settles the question", "Further steps are unlikely to change the decision (state so in stop_reason)"]
EXECUTION = "The agent recommends. Only `auto` actions may be executed by the agent; L1 and L2 actions are recommended with the route stated and wait for a human."


def policy_context(fired_signal_ids=None):
    """Return the policy, plus which rules the FIRED signals make potentially relevant (a pointer, not a decision)."""
    fired = set(fired_signal_ids or [])
    pointers = []
    for rid, r in RULES.items():
        sigs = set([r["evidence_signal"]] if "evidence_signal" in r else r.get("evidence_signals", []))
        hit = sorted(sigs & fired)
        if hit:
            pointers.append({"rule": rid, "because_signals": hit,
                             "still_required": r.get("requires", "a probability assessment and, where the rule says so, a customer response"),
                             "type": "policy_pointer_not_a_decision"})
    if fired:
        pointers.append({"rule": "R1", "because_signals": sorted(fired),
                         "still_required": "R1 applies if the case rests on ONE independent evidence source and the assessed probability is below 0.70",
                         "type": "policy_pointer_not_a_decision"})
    return {
        "kind": "policy",
        "notice": "This is Fraud Policy v1.0, not evidence. It does not choose an action or a probability.",
        "rules": RULES, "actions": ACTIONS, "routes": ROUTES, "block_card_l1_max_exposure_usd": BLOCK_CARD_L1_MAX_EXPOSURE,
        "case_vs_report": CASE_VS_REPORT, "exposure_definition": EXPOSURE, "stopping": STOPPING, "execution": EXECUTION,
        "evidence_precedence": "Evidence hierarchy: customer response > shared-origin device evidence > policy sequences/burst > amount history > everything else; "
                               "risk_score is an input and never a verdict; hub links are never evidence.",
        "potentially_relevant": pointers,
    }
