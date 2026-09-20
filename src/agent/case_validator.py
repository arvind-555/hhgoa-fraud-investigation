"""Case-write validator (spec 8.6 / 13-H / Q18). Runner-side; reads the graph through GraphIO. A case that violates any invariant is REJECTED (no write).

`validate(record, as_of_epoch, io=None)` returns a list of violation strings ([] = valid). With `io=None` only the offline (structural) checks run;
with a GraphIO the id/epoch/amount checks run against the graph. `assert_valid` raises CaseValidationError with every violation.

Invariants
 S1  required fields, enums, ranges (status/verdict/pattern, probability in [0,1], route in auto|L1|L2, actions from the policy list)
 S2  legitimate => no affected txns, no exposure, no FILE_REPORT; pattern_description non-empty iff pattern == undocumented; first_suspicious in affected
 S3  affected_txn_ids unique; every action/route pair follows spec 8.1 (BLOCK_CARD route follows exposure); nothing executed; no BLOCK_ALL_CARDS
 S4  what_changed == "nothing" iff final == initial actions
 S5  a probability may be stated only if probability_calibrated and source == calibration_table; otherwise fraud_probability must be null
 S6  explanation: every [E#] citation in the summary refers to an evidence_id of the case; evidence ids unique
 S7  simulated evidence is marked: every customer/step-up evidence item is simulated and starts with [SIMULATED]; ids match simulated_evidence_ids;
     evidence_requests entries are simulated
 S9  SAR: file <=> FILE_REPORT in final actions; file=false => empty narrative/subjects, total 0, no dates; file=true => 6-12 sentences, YYYY-MM-DD dates (first <= last),
     total == exposure, subjects non-empty, no probability stated, identifiers/amounts only from the case
 S8  no leak: no benchmark case id other than the case's own, no forbidden keys (risk_score, model_score, snap_class, as_of)
 G1  (graph) every card, customer, device, transaction and prior case id exists; the flagged txn belongs to the card and the card to the customer
 G2  (graph) every affected txn has epoch <= as_of; every transaction of a connected card sits on a cited device (for other-card transactions)
 G3  (graph) exposure_usd == sum(|amount|) of the affected txns, read from the graph (not from the agent)
 G4  (graph) similar prior cases are ClosedCase vertices with close_epoch <= as_of (visibility by close time, never open time)
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fraud_tools.policy import ACTIONS  # noqa: E402

from .actions import route_of  # noqa: E402
from .sar import sentences as SAR_sentences  # noqa: E402

STATUS = {"open", "closed_fraud", "closed_legitimate", "escalated"}
VERDICT = {"fraud", "legitimate", "uncertain"}
PATTERN = {"card_testing", "card_not_present_fraud", "card_not_present_new_device", "out_of_region_use", "account_takeover", "undocumented", "none"}
ROUTES = {"auto", "L1", "L2"}
FORBIDDEN_KEYS = {"risk_score", "model_score", "snap_class", "snap_customers", "as_of", "as_of_epoch"}
_CITE = re.compile(r"\[(E\d+)\]")
_BENCH = re.compile(r"HHG-\d+")


class CaseValidationError(Exception):
    def __init__(self, violations):
        super().__init__("; ".join(violations[:8]) + (f" (+{len(violations) - 8} more)" if len(violations) > 8 else ""))
        self.violations = violations


def _keys(x, out):
    if isinstance(x, dict):
        for k, v in x.items():
            out.add(str(k))
            _keys(v, out)
    elif isinstance(x, list):
        for v in x:
            _keys(v, out)
    return out


def _strings(x, out):
    if isinstance(x, str):
        out.append(x)
    elif isinstance(x, dict):
        for v in x.values():
            _strings(v, out)
    elif isinstance(x, list):
        for v in x:
            _strings(v, out)
    return out


def structural(record):
    v = []
    try:
        c, nb = record["case"], record["next_best_actions"]
        cid = record["case_id"]
    except KeyError as e:
        return [f"S1 missing top-level field {e}"]
    for f in ("status", "verdict", "fraud_probability", "pattern", "pattern_description", "affected_txn_ids", "first_suspicious_txn_id", "connected_card_ids",
              "connected_device_profiles", "exposure_usd", "evidence", "similar_prior_cases", "summary"):
        if f not in c:
            v.append(f"S1 case.{f} missing")
    if v:
        return v
    if c["status"] not in STATUS:
        v.append(f"S1 status {c['status']!r}")
    if c["verdict"] not in VERDICT:
        v.append(f"S1 verdict {c['verdict']!r}")
    if c["pattern"] not in PATTERN:
        v.append(f"S1 pattern {c['pattern']!r}")
    fp = c["fraud_probability"]
    if fp is not None and not (isinstance(fp, (int, float)) and not isinstance(fp, bool) and 0 <= fp <= 1):
        v.append("S1 fraud_probability must be null or a number in [0,1]")
    if not isinstance(c["exposure_usd"], (int, float)) or c["exposure_usd"] < 0:
        v.append("S1 exposure_usd must be a non-negative number")
    acts_i, acts_f = nb.get("initial", []), nb.get("final", [])
    for a in acts_i + acts_f:
        if a["action"] not in ACTIONS:
            v.append(f"S1 unknown action {a['action']!r}")
        if a["route"] not in ROUTES:
            v.append(f"S1 unknown route {a['route']!r}")
        elif a["action"] in ACTIONS and a["route"] != route_of(a["action"], c["exposure_usd"]):
            v.append(f"S3 {a['action']} route {a['route']} != {route_of(a['action'], c['exposure_usd'])} (exposure {c['exposure_usd']})")
        if a.get("executed"):
            v.append(f"S3 action {a['action']} marked executed")
        if a["action"] == "BLOCK_ALL_CARDS":
            v.append("S3 BLOCK_ALL_CARDS is not permitted (R10)")
    aff = c["affected_txn_ids"]
    if len(set(aff)) != len(aff):
        v.append("S3 affected_txn_ids contains duplicates")
    if c["verdict"] == "legitimate":
        if aff or c["exposure_usd"]:
            v.append("S2 legitimate case has affected transactions or exposure")
        if any(a["action"] == "FILE_REPORT" for a in acts_f):
            v.append("S2 legitimate case files a report")
    if (c["pattern"] == "undocumented") != bool(c["pattern_description"]):
        v.append("S2 pattern_description must be non-empty exactly when pattern is undocumented")
    if c["first_suspicious_txn_id"] and c["first_suspicious_txn_id"] not in aff:
        v.append("S2 first_suspicious_txn_id not in affected_txn_ids")
    same = [a["action"] for a in acts_i] == [a["action"] for a in acts_f]
    if (nb.get("what_changed") == "nothing") != same:
        v.append("S4 what_changed inconsistent with initial/final actions")
    if c.get("probability_calibrated") and (c.get("probability_source") != "calibration_table" or fp is None):
        v.append("S5 probability_calibrated=true requires a number from probability_source=calibration_table")
    if not c.get("probability_calibrated") and fp is not None:
        v.append("S5 an uncalibrated probability must not be stated (fraud_probability must be null)")
    # explanation + evidence
    ids = [e.get("evidence_id") for e in c["evidence"]]
    if len(set(ids)) != len(ids):
        v.append("S6 duplicate evidence ids")
    for cite in _CITE.findall(c["summary"]):
        if cite not in ids:
            v.append(f"S6 summary cites unknown evidence {cite}")
    sim_ids = [e["evidence_id"] for e in c["evidence"] if e.get("simulated")]
    if sorted(sim_ids) != sorted(record.get("simulated_evidence_ids", [])):
        v.append("S7 simulated_evidence_ids does not match the simulated evidence items")
    for e in c["evidence"]:
        if e.get("source") == "customer" and not e.get("simulated"):
            v.append(f"S7 customer/step-up evidence {e.get('evidence_id')} is not marked simulated")
        if e.get("simulated") and not str(e.get("claim", "")).startswith("[SIMULATED]"):
            v.append(f"S7 simulated evidence {e.get('evidence_id')} claim lacks the [SIMULATED] prefix")
    for r in record.get("evidence_requests", []):
        if not r.get("simulated"):
            v.append("S7 evidence request response is not marked simulated")
    v += sar_checks(record)
    # leaks
    bad = _keys(record, set()) & FORBIDDEN_KEYS
    if bad:
        v.append(f"S8 forbidden keys present: {sorted(bad)}")
    others = {s for s in _BENCH.findall(" ".join(_strings(record, []))) if s != cid}
    if others:
        v.append(f"S8 other case ids present: {sorted(others)}")
    return v


def sar_checks(record):
    v = []
    sar = record.get("sar")
    c, nb = record["case"], record["next_best_actions"]
    if sar is None:
        return ["S9 sar missing"]
    for f in ("file", "reason", "narrative", "subjects", "total_amount_usd", "activity_dates"):
        if f not in sar:
            v.append(f"S9 sar.{f} missing")
    if v:
        return v
    filed = any(a["action"] == "FILE_REPORT" for a in nb["final"])
    if bool(sar["file"]) != filed:
        v.append("S9 sar.file must equal (FILE_REPORT in final actions)")
    if not sar["reason"]:
        v.append("S9 sar.reason is empty")
    if not sar["file"]:
        if sar["narrative"] or sar["subjects"] or sar["total_amount_usd"] or sar["activity_dates"]:
            v.append("S9 sar.file=false requires empty narrative/subjects, total 0 and no activity dates")
        return v
    n = len(SAR_sentences(sar["narrative"]))
    if not 6 <= n <= 12:
        v.append(f"S9 sar.narrative has {n} sentences (need 6-12)")
    d = sar["activity_dates"]
    if not (isinstance(d, list) and len(d) == 2 and all(re.match(r"^\d{4}-\d{2}-\d{2}$", str(x)) for x in d) and d[0] <= d[1]):
        v.append("S9 sar.activity_dates must be [first, last] as YYYY-MM-DD")
    if abs(float(sar["total_amount_usd"]) - float(c["exposure_usd"])) > 0.01:
        v.append("S9 sar.total_amount_usd must equal the case exposure")
    if not sar["subjects"]:
        v.append("S9 sar.subjects is empty")
    if re.search(r"probab|likelihood of fraud", sar["narrative"], re.I):
        v.append("S9 sar.narrative states a probability")
    if c["verdict"] == "legitimate":
        v.append("S9 a legitimate case cannot file a report")
    return v


def graph_checks(record, as_of_epoch, io):
    v = []
    c = record["case"]
    refs = record.get("graph_refs") or {}
    card, cust = refs.get("card_id"), refs.get("customer_id")
    if not card or not cust:
        return ["G1 graph_refs.card_id/customer_id missing"]
    aff = [int(i) for i in c["affected_txn_ids"]]
    flagged = refs.get("flagged_txn_id")
    txn_ids = sorted(set(aff) | ({int(flagged)} if flagged else set()))
    dev_ids = list(refs.get("device_ids", []))
    conn = list(refs.get("connected_cards", {}))
    prior = list(c["similar_prior_cases"])
    jobs = [("Card", card), ("Customer", cust)] + [("Card", x) for x in conn] + [("DeviceProfile", x) for x in dev_ids] + [("ClosedCase", x) for x in prior] + \
           [("Transaction", t) for t in txn_ids]
    sar = record.get("sar") or {}
    known = {cust, card} | set(conn) | set(dev_ids)
    for x in sar.get("subjects", []):
        if x not in known:
            jobs.append(("Customer", x) if re.match(r"^C\d{5}$", x) else ("Card", x) if re.match(r"^C\d{5}-K\d$", x) else ("DeviceProfile", x))
    jobs = list(dict.fromkeys(jobs))
    with ThreadPoolExecutor(8) as ex:
        res = dict(zip(jobs, ex.map(lambda j: io.get_vertex(*j), jobs)))
    for (vt, vid), a in res.items():
        if a is None:
            v.append(f"G1 {vt} {vid} does not exist")
    if v:
        return v
    if res[("Card", card)].get("customer_id") != cust:
        v.append(f"G1 card {card} does not belong to customer {cust}")
    if flagged and res[("Transaction", int(flagged))].get("card_id") != card:
        v.append(f"G1 flagged transaction {flagged} is not on card {card}")
    total = 0.0
    for t in aff:
        a = res[("Transaction", t)]
        if a["epoch"] > as_of_epoch:
            v.append(f"G2 transaction {t} epoch {a['epoch']} > as_of {as_of_epoch}")
        total += abs(a["amount"])
    if not v and abs(total - c["exposure_usd"]) > 0.01:
        v.append(f"G3 exposure_usd {c['exposure_usd']} != graph sum {round(total, 2)}")
    for k in prior:
        a = res[("ClosedCase", k)]
        if a["close_epoch"] > as_of_epoch:
            v.append(f"G4 closed case {k} closes at {a['close_epoch']} > as_of {as_of_epoch}")
    # other-card transactions must sit on a cited device
    other = [t for t in aff if res[("Transaction", t)].get("card_id") != card]
    if other:
        if not dev_ids:
            v.append("G2 other-card transactions without a cited evidence device")
        else:
            with ThreadPoolExecutor(8) as ex:
                devs = dict(zip(other, ex.map(io.txn_devices, other)))
            for t, ds in devs.items():
                if not set(ds) & set(dev_ids):
                    v.append(f"G2 other-card transaction {t} is not on a cited evidence device")
    return v


def validate(record, as_of_epoch, io=None):
    v = structural(record)
    if io is not None and not v:
        v += graph_checks(record, as_of_epoch, io)
    return v


def assert_valid(record, as_of_epoch, io=None):
    v = validate(record, as_of_epoch, io)
    if v:
        raise CaseValidationError(v)
