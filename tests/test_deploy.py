"""Deployment adapters: /api/policies needs no large CSV, TigerGraph credentials come from the process environment first, and the deployment
asset case_pack.csv is present. None of this touches the investigation (the frozen tests cover that)."""
import builtins
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
FAKE = {"TG_HOST": "https://tg.example.invalid/", "TG_SECRET": "fake-secret-value", "TG_GRAPHNAME": "FraudInvestigation"}
LARGE = ("transactions.csv", "closed_cases_history.csv", "identity.csv")


class PoliciesWithoutLargeCsv(unittest.TestCase):
    def _policies_recording(self):
        import pandas as pd
        from ui_api import server
        opened = []
        real_open = builtins.open

        def spy_open(f, *a, **k):
            opened.append(str(f))
            return real_open(f, *a, **k)

        def spy_csv(f, *a, **k):
            opened.append(str(f))
            raise AssertionError(f"read_csv must not be called: {f}")
        with mock.patch.object(builtins, "open", spy_open), mock.patch.object(pd, "read_csv", spy_csv):
            out = server._policies()
        return out, opened

    def test_endpoint_data_is_built_without_any_large_csv(self):
        out, opened = self._policies_recording()
        self.assertTrue(out["documents"])
        self.assertFalse([p for p in opened if p.endswith(LARGE)], opened)

    def test_expected_policy_information(self):
        out, _ = self._policies_recording()
        refs = {d["ref"] for d in out["documents"]}
        self.assertTrue({"R1", "R2", "R9", "routes"} <= refs, refs)
        self.assertTrue(any(r.startswith("3") for r in refs))                       # the README policy sections (e.g. 3a)
        self.assertTrue(all(d["text"].strip() for d in out["documents"]))
        self.assertEqual(out["routes"], {"auto": "No approval", "L1": "Team lead", "L2": "Fraud manager"})
        self.assertNotIn("HHG-", " ".join(d["text"] + d["ref"] for d in out["documents"]))       # no benchmark ids

    def test_http_endpoint_works_and_is_read_only(self):
        from starlette.testclient import TestClient
        from ui_api.server import make_app
        c = TestClient(make_app(graph_probe=lambda gid: {"reachable": False}))
        self.assertEqual(c.get("/api/policies").status_code, 200)
        self.assertEqual(c.post("/api/policies").status_code, 405)

    @unittest.skipUnless(all((ROOT / f).exists() for f in LARGE[:2]), "large CSVs not present")
    def test_same_documents_as_the_full_rag_build(self):
        from rag.chunks import build_all
        from ui_api import server
        full = [{"ref": c["source_ref"].replace("policy:", ""), "text": c["text"]} for c in build_all() if c["doc_type"] == "policy"]
        self.assertEqual(server._policies()["documents"], full)                    # the investigation's chunk set is unchanged; the UI reads the static subset


class TigerGraphCredentials(unittest.TestCase):
    def test_qclient_reads_process_environment_without_env_file(self):
        from fraud_tools import qclient
        with tempfile.TemporaryDirectory() as d, mock.patch.object(qclient, "ROOT", Path(d)), mock.patch.dict(os.environ, FAKE):
            self.assertEqual(qclient.QueryClient()._host, "https://tg.example.invalid")

    def test_qclient_environment_takes_precedence_over_env_file(self):
        from fraud_tools import qclient
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".env").write_text("TG_HOST=https://from-file.invalid\nTG_SECRET=file-secret\nTG_GRAPHNAME=FraudInvestigation\n", encoding="utf-8")
            with mock.patch.object(qclient, "ROOT", Path(d)):
                with mock.patch.dict(os.environ, {"TG_HOST": "", "TG_SECRET": ""}):
                    self.assertEqual(qclient.QueryClient()._host, "https://from-file.invalid")          # .env stays the local fallback
                with mock.patch.dict(os.environ, FAKE):
                    self.assertEqual(qclient.QueryClient()._host, "https://tg.example.invalid")         # environment wins

    def test_qclient_missing_credentials_fail_cleanly(self):
        from fraud_tools import qclient
        from fraud_tools.guards import ToolError
        with tempfile.TemporaryDirectory() as d, mock.patch.object(qclient, "ROOT", Path(d)), mock.patch.dict(os.environ, {"TG_HOST": "", "TG_SECRET": ""}):
            with self.assertRaises(ToolError):
                qclient.QueryClient()

    def test_graph_name_guard_still_applies(self):
        from fraud_tools import qclient
        from fraud_tools.guards import ToolError
        with tempfile.TemporaryDirectory() as d, mock.patch.object(qclient, "ROOT", Path(d)), mock.patch.dict(os.environ, {**FAKE, "TG_GRAPHNAME": "Transaction_Fraud"}):
            with self.assertRaises(ToolError):
                qclient.QueryClient()

    def test_ingestion_tg_loader(self):
        sys.path.insert(0, str(ROOT / "src" / "ingestion"))
        with mock.patch.dict(os.environ, FAKE):
            import tg
            with tempfile.TemporaryDirectory() as d, mock.patch.object(tg, "ROOT", Path(d)):
                self.assertEqual(tg._load_env()["TG_HOST"], FAKE["TG_HOST"])
                with mock.patch.dict(os.environ, {"TG_HOST": "", "TG_SECRET": ""}):
                    with self.assertRaises(RuntimeError):
                        tg._load_env()


class GraphUnavailable(unittest.TestCase):
    """The live probe must degrade to 'unreachable' (never raise / hang) when TigerGraph cannot be reached: tg.token() ends a failed token mint with SystemExit."""

    def test_failed_token_mint_is_reported_not_raised(self):
        sys.path.insert(0, str(ROOT / "src" / "ingestion"))
        with mock.patch.dict(os.environ, FAKE):
            import tg
            from ui_api import server
            with mock.patch.object(tg, "_raw", return_value=(None, "network error")):
                tg._token["v"] = ""
                out = server._live_probe("CASE-HHG-014")
        self.assertEqual(out["reachable"], False)
        self.assertNotIn("fake-secret-value", str(out))


class DeploymentAssets(unittest.TestCase):
    def test_case_pack_is_small_and_complete(self):
        p = ROOT / "case_pack.csv"
        self.assertLess(p.stat().st_size, 20_000)
        self.assertEqual(len(p.read_text(encoding="utf-8").strip().splitlines()), 21)          # header + 20 cases

    def test_docker_image_ships_the_calibration_file_and_nothing_else_from_config(self):
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        ignore = [l.strip() for l in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()]
        self.assertIn("COPY config/calibration_v1.json ./config/calibration_v1.json", docker)
        self.assertIn("config/*", ignore)                                              # the rest of config/ stays out of the image
        self.assertIn("!config/calibration_v1.json", ignore)
        self.assertLess(ignore.index("config/*"), ignore.index("!config/calibration_v1.json"))
        for kept in ("COPY cases ./cases", "COPY demo/records ./demo/records", "COPY src ./src"):
            self.assertIn(kept, docker)
        self.assertNotIn(".env", [l for l in docker.splitlines() if l.startswith("COPY")])

    def test_calibration_artifact_is_readable_by_the_calibrator(self):
        from calibration.runtime import Calibrator, DEFAULT_ARTIFACT
        self.assertEqual(DEFAULT_ARTIFACT, ROOT / "config" / "calibration_v1.json")
        self.assertEqual(Calibrator(10 ** 7).version, "b3-v1")

    def test_requirements_pin_the_runtime_packages(self):
        lines = [l.split("==") for l in (ROOT / "requirements.txt").read_text().splitlines() if l and not l.startswith("#")]
        self.assertEqual(sorted(n for n, _ in lines), ["numpy", "pandas", "starlette", "uvicorn"])
        self.assertTrue(all(len(x) == 2 for x in lines))


if __name__ == "__main__":
    unittest.main()
