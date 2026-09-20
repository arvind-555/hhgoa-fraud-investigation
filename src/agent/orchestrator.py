"""Agent state machine (Phase 9B).

TRIGGER -> INITIAL_INVESTIGATION -> EVIDENCE_SYNTHESIS -> UNCERTAINTY_ASSESSMENT -> [ADDITIONAL_EVIDENCE_REQUEST] -> NEXT_BEST_ACTION
        -> EXPLANATION -> CASE_WRITE -> DONE

Everything that decides (evidence, strength, uncertainty, actions, routes, exposure, rule ids; no probability) is deterministic code over tool results.
The only free-text step is EXPLANATION, behind an interface, validated for citations. Nothing is executed: actions are recommendations with routes.
The agent holds a ToolGateway; it never sees as_of, GSQL, the case pack, labels, other cases or the raw risk score.
"""
import re
import time

from . import actions as A
from . import sar as SAR
from .gateway import ToolGateway
from .investigator import InvestigatorFailure
from .reasoner import PendingResponder, TemplateExplainer, check_citations
from .schema import Evidence, EvidenceRequest, State, TRANSITIONS, Trigger, Uncertainty, to_dict

HIST_HOURS, HIST_ROWS = 720, 300
EXPOSURE_WINDOW_S = 30 * 86400          # episode window for other cards on an evidence device (same 30 days as find_shared_devices)
MAX_EXPAND_CARDS, EXPAND_ROWS = 40, 300
# responder vocabulary -> internal outcome (simulator vocabulary and the legacy test-double vocabulary are both accepted)
NORMALIZE = {"pending": "pending", "no_response": "pending", "no_reply": "no_reply", "confirmed": "confirmed", "verified_legitimate": "confirmed",
             "denied": "denied", "denied_or_unrecognized": "denied", "passed": "passed", "failed": "failed"}


class InvariantError(Exception):
    pass


def _max_epoch(x, k=0):
    if isinstance(x, dict):
        for key, v in x.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and ("epoch" in str(key)):
                k = max(k, int(v))
            else:
                k = _max_epoch(v, k)
    elif isinstance(x, list):
        for v in x:
            k = _max_epoch(v, k)
    return k


class Agent:
    def __init__(self, gateway: ToolGateway, trigger: Trigger, responder=None, explainer=None, store=None, calibrator=None, writer=None, sar_writer=None, budget=None, investigator=None):
        self.gw, self.trigger = gateway, trigger
        self.investigator = investigator                  # optional LLM investigation orchestrator (agent/investigator.py); None = fixed deterministic pipeline
        self.agentic = {"mode": "deterministic", "fallback": None, "forced_calls": [], "request_decision": None}
        self._llm_type = None
        self.sar_writer, self.budget = sar_writer, budget      # optional LLM SAR wording + per-case token budget (agent/llm.py)
        self.writer = writer                        # runner-built CaseWriter (knows as_of); None = no graph write
        self.calibrator = calibrator                # runner-built (knows as_of); the agent only receives its output
        self.responder = responder or PendingResponder()
        self.explainer = explainer or TemplateExplainer()
        self.store = store
        self.state = State.TRIGGER
        self.trace, self.evidence, self.request = [], [], None
        self.t_start = time.perf_counter()
        self.data = {}

    # ---------------------------------------------------------------------------------------------- machinery
    def _go(self, nxt):
        if nxt not in TRANSITIONS.get(self.state, set()):
            raise InvariantError(f"illegal transition {self.state.value} -> {nxt.value}")
        self.state = nxt
        self._t0, self._c0 = time.perf_counter(), len(self.gw.calls)

    def _close_step(self, note=""):
        self.trace.append({"step": len(self.trace) + 1, "state": self.state.value, "ms": round((time.perf_counter() - self._t0) * 1000, 1),
                           "tool_calls": [c["tool"] for c in self.gw.calls[self._c0:]], "note": note})

    # ---------------------------------------------------------------------------------------------- run
    def run(self):
        self._go(State.INITIAL_INVESTIGATION)
        self._investigate()
        self._go(State.EVIDENCE_SYNTHESIS)
        self._synthesize()
        self._go(State.UNCERTAINTY_ASSESSMENT)
        unc = self._assess()
        outcome = "pending"
        if self._decide_request(unc):
            self._go(State.ADDITIONAL_EVIDENCE_REQUEST)
            outcome = self._request_evidence()
        self._go(State.NEXT_BEST_ACTION)
        self._next_best_action(outcome)
        self._go(State.EXPLANATION)
        self._explain()
        self._go(State.CASE_WRITE)
        rec = self._write()
        self._go(State.DONE)
        return rec

    # ---------------------------------------------------------------------------------------------- 1. investigation
    def _investigate(self):
        if self.investigator is not None:
            try:
                self._investigate_agentic()
                return
            except InvestigatorFailure as e:                  # the model path failed: the deterministic pipeline finishes the case
                self.agentic.update(mode="deterministic_fallback", fallback=str(e)[:200])
        self._investigate_fixed()

    _EMPTY = {"cust": {}, "card": {"rows": [], "truncated": False}, "shared": {"devices": [], "skipped_hubs": []}, "conn": {"connected_cards": [], "links": [], "skipped_hubs": []},
              "prior": {"cases": [], "n_related_visible": 0}, "similar": {"hits": []}, "policy": {}}

    def _need(self, tool, args, forced):
        """A result the deterministic synthesis requires: reuse the model's identical call, else make it (recorded as forced)."""
        if not self.gw.has_result(tool, args):
            forced.append(tool)
        return self.gw.call(tool, args, State.INITIAL_INVESTIGATION)

    def _investigate_agentic(self):
        st, t = State.INITIAL_INVESTIGATION, self.trigger
        n0 = len(self.gw.calls)
        log = self.investigator.investigate(self.gw, t.txn_id, t.trigger_type, st)          # the MODEL chooses tools, order and when to stop
        forced = []
        ctx = self._need("get_transaction_context", {"txn_id": t.txn_id}, forced)
        card, cust = ctx["transaction"]["card_id"], ctx["transaction"]["customer_id"]
        det = self._need("detect_fraud_patterns", {"txn_id": t.txn_id}, forced)
        canon = {"card": ("get_card_history", {"card_id": card, "hours": HIST_HOURS, "max_rows": HIST_ROWS}), "shared": ("find_shared_devices", {"card_id": card}),
                 "prior": ("find_prior_cases", {"card_id": card}), "cust": ("get_customer_history", {"customer_id": cust}),
                 "conn": ("find_connected_entities", {"card_id": card}), "similar": ("find_similar_cases", {"txn_id": t.txn_id})}
        must = ["card", "shared", "prior"] if det["fired"] else []                         # evidence validity: exposure/connected cards need these when a signal fired
        data = {"ctx": ctx, "det": det}
        for k, (tool, args) in canon.items():
            if k in must or self.gw.has_result(tool, args):
                data[k] = self._need(tool, args, forced)
            else:
                data[k] = self._EMPTY[k]
        data["policy"] = self._EMPTY["policy"]
        self.data = data
        self.agentic.update(mode="llm_orchestrated", forced_calls=forced, llm_log=log, first_call_index=n0)
        self._expand_exposure()
        self._close_step("llm-orchestrated; forced=" + ",".join(forced))

    def _investigate_fixed(self):
        st = State.INITIAL_INVESTIGATION
        t = self.trigger
        ctx = self.gw.call("get_transaction_context", {"txn_id": t.txn_id}, st)
        card, cust = ctx["transaction"]["card_id"], ctx["transaction"]["customer_id"]
        res = self.gw.call_many([("get_customer_history", {"customer_id": cust}), ("get_card_history", {"card_id": card, "hours": HIST_HOURS, "max_rows": HIST_ROWS}),
                                 ("find_shared_devices", {"card_id": card}), ("find_connected_entities", {"card_id": card}), ("find_prior_cases", {"card_id": card}),
                                 ("detect_fraud_patterns", {"txn_id": t.txn_id}), ("find_similar_cases", {"txn_id": t.txn_id})], st)
        cust_h, card_h, shared, conn, prior, det, similar = res
        if similar.get("error") == "ModuleNotFoundError":          # GraphRAG package not installed: case memory by text similarity is optional, structured prior cases still apply
            similar = self._EMPTY["similar"]
        errs = [r for r in (cust_h, card_h, shared, conn, prior, det, similar) if "error" in r]
        if errs:
            raise InvariantError(f"tool error during investigation: {errs[0]}")
        pol = self.gw.call("get_policy_context", {"signals": det["fired"]} if det["fired"] else {}, st)
        self.data = dict(ctx=ctx, cust=cust_h, card=card_h, shared=shared, conn=conn, prior=prior, det=det, policy=pol, similar=similar)
        self._expand_exposure()
        self._close_step()

    def _expand_exposure(self):
        """Transactions on OTHER cards that used a device carrying validated shared-origin evidence (S01 ring, S02a/b), within the 30-day episode window,
        excluding transactions already belonging to visible closed cases. Only existing tools are used, so every row is already as_of-safe and hub-guarded
        (skipped/blocked devices never appear because they carry no evidence)."""
        d = self.data
        txn = d["ctx"]["transaction"]
        own = txn["card_id"]
        devs = sorted({e for s in d["det"]["signals"] if s["fired"] and s["id"] in ("S01", "S02a", "S02b") for e in s["entity_ids"]})
        # Completeness = "no RELEVANT card is known to be missing". Relevant = active on an evidence device inside the discovery window
        # [as_of - 30 d, as_of] (that is what the tools return as neighbours). Customers seen on the device only BEFORE that window have no exposure in it:
        # they are reported as `historical_cards_outside_window` (information) and never make the expansion incomplete.
        self.expansion = {"device_ids": devs, "window_days": 30, "cards_expanded": 0, "cards_not_expanded": 0, "historical_cards_outside_window": 0,
                          "incomplete_reasons": [], "txns": {}, "complete": True, "rule": "none",
                          "discovery_window_gap_s": max(0, d["ctx"]["temporal"]["seconds_since_transaction"])}
        if not devs:
            return
        ex = self.expansion
        ex["rule"] = "other-card transactions on evidence devices within 30 days, excluding visible closed-case transactions"
        closed = {int(t) for c in d["prior"]["cases"] for t in c["transaction_ids"]}
        cards, tool_cap_missing, historical = {}, 0, 0
        for dv in d["shared"]["devices"]:
            if dv["device_id"] in devs and not dv["blocked"]:
                for n in dv["neighbours"]:
                    if n["card_id"] != own:
                        cards.setdefault(n["card_id"], set()).add(dv["device_id"])
                in_window = dv.get("neighbours_total", len(dv["neighbours"]))          # cards active in the discovery window (before the tool's list cap)
                tool_cap_missing += max(0, in_window - len(dv["neighbours"]))          # RELEVANT cards the tool did not list
                historical += max(0, (dv["customers"] or 0) - 1 - in_window)           # seen on the device only before the window: irrelevant to this window
        order = sorted(cards)[:MAX_EXPAND_CARDS]
        over_cap = max(0, len(cards) - len(order))                                       # RELEVANT cards beyond our own expansion cap
        ex["historical_cards_outside_window"] = historical
        ex["cards_not_expanded"] = tool_cap_missing + over_cap
        if tool_cap_missing:
            ex["incomplete_reasons"].append(f"neighbour list capped by the tool: {tool_cap_missing} relevant card(s) not listed")
        if over_cap:
            ex["incomplete_reasons"].append(f"expansion cap of {MAX_EXPAND_CARDS} cards: {over_cap} relevant card(s) not expanded")
        lo = txn["epoch"] - EXPOSURE_WINDOW_S
        hours = min(4800, (d["ctx"]["temporal"]["seconds_since_transaction"] + EXPOSURE_WINDOW_S) // 3600 + 1)
        res = self.gw.call_many([("get_card_history", {"card_id": c, "hours": int(hours), "max_rows": EXPAND_ROWS}) for c in order], State.INITIAL_INVESTIGATION)
        errors = truncated = 0
        for c, r in zip(order, res):
            if "error" in r:
                errors += 1
                continue
            if r.get("truncated"):
                truncated += 1
            ex["cards_expanded"] += 1
            for row in r["rows"]:
                tid = int(row["txn_id"])
                if row.get("device_id") in cards[c] and row["epoch"] >= lo and tid not in closed:
                    ex["txns"][tid] = {"card_id": c, "epoch": row["epoch"], "amount": abs(row["amount"])}
        if errors:
            ex["cards_not_expanded"] += errors
            ex["incomplete_reasons"].append(f"card history failed for {errors} relevant card(s)")
        if truncated:
            ex["incomplete_reasons"].append(f"card history rows truncated for {truncated} card(s)")
        ex["complete"] = not ex["incomplete_reasons"]

    # ---------------------------------------------------------------------------------------------- 2. synthesis
    def _add(self, **kw):
        e = Evidence(id=f"E{len(self.evidence) + 1}", **kw)
        self.evidence.append(e)
        return e

    def _synthesize(self):
        d, t = self.data, self.trigger
        txn = d["ctx"]["transaction"]
        self.txn_amounts = {int(r["txn_id"]): abs(r["amount"]) for r in d["card"]["rows"]}
        self.txn_amounts[txn["txn_id"]] = abs(txn["amount"])
        self.txn_epoch = {int(r["txn_id"]): r["epoch"] for r in d["card"]["rows"]}
        self.txn_epoch[txn["txn_id"]] = txn["epoch"]
        for tid, m in self.expansion["txns"].items():
            self.txn_amounts[tid], self.txn_epoch[tid] = m["amount"], m["epoch"]
        self.by_signal, affected, devices_used = {}, {txn["txn_id"]}, set()
        for s in d["det"]["signals"]:
            if not s["fired"]:
                continue
            refs = {}
            if s["id"] in ("S01", "S02a", "S02b"):
                refs["device_ids"] = s["entity_ids"]
                devices_used |= set(s["entity_ids"])
            elif s["id"] in ("S06", "S07"):
                refs["txn_ids"] = [str(i) for i in s["entity_ids"]]
                affected |= {int(i) for i in s["entity_ids"]}
            elif s["id"] == "S05":
                refs["case_ids"] = s["entity_ids"]
            e = self._add(source_tool="detect_fraud_patterns", kind="signal", summary=f"{s['id']} {s['name']}", rating=s["rating"], signal_id=s["id"], tier=str(s["tier"]),
                          independent_source=s["source"], refs=refs, max_epoch=txn["epoch"])
            self.by_signal.setdefault(s["id"], []).append(e.id)
        # own-card transactions that came from a device used as shared-origin evidence belong to the same episode
        for r in d["card"]["rows"]:
            if r.get("device_id") in devices_used:
                affected.add(int(r["txn_id"]))
        own_n = len(affected)
        affected |= set(self.expansion["txns"])
        self.affected = sorted(affected, key=lambda i: (self.txn_epoch.get(i, 10 ** 12), i))
        ex_cards = sorted({m["card_id"] for m in self.expansion["txns"].values()})
        self.exposure_scope = {"own_card_txns": own_n, "other_card_txns": len(self.expansion["txns"]), "other_cards_with_txns": ex_cards,
                               "cards_expanded": self.expansion["cards_expanded"], "cards_not_expanded": self.expansion["cards_not_expanded"],
                               "historical_cards_outside_window": self.expansion["historical_cards_outside_window"],
                               "incomplete_reasons": self.expansion["incomplete_reasons"], "discovery_window_gap_s": self.expansion["discovery_window_gap_s"],
                               "window_days": self.expansion["window_days"], "device_ids": self.expansion["device_ids"], "rule": self.expansion["rule"],
                               "complete": self.expansion["complete"],
                               "other_card_exposure_usd": round(sum(m["amount"] for m in self.expansion["txns"].values()), 2)}
        if self.expansion["txns"]:
            self._add(source_tool="get_card_history", kind="link", rating="CONTEXT", direction="context",
                      summary=f"{len(self.expansion['txns'])} transaction(s) on {len(ex_cards)} other card(s) used the evidence device(s) in the 30-day episode window"
                              + ("" if self.expansion["complete"] else " (expansion incomplete: " + "; ".join(self.expansion["incomplete_reasons"]) + ")"),
                      refs={"txn_ids": [str(i) for i in sorted(self.expansion["txns"])][:50], "card_ids": ex_cards[:50]}, max_epoch=max(m["epoch"] for m in self.expansion["txns"].values()))
        self.exposure = round(sum(self.txn_amounts.get(i, 0.0) for i in self.affected), 2)
        self.first_suspicious = str(self.affected[0]) if self.affected else ""
        # connected cards: the OTHER cards on a device that carries shared-origin evidence (S01 ring / S02 with another customer's visible fraud).
        # Merely sharing an eligible device is context (validated: no lift without fraud evidence), so it is recorded as evidence but not as a connected card.
        conn_cards, via = set(), {}
        for dv in d["shared"]["devices"]:
            if dv["device_id"] in devices_used and not dv["blocked"]:
                conn_cards |= {n["card_id"] for n in dv["neighbours"]}
                for n in dv["neighbours"]:
                    via.setdefault(n["card_id"], set()).add("device:" + dv["device_id"])
        self.connected_via = {k: ",".join(sorted(v)) for k, v in via.items() if k != txn["card_id"]}
        for c in d["conn"]["connected_cards"]:
            if any(l.split(":")[0] == "device" and l.split(":")[-1] in ("ring_suspect", "strong", "moderate") for l in c["links"]):
                self._add(source_tool="find_connected_entities", kind="link", summary=f"card {c['card_id']} shares an eligible device with this card (context)", rating="CONTEXT",
                          direction="context", refs={"card_ids": [c["card_id"]]}, max_epoch=_max_epoch(c))
        conn_cards.discard(txn["card_id"])
        self.connected_cards = sorted(conn_cards)
        self.profiles = sorted({dv["profile_str"] for dv in d["shared"]["devices"] if not dv["blocked"] and dv["device_id"] in devices_used and dv["neighbours"] and dv["profile_str"]})
        if d["ctx"].get("model_alert", {}).get("band"):
            self._add(source_tool="get_transaction_context", kind="context", summary=f"bank model alert band: {d['ctx']['model_alert']['band']} (input, not a verdict)",
                      rating="CONTEXT", direction="context", max_epoch=txn["epoch"])
        cleared = [c for c in d["prior"]["cases"] if c["outcome"] == "cleared"]
        if cleared:
            self._add(source_tool="find_prior_cases", kind="prior_case", summary=f"{len(cleared)} visible cleared case(s) related to this card/customer (history, not proof)",
                      rating="CONTEXT", direction="context", refs={"case_ids": [c["case_id"] for c in cleared][:10]}, max_epoch=_max_epoch(cleared))
        links = {}
        for h in d["similar"]["hits"]:                       # GraphRAG hits (linked seeds first, then text precedents), all visible by close time
            links[h["case_id"]] = {"case_id": h["case_id"], "match_reasons": h["match_reasons"], "method": "prior_cases_tool" if h["label"] == "linked" else "graphrag_bm25",
                                   "score": float(h["score"]) if h["label"] != "linked" else 1.0}
        for c in d["prior"]["cases"]:                        # structured seeds always count as memory
            if c["match_reasons"] and c["case_id"] not in links:
                links[c["case_id"]] = {"case_id": c["case_id"], "match_reasons": sorted(c["match_reasons"]), "method": "prior_cases_tool", "score": 1.0}
        self.similar_links = list(links.values())[:10]
        self.similar = [x["case_id"] for x in self.similar_links]
        hits = d["similar"]["hits"]
        if hits:
            nl = sum(1 for h in hits if h["label"] == "linked")
            fr = sum(1 for h in hits if h["outcome"] == "confirmed_fraud")
            self._add(source_tool="find_similar_cases", kind="prior_case", rating="CONTEXT", direction="context",
                      summary=f"{len(hits)} similar closed case(s) retrieved by GraphRAG ({nl} linked to this card/customer/device, {len(hits) - nl} textual precedents; "
                              f"{fr} were confirmed fraud): memory and wording context, not proof",
                      refs={"case_ids": [h["case_id"] for h in hits]}, max_epoch=max(h["close_epoch"] for h in hits))
        self._close_step()

    # ---------------------------------------------------------------------------------------------- 3. uncertainty
    def _assess(self):
        d, t = self.data, self.trigger
        ind = d["det"]["independence"]
        n, low = ind["n_tier_1_to_5_sources"], ind["low_context_sources"]
        fired = set(d["det"]["fired"])
        band = (d["ctx"].get("model_alert") or {}).get("band") or t.model_alert_band
        strength = "strong" if ("S01" in fired or "S02a" in fired or n >= 2) else "moderate" if n == 1 else "weak" if low else "none"
        if strength == "strong":
            level = "low" if n >= 2 else "medium"
        elif strength == "moderate":
            level = "high"
        elif strength == "weak":
            level = "medium"
        else:
            level = "medium" if (band == "high" or t.trigger_type != "risk_score") else "low"
        conflicts = []
        if band == "high" and strength in ("none", "weak"):
            conflicts.append("an alert was raised without validated (tier 1-5) evidence")
        missing = ["customer response not available yet", "no merchant field: policy R7 (recurring merchant) cannot be evaluated"]
        if d["card"]["truncated"]:
            missing.append("card history truncated at the row limit")
        cal = self.calibrator.calibrate(strength) if self.calibrator else {"probability": None, "calibrated": False, "source": "unavailable", "method": "none",
                                                                          "gates_failed": ["NOCALIBRATOR"], "gate_notes": ["no calibrator supplied"]}
        self.cal = cal
        self.unc = Uncertainty(evidence_strength=strength, independent_sources=n + (1 if low else 0), conflicts=conflicts, missing=missing, level=level,
                               needs_more_evidence=(level != "low"),
                               calibrated_probability={"value": cal["probability"], "calibrated": cal["calibrated"], "source": cal["source"], "method": cal["method"],
                                                       "status": "calibrated" if cal["calibrated"] else "unavailable_validation_gate_failed",
                                                       "gates_failed": cal["gates_failed"], "gate_notes": cal["gate_notes"]},
                               reasons=[f"tier1-5 independent sources: {n}", f"low-context sources: {len(low)}", f"model alert band: {band or 'n/a'}"])
        self.model_band = band
        self._close_step()
        return self.unc

    # ---------------------------------------------------------------------------------------------- 4. evidence request
    def _assessment_view(self, unc):
        d = self.data
        return {"evidence_strength": unc.evidence_strength, "independent_sources": unc.independent_sources, "investigation_uncertainty": unc.level, "conflicts": unc.conflicts,
                "missing": unc.missing, "signals_fired": d["det"]["fired"], "channel": d["ctx"]["transaction"]["channel"],
                "policy_requires_verification": unc.needs_more_evidence, "trigger": "customer report" if self.trigger.trigger_type in ("customer_complaint", "customer_report") else "alert"}

    def _decide_request(self, unc):
        """Policy decides what is REQUIRED; the model may ask for more but can never skip a required verification. The type must be an allowed one."""
        policy = unc.needs_more_evidence
        dec = {"policy_required": policy, "llm": None, "final": policy, "source": "policy" if policy else "none"}
        if self.investigator is not None and self.agentic["mode"] == "llm_orchestrated":
            online = self.data["ctx"]["transaction"]["channel"] == "online"
            allowed = ["customer_validation"] + (["step_up_auth"] if online else [])
            try:
                d = self.investigator.decide_request(self._assessment_view(unc), allowed)
                dec["llm"] = d
                dec["final"] = policy or d["request"]
                dec["source"] = ("llm+policy" if policy and d["request"] else "policy_overrode_llm" if policy else "llm" if d["request"] else "none")
                self._llm_type = d["type"] if d["request"] else None
            except InvestigatorFailure as e:
                self.agentic["fallback"] = ((self.agentic["fallback"] or "") + " | request decision: " + str(e)[:120]).strip(" |")
        self.agentic["request_decision"] = dec
        return dec["final"]

    def _request_evidence(self):
        t = self.trigger
        online = self.data["ctx"]["transaction"]["channel"] == "online"
        typ = "customer_validation" if t.trigger_type in ("customer_complaint", "customer_report") or not online else "step_up_auth"
        if self._llm_type in ("customer_validation", "step_up_auth") and not (t.trigger_type in ("customer_complaint", "customer_report")) and (online or self._llm_type == "customer_validation"):
            typ = self._llm_type                              # the model's choice, only among the allowed types
        self.request = EvidenceRequest(type=typ, asked_after_step=len(self.gw.calls), reason=f"uncertainty {self.unc.level}; evidence strength {self.unc.evidence_strength}",
                                       request_id=f"{t.case_id}:REQ1")
        raw, text = self.responder.respond(self.request, {"case_id": t.case_id, "request_type": typ})   # request and case id only: no labels, no graph, no other cases
        if raw not in NORMALIZE:
            raise InvariantError(f"unknown responder outcome {raw!r}")
        outcome = NORMALIZE[raw]
        self.request.outcome, self.request.assumed_response = raw, text
        # simulated evidence is APPENDED as its own Evidence objects; graph evidence is never modified (Evidence is frozen)
        if outcome in ("confirmed", "denied", "passed", "failed"):
            kind, label = ("customer", "customer validation") if typ == "customer_validation" else ("step_up", "step-up authentication")
            neg = outcome in ("denied", "failed")
            e = self._add(source_tool="evidence_simulator", kind=kind, summary=f"[SIMULATED] {label}: {raw}", rating="HIGH" if kind == "customer" else "MEDIUM",
                          direction="supports_fraud" if neg else "contradicts", simulated=True, max_epoch=0)
            self.by_signal.setdefault("CUSTOMER" if kind == "customer" else "STEPUP", []).append(e.id)
        self._close_step(f"outcome={raw} (simulated)")
        return outcome

    # ---------------------------------------------------------------------------------------------- 5. next best action
    def _ctx(self):
        return {"exposure": self.exposure, "fired": self.data["det"]["fired"], "channel": self.data["ctx"]["transaction"]["channel"], "trigger_type": self.trigger.trigger_type,
                "model_band": self.model_band, "connected_card_ids": self.connected_cards, "evidence_ids_by_signal": self.by_signal}

    def _next_best_action(self, outcome):
        ctx = self._ctx()
        self.initial = A.initial_actions(self.unc, ctx)
        self.final, o = A.final_actions(self.initial, self.unc, ctx, outcome)
        self._policy_documents()
        strong = self.unc.evidence_strength == "strong"
        # no probability is used anywhere: a calibrated value exists only if the calibration gates passed and no simulated response has replaced the evidence base
        self.p = self.cal["probability"] if (self.cal["calibrated"] and o in ("pending", "no_reply")) else None
        self.p_calibrated = self.p is not None
        self.p_source = "calibration_table" if self.p_calibrated else "unavailable"
        acts = {a.action for a in self.final}
        if o == "denied":
            self.verdict, self.status = "fraud", "closed_fraud"
        elif o == "confirmed" and "CLOSE_NO_FRAUD" in acts:
            self.verdict, self.status = "legitimate", "closed_legitimate"
        elif o == "passed" and "ALLOW_TRANSACTION" in acts:
            self.verdict, self.status = "legitimate", "closed_legitimate"
        elif "ALLOW_TRANSACTION" in acts and len(acts) == 1:
            self.verdict, self.status = "legitimate", "closed_legitimate"
        else:
            self.verdict = "fraud" if strong else "uncertain"
            self.status = "escalated" if "ESCALATE_TO_ANALYST" in acts else "open"
        self.outcome = o
        self.stop_reason = {"denied": "simulated customer denial settles the question (R2)", "confirmed": "simulated customer confirmation settles the question (R3)",
                            "passed": ("simulated step-up passed but strong shared-origin evidence remains: escalate (R8)" if strong else
                                       "simulated step-up passed; no shared-origin evidence contradicts it"),
                            "failed": "simulated step-up failed; decline and verify with the customer",
                            "no_reply": "no customer reply (R4); further steps unlikely to change the recommendation"}.get(
            o, "requested evidence is pending (no simulated response); recommendations stand until it arrives" if self.request else
            "no validated evidence and no request warranted; further steps are unlikely to change the decision")
        fin = (self.agentic.get("llm_log") or {}).get("finish")
        if fin and fin.get("rationale"):
            self.stop_reason += f" Investigator: {'sufficient' if fin.get('sufficient') else 'stopped'} - {fin['rationale'][:200]}"
        self._close_step(f"initial={[a.action for a in self.initial]} final={[a.action for a in self.final]}")

    def _policy_documents(self):
        """Fetch the policy text for every rule the actions cite (GraphRAG `retrieve_policy`, exact references) and record it as document evidence."""
        refs = sorted({r for a in self.initial + self.final for r in a.rules if re.match(r"^(R([1-9]|10)|3a|3b)$", r)})
        if not refs:
            return
        try:
            res = self.gw.call("retrieve_policy", {"rule_ids": refs}, State.NEXT_BEST_ACTION)
        except ModuleNotFoundError:                                  # GraphRAG package not installed: policy text is context only, the rule ids in the actions are unaffected
            return
        for c in res["chunks"]:
            first = c["text"].split(". ", 1)[-1][:160].rstrip(".")
            self._add(source_tool="retrieve_policy", kind="document", rating="CONTEXT", direction="context", signal_id=c["source_ref"],
                      summary=f"policy text consulted ({c['source_ref']}): {first}", max_epoch=0)

    # ---------------------------------------------------------------------------------------------- 6. explanation
    def _explain(self):
        inp = {"evidence": [to_dict(e) for e in self.evidence], "uncertainty": to_dict(self.unc), "request": to_dict(self.request) if self.request else None,
               "final_actions": [to_dict(a) for a in self.final]}
        text = self.explainer.explain(inp)
        if not check_citations(text, {e.id for e in self.evidence}):
            raise InvariantError("explanation cites unknown evidence ids")
        self.summary = text
        self._close_step()

    # ---------------------------------------------------------------------------------------------- 7. case write
    def _write(self):
        legit = self.verdict == "legitimate"
        affected = [] if legit else [str(i) for i in self.affected]
        exposure = 0 if legit else self.exposure
        fired = set(self.data["det"]["fired"])
        pattern, desc = "none", ""
        if "S06" in fired:
            pattern = "card_testing"
        elif "S01" in fired or "S07" in fired:
            pattern = "undocumented"
            desc = ("Device shared by several customers whose transactions are almost all on a new device profile behind an anonymising proxy; found by device-graph traversal (S01)."
                    if "S01" in fired else "Repeated same-card ProductCD C purchases of $400-500 within one hour (S07 burst family).")
        elif fired & {"S02a", "S02b"}:
            pattern = "card_not_present_new_device"
        elif "S08" in fired:
            pattern = "card_not_present_fraud"
        sar, sar_facts = SAR.build_sar(self, pattern, desc)
        problems = SAR.check_narrative(sar["narrative"], sar_facts, sar["subjects"]) if sar["file"] else []
        if problems:
            raise InvariantError("SAR narrative failed its own fact check: " + "; ".join(problems[:3]))
        if self.sar_writer is not None and sar["file"]:
            sar = self.sar_writer.rewrite(sar, sar_facts)         # validated LLM wording, or the template SAR unchanged
        self.sar, self.sar_facts = sar, sar_facts
        record = {
            "case_id": self.trigger.case_id,
            "case": {"status": self.status, "verdict": self.verdict, "fraud_probability": None if self.p is None else round(self.p, 4), "probability_calibrated": self.p_calibrated, "probability_source": self.p_source,
                     "calibration": {"method": self.cal["method"], "gates_failed": self.cal["gates_failed"]}, "pattern": pattern,
                     "pattern_description": desc, "affected_txn_ids": affected, "first_suspicious_txn_id": "" if legit else self.first_suspicious,
                     "connected_card_ids": [] if legit else self.connected_cards, "connected_device_profiles": [] if legit else self.profiles, "exposure_usd": exposure,
                     "evidence": [{"claim": e.summary, "source": "customer" if e.kind in ("customer", "step_up") else "document" if e.kind == "document" else "graph",
                                   "ref": ("document:" + e.signal_id) if e.kind == "document" else f"{e.source_tool}",
                                   "simulated": e.simulated,
                                   "entity_ids": [x for v in e.refs.values() for x in v], "evidence_id": e.id, "rating": e.rating} for e in self.evidence],
                     "similar_prior_cases": self.similar, "summary": self.summary, "written_to_graph": False, "graph_case_id": ""},
            "evidence_requests": [] if not self.request else [{"type": self.request.type, "asked_after_step": self.request.asked_after_step,
                                                               "assumed_response": self.request.assumed_response or f"[SIMULATED] {self.request.outcome}",
                                                               "simulated": True, "request_id": self.request.request_id, "outcome": self.request.outcome}],
            "next_best_actions": {"initial": [self._na(a) for a in self.initial], "final": [self._na(a) for a in self.final],
                                  "what_changed": "nothing" if [a.action for a in self.initial] == [a.action for a in self.final] else
                                  f"the {self.request.outcome if self.request else 'evidence'} response changed the recommendation"},
            "sar": sar,
            "graph_refs": self._graph_refs(legit),
            "exposure_scope": self.exposure_scope, "simulated_evidence_ids": [e.id for e in self.evidence if e.simulated],
            "uncertainty": to_dict(self.unc), "stop_reason": self.stop_reason,
            "recommendation_only": True,
        }
        self._validate(record)
        if self.writer is not None:
            receipt = self.writer.write(record)               # validates against the graph, writes FI_Case + edges, reads back; raises on any violation
            record["case"]["written_to_graph"] = True
            record["case"]["graph_case_id"] = receipt["graph_case_id"]
            record["graph_write"] = receipt
        self._close_step()
        stats = self.gw.stats()
        record["tool_calls"] = stats["tool_calls"]
        record["agentic"] = self._agentic_record()
        record["tokens"] = self.budget.used if self.budget else 0
        record["llm"] = {"explanation": getattr(self.explainer, "status", {"used": False, "reason": "template explainer"}),
                         "sar": getattr(self.sar_writer, "status", {"used": False, "reason": "template narrative"}),
                         "tokens": record["tokens"], "calls": self.budget.calls if self.budget else 0}
        record["latency_s"] = round(time.perf_counter() - self.t_start, 2)
        record["trace"] = self.trace
        record["gateway"] = stats
        if self.store is not None:
            self.store.write(record)
        return record

    def _graph_refs(self, legit):
        t = self.data["ctx"]["transaction"]
        cc = {} if legit else {k: v for k, v in self.connected_via.items() if k in self.connected_cards}
        return {"card_id": t["card_id"], "customer_id": t["customer_id"], "flagged_txn_id": str(t["txn_id"]),
                "device_ids": [] if legit else list(self.expansion["device_ids"]), "connected_cards": cc,
                "similar_cases": self.similar_links}

    def _agentic_record(self):
        """Measured behaviour of the investigation (for evaluation only; never sent to the model)."""
        a = dict(self.agentic)
        log = a.pop("llm_log", None)
        d = self.data
        fired = bool(d["det"]["fired"])
        required = {"get_transaction_context", "detect_fraud_patterns"} | ({"get_card_history", "find_shared_devices", "find_prior_cases"} if fired else set())
        used_by_synthesis = set(required)
        if d["similar"]["hits"]:
            used_by_synthesis.add("find_similar_cases")
        if any(e.source_tool == "find_connected_entities" for e in self.evidence):
            used_by_synthesis.add("find_connected_entities")
        out = {"mode": a["mode"], "fallback": a["fallback"], "fallback_used": bool(a["fallback"]),
               "forced_calls": a["forced_calls"], "request_decision": a["request_decision"]}
        if log is not None:
            steps = log["steps"]
            valid = [s for s in steps if s["valid"]]
            order = [{"tool": s["tool"], "source": "llm", "duplicate": s["duplicate"]} for s in valid] + [{"tool": t, "source": "forced"} for t in a["forced_calls"]]
            called = {s["tool"] for s in valid}
            non_contrib = [s["tool"] for s in valid if s["tool"] not in used_by_synthesis and not s["duplicate"]]
            out.update({"llm_steps": len({s["step"] for s in steps}), "tool_order": order, "llm_tool_calls": len(valid), "invalid_calls": log["invalid_calls"],
                        "duplicate_calls": sum(1 for s in valid if s["duplicate"]), "non_contributing_calls": non_contrib, "leak_events": log["leak_events"],
                        "unnecessary_calls": log["invalid_calls"] + sum(1 for s in valid if s["duplicate"]) + len(non_contrib),
                        "coverage_required_by_llm": round(len(required & called) / len(required), 3), "coverage_required_final": 1.0,
                        "evidence_classes_touched": len({c["tool"] for c in self.gw.calls if c["ok"]} & {"get_transaction_context", "get_customer_history", "get_card_history", "find_shared_devices",
                                                                                                    "find_connected_entities", "find_prior_cases", "detect_fraud_patterns", "find_similar_cases"}),
                        "finish": log["finish"], "llm_ms": round(log["llm_ms"], 1), "phase_tokens": log["phase_tokens"]})
        return out

    @staticmethod
    def _na(a):
        return {"action": a.action, "route": a.route, "reason": a.reason, "rules": a.rules, "executed": False, "requires_approval": a.requires_approval}

    def _validate(self, r):
        c = r["case"]
        if c["verdict"] == "legitimate" and (c["affected_txn_ids"] or c["exposure_usd"]):
            raise InvariantError("legitimate case must have no affected transactions/exposure")
        if c["pattern"] == "undocumented" and not c["pattern_description"]:
            raise InvariantError("undocumented pattern needs a description")
        if c["pattern"] != "undocumented" and c["pattern_description"]:
            raise InvariantError("pattern_description only for undocumented")
        if c["affected_txn_ids"] and abs(sum(self.txn_amounts.get(int(i), 0) for i in c["affected_txn_ids"]) - c["exposure_usd"]) > 0.01:
            raise InvariantError("exposure != sum of affected amounts")
        seen = set(self.txn_amounts)
        if any(int(i) not in seen for i in c["affected_txn_ids"]):
            raise InvariantError("affected transaction not observed in tool results")
        for a in r["next_best_actions"]["initial"] + r["next_best_actions"]["final"]:
            if a["action"] == "BLOCK_ALL_CARDS":
                raise InvariantError("BLOCK_ALL_CARDS is out of scope for Phase 9B (R10)")
            if a["executed"]:
                raise InvariantError("nothing may be executed in Phase 9B")
        if any(a["action"] in ("BLOCK_CARD", "BLOCK_ALL_CARDS") for a in r["next_best_actions"]["initial"]) and not self.request:
            raise InvariantError("BLOCK must not precede verification (R1)")
