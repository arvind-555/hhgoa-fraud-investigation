"""CaseWriter: the real write path from an agent case record into FI_Case (+ CASE_* / SIMILAR_CASE edges). Runner-side; bound to the case's as_of.

Flow: validate (structural + graph) -> build vertex/edges -> compare with the existing vertex -> upsert -> read back and verify -> report.
Deterministic and idempotent:
* graph_case_id = "CASE-" + source case id (no clock, no counter);
* content_sha256 of the canonical case content (excludes latency, tool counts, trace and the write receipt) is stored in evidence_json.provenance;
* same content again  -> nothing is written (action "unchanged", revision kept);
* changed content     -> revision + 1, the case's outgoing edges are replaced together with the vertex (action "updated");
* a different as_of for an existing case id is refused (an investigation is pinned to one as_of).
created_at / updated_at hold the CASE time (as_of), not the wall clock, so re-running never changes the vertex.
Only FI_Case-family objects are written; source facts (Transaction, Customer, Card, DeviceProfile, ClosedCase, ...) are never touched.

Attribute mapping (FI_Case, DDL spec 2.1 -- no schema change):
  initial_actions/final_actions   JSON lists [{action, route, reason, rules, requires_approval, executed:false}]
  evidence_json                   {"evidence":[...], "simulated_evidence_ids":[...], "uncertainty":{...}, "probability":{...}, "exposure_scope":{...}, "provenance":{...}}
  evidence_requests_json          the evidence requests, each marked simulated with the simulator policy/seed
  sar_file / sar_json             sar_file = FILE_REPORT in the final actions; sar_json = the SAR object (file, reason, narrative, subjects, total_amount_usd, activity_dates)
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fraud_tools.tools import ts_of  # noqa: E402

from .case_validator import CaseValidationError, assert_valid  # noqa: E402

WRITER_VERSION = "9b-cw1"
HASH_FIELDS = ("case_id", "case", "sar", "evidence_requests", "next_best_actions", "exposure_scope", "simulated_evidence_ids", "uncertainty", "stop_reason", "graph_refs")
CASE_HASH_EXCLUDE = {"written_to_graph", "graph_case_id"}


class CaseWriteError(Exception):
    pass


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(record):
    body = {k: record[k] for k in HASH_FIELDS if k in record}
    body["case"] = {k: v for k, v in body["case"].items() if k not in CASE_HASH_EXCLUDE}
    return hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()


def graph_case_id_for(source_case_id):
    return f"CASE-{source_case_id}"


class CaseWriter:
    def __init__(self, as_of_epoch, io=None, simulator=None, agent_version="9b"):
        if not isinstance(as_of_epoch, int) or as_of_epoch <= 0:
            raise CaseWriteError("as_of_epoch must be a positive int (supplied by the runner)")
        if io is None:
            from .graphio import GraphIO
            io = GraphIO()
        self.as_of, self.io, self.simulator, self.agent_version = as_of_epoch, io, simulator, agent_version

    # ------------------------------------------------------------------------------------------ build
    def build(self, record, revision):
        c, nb = record["case"], record["next_best_actions"]
        refs = record["graph_refs"]
        h = content_hash(record)
        prov = {"source_case_id": record["case_id"], "as_of_epoch": self.as_of, "content_sha256": h, "writer_version": WRITER_VERSION, "agent_version": self.agent_version,
                "calibration": c.get("calibration", {}), "graph_facts_modified": False,
                "simulator": self.simulator.describe() if self.simulator is not None and record.get("evidence_requests") else None}
        ev = {"evidence": c["evidence"], "simulated_evidence_ids": record["simulated_evidence_ids"], "uncertainty": record["uncertainty"],
              "probability": {"value": c["fraud_probability"], "calibrated": c.get("probability_calibrated", False), "source": c.get("probability_source", ""),
                              **c.get("calibration", {})},
              "exposure_scope": record["exposure_scope"], "connected_device_profiles": c["connected_device_profiles"], "provenance": prov}
        file_report = any(a["action"] == "FILE_REPORT" for a in nb["final"])
        gid = graph_case_id_for(record["case_id"])
        attrs = {"source_case_id": record["case_id"], "as_of_epoch": self.as_of, "as_of": ts_of(self.as_of), "status": c["status"], "verdict": c["verdict"],
                 "fraud_probability": -1.0 if c["fraud_probability"] is None else float(c["fraud_probability"]),   # -1 = missing (E18); never a made-up value
                 "pattern": c["pattern"], "pattern_description": c["pattern_description"],
                 "first_suspicious_txn_id": int(c["first_suspicious_txn_id"] or 0), "exposure_usd": float(c["exposure_usd"]), "summary": c["summary"],
                 "initial_actions": canonical(nb["initial"]), "final_actions": canonical(nb["final"]), "what_changed": nb["what_changed"],
                 "evidence_json": canonical(ev), "evidence_requests_json": canonical(record["evidence_requests"]), "sar_file": file_report,
                 "sar_json": canonical(record["sar"]),
                 "stop_reason": record["stop_reason"], "revision": revision, "created_at": ts_of(self.as_of), "updated_at": ts_of(self.as_of)}
        roles = {}
        if refs.get("flagged_txn_id"):
            roles.setdefault(int(refs["flagged_txn_id"]), set()).add("flagged")
        for t in c["affected_txn_ids"]:
            roles.setdefault(int(t), set()).add("affected")
        if c["first_suspicious_txn_id"]:
            roles.setdefault(int(c["first_suspicious_txn_id"]), set()).add("first_suspicious")
        edges = {"CASE_ON_CARD": [(refs["card_id"], {})],
                 "CASE_TXN": [(t, {"role": "+".join(sorted(r))}) for t, r in sorted(roles.items())],
                 "CASE_CONNECTED_TO": [(k, {"via": v}) for k, v in sorted(refs.get("connected_cards", {}).items())],
                 "CASE_CITES_DEVICE": [(d, {}) for d in sorted(refs.get("device_ids", []))],
                 "SIMILAR_CASE": [(s["case_id"], {"score": s.get("score", 1.0), "method": s.get("method", "prior_cases_tool"), "reason": "|".join(s["match_reasons"])[:200]}) for s in refs.get("similar_cases", [])]}
        return gid, attrs, edges, h

    # ------------------------------------------------------------------------------------------ write
    def write(self, record):
        assert_valid(record, self.as_of, self.io)                                 # rejects malformed / inconsistent / unsafe cases BEFORE anything is written
        gid = graph_case_id_for(record["case_id"])
        existing = self.io.get_vertex("FI_Case", gid)
        revision, action = 1, "created"
        if existing:
            if existing.get("source_case_id") != record["case_id"] or existing.get("as_of_epoch") != self.as_of:
                raise CaseWriteError(f"{gid} exists with a different source case or as_of; refusing to overwrite")
            old_hash = (json.loads(existing["evidence_json"]).get("provenance") or {}).get("content_sha256")
            if old_hash == content_hash(record):
                return self._receipt(gid, existing["revision"], "unchanged", old_hash)
            revision, action = existing["revision"] + 1, "updated"
        gid, attrs, edges, h = self.build(record, revision)
        if existing:
            self.io.delete_case_edges(gid)                                        # replace this case's own edges only
        self.io.upsert_case(gid, attrs, edges)
        self._verify(gid, attrs, edges)
        return self._receipt(gid, revision, action, h)

    def _verify(self, gid, attrs, edges):
        back = self.io.get_vertex("FI_Case", gid)
        if not back:
            raise CaseWriteError(f"read-back failed: {gid} not found after upsert")
        for k in ("source_case_id", "as_of_epoch", "status", "verdict", "pattern", "revision", "sar_file", "evidence_json", "final_actions"):
            if back.get(k) != attrs[k]:
                raise CaseWriteError(f"read-back mismatch on {k}")
        if abs(back["fraud_probability"] - attrs["fraud_probability"]) > 1e-9 or abs(back["exposure_usd"] - attrs["exposure_usd"]) > 1e-6:
            raise CaseWriteError("read-back mismatch on numbers")
        got = self.io.case_edge_counts(gid)
        for et, tg in edges.items():
            if sorted(str(t) for t, _ in tg) != sorted(x[1] for x in got[et]):
                raise CaseWriteError(f"read-back mismatch on {et} edges")

    @staticmethod
    def _receipt(gid, revision, action, h):
        return {"graph_case_id": gid, "revision": revision, "action": action, "content_sha256": h, "written_to_graph": True}
