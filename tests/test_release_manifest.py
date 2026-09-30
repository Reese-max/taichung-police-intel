import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release-manifest.py"
spec = importlib.util.spec_from_file_location("release_manifest", SCRIPT)
assert spec and spec.loader


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(SCRIPT.exists(), "release-manifest.py must provide the release contract")
        self.release_manifest = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.release_manifest)
        self.data = {
            "intelligence-feed.json": {
                "schema_version": 1,
                "collection_run_id": "run-1",
                "generated_at": "2026-09-30T00:00:00Z",
                "items": [],
            },
            "source-status.json": {
                "schema_version": 1,
                "generated_at": "2026-09-30T00:00:00Z",
                "latest_collection_run": {"collection_run_id": "run-1", "status": "SUCCEEDED"},
                "sources": [],
            },
            "v2-daily-brief.json": {
                "schema_version": 1,
                "source_collection_run_id": "run-1",
                "source_status_generated_at": "2026-09-30T00:00:00Z",
                "generated_at": "2026-09-30T00:00:00Z",
                "publication_status": "READY",
                "snapshot_complete": True,
            },
            "source-policy.json": {
                "schema_version": 1,
                "policy_version": 1,
                "policy_hash": "a" * 64,
                "catalog_hash": "b" * 64,
                "active_source_ids": ["S-001"],
            },
        }

    def write_data(self, directory):
        for name, value in self.data.items():
            (directory / name).write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")

    def test_build_manifest_contains_cross_surface_binding(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            self.write_data(data_dir)
            manifest = self.release_manifest.build_manifest(
                data_dir,
                code_sha="c" * 40,
                worker_version="query-gateway-v1-workers",
                pages_deployment={"status": "PENDING"},
                built_at="2026-09-30T00:01:00Z",
            )

        self.assertEqual(manifest["publication_generation"], "run-1")
        self.assertEqual(manifest["source_policy_hash"], "a" * 64)
        self.assertRegex(manifest["publication_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["query_generation"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["evidence_catalog_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(manifest["release_id"], r"^[0-9a-f]{64}$")
        self.release_manifest.validate_manifest(manifest)

    def test_binding_rejects_mixed_publication_generation(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            self.write_data(data_dir)
            manifest = self.release_manifest.build_manifest(
                data_dir,
                code_sha="c" * 40,
                worker_version="query-gateway-v1-workers",
                pages_deployment={"status": "PENDING"},
                built_at="2026-09-30T00:01:00Z",
            )

        mismatched = dict(manifest, publication_generation="run-2")
        with self.assertRaisesRegex(ValueError, "release_id"):
            self.release_manifest.validate_binding(mismatched, manifest)

    def test_manifest_hashes_raw_publication_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            self.write_data(data_dir)
            manifest = self.release_manifest.build_manifest(
                data_dir,
                code_sha="c" * 40,
                worker_version="query-gateway-v1-workers",
                pages_deployment={"status": "PENDING"},
                built_at="2026-09-30T00:01:00Z",
            )
            expected = hashlib.sha256((data_dir / "v2-daily-brief.json").read_bytes()).hexdigest()
        self.assertEqual(manifest["publication_hash"], expected)

    def test_release_fixture_survives_git_checkout_line_endings(self):
        manifest = json.loads((ROOT / "apps/web/public/data/release.json").read_text())
        paths = ["apps/web/public/data/" + name for name in self.data]
        for autocrlf in ("true", "false"):
            with self.subTest(autocrlf=autocrlf), tempfile.TemporaryDirectory() as temp:
                subprocess.run(["git", "-c", f"core.autocrlf={autocrlf}", "checkout-index",
                                f"--prefix={Path(temp).as_posix()}/", "--", *paths],
                               cwd=ROOT, check=True, capture_output=True)
                expected = self.release_manifest.build_manifest(
                    Path(temp) / "apps/web/public/data", code_sha=manifest["code_sha"],
                    worker_version=manifest["worker_version"],
                    pages_deployment=manifest["pages_deployment"], built_at=manifest["built_at"],
                )
                self.release_manifest.validate_binding(manifest, expected)

    def test_verify_cli_rejects_changed_artifact_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            data_dir = Path(temp)
            self.write_data(data_dir)
            manifest = self.release_manifest.build_manifest(
                data_dir, code_sha="c" * 40, worker_version="query-gateway-v1-workers",
                pages_deployment={"status": "PENDING"}, built_at="2026-09-30T00:01:00Z",
            )
            path = data_dir / "release.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            command = [sys.executable, "-X", "utf8", str(SCRIPT), "verify", str(path)]
            valid = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(valid.returncode, 0, valid.stderr)
            feed = data_dir / "intelligence-feed.json"
            feed.write_bytes(feed.read_bytes() + b"\n")
            stale = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(stale.returncode, 0, stale.stdout)
            self.assertIn("mismatch", stale.stderr)

    def test_workflows_build_manifest_after_artifact_writes(self):
        pages = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        build = pages.index("scripts/release-manifest.py build")
        with self.subTest(workflow="pages"):
            self.assertIn("node apps/web/scripts/sync-source-policy.mjs", pages[:build])
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        writes = ci.index("cp /tmp/v2-regression-brief.json apps/web/public/data/v2-daily-brief.json")
        tests = ci.index("GOVINTEL_SKIP_DIRTY_FIXTURE_RUNTIME=1", writes)
        with self.subTest(workflow="ci-display-cap"):
            self.assertIn("scripts/release-manifest.py build", ci[writes:tests])

    def test_production_verifier_rejects_tampering_and_mid_smoke_release_changes(self):
        verifier_spec = importlib.util.spec_from_file_location(
            "production_verifier", ROOT / "scripts/verify-query-gateway-production.py")
        verifier = importlib.util.module_from_spec(verifier_spec)
        verifier_spec.loader.exec_module(verifier)
        with tempfile.TemporaryDirectory() as temp:
            self.write_data(Path(temp))
            release = self.release_manifest.build_manifest(
                Path(temp), code_sha="c" * 40, worker_version="query-gateway-v1-workers",
                pages_deployment={"status": "PENDING"}, built_at="2026-09-30T00:01:00Z",
            )
            documents = [(200, self.data[name], (Path(temp) / name).read_bytes())
                         for name in verifier.ARTIFACTS if name != "release.json"]
        binding = {"release_id": release["release_id"], "code_sha": release["code_sha"],
                   "publication_id": release["publication_generation"],
                   "publication_hash": release["publication_hash"]}
        health = {**binding, "status": "ok", "server_version": release["worker_version"],
                  "query_generation": release["query_generation"]}
        capabilities = {"capabilities": sorted(verifier.REQUIRED_CAPABILITIES),
                        "release_id": release["release_id"]}
        receipt = {**binding, "generation_id": release["query_generation"],
                   "artifact_hashes": {key: release["artifact_hashes"][key] for key in ("feed", "status", "brief")}}
        search_binding = {**binding, "query_generation_id": release["query_generation"]}
        search = {**search_binding, "tool_name": "search_evidence", "receipt": search_binding}
        clean = {"release": release, "health": health, "capabilities": capabilities, "search": search}
        cases = [(None, None, None), ("release", "code_sha", "f" * 40),
                 ("release", "evidence_catalog_hash", "0" * 64), ("release", "built_at", None),
                 ("capabilities", "release_id", "f" * 64), ("health", "code_sha", "f" * 40),
                 ("search", "release_id", "f" * 64), ("search", "query_generation_id", "f" * 64),
                 ("search", "publication_hash", "f" * 64), ("search", "receipt", {})]
        for surface, field, value in cases:
            with self.subTest(surface=surface, field=field):
                changed = deepcopy(clean)
                if surface:
                    changed[surface][field] = value
                response = lambda doc: (200, doc, json.dumps(doc).encode())
                client = Mock()
                client.request.side_effect = [response(changed["health"]), response(changed["capabilities"]),
                    *documents, response(changed["release"]), response({"publication_receipt": receipt}),
                    response(changed["search"]), response({"result": {"protocolVersion": "2025-06-18"}}),
                    response({"result": {"tools": [{"name": name} for name in verifier.REQUIRED_CAPABILITIES]}})]
                args = ("https://gateway.example", "https://reese-max.github.io/taichung-police-intel", client)
                if surface:
                    with self.assertRaises((RuntimeError, ValueError)):
                        verifier.verify(*args)
                else:
                    self.assertEqual(verifier.verify(*args)["checks"]["hash_binding"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
