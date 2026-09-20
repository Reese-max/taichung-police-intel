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


if __name__ == "__main__":
    unittest.main()
