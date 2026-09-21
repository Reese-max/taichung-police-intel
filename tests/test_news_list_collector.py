import unittest
from datetime import date
from unittest import mock

import online_collect as oc


def _page(body: str) -> bytes:
    return f"<html><body><ul>{body}</ul></body></html>".encode("utf-8")


POLICE_LIST = _page(
    """
    <li><a href="home.jsp?id=1&mcustomize=news_view.jsp&dataserno=202609100003">第三分局第一分隊 115-09-10 警破獲詐欺車手</a></li>
    <li><a href="home.jsp?id=1&mcustomize=news_view.jsp&dataserno=202609080003">第三分局 115-09-08 交通宣導</a></li>
    <li><a href="home.jsp?id=1&mcustomize=news_view.jsp&dataserno=202607010001">舊聞 115-07-01</a></li>
    """
)

RDEC_LIST = _page(
    """
    <li><a href="/12047/12142/12186/873338/post">第 650 次市政會議紀錄 115年9月10日</a></li>
    <li><a href="/12047/12142/12186/873320/post">第 649 次市政會議紀錄 115年8月27日</a></li>
    """
)

TRAFFIC_LIST = _page(
    """
    <li><a href="index-1.asp?Parser=9,4,20,,,,21750">大眾運輸優惠</a><span>運輸管理科 2026-09-11</span></li>
    <li><a href="index-1.asp?Parser=9,4,20,,,,21749">停車費率調整</a><span>運輸管理科 2026-09-01</span></li>
    """
)

RSS_LIST = '''<?xml version="1.0"?><rss version="2.0"><channel>
<item iCuItem="3375296"><title><![CDATA[活動公告]]></title><link>/3375296/post</link><pubDate>Mon, 21 Sep 2026 02:57:54 GMT</pubDate></item>
<item iCuItem="3372712"><title>課程公告</title><link>/3372712/post</link><pubDate>Thu, 17 Sep 2026 01:40:58 GMT</pubDate></item>
</channel></rss>'''.encode("utf-8")

DETAIL = _page("<div>detail body</div><a href='files/a.pdf'>附件</a>")


class FakeResponse:
    def __init__(self, content: bytes, url: str):
        self.content = content
        self.url = url
        self.request = mock.Mock(url=url)
        self.status_code = 200
        self.headers = {"content-type": "text/html"}

    def raise_for_status(self) -> None:
        pass


class FakeSession:
    def __init__(self, pages: dict[str, bytes]):
        self.pages = pages
        self.fetched: list[str] = []

    def get(self, url: str, timeout: int = 60, **kwargs):
        self.fetched.append(url)
        return FakeResponse(self.pages.get(url, DETAIL), url)


def _collect(cfg_id: str, list_html: bytes, existing=None, max_details=None, **extra):
    session = FakeSession({oc.NEWS_LIST_SOURCES[cfg_id]["list_url"]: list_html, **extra})
    result = oc.collect_news_list(
        session, cfg_id, date(2026, 9, 4), date(2026, 9, 11), existing, max_details
    )
    return result, session


class ParseNewsListTests(unittest.TestCase):
    def test_source_patterns_extract_stable_ids_and_dates(self):
        cases = (
            ("S-001", POLICE_LIST, ["202609100003", "202609080003", "202607010001"]),
            ("S-019", RDEC_LIST, ["873338", "873320"]),
            ("S-032", TRAFFIC_LIST, ["21750", "21749"]),
        )
        for source_id, html, expected in cases:
            with self.subTest(source_id=source_id):
                entries = oc.parse_news_list(
                    html,
                    oc.NEWS_LIST_SOURCES[source_id]["list_url"],
                    oc.NEWS_LIST_SOURCES[source_id]["id_pattern"],
                )
                self.assertEqual([entry["stable_key"] for entry in entries], expected)
                self.assertEqual(entries[0]["published"], date(2026, 9, 10) if source_id != "S-032" else date(2026, 9, 11))

    def test_rss_parser_extracts_stable_ids_and_rfc822_dates(self):
        entries = oc.parse_news_rss(RSS_LIST, "https://www.news.taichung.gov.tw/feed")
        self.assertEqual([entry["stable_key"] for entry in entries], ["3375296", "3372712"])
        self.assertEqual(entries[0]["published"], date(2026, 9, 21))
        self.assertEqual(entries[0]["detail_url"], "https://www.news.taichung.gov.tw/3375296/post")

    def test_rss_parser_fails_closed_on_missing_date(self):
        broken = RSS_LIST.replace(b"<pubDate>Thu, 17 Sep 2026 01:40:58 GMT</pubDate>", b"")
        with self.assertRaises(ValueError):
            oc.parse_news_rss(broken, "https://www.news.taichung.gov.tw/feed")

    def test_unparseable_list_raises_not_zero(self):
        with self.assertRaises(ValueError):
            oc.parse_news_list(_page("<li>no links</li>"), "https://x.test/", r"dataserno=(\d+)")


class ListFirstGatingTests(unittest.TestCase):
    def test_unchanged_items_skip_detail_fetch(self):
        detail_url = "https://www.police.taichung.gov.tw/home.jsp?id=1&mcustomize=news_view.jsp&dataserno=202609100003"
        first, session = _collect("S-001", POLICE_LIST, **{detail_url: DETAIL})
        self.assertEqual(len([url for url in session.fetched if "news_view" in url]), 3)
        existing = {item["stable_key"]: item for item in first["items"]}
        second, session2 = _collect("S-001", POLICE_LIST, existing=existing)
        self.assertEqual(session2.fetched, [oc.NEWS_LIST_SOURCES["S-001"]["list_url"]])
        self.assertTrue(all(item["payload"]["detail"] == "unchanged-skipped" for item in second["items"]))

    def test_changed_list_row_fetches_detail(self):
        first, _ = _collect("S-019", RDEC_LIST)
        existing = {item["stable_key"]: item for item in first["items"]}
        changed = _page(
            """
            <li><a href="/12047/12142/12186/873338/post">第 650 次市政會議紀錄（修正） 115年9月10日</a></li>
            <li><a href="/12047/12142/12186/873320/post">第 649 次市政會議紀錄 115年8月27日</a></li>
            """
        )
        detail_url = "https://www.rdec.taichung.gov.tw/12047/12142/12186/873338/post"
        second, session = _collect("S-019", changed, existing=existing, **{detail_url: DETAIL})
        self.assertEqual(session.fetched, [oc.NEWS_LIST_SOURCES["S-019"]["list_url"], detail_url])
        changed_item = next(item for item in second["items"] if item["stable_key"] == "873338")
        self.assertEqual(changed_item["payload"]["detail"], "fetched")
        self.assertIn("body_sha256", changed_item["payload"])

    def test_canary_detail_cap(self):
        result, session = _collect("S-001", POLICE_LIST, max_details=1)
        self.assertEqual(len([url for url in session.fetched if "news_view" in url]), 1)
        self.assertEqual(
            sum(item["payload"]["detail"] == "skipped-detail-cap" for item in result["items"]),
            2,
        )

    def test_collect_source_accepts_bounded_canary_option(self):
        session = FakeSession({oc.NEWS_LIST_SOURCES["S-001"]["list_url"]: POLICE_LIST})
        result = oc.collect_source(
            session, "S-001", date(2026, 9, 4), date(2026, 9, 11), {}, max_details=1
        )
        self.assertEqual(result["source_health"], "PASS")
        self.assertEqual(len([url for url in session.fetched if "news_view" in url]), 1)

    def test_window_completeness_requires_boundary_evidence(self):
        result, _ = _collect("S-001", POLICE_LIST)
        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")
        self.assertEqual(result["window_item_count"], 2)

    def test_recent_only_and_non_monotonic_lists_are_partial(self):
        recent_only = _page(
            """
            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=1">115-09-10 A</a></li>
            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=2">115-09-08 B</a></li>
            """
        )
        unordered = _page(
            """
            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=1">115-09-08 A</a></li>
            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=2">115-09-10 B</a></li>
            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=3">115-07-01 C</a></li>
            """
        )
        self.assertEqual(_collect("S-001", recent_only)[0]["window_completeness"], "PARTIAL")
        self.assertEqual(_collect("S-001", unordered)[0]["window_completeness"], "PARTIAL")


class RocDateTests(unittest.TestCase):
    def test_roc_formats(self):
        self.assertEqual(oc.roc_date("115-09-10"), date(2026, 9, 10))
        self.assertEqual(oc.roc_date("115年9月10日"), date(2026, 9, 10))
        self.assertEqual(oc.roc_date("115.07.27"), date(2026, 7, 27))
        self.assertIsNone(oc.roc_date("沒有日期"))

    def test_gregorian_dates_are_not_reinterpreted_as_roc(self):
        self.assertEqual(oc.roc_date("2026-09-16"), date(2026, 9, 16))
        self.assertEqual(oc.roc_date("2026/09/16"), date(2026, 9, 16))
        self.assertEqual(oc.roc_date("2026年9月16日"), date(2026, 9, 16))

    def test_invalid_dates_fail_closed(self):
        self.assertIsNone(oc.roc_date("2026-13-40"))
        self.assertIsNone(oc.roc_date("115-02-30"))


if __name__ == "__main__":
    unittest.main()
