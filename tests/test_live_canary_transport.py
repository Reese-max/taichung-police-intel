import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from datetime import date
import contextlib
import io
import json
import tempfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "canary-s026-s029.py"
spec = importlib.util.spec_from_file_location("canary_s026_s029", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
S028_SCRIPT = ROOT / "canary-s028-165.py"
s028_spec = importlib.util.spec_from_file_location("canary_s028_165", S028_SCRIPT)
s028 = importlib.util.module_from_spec(s028_spec)
assert s028_spec and s028_spec.loader
s028_spec.loader.exec_module(s028)


class Response:
    def __init__(self, url, *, status_code=200, location=None, content=b"ok", headers=None):
        self.url = url
        self.status_code = status_code
        self.headers = dict(headers or {})
        if location:
            self.headers["location"] = location
        self.content = content
        self.request = SimpleNamespace(url=url)
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        return None

    def json(self):
        return json.loads(self.content)


class ApiResponse(Response):
    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class LiveCanaryTransportTests(unittest.TestCase):
    HOSTS = {"official.test"}

    def test_external_redirect_is_rejected_before_following(self):
        session = Session([
            Response(
                "https://official.test/start",
                status_code=302,
                location="https://example.invalid/redirected",
            )
        ])
        with self.assertRaisesRegex(RuntimeError, "非白名單來源"):
            module.get(session, "https://official.test/start", self.HOSTS)
        self.assertEqual(len(session.calls), 1)


    def test_same_host_redirect_keeps_original_request_evidence(self):
        session = Session([
            Response("https://official.test/start", status_code=302, location="/next"),
            Response("https://official.test/next"),
        ])
        response = module.get(session, "https://official.test/start", self.HOSTS)
        evidence = module.response_evidence(response)
        self.assertEqual(evidence["requested_url"], "https://official.test/start")
        self.assertEqual(evidence["final_url"], "https://official.test/next")
        self.assertEqual(len(session.calls), 2)

    def test_s028_transport_rejects_external_redirect_before_following(self):
        session = Session([
            Response(
                "https://official.test/start",
                status_code=302,
                location="https://example.invalid/redirected",
            )
        ])
        with self.assertRaisesRegex(RuntimeError, "非白名單來源"):
            s028.get(session, "https://official.test/start", self.HOSTS)
        self.assertEqual(len(session.calls), 1)

class S029CollectorTests(unittest.TestCase):
    INDEX = module.S029_INDEX_URL
    SESSION = "https://www.rdec.taichung.gov.tw/12047/12142/12145/11508/Lpsimplelist"
    PDF = "https://www.rdec.taichung.gov.tw/media/police.pdf"

    def session(self, *, pdf_body=b"%PDF-1.7 observed attachment"):
        index = f'<meta charset="utf-8"><a title="臺中市議會第4屆第8次定期會" href="{self.SESSION}">會期</a>'.encode()
        first = (f'<meta charset="utf-8"><p>共 2 筆資料 第 1 / 2 頁</p>'
                 f'<li>2026-09-30<a href="{self.PDF}" title="1150930-1 警察局專案報告(另開新視窗)">報告</a></li>').encode()
        second = ('<meta charset="utf-8"><p>共 2 筆資料 第 2 / 2 頁</p>'
                  '<li>2026-09-01<a href="/media/other.pdf" title="1150901-1 民政專案報告">報告</a></li>').encode()
        return Session([
            Response(self.INDEX, content=index),
            Response(self.SESSION + "?Page=1&PageSize=30&type=", content=first),
            Response(self.SESSION + "?Page=2&PageSize=30&type=", content=second),
            Response(self.PDF, content=pdf_body),
        ])

    def test_one_fetch_per_response_preserves_validated_attachment_bytes(self):
        import online_collect as oc

        session = self.session()
        result = oc.collect_s029(session, date(2026, 9, 29), date(2026, 10, 4))
        self.assertEqual(len(session.calls), 4)
        self.assertEqual([snap["purpose"] for snap in result["snapshots"]], ["LIST", "LIST", "LIST", "ATTACHMENT"])
        attachment = result["snapshots"][-1]
        self.assertEqual(attachment["content_sha256"], result["items"][0]["payload"]["sha256"])
        self.assertEqual(attachment["body"], b"%PDF-1.7 observed attachment")
        self.assertEqual(result["snapshot_item_count"], 2)
        self.assertEqual(result["window_item_count"], 1)
        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")

    def test_non_pdf_response_still_fails_closed(self):
        import online_collect as oc

        session = self.session(pdf_body=b"<html>temporary error</html>")
        with self.assertRaisesRegex(RuntimeError, "不是 PDF"):
            oc.collect_s029(session, date(2026, 9, 29), date(2026, 10, 4))
        self.assertEqual(len(session.calls), 4)

    def test_canary_without_observer_preserves_existing_contract(self):
        session = self.session()
        result = module.fetch_s029(session, date(2026, 9, 29), date(2026, 10, 4))
        self.assertEqual(result["canary_status"], "PASS")
        self.assertEqual(len(session.calls), 4)

    def test_snapshot_retains_original_requested_url_after_canary_redirect(self):
        import online_collect as oc

        response = Response(self.PDF, content=b"%PDF-1.7")
        response._govintel_requested_url = self.INDEX
        snap = oc.snapshot(response, "ATTACHMENT")
        self.assertEqual(snap["requested_url"], self.INDEX)
        self.assertEqual(snap["final_url"], self.PDF)



class DownloadListDateTruthTests(unittest.TestCase):
    def collect(self, titles):
        import online_collect as oc

        source_url = oc.P0_SOURCES["S-006"][1]
        listing = '<meta charset="utf-8">' + ''.join(
            f'<div id="Fdownload_list"><div class="text02_1">{title}</div>'
            f'<div class="text02_2"><a href="/media/order-{index}.pdf">附件</a></div></div>'
            for index, title in enumerate(titles)
        )
        session = Session([
            Response(source_url, content=listing.encode()),
            *[Response(f'https://www.tccc.gov.tw/media/order-{index}.pdf', content=b'%PDF-1.7')
              for index in range(len(titles))],
        ])
        return oc.collect_download_list(session, "S-006", date(2026, 9, 29), date(2026, 10, 4))

    def test_undated_question_orders_are_partial_not_complete_zero(self):
        result = self.collect(["臺中市議會第4屆第8次定期會委員會質詢順序表"])
        self.assertEqual(result["source_health"], "PASS")
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertEqual(result["snapshot_item_count"], 1)
        self.assertIsNone(result["items"][0]["published_at"])

    def test_undated_entry_prevents_complete_window_claim_when_other_entry_is_dated(self):
        result = self.collect([
            "臺中市議會第4屆第8次定期會質詢順序表 115年9月30日",
            "臺中市議會第4屆第8次定期會委員會質詢順序表",
        ])
        self.assertEqual(result["window_item_count"], 1)
        self.assertEqual(result["window_completeness"], "PARTIAL")

    def test_official_dates_still_establish_a_complete_window(self):
        for title, completeness, count in [
            ("臺中市議會第4屆第8次定期會質詢順序表 115年9月30日", "COMPLETE_WITH_ITEMS", 1),
            ("臺中市議會第4屆第8次定期會質詢順序表 115年9月1日", "COMPLETE_ZERO", 0),
        ]:
            with self.subTest(title=title):
                result = self.collect([title])
                self.assertEqual(result["window_completeness"], completeness)
                self.assertEqual(result["window_item_count"], count)

    def test_publication_does_not_turn_undated_attachment_or_prior_fetch_clock_into_freshness(self):
        import online_collect as oc

        collected = self.collect(["臺中市議會第4屆第8次定期會委員會質詢順序表"])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source-status.json"
            output.write_text(json.dumps({
                "schema_version": 1, "mode": "COMPETITION_DEMO",
                "sources": [{"source_id": "S-006", "data_as_of": "2026-10-04T23:26:28+08:00"}],
            }))
            with mock.patch.object(oc, "P0_SOURCES", {"S-006": oc.P0_SOURCES["S-006"]}), mock.patch.object(
                oc, "collect_source", return_value=collected
            ), contextlib.redirect_stdout(io.StringIO()):
                state = oc.build_demo_status(output, "EVENING", date(2026, 10, 4), "manual")
            source = state["sources"][0]
            feed = json.loads((Path(directory) / "intelligence-feed.json").read_text())
        self.assertIsNone(source["data_as_of"])
        self.assertLessEqual(source["last_checked_at"], state["generated_at"])
        self.assertEqual(source["freshness_status"], "NO_DATA")
        self.assertEqual(source["source_health"], "PASS")
        self.assertEqual(source["result"], "PARTIAL")
        self.assertEqual(state["latest_collection_run"]["status"], "PARTIAL")
        self.assertIn("WINDOW_PARTIAL", source["intelligence_gaps"])
        self.assertIn("NO_DATA_AS_OF", source["intelligence_gaps"])
        self.assertEqual(feed["items"][0]["eligibility"], "INELIGIBLE_BASELINE")


class ProposalDateTruthTests(unittest.TestCase):
    def collect(self, *, empty=False):
        import online_collect as oc

        payload = json.loads((ROOT / "tests/fixtures/s009-undated-front-list.json").read_text())
        payload.pop("_fixture_provenance")
        if empty:
            payload["data"].update(data=[], totalCount=0, totalPages=1)
        session = Session([Response(oc.API_S009, content=json.dumps(payload).encode())])
        result = oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4))
        self.assertEqual(len(session.calls), 1)
        return result

    @staticmethod
    def api_page(records, total_count, total_pages):
        body = {"success": True, "data": {
            "data": records, "totalCount": total_count, "totalPages": total_pages,
        }}
        return ApiResponse(
            "https://yishi.tccc.gov.tw/api/Proposal/FrontList",
            content=json.dumps(body).encode(),
        )

    @staticmethod
    def proposal(bill_id):
        return {"billId": bill_id, "proposalRationale": "公開提案測試"}

    def test_http_session_preserves_other_source_retries_but_leaves_s009_429_to_collector(self):
        import online_collect as oc

        session = oc.http_session()
        self.addCleanup(session.close)
        self.assertIn(429, session.get_adapter(oc.API_S007).max_retries.status_forcelist)
        self.assertNotIn(429, session.get_adapter(oc.API_S009).max_retries.status_forcelist)

    def test_s009_collects_all_pages_without_keyword_and_injects_page_sleep(self):
        import online_collect as oc

        session = Session([
            self.api_page([self.proposal("proposal-001")], 2, 2),
            self.api_page([self.proposal("proposal-002")], 2, 2),
        ])
        sleeps = []
        result = oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4), sleep_fn=sleeps.append)

        self.assertEqual([call[1]["params"] for call in session.calls], [
            {"pageNumber": 1, "pageSize": 200},
            {"pageNumber": 2, "pageSize": 200},
        ])
        self.assertEqual(sleeps, [1.5])
        self.assertEqual(result["snapshot_item_count"], 2)
        self.assertEqual(result["window_item_count"], 0)
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertTrue(all(item["published_at"] is None for item in result["items"]))

    def test_s009_retries_429_using_retry_after(self):
        import online_collect as oc

        for retry_after, expected_wait in (("7", 7.0), ("150", 120)):
            with self.subTest(retry_after=retry_after):
                throttled = ApiResponse(
                    oc.API_S009, status_code=429, headers={"Retry-After": retry_after}, content=b"",
                )
                session = Session([throttled, self.api_page([self.proposal("proposal-001")], 1, 1)])
                sleeps = []
                result = oc.collect_s009(
                    session, date(2026, 9, 29), date(2026, 10, 4), sleep_fn=sleeps.append,
                )

                self.assertEqual(sleeps, [expected_wait])
                self.assertEqual(len(session.calls), 2)
                self.assertEqual(result["snapshot_item_count"], 1)

    def test_s009_retry_after_accepts_http_dates_and_falls_back_on_unusable_values(self):
        import online_collect as oc

        cases = (
            ("Wed, 21 Oct 2099 07:28:00 GMT", 120),   # far future HTTP-date: capped
            ("Wed, 21 Oct 2015 07:28:00 GMT", 5),     # already past: first fallback
            ("-3", 5), ("nan", 5), ("soon", 5), (None, 5),
        )
        for retry_after, expected_wait in cases:
            with self.subTest(retry_after=retry_after):
                headers = {} if retry_after is None else {"Retry-After": retry_after}
                throttled = ApiResponse(oc.API_S009, status_code=429, headers=headers, content=b"")
                session = Session([throttled, self.api_page([self.proposal("proposal-001")], 1, 1)])
                sleeps = []
                oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4), sleep_fn=sleeps.append)
                self.assertEqual(sleeps, [expected_wait])

    def test_feed_csv_export_hashes_each_raw_item_once_and_keeps_payload_matching(self):
        import csv
        import tempfile
        from pathlib import Path
        from unittest import mock

        import online_collect as oc

        count = 400
        raw = [{"stable_key": f"bill-{index}", "payload": {"committee": f"委員會-{index}", "proposalRationale": f"<p>案由 {index}</p>"}}
               for index in range(count)]
        # A second source reuses the same stable keys; its payloads must never leak across sources.
        other = [{"stable_key": f"bill-{index}", "payload": {"committee": "其他來源"}} for index in range(3)]
        feed_items = [{"source_id": "S-009", "stable_id": f"FEED-S-009-{oc.canonical_sha256(f'S-009:bill-{index}')[:16]}",
                       "title": f"提案 {index}"} for index in reversed(range(count))]
        feed_items.append({"source_id": "S-009", "stable_id": "FEED-S-009-0000000000000000", "title": "無對應原始資料"})
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "feed-export.csv"
            with mock.patch.object(oc, "canonical_sha256", wraps=oc.canonical_sha256) as hashed:
                oc.generate_feed_csv(feed_items, {"S-009": raw, "S-004": other}, target)
            self.assertLessEqual(hashed.call_count, count + len(other))
            with target.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), count + 1)
        by_title = {row["title"]: row for row in rows}
        for index in (0, 1, count - 1):
            self.assertEqual(by_title[f"提案 {index}"]["committee"], f"委員會-{index}")
            self.assertEqual(by_title[f"提案 {index}"]["proposal_rationale"], f"案由 {index}")
        self.assertEqual(by_title["無對應原始資料"]["committee"], "")

    def test_production_verifier_reads_artifacts_up_to_the_query_store_budget(self):
        import importlib.util
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]

        def load(name, relative):
            spec = importlib.util.spec_from_file_location(name, root / relative)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        verifier = load("budget_production_verifier", "scripts/verify-query-gateway-production.py")
        store = load("budget_query_store", "scripts/query-store.py")
        self.assertEqual(verifier.MAX_BYTES, store.MAX_BYTES)

    def test_widening_the_s009_scope_rebaselines_once_and_then_reports_new_proposals(self):
        import online_collect as oc

        def run(directory, proposal_ids):
            session = Session([self.api_page([self.proposal(pid) for pid in proposal_ids], len(proposal_ids), 1)])
            collected = oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4), sleep_fn=lambda _: None)
            output = Path(directory) / "source-status.json"
            with mock.patch.object(oc, "P0_SOURCES", {"S-009": oc.P0_SOURCES["S-009"]}), mock.patch.object(
                oc, "collect_source", return_value=collected
            ), contextlib.redirect_stdout(io.StringIO()):
                state = oc.build_demo_status(output, "EVENING", date(2026, 10, 4), "manual")
            feed = json.loads((Path(directory) / "intelligence-feed.json").read_text(encoding="utf-8"))
            return state["sources"][0], {item["official_url"].rsplit("/", 1)[-1]: item for item in feed["items"]}

        with tempfile.TemporaryDirectory() as directory:
            # State left by the keyword-filtered collector: a last good snapshot without a scope label.
            _, legacy = run(directory, ["old-police-1"])
            status_path = Path(directory) / "source-status.json"
            state = json.loads(status_path.read_text(encoding="utf-8"))
            state["sources"][0]["last_known_good"].pop("collection_scope", None)
            status_path.write_text(json.dumps(state), encoding="utf-8")
            self.assertEqual(set(legacy), {"old-police-1"})

            # First run after the scope widened: everything is baseline, nothing is "new today".
            source, widened = run(directory, ["old-police-1", "old-traffic-1", "old-fire-1"])
            self.assertEqual({item["eligibility"] for item in widened.values()}, {"INELIGIBLE_BASELINE"})
            self.assertTrue(all(item["change_type"] != "NEW" for item in widened.values()))
            self.assertTrue(all("FIRST_OBSERVATION_BASELINE" in item["reason_codes"] for item in widened.values()))
            self.assertEqual(source["last_known_good"]["collection_scope"], oc.COLLECTION_SCOPES["S-009"])
            self.assertNotEqual(source["result"], "NEW_ITEM")

            # Next run under the same scope: only the genuinely new proposal is reported as new.
            source, later = run(directory, ["old-police-1", "old-traffic-1", "old-fire-1", "brand-new-1"])
            self.assertEqual(later["brand-new-1"]["change_type"], "NEW")
            self.assertTrue(all(later[pid]["change_type"] != "NEW" for pid in ("old-police-1", "old-traffic-1", "old-fire-1")))
            self.assertEqual(source["last_known_good"]["collection_scope"], oc.COLLECTION_SCOPES["S-009"])

    def test_sources_without_a_declared_scope_keep_the_original_baseline_rule(self):
        import online_collect as oc

        self.assertEqual(set(oc.COLLECTION_SCOPES), {"S-009"})

    def test_s009_stops_after_three_429_retries_without_returning_partial_data(self):
        import online_collect as oc
        import requests

        session = Session([ApiResponse(oc.API_S009, status_code=429, content=b"")] * 4)
        sleeps = []
        with self.assertRaises(requests.HTTPError):
            oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4), sleep_fn=sleeps.append)

        self.assertEqual(len(session.calls), 4)
        self.assertEqual(sleeps, [5, 15, 45])

    def test_other_paginated_sources_do_not_sleep_by_default(self):
        import online_collect as oc

        session = Session([
            self.api_page([self.proposal("record-001")], 2, 2),
            self.api_page([self.proposal("record-002")], 2, 2),
        ])
        sleeps = []
        records, _ = oc.paginated_api(
            session, oc.API_S007, {}, source_id="S-007", sleep_fn=sleeps.append,
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(sleeps, [])
        self.assertTrue(all("keywordList" not in call[1]["params"] for call in session.calls))

    def test_s009_rejects_total_count_changes_between_pages(self):
        import online_collect as oc

        session = Session([
            self.api_page([self.proposal("proposal-001")], 2, 2),
            self.api_page([self.proposal("proposal-002")], 3, 2),
        ])
        with self.assertRaisesRegex(ValueError, "inconsistent API totalCount"):
            oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4), sleep_fn=lambda _: None)
        self.assertEqual(len(session.calls), 2)

    def test_observed_undated_schema_cannot_prove_zero_items_in_date_window(self):
        result = self.collect()
        self.assertEqual(result["source_health"], "PASS")
        self.assertEqual(result["snapshot_item_count"], 1)
        self.assertEqual(result["window_item_count"], 0)
        self.assertEqual(result["window_completeness"], "PARTIAL")
        self.assertIsNone(result["items"][0]["published_at"])
        self.assertNotIn("api_confirmed_at", result)

    def test_publication_clears_legacy_fetch_date_and_preserves_unknown_date_gaps(self):
        import online_collect as oc

        for empty in (False, True):
            with self.subTest(empty=empty), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "source-status.json"
                output.write_text(json.dumps({
                    "schema_version": 1, "mode": "COMPETITION_DEMO",
                    "sources": [{"source_id": "S-009", "data_as_of": "2026-10-04T23:26:52+08:00"}],
                }))
                with mock.patch.object(oc, "P0_SOURCES", {"S-009": oc.P0_SOURCES["S-009"]}), mock.patch.object(
                    oc, "collect_source", return_value=self.collect(empty=empty)
                ), contextlib.redirect_stdout(io.StringIO()):
                    state = oc.build_demo_status(output, "EVENING", date(2026, 10, 4), "manual")
                source = state["sources"][0]
                feed = json.loads((Path(directory) / "intelligence-feed.json").read_text())
                self.assertIsNone(source["data_as_of"])
                self.assertLessEqual(source["last_checked_at"], state["generated_at"])
                self.assertEqual(source["freshness_status"], "NO_DATA")
                self.assertEqual(source["source_health"], "PASS")
                self.assertIn("NO_DATA_AS_OF", source["intelligence_gaps"])
                if empty:
                    self.assertEqual(source["window_completeness"], "COMPLETE_ZERO")
                    self.assertEqual(feed["items"], [])
                else:
                    self.assertEqual(source["result"], "PARTIAL")
                    self.assertEqual(state["latest_collection_run"]["status"], "PARTIAL")
                    self.assertIn("WINDOW_PARTIAL", source["intelligence_gaps"])
                    self.assertEqual(feed["items"][0]["eligibility"], "INELIGIBLE_BASELINE")
                    self.assertIsNone(feed["items"][0]["data_as_of"])

    def test_empty_page_cannot_establish_complete_zero_when_official_total_is_positive(self):
        import online_collect as oc

        # Both council API collectors share the count proof, including the
        # empty-page response that previously bypassed that proof.
        for source_id, collector, url in (
            ("S-007", oc.collect_s007, oc.API_S007),
            ("S-009", oc.collect_s009, oc.API_S009),
        ):
            with self.subTest(source_id=source_id):
                payload = {"success": True, "data": {"data": [], "totalPages": 1, "totalCount": 2}}
                session = Session([Response(url, content=json.dumps(payload).encode())])
                with self.assertRaisesRegex(ValueError, "API count mismatch"):
                    collector(session, date(2026, 9, 29), date(2026, 10, 4))
                self.assertEqual(len(session.calls), 1)

    def test_invalid_count_rejected_but_zero_page_empty_inventory_is_valid(self):
        import online_collect as oc

        for count in (-1, False, 0.5, "0"):
            with self.subTest(count=count):
                payload = {"success": True, "data": {"data": [], "totalPages": 1, "totalCount": count}}
                session = Session([Response(oc.API_S009, content=json.dumps(payload).encode())])
                with self.assertRaisesRegex(ValueError, "invalid API totalCount"):
                    oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4))
        payload = {"success": True, "data": {"data": [], "totalPages": 0, "totalCount": 0}}
        session = Session([Response(oc.API_S009, content=json.dumps(payload).encode())])
        result = oc.collect_s009(session, date(2026, 9, 29), date(2026, 10, 4))
        self.assertEqual(result["window_completeness"], "COMPLETE_ZERO")

    def test_failed_first_post_fix_fetch_keeps_lkg_content_with_unknown_data_time(self):
        import online_collect as oc

        collected = self.collect()
        legacy_time = "2026-10-04T23:26:52+08:00"
        legacy_lkg = {"source_run_id": "fixture-old-run", "completed_at": legacy_time,
                      "manifest_sha256": collected["manifest_sha256"], "snapshot_item_count": 1, "snapshot_count": 1}
        prior_item = oc.project_feed_item(
            item=collected["items"][0], source_id="S-009",
            source_name=oc.P0_SOURCES["S-009"][0], source_url=oc.P0_SOURCES["S-009"][1],
            source_role="PRIMARY_REFERENCE", integration_status="PRODUCTION_ACTIVE",
            freshness="FRESH", source_health="PASS", window_completeness="COMPLETE_ZERO",
            data_as_of=legacy_time, fetched_at=legacy_time, previous_sha256s=set(),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source-status.json"
            output.write_text(json.dumps({
                "schema_version": 1, "mode": "COMPETITION_DEMO",
                "sources": [{"source_id": "S-009", "data_as_of": legacy_time,
                             "last_success_at": legacy_time, "last_known_good": legacy_lkg, "snapshot_item_count": 1}],
            }))
            (Path(directory) / "intelligence-feed.json").write_text(json.dumps({"items": [prior_item]}))
            with mock.patch.object(oc, "P0_SOURCES", {"S-009": oc.P0_SOURCES["S-009"]}), mock.patch.object(
                oc, "collect_source", side_effect=ConnectionError("upstream unavailable")
            ), contextlib.redirect_stdout(io.StringIO()):
                state = oc.build_demo_status(output, "EVENING", date(2026, 10, 4), "manual")
            source = state["sources"][0]
            feed = json.loads((Path(directory) / "intelligence-feed.json").read_text())
        self.assertEqual(source["source_health"], "FAILED")
        self.assertIsNone(source["data_as_of"])
        self.assertEqual(source["freshness_status"], "NO_DATA")
        self.assertEqual(source["last_known_good"], legacy_lkg)
        self.assertEqual(source["last_success_at"], legacy_time)
        self.assertIn("NO_DATA_AS_OF", source["intelligence_gaps"])
        self.assertEqual(len(feed["items"]), 1)
        lkg_item = feed["items"][0]
        self.assertEqual(lkg_item["content_sha256"], prior_item["content_sha256"])
        self.assertEqual(lkg_item["title"], prior_item["title"])
        self.assertEqual(lkg_item["change_type"], "LKG")
        self.assertEqual(lkg_item["eligibility"], "INELIGIBLE_SOURCE_FAILED")
        self.assertIsNone(lkg_item["data_as_of"])
        self.assertEqual(lkg_item["freshness_status"], "NO_DATA")


class PopulationBackgroundTests(unittest.TestCase):
    def csv(self, *, period="112Y12M", population="100", town_id="66000180", labels=True):
        from intel_v2.population_background import FIELDS, LABELS

        header = ','.join(FIELDS) + '\n'
        description = ','.join(LABELS[field] for field in FIELDS) + '\n' if labels else ''
        row = f'66000,臺中市,{town_id},大雅區,30,{population},40,60,{period}\n'
        return (header + description + row).encode('utf-8')

    def test_exact_official_label_row_is_skipped_and_counts_are_integers(self):
        from intel_v2.population_background import parse_population_csv

        for labels in (True, False):
            rows = parse_population_csv(self.csv(labels=labels), expected_period="112Y12M")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["P_CNT"], 100)
            self.assertEqual(rows[0]["TOWN_ID"], "66000180")

    def test_dynamic_newer_period_is_not_accepted_as_requested_historical_period(self):
        from intel_v2.population_background import parse_population_csv

        with self.assertRaisesRegex(ValueError, "period mismatch"):
            parse_population_csv(self.csv(period="114Y12M"), expected_period="112Y12M")

    def test_missing_counts_duplicate_id_and_inconsistent_totals_fail_closed(self):
        from intel_v2.population_background import parse_population_csv

        csv_body = self.csv()
        invalid = [self.csv(population=""), self.csv(population="99"), self.csv(town_id="65000180"),
                   self.csv(population="NaN"), csv_body + csv_body.splitlines(keepends=True)[-1],
                   csv_body.replace('人口數'.encode(), '未知欄位'.encode(), 1)]
        for body in invalid:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    parse_population_csv(body, expected_period="112Y12M")


if __name__ == "__main__":
    unittest.main()
