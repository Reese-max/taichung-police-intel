import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-source-policy-integration.py"
spec = importlib.util.spec_from_file_location("source_policy_integration_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(check)


class SourcePolicyIntegrationTests(unittest.TestCase):
    def test_all_consumers_and_replay_share_current_policy(self):
        result = check.run_checks()
        self.assertEqual(result["consumer_count"], 7)
        self.assertEqual(
            result["consumer_names"],
            [
                "query_store",
                "system_health",
                "ui",
                "query_gateway",
                "collector",
                "online_collector",
                "publication_validator",
            ],
        )
        self.assertTrue(result["mixed_policy_rejected"])
        self.assertTrue(result["partial_gap_preserved"])
        self.assertTrue(result["unsupported_explicit"])
        self.assertTrue(result["query_coverage_bound"])
        self.assertEqual(result["promoted_policy_version"], result["policy_version"] + 1)


if __name__ == "__main__":
    unittest.main()
