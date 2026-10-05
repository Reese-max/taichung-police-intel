"""Regression coverage for transient official-source transport failures."""

import importlib.util
import contextlib
import io
import json
from datetime import date
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import requests

import online_collect as oc


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("source_recovery_canary", ROOT / "canary-s026-s029.py")
canary = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(canary)
DRIFT_SPEC = importlib.util.spec_from_file_location("source_recovery_drift", ROOT / "scripts/schema_drift.py")
drift = importlib.util.module_from_spec(DRIFT_SPEC)
DRIFT_SPEC.loader.exec_module(drift)
CANDIDATE_SPEC = importlib.util.spec_from_file_location("source_recovery_candidate", ROOT / "scripts/candidate-runtime-canary.py")
candidate = importlib.util.module_from_spec(CANDIDATE_SPEC)
CANDIDATE_SPEC.loader.exec_module(candidate)


def response(url, status=200, body=b"official body", location=None):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result.request = requests.Request("GET", url).prepare()
    result._content = body
    result._content_consumed = True
    if location is not None:
        result.headers["location"] = location
    return result


class OfficialTransportRecoveryTests(unittest.TestCase):
    def test_exhausted_http_retries_keep_the_original_http_status(self):
        session = oc.http_session()
        self.addCleanup(session.close)
        retry = session.get_adapter(canary.S029_INDEX_URL).max_retries
        # Exercise retry exhaustion rather than only checking its configuration.
        from urllib3.response import HTTPResponse

        failure = HTTPResponse(status=503)
        retry = retry.increment(method="GET", url=canary.S029_INDEX_URL, response=failure)
        retry = retry.increment(method="GET", url=canary.S029_INDEX_URL, response=failure)
        self.assertTrue(retry.is_exhausted() is False)
        self.assertFalse(retry.raise_on_status)
        final = response(canary.S029_INDEX_URL, 503, b"upstream connection timeout")
        with mock.patch.object(session, "get", return_value=final):
            with self.assertRaises(requests.HTTPError) as caught:
                oc.get(session, canary.S029_INDEX_URL, source_id="S-029")
        self.assertEqual(caught.exception.response.status_code, 503)

    def test_s029_does_not_multiply_adapter_and_canary_retry_budgets(self):
        session = oc.http_session()
        self.addCleanup(session.close)
        final = response(canary.S029_INDEX_URL, 503)
        with mock.patch.object(session, "get", return_value=final) as fetch, mock.patch.object(canary.time, "sleep") as sleep:
            with self.assertRaises(requests.HTTPError) as caught:
                canary.get(session, canary.S029_INDEX_URL, canary.S029_HOSTS)
        fetch.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(caught.exception.response.status_code, 503)

    def test_standalone_canary_keeps_its_three_attempt_budget(self):
        session = requests.Session()
        self.addCleanup(session.close)
        with mock.patch.object(session, "get", return_value=response(canary.S029_INDEX_URL, 503)) as fetch, mock.patch.object(canary.time, "sleep") as sleep:
            with self.assertRaises(requests.HTTPError):
                canary.get(session, canary.S029_INDEX_URL, canary.S029_HOSTS)
        self.assertEqual(fetch.call_count, 3)
        self.assertEqual(sleep.call_args_list, [mock.call(1), mock.call(2)])

    def test_standalone_canary_recovers_from_one_transient_failure(self):
        session = requests.Session()
        self.addCleanup(session.close)
        good = response(canary.S029_INDEX_URL)
        with mock.patch.object(session, "get", side_effect=[response(canary.S029_INDEX_URL, 503), good]) as fetch, mock.patch.object(canary.time, "sleep"):
            result = canary.get(session, canary.S029_INDEX_URL, canary.S029_HOSTS)
        self.assertIs(result, good)
        self.assertEqual(fetch.call_count, 2)

    def test_police_http_503_uses_catalog_bound_fallback(self):
        config = oc.NEWS_LIST_SOURCES["S-001"]
        session = requests.Session()
        self.addCleanup(session.close)
        good = response(config["fallback_list_url"])
        with mock.patch.object(session, "get", side_effect=[response(config["list_url"], 503), good]) as fetch:
            self.assertIs(oc.get_news_listing(session, "S-001"), good)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [config["list_url"], config["fallback_list_url"]])
        self.assertEqual(good._govintel_requested_url, config["fallback_list_url"])

    def test_access_denials_and_missing_pages_do_not_trigger_fallback(self):
        config = oc.NEWS_LIST_SOURCES["S-001"]
        for status in (401, 403, 404):
            with self.subTest(status=status), requests.Session() as session:
                with mock.patch.object(session, "get", return_value=response(config["list_url"], status)) as fetch:
                    with self.assertRaises(requests.HTTPError):
                        oc.get_news_listing(session, "S-001")
                fetch.assert_called_once()

    def test_tls_failure_does_not_trigger_alternate_request(self):
        with mock.patch.object(oc, "get", side_effect=requests.exceptions.SSLError("certificate validation failed")) as fetch:
            with self.assertRaises(requests.exceptions.SSLError):
                oc.get_news_listing(object(), "S-001")
        fetch.assert_called_once()

    def test_fallback_cannot_follow_an_external_redirect(self):
        config = oc.NEWS_LIST_SOURCES["S-001"]
        with requests.Session() as session:
            with mock.patch.object(session, "get", side_effect=[
                response(config["list_url"], 503),
                response(config["fallback_list_url"], 302, location="https://example.invalid/list"),
            ]) as fetch:
                with self.assertRaisesRegex(ValueError, "outside the approved source origin"):
                    oc.get_news_listing(session, "S-001")
        self.assertEqual(fetch.call_count, 2)

    def test_both_police_endpoints_unavailable_remain_a_failure(self):
        config = oc.NEWS_LIST_SOURCES["S-001"]
        with requests.Session() as session:
            with mock.patch.object(session, "get", side_effect=[
                response(config["list_url"], 503), response(config["fallback_list_url"], 503),
            ]) as fetch:
                with self.assertRaises(requests.HTTPError):
                    oc.get_news_listing(session, "S-001")
        self.assertEqual(fetch.call_count, 2)

    def test_malformed_fallback_does_not_become_complete_zero(self):
        config = oc.NEWS_LIST_SOURCES["S-001"]
        with requests.Session() as session:
            with mock.patch.object(session, "get", side_effect=[
                response(config["list_url"], 503), response(config["fallback_list_url"], body=b"<html>temporary error</html>"),
            ]):
                with self.assertRaisesRegex(ValueError, "no parseable entries"):
                    oc.collect_news_list(session, "S-001", date(2026, 9, 29), date(2026, 10, 5), max_details=0)

    def test_no_unverified_alternative_is_used_for_s019(self):
        config = oc.NEWS_LIST_SOURCES["S-019"]
        with requests.Session() as session:
            with mock.patch.object(session, "get", return_value=response(config["list_url"], 503)) as fetch:
                with self.assertRaises(requests.HTTPError):
                    oc.get_news_listing(session, "S-019")
        fetch.assert_called_once()

    def test_same_origin_redirect_keeps_requested_url_evidence(self):
        config = oc.NEWS_LIST_SOURCES["S-019"]
        next_url = config["list_url"] + "/"
        good = response(next_url)
        with requests.Session() as session:
            with mock.patch.object(session, "get", side_effect=[response(config["list_url"], 302, location=next_url), good]):
                result = oc.get_news_listing(session, "S-019")
        self.assertEqual(oc.snapshot(result, "LIST")["requested_url"], config["list_url"])
        self.assertEqual(oc.snapshot(result, "LIST")["final_url"], next_url)

    def test_public_source_503_keeps_last_good_and_unknown_current_counts(self):
        prior_lkg = {"completed_at": "2026-09-24T23:18:12+08:00", "manifest_sha256": "a" * 64}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source-status.json"
            output.write_text(json.dumps({
                "schema_version": 1, "mode": "COMPETITION_DEMO", "sources": [{
                    "source_id": "S-029", "last_success_at": prior_lkg["completed_at"],
                    "last_known_good": prior_lkg, "data_as_of": "2026-07-24T00:00:00+08:00",
                    "snapshot_item_count": 35,
                }],
            }))
            (Path(directory) / "intelligence-feed.json").write_text(json.dumps({"items": [{
                "source_id": "S-029", "stable_id": "prior-police-report", "title": "official report",
                "content_sha256": "b" * 64, "published_at": "2026-07-24T00:00:00+08:00",
            }]}))
            failed = response(canary.S029_INDEX_URL, 503)
            error = requests.HTTPError("503 upstream timeout", response=failed)
            with mock.patch.object(oc, "P0_SOURCES", {"S-029": oc.P0_SOURCES["S-029"]}), mock.patch.object(
                oc, "collect_source", side_effect=error
            ), contextlib.redirect_stdout(io.StringIO()):
                state = oc.build_demo_status(output, "MORNING", date(2026, 10, 5), "manual")
            feed = json.loads((Path(directory) / "intelligence-feed.json").read_text())
        source = state["sources"][0]
        self.assertEqual(source["error_code"], "HTTP_503")
        self.assertEqual(source["source_health"], "FAILED")
        self.assertEqual(source["window_completeness"], "PARTIAL")
        self.assertIsNone(source["window_item_count"])
        self.assertEqual(source["last_known_good"], prior_lkg)
        self.assertEqual(source["last_success_at"], prior_lkg["completed_at"])
        self.assertEqual(source["data_as_of"], "2026-07-24T00:00:00+08:00")
        self.assertEqual(feed["items"][0]["change_type"], "LKG")
        self.assertEqual(feed["items"][0]["eligibility"], "INELIGIBLE_SOURCE_FAILED")

    def test_schema_http_503_is_source_unavailable_with_status_evidence(self):
        failed = response(oc.NEWS_LIST_SOURCES["S-019"]["list_url"], 503, b"upstream timeout")
        observation = drift._failed_observation("S-019", requests.HTTPError(response=failed))
        result = drift.observe(drift.CONTRACTS["S-019"], **{key: value for key, value in observation.items() if key != "source_id"})
        self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(result["http_status"], 503)
        self.assertEqual(result["reasons"], ["HTTP_503"])
        self.assertEqual(result["window_completeness"], "PARTIAL")

    def test_schema_http_200_parser_drift_stays_distinct_from_transport_failure(self):
        result = drift.observe(drift.CONTRACTS["S-019"], b"<html>changed list</html>", content_type="text/html")
        self.assertEqual(result["status"], "BREAKING_DRIFT")
        self.assertEqual(result["http_status"], 200)

    def test_schema_adapter_owns_retry_budget(self):
        session = drift._live_session()
        self.addCleanup(session.close)
        with mock.patch.object(drift, "CONTRACTS", {"S-019": drift.CONTRACTS["S-019"]}), mock.patch.object(
            drift.time, "sleep"
        ) as sleep, contextlib.redirect_stdout(io.StringIO()):
            fetch = mock.Mock(side_effect=requests.ConnectionError("upstream timed out"))
            observations = drift.live_observations(session=session, fetch_source=fetch)
        fetch.assert_called_once()
        sleep.assert_not_called()
        self.assertEqual(observations[0]["http_status"], 0)
        self.assertEqual(observations[0]["error_reason"], "LIVE_FETCH_CONNECTIONERROR")

    def test_two_live_cli_runs_persist_history_and_last_good_across_outage(self):
        body = '<meta charset="utf-8"><li><a href="news_view.jsp?dataserno=1">官方新聞 115-09-30</a></li>'.encode()
        good = drift._response_observation("S-001", response(oc.NEWS_LIST_SOURCES["S-001"]["list_url"], body=body))
        good["content_type"] = "text/html"
        failed = drift._failed_observation("S-001", requests.ConnectionError("reset"))
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            output_path = Path(directory) / "receipt.json"
            argv = ["--live", "--state", str(state_path), "--output", str(output_path)]
            with mock.patch.object(drift, "live_observations", side_effect=[[good], [failed]]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(drift.main(argv), 0)
                first = json.loads(state_path.read_text())
                self.assertEqual(drift.main(argv), 0)
            saved = json.loads(state_path.read_text())["sources"]["S-001"]
            receipt = json.loads(output_path.read_text())
            source = next(row for row in receipt["sources"] if row["source_id"] == "S-001")
        self.assertEqual(saved["current"]["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(saved["last_known_good"], first["sources"]["S-001"]["last_known_good"])
        self.assertEqual(len(saved["history"]), 1)
        self.assertEqual(saved["history"][0]["status"], "NO_DRIFT")
        self.assertEqual(source["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(source["last_known_good"], saved["last_known_good"])
        self.assertEqual(source["history_count"], 1)
        self.assertEqual(receipt["overall"], "BLOCKED")

    def test_candidate_http_503_has_no_schema_or_promotion_claim(self):
        config = oc.NEWS_LIST_SOURCES["S-019"]
        with requests.Session() as transport:
            with mock.patch.object(transport, "get", return_value=response(config["list_url"], 503)):
                report = candidate.run_canary(
                    oc, ["S-019"], datetime(2026, 10, 5, tzinfo=timezone.utc),
                    session_factory=lambda url: candidate.BoundedSession(url, transport),
                )
        row = report["sources"][0]
        self.assertEqual(row["schema_contract"]["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(row["schema_contract"]["reasons"], ["HTTP_503"])
        self.assertEqual(row["last_http_status"], 503)
        self.assertEqual(row["http_calls"], 1)
        self.assertEqual(row["integration_status"], "CANDIDATE")
        self.assertFalse(row["promotion_eligible"])
        self.assertIsNone(row["window_item_count"])

    def test_candidate_keeps_an_observed_schema_drift_diagnosis(self):
        with mock.patch.object(oc, "collect_source", return_value={
            "source_health": "PASS", "window_completeness": "PARTIAL",
            "window_item_count": 0, "snapshot_item_count": 1,
            "manifest_sha256": "a" * 64,
            "snapshots": [{"purpose": "LIST", "body": b"<html>changed</html>", "content_type": "text/html", "http_status": 200}],
        }):
            report = candidate.run_canary(
                oc, ["S-019"], datetime(2026, 10, 5, tzinfo=timezone.utc),
                session_factory=lambda url: candidate.BoundedSession(url),
            )
        row = report["sources"][0]
        self.assertEqual(row["source_health"], "FAILED")
        self.assertEqual(row["schema_contract"]["status"], "BREAKING_DRIFT")
        self.assertIn("HTML_LIST_ID_OR_SELECTOR_FAILED", row["schema_contract"]["reasons"])


if __name__ == "__main__":
    unittest.main()
