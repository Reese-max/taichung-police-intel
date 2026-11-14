import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from governed_policy_fixture import make_legacy_policy_fixture, load_fixture_module

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


sp = load("governance_test_policy", "scripts/source-policy.py")
qs = load("governance_test_store", "scripts/query-store.py")


class SourceGovernanceTests(unittest.TestCase):
    def test_legacy_formal_projection_excludes_unadmitted_sources(self):
        directory = tempfile.TemporaryDirectory(prefix="legacy-source-governance-")
        self.addCleanup(directory.cleanup)
        fixture = make_legacy_policy_fixture(Path(directory.name) / "legacy", source_root=ROOT)
        self.addCleanup(fixture["cleanup"])
        legacy_store = load_fixture_module(fixture, "legacy_governance_store", "scripts/query-store.py")
        store = legacy_store.build_from_paths(
            legacy_store.DEFAULT_FEED, legacy_store.DEFAULT_STATUS, legacy_store.DEFAULT_BRIEF,
        )
        self.assertEqual(store["items"], [])
        self.assertEqual(len(store["sources"]), 5)
        self.assertEqual(store["formal_admission"]["status"], "UNKNOWN")
        gateway_module = load_fixture_module(fixture, "legacy_governance_health", "scripts/query-gateway.py")
        health = gateway_module.QueryGateway().execute("get_source_health", {})
        self.assertEqual(len(health["sources"]), 5)
        self.assertTrue(all(row["rights_status"] == "UNKNOWN" and row["review_required"]
                            for row in health["source_rights"]))
        self.assertEqual(health["formal_admission"]["status"], "UNKNOWN")

    def test_current_formal_projection_admits_only_allowed_public_fields(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        self.assertEqual(store["formal_admission"]["status"], "ADMITTED")
        self.assertEqual(store["formal_admission"]["blocked_sources"], [])
        self.assertGreater(store["counts"]["publication_items"], 0)
        allowed_fields = {
            row["source_id"]: set(row["rights_retention_public_policy_refs"]["public_fields"])
            for row in sp.load_current_policy()["active_sources"]
        }
        system_fields = {
            "record_type", "canonical_id", "source_role", "trust_tier",
            "verification_status", "canonical_ref",
        }
        for row in store["items"]:
            self.assertLessEqual(set(row) - system_fields, allowed_fields[row["source_id"]])
            self.assertNotIn("full_text", row)
            self.assertNotIn("excerpt", row)

    def test_governed_candidate_keeps_active_sources_and_capabilities(self):
        legacy = self.legacy_policy()
        current = sp.load_current_policy()
        catalog = sp.load_catalog()
        candidate = sp.compile_governed_policy(catalog, previous=legacy)
        self.assertEqual(candidate["active_source_ids"], legacy["active_source_ids"])
        self.assertEqual(candidate["capabilities"], legacy["capabilities"])
        self.assertEqual(candidate["transition"], {"promoted": [], "retired": []})
        self.assertEqual(candidate["policy_version"], legacy["policy_version"] + 1)
        self.assertNotEqual(candidate["policy_hash"], legacy["policy_hash"])
        self.assertEqual(sp.compile_governed_policy(catalog, previous=candidate), candidate)
        sp.validate_catalog_binding(candidate, catalog)
        self.assertEqual(sp.load_current_policy(), current)

    def test_owner_released_rights_admit_a_governed_candidate_but_not_the_legacy_policy(self):
        old = self.legacy_policy()
        candidate = sp.compile_governed_policy(sp.load_catalog(), previous=old)
        result = sp.formal_admission(candidate)
        self.assertEqual(result["status"], "ADMITTED")
        self.assertEqual(result["blocked_sources"], [])
        for row in candidate["active_sources"]:
            rights = row["rights_retention_public_policy_refs"]
            self.assertEqual(rights["rights_status"], "OWNER_ASSERTED_PERMISSION")
            self.assertFalse(rights["full_text_allowed"] or rights["excerpt_allowed"])
            self.assertFalse(rights["may_claim_open_permission"])
        self.assertEqual(sp.formal_admission(old)["reason"], "APPROVED_GOVERNANCE_MISSING")
        # The gate itself is unchanged: unknown or unreviewed rights still block every active source.
        admission = sp.governance_module().admission
        for change in ({"rights_status": "UNKNOWN"}, {"review_required": True}):
            with self.subTest(change=change):
                blocked = copy.deepcopy(candidate)
                for row in blocked["active_sources"]:
                    row["rights_retention_public_policy_refs"].update(change)
                refused = admission(blocked)
                self.assertEqual(refused["status"], "RIGHTS_BLOCKED")
                self.assertEqual(refused["blocked_sources"], sorted(candidate["active_source_ids"]))
                self.assertEqual(set(refused["per_source"].values()), {"RIGHTS_UNKNOWN_OR_UNREVIEWED"})

    @staticmethod
    def legacy_policy():
        with tempfile.TemporaryDirectory(prefix="legacy-source-policy-") as directory:
            fixture = make_legacy_policy_fixture(Path(directory) / "legacy", source_root=ROOT)
            try:
                return json.loads(fixture["paths"]["approved"].read_text(encoding="utf-8"))
            finally:
                fixture["cleanup"]()

    def test_resigned_source_governance_tampering_cannot_change_trusted_inputs(self):
        candidate = sp.compile_governed_policy(sp.load_catalog(), previous=sp.load_current_policy())
        mutations = [
            lambda p: p["active_sources"][0].update(approved_origins=["https://evil.example.test"]),
            lambda p: p["active_sources"][0]["geographic_scope"].update(status="VERIFIED", declared_scope="全臺"),
            lambda p: p["active_sources"][0]["rights_retention_public_policy_refs"].update(rights_status="OPEN_DATA_LICENSED", review_required=False),
            lambda p: p["active_sources"][0]["rights_retention_public_policy_refs"].update(public_fields=["full_text"]),
            lambda p: p["governance_binding"]["retention_policy"].update(file_sha256="0" * 64),
            lambda p: p["governance_binding"]["freshness_rules"].update(file_sha256="0" * 64),
            lambda p: p["active_sources"][0]["freshness_policy"].update(fresh_hours=1),
        ]
        for change in mutations:
            with self.subTest(change=change):
                altered = copy.deepcopy(candidate)
                change(altered)
                altered["policy_hash"] = sp.digest({k: v for k, v in altered.items() if k != "policy_hash"})
                with self.assertRaises(ValueError):
                    sp.validate_catalog_binding(altered, sp.load_catalog())

    def test_approved_and_archive_bytes_are_unchanged_by_candidate_compilation(self):
        paths = [sp.APPROVED_POLICY, *sp.APPROVED_HISTORY.glob("*.json")]
        before = {p: p.read_bytes() for p in paths}
        sp.compile_governed_policy(sp.load_catalog(), previous=sp.load_current_policy())
        self.assertEqual(before, {p: p.read_bytes() for p in paths})


if __name__ == "__main__":
    unittest.main()
