import contextlib
import hashlib
import io
import json
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

import online_collect as oc


FIXTURES = Path(__file__).parent / "fixtures"
PDF_URL = "https://www.tccc.gov.tw/pic/DocCouncil/17851627092858(2).pdf"


def api_page(records, *, total_count=None):
    return {"success": True, "data": {
        "data": records, "totalPages": 1 if records else 0,
        "totalCount": len(records) if total_count is None else total_count,
    }}


class Response:
    def __init__(self, url, content, content_type="application/json"):
        self.url = url
        self.request = SimpleNamespace(url=url)
        self.status_code = 200
        self.content = content
        self.headers = {"content-type": content_type}

    def json(self):
        return json.loads(self.content)

    def raise_for_status(self):
        pass


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class OfficialSourceDateTests(unittest.TestCase):
    def revision_pdf(self):
        body = (FIXTURES / "s006-synthetic-revision-date.pdf").read_bytes()
        provenance = json.loads((FIXTURES / "s006-synthetic-revision-date.provenance.json").read_text())
        self.assertEqual(hashlib.sha256(body).hexdigest(), provenance["fixture_sha256"])
        return body

    def collect_question_order(self):
        listing_url = oc.P0_SOURCES["S-006"][1]
        listing = ('<meta charset="utf-8"><div id="Fdownload_list">'
                   '<div class="text02_1">臺中市議會第4屆第8次定期會警消環衛委員會業務質詢順序表</div>'
                   f'<div class="text02_2"><a href="{PDF_URL}">PDF</a></div></div>').encode()
        session = Session([
            Response(listing_url, listing, "text/html"),
            Response(PDF_URL, self.revision_pdf(), "application/pdf"),
        ])
        result = oc.collect_download_list(session, "S-006", date(2026, 8, 25), date(2026, 8, 29))
        self.assertEqual(len(session.calls), 2)  # Parse the already fetched bytes.
        return result

    def test_pdf_revision_header_is_not_the_later_meeting_date_or_first_publication(self):
        result = self.collect_question_order()
        item = result["items"][0]
        self.assertIsNone(item["published_at"])
        self.assertEqual(item["document_revision_at"], "2026-08-27T00:00:00+08:00")
        self.assertEqual(item["date_basis"], "OFFICIAL_DOCUMENT_REVISION_DATE")
        evidence = item["date_evidence"]
        self.assertEqual(evidence["official_url"], PDF_URL)
        self.assertEqual(evidence["content_sha256"], hashlib.sha256(self.revision_pdf()).hexdigest())
        self.assertIn("115.08.27", evidence["excerpt"])
        self.assertIn("修正", evidence["excerpt"])
        self.assertEqual(result["window_item_count"], 1)
        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")

    def test_unlabelled_meeting_dates_are_not_revision_dates(self):
        page = mock.Mock()
        page.extract_text.return_value = "質詢日期 115.08.31\n議員姓名\n115.08.27 第13次會議"
        with mock.patch.object(oc, "PdfReader", return_value=SimpleNamespace(is_encrypted=False, pages=[page])):
            self.assertIsNone(oc.attachment_revision_evidence(b"%PDF-1.7", PDF_URL))

    def test_malformed_or_oversized_pdf_stays_undated(self):
        self.assertIsNone(oc.attachment_revision_evidence(b"%PDF-1.7 malformed", PDF_URL))
        with mock.patch.object(oc, "PdfReader") as reader:
            self.assertIsNone(oc.attachment_revision_evidence(b"%PDF-1.7" + b"x" * (8 * 1024 * 1024), PDF_URL))
            reader.assert_not_called()

    def test_explicit_offset_is_converted_without_changing_the_instant(self):
        self.assertEqual(oc.official_record_timestamp("2026-05-26T17:12:00Z"), "2026-05-27T01:12:00+08:00")
        self.assertEqual(oc.official_record_timestamp("2026-05-27T01:12:00+08:00"), "2026-05-27T01:12:00+08:00")
        self.assertEqual(oc.official_record_timestamp("2026-05-27T01:12:00"), "2026-05-27T01:12:00+08:00")

    def test_probe_uses_max_observed_date_without_assuming_first_row_sort(self):
        session = Session([
            Response(oc.API_S007, json.dumps(api_page([])).encode()),
            Response(oc.API_S007, json.dumps(api_page([
                {"date": "2026-05-26T09:00:00+08:00"},
                {"date": "2026-05-27T01:12:00Z"},
            ])).encode()),
        ])
        result = oc.collect_s007(session, date(2026, 9, 29), date(2026, 10, 5))
        self.assertEqual(result["latest_record_date"], "2026-05-27T09:12:00+08:00")
        self.assertEqual(result["latest_record_date_scope"], "OBSERVED_API_PAGE")
        self.assertEqual(session.calls[1][1]["params"]["pageSize"], 20)
        self.assertEqual(result["window_completeness"], "COMPLETE_ZERO")
        self.assertEqual(oc.freshness_status(result["latest_record_date"], datetime(2026, 10, 5, tzinfo=oc.TZ), *oc.SOURCE_FRESHNESS_POLICY["S-007"]), "STALE")

    def test_window_record_preserves_an_explicit_utc_offset(self):
        record = {"proceedingsId": "fixture-id", "speaker": "fixture",
                  "content": "警察局測試", "date": "2026-09-28T17:12:00Z"}
        session = Session([
            Response(oc.API_S007, json.dumps(api_page([record])).encode()),
            Response(oc.API_S007, json.dumps(api_page([])).encode()),
        ])
        result = oc.collect_s007(session, date(2026, 9, 29), date(2026, 10, 5))
        self.assertEqual(result["items"][0]["published_at"], "2026-09-29T01:12:00+08:00")
        self.assertEqual(result["items"][0]["date_basis"], "OFFICIAL_API_RECORD_DATE")

    def publish(self, collected, *, previous=None, error=None):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source-status.json"
            if previous:
                output.write_text(json.dumps({"schema_version": 1, "mode": "COMPETITION_DEMO", "sources": [previous]}))
            with mock.patch.object(oc, "P0_SOURCES", {"S-006": oc.P0_SOURCES["S-006"]}), mock.patch.object(
                oc, "collect_source", side_effect=error, return_value=collected
            ), contextlib.redirect_stdout(io.StringIO()):
                state = oc.build_demo_status(output, "EVENING", date(2026, 10, 5), "manual")
            feed = json.loads((Path(directory) / "intelligence-feed.json").read_text())
        return state["sources"][0], feed

    def test_revision_evidence_survives_publication_without_inventing_published_at(self):
        collected = self.collect_question_order()
        source, feed = self.publish(collected)
        self.assertEqual(source["data_as_of"], "2026-08-27T00:00:00+08:00")
        self.assertEqual(source["data_as_of_basis"], "OFFICIAL_DOCUMENT_REVISION_DATE")
        self.assertEqual(source["data_as_of_scope"], "LATEST_EVIDENCED_DOCUMENT_VERSION")
        self.assertEqual(source["data_as_of_evidence"]["official_url"], PDF_URL)
        self.assertIsNone(feed["items"][0]["published_at"])
        self.assertEqual(feed["items"][0]["document_revision_at"], source["data_as_of"])
        self.assertEqual(feed["items"][0]["date_evidence"], source["data_as_of_evidence"])
        self.assertIn("excerpt", collected["items"][0]["date_evidence"])
        self.assertNotIn("excerpt", source["data_as_of_evidence"])
        self.assertNotIn("excerpt", feed["items"][0]["date_evidence"])
        self.assertEqual(set(source["data_as_of_evidence"]), {
            "date_basis", "document_revision_at", "official_url", "content_sha256", "page_number",
        })

    def test_failed_or_empty_fetch_clears_a_legacy_question_order_fetch_clock(self):
        previous = {"source_id": "S-006", "data_as_of": "2026-10-05T22:00:00+08:00"}
        empty = {"items": [], "manifest_sha256": "fixture", "window_item_count": 0,
                 "window_completeness": "COMPLETE_ZERO", "snapshot_item_count": 0,
                 "snapshots": [], "source_health": "PASS"}
        for error in (None, ConnectionError("upstream unavailable")):
            with self.subTest(error=bool(error)):
                source, _ = self.publish(empty, previous=previous, error=error)
                self.assertIsNone(source["data_as_of"])
                self.assertEqual(source["freshness_status"], "NO_DATA")

    def test_failed_fetch_preserves_a_previously_evidenced_revision_date(self):
        source, _ = self.publish(self.collect_question_order())
        failed, _ = self.publish(None, previous=source, error=ConnectionError("upstream unavailable"))
        self.assertEqual(failed["source_health"], "FAILED")
        self.assertEqual(failed["data_as_of"], source["data_as_of"])
        self.assertEqual(failed["data_as_of_basis"], "OFFICIAL_DOCUMENT_REVISION_DATE")
        self.assertEqual(failed["data_as_of_evidence"], source["data_as_of_evidence"])
        self.assertEqual(failed["last_known_good"], source["last_known_good"])

    def test_failed_fetch_strips_an_old_raw_excerpt_from_retained_public_evidence(self):
        source, _ = self.publish(self.collect_question_order())
        source["data_as_of_evidence"]["excerpt"] = "115.08.27 第13次修正"
        source["data_as_of_evidence"]["unknown_text_field"] = "not public metadata"
        failed, _ = self.publish(None, previous=source, error=ConnectionError("upstream unavailable"))
        self.assertEqual(failed["data_as_of"], source["data_as_of"])
        self.assertNotIn("excerpt", failed["data_as_of_evidence"])
        self.assertNotIn("unknown_text_field", failed["data_as_of_evidence"])


if __name__ == "__main__":
    unittest.main()
