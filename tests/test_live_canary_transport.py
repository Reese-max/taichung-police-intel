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
    def __init__(self, url, *, status_code=200, location=None, content=b"ok"):
        self.url = url
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self.content = content
        self.request = SimpleNamespace(url=url)
        self.closed = False

    def close(self):
        self.closed = True

    def raise_for_status(self):
        return None


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
        self.assertEqual(source["last_checked_at"], state["generated_at"])
        self.assertEqual(source["freshness_status"], "NO_DATA")
        self.assertEqual(source["source_health"], "PASS")
        self.assertEqual(source["result"], "PARTIAL")
        self.assertEqual(state["latest_collection_run"]["status"], "PARTIAL")
        self.assertIn("WINDOW_PARTIAL", source["intelligence_gaps"])
        self.assertIn("NO_DATA_AS_OF", source["intelligence_gaps"])
        self.assertEqual(feed["items"][0]["eligibility"], "INELIGIBLE_PARTIAL")


if __name__ == "__main__":
    unittest.main()
