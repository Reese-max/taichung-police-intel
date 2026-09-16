import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "system-health.py"
spec = importlib.util.spec_from_file_location("system_health", SCRIPT)
health = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(health)


def stage(lane, name, outcome, **extra):
    return {"lane": lane, "stage": name, "outcome": outcome, **extra}


class SystemHealthTests(unittest.TestCase):
    def test_query_failure_does_not_rewrite_publication_truth(self):
        stages = [
            stage("publication", "collection", "SUCCESS"),
            stage("publication", "validation", "SUCCESS"),
            stage("publication", "deployment", "SUCCESS"),
            stage("publication", "public_http", "SUCCESS"),
            stage("query", "query_index", "FAILED", error_class="INDEX_BUILD"),
        ]
        result = health.build_health(stages)
        self.assertEqual(result["lanes"]["publication"], "HEALTHY")
        self.assertEqual(result["lanes"]["query"], "BLOCKED")
        self.assertEqual(result["overall"], "DEGRADED")

    def test_publish_or_deploy_failure_blocks_publication_lane(self):
        result = health.build_health([
            stage("publication", "collection", "SUCCESS"),
            stage("publication", "deployment", "FAILED", error_class="PROTECTED_BRANCH"),
        ])
        self.assertEqual(result["lanes"]["publication"], "BLOCKED")
        self.assertEqual(result["overall"], "BLOCKED")

    def test_partial_collection_is_not_reported_as_zero_or_healthy(self):
        result = health.build_health([
            stage("publication", "collection", "PARTIAL", error_class="SOURCE_GAP"),
            stage("publication", "validation", "SUCCESS"),
        ])
        self.assertEqual(result["lanes"]["publication"], "PARTIAL")
        self.assertEqual(result["overall"], "PARTIAL")

    def test_unknown_deployment_receipt_keeps_publication_unknown(self):
        result = health.build_health([
            stage("publication", "collection", "SUCCESS"),
            stage("publication", "validation", "SUCCESS"),
            stage("publication", "deployment", "UNKNOWN"),
        ])
        self.assertEqual(result["lanes"]["publication"], "UNKNOWN")
        self.assertEqual(result["overall"], "UNKNOWN")

    def test_latency_metrics_require_reliable_aware_ordered_times(self):
        result = health.build_health([], {
            "source_published_at": "2026-09-17T00:00:00+08:00",
            "detected_at": "2026-09-17T00:05:00+08:00",
            "verified_at": "2026-09-17T00:08:00+08:00",
            "published_at": "2026-09-17T00:10:00+08:00",
            "public_visible_at": "2026-09-17T00:11:00+08:00",
        })
        self.assertEqual(result["latency_metrics"]["source_to_detect_ms"], 300000)
        self.assertEqual(result["latency_metrics"]["detect_to_verify_ms"], 180000)
        self.assertEqual(result["latency_metrics"]["verify_to_publish_ms"], 120000)
        self.assertEqual(result["latency_metrics"]["publish_to_visible_ms"], 60000)
        self.assertIsNone(health.latency_ms("2026-09-17T00:00:00", "2026-09-17T00:01:00"))

    def test_duplicate_stage_receipts_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate stage"):
            health.build_health([
                stage("publication", "deployment", "SUCCESS"),
                stage("publication", "deployment", "FAILED"),
            ])

    def test_bad_stage_time_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "invalid ended_at"):
            health.build_health([stage("publication", "collection", "SUCCESS", ended_at="not-a-time")])

    def test_checked_in_current_artifacts_produce_machine_readable_stage_breakdown(self):
        result = health.load_current()
        self.assertEqual(result["schema_version"], 1)
        names = {(row["lane"], row["stage"]) for row in result["stages"]}
        self.assertIn(("publication", "collection"), names)
        self.assertIn(("publication", "canonical_validation"), names)
        self.assertIn(("publication", "deployment"), names)
        self.assertIn(("publication", "public_http_verification"), names)
        self.assertIn(("query", "query_index"), names)
        # Existing checked-in artifacts do not contain a deployment/hash receipt;
        # the model must say UNKNOWN rather than manufacture success.
        self.assertEqual(result["lanes"]["publication"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
