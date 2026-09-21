import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/candidate-runtime-canary.py"
sys.path.insert(0, str(SCRIPT.parent.parent))
spec = importlib.util.spec_from_file_location("candidate_runtime_canary", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
NOW = datetime(2026, 9, 17, tzinfo=timezone.utc)
SOURCES = ("S-001", "S-019", "S-032")


class FakeSession:
    def __init__(self, url):
        self.calls = 0

    def close(self):
        pass


def collector(fail=None):
    def collect(session, source_id, start, end, existing, *, max_details):
        assert max_details == 1
        session.calls = 2
        if source_id == fail:
            raise ValueError("fixture unavailable")
        return {
            "manifest_sha256": "a" * 64,
            "source_health": "PASS",
            "window_completeness": "PARTIAL",
            "window_item_count": 2,
            "snapshot_item_count": 3,
            "snapshots": [{"purpose": "LIST"}, {"purpose": "DETAIL"}],
        }

    return SimpleNamespace(
        NEWS_LIST_SOURCES={source: {"list_url": "https://official.example.test/"} for source in SOURCES},
        collect_source=collect,
    )


class CanaryContractTests(unittest.TestCase):
    def test_success_is_candidate_observation_not_promotion(self):
        report = module.run_canary(collector(), list(SOURCES), NOW, session_factory=FakeSession)
        self.assertEqual(report["source_count"], 3)
        self.assertEqual(report["failed_count"], 0)
        for item in report["sources"]:
            self.assertFalse(item["promotion_eligible"])
            self.assertFalse(item["coverage_independently_verified"])
            self.assertEqual(item["integration_status"], "CANDIDATE")
            self.assertNotIn("body", str(item))

    def test_one_source_failure_preserves_other_observations_and_null_counts(self):
        report = module.run_canary(collector("S-019"), list(SOURCES), NOW, session_factory=FakeSession)
        self.assertEqual(report["failed_count"], 1)
        failed = report["sources"][1]
        self.assertEqual(failed["source_health"], "FAILED")
        self.assertEqual(failed["error_message"], "fixture unavailable")
        self.assertIsNone(failed["window_item_count"])
        self.assertEqual(report["sources"][2]["source_health"], "PASS")

    def test_unknown_duplicate_and_empty_source_selection_rejected(self):
        for sources in ([], ["S-001", "S-001"], ["file:///etc/passwd"]):
            with self.assertRaises(ValueError):
                module.run_canary(collector(), sources, NOW, session_factory=FakeSession)

    def test_naive_clock_rejected(self):
        with self.assertRaises(ValueError):
            module.run_canary(collector(), ["S-001"], NOW.replace(tzinfo=None), session_factory=FakeSession)

    def test_transport_denies_arbitrary_host_before_any_request(self):
        class Transport:
            headers = {}

            def get(self, *args, **kwargs):
                raise AssertionError("must not issue external request")

        session = module.BoundedSession("https://www.official.example.test/list", Transport())
        with self.assertRaisesRegex(ValueError, "unapproved"):
            session.get("https://evil.example.test/")
        self.assertEqual(session.calls, 0)

    def test_default_transport_reuses_collector_retry_policy(self):
        session = module.BoundedSession("https://official.example.test/")
        try:
            retry = session.transport.adapters["https://"].max_retries
            self.assertEqual(retry.total, 2)
            self.assertEqual(set(retry.status_forcelist), {429, 500, 502, 503, 504})
            self.assertIn("GET", retry.allowed_methods)
        finally:
            session.close()

    def test_catalog_candidate_inventory_includes_live_adapters(self):
        import online_collect

        self.assertIn("S-033", module.candidate_source_ids(online_collect))
        self.assertIs(online_collect.COLLECTORS["S-033"], online_collect.collect_news_list)
        self.assertIn("S-031", module.candidate_source_ids(online_collect))
        self.assertIs(online_collect.COLLECTORS["S-031"], online_collect.collect_fire_live)


if __name__ == "__main__":
    unittest.main()
