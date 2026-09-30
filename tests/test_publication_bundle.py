import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-publication-bundle.py"
spec = importlib.util.spec_from_file_location("verify_publication_bundle", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class PublicationBundleTests(unittest.TestCase):
    def test_source_rows_are_not_silently_filtered(self):
        with self.assertRaisesRegex(ValueError, "only objects"):
            module.source_ids([{"source_id": "S-004"}, None])

    def test_source_ids_must_be_nonempty_strings(self):
        with self.assertRaisesRegex(ValueError, "non-empty strings"):
            module.source_ids([{"source_id": ""}])

    def test_validator_uses_current_source_policy(self):
        policy_path = Path(__file__).resolve().parents[1] / "scripts/source-policy.py"
        policy_spec = importlib.util.spec_from_file_location("source_policy", policy_path)
        policy = importlib.util.module_from_spec(policy_spec)
        assert policy_spec and policy_spec.loader
        policy_spec.loader.exec_module(policy)
        expected = {
            row["source_id"]
            for row in policy.load_catalog()["sources"]
            if row["status"] == "PRODUCTION_ACTIVE"
        }
        self.assertEqual(module.load_expected_sources(), expected)

    def test_checked_in_bundle_passes(self):
        self.assertEqual(module.main(), 0)

    def test_checked_in_sources_expose_catalog_role_and_integration_status(self):
        status = module.load_json("source-status.json")
        metadata = module.load_catalog_source_metadata()
        for source in status["sources"]:
            source_id = source["source_id"]
            self.assertEqual(source["source_role"], metadata[source_id]["role"])
            self.assertEqual(source["integration_status"], metadata[source_id]["status"])


if __name__ == "__main__":
    unittest.main()
