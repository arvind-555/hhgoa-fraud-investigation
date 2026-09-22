"""Live investigation PREVIEW (src/ui_api/live.py + the two /api/live routes): case id is the only input, the agent runs with writer=None, nothing is written,
failures are reduced to safe messages, one run at a time, a short result cache, and the live result is compared with the stored FI_Case read-only.
Offline tests use a real Agent over the repository's FakeSession; one gated test runs the real thing against live TigerGraph (reads only)."""
import asyncio
import inspect
import json
import os
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))
from agent.case_writer import canonical, content_hash  # noqa: E402
from agent.gateway import ToolGateway  # noqa: E402
from agent.orchestrator import Agent  # noqa: E402
from agent.schema import Trigger  # noqa: E402
from agent.simulator import EvidenceSimulator  # noqa: E402
from calibration.runtime import Calibrator  # noqa: E402
from fraud_tools.guards import ToolError  # noqa: E402
from ui_api import live as L  # noqa: E402
from ui_api.server import make_app  # noqa: E402
from ui_api.service import CaseService  # noqa: E402
from test_agentic import TXN  # noqa: E402
from test_simulator_exposure import FakeSession  # noqa: E402

PACK = CaseService().pack
ROW = dict(PACK["HHG-014"], flagged_txn_id=TXN, trigger_type="risk_score")      # a pack row whose transaction the FakeSession knows
SECRET = "fake-secret-value-123"


def fake_record(case_id="HHG-014"):
    """A real deterministic Agent.run() over FakeSession, exactly as the live composition builds it (writer=None, Calibrator, simulator)."""
    return Agent(ToolGateway(FakeSession(ring=False)), Trigger(case_id, TXN, "risk_score"), responder=EvidenceSimulator(), calibrator=Calibrator(2000), writer=None).run()


def stored_vertex(rec, revision=3, **override):
    nb, c = rec["next_best_actions"], rec["case"]
    v = {"source_case_id": rec["case_id"], "revision": revision, "verdict": c["verdict"], "status": c["status"], "pattern": c["pattern"], "exposure_usd": float(c["exposure_usd"]),
         "sar_file": bool(rec["sar"]["file"]), "final_actions": canonical(nb["final"]), "evidence_json": canonical({"provenance": {"content_sha256": content_hash(rec)}})}
    v.update(override)
    return v


def make_service(record, stored="same", **kw):
    calls = {"investigate": [], "read": []}

    def investigate(row):
        calls["investigate"].append(dict(row))
        return json.loads(json.dumps(record, default=str))

    def read_stored(gid):
        calls["read"].append(gid)
        if stored == "same":
            return stored_vertex(record)
        if callable(stored):
            return stored(gid)
        return stored
    kw.setdefault("spawn", lambda fn: fn())                                       # synchronous by default: deterministic tests
    return L.LiveService({**PACK, "HHG-014": ROW, "HHG-001": dict(PACK["HHG-001"], flagged_txn_id=TXN, trigger_type="risk_score")}, investigate, read_stored, **kw), calls


class Http:
    def __init__(self, app):
        self.app = app

    def call(self, method, path, body=b"", query=b""):
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(m):
            sent.append(m)
        scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": query, "headers": [(b"content-length", str(len(body)).encode())],
                 "server": ("t", 80), "scheme": "http", "client": ("c", 1), "http_version": "1.1", "root_path": ""}
        asyncio.run(self.app(scope, receive, send))
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, (json.loads(data) if data[:1] in (b"{", b"[") else data.decode("utf-8", "replace"))


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = fake_record()

    def test_valid_case_completes_with_a_preview_result_that_writes_nothing(self):
        svc, calls = make_service(self.rec)
        job, reused = svc.submit("HHG-014")
        v = svc.view(job.id)
        self.assertEqual((v["status"], reused, v["error"]), ("completed", False, None))
        r = v["result"]
        self.assertEqual((r["live"], r["written_to_graph"], r["fi_case_write"], r["executed_against"]), (True, False, "not performed", "live TigerGraph"))
        self.assertIs(r["detail"]["case"]["written_to_graph"], False)
        self.assertEqual(r["detail"]["case"]["graph_case_id"], "")
        self.assertEqual(r["detail"]["live"], {"preview": True, "written_to_graph": False})
        self.assertEqual(r["summary"]["verdict"], self.rec["case"]["verdict"])
        self.assertEqual(r["summary"]["final_actions"], [{"action": a["action"], "route": a["route"]} for a in self.rec["next_best_actions"]["final"]])
        self.assertEqual(r["summary"]["sar_file"], bool(self.rec["sar"]["file"]))
        self.assertTrue(r["detail"]["activity"]["events"] and any(e["stage"] == "write" and "not performed" in e["label"] for e in r["detail"]["activity"]["events"]))
        self.assertEqual(len(calls["investigate"]), 1)

    def test_the_case_id_is_the_only_input_and_everything_else_comes_from_the_server_side_pack(self):
        self.assertEqual(list(inspect.signature(L.LiveService.submit).parameters), ["self", "case_id"])
        svc, calls = make_service(self.rec)
        svc.submit("HHG-014")
        self.assertEqual(calls["investigate"][0], ROW)                             # flagged txn / trigger type / opened_at are the pack's, not the caller's

    def test_only_the_twenty_benchmark_ids_are_accepted(self):
        svc, calls = make_service(self.rec)
        self.assertEqual(L.CASE_IDS, tuple(f"HHG-{i:03d}" for i in range(1, 21)))
        for bad in ("HHG-000", "HHG-021", "hhg-014", "HHG-014 ", " HHG-014", "HHG-14", "3514030", "../x", "HHG-014;DROP", "HHG-014|HHG-001", "", "SYN-001", "CASE-HHG-014"):
            with self.assertRaises(L.UnknownCase, msg=repr(bad)):
                svc.submit(bad)
        self.assertEqual(calls["investigate"], [])

    def test_default_composition_uses_writer_none_and_the_pack_as_of(self):
        from fraud_tools.tools import epoch_of
        seen = {}

        class SpyAgent:
            def __init__(self, gateway, trigger, **kw):
                seen.update(trigger=trigger, kw=kw)

            def run(self):
                return "record"
        boom = mock.Mock(side_effect=AssertionError("a graph write path was touched"))
        with mock.patch("agent.orchestrator.Agent", SpyAgent), mock.patch("fraud_tools.qclient.QueryClient", lambda: "client"), \
                mock.patch("fraud_tools.tools.InvestigationSession", lambda as_of, client: seen.update(as_of=as_of, client=client) or "session"), \
                mock.patch("agent.gateway.ToolGateway", lambda s: seen.update(session=s) or "gateway"), \
                mock.patch("agent.case_writer.CaseWriter.__init__", boom), mock.patch("agent.graphio.GraphIO.upsert_case", boom), \
                mock.patch("agent.graphio.GraphIO.delete_case_edges", boom), mock.patch("agent.graphio.GraphIO.delete_test_case", boom):
            out = L.default_investigate(PACK["HHG-014"])
        self.assertEqual(out, "record")
        self.assertEqual(seen["as_of"], epoch_of(PACK["HHG-014"]["opened_at"]))       # derived server-side with the existing helper
        self.assertEqual((seen["client"], seen["session"]), ("client", "session"))
        t = seen["trigger"]
        self.assertEqual((t.case_id, t.txn_id, t.trigger_type), ("HHG-014", PACK["HHG-014"]["flagged_txn_id"], PACK["HHG-014"]["trigger_type"]))
        kw = seen["kw"]
        self.assertIsNone(kw["writer"])
        self.assertIsInstance(kw["responder"], EvidenceSimulator)
        self.assertIsNotNone(kw["calibrator"])
        for absent in ("investigator", "budget", "sar_writer", "store", "explainer"):
            self.assertNotIn(absent, kw)                                                 # no LLM, no token budget, no local store
        boom.assert_not_called()

    def test_writer_none_means_the_record_says_nothing_was_written(self):
        self.assertIs(self.rec["case"]["written_to_graph"], False)
        self.assertEqual(self.rec["case"]["graph_case_id"], "")
        self.assertNotIn("graph_write", self.rec)
        bad = json.loads(json.dumps(self.rec, default=str))
        bad["graph_write"] = {"written_to_graph": True}
        svc, _ = make_service(bad)
        job, _ = svc.submit("HHG-014")
        v = svc.view(job.id)
        self.assertEqual((v["status"], v["error"]), ("failed", L.MSG_INTERNAL))          # a record that claims a write is refused, never shown

    def test_module_has_no_write_llm_mcp_or_gsql_path(self):
        code = "\n".join(l for l in (ROOT / "src" / "ui_api" / "live.py").read_text(encoding="utf-8").splitlines() if not l.strip().startswith(("#", '"""', "*")))
        code = re.sub(r'""".*?"""', "", code, flags=re.S)
        for banned in ("CaseWriter(", "upsert", "delete_", "put_vertex", "writer=CaseWriter", "gsql", "interpreted_query", "OPENAI", "openai", "mcp", "providers", "investigator", "TG_SECRET"):
            self.assertNotIn(banned, code, banned)
        self.assertEqual(code.count("calibrator=Calibrator(as_of), writer=None).run()"), 1)

    def test_failures_become_safe_messages_and_never_leak(self):
        cases = [(ToolError(f"network error: Bearer {SECRET} timed out"), "graph_unavailable", L.MSG_GRAPH), (SystemExit(f"token mint failed (HTTP None) {SECRET}"), "graph_unavailable", L.MSG_GRAPH),
                 (TimeoutError(SECRET), "graph_unavailable", L.MSG_GRAPH), (RuntimeError(f"tool error during investigation: {{'error': 'ToolError'}} {SECRET}"), "graph_unavailable", L.MSG_GRAPH),
                 (RuntimeError(f"boom {SECRET} Traceback (most recent call last)"), "internal", L.MSG_INTERNAL), (KeyError(SECRET), "internal", L.MSG_INTERNAL)]
        for exc, code, msg in cases:
            svc = L.LiveService(PACK, lambda row, e=exc: (_ for _ in ()).throw(e), lambda g: None, spawn=lambda fn: fn())
            job, _ = svc.submit("HHG-014")
            v = svc.view(job.id)
            self.assertEqual((v["status"], v["error_code"], v["error"]), ("failed", code, msg), repr(exc))
            self.assertNotIn(SECRET, json.dumps(v))
            self.assertNotIn("Traceback", json.dumps(v))

    def test_failed_runs_free_the_slot_and_are_not_cached(self):
        n = {"i": 0}

        def flaky(row):
            n["i"] += 1
            if n["i"] == 1:
                raise SystemExit("token mint failed")
            return self.rec
        svc = L.LiveService({**PACK, "HHG-014": ROW}, flaky, lambda g: None, spawn=lambda fn: fn())
        a, _ = svc.submit("HHG-014")
        self.assertEqual(svc.view(a.id)["status"], "failed")
        b, reused = svc.submit("HHG-014")
        self.assertFalse(reused)
        self.assertNotEqual(a.id, b.id)
        self.assertEqual(svc.view(b.id)["status"], "completed")

    def test_single_flight_one_investigation_at_a_time(self):
        gate, started = threading.Event(), threading.Event()
        count = {"n": 0}

        def slow(row):
            count["n"] += 1
            started.set()
            gate.wait(10)
            return self.rec
        svc = L.LiveService({**PACK, "HHG-014": ROW, "HHG-001": ROW}, slow, lambda g: None)          # real background thread
        a, reused = svc.submit("HHG-014")
        self.assertTrue(started.wait(5))
        self.assertEqual(svc.view(a.id)["status"], "running")
        again, reused = svc.submit("HHG-014")                                                            # same case: shares the running job
        self.assertEqual((again.id, reused), (a.id, True))
        with self.assertRaises(L.Busy):                                                                    # another case: refused, not stacked
            svc.submit("HHG-001")
        self.assertEqual(count["n"], 1)
        gate.set()
        for _ in range(100):
            if svc.view(a.id)["status"] == "completed":
                break
            threading.Event().wait(0.05)
        self.assertEqual(svc.view(a.id)["status"], "completed")
        b, _ = svc.submit("HHG-001")                                                                       # the slot is free again
        self.assertNotEqual(b.id, a.id)

    def test_completed_results_are_cached_briefly(self):
        t = {"now": 1000.0}
        svc, calls = make_service(self.rec, clock=lambda: t["now"], cache_ttl=120)
        a, _ = svc.submit("HHG-014")
        t["now"] += 60
        b, reused = svc.submit("HHG-014")
        self.assertEqual((b.id, reused, len(calls["investigate"])), (a.id, True, 1))                      # reused: no second graph investigation
        t["now"] += 61                                                                                       # 121 s after completion: expired
        c, reused = svc.submit("HHG-014")
        self.assertEqual((reused, len(calls["investigate"])), (False, 2))
        self.assertNotEqual(c.id, a.id)

    def test_a_run_that_never_finishes_is_reported_as_timed_out_and_frees_the_slot(self):
        t = {"now": 0.0}
        gate = threading.Event()
        svc = L.LiveService({**PACK, "HHG-014": ROW, "HHG-001": ROW}, lambda row: (gate.wait(10), self.rec)[1], lambda g: None, clock=lambda: t["now"], max_run_s=300)
        a, _ = svc.submit("HHG-014")
        for _ in range(100):
            if svc.view(a.id)["status"] == "running":
                break
            threading.Event().wait(0.02)
        t["now"] = 301
        v = svc.view(a.id)
        self.assertEqual((v["status"], v["error"], v["error_code"]), ("failed", L.MSG_TIMEOUT, "timeout"))
        svc.submit("HHG-001")                                                                               # the single slot was released
        gate.set()

    def test_job_ids_must_be_well_formed(self):
        svc, _ = make_service(self.rec)
        for bad in ("", "x", "../etc", "G" * 32, "a" * 31, "a" * 33, "HHG-014"):
            with self.assertRaises(L.UnknownCase):
                svc.view(bad)
        with self.assertRaises(L.UnknownCase):
            svc.view("0" * 32)


class ConsistencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = fake_record()

    def result(self, stored):
        svc, calls = make_service(self.rec, stored=stored)
        job, _ = svc.submit("HHG-014")
        return svc.view(job.id)["result"]["consistency"], calls

    def test_identical_hash_reports_a_match_and_the_stored_revision(self):
        c, calls = self.result("same")
        self.assertEqual((c["match"], c["checked"], c["level"], c["message"], c["stored_revision"], c["written_to_graph"]),
                         (True, True, "match", "Live result matches stored validated case.", 3, False))
        self.assertEqual(c["live_sha256"], content_hash(self.rec)[:12])
        self.assertEqual(calls["read"], ["CASE-HHG-014"])                                                    # exactly one READ of the case's own vertex

    def test_a_different_hash_is_a_visible_warning_with_only_safe_diagnostics(self):
        other = stored_vertex(self.rec, verdict="fraud" if self.rec["case"]["verdict"] != "fraud" else "uncertain", evidence_json=canonical({"provenance": {"content_sha256": "0" * 64}}))
        c, _ = self.result(other)
        self.assertEqual((c["match"], c["level"], c["message"]), (False, "differs", "Live result differs from stored case."))
        self.assertIn("verdict", c["differs"])
        self.assertLessEqual(set(c["differs"]), {"verdict", "status", "pattern", "final_actions", "sar_decision", "exposure_usd", "content_hash_only"})
        self.assertNotIn("evidence_json", json.dumps(c))
        c2, _ = self.result(stored_vertex(self.rec, evidence_json=canonical({"provenance": {"content_sha256": "f" * 64}})))
        self.assertEqual(c2["differs"], ["content_hash_only"])

    def test_unreadable_or_missing_stored_case_is_reported_not_raised(self):
        for stored in (None, lambda g: (_ for _ in ()).throw(ToolError(f"network error {SECRET}")), lambda g: (_ for _ in ()).throw(SystemExit("token mint failed")),
                       {"evidence_json": "not json", "final_actions": "[]"}):
            c, _ = self.result(stored)
            self.assertEqual((c["checked"], c["match"], c["level"]), (False, None, "unknown"))
            self.assertNotIn(SECRET, json.dumps(c))

    def test_nothing_is_ever_written_while_comparing(self):
        boom = mock.Mock(side_effect=AssertionError("graph write attempted"))
        with mock.patch("agent.graphio.GraphIO.upsert_case", boom), mock.patch("agent.graphio.GraphIO.delete_case_edges", boom), mock.patch("agent.graphio.GraphIO.delete_test_case", boom), \
                mock.patch("agent.case_writer.CaseWriter.write", boom):
            self.result(stored_vertex(self.rec, evidence_json=canonical({"provenance": {"content_sha256": "0" * 64}})))
        boom.assert_not_called()


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rec = fake_record()

    def app(self, **kw):
        svc, calls = make_service(self.rec, **kw)
        self.calls = calls
        return Http(make_app(graph_probe=lambda gid: {"reachable": False}, live=svc)), svc

    def test_post_returns_a_job_and_get_returns_the_completed_result(self):
        h, _ = self.app()
        s, j = h.call("POST", "/api/live/HHG-014")
        self.assertIn(s, (200, 202))
        self.assertEqual(j["case_id"], "HHG-014")
        s, g = h.call("GET", f"/api/live/{j['job_id']}")
        self.assertEqual((s, g["status"], g["result"]["written_to_graph"], g["result"]["consistency"]["match"]), (200, "completed", False, True))

    def test_unknown_case_ids_are_rejected(self):
        h, _ = self.app()
        for bad in ("HHG-000", "HHG-021", "hhg-014", "..%2Fx", "3514030", "CASE-HHG-014"):
            self.assertEqual(h.call("POST", f"/api/live/{bad}")[0], 404, bad)
        self.assertEqual(self.calls["investigate"], [])

    def test_request_bodies_are_rejected(self):
        h, _ = self.app()
        for body in (b'{"as_of": 1}', b'{"txn_id": "3514030"}', b'{"trigger_type": "risk_score"}', b'{"action": "BLOCK_CARD"}', b"x", b'{"evidence": []}'):
            self.assertEqual(h.call("POST", "/api/live/HHG-014", body=body)[0], 400, body)
        self.assertEqual(self.calls["investigate"], [])

    def test_the_caller_cannot_supply_as_of_txn_trigger_or_anything_else_in_the_query(self):
        h, _ = self.app()
        for q in (b"as_of=1", b"txn_id=3514030", b"trigger_type=analyst_request", b"opened_at=2016-12-31", b"tool=find_shared_devices", b"gsql=x", b"k=1", b"anything=1"):
            self.assertEqual(h.call("POST", "/api/live/HHG-014", query=q)[0], 400, q)
        self.assertEqual(self.calls["investigate"], [])                                                       # nothing ran: the pack row is the only source
        s, j = h.call("POST", "/api/live/HHG-014")
        self.assertEqual(self.calls["investigate"][0]["opened_at"], PACK["HHG-014"]["opened_at"])
        self.assertEqual(h.call("GET", f"/api/live/{j['job_id']}", query=b"as_of=1")[0], 400)

    def test_job_lookup_rejects_unknown_and_malformed_ids_and_other_methods(self):
        h, _ = self.app()
        self.assertEqual(h.call("GET", "/api/live/" + "0" * 32)[0], 404)
        self.assertEqual(h.call("GET", "/api/live/HHG-014")[0], 404)
        for m in ("PUT", "PATCH", "DELETE"):
            self.assertEqual(h.call(m, "/api/live/HHG-014")[0], 405, m)

    def test_busy_returns_429_and_does_not_start_a_second_run(self):
        gate = threading.Event()
        svc = L.LiveService({**PACK, "HHG-014": ROW, "HHG-001": ROW}, lambda row: (gate.wait(10), self.rec)[1], lambda g: None)
        h = Http(make_app(graph_probe=lambda g: {}, live=svc))
        self.assertEqual(h.call("POST", "/api/live/HHG-014")[0], 202)
        s, b = h.call("POST", "/api/live/HHG-001")
        self.assertEqual((s, b["error"]), (429, "busy"))
        gate.set()

    def test_failures_are_safe_over_http(self):
        svc = L.LiveService(PACK, lambda row: (_ for _ in ()).throw(SystemExit(f"token mint failed {SECRET}")), lambda g: None, spawn=lambda fn: fn())
        h = Http(make_app(graph_probe=lambda g: {}, live=svc))
        s, j = h.call("POST", "/api/live/HHG-014")
        s, g = h.call("GET", f"/api/live/{j['job_id']}")
        self.assertEqual((g["status"], g["error"]), ("failed", "Live graph unavailable"))
        self.assertNotIn(SECRET, json.dumps([j, g]))
        self.assertNotIn("Traceback", json.dumps([j, g]))

    def test_no_credentials_or_internals_in_any_live_response(self):
        h, _ = self.app()
        s, j = h.call("POST", "/api/live/HHG-014")
        blob = json.dumps([j, h.call("GET", f"/api/live/{j['job_id']}")[1]])
        env = {}
        f = ROOT / ".env"
        for line in (f.read_text(encoding="utf-8").splitlines() if f.exists() else []):
            if "=" in line and line.split("=", 1)[0] in ("TG_SECRET", "TG_HOST", "OPENAI_API_KEY", "OPENROUTER_API_KEY"):
                env[line.split("=", 1)[0]] = line.split("=", 1)[1].strip().strip("\"'")
        for k, v in env.items():
            if len(v) >= 6:
                self.assertNotIn(v, blob, k)
        self.assertIsNone(re.search(r"sk-|Bearer |TG_SECRET|scratchpad|AppData|[A-Za-z]:\\\\Users|Traceback|gsql", blob))

    def test_replay_mode_is_unchanged_by_live_runs(self):
        h, _ = self.app()
        before = [h.call("GET", p)[1] for p in ("/api/cases", "/api/cases/HHG-014", "/api/overview")]
        h.call("POST", "/api/live/HHG-014")
        after = [h.call("GET", p)[1] for p in ("/api/cases", "/api/cases/HHG-014", "/api/overview")]
        self.assertEqual(before, after)
        self.assertIs(after[1]["case"]["written_to_graph"], True)                                            # the stored case still says written; live results live elsewhere
        self.assertEqual(h.call("GET", "/api/health")[1], {"status": "ok", "cases": 20, "mode": "read-only"})

    def test_route_names_expose_no_query_or_tool_surface(self):
        paths = {r.path for r in make_app(graph_probe=lambda g: {}).routes}
        self.assertFalse([p for p in paths if re.search(r"gsql|query|write|as_of|tool", p)])


@unittest.skipUnless(os.environ.get("TG_HOST") or (ROOT / ".env").exists(), "no TigerGraph credentials configured")
@unittest.skipIf(os.environ.get("HHG_SKIP_LIVE"), "live tests skipped")
class LivePreviewAgainstTigerGraph(unittest.TestCase):
    """The real composition: live TigerGraph reads only. HHG-014's live result must equal the stored, validated FI_Case (same content hash)."""

    def test_hhg014_live_result_matches_the_stored_case_and_writes_nothing(self):
        import time
        svc = L.LiveService(CaseService().pack)
        job, _ = svc.submit("HHG-014")
        t0 = time.time()
        while svc.view(job.id)["status"] in ("queued", "running") and time.time() - t0 < 240:
            time.sleep(1)
        v = svc.view(job.id)
        self.assertEqual(v["status"], "completed", v.get("error"))
        r = v["result"]
        self.assertIs(r["written_to_graph"], False)
        self.assertIs(r["detail"]["case"]["written_to_graph"], False)
        self.assertEqual((r["consistency"]["match"], r["consistency"]["message"]), (True, "Live result matches stored validated case."), r["consistency"])
        stored = json.loads((ROOT / "demo" / "records" / "HHG-014.json").read_text(encoding="utf-8"))
        self.assertEqual(r["consistency"]["live_sha256"], stored["graph_write"]["content_sha256"][:12])
        self.assertEqual((r["summary"]["verdict"], r["consistency"]["stored_revision"]), (stored["case"]["verdict"], stored["graph_write"]["revision"]))


if __name__ == "__main__":
    unittest.main()
