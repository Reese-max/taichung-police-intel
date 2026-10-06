import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import re
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


def collector(fail=None, sources=SOURCES, existing_log=None):
    def collect(session, source_id, start, end, existing, *, max_details):
        assert max_details == 1
        session.calls = 2
        if existing_log is not None:
            existing_log[source_id] = existing
        if source_id == fail:
            raise ValueError("fixture unavailable")
        return {
            "manifest_sha256": "a" * 64,
            "source_health": "PASS",
            "window_completeness": "PARTIAL",
            "window_item_count": 2,
            "snapshot_item_count": 3,
            "snapshots": [{"purpose": "LIST"}, {"purpose": "DETAIL"}],
            "items": [
                {
                    "stable_key": f"{source_id}-item-1",
                    "content_sha256": "b" * 64,
                    "payload": {"detail": "unchanged-skipped" if existing else "fetched"},
                }
            ],
        }

    return SimpleNamespace(
        NEWS_LIST_SOURCES={source: {"list_url": "https://official.example.test/"} for source in sources},
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

    def test_default_transport_preserves_runtime_proxy_and_ca_settings(self):
        session = module.BoundedSession("https://official.example.test/")
        try:
            self.assertTrue(session.transport.trust_env)
        finally:
            session.close()

    def test_default_transport_has_no_hidden_retries_outside_call_budget(self):
        session = module.BoundedSession("https://official.example.test/")
        try:
            retry = session.transport.adapters["https://"].max_retries
            self.assertEqual(retry.total, 0)
            self.assertEqual(retry.connect, 0)
            self.assertEqual(retry.read, 0)
            self.assertEqual(retry.status, 0)
        finally:
            session.close()

    def test_injected_requests_transport_is_also_retry_free(self):
        from online_collect import http_session

        transport = http_session()
        session = module.BoundedSession("https://official.example.test/", transport)
        try:
            self.assertIs(session.transport, transport)
            self.assertTrue(transport.trust_env)
            self.assertTrue(all(adapter.max_retries.total == 0 for adapter in transport.adapters.values()))
        finally:
            session.close()

    def test_transport_keeps_redirects_disabled_when_collector_passes_option(self):
        response = SimpleNamespace(status_code=200, headers={})
        response.iter_content = lambda chunk_size: [b"ok"]
        response.raise_for_status = lambda: None
        response.close = lambda: None

        class Transport:
            headers = {}

            def __init__(self):
                self.calls = []
                self.trust_env = True

            def get(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return response

            def close(self):
                pass

        transport = Transport()
        session = module.BoundedSession("https://official.example.test/", transport)
        try:
            session.get("https://official.example.test/list", allow_redirects=True)
        finally:
            session.close()
        self.assertEqual(len(transport.calls), 1)
        self.assertFalse(transport.calls[0][1]["allow_redirects"])
        self.assertTrue(transport.trust_env)

    def test_each_redirect_consumes_call_budget_and_seventh_request_is_blocked(self):
        class Transport:
            headers = {}

            def __init__(self):
                self.calls = []
                self.closed = 0

            def get(self, url, **kwargs):
                self.calls.append(url)
                response = SimpleNamespace(status_code=302, headers={"location": "/redirect"})
                response.close = lambda: setattr(self, "closed", self.closed + 1)
                return response

            def close(self):
                pass

        transport = Transport()
        session = module.BoundedSession("https://official.example.test/", transport)
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "redirect budget exhausted"):
                session.get("https://official.example.test/list")
        with self.assertRaisesRegex(RuntimeError, "HTTP budget exhausted"):
            session.get("https://official.example.test/list")
        self.assertEqual(session.calls, 6)
        self.assertEqual(len(transport.calls), 6)
        self.assertEqual(transport.closed, 6)

    def test_catalog_candidate_inventory_includes_live_adapters(self):
        import online_collect

        self.assertIn("S-033", module.candidate_source_ids(online_collect))
        self.assertIs(online_collect.COLLECTORS["S-033"], online_collect.collect_news_list)
        self.assertIn("S-031", module.candidate_source_ids(online_collect))
        self.assertIs(online_collect.COLLECTORS["S-031"], online_collect.collect_fire_live)

    def test_observation_workflow_matrix_covers_every_candidate(self):
        import online_collect

        workflow = (SCRIPT.parent.parent / ".github/workflows/candidate-source-observation.yml").read_text(
            encoding="utf-8"
        )
        matrix_match = re.search(r"source:\s*\[([^\]]+)\]", workflow)
        self.assertIsNotNone(matrix_match, "workflow must keep a source matrix")
        matrix = {item.strip() for item in matrix_match.group(1).split(",")}
        expected = set(module.candidate_source_ids(online_collect))
        self.assertEqual(expected, {"S-001", "S-019", "S-031", "S-032", "S-033"})
        self.assertEqual(matrix, expected)

    def test_existing_items_are_threaded_into_collect_source(self):
        log = {}
        prior = {"S-001": {"S-001-item-1": {"content_sha256": "b" * 64}}}
        report = module.run_canary(
            collector(existing_log=log),
            ["S-001"],
            NOW,
            session_factory=FakeSession,
            existing_items=prior,
        )
        self.assertEqual(log["S-001"], prior["S-001"])
        record = report["sources"][0]
        self.assertEqual(record["unchanged_detail_skips"], 1)

    def test_report_carries_incremental_item_state(self):
        report = module.run_canary(collector(), ["S-001"], NOW, session_factory=FakeSession)
        self.assertEqual(
            report["item_state"],
            {"S-001": {"S-001-item-1": "b" * 64}},
        )

    def test_fire_candidate_record_carries_usage_notice_and_catalog_truth(self):
        report = module.run_canary(
            collector(sources=("S-031",)), ["S-031"], NOW, session_factory=FakeSession
        )
        record = report["sources"][0]
        self.assertEqual(record["catalog_status"], "VERIFIED_CANDIDATE")
        self.assertEqual(record["source_role"], "PRIMARY_EVENT")
        self.assertIn("派遣", record["public_usage_notice"])
        self.assertEqual(record["retention_class"], "OFFICIAL_TRANSIENT_METADATA")


if __name__ == "__main__":
    unittest.main()
