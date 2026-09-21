"""Pluggable parts that are NOT graph tools: the evidence-request responder (simulator hook) and the explanation writer.

* `PendingResponder` (default): no response is ever invented; the case stays `open`.
* `Responder` protocol: any object with `respond(request, case_ctx) -> (outcome, text)` (the seeded `EvidenceSimulator`, or a test double).
* `TemplateExplainer` (default): deterministic prose built only from Evidence objects. The LLM explainer (agent/llm.py) has the same interface and falls back to this one.

The explanation ALWAYS separates three different things (they must never be blended):
  1. EVIDENCE STRENGTH        what validated evidence exists (independent sources, tiers), and what is only weak context;
  2. INVESTIGATION UNCERTAINTY what is still unknown, conflicting or pending (open questions, evidence requested, missing data);
  3. CALIBRATED PROBABILITY   a probability exists only if the calibration procedure passed its validation gates. On the available labels it did not, so the explanation
                              states that no probability is given; it never substitutes a placeholder or an assumed prevalence.
"""
import re


class PendingResponder:
    def respond(self, request, case_ctx):
        return "pending", ""


STRENGTH_TEXT = {"none": "no validated evidence", "weak": "only weak context signals (not independent proof)", "moderate": "one validated independent source",
                 "strong": "strong validated evidence"}


def sections(inp):
    """Return the three separated explanation parts, ONE sentence each (used by the template and as the structured facts for the LLM)."""
    ev = inp["evidence"]
    u = inp["uncertainty"]
    sup = [e for e in ev if e["direction"] == "supports_fraud" and e["rating"] in ("HIGH", "MEDIUM") and not e.get("simulated")]
    low = [e for e in ev if e["direction"] == "supports_fraud" and e["rating"] == "LOW" and not e.get("simulated")]
    sim = [e for e in ev if e.get("simulated")]
    denial = u.get("customer_statement") == "denial"
    validated = u["independent_sources"] - (1 if denial else 0)
    p1 = [f"Evidence strength: {u['evidence_strength']} ({STRENGTH_TEXT[u['evidence_strength']]}; {validated} validated independent source(s)"
          + (" plus the customer's own statement" if denial else "") + ")"]
    if sup:
        p1.append("validated evidence: " + "; ".join(f"{e['summary']} [{e['id']}]" for e in sup))
    pd = set(u.get("pattern_defining") or [])
    defining = [e for e in low if e.get("signal_id") in pd]
    low = [e for e in low if e.get("signal_id") not in pd]
    if defining:
        p1.append("pattern-defining characteristics of the ring rule, not counted as corroboration: " + "; ".join(f"{e['summary']} [{e['id']}]" for e in defining))
    if low:
        p1.append("weak context only: " + "; ".join(f"{e['summary']} [{e['id']}]" for e in low[:4]))
    if u.get("customer_statement") == "denial":
        p1.append("customer report: the customer states they did not make the transaction (trigger evidence, unverified)")
    if sim:
        p1.append("simulated response (not real evidence): " + "; ".join(f"{e['summary']} [{e['id']}]" for e in sim))
    s1 = "; ".join(p1) + "."
    p2 = [f"Investigation uncertainty: {u['level']}"]
    if u["conflicts"]:
        p2.append("conflicts: " + ", ".join(u["conflicts"]))
    if u["missing"]:
        p2.append("not available or unresolved: " + ", ".join(u["missing"]))
    r = inp.get("request")
    if r:
        p2.append(f"evidence requested ({r['type']}), outcome: {r['outcome']}")
    s2 = "; ".join(p2) + "."
    cp = u["calibrated_probability"]
    if cp["calibrated"] and cp["value"] is not None:
        s3 = f"Calibrated probability: {cp['value']:.2f} (calibration method {cp['method']})."
    else:
        s3 = ("Calibrated probability: not available, because the calibration procedure did not pass its validation gates on the available history"
              + (f" ({'; '.join(cp['gate_notes'])})" if cp.get("gate_notes") else "") + ", so no probability is stated.")
    return s1, s2, s3


class TemplateExplainer:
    name = "template"

    def explain(self, inp):
        s1, s2, s3 = sections(inp)
        acts = ", ".join(f"{a['action']} ({a['route']}, {'/'.join(a['rules']) or 'no rule'})" for a in inp["final_actions"])
        text = f"{s1} {s2} {s3} Recommended (not executed): {acts}."
        return text if check_citations(text, {e["id"] for e in inp["evidence"]}) else "explanation failed citation check"


_CITE = re.compile(r"\[(E\d+)\]")


def check_citations(text, valid_ids):
    """Every evidence citation must refer to an evidence id that exists."""
    return all(c in valid_ids for c in _CITE.findall(text))
