import copy
from datetime import datetime, timedelta, timezone
import hashlib
from io import BytesIO
import importlib.util
import json
import os
import re
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from unittest.mock import patch, Mock
from governed_policy_fixture import make_governed_policy_fixture, load_fixture_module

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("production_query", Path(os.environ.get("TAI398_VERIFIER_SOURCE", ROOT / "scripts/verify-query-gateway-production.py")))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PUBLIC = "https://reese-max.github.io/taichung-police-intel"


def shadow_json_key(text, key, canary):
    """Insert a discarded first value without assuming the lawful value or spacing."""
    return re.subn(
        r'("' + re.escape(key) + r'"\s*:\s*)',
        lambda match: json.dumps(key) + ": " + json.dumps(canary) + ", " + match.group(0),
        text, count=1,
    )


class PublicationClient:
    fixture_directory = None
    fixture = None

    def __init__(self, *, blocked=False):
        self.blocked = blocked
        if not blocked:
            if PublicationClient.fixture is None:
                PublicationClient.fixture_directory = tempfile.TemporaryDirectory()
                PublicationClient.fixture = make_governed_policy_fixture(Path(PublicationClient.fixture_directory.name), source_root=ROOT)
            root = PublicationClient.fixture["root"]
        else:
            root = ROOT
        self.raw = {name: (root / "apps/web/public/data" / name).read_bytes() for name in module.ARTIFACTS}
        self.hashes = {key: hashlib.sha256(self.raw[name]).hexdigest() for key, name in
                       zip(("feed", "status", "brief"), module.ARTIFACTS)}
        self.feed = json.loads(self.raw["intelligence-feed.json"])
        self.status = json.loads(self.raw["source-status.json"])
        self.policy = json.loads((root / "apps/web/public/data/source-policy.json").read_bytes())
        if blocked:
            ids = self.policy["active_source_ids"]
            self.admission = {"status": "UNKNOWN", "reason": "APPROVED_GOVERNANCE_MISSING", "governance_hash": None,
                "blocked_sources": ids, "per_source": {sid: "APPROVED_GOVERNANCE_MISSING" for sid in ids}}
        else:
            self.admission = {"status": "ADMITTED", "reason": None, "governance_hash": self.policy["governance_binding"]["governance_hash"], "blocked_sources": [], "per_source": {}}
        self.binding = {key: self.policy[key] for key in ("policy_version", "policy_hash", "catalog_hash", "active_source_ids")}
        if not blocked:
            self.binding["governance_hash"] = self.policy["governance_binding"]["governance_hash"]
            self.binding["retention_policy_hash"] = self.policy["governance_binding"]["retention_policy"]["policy_hash"]
        ids = self.policy["active_source_ids"]
        self.store_module = load_fixture_module({"root": root}, "release_fixture_store", "scripts/query-store.py")
        self.approved = json.loads((root / "docs/govintel/source-policy.approved.json").read_text())
        self.store = self.store_module.build_store(self.feed, self.status, json.loads(self.raw["v2-daily-brief.json"]), self.hashes, policy=self.approved)
        self.coverage = self.store_module._query_coverage(self.store, "publication_metadata", policy=self.approved)
        self.coverage.setdefault("collection_coverage_status", self.coverage["status"])
        self.health_coverage = self.store_module._query_coverage(self.store, "source_health", policy=self.approved)
        self.release = {
            "schema_version": 1, "release_id": "d" * 64, "code_sha": "a" * 40,
            "publication_generation": self.feed["collection_run_id"],
            "publication_hash": self.hashes["brief"], "source_policy_hash": self.policy["policy_hash"],
            "query_generation": self.store["generation_id"], "evidence_catalog_hash": "c" * 64,
            "artifact_hashes": self.hashes, "built_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
            "worker_version": None, "pages_deployment": None, "deployed_at": None,
            "anonymous_http_verified_at": None, "production_verified": False, "evidence_level": "BUILD_ONLY",
        }
        self.mutate = lambda route, document: None
        self.calls = []

    def query(self, tool, common):
        if tool == "get_publication_receipt":
            return {**common, "publication_receipt": {
                "publication_id": self.feed["collection_run_id"], "publication_hash": self.hashes["brief"],
                "artifact_hashes": self.hashes, "generation_id": self.release["query_generation"],
                "policy_hash": self.policy["policy_hash"], "source_gaps": common["source_gaps"],
                "collection_status": self.store["generated_from"]["collection_status"], "publication_status": self.store["generated_from"]["publication_status"],
                "snapshot_complete": self.store["generated_from"]["snapshot_complete"], "freshness": common["freshness"],
                "current_as_of_server_clock": common["freshness"] == "RECENT",
            }}
        if tool in ("get_current_brief", "validate_answer"):
            return {"schema_version": 1, "error": {"code": "RIGHTS_BLOCKED", "message": "Fictional typed refusal"}}
        if tool == "get_source_health":
            return {**common, "sources": self.status["sources"], "source_rights": [
                {"source_id": sid, "rights_status": "UNKNOWN", "review_required": True} for sid in self.policy["active_source_ids"]],
                "formal_admission": self.admission, "query_coverage": self.health_coverage,
                "receipt": {**common["receipt"], "result_count": len(self.policy["active_source_ids"])}}
        row = next(item for item in self.feed["items"] if item.get("official_url", "").startswith("https://"))
        results = [] if self.blocked else [{"canonical_id": row["stable_id"], "official_url": row["official_url"],
            "canonical_ref": {"artifact_sha256": self.hashes["feed"]}}]
        return {**common, "tool_name": "search_evidence", "results": results, "result_count": len(results),
            "total_matches": len(results), "answerable_no_match": False, "has_more": False, "next_cursor": None,
            "offset": 0, "truncated": False, "event_ids": [], "evidence_ids": [],
            "receipt": {**common["receipt"], "result_count": len(results), "truncated": False}}

    def request(self, url, *, method="GET", payload=None, expected_status=200):
        self.calls.append((url, payload))
        name = url.rsplit("/", 1)[-1]
        bound = {**self.release, "worker_version": "version-62"}
        queried_at = datetime.now(timezone.utc)
        scope, gaps = self.store_module.assess_scope(self.store, None, queried_at)
        common = {"release": bound, "queried_at": queried_at.isoformat(), "freshness": "RECENT" if scope == "SNAPSHOT_RECENT" else scope, "query_generation_id": self.release["query_generation"],
                  "publication_id": self.feed["collection_run_id"], "publication_hash": self.hashes["brief"],
                  "policy": self.binding, "query_coverage": self.coverage, "source_gaps": gaps,
                  "receipt": {"query_generation_id": self.release["query_generation"], "publication_hash": self.hashes["brief"], "issued_at": queried_at.isoformat()}}
        status = 200
        if name in self.raw:
            return 200, json.loads(self.raw[name]), self.raw[name]
        if name == "release.json":
            document = self.release
        elif name == "source-policy.json":
            document = self.policy
        elif name == "health":
            document = {**common, "status": "ok", "publication_freshness": common["freshness"], "server_version": "query-gateway-v1-workers"}
        elif name == "capabilities":
            document = {**common, "capabilities": sorted(module.REQUIRED_CAPABILITIES)}
        elif name == "query":
            document = self.query(payload["tool"], common)
            if payload["tool"] in ("get_current_brief", "validate_answer"):
                status = 503
        elif payload["method"] == "initialize":
            document = {"result": {"protocolVersion": "2025-06-18", "release": bound}}
        elif payload["method"] == "tools/list":
            document = {"result": {"tools": [{"name": name} for name in module.REQUIRED_CAPABILITIES], "release": bound}}
        else:
            content = self.query(payload["params"]["name"], common)
            error = payload["params"]["name"] in ("get_current_brief", "validate_answer")
            document = {"result": {"isError": error, "content": [{"type": "text", "text": json.dumps(content)}], **({} if error else {"structuredContent": content})}}
        document = copy.deepcopy(document)
        route = payload.get("tool", payload.get("method")) if payload else name
        if route == "tools/call" and payload["params"]["name"] != "get_publication_receipt":
            route = "mcp_" + payload["params"]["name"]
        self.mutate(route, document)
        return status, document, json.dumps(document).encode()


# Exercise the positive verifier from its own compiler-produced, fictional
# approved repository. The native current repository remains legacy policy 1.
PublicationClient()
native_module = module
fixture_verifier_path = PublicationClient.fixture["root"] / "scripts/verify-query-gateway-production.py"
shutil.copyfile(Path(os.environ.get("TAI398_VERIFIER_SOURCE", ROOT / "scripts/verify-query-gateway-production.py")), fixture_verifier_path)
spec = importlib.util.spec_from_file_location("fictional_positive_release_verifier", fixture_verifier_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


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

    def test_legacy_rights_refusal_verifies_only_deployment_binding(self):
        module = native_module
        client = PublicationClient(blocked=True)
        provenance = {"pages_deployment": "https://github.com/Reese-max/taichung-police-intel/actions/runs/123/attempts/1",
            "deployed_at": datetime.now(timezone.utc).isoformat(), "expected_code_sha": client.release["code_sha"]}
        with patch.object(module, "JsonClient", return_value=client):
            receipt = module.verify("https://gateway.example", PUBLIC, **provenance)
        self.assertIs(receipt["deployment_verified"], True)
        self.assertIs(receipt["production_verified"], False)
        self.assertEqual(receipt["query_readiness"], "RIGHTS_BLOCKED")
        self.assertEqual(receipt["evidence_level"], "DEPLOYMENT_BOUND_RIGHTS_BLOCKED")
        self.assertIsNone(receipt["official_evidence_id"])
        self.assertIsNone(receipt["official_evidence_url"])
        self.assertEqual(receipt["formal_admission"], client.admission)
        self.assertEqual(receipt["checks"]["formal_query_admission"], "UNKNOWN")
        self.assertEqual(receipt["checks"]["protected_query_refusal"], "SUCCESS")

    def test_blocked_policy_cannot_promote_an_injected_official_locator(self):
        module = native_module
        client = PublicationClient(blocked=True)
        def mutate(route, document):
            if route == "search_evidence":
                row = next(row for row in client.feed["items"] if row.get("official_url", "").startswith("https://"))
                document["results"] = [{"canonical_id": row["stable_id"], "official_url": row["official_url"],
                    "canonical_ref": {"artifact_sha256": client.hashes["feed"]}}]
                document["result_count"] = document["total_matches"] = document["receipt"]["result_count"] = 1
        client.mutate = mutate
        with self.assertRaisesRegex(RuntimeError, "Rights-blocked"):
            module.verify("https://gateway.example", PUBLIC, client)

    def test_bare_empty_and_contradictory_blocked_counts_are_rejected(self):
        module = native_module
        mutations = {"untyped": lambda d: d.pop("query_coverage"),
            "total": lambda d: d.update(total_matches=1), "count": lambda d: d.update(result_count=1),
            "boolean_count": lambda d: d.update(result_count=False), "receipt_boolean": lambda d: d["receipt"].update(result_count=False),
            "receipt_count": lambda d: d["receipt"].update(result_count=1), "bounded": lambda d: d.update(answerable_no_match=True),
            "coverage_bounded": lambda d: d["query_coverage"].update(can_state_bounded_no_match=True),
            "cursor": lambda d: d.update(next_cursor="invented"), "missing_cursor": lambda d: d.pop("next_cursor"),
            "has_more": lambda d: d.update(has_more=True), "truncated": lambda d: d.update(truncated=True),
            "evidence": lambda d: d.update(evidence_ids=["invented"]), "results_type": lambda d: d.update(results={})}
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                client = PublicationClient(blocked=True)
                client.mutate = lambda route, d: mutate(d) if route == "search_evidence" else None
                with self.assertRaises(RuntimeError):
                    module.verify("https://gateway.example", PUBLIC, client)

    def test_all_blocked_release_surfaces_must_preserve_exact_admission(self):
        module = native_module
        for route in ("health", "get_publication_receipt", "search_evidence", "tools/call", "mcp_search_evidence"):
            for kind in ("admitted", "wrong_reason", "missing_blocker", "unknown_blocker", "forged_source_reason", "extra_required"):
                with self.subTest(route=route, kind=kind):
                    client = PublicationClient(blocked=True)
                    def mutate(actual, d):
                        if actual != route:
                            return
                        d = d.get("result", {}).get("structuredContent", d)
                        c = d["query_coverage"]
                        a = c["formal_admission"]
                        if kind == "admitted": a["status"] = "ADMITTED"
                        elif kind == "wrong_reason": a["reason"] = "OTHER"
                        elif kind == "missing_blocker": a["blocked_sources"].pop()
                        elif kind == "unknown_blocker": a["blocked_sources"].append("S-999")
                        elif kind == "forged_source_reason": a["per_source"]["S-004"] = "REVIEWED"
                        else: c["required_sources"].append("S-999")
                    client.mutate = mutate
                    with self.assertRaises(RuntimeError):
                        module.verify("https://gateway.example", PUBLIC, client)

    def test_health_diagnostics_and_source_identity_cannot_be_discarded(self):
        module = native_module
        for route in ("get_source_health", "mcp_get_source_health"):
            for kind in ("missing", "unknown", "rights", "source_scalar", "gap", "formal"):
                with self.subTest(route=route, kind=kind):
                    client = PublicationClient(blocked=True)
                    def mutate(actual, d):
                        if actual != route: return
                        d = d.get("result", {}).get("structuredContent", d)
                        if kind == "missing": d["sources"].pop()
                        elif kind == "unknown": d["sources"][0]["source_id"] = "S-999"
                        elif kind == "rights": d["source_rights"][0]["rights_status"] = "OPEN_DATA_LICENSED"
                        elif kind == "source_scalar": d["sources"][0]["source_health"] = {"body": "PRIVATE"}
                        elif kind == "gap": d["query_coverage"]["missing_required_sources"] = ["S-004"]
                        else: d["formal_admission"]["status"] = "ADMITTED"
                    client.mutate = mutate
                    with self.assertRaises(RuntimeError): module.verify("https://gateway.example", PUBLIC, client)

    def test_protected_queries_require_only_the_exact_typed_refusal(self):
        module = native_module
        for route in ("get_current_brief", "validate_answer", "mcp_get_current_brief", "mcp_validate_answer"):
            for kind in ("wrong_code", "leaked_data", "success"):
                with self.subTest(route=route, kind=kind):
                    client = PublicationClient(blocked=True)
                    def mutate(actual, d):
                        if actual != route: return
                        if actual.startswith("mcp_"):
                            if kind == "success": d["result"]["isError"] = False; return
                            typed = json.loads(d["result"]["content"][0]["text"])
                        else: typed = d
                        if kind == "wrong_code": typed["error"]["code"] = "UPSTREAM_UNAVAILABLE"
                        else: typed["brief"] = {"body": "PRIVATE"}
                        if actual.startswith("mcp_"): d["result"]["content"][0]["text"] = json.dumps(typed)
                    client.mutate = mutate
                    with self.assertRaises(RuntimeError): module.verify("https://gateway.example", PUBLIC, client)

    def test_injected_client_never_sets_deployment_verified(self):
        module = native_module
        receipt = module.verify("https://gateway.example", PUBLIC, PublicationClient(blocked=True),
            pages_deployment="https://github.com/Reese-max/taichung-police-intel/actions/runs/123/attempts/1",
            deployed_at=datetime.now(timezone.utc).isoformat(), expected_code_sha="a" * 40)
        self.assertIs(receipt["deployment_verified"], False)
        self.assertIs(receipt["production_verified"], False)
        self.assertEqual(receipt["evidence_level"], "LOCAL_TEST")

    def test_cli_operational_success_preserves_blocked_readiness_outputs(self):
        module = native_module
        client = PublicationClient(blocked=True)
        provenance = {"pages_deployment": "https://github.com/Reese-max/taichung-police-intel/actions/runs/123/attempts/1",
            "deployed_at": datetime.now(timezone.utc).isoformat(), "expected_code_sha": "a" * 40}
        with patch.object(module, "JsonClient", return_value=client):
            receipt = module.verify("https://gateway.example", PUBLIC, **provenance)
        with tempfile.TemporaryDirectory() as directory:
            output, github_output = Path(directory)/"receipt.json", Path(directory)/"github-output"
            with patch.object(sys, "argv", ["verify", "--output", str(output)]), patch.object(module, "verify", return_value=receipt), \
                    patch.dict(os.environ, {"GITHUB_OUTPUT": str(github_output)}), patch("builtins.print") as printed:
                self.assertEqual(module.main(), 0)
            saved = json.loads(output.read_text())
            self.assertIs(saved["deployment_verified"], True)
            self.assertIs(saved["production_verified"], False)
            self.assertEqual(github_output.read_text(), "query_readiness=RIGHTS_BLOCKED\nproduction_verified=false\n")
            self.assertTrue(printed.call_args[0][0].startswith("DEPLOYMENT_QUERY_RECEIPT_OK "))
            self.assertNotIn("PRODUCTION_QUERY_RECEIPT_OK", printed.call_args[0][0])

    def test_self_signed_governed_public_policy_cannot_replace_current_approval(self):
        # All fake remote hashes/releases agree, but the real local approval is 1.
        with self.assertRaisesRegex(RuntimeError, "approved repository"):
            native_module.verify("https://gateway.example", PUBLIC, PublicationClient())

    def test_json_client_accepts_only_the_declared_bounded_status(self):
        client = module.JsonClient()
        for actual, expected in ((503, 200), (429, 503), (500, 503)):
            with self.subTest(actual=actual, expected=expected):
                client.opener = Mock()
                client.opener.open.side_effect = HTTPError("https://gateway.example/query", actual, "error", {}, BytesIO(b'{}'))
                with self.assertRaisesRegex(RuntimeError, "HTTP"):
                    client.request("https://gateway.example/query", expected_status=expected)
        client.opener = Mock()
        raw = b'{"schema_version":1,"error":{"code":"RIGHTS_BLOCKED","message":"Denied"}}'
        client.opener.open.side_effect = HTTPError("https://gateway.example/query", 503, "error", {}, BytesIO(raw))
        status, document, returned = client.request("https://gateway.example/query", expected_status=503)
        self.assertEqual(status, 503)
        self.assertEqual(returned, raw)
        self.assertEqual(document["error"]["code"], "RIGHTS_BLOCKED")
        for raw in (b'[]', b'not JSON', b'x' * (module.MAX_BYTES + 1)):
            client.opener.open.side_effect = HTTPError("https://gateway.example/query", 503, "error", {}, BytesIO(raw))
            with self.assertRaises(RuntimeError): client.request("https://gateway.example/query", expected_status=503)

    def test_admitted_locator_must_still_belong_to_an_approved_source_origin(self):
        for kind in ("source", "origin"):
            with self.subTest(kind=kind):
                client = PublicationClient()
                if kind == "source": client.feed["items"][0]["source_id"] = "S-999"
                else: client.feed["items"][0]["official_url"] = "https://unapproved.gov.tw/fake"
                client.raw["intelligence-feed.json"] = json.dumps(client.feed).encode()
                client.hashes["feed"] = hashlib.sha256(client.raw["intelligence-feed.json"]).hexdigest()
                with self.assertRaisesRegex(RuntimeError, "evidence"):
                    module.verify("https://gateway.example", PUBLIC, client)

    def test_bound_canonical_stale_diagnostics_cannot_be_erased_consistently(self):
        client = PublicationClient(blocked=True)
        self.assertTrue(client.coverage["stale_required_sources"])
        def mutate(route, document):
            d = document.get("result", {}).get("structuredContent", document)
            if "query_coverage" in d:
                c = d["query_coverage"]
                c.update(missing_required_sources=[], stale_required_sources=[], collection_covered_sources=client.policy["active_source_ids"])
                if c["capability_id"] == "publication_metadata": c["collection_coverage_status"] = "COVERED_BOUNDED_SCOPE"
                else: c["status"] = "COVERED_BOUNDED_SCOPE"
            if "source_gaps" in d: d["source_gaps"] = []
            if "publication_receipt" in d: d["publication_receipt"]["source_gaps"] = []
        client.mutate = mutate
        with self.assertRaisesRegex(RuntimeError, "canonical"):
            native_module.verify("https://gateway.example", PUBLIC, client)

    def test_clock_retreat_cannot_suppress_canonical_snapshot_age(self):
        client = PublicationClient(blocked=True)
        def mutate(route, document):
            d = document.get("result", {}).get("structuredContent", document)
            if route == "search_evidence":
                d["queried_at"] = d["receipt"]["issued_at"] = "2000-01-01T00:00:00Z"
        client.mutate = mutate
        with self.assertRaisesRegex(RuntimeError, "clock"):
            native_module.verify("https://gateway.example", PUBLIC, client)

    def test_mcp_text_cannot_contradict_its_structured_receipt(self):
        client = PublicationClient(blocked=True)
        def mutate(route, document):
            if route == "mcp_search_evidence":
                text = json.loads(document["result"]["content"][0]["text"])
                text["results"] = [{"canonical_id": "FICTIONAL_PRIVATE_CANARY"}]
                text["result_count"] = text["total_matches"] = text["receipt"]["result_count"] = 1
                document["result"]["content"][0]["text"] = json.dumps(text)
        client.mutate = mutate
        with self.assertRaisesRegex(RuntimeError, "MCP"):
            native_module.verify("https://gateway.example", PUBLIC, client)

    def test_mcp_success_requires_single_finite_object_text(self):
        for kind in ("missing", "extra", "scalar", "malformed", "nonfinite", "typed_mismatch"):
            with self.subTest(kind=kind):
                client = PublicationClient(blocked=True)
                def mutate(route, document):
                    if route != "tools/call": return
                    result = document["result"]
                    if kind == "missing": result.pop("content")
                    elif kind == "extra": result["content"].append({"type": "text", "text": "PRIVATE"})
                    elif kind == "scalar": result["content"][0]["text"] = "0"
                    elif kind == "malformed": result["content"][0]["text"] = "{"
                    elif kind == "nonfinite":
                        result["structuredContent"]["invalid"] = float("nan")
                        result["content"][0]["text"] = json.dumps(result["structuredContent"])
                    else:
                        text = json.loads(result["content"][0]["text"])
                        text["policy"]["policy_version"] = True
                        result["content"][0]["text"] = json.dumps(text)
                client.mutate = mutate
                with self.assertRaises(RuntimeError): native_module.verify("https://gateway.example", PUBLIC, client)

    def test_canonical_incomplete_source_cannot_disappear_from_all_diagnostics(self):
        client = PublicationClient(blocked=True)
        row = next(row for row in client.status["sources"] if row["source_id"] == "S-006")
        row.update(source_health="FAILED", window_completeness="PARTIAL", result="PARTIAL")
        client.raw["source-status.json"] = json.dumps(client.status).encode()
        client.hashes["status"] = hashlib.sha256(client.raw["source-status.json"]).hexdigest()
        client.store = client.store_module.build_store(client.feed, client.status, json.loads(client.raw["v2-daily-brief.json"]), client.hashes, policy=client.approved)
        client.coverage = client.store_module._query_coverage(client.store, "publication_metadata", policy=client.approved)
        client.health_coverage = client.store_module._query_coverage(client.store, "source_health", policy=client.approved)
        client.release["query_generation"] = client.store["generation_id"]
        self.assertIn("S-006", client.coverage["missing_required_sources"])
        def mutate(route, document):
            d = document.get("result", {}).get("structuredContent", document)
            if "query_coverage" in d:
                c = d["query_coverage"]
                c["missing_required_sources"] = []
                if c["capability_id"] == "publication_metadata": c["collection_coverage_status"] = "STALE"
                else: c["status"] = "STALE"
            if "source_gaps" in d: d["source_gaps"] = [gap for gap in d["source_gaps"] if gap.get("reason") != "SOURCE_INCOMPLETE"]
            if "publication_receipt" in d: d["publication_receipt"]["source_gaps"] = d["source_gaps"]
            if "result" in document and "structuredContent" in document["result"]:
                document["result"]["content"][0]["text"] = json.dumps(document["result"]["structuredContent"])
        client.mutate = mutate
        with self.assertRaisesRegex(RuntimeError, "canonical"):
            native_module.verify("https://gateway.example", PUBLIC, client)

    def test_mcp_duplicate_keys_cannot_hide_extra_evidence_or_diagnostics(self):
        for kind in ("evidence", "nested_diagnostic"):
            with self.subTest(kind=kind):
                client = PublicationClient(blocked=True)
                def mutate(route, document):
                    if route != "mcp_search_evidence": return
                    text = document["result"]["content"][0]["text"]
                    key = "results" if kind == "evidence" else "missing_required_sources"
                    canary = [{"canonical_id": "FICTIONAL_PRIVATE_CANARY"}] if kind == "evidence" else ["S-999"]
                    changed, replacements = shadow_json_key(text, key, canary)
                    self.assertEqual(replacements, 1, "The duplicate-key negative input must actually be injected")
                    self.assertNotEqual(changed, text)
                    self.assertEqual(json.loads(changed), document["result"]["structuredContent"])
                    document["result"]["content"][0]["text"] = changed
                client.mutate = mutate
                with self.assertRaisesRegex(RuntimeError, "MCP"):
                    native_module.verify("https://gateway.example", PUBLIC, client)

    def test_duplicate_diagnostic_injection_handles_all_list_values_and_json_spacing(self):
        for missing in ([], ["S-006"], ["S-006", "S-009", "S-029"]):
            for formatting in ({}, {"separators": (",", ":")}, {"indent": 2}):
                with self.subTest(missing=missing, formatting=formatting):
                    structured = {"results": [], "query_coverage": {"missing_required_sources": missing}}
                    text = json.dumps(structured, **formatting)
                    document = {"result": {"isError": False, "structuredContent": structured,
                        "content": [{"type": "text", "text": text}]}}
                    self.assertEqual(native_module.mcp_content(document), structured)
                    changed, replacements = shadow_json_key(text, "missing_required_sources", ["S-999"])
                    self.assertEqual(replacements, 1)
                    self.assertNotEqual(changed, text)
                    self.assertEqual(json.loads(changed), structured)
                    document["result"]["content"][0]["text"] = changed
                    with self.assertRaisesRegex(RuntimeError, "MCP"):
                        native_module.mcp_content(document)

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
