import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "retention-policy.py"
spec = importlib.util.spec_from_file_location("retention_policy", SCRIPT)
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


class RetentionPolicyTests(unittest.TestCase):
    def test_catalog_has_one_conservative_policy_per_source(self):
        compiled = retention.compile_policy()
        self.assertEqual(set(compiled["source_policies"]), set(compiled["source_classes"]))
        self.assertTrue(all(value["public_projection"] in retention.PUBLIC_PROJECTIONS for value in compiled["source_policies"].values()))
        self.assertTrue(all(not value["full_text_allowed"] for value in compiled["source_policies"].values()))

    def test_policy_compile_is_deterministic_and_hash_bound(self):
        first = retention.compile_policy()
        second = retention.compile_policy()
        self.assertEqual(first, second)
        self.assertRegex(first["policy_hash"], r"^[0-9a-f]{64}$")
        self.assertRegex(first["catalog_hash"], r"^[0-9a-f]{64}$")

    def test_unknown_rights_cannot_become_unreviewed_full_text(self):
        policy = retention.load_json(retention.POLICY)
        mutated = copy.deepcopy(policy)
        mutated["classes"]["OFFICIAL_METADATA_LINK"]["review_required"] = False
        with self.assertRaisesRegex(ValueError, "unknown rights"):
            retention.compile_policy(policy=mutated)

    def test_prohibited_public_field_cannot_become_allowlisted(self):
        policy = retention.load_json(retention.POLICY)
        mutated = copy.deepcopy(policy)
        mutated["classes"]["OFFICIAL_METADATA_LINK"]["public_fields"] = ["raw_payload"]
        with self.assertRaisesRegex(ValueError, "prohibited"):
            retention.compile_policy(policy=mutated)

    def test_media_source_is_link_only_and_dry_run_preserves_audit_linkage(self):
        compiled = retention.compile_policy()
        self.assertEqual(compiled["source_policies"]["S-010"]["public_projection"], "LINK_ONLY")
        receipt = retention.plan_expiry([
            {
                "record_id": "raw-media-1",
                "source_id": "S-010",
                "layer": "raw_snapshot",
                "captured_at": "2026-09-01T00:00:00+00:00",
                "content_sha256": "a" * 64,
                "audit_refs": ["AUDIT-1"],
            },
            {
                "record_id": "event-1",
                "source_id": "S-010",
                "layer": "canonical_event",
                "captured_at": "2026-01-01T00:00:00+00:00",
                "content_sha256": "b" * 64,
                "audit_refs": ["AUDIT-2"],
            },
        ], observed_at="2026-09-21T00:00:00+00:00", policy=compiled)
        self.assertEqual(receipt["counts"], {"total": 2, "retained": 1, "expired": 1, "blocked": 0})
        self.assertEqual(receipt["records"][0]["action"], "PURGE_RAW_KEEP_AUDIT")
        self.assertEqual(receipt["records"][1]["action"], "RETAIN")
        self.assertTrue(receipt["dry_run"])
        self.assertRegex(receipt["receipt_sha256"], r"^[0-9a-f]{64}$")

    def test_expired_raw_without_audit_linkage_is_blocked_and_private_data_fails_closed(self):
        base = {
            "record_id": "raw-media-2",
            "source_id": "S-010",
            "layer": "raw_snapshot",
            "captured_at": "2026-09-01T00:00:00+00:00",
            "content_sha256": "c" * 64,
            "audit_refs": [],
        }
        receipt = retention.plan_expiry([base], observed_at="2026-09-21T00:00:00+00:00")
        self.assertEqual(receipt["records"][0]["status"], "BLOCKED")
        private = dict(base, private_notes="do not publish")
        with self.assertRaisesRegex(ValueError, "prohibited"):
            retention.plan_expiry([private], observed_at="2026-09-21T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
