import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
