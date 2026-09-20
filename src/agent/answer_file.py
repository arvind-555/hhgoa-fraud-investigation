"""Answer-file assembler + validator: agent case record -> `cases/<case_id>.json` in the exact README "Answer Format".

The answer file contains ONLY the README fields (no internal extras). Internal provenance (simulator seed, calibration gates, exposure scope, content hash) stays in the FI_Case
vertex. Simulated customer/step-up evidence remains visibly simulated inside the README fields: the evidence claim and the assumed_response start with "[SIMULATED]".

fraud_probability: this project keeps it null (project decision: calibration failed its validation gates, no fabricated or prevalence-assumed number). The README types the field as
a number in 0-1, so `validate_answer` reports that as a WARNING (never an error) while `probability_policy == "null"`; a stated number is an ERROR unless probability_calibrated
(which is never true on the available data).

Validation (spec 8.6): schema and enums; ids exist (graph); affected txns have epoch <= as_of (graph); exposure == graph sum of |amount| over the deduplicated affected ids;
legitimate => no affected txns/exposure/report; sar.file <=> FILE_REPORT in final actions and blank sar otherwise; pattern_description non-empty iff undocumented; final == initial
=> what_changed == "nothing"; action/route pairs follow spec 8.1; nothing marked executed; tool_calls/tokens/latency_s equal the runner's measurement; written_to_graph true only if
the FI_Case vertex exists, its source case and as_of match and graph_case_id resolves; similar_prior_cases are closed cases visible at as_of by close time; no other case ids, no
forbidden keys, simulated evidence marked.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .actions import route_of
from .case_validator import FORBIDDEN_KEYS, PATTERN, STATUS, VERDICT, _keys, _strings
from .sar import sentences

TOP = ["case_id", "case", "evidence_requests", "next_best_actions", "sar", "stop_reason", "tool_calls", "tokens", "latency_s"]
CASE = ["status", "verdict", "fraud_probability", "pattern", "pattern_description", "affected_txn_ids", "first_suspicious_txn_id", "connected_card_ids",
        "connected_device_profiles", "exposure_usd", "evidence", "similar_prior_cases", "summary", "written_to_graph", "graph_case_id"]
SAR = ["file", "reason", "narrative", "subjects", "total_amount_usd", "activity_dates"]
EVIDENCE_SOURCE = {"graph", "document", "customer", "external"}
REQUEST_TYPE = {"customer_validation", "step_up_auth", "analyst_info"}
_RULE = re.compile(r"\b(R\d{1,2}|3a|3b)\b")


def strip_citations(text):
    return re.sub(r" ?\[E\d+\]", "", text)


def _reason(a):
    r = a["reason"]
    rules = a.get("rules") or []
    return r if (_RULE.search(r) or not rules) else f"{'/'.join(rules)}: {r}"


def assemble(record, measured=None):
    """Build the README-format answer from an agent case record. `measured` = {tool_calls, tokens, latency_s} from the runner (defaults to the record's own measurements)."""
    c, nb = record["case"], record["next_best_actions"]
    m = measured or {"tool_calls": record["tool_calls"], "tokens": record["tokens"], "latency_s": record["latency_s"]}
    ev = []
    for e in c["evidence"]:
        ref = "evidence_request:1" if e["source"] == "customer" else e["ref"] if e["source"] == "document" else f"tool:{e['ref']}"
        ev.append({"claim": e["claim"], "source": e["source"], "ref": ref, "entity_ids": [str(x) for x in e["entity_ids"]]})
    reqs = [{"type": r["type"], "asked_after_step": int(r["asked_after_step"]), "assumed_response": r["assumed_response"] if r["assumed_response"].startswith("[SIMULATED]")
             else "[SIMULATED] " + (r["assumed_response"] or "no response yet; evidence remains pending")} for r in record["evidence_requests"]]
    answer = {
        "case_id": record["case_id"],
        "case": {"status": c["status"], "verdict": c["verdict"], "fraud_probability": c["fraud_probability"], "pattern": c["pattern"],
                 "pattern_description": c["pattern_description"], "affected_txn_ids": list(c["affected_txn_ids"]), "first_suspicious_txn_id": c["first_suspicious_txn_id"],
                 "connected_card_ids": list(c["connected_card_ids"]), "connected_device_profiles": list(c["connected_device_profiles"]), "exposure_usd": c["exposure_usd"],
                 "evidence": ev, "similar_prior_cases": list(c["similar_prior_cases"]), "summary": strip_citations(c["summary"]), "written_to_graph": bool(c["written_to_graph"]),
                 "graph_case_id": c["graph_case_id"] if c["written_to_graph"] else ""},
        "evidence_requests": reqs,
        "next_best_actions": {"initial": [{"action": a["action"], "route": a["route"], "reason": _reason(a)} for a in nb["initial"]],
                              "final": [{"action": a["action"], "route": a["route"], "reason": _reason(a)} for a in nb["final"]], "what_changed": nb["what_changed"]},
        "sar": {k: record["sar"][k] for k in SAR},
        "stop_reason": record["stop_reason"], "tool_calls": int(m["tool_calls"]), "tokens": int(m["tokens"]), "latency_s": round(float(m["latency_s"]), 2)}
    return answer


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def schema_errors(a):
    e = []
    if not isinstance(a, dict) or list(a) != TOP and sorted(a) != sorted(TOP):
        return [f"schema: top-level keys must be exactly {TOP}"]
    c = a["case"]
    if sorted(c) != sorted(CASE):
        e.append(f"schema: case keys must be exactly {CASE}")
        return e
    if not isinstance(a["case_id"], str) or not a["case_id"]:
        e.append("schema: case_id")
    if c["status"] not in STATUS:
        e.append(f"schema: status {c['status']!r}")
    if c["verdict"] not in VERDICT:
        e.append(f"schema: verdict {c['verdict']!r}")
    if c["pattern"] not in PATTERN:
        e.append(f"schema: pattern {c['pattern']!r}")
    for k in ("affected_txn_ids", "connected_card_ids", "connected_device_profiles", "similar_prior_cases"):
        if not (isinstance(c[k], list) and all(isinstance(x, str) for x in c[k])):
            e.append(f"schema: case.{k} must be a list of strings")
    for k in ("pattern_description", "first_suspicious_txn_id", "summary", "graph_case_id"):
        if not isinstance(c[k], str):
            e.append(f"schema: case.{k} must be a string")
    if not _is_num(c["exposure_usd"]) or c["exposure_usd"] < 0:
        e.append("schema: exposure_usd must be a non-negative number")
    if not isinstance(c["written_to_graph"], bool):
        e.append("schema: written_to_graph must be boolean")
    for x in c["evidence"]:
        if sorted(x) != ["claim", "entity_ids", "ref", "source"] or x["source"] not in EVIDENCE_SOURCE or not isinstance(x["entity_ids"], list):
            e.append("schema: evidence item must be {claim, source(graph|document|customer|external), ref, entity_ids}")
            break
    for r in a["evidence_requests"]:
        if sorted(r) != ["asked_after_step", "assumed_response", "type"] or r["type"] not in REQUEST_TYPE or not isinstance(r["asked_after_step"], int):
            e.append("schema: evidence_requests item must be {type, asked_after_step:int, assumed_response}")
            break
    nb = a["next_best_actions"]
    if sorted(nb) != ["final", "initial", "what_changed"]:
        e.append("schema: next_best_actions keys")
    else:
        for grp in ("initial", "final"):
            for x in nb[grp]:
                if sorted(x) != ["action", "reason", "route"]:
                    e.append(f"schema: next_best_actions.{grp} item must be {{action, route, reason}}")
                    break
    if sorted(a["sar"]) != sorted(SAR):
        e.append(f"schema: sar keys must be exactly {SAR}")
    if not isinstance(a["stop_reason"], str) or not a["stop_reason"]:
        e.append("schema: stop_reason")
    if not isinstance(a["tool_calls"], int) or not isinstance(a["tokens"], int) or not _is_num(a["latency_s"]):
        e.append("schema: tool_calls/tokens must be int and latency_s a number")
    return e


def rule_errors(a, measured=None, probability_policy="null"):
    """Returns (errors, warnings) that need no graph."""
    e, w = [], []
    c, nb, sar = a["case"], a["next_best_actions"], a["sar"]
    fp = c["fraud_probability"]
    if fp is None:
        if probability_policy == "null":
            w.append("fraud_probability is null (project decision: calibration failed its gates); the README types it as a number in 0-1, so the scorer may not accept null")
        else:
            e.append("fraud_probability must be a number in 0-1")
    elif not (_is_num(fp) and 0 <= fp <= 1):
        e.append("fraud_probability must be null or a number in 0-1")
    else:
        e.append("a probability is stated but none is calibrated (fabricated/assumed probabilities are not allowed)")
    aff = c["affected_txn_ids"]
    if len(set(aff)) != len(aff):
        e.append("affected_txn_ids contains duplicates")
    if c["verdict"] == "legitimate" and (aff or c["exposure_usd"]):
        e.append("legitimate => affected_txn_ids=[] and exposure_usd=0")
    if c["first_suspicious_txn_id"] and c["first_suspicious_txn_id"] not in aff:
        e.append("first_suspicious_txn_id must be one of affected_txn_ids (or empty)")
    if (c["pattern"] == "undocumented") != bool(c["pattern_description"]):
        e.append("pattern_description must be non-empty exactly when pattern is undocumented")
    same = [(x["action"], x["route"]) for x in nb["initial"]] == [(x["action"], x["route"]) for x in nb["final"]]
    if (nb["what_changed"] == "nothing") != same:
        e.append('what_changed must be "nothing" exactly when final == initial')
    from fraud_tools.policy import ACTIONS
    for x in nb["initial"] + nb["final"]:
        if x["action"] not in ACTIONS:
            e.append(f"unknown action {x['action']}")
        elif x["route"] != route_of(x["action"], c["exposure_usd"]):
            e.append(f"route for {x['action']} must be {route_of(x['action'], c['exposure_usd'])} at exposure {c['exposure_usd']}")
        if x["action"] == "BLOCK_ALL_CARDS":
            e.append("BLOCK_ALL_CARDS not permitted (R10)")
        if not _RULE.search(x["reason"]):
            e.append(f"reason for {x['action']} must cite a policy rule")
    filed = any(x["action"] == "FILE_REPORT" for x in nb["final"])
    if sar["file"] != filed:
        e.append("sar.file must equal (FILE_REPORT in final actions)")
    if not sar["file"]:
        if sar["narrative"] or sar["subjects"] or sar["total_amount_usd"] or sar["activity_dates"]:
            e.append("sar.file=false => narrative '', subjects [], total 0, activity_dates []")
    else:
        n = len(sentences(sar["narrative"]))
        if not 6 <= n <= 12:
            e.append(f"sar.narrative must have 6-12 sentences (has {n})")
        d = sar["activity_dates"]
        if not (isinstance(d, list) and len(d) == 2 and all(re.match(r"^\d{4}-\d{2}-\d{2}$", str(x)) for x in d) and d[0] <= d[1]):
            e.append("sar.activity_dates must be two YYYY-MM-DD dates, first <= last")
        if abs(float(sar["total_amount_usd"]) - float(c["exposure_usd"])) > 0.01:
            e.append("sar.total_amount_usd must equal exposure_usd")
        if not sar["subjects"]:
            e.append("sar.subjects must list the ids named")
        if re.search(r"probab|likelihood of fraud", sar["narrative"], re.I):
            e.append("sar.narrative states a probability")
    if re.search(r"\[E\d+\]", c["summary"]):
        e.append("summary still contains internal evidence citations [E#] (they are stripped on export)")
    if not 2 <= len(sentences(c["summary"])) <= 6:
        e.append(f"summary must have 2-6 sentences (has {len(sentences(c['summary']))})")
    for r in a["evidence_requests"]:
        if not r["assumed_response"].startswith("[SIMULATED]"):
            e.append("evidence_requests assumed_response must be marked [SIMULATED]")
    for x in c["evidence"]:
        if x["source"] == "customer" and not x["claim"].startswith("[SIMULATED]"):
            e.append("customer evidence must be marked [SIMULATED]")
    if (c["written_to_graph"]) != bool(c["graph_case_id"]):
        e.append("written_to_graph and graph_case_id must agree")
    if measured:
        for k in ("tool_calls", "tokens"):
            if a[k] != int(measured[k]):
                e.append(f"{k} must equal the measured value {measured[k]}")
        if abs(a["latency_s"] - float(measured["latency_s"])) > 0.05:
            e.append(f"latency_s must equal the measured value {measured['latency_s']}")
    if _keys(a, set()) & FORBIDDEN_KEYS:
        e.append(f"forbidden keys present: {sorted(_keys(a, set()) & FORBIDDEN_KEYS)}")
    others = {s for s in re.findall(r"HHG-\d+", " ".join(_strings(a, []))) if s != a["case_id"]}
    if others:
        e.append(f"other case ids present: {sorted(others)}")
    return e, w


def graph_errors(a, as_of_epoch, io):
    e = []
    c, sar = a["case"], a["sar"]
    aff = [int(x) for x in c["affected_txn_ids"]]
    jobs = [("Transaction", t) for t in aff] + [("Card", x) for x in c["connected_card_ids"]] + [("ClosedCase", x) for x in c["similar_prior_cases"]]
    for x in sar["subjects"]:
        jobs.append(("Customer", x) if re.match(r"^C\d{5}$", x) else ("Card", x) if re.match(r"^C\d{5}-K\d$", x) else ("DeviceProfile", x))
    jobs = list(dict.fromkeys(jobs))
    with ThreadPoolExecutor(8) as ex:
        res = dict(zip(jobs, ex.map(lambda j: io.get_vertex(*j), jobs)))
    for (vt, vid), v in res.items():
        if v is None:
            e.append(f"{vt} {vid} does not exist in the graph")
    if e:
        return e
    total = 0.0
    for t in aff:
        v = res[("Transaction", t)]
        if v["epoch"] > as_of_epoch:
            e.append(f"transaction {t} epoch {v['epoch']} is after as_of")
        total += abs(v["amount"])
    if abs(total - c["exposure_usd"]) > 0.01:
        e.append(f"exposure_usd {c['exposure_usd']} != graph sum {round(total, 2)} over the deduplicated affected transactions")
    for k in c["similar_prior_cases"]:
        if res[("ClosedCase", k)]["close_epoch"] > as_of_epoch:
            e.append(f"similar prior case {k} is not visible at as_of (closes after it)")
    if c["written_to_graph"]:
        v = io.get_vertex("FI_Case", c["graph_case_id"])
        if v is None:
            e.append(f"written_to_graph=true but {c['graph_case_id']} does not exist")
        elif v["source_case_id"] != a["case_id"] or v["as_of_epoch"] != as_of_epoch:
            e.append("graph case does not belong to this case/as_of")
    return e


def validate_answer(answer, as_of_epoch=None, io=None, measured=None, probability_policy="null"):
    """-> (errors, warnings). Errors block export."""
    e = schema_errors(answer)
    if e:
        return e, []
    errs, warns = rule_errors(answer, measured, probability_policy)
    if io is not None and as_of_epoch is not None and not errs:
        errs += graph_errors(answer, as_of_epoch, io)
    return errs, warns


class AnswerValidationError(Exception):
    def __init__(self, errors):
        super().__init__("; ".join(errors[:6]))
        self.errors = errors


def write_answer(answer, folder, as_of_epoch=None, io=None, measured=None):
    errs, warns = validate_answer(answer, as_of_epoch, io, measured)
    if errs:
        raise AnswerValidationError(errs)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{answer['case_id']}.json"
    path.write_text(json.dumps(answer, indent=2, ensure_ascii=False), encoding="utf-8")
    return path, warns
