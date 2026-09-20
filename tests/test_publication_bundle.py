import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-publication-bundle.py"
spec = importlib.util.spec_from_file_location("verify_publication_bundle", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class PublicationBundleTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
