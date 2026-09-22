"""Live investigation PREVIEW (read-only).

Runs the existing deterministic `Agent.run()` against live TigerGraph for ONE of the 20 benchmark cases and returns the fresh record. It is a preview, not
a case-write endpoint:

* the case id is the ONLY caller input; every other value (flagged transaction, trigger type, opened_at, as_of) comes from the server-side case_pack.csv,
  and as_of is derived with the existing `epoch_of(opened_at)`;
* the agent is built with `writer=None`: no CaseWriter is created and no graph write API is called anywhere in this module (FI_Case is only READ, for the
  consistency check);
* no LLM, no MCP, no arbitrary tool arguments, no GSQL: the composition is the trusted one (QueryClient -> InvestigationSession -> ToolGateway -> Agent) and the
  gateway's whitelist and temporal guards are untouched;
* one investigation at a time (single-flight), a short cache of completed results, and every failure reduced to a fixed, safe message.
"""
import json
import re
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

CASE_IDS = tuple(f"HHG-{i:03d}" for i in range(1, 21))
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
CACHE_TTL_S = 120          # a completed result is reused for this long, so repeated clicks never stack graph investigations
MAX_RUN_S = 300            # a run older than this is reported as timed out and frees the single slot
KEEP_JOBS = 20

MSG_GRAPH = "Live graph unavailable"
MSG_INTERNAL = "The live investigation could not be completed"
MSG_TIMEOUT = "The live investigation timed out"
MSG_MATCH = "Live result matches stored validated case."
MSG_DIFF = "Live result differs from stored case."
MSG_UNREAD = "The stored case could not be read, so consistency was not verified."
MSG_NONE = "No stored case was found for this case, so consistency was not verified."


class UnknownCase(Exception):
    pass


class Busy(Exception):
    def __init__(self, retry_after_s):
        super().__init__("busy")
        self.retry_after_s = retry_after_s


def classify(exc):
    """Reduce ANY failure to a fixed message. The exception text is inspected only to tell a graph/network problem from an internal one; it is never returned."""
    text = str(exc)
    graph = isinstance(exc, (SystemExit, OSError, TimeoutError)) or type(exc).__name__ in ("ToolError", "GraphIOError", "URLError", "HTTPError") \
        or "ToolError" in text or "network error" in text or "graph request failed" in text or "token mint failed" in text
    return ("graph_unavailable", MSG_GRAPH) if graph else ("internal", MSG_INTERNAL)


def default_investigate(row):
    """The trusted composition, the same one the benchmark uses. Nothing here is caller-controlled beyond the case id that selected `row`."""
    from agent.gateway import ToolGateway
    from agent.orchestrator import Agent
    from agent.schema import Trigger
    from agent.simulator import EvidenceSimulator
    from calibration.runtime import Calibrator
    from fraud_tools.qclient import QueryClient
    from fraud_tools.tools import InvestigationSession, epoch_of
    as_of = epoch_of(row["opened_at"])
    gateway = ToolGateway(InvestigationSession(as_of, QueryClient()))
    trigger = Trigger(row["case_id"], row["flagged_txn_id"], row["trigger_type"])
    return Agent(gateway, trigger, responder=EvidenceSimulator(), calibrator=Calibrator(as_of), writer=None).run()


def default_read_stored(graph_case_id):
    """READ-ONLY: one FI_Case vertex through the whitelisted GraphIO. Never used to write."""
    from agent.graphio import GraphIO
    v = GraphIO().get_vertex("FI_Case", graph_case_id)
    return (v.get("attributes", v)) if v else None


class Job:
    def __init__(self, case_id, now):
        self.id, self.case_id, self.status = uuid.uuid4().hex, case_id, "queued"
        self.created, self.started, self.finished = now, None, None
        self.error, self.code, self.result = None, None, None


class LiveService:
    def __init__(self, pack, investigate=None, read_stored=None, service_factory=None, clock=time.monotonic, spawn=None, cache_ttl=CACHE_TTL_S, max_run_s=MAX_RUN_S):
        self.pack = pack
        self._investigate = investigate or default_investigate
        self._read_stored = read_stored or default_read_stored
        self._service_factory = service_factory
        self._clock, self._cache_ttl, self._max_run = clock, cache_ttl, max_run_s
        self._spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True).start())
        self._lock = threading.Lock()
        self._jobs, self._active = {}, None

    # ---------------------------------------------------------------------------------------------- submit / view
    def submit(self, case_id):
        """-> (job, reused). Only the case id is accepted; anything outside the fixed allowlist is refused before any work is scheduled."""
        if case_id not in CASE_IDS or case_id not in self.pack:
            raise UnknownCase(case_id)
        with self._lock:
            now = self._clock()
            self._expire(now)
            for j in self._jobs.values():                                           # the same case is already queued / running: share that job
                if j.case_id == case_id and j.status in ("queued", "running"):
                    return j, True
            for j in sorted(self._jobs.values(), key=lambda x: x.finished or 0, reverse=True):
                if j.case_id == case_id and j.status == "completed" and now - j.finished <= self._cache_ttl:
                    return j, True                                                  # recent completed result: no new graph investigation
            if self._active is not None:                                            # single-flight: never stack investigations
                raise Busy(15)
            job = Job(case_id, now)
            self._jobs[job.id] = job
            self._active = job.id
            for old in sorted(self._jobs.values(), key=lambda x: x.created)[:-KEEP_JOBS]:
                if old.id != self._active:
                    del self._jobs[old.id]
        self._spawn(lambda: self._run(job))
        return job, False

    def view(self, job_id):
        if not JOB_ID.match(job_id or ""):
            raise UnknownCase(job_id)
        with self._lock:
            self._expire(self._clock())
            job = self._jobs.get(job_id)
            if job is None:
                raise UnknownCase(job_id)
            return self._view(job)

    def _view(self, job):
        now = self._clock()
        t0 = job.started if job.started is not None else job.created
        out = {"job_id": job.id, "case_id": job.case_id, "status": job.status, "elapsed_s": round((job.finished or now) - t0, 1) if job.status != "queued" else 0.0,
               "error": job.error, "error_code": job.code}
        if job.status == "completed":
            out["result"] = job.result
        return out

    def view_job(self, job):
        with self._lock:
            return self._view(job)

    def _expire(self, now):
        a = self._jobs.get(self._active) if self._active else None
        if a is not None and a.started is not None and now - a.started > self._max_run:
            a.status, a.finished, a.code, a.error = "failed", now, "timeout", MSG_TIMEOUT
            self._active = None

    # ---------------------------------------------------------------------------------------------- run
    def _run(self, job):
        with self._lock:
            job.status, job.started = "running", self._clock()
        result = code = error = None
        try:
            record = self._investigate(self.pack[job.case_id])
            result = self._package(job.case_id, record)
        except (Exception, SystemExit) as e:            # tg.token() ends a failed token mint with SystemExit; nothing may escape or reach the caller
            code, error = classify(e)
        finally:
            with self._lock:
                if job.status != "failed":               # a job already reported as timed out stays failed
                    job.finished = self._clock()
                    if error is None:
                        job.status, job.result = "completed", result
                    else:
                        job.status, job.code, job.error = "failed", code, error
                if self._active == job.id:
                    self._active = None

    def _package(self, case_id, rec):
        from agent import answer_file as AF
        if rec["case"]["written_to_graph"] or rec["case"]["graph_case_id"] or "graph_write" in rec:
            raise RuntimeError("preview record carries a graph write")            # cannot happen with writer=None; refuse to show it if it ever did
        ans = AF.assemble(rec)
        if self._service_factory is not None:
            svc = self._service_factory()
        else:
            from ui_api.service import CaseService
            svc = CaseService()
        svc._cache[case_id] = (ans, rec)                                          # a private instance: the replay service and its cache are never touched
        detail = svc.get_case(case_id)
        detail["live"] = {"preview": True, "written_to_graph": False}
        for ev in detail["activity"]["events"]:
            if ev["stage"] == "write":
                ev.update(label="Case write not performed (live preview)", source="Live preview", status="done", summary="No FI_Case write; the result is a preview only")
        nb = rec["next_best_actions"]
        return {"live": True, "executed_against": "live TigerGraph", "written_to_graph": False, "fi_case_write": "not performed",
                "summary": {"verdict": rec["case"]["verdict"], "status": rec["case"]["status"], "pattern": rec["case"]["pattern"], "exposure_usd": rec["case"]["exposure_usd"],
                            "evidence_strength": rec["uncertainty"]["evidence_strength"], "sar_file": bool(rec["sar"]["file"]),
                            "final_actions": [{"action": a["action"], "route": a["route"]} for a in nb["final"]],
                            "tool_calls": rec["tool_calls"], "latency_s": rec["latency_s"], "graph_queries": rec["gateway"]["graph_queries_executed"]},
                "consistency": self._consistency(case_id, rec), "detail": detail}

    # ---------------------------------------------------------------------------------------------- consistency (READ-ONLY)
    def _consistency(self, case_id, rec):
        from agent.case_writer import content_hash, graph_case_id_for
        live_sha = content_hash(rec)
        base = {"checked": False, "match": None, "live_sha256": live_sha[:12], "stored_sha256": None, "stored_revision": None, "differs": [], "written_to_graph": False}
        try:
            stored = self._read_stored(graph_case_id_for(case_id))
        except (Exception, SystemExit):
            return {**base, "level": "unknown", "message": MSG_UNREAD}
        if not stored:
            return {**base, "level": "unknown", "message": MSG_NONE}
        try:
            stored_sha = (json.loads(stored["evidence_json"]).get("provenance") or {}).get("content_sha256")
            stored_final = [(a["action"], a["route"]) for a in json.loads(stored["final_actions"])]
        except Exception:                                                        # noqa: BLE001 - unreadable stored content is reported, never raised
            return {**base, "level": "unknown", "message": MSG_UNREAD}
        out = {**base, "checked": True, "stored_sha256": (stored_sha or "")[:12], "stored_revision": stored.get("revision")}
        if stored_sha and stored_sha == live_sha:
            return {**out, "match": True, "level": "match", "message": MSG_MATCH}
        c = rec["case"]
        live_final = [(a["action"], a["route"]) for a in rec["next_best_actions"]["final"]]
        pairs = {"verdict": (c["verdict"], stored.get("verdict")), "status": (c["status"], stored.get("status")), "pattern": (c["pattern"], stored.get("pattern")),
                 "final_actions": (live_final, stored_final), "sar_decision": (bool(rec["sar"]["file"]), bool(stored.get("sar_file"))),
                 "exposure_usd": (float(c["exposure_usd"]), stored.get("exposure_usd"))}
        differs = [k for k, (a, b) in pairs.items() if a != b] or ["content_hash_only"]
        return {**out, "match": False, "level": "differs", "message": MSG_DIFF, "differs": differs}
