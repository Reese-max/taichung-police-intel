import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("production_query", ROOT / "scripts/verify-query-gateway-production.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PUBLIC = "https://reese-max.github.io/taichung-police-intel"


class PublicationClient:
    def __init__(self):
        self.raw = {name: (ROOT / "apps/web/public/data" / name).read_bytes() for name in module.ARTIFACTS}
        self.hashes = {key: hashlib.sha256(self.raw[name]).hexdigest() for key, name in
                       zip(("feed", "status", "brief"), module.ARTIFACTS)}
        self.feed = json.loads(self.raw["intelligence-feed.json"])
        self.policy = json.loads((ROOT / "apps/web/public/data/source-policy.json").read_bytes())
        self.release = {
            "schema_version": 1, "release_id": "d" * 64, "code_sha": "a" * 40,
            "publication_generation": self.feed["collection_run_id"],
            "publication_hash": self.hashes["brief"], "source_policy_hash": self.policy["policy_hash"],
            "query_generation": "b" * 64, "evidence_catalog_hash": "c" * 64,
            "artifact_hashes": self.hashes, "built_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "worker_version": None, "pages_deployment": None, "deployed_at": None,
            "anonymous_http_verified_at": None, "production_verified": False, "evidence_level": "BUILD_ONLY",
        }
        self.mutate = lambda route, document: None
        self.calls = []

    def request(self, url, *, method="GET", payload=None):
        self.calls.append((url, payload))
        name = url.rsplit("/", 1)[-1]
        bound = {**self.release, "worker_version": "version-62"}
        common = {"release": bound, "query_generation_id": self.release["query_generation"],
                  "publication_id": self.feed["collection_run_id"], "publication_hash": self.hashes["brief"],
                  "policy": self.policy, "receipt": {"query_generation_id": self.release["query_generation"],
                                                     "publication_hash": self.hashes["brief"]}}
        if name in self.raw:
            return 200, json.loads(self.raw[name]), self.raw[name]
        if name == "release.json":
            document = self.release
        elif name == "source-policy.json":
            document = self.policy
        elif name == "health":
            document = {**common, "status": "ok", "server_version": "query-gateway-v1-workers"}
        elif name == "capabilities":
            document = {**common, "capabilities": sorted(module.REQUIRED_CAPABILITIES)}
        elif name == "query" and payload["tool"] == "get_publication_receipt":
            document = {**common, "publication_receipt": {
                "publication_id": self.feed["collection_run_id"], "publication_hash": self.hashes["brief"],
                "artifact_hashes": self.hashes, "generation_id": self.release["query_generation"],
                "policy_hash": self.policy["policy_hash"],
            }}
        elif name == "query":
            row = next(item for item in self.feed["items"] if item.get("official_url", "").startswith("https://"))
            document = {**common, "tool_name": "search_evidence", "results": [{
                "canonical_id": row["stable_id"], "official_url": row["official_url"],
                "canonical_ref": {"artifact_sha256": self.hashes["feed"]},
            }]}
        elif payload["method"] == "initialize":
            document = {"result": {"protocolVersion": "2025-06-18", "release": bound}}
        elif payload["method"] == "tools/list":
            document = {"result": {"tools": [{"name": name} for name in module.REQUIRED_CAPABILITIES], "release": bound}}
        else:
            document = {"result": {"isError": False, "structuredContent": common}}
        document = copy.deepcopy(document)
        route = payload.get("tool", payload.get("method")) if payload else name
        self.mutate(route, document)
        return 200, document, json.dumps(document).encode()


class ReleaseVerificationTests(unittest.TestCase):
    def test_receipt_and_health_hashes_cannot_disagree_with_the_release(self):
        for route in ("health", "get_publication_receipt", "search_evidence", "tools/call"):
            with self.subTest(route=route):
                client = PublicationClient()
                def mutate(actual_route, document):
                    if actual_route == route:
                        target = document.get("result", {}).get("structuredContent", document)
                        if route != "health":
                            target = target["receipt"]
                        target["publication_hash"] = "e" * 64
                client.mutate = mutate
                with self.assertRaisesRegex(RuntimeError, "release"):
                    module.verify("https://gateway.example", PUBLIC, client)

    def test_loopback_or_private_gateway_is_not_a_production_origin(self):
        for origin in ("https://localhost:8788", "https://127.0.0.1", "https://[::1]",
                       "https://10.1.2.3", "https://dev.localhost", "https://gateway.local",
                       "https://0x7f000001", "https://0x7f.0.0.1"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                module.checked_base(origin)

    def test_production_requires_valid_deployment_metadata_and_real_http_client(self):
        client = PublicationClient()
        provenance = {"pages_deployment": "https://github.com/Reese-max/taichung-police-intel/actions/runs/123/attempts/1",
                      "deployed_at": datetime.now(timezone.utc).isoformat(), "expected_code_sha": "a" * 40}
        self.assertFalse(module.verify("https://gateway.example", PUBLIC, client, **provenance)["production_verified"])
        with patch.object(module, "JsonClient", return_value=client):
            self.assertTrue(module.verify("https://gateway.example", PUBLIC, **provenance)["production_verified"])
            for field, value in (("pages_deployment", "not-a-deployment"),
                                 ("deployed_at", (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()),
                                 ("deployed_at", "2000-01-01T00:00:00Z")):
                with self.subTest(field=field, value=value), self.assertRaises(RuntimeError):
                    module.verify("https://gateway.example", PUBLIC, **{**provenance, field: value})

    def test_failed_cli_replaces_a_previous_success_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "receipt.json"
            output.write_text('{"production_verified": true}', encoding="utf-8")
            with patch.object(sys, "argv", ["verify", "--output", str(output)]), \
                    patch.object(module, "verify", side_effect=RuntimeError("mixed release")), \
                    patch.object(module.time, "sleep"):
                self.assertEqual(module.main(), 1)
            receipt = json.loads(output.read_text(encoding="utf-8"))
            self.assertIs(receipt["production_verified"], False)
            self.assertEqual(receipt["status"], "FAILED")

    def test_local_client_receipt_is_not_production_evidence(self):
        client = PublicationClient()
        receipt = module.verify("https://gateway.example", PUBLIC, client)
        self.assertFalse(receipt["production_verified"])
        self.assertEqual(receipt["evidence_level"], "LOCAL_TEST")
        self.assertEqual(receipt["release_id"], client.release["release_id"])
        self.assertEqual(receipt["worker_version"], "version-62")
        self.assertTrue(receipt["official_evidence_url"].startswith("https://"))

    def test_each_worker_surface_must_match_the_pages_release(self):
        for route in ("health", "capabilities", "get_publication_receipt", "search_evidence", "initialize", "tools/list", "tools/call"):
            for field in ("code_sha", "release_id", "publication_generation", "publication_hash",
                          "artifact_hashes", "source_policy_hash", "query_generation", "evidence_catalog_hash"):
                with self.subTest(route=route, field=field):
                    client = PublicationClient()
                    def mutate(actual_route, document):
                        if route == actual_route:
                            target = document.get("result", document)
                            target = target.get("structuredContent", target)
                            if field == "artifact_hashes":
                                # A same-shape tamper (one flipped artifact hash)
                                # must fail closed, not just a type change.
                                target["release"][field] = {**target["release"][field], "feed": "f" * 64}
                            else:
                                target["release"][field] = "mixed-release"
                    client.mutate = mutate
                    with self.assertRaisesRegex(RuntimeError, "release"):
                        module.verify("https://gateway.example", PUBLIC, client)

    def test_public_policy_and_query_receipt_are_checked_independently(self):
        for route, target, field in (("source-policy.json", None, "policy_hash"),
                                     ("get_publication_receipt", "publication_receipt", "generation_id"),
                                     ("get_publication_receipt", "publication_receipt", "policy_hash"),
                                     ("search_evidence", "receipt", "query_generation_id")):
            with self.subTest(route=route, field=field):
                client = PublicationClient()
                def mutate(actual_route, document):
                    if actual_route == route:
                        (document[target] if target else document)[field] = "mixed-release"
                client.mutate = mutate
                with self.assertRaises(RuntimeError):
                    module.verify("https://gateway.example", PUBLIC, client)

    def test_pages_manifest_code_sha_must_match_the_workflow_commit(self):
        client = PublicationClient()
        with self.assertRaisesRegex(RuntimeError, "code SHA"):
            module.verify("https://gateway.example", PUBLIC, client, expected_code_sha="f" * 40)

    def test_search_must_return_an_official_locator_from_the_bound_feed(self):
        client = PublicationClient()
        def mutate(route, document):
            if route == "search_evidence":
                document["results"][0]["official_url"] = "https://example.com/fabricated"
        client.mutate = mutate
        with self.assertRaisesRegex(RuntimeError, "evidence"):
            module.verify("https://gateway.example", PUBLIC, client)

    def test_missing_manifest_is_not_a_successful_production_smoke(self):
        client = PublicationClient()
        def mutate(route, document):
            if route == "release.json":
                document.clear()
        client.mutate = mutate
        with self.assertRaisesRegex(RuntimeError, "release"):
            module.verify("https://gateway.example", PUBLIC, client)

    def test_workflows_generate_manifest_after_build_and_stamp_worker_code(self):
        pages = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        worker = (ROOT / ".github/workflows/query-gateway-workers.yml").read_text(encoding="utf-8")
        self.assertIn("build-release-manifest.mjs", pages)
        self.assertIn("--data-dir apps/web/out/data", pages)
        self.assertLess(pages.index("npm run check"), pages.index("build-release-manifest.mjs"))
        self.assertLess(pages.index("build-release-manifest.mjs"), pages.index("id: pages_upload"))
        self.assertIn("--tag ${{ github.sha }}", worker)
        self.assertNotIn("    paths:", worker)
        self.assertNotIn("steps.scope", worker)
        self.assertNotIn("required == 'false'", worker)
        self.assertIn("PAGES_DEPLOYMENT:", pages)
        config = json.loads((ROOT / "workers/query-gateway/wrangler.jsonc").read_text())
        self.assertEqual(config["version_metadata"]["binding"], "CF_VERSION_METADATA")

    def test_worker_activation_waits_for_the_serialized_pages_build(self):
        pages = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        worker = (ROOT / ".github/workflows/query-gateway-workers.yml").read_text(encoding="utf-8")
        self.assertIn("  workflow_call:", worker)
        self.assertNotIn("  push:", worker)
        self.assertNotIn("  workflow_dispatch:", worker)
        self.assertIn("  worker:\n    needs: build\n    uses: ./.github/workflows/query-gateway-workers.yml\n    secrets: inherit", pages)
        self.assertIn("    needs: [build, worker]", pages)
        self.assertIn("    needs: [build, worker, deploy]", pages)
        self.assertIn("WORKER_DEPLOY: ${{ needs.worker.result }}", pages)
        self.assertIn("group: competition-demo-pages\n  cancel-in-progress: false", pages)
        self.assertNotIn("group: competition-demo-pages", worker)


if __name__ == "__main__":
    unittest.main()
