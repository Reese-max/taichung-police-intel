"""Actual compiler/query projection in fictional, isolated reviewed fixtures."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from governed_policy_fixture import ROOT, load_fixture_module, make_governed_policy_fixture


class GovernedFixtureIntegrationTests(unittest.TestCase):
    @staticmethod
    def bind_snapshot(gateway, snapshot):
        artifacts = dict(snapshot["canonical_artifacts"])
        for name in ("status", "brief"):
            artifacts[name] = gateway.qs.canonical_json(snapshot[name]).decode("utf-8")
        snapshot["canonical_artifacts"] = artifacts
        snapshot["store"] = gateway.qs.build_from_canonical_artifacts(artifacts)
        return snapshot

    def fixture(self, **kwargs):
        temporary = tempfile.TemporaryDirectory(prefix="fictional-governed-python-")
        self.addCleanup(temporary.cleanup)
        return make_governed_policy_fixture(temporary.name, **kwargs)

    def test_fictional_review_compiles_exact_policy_without_writing_real_inputs(self):
        inputs = [ROOT / relative for relative in (
            "docs/govintel/source-catalog.v2.json", "docs/govintel/retention-rights-policy.v1.json",
            "docs/govintel/source-policy.approved.json", "intel_v2/freshness_policy.py")]
        inputs.extend((ROOT / "docs/govintel/source-policy-history").glob("*.json"))
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
        f = self.fixture()
        sp = load_fixture_module(f, "fixture_integration_policy", "scripts/source-policy.py")
        self.assertEqual(sp.load_current_policy(), f["policy"])
        self.assertEqual(f["policy"]["schema_version"], 2)
        self.assertEqual(sp.formal_admission(f["policy"])["status"], "ADMITTED")
        self.assertEqual(f["fixture_rights_review"], "FICTIONAL_OFFLINE_ONLY")
        self.assertEqual(before, {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs})

    def test_unknown_rights_fixture_keeps_health_but_has_no_formal_rows(self):
        f = self.fixture(rights_reviewed=False)
        sp = load_fixture_module(f, "fixture_unknown_policy", "scripts/source-policy.py")
        qs = load_fixture_module(f, "fixture_unknown_store", "scripts/query-store.py")
        self.assertEqual(sp.formal_admission(f["policy"])["status"], "RIGHTS_BLOCKED")
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        self.assertEqual(store["items"], [])
        self.assertEqual(len(store["sources"]), len(f["policy"]["active_source_ids"]))

    def test_actual_query_projection_uses_exact_39_field_whitelist(self):
        f = self.fixture()
        qs = load_fixture_module(f, "fixture_reviewed_store", "scripts/query-store.py")
        feed_path = f["paths"]["public"] / "intelligence-feed.json"
        feed = json.loads(feed_path.read_text())
        for row in feed["items"]:
            row.update(committee="FICTIONAL_COMMITTEE", next_milestone="FICTIONAL_MILESTONE",
                       private_notes="FICTIONAL_PRIVATE", full_text="FICTIONAL_BODY")
        feed_path.write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        self.assertEqual(len(store["items"]), 2)
        system = {"record_type", "canonical_id", "source_role", "trust_tier", "verification_status", "canonical_ref"}
        sources = {row["source_id"]: row for row in f["policy"]["active_sources"]}
        for row in store["items"]:
            fields = set(sources[row["source_id"]]["rights_retention_public_policy_refs"]["public_fields"])
            self.assertLessEqual(set(row), fields | system)
            self.assertTrue({"committee", "next_milestone", "private_notes", "full_text"}.isdisjoint(row))
        self.assertNotIn("FICTIONAL_PRIVATE", json.dumps(store))

    def test_unapproved_origin_and_mixed_governance_binding_fail_closed(self):
        for mode in ("origin", "binding"):
            with self.subTest(mode=mode):
                f = self.fixture()
                qs = load_fixture_module(f, "fixture_bad_store", "scripts/query-store.py")
                path = f["paths"]["public"] / "intelligence-feed.json"
                feed = json.loads(path.read_text())
                if mode == "origin":
                    feed["items"][0]["official_url"] = "https://unapproved.example.test/fictional"
                else:
                    feed["source_policy"]["governance_hash"] = "0" * 64
                path.write_text(json.dumps(feed, ensure_ascii=False), encoding="utf-8")
                with self.assertRaises(ValueError):
                    qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)

    def test_changed_copied_rights_matrix_invalidates_previously_compiled_policy(self):
        f = self.fixture()
        sp = load_fixture_module(f, "fixture_changed_policy", "scripts/source-policy.py")
        matrix = json.loads(f["paths"]["rights_matrix"].read_text())
        matrix["classes"]["OFFICIAL_METADATA_LINK"]["public_fields"].append("committee")
        f["paths"]["rights_matrix"].write_text(json.dumps(matrix), encoding="utf-8")
        with self.assertRaises(ValueError):
            sp.load_current_policy()

    def test_factory_refuses_repository_or_existing_nonempty_root(self):
        with self.assertRaisesRegex(ValueError, "separate"):
            make_governed_policy_fixture(ROOT / "tests")
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "unrelated.txt"
            p.write_text("retain")
            with self.assertRaisesRegex(ValueError, "empty"):
                make_governed_policy_fixture(root)
            self.assertEqual(p.read_text(), "retain")

    def test_metadata_permission_does_not_authorize_a_derived_brief(self):
        f = self.fixture()
        gateway = load_fixture_module(f, "metadata_brief_refusal", "scripts/query-gateway.py")
        with self.assertRaises(gateway.GatewayError) as denied:
            gateway.QueryGateway().execute("get_current_brief", {})
        self.assertEqual(denied.exception.code, "RIGHTS_BLOCKED")

    def test_separately_reviewed_brief_keeps_closed_fields_and_source_origins(self):
        f = self.fixture(brief_reviewed=True)
        gateway = load_fixture_module(f, "reviewed_brief_boundary", "scripts/query-gateway.py")
        original = gateway.load_snapshot()
        source = f["policy"]["active_sources"][0]
        row = {"source_id": source["source_id"], "headline": "FICTIONAL_SUMMARY_ONLY",
               "official_url": source["approved_origins"][0] + "/fictional"}
        original["brief"]["priority_items"] = [row]
        self.bind_snapshot(gateway, original)
        self.assertEqual(gateway.QueryGateway(original).execute("get_current_brief", {})["brief"]["priority_items"], [row])
        for mutation, code in (({"source_id": "S-032"}, "RIGHTS_BLOCKED"),
                               ({"official_url": "https://unapproved.example.test/fictional"}, "RIGHTS_BLOCKED"),
                               ({"headline": {"source_id": "hidden"}}, "PUBLIC_PROJECTION_INVALID"),
                               ({"private_case": "PRIVATE_SYNTHETIC_MARKER"}, "PUBLIC_PROJECTION_INVALID")):
            with self.subTest(mutation=mutation):
                candidate = json.loads(json.dumps(original))
                candidate["brief"]["priority_items"][0].update(mutation)
                self.bind_snapshot(gateway, candidate)
                result = gateway.dispatch_mcp(gateway.QueryGateway(candidate), {
                    "jsonrpc": "2.0", "id": 49, "method": "tools/call",
                    "params": {"name": "get_current_brief", "arguments": {}},
                })["result"]
                self.assertTrue(result["isError"])
                self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], code)
                self.assertNotIn("PRIVATE_SYNTHETIC_MARKER", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
