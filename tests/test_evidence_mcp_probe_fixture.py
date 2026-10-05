"""Actual compiler/runtime controls for the optional #15 diagnostic fixture.

Every permission here is fictional and confined to a separate temporary root.
The checked-in legacy policy remains UNKNOWN and cannot authorize a brief.
"""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from governed_policy_fixture import make_governed_policy_fixture, load_fixture_module

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("evidence_mcp_probe", ROOT / "scripts/probe-evidence-mcp-sdk.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class EvidenceMCPProbeFixtureTests(unittest.TestCase):
    def setUp(self):
        self.owned = tempfile.TemporaryDirectory()
        self.addCleanup(self.owned.cleanup)
        self.before = probe.canonical_input_hashes(ROOT)
        self.receipts = {p: p.read_bytes() for p in (ROOT / "docs/research").glob("issue-15-*.json")}
        self.addCleanup(self.assert_source_unchanged)

    def assert_source_unchanged(self):
        self.assertEqual(probe.canonical_input_hashes(ROOT), self.before)
        self.assertEqual({p: p.read_bytes() for p in self.receipts}, self.receipts)

    def fixture(self, **options):
        return make_governed_policy_fixture(Path(self.owned.name) / "fixture", source_root=ROOT, **options)

    def test_unchanged_current_policy_refuses_formal_brief_and_answer(self):
        g = load_fixture_module({"root": ROOT}, "probe_current_gateway", "scripts/query-gateway.py")
        gateway = g.QueryGateway()
        result = gateway.execute("search_evidence", {"limit": 1})
        self.assertEqual(result["result_count"], 0)
        self.assertEqual(result["query_coverage"]["formal_admission"]["status"], "UNKNOWN")
        self.assertFalse(result["query_coverage"]["can_state_bounded_no_match"])
        for tool, args in (("get_current_brief", {}), ("validate_answer", {"claims": [{"text": "inert", "claim_type": "OTHER"}]})):
            with self.subTest(tool=tool), self.assertRaises(g.GatewayError) as error:
                gateway.execute(tool, args)
            self.assertEqual(error.exception.code, "RIGHTS_BLOCKED")

    def test_explicit_separate_review_uses_real_compiler_ids_origins_and_two_rows(self):
        result = probe.prepare_fictional_fixture(ROOT, Path(self.owned.name) / "fictional")
        fixture = {"root": result["root"]}
        sp = load_fixture_module(fixture, "probe_fixture_policy", "scripts/source-policy.py")
        policy = sp.load_current_policy()
        original = json.loads((ROOT / "docs/govintel/source-policy.approved.json").read_bytes())
        self.assertEqual(policy["schema_version"], 2)
        self.assertEqual(result["scope"], "FICTIONAL_OFFLINE_ONLY")
        self.assertEqual(result["policy_binding"], sp.policy_binding(policy))
        self.assertEqual(policy["active_source_ids"], original["active_source_ids"])
        self.assertEqual(policy["transition"], {"promoted": [], "retired": []})
        catalog = sp.load_catalog()
        sp.validate_catalog_binding(policy, catalog)
        from urllib.parse import urlsplit
        for row in policy["active_sources"]:
            entry = next(r for r in catalog["sources"] if r["source_id"] == row["source_id"])
            url = urlsplit(entry["entrypoint"])
            self.assertEqual(row["approved_origins"], [f"https://{url.netloc}"])
            self.assertFalse(set(row["rights_retention_public_policy_refs"]["public_fields"]) &
                             set(policy["governance_binding"]["prohibited_public_fields"]))
        g = load_fixture_module(fixture, "probe_fixture_gateway", "scripts/query-gateway.py")
        gateway = g.QueryGateway()
        search = gateway.execute("search_evidence", {})
        self.assertEqual(search["result_count"], 2)
        self.assertEqual(search["query_coverage"]["formal_admission"]["status"], "ADMITTED")
        self.assertTrue(all("/fictional-governance/" in r["official_url"] for r in search["results"]))
        self.assertEqual(gateway.execute("get_current_brief", {})["brief"]["schema_version"], 1)

    def test_metadata_permission_alone_does_not_authorize_a_derived_brief(self):
        fixture = self.fixture(rights_reviewed=True, brief_reviewed=False)
        g = load_fixture_module(fixture, "probe_metadata_only_gateway", "scripts/query-gateway.py")
        gateway = g.QueryGateway()
        self.assertEqual(gateway.execute("search_evidence", {})["result_count"], 2)
        with self.assertRaises(g.GatewayError) as error:
            gateway.execute("get_current_brief", {})
        self.assertEqual(error.exception.code, "RIGHTS_BLOCKED")

    def test_resigned_origin_mutation_cannot_override_catalog_binding(self):
        fixture = self.fixture(rights_reviewed=True, brief_reviewed=True)
        sp = load_fixture_module(fixture, "probe_origin_policy", "scripts/source-policy.py")
        altered = copy.deepcopy(fixture["policy"])
        altered["active_sources"][0]["approved_origins"] = ["https://unapproved.example.invalid"]
        altered["policy_hash"] = sp.digest({k: v for k, v in altered.items() if k != "policy_hash"})
        with self.assertRaises(ValueError):
            sp.validate_catalog_binding(altered, sp.load_catalog())

    def test_nested_private_brief_cannot_reach_public_runtime(self):
        fixture = self.fixture(rights_reviewed=True, brief_reviewed=True)
        path = fixture["paths"]["public"] / "v2-daily-brief.json"
        brief = json.loads(path.read_bytes())
        brief["source_health"] = {"status": "PASS", "private_operational_note": probe.MARKER}
        path.write_text(json.dumps(brief), encoding="utf-8")
        g = load_fixture_module(fixture, "probe_private_gateway", "scripts/query-gateway.py")
        with self.assertRaises(g.GatewayError) as error:
            g.QueryGateway().execute("get_current_brief", {})
        self.assertEqual(error.exception.code, "PUBLIC_PROJECTION_INVALID")
        self.assertNotIn(probe.MARKER, str(error.exception))

    def test_policy_binding_mismatch_fails_before_formal_results(self):
        fixture = self.fixture(rights_reviewed=True, brief_reviewed=True)
        path = fixture["paths"]["public"] / "intelligence-feed.json"
        feed = json.loads(path.read_bytes())
        feed["source_policy"]["policy_hash"] = "0" * 64
        path.write_text(json.dumps(feed), encoding="utf-8")
        g = load_fixture_module(fixture, "probe_bad_binding_gateway", "scripts/query-gateway.py")
        with self.assertRaises(ValueError):
            g.QueryGateway()

    def test_positive_cli_requires_explicit_fixture_review(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/probe-evidence-mcp-sdk.py"),
            "--mode", "journeys", "--output", str(Path(self.owned.name) / "receipt.json")],
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn("positive research requires --fixture-policy fictional-reviewed", result.stderr)
        self.assertFalse((Path(self.owned.name) / "receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
