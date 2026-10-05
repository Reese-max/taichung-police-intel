import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


sp = load("governance_test_policy", "scripts/source-policy.py")
qs = load("governance_test_store", "scripts/query-store.py")


class SourceGovernanceTests(unittest.TestCase):
    def test_current_formal_projection_excludes_actual_ungoverned_sources(self):
        store = qs.build_from_paths(qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF)
        self.assertEqual(store["items"], [])
        self.assertEqual(len(store["sources"]), 5)
        gateway_module = load("current_governance_health", "scripts/query-gateway.py")
        health = gateway_module.QueryGateway().execute("get_source_health", {})
        self.assertEqual(len(health["sources"]), 5)
        self.assertTrue(all(row["rights_status"] == "UNKNOWN" and row["review_required"] for row in health["source_rights"]))
        self.assertEqual(health["formal_admission"]["status"], "UNKNOWN")

    def test_governed_candidate_keeps_active_sources_and_capabilities(self):
        old = sp.load_current_policy()
        catalog = sp.load_catalog()
        candidate = sp.compile_governed_policy(catalog, previous=old)
        self.assertEqual(candidate["active_source_ids"], old["active_source_ids"])
        self.assertEqual(candidate["capabilities"], old["capabilities"])
        self.assertEqual(candidate["transition"], {"promoted": [], "retired": []})
        self.assertEqual(candidate["policy_version"], old["policy_version"] + 1)
        self.assertNotEqual(candidate["policy_hash"], old["policy_hash"])
        self.assertEqual(sp.compile_governed_policy(catalog, previous=candidate), candidate)
        sp.validate_catalog_binding(candidate, catalog)
        self.assertEqual(sp.load_current_policy(), old)

    def test_all_actual_rights_unknown_remain_refused(self):
        old = sp.load_current_policy()
        candidate = sp.compile_governed_policy(sp.load_catalog(), previous=old)
        result = sp.formal_admission(candidate)
        self.assertEqual(result["status"], "RIGHTS_BLOCKED")
        self.assertEqual(result["blocked_sources"], old["active_source_ids"])
        self.assertEqual(sp.formal_admission(old)["reason"], "APPROVED_GOVERNANCE_MISSING")

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
