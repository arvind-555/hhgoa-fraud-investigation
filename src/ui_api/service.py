"""Read-only presentation service for the Fraud Investigation Command Center.

Nothing here investigates, scores, chooses as_of, or writes. It loads what the deterministic backend already produced:
  cases/HHG-###.json          the submitted answer file (authoritative case, actions, SAR)
  demo/records/HHG-###.json   the full agent record of the same run (trace, evidence, uncertainty, exposure scope, graph refs)
  case_pack.csv               the trigger row the analyst receives (id, opened_at, text)
and shapes them for display. Case / actions / SAR always come from the answer file; if the record disagrees the case is flagged (`integrity`).
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from agent.answer_file import strip_citations  # noqa: E402  (the exporter's own citation stripping, reused so the comparison is exact)
CASE_ID = re.compile(r"^HHG-\d{3}$")

TOOL_LABEL = {
    "get_transaction_context": "Transaction context", "get_customer_history": "Customer history", "get_card_history": "Card history",
    "find_shared_devices": "Shared-device analysis", "find_connected_entities": "Connected entities", "find_prior_cases": "Prior cases",
    "find_similar_cases": "Similar cases (GraphRAG)", "detect_fraud_patterns": "Pattern detection", "get_policy_context": "Policy evaluation",
    "request_customer_validation": "Customer verification", "request_step_up_auth": "Step-up authentication",
}
STATE_LABEL = {
    "INITIAL_INVESTIGATION": "Evidence gathering", "EVIDENCE_SYNTHESIS": "Evidence synthesis", "UNCERTAINTY_ASSESSMENT": "Uncertainty assessment",
    "ADDITIONAL_EVIDENCE_REQUEST": "Additional evidence", "NEXT_BEST_ACTION": "Next best action", "EXPLANATION": "Case explanation", "CASE_WRITE": "Case written to TigerGraph",
}
REQUEST_LABEL = {"customer_validation": "Customer verification", "step_up_auth": "Step-up authentication", "analyst_info": "Analyst information"}
RATING_LABEL = {"HIGH": "Strong", "MEDIUM": "Moderate", "LOW": "Weak", "CONTEXT": "Context"}
RATING_WHY = {
    "HIGH": "Validated signal. Counts as an independent source toward evidence strength.",
    "MEDIUM": "Validated signal with moderate weight. Counts toward evidence strength.",
    "LOW": "Weak signal. Context only; cannot support a block on its own (policy R1).",
    "CONTEXT": "Context for the analyst. Not counted toward evidence strength.",
}
ROUTE_LABEL = {"auto": "Automatic (no approval)", "L1": "Level 1 approval (team lead)", "L2": "Level 2 approval (fraud manager)"}
CATEGORY_ORDER = ["Transaction", "Pattern signals", "Device", "Card history", "Customer history", "Connected entities", "Prior cases", "Similar cases", "Additional evidence", "Policy"]
TOOL_CATEGORY = {"get_transaction_context": "Transaction", "detect_fraud_patterns": "Pattern signals", "find_shared_devices": "Device", "get_card_history": "Card history",
                  "get_customer_history": "Customer history", "find_connected_entities": "Connected entities", "find_prior_cases": "Prior cases", "find_similar_cases": "Similar cases"}
# which UI panel each investigation step makes visible during replay
REVEAL = {"get_transaction_context": ["evidence"], "get_customer_history": ["evidence"], "get_card_history": ["evidence"], "find_shared_devices": ["graph", "evidence"],
          "find_connected_entities": ["graph"], "find_prior_cases": ["evidence"], "find_similar_cases": ["evidence"], "detect_fraud_patterns": ["patterns"], "get_policy_context": ["policy"]}


class CaseNotFound(KeyError):
    pass


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _amount(text):
    m = re.search(r"\$([\d,]+(?:\.\d+)?)", text or "")
    return float(m.group(1).replace(",", "")) if m else None


def _channel(text):
    t = text or ""
    if "online" in t:
        return "Online"
    m = re.search(r"billing region ([\d.]+)", t)
    return f"In person (billing region {m.group(1).rstrip('0').rstrip('.') if '.' in m.group(1) else m.group(1)})" if m else "Not stated in the trigger"


class CaseService:
    def __init__(self, cases_dir=None, records_dir=None, case_pack=None):
        self.cases_dir = Path(cases_dir or ROOT / "cases")
        self.records_dir = Path(records_dir or ROOT / "demo" / "records")
        self.pack = {}
        with open(case_pack or ROOT / "case_pack.csv", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                self.pack[r["case_id"]] = r
        self._cache = {}

    # ------------------------------------------------------------------ loading
    def ids(self):
        return sorted(p.stem for p in self.cases_dir.glob("HHG-*.json") if CASE_ID.match(p.stem) and p.stem in self.pack)

    def _raw(self, case_id):
        if not CASE_ID.match(case_id or "") or case_id not in self.pack or not (self.cases_dir / f"{case_id}.json").exists():
            raise CaseNotFound(case_id)
        if case_id not in self._cache:
            rec_path = self.records_dir / f"{case_id}.json"
            self._cache[case_id] = (_load(self.cases_dir / f"{case_id}.json"), _load(rec_path) if rec_path.exists() else None)
        return self._cache[case_id]

    @staticmethod
    def _integrity(ans, rec):
        if rec is None:
            return {"ok": False, "checks": [], "note": "no stored investigation record: trace and graph unavailable"}
        scalar = ("status", "verdict", "fraud_probability", "pattern", "pattern_description", "affected_txn_ids", "first_suspicious_txn_id", "connected_card_ids",
                  "connected_device_profiles", "exposure_usd", "similar_prior_cases", "written_to_graph", "graph_case_id")
        pairs = lambda k: {s: [(a["action"], a["route"]) for a in (ans if s == "a" else rec)["next_best_actions"][k]] for s in ("a", "r")}
        checks = [("case", all(ans["case"][k] == rec["case"][k] for k in scalar)),
                  ("summary", ans["case"]["summary"] == strip_citations(rec["case"]["summary"])),
                  ("evidence claims", [e["claim"] for e in ans["case"]["evidence"]] == [e["claim"] for e in rec["case"]["evidence"]]),
                  ("initial actions", pairs("initial")["a"] == pairs("initial")["r"]), ("final actions", pairs("final")["a"] == pairs("final")["r"]),
                  ("sar", ans["sar"] == {k: rec["sar"][k] for k in ans["sar"]})]
        return {"ok": all(v for _, v in checks), "checks": [{"name": n, "match": v} for n, v in checks], "note": ""}

    # ------------------------------------------------------------------ list
    def list_cases(self):
        out = []
        for cid in self.ids():
            ans, rec = self._raw(cid)
            p, c, fin = self.pack[cid], ans["case"], ans["next_best_actions"]["final"]
            out.append({
                "case_id": cid, "trigger_type": p["trigger_type"], "opened_at": p["opened_at"], "customer_id": p["customer_id"], "card_id": p["card_id"],
                "pattern": c["pattern"], "verdict": c["verdict"], "status": c["status"],
                "evidence_strength": rec["uncertainty"]["evidence_strength"] if rec else None,
                "final_actions": [{"action": a["action"], "route": a["route"]} for a in fin],
                "top_action": fin[0]["action"] if fin else None, "exposure_usd": c["exposure_usd"], "affected_txn_count": len(c["affected_txn_ids"]),
                "sar_filed": ans["sar"]["file"], "written_to_graph": c["written_to_graph"], "graph_case_id": c["graph_case_id"],
                "revision": (rec or {}).get("graph_write", {}).get("revision"), "simulated_evidence": bool(rec and rec.get("simulated_evidence_ids")),
                "requested": [q["type"] for q in ans["evidence_requests"]],
            })
        return out

    # ------------------------------------------------------------------ detail
    def get_case(self, case_id):
        ans, rec = self._raw(case_id)
        pack, c = self.pack[case_id], ans["case"]
        rec = rec or {}
        unc = rec.get("uncertainty") or {}
        gw = rec.get("graph_write") or {}
        requests = ans["evidence_requests"]
        outcomes = [q.get("outcome") for q in rec.get("evidence_requests", [])]
        return {
            "integrity": self._integrity(ans, rec if rec else None),
            "header": {"case_id": case_id, "status": c["status"], "verdict": c["verdict"], "pattern": c["pattern"], "trigger_type": pack["trigger_type"],
                       "opened_at": pack["opened_at"], "as_of_note": "Evidence is limited to what existed at the time the case opened.",
                       "evidence_strength": unc.get("evidence_strength"), "uncertainty_level": unc.get("level")},
            "trigger": {"text": pack["trigger_text"], "flagged_txn_id": pack["flagged_txn_id"], "card_id": pack["card_id"], "customer_id": pack["customer_id"],
                        "amount_usd": _amount(pack["trigger_text"]), "channel": _channel(pack["trigger_text"]), "trigger_type": pack["trigger_type"],
                        "opened_at": pack["opened_at"]},
            "uncertainty": self._uncertainty(unc, requests, c, rec),
            "requests": [{"type": q["type"], "label": REQUEST_LABEL.get(q["type"], q["type"]), "asked_after_step": q["asked_after_step"], "assumed_response": q["assumed_response"],
                          "outcome": outcomes[i] if i < len(outcomes) else None, "simulated": True} for i, q in enumerate(requests)],
            "evidence": self._evidence(rec["case"]["evidence"] if rec else [], rec),
            "activity": self._activity(rec, c),
            "actions": {"initial": [self._action(a) for a in ans["next_best_actions"]["initial"]], "final": [self._action(a) for a in ans["next_best_actions"]["final"]],
                        "what_changed": ans["next_best_actions"]["what_changed"]},
            "case": {"status": c["status"], "verdict": c["verdict"], "pattern": c["pattern"], "pattern_description": c["pattern_description"], "summary": c["summary"],
                     "affected_txn_ids": c["affected_txn_ids"], "first_suspicious_txn_id": c["first_suspicious_txn_id"], "exposure_usd": c["exposure_usd"],
                     "connected_card_ids": c["connected_card_ids"], "connected_device_profiles": c["connected_device_profiles"], "similar_prior_cases": c["similar_prior_cases"],
                     "written_to_graph": c["written_to_graph"], "graph_case_id": c["graph_case_id"], "revision": gw.get("revision"), "graph_write_action": gw.get("action"),
                     "fraud_probability": c["fraud_probability"], "probability_note": self._prob_note(unc),
                     "stop_reason": ans["stop_reason"]},
            "sar": ans["sar"],
            "measured": {"tool_calls": ans["tool_calls"], "tokens": ans["tokens"], "latency_s": ans["latency_s"], "mode": (rec.get("agentic") or {}).get("mode", "deterministic")},
            "graph": self._graph(case_id, ans, rec),
        }

    @staticmethod
    def _prob_note(unc):
        cp = (unc.get("calibrated_probability") or {})
        return ("No probability is stated: the calibration procedure did not pass its validation gates on the available history"
                + (f" ({'; '.join(cp.get('gate_notes', []))})" if cp.get("gate_notes") else "") + ".")

    @staticmethod
    def _action(a):
        return {"action": a["action"], "route": a["route"], "route_label": ROUTE_LABEL.get(a["route"], a["route"]), "reason": a["reason"], "rules": a.get("rules") or re.findall(r"R\d+|3[ab]", a["reason"]),
                "requires_approval": a["route"] != "auto", "executed": bool(a.get("executed", False))}

    @staticmethod
    def _uncertainty(unc, requests, c, rec):
        needs = bool(unc.get("needs_more_evidence"))
        before = "uncertain" if needs else c["verdict"]
        return {"evidence_strength": unc.get("evidence_strength"), "level": unc.get("level"), "independent_sources": unc.get("independent_sources"),
                "conflicts": unc.get("conflicts", []), "missing": unc.get("missing", []), "reasons": unc.get("reasons", []), "needs_more_evidence": needs,
                "before_verdict": before, "after_verdict": c["verdict"], "after_status": c["status"],
                "statement": ("Before additional evidence, the evidence was not enough to settle this case on its own." if needs else "The available evidence supports the verdict without further requests.")}

    def _evidence(self, items, rec):
        groups = {}
        for e in items:
            cat = ("Additional evidence" if e["source"] == "customer" else "Policy" if e["source"] == "document" else TOOL_CATEGORY.get(e["ref"], "Pattern signals"))
            if cat == "Pattern signals" and any(str(x).startswith("D_") for x in e["entity_ids"]):
                cat = "Device"
            groups.setdefault(cat, []).append({
                "id": e["evidence_id"], "title": e["claim"], "source": e["ref"].replace("document:", ""), "source_label": TOOL_LABEL.get(e["ref"], e["ref"].replace("document:", "")),
                "rating": e["rating"], "strength": RATING_LABEL[e["rating"]], "why": ("Simulated response: not real customer evidence." if e["simulated"] else RATING_WHY[e["rating"]]),
                "entities": e["entity_ids"], "simulated": e["simulated"]})
        return [{"category": k, "items": groups[k]} for k in CATEGORY_ORDER if k in groups]

    def _activity(self, rec, c):
        """Per-tool summary: what was run, how often, what it produced (from the stored trace and evidence, nothing recomputed)."""
        if not rec:
            return {"steps": [], "tools": [], "events": []}
        ev_by_tool = {}
        for e in rec["case"]["evidence"]:
            ev_by_tool.setdefault(e["ref"], []).append(e)
        steps, events, offset, expanded = [], [], 0.0, (rec.get("exposure_scope") or {}).get("cards_expanded", 0)
        events.append({"stage": "trigger", "label": "Investigation triggered", "tool": None, "source": "Case pack trigger", "status": "done", "summary": self.pack[rec["case_id"]]["trigger_text"],
                       "detail": [], "reveal": ["hero"], "simulated": False, "step": 0})
        for t in rec["trace"]:
            state, ms = t["state"], t["ms"]
            steps.append({"step": t["step"], "state": state, "label": STATE_LABEL.get(state, state), "start_ms": round(offset), "duration_ms": round(ms), "tool_calls": len(t["tool_calls"]), "note": t["note"]})
            offset += ms
            if state == "INITIAL_INVESTIGATION":
                counts = {}
                for tool in t["tool_calls"]:
                    counts[tool] = counts.get(tool, 0) + 1
                emitted, card_seen, expanded_emitted = set(), False, False
                for tool in t["tool_calls"]:      # keep the recorded call order; repeated card-history calls after the first are the ring expansion
                    ev, label = ev_by_tool.get(tool, []), TOOL_LABEL.get(tool, tool)
                    if tool == "get_card_history" and card_seen:
                        if not expanded_emitted:
                            expanded_emitted = True
                            events.append({"stage": "expand", "label": "Ring expansion", "tool": tool, "source": label, "status": "done", "step": t["step"], "simulated": False,
                                           "summary": f"{counts[tool] - 1} connected card histories retrieved across the shared device ({expanded} cards expanded)", "detail": [], "reveal": ["graph"]})
                        continue
                    if tool in emitted:
                        continue
                    emitted.add(tool)
                    card_seen = card_seen or tool == "get_card_history"
                    n = 1 if tool == "get_card_history" else counts[tool]
                    events.append(self._ev("gather", label + (" analyzed" if tool in ("get_card_history", "get_customer_history") else ""), tool, ev[:1] if tool == "get_card_history" else ev, t["step"],
                                           f"{n} call{'s' if n > 1 else ''}", REVEAL.get(tool, ["evidence"])))
            elif state == "EVIDENCE_SYNTHESIS":
                events.append({"stage": "synthesis", "label": "Evidence synthesized", "tool": None, "source": "Deterministic evidence hierarchy", "status": "done", "step": t["step"], "simulated": False,
                               "summary": f"Evidence strength: {rec['uncertainty']['evidence_strength']} ({rec['uncertainty']['independent_sources']} independent source(s))", "detail": [], "reveal": ["patterns", "evidence"]})
            elif state == "UNCERTAINTY_ASSESSMENT":
                events.append({"stage": "uncertainty", "label": "Uncertainty assessed", "tool": None, "source": "Deterministic uncertainty model", "status": "done", "step": t["step"], "simulated": False,
                               "summary": ("Evidence is insufficient to settle the case: more evidence needed" if rec["uncertainty"]["needs_more_evidence"] else "No further evidence needed"),
                               "detail": list(rec["uncertainty"].get("missing", [])), "reveal": ["uncertainty"]})
            elif state == "ADDITIONAL_EVIDENCE_REQUEST":
                for q in rec.get("evidence_requests", []):
                    events.append({"stage": "request", "label": f"{REQUEST_LABEL.get(q['type'], q['type'])} requested", "tool": None, "source": "Simulated responder", "status": q.get("outcome", "done"), "step": t["step"],
                                   "simulated": True, "summary": q["assumed_response"], "detail": [], "reveal": ["requests"]})
            elif state == "NEXT_BEST_ACTION":
                fin = rec["next_best_actions"]["final"]
                events.append({"stage": "action", "label": "Next-best action selected", "tool": None, "source": "Deterministic action engine", "status": "done", "step": t["step"], "simulated": False,
                               "summary": ", ".join(f"{a['action']} [{a['route']}]" for a in fin), "detail": [], "reveal": ["action"]})
            elif state == "EXPLANATION":
                events.append({"stage": "explain", "label": "Explanation prepared", "tool": None, "source": "Template explainer", "status": "done", "step": t["step"], "simulated": False,
                               "summary": "Case summary and SAR decision assembled", "detail": [], "reveal": ["summary", "sar"]})
            elif state == "CASE_WRITE":
                gw = rec.get("graph_write") or {}
                events.append({"stage": "write", "label": "Case written to TigerGraph", "tool": None, "source": "Case writer", "status": "done" if gw.get("written_to_graph") else "failed", "step": t["step"], "simulated": False,
                               "summary": f"{gw.get('graph_case_id', '')} · revision {gw.get('revision', '?')} · {gw.get('action', '')}", "detail": [], "reveal": ["case"]})
        tools = []
        for tool, ev in ev_by_tool.items():
            if tool in TOOL_LABEL:
                tools.append({"tool": tool, "label": TOOL_LABEL[tool], "evidence_count": len(ev), "top": [e["claim"] for e in ev[:2]]})
        for i, e in enumerate(events):
            e["i"] = i
        return {"steps": steps, "tools": tools, "events": events, "total_ms": round(offset)}

    @staticmethod
    def _ev(stage, label, tool, ev, step, calls, reveal):
        return {"stage": stage, "label": label, "tool": tool, "source": TOOL_LABEL.get(tool, tool), "status": "done", "step": step, "simulated": False,
                "summary": (f"{len(ev)} evidence item(s): " + "; ".join(e["claim"][:90] for e in ev[:2])) if ev else "Retrieved; nothing notable recorded as evidence",
                "detail": [f"{e['evidence_id']} [{RATING_LABEL[e['rating']]}] {e['claim']}" for e in ev[:6]] + [calls], "reveal": reveal}

    # ------------------------------------------------------------------ graph
    def _graph(self, case_id, ans, rec):
        """Nodes and edges strictly from the stored case and record. A cluster node stands in for connected cards until the UI expands it."""
        c, refs = ans["case"], (rec or {}).get("graph_refs", {})
        nodes, edges = {}, []

        def node(i, kind, label, **kw):
            nodes.setdefault(i, {"id": i, "kind": kind, "label": label, **kw})

        def edge(a, b, rel, **kw):
            edges.append({"source": a, "target": b, "rel": rel, **kw})
        card, cust = self.pack[case_id]["card_id"], self.pack[case_id]["customer_id"]
        gid = c["graph_case_id"] or f"CASE-{case_id}"
        node(gid, "case", case_id, detail={"verdict": c["verdict"], "status": c["status"], "exposure_usd": c["exposure_usd"], "pattern": c["pattern"]}, focus=True)
        node(cust, "customer", cust, focus=True)
        node(card, "card", card, focus=True)
        edge(cust, card, "OWNS")
        edge(gid, card, "CASE_ON_CARD")
        flagged = self.pack[case_id]["flagged_txn_id"]
        for t in c["affected_txn_ids"] or [flagged]:
            node("txn:" + t, "txn", t, detail={"flagged": t == flagged, "first": t == c["first_suspicious_txn_id"]}, focus=True)
            edge(card, "txn:" + t, "MADE")
            if c["affected_txn_ids"]:
                edge(gid, "txn:" + t, "CASE_TXN")
        if not c["affected_txn_ids"]:
            nodes["txn:" + flagged]["detail"]["note"] = "flagged transaction (not part of a fraud episode)"
        devices = refs.get("device_ids") or []
        conn = refs.get("connected_cards") or {}
        for d in devices:
            node(d, "device", d, detail={"profile": (c["connected_device_profiles"] or [None])[0], "shared_by_cards": len(conn)}, focus=True, ring=len(conn) > 0)
            edge(card, d, "USED_DEVICE", ring=len(conn) > 0)
            edge(gid, d, "CASE_CITES_DEVICE")
        if conn:
            cluster = "cluster:" + case_id
            node(cluster, "cluster", f"{len(conn)} connected cards", detail={"count": len(conn)}, members=list(conn))
            for d in devices[:1]:
                edge(d, cluster, "SHARED_BY", ring=True)
            for cc, via in conn.items():
                dev = via.split(":", 1)[1] if ":" in via else (devices[0] if devices else None)
                node(cc, "card", cc, hidden=True, cluster=cluster, detail={"connected_via": via})
                if dev and dev in nodes:
                    edge(dev, cc, "SHARED_BY", ring=True, hidden=True)
                edge(gid, cc, "CASE_CONNECTED_TO", hidden=True)
        for pc in c["similar_prior_cases"][:10]:
            node(pc, "prior", pc, hidden=False, detail={"note": "textual precedent retrieved by GraphRAG (memory, not proof)"})
            edge(gid, pc, "SIMILAR_CASE")
        return {"nodes": list(nodes.values()), "edges": edges,
                "legend": [{"kind": k, "label": v} for k, v in (("case", "Current case"), ("customer", "Customer"), ("card", "Card"), ("txn", "Transaction"), ("device", "Device profile"), ("cluster", "Connected cards"), ("prior", "Prior case"))],
                "note": "Built from the stored investigation record. Email domains and billing regions are not part of the stored record and are not drawn."}

    def overview_graph(self):
        """Cross-case view: cases, their cards and shared devices, from the stored records only."""
        nodes, edges = {}, []
        for cid in self.ids():
            ans, rec = self._raw(cid)
            if not rec:
                continue
            c, refs = ans["case"], rec["graph_refs"]
            nodes[cid] = {"id": cid, "kind": "case", "label": cid, "detail": {"verdict": c["verdict"], "pattern": c["pattern"], "exposure_usd": c["exposure_usd"]}}
            card = self.pack[cid]["card_id"]
            nodes.setdefault(card, {"id": card, "kind": "card", "label": card, "detail": {}})
            edges.append({"source": cid, "target": card, "rel": "CASE_ON_CARD"})
            for d in refs.get("device_ids") or []:
                nodes.setdefault(d, {"id": d, "kind": "device", "label": d, "detail": {"shared_by_cards": len(refs.get("connected_cards") or {})}, "ring": True})
                edges.append({"source": card, "target": d, "rel": "USED_DEVICE", "ring": True})
        return {"nodes": list(nodes.values()), "edges": edges, "legend": [{"kind": "case", "label": "Case"}, {"kind": "card", "label": "Card"}, {"kind": "device", "label": "Device profile"}], "note": ""}

    # ------------------------------------------------------------------ other views
    def customers(self):
        agg = {}
        for r in self.list_cases():
            a = agg.setdefault(r["customer_id"], {"customer_id": r["customer_id"], "cards": set(), "cases": [], "exposure_usd": 0.0})
            a["cards"].add(r["card_id"])
            a["cases"].append({"case_id": r["case_id"], "verdict": r["verdict"], "pattern": r["pattern"]})
            a["exposure_usd"] = round(a["exposure_usd"] + r["exposure_usd"], 2)
        return sorted(({**a, "cards": sorted(a["cards"])} for a in agg.values()), key=lambda x: x["customer_id"])

    def overview(self):
        rows = self.list_cases()
        count = lambda k: {v: sum(1 for r in rows if r[k] == v) for v in sorted({r[k] for r in rows})}
        return {"total": len(rows), "verdicts": count("verdict"), "patterns": count("pattern"), "triggers": count("trigger_type"), "sar_filed": sum(r["sar_filed"] for r in rows),
                "written_to_graph": sum(r["written_to_graph"] for r in rows), "exposure_usd": round(sum(r["exposure_usd"] for r in rows), 2),
                "needs_approval": sum(1 for r in rows if any(a["route"] != "auto" for a in r["final_actions"])), "showcase": "HHG-014" if "HHG-014" in self.ids() else None}
