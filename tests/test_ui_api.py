"""Offline tests for the read-only UI API (src/ui_api): the values the interface shows equal the validated backend outputs, nothing is writable,
no credentials or internals leak, and every benchmark case can be opened. No TigerGraph or LLM access (the live probe is injected)."""
import asyncio
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "analysis"))
from ui_api.server import make_app  # noqa: E402
from ui_api.service import CaseNotFound, CaseService  # noqa: E402


def call(app, method, path):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        sent.append(m)
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"", "headers": [], "server": ("t", 80), "scheme": "http",
             "client": ("c", 1), "http_version": "1.1", "root_path": ""}
    asyncio.run(app(scope, receive, send))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


def jget(app, path):
    s, b = call(app, "GET", path)
    return s, (json.loads(b) if b[:1] in (b"{", b"[") else b.decode("utf-8", "replace"))


def probe_ok(gid):
    return {"reachable": True, "found": True, "graph_case_id": gid, "revision": 1, "status": "escalated"}


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svc = CaseService()
        cls.ids = cls.svc.ids()

    def test_all_twenty_cases_open_and_match_their_records(self):
        self.assertEqual(self.ids, [f"HHG-{i:03d}" for i in range(1, 21)])
        for cid in self.ids:
            d = self.svc.get_case(cid)
            self.assertTrue(d["integrity"]["ok"], (cid, d["integrity"]))
            self.assertTrue(d["activity"]["events"] and d["graph"]["nodes"], cid)

    def test_displayed_values_equal_the_submitted_answer_files(self):
        for cid in self.ids:
            ans = json.loads((ROOT / "cases" / f"{cid}.json").read_text(encoding="utf-8"))
            d = self.svc.get_case(cid)
            c = ans["case"]
            for k in ("status", "verdict", "pattern", "affected_txn_ids", "exposure_usd", "similar_prior_cases", "graph_case_id", "written_to_graph", "fraud_probability"):
                self.assertEqual(d["case"][k], c[k], (cid, k))
            for phase in ("initial", "final"):
                self.assertEqual([(a["action"], a["route"]) for a in d["actions"][phase]], [(a["action"], a["route"]) for a in ans["next_best_actions"][phase]], (cid, phase))
            self.assertEqual(d["sar"], ans["sar"], cid)
            self.assertEqual(d["case"]["summary"], c["summary"], cid)

    def test_routes_are_never_altered_or_invented(self):
        labels = {"auto": "Automatic (no approval)", "L1": "Level 1 approval (team lead)", "L2": "Level 2 approval (fraud manager)"}
        for cid in self.ids:
            for a in self.svc.get_case(cid)["actions"]["final"]:
                self.assertEqual(a["route_label"], labels[a["route"]])
                self.assertEqual(a["requires_approval"], a["route"] != "auto")
                self.assertFalse(a["executed"])

    def test_probability_is_never_stated_and_the_reason_is_shown(self):
        for cid in self.ids:
            d = self.svc.get_case(cid)
            self.assertIsNone(d["case"]["fraud_probability"])
            self.assertIn("No probability is stated", d["case"]["probability_note"])

    def test_simulated_evidence_is_always_labelled(self):
        for cid in self.ids:
            d = self.svc.get_case(cid)
            sims = [i for g in d["evidence"] for i in g["items"] if i["simulated"]]
            self.assertTrue(not sims or d["requests"], cid)      # simulated evidence only ever comes from a request (a no-response request adds none)
            self.assertTrue(all(q["simulated"] and q["assumed_response"].startswith("[SIMULATED]") for q in d["requests"]), cid)
            self.assertTrue(all(e["simulated"] for e in d["activity"]["events"] if e["stage"] == "request"), cid)

    def test_evidence_strength_and_hierarchy_come_from_the_record(self):
        d = self.svc.get_case("HHG-014")
        self.assertEqual(d["header"]["evidence_strength"], "strong")
        strong = [i for g in d["evidence"] for i in g["items"] if i["rating"] == "HIGH"]
        self.assertTrue(strong and all(i["strength"] == "Strong" for i in strong))
        self.assertTrue(any("S01" in i["title"] for i in strong))

    def test_hhg014_showcase_flow(self):
        d = self.svc.get_case("HHG-014")
        labels = [e["label"] for e in d["activity"]["events"]]
        for expected in ("Investigation triggered", "Shared-device analysis", "Ring expansion", "Pattern detection", "Uncertainty assessed", "Step-up authentication requested",
                         "Next-best action selected", "Case written to TigerGraph"):
            self.assertIn(expected, labels)
        self.assertLess(labels.index("Shared-device analysis"), labels.index("Ring expansion"))
        self.assertLess(labels.index("Uncertainty assessed"), labels.index("Next-best action selected"))
        self.assertEqual(d["case"]["affected_txn_ids"].__len__(), 26)
        self.assertEqual([a["action"] for a in d["actions"]["final"]], ["ESCALATE_TO_ANALYST"])
        kinds = {n["kind"] for n in d["graph"]["nodes"]}
        self.assertTrue({"case", "customer", "card", "txn", "device", "cluster", "prior"} <= kinds)
        self.assertTrue(any(e.get("ring") for e in d["graph"]["edges"]))

    def test_graph_contains_only_stored_entities(self):
        for cid in self.ids:
            d = self.svc.get_case(cid)
            ans = json.loads((ROOT / "cases" / f"{cid}.json").read_text(encoding="utf-8"))["case"]
            graph_txns = {n["label"] for n in d["graph"]["nodes"] if n["kind"] == "txn"}
            self.assertTrue(set(ans["affected_txn_ids"]) <= graph_txns, cid)
            cards = {n["label"] for n in d["graph"]["nodes"] if n["kind"] == "card"}
            self.assertTrue(set(ans["connected_card_ids"]) <= cards, cid)

    def test_unknown_or_malformed_ids_are_rejected(self):
        for bad in ("HHG-999", "../secrets", "HHG-01", "", "hhg-001"):
            with self.assertRaises(CaseNotFound):
                self.svc.get_case(bad)

    def test_trigger_facts_are_parsed_from_the_case_pack_only(self):
        t = self.svc.get_case("HHG-002")["trigger"]
        self.assertEqual((t["amount_usd"], t["channel"], t["flagged_txn_id"]), (292.36, "Online", "3478782"))
        self.assertIsNone(self.svc.get_case("HHG-014")["trigger"]["amount_usd"])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = make_app(graph_probe=probe_ok)

    def test_health_and_lists(self):
        self.assertEqual(jget(self.app, "/api/health"), (200, {"status": "ok", "cases": 20, "mode": "read-only"}))
        s, rows = jget(self.app, "/api/cases")
        self.assertEqual((s, len(rows)), (200, 20))
        s, o = jget(self.app, "/api/overview")
        self.assertEqual((o["total"], o["written_to_graph"], o["sar_filed"], o["showcase"]), (20, 20, 1, "HHG-014"))
        self.assertEqual(o["verdicts"], {"fraud": 5, "legitimate": 9, "uncertain": 6})

    def test_every_case_endpoint_serves(self):
        for i in range(1, 21):
            s, d = jget(self.app, f"/api/cases/HHG-{i:03d}")
            self.assertEqual(s, 200)
            self.assertTrue(d["integrity"]["ok"])

    def test_missing_case_is_404(self):
        self.assertEqual(call(self.app, "GET", "/api/cases/HHG-404")[0], 404)
        self.assertEqual(call(self.app, "GET", "/api/cases/..%2Fx")[0], 404)

    def test_nothing_is_writable(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            for path in ("/api/cases", "/api/cases/HHG-014", "/api/cases/HHG-014/graph-check", "/api/graph", "/api/system"):
                self.assertEqual(call(self.app, method, path)[0], 405, (method, path))
        paths = {r.path for r in self.app.routes}
        self.assertFalse([p for p in paths if re.search(r"gsql|query|write|run|as_of", p)])

    def test_graph_check_uses_only_the_injected_read_only_probe(self):
        s, d = jget(self.app, "/api/cases/HHG-014/graph-check")
        self.assertEqual((s, d["found"], d["graph_case_id"]), (200, True, "CASE-HHG-014"))

    def test_live_graph_failure_degrades_without_leaking_internals(self):
        def boom(gid):
            return {"reachable": False, "error": "graph unavailable (URLError)"}
        app = make_app(graph_probe=boom)
        s, d = jget(app, "/api/system")
        self.assertEqual((s, d["graph"]["reachable"]), (200, False))
        self.assertEqual(jget(app, "/api/cases/HHG-014")[0], 200)     # stored results still work

    def test_no_credentials_or_internal_names_in_any_response(self):
        env = {}
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
            if "=" in line and line.split("=", 1)[0] in ("TG_SECRET", "TG_HOST", "OPENROUTER_API_KEY", "TG_USER", "TG_PASSWORD"):
                env[line.split("=", 1)[0]] = line.split("=", 1)[1].strip().strip("\"'")
        blob = ""
        for path in ["/api/cases", "/api/overview", "/api/graph", "/api/customers", "/api/system"] + [f"/api/cases/HHG-{i:03d}" for i in range(1, 21)]:
            blob += call(self.app, "GET", path)[1].decode("utf-8")
        for k, v in env.items():
            if len(v) >= 6:
                self.assertNotIn(v, blob, k)
        self.assertIsNone(re.search(r"sk-or-|Bearer |TG_SECRET|scratchpad|AppData|[A-Za-z]:\\\\Users", blob))

    def test_policies_endpoint_serves_the_stored_policy_text(self):
        s, p = jget(self.app, "/api/policies")
        self.assertEqual(s, 200)
        refs = {d["ref"] for d in p["documents"]}
        self.assertTrue({"R1", "R2", "R8", "R9", "routes"} <= refs)


if __name__ == "__main__":
    unittest.main()
