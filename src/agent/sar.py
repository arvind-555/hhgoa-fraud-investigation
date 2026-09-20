"""SAR (Suspicious Activity Report) generation. Facts are computed deterministically from the investigation; the narrative is a template over those facts
(the LLM writer in agent/llm.py may rephrase it but is validated against the same facts with `check_narrative`).

README rules implemented: `file` is true exactly when FILE_REPORT is in the final actions; the narrative (6-12 sentences) covers who, what, when, where, how and why;
`subjects` lists the customers/cards/devices named; `total_amount_usd` is the suspicious activity total (= case exposure); `activity_dates` are the first and last
activity dates YYYY-MM-DD. When `file` is false: narrative "", subjects [], total 0, activity_dates [].
No probability is stated anywhere (calibration failed its validation gates). Simulated customer responses are labelled as simulated.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fraud_tools.tools import ts_of  # noqa: E402

MIN_SENTENCES, MAX_SENTENCES = 6, 12
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\[])")
_ID = re.compile(r"\b(?:C\d{5}-K\d|C\d{5}|D_[0-9a-f]{6,}|\d{7})\b")
_USD = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def sentences(text):
    return [s for s in _SENT.split(text.strip()) if s]


def _date(epoch):
    return ts_of(int(epoch))[:10]


def _money(x):
    return f"${x:,.2f}"


def build_facts(agent):
    """Collect the facts the narrative may use (only values that came from tool results)."""
    d = agent.data
    t = d["ctx"]["transaction"]
    txns = {}
    for r in d["card"]["rows"]:
        txns[int(r["txn_id"])] = {"txn_id": int(r["txn_id"]), "epoch": r["epoch"], "amount": abs(r["amount"]), "card_id": t["card_id"], "channel": r["channel"],
                                  "product_cd": r["product_cd"]}
    txns[t["txn_id"]] = {"txn_id": t["txn_id"], "epoch": t["epoch"], "amount": abs(t["amount"]), "card_id": t["card_id"], "channel": t["channel"], "product_cd": t.get("product_cd")}
    for tid, m in agent.expansion["txns"].items():
        txns[tid] = {"txn_id": tid, "epoch": m["epoch"], "amount": m["amount"], "card_id": m["card_id"], "channel": "online", "product_cd": None}
    aff = [txns[i] for i in agent.affected if i in txns]
    cust_of = {n["card_id"]: n["customer_id"] for dv in d["shared"]["devices"] for n in dv["neighbours"]}
    connected = [{"card_id": c, "customer_id": cust_of.get(c)} for c in agent.connected_cards]
    devs = [{"device_id": dv["device_id"], "profile_str": dv["profile_str"], "customers": dv["customers"]} for dv in d["shared"]["devices"]
            if dv["device_id"] in agent.expansion["device_ids"]]
    ring = None
    for s in d["det"]["signals"]:
        if s["id"] == "S01" and s["fired"] and s["detail"].get("devices"):
            ring = s["detail"]["devices"][0]
    fired = {s["id"]: s for s in d["det"]["signals"] if s["fired"]}
    return {"case_id": agent.trigger.case_id, "customer_id": t["customer_id"], "card_id": t["card_id"], "flagged_txn_id": t["txn_id"], "flagged_epoch": t["epoch"],
            "flagged_channel": t["channel"], "region": t.get("addr1"), "country": t.get("addr2"), "connected": connected, "devices": devs, "txns": aff, "ring": ring,
            "fired": sorted(fired), "s06": fired.get("S06"), "s07": fired.get("S07"), "s02": [fired[k] for k in ("S02a", "S02b") if k in fired],
            "pattern": None, "exposure": agent.exposure, "first_suspicious": agent.first_suspicious, "final_actions": [a.action for a in agent.final],
            "simulated": [e.summary for e in agent.evidence if e.simulated], "customer_response": agent.request.outcome if agent.request else None}


def reason_text(facts, filed, pattern):
    acts = set(facts["final_actions"])
    if filed:
        why = []
        if "S01" in facts["fired"] or pattern == "undocumented":
            why.append("R9: coordinated or repeated abuse across customers that fits no documented pattern")
        if set(facts["fired"]) & {"S01", "S02a", "S02b"}:
            why.append("R6: shared device profile linking several cards")
        if facts["exposure"] > 1000:
            why.append("exposure above $1,000")
        return "FILE_REPORT recommended (policy section 3a): " + "; ".join(why or ["fraud confirmed or strongly suspected with a qualifying condition"]) + "."
    need = "fraud confirmed or strongly suspected AND (exposure above $1,000, a shared device/region cluster or another customer's fraud, or a coordinated/undocumented pattern)"
    return f"No report: policy section 3a requires {need}; this case does not meet it (final actions: {', '.join(sorted(acts)) or 'none'})."


def build_sar(agent, pattern, description):
    facts = build_facts(agent)
    facts["pattern"] = pattern
    filed = "FILE_REPORT" in facts["final_actions"]
    if not filed or not facts["txns"]:
        return {"file": False, "reason": reason_text(facts, False, pattern), "narrative": "", "subjects": [], "total_amount_usd": 0, "activity_dates": []}, facts
    tx = sorted(facts["txns"], key=lambda x: (x["epoch"], x["txn_id"]))
    first, last = tx[0], tx[-1]
    cards = [facts["card_id"]] + [c["card_id"] for c in facts["connected"]]
    custs = sorted({facts["customer_id"]} | {c["customer_id"] for c in facts["connected"] if c["customer_id"]})
    dev_ids = [d["device_id"] for d in facts["devices"]]
    total = round(sum(x["amount"] for x in tx), 2)
    s = []
    s.append(f"Who: the subject is customer {facts['customer_id']}, holder of card {facts['card_id']}"
             + (f", together with {len(facts['connected'])} other card(s) ({', '.join(c['card_id'] for c in facts['connected'][:8])}"
                + (", and others" if len(facts["connected"]) > 8 else "") + ") that used the same device profile." if facts["connected"] else "."))
    s.append(f"What: {len(tx)} transaction(s) totalling {_money(total)} across {len({x['card_id'] for x in tx})} card(s) were identified as one suspected fraud episode, "
             f"beginning with transaction {facts['first_suspicious'] or first['txn_id']}.")
    s.append(f"When: the activity ran from {_date(first['epoch'])} to {_date(last['epoch'])}, and the transaction under review occurred on {_date(facts['flagged_epoch'])}.")
    ch = sorted({x["channel"] for x in tx if x["channel"]})
    reg = "an unrecorded billing region" if facts["region"] is None else f"billing region {facts['region']}"
    s.append(f"Where: the activity was {' and '.join(ch) or 'card-not-present'}, with the reviewed transaction billed in {reg}"
             + (f" and using device profile {facts['devices'][0]['profile_str']} ({facts['devices'][0]['device_id']})." if facts["devices"] else "."))
    if facts["ring"]:
        r = facts["ring"]
        s.append(f"How: one device profile was shared by {r['customers']} customers, {round(r['new_share'] * 100)}% of its transactions carried a new-device marker and "
                 f"{round(r['proxy_share'] * 100)}% came through an anonymising proxy, which is how the linked cards were found.")
    elif facts["s06"]:
        s.append(f"How: {facts['s06']['detail'].get('flagged_txn_small_auths_in_prior_hour', 'several')} small online authorisations on the card within one hour preceded a larger purchase (card-testing sequence).")
    elif facts["s07"]:
        s.append("How: repeated same-card purchases of $400 to $500 under product code C occurred within one hour.")
    elif facts["s02"]:
        s.append("How: the device used for the transaction is shared with another customer whose confirmed fraud case was already closed.")
    else:
        s.append("How: the linkage rests on the validated signals recorded in the case evidence.")
    why = []
    if "S01" in facts["fired"] or pattern == "undocumented":
        why.append("the pattern matches no documented fraud type but shows coordinated use of one device across many customers (policy R9)")
    if set(facts["fired"]) & {"S01", "S02a", "S02b"}:
        why.append("several cards are tied to one device profile (policy R6)")
    if facts["exposure"] > 1000:
        why.append("the total exceeds $1,000")
    s.append("Why suspicious: " + ("; ".join(why) if why else "the validated signals in the case evidence indicate probable fraud") + ".")
    if facts["simulated"]:
        s.append("Customer contact: the recorded response is simulated for this exercise and is not real customer evidence.")
    s.append(f"Recommended next actions (recommendations only, none executed): {', '.join(facts['final_actions'])}.")
    s.append("All identifiers and amounts are taken from the bank's transaction records as they stood when the case was opened.")
    narrative = " ".join(s)
    sar = {"file": True, "reason": reason_text(facts, True, pattern), "narrative": narrative, "subjects": custs + cards + dev_ids, "total_amount_usd": total,
           "activity_dates": [_date(first["epoch"]), _date(last["epoch"])]}
    return sar, facts


def check_narrative(text, facts, subjects):
    """Validate any narrative (template or LLM): 6-12 sentences; every identifier is a known subject/transaction; every dollar amount is a known total/transaction amount;
    no probability or percentage-of-fraud claim; simulated response labelled when present. Returns a list of problems."""
    v = []
    n = len(sentences(text))
    if not MIN_SENTENCES <= n <= MAX_SENTENCES:
        v.append(f"narrative has {n} sentences (need {MIN_SENTENCES}-{MAX_SENTENCES})")
    known_ids = set(subjects) | {str(t["txn_id"]) for t in facts["txns"]} | {str(facts["flagged_txn_id"]), str(facts["first_suspicious"])}
    for i in _ID.findall(text):
        if i not in known_ids:
            v.append(f"unknown identifier in narrative: {i}")
    amounts = {round(t["amount"], 2) for t in facts["txns"]} | {round(facts["exposure"], 2)}
    for m in _USD.findall(text):
        x = round(float(m.replace(",", "")), 2)
        if x not in amounts and x not in (1000.0, 400.0, 500.0):
            v.append(f"amount not in the facts: ${m}")
    if re.search(r"probab|likelihood of fraud|\d\s?% (chance|likely)", text, re.I):
        v.append("narrative states a probability")
    if facts["simulated"] and "simulated" not in text.lower():
        v.append("simulated response not labelled as simulated")
    return v
