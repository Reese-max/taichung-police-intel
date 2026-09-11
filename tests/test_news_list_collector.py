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
    <tr><td>115-09-11</td><td><a href="../news/index.asp?Parser=9,4,632">大眾運輸優惠</a></td></tr>
    <tr><td>115-09-01</td><td><a href="../news/index.asp?Parser=9,4,631">停車費率調整</a></td></tr>
    """
)

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
        # 未註冊的 URL 視為明細頁——fetched 清單仍是 gating 的驗證依據
        return FakeResponse(self.pages.get(url, DETAIL), url)


def _collect(cfg_id: str, list_html: bytes, existing=None, max_details=None, **extra):
    session = FakeSession({oc.NEWS_LIST_SOURCES[cfg_id]["list_url"]: list_html, **extra})
    result = oc.collect_news_list(
        session, cfg_id, date(2026, 9, 4), date(2026, 9, 11), existing, max_details
    )
    return result, session


class ParseNewsListTests(unittest.TestCase):
    def test_dataserno_pattern(self):
        entries = oc.parse_news_list(POLICE_LIST, "https://www.police.taichung.gov.tw/", r"dataserno=(\d+)")
        keys = [e["stable_key"] for e in entries]
        self.assertEqual(keys, ["202609100003", "202609080003", "202607010001"])
        self.assertEqual(entries[0]["published"], date(2026, 9, 10))

    def test_post_pattern(self):
        entries = oc.parse_news_list(RDEC_LIST, "https://www.rdec.taichung.gov.tw", r"/(\d+)/post\b")
        self.assertEqual([e["stable_key"] for e in entries], ["873338", "873320"])
        self.assertEqual(entries[0]["published"], date(2026, 9, 10))

    def test_parser_pattern(self):
        entries = oc.parse_news_list(TRAFFIC_LIST, "https://www.traffic.taichung.gov.tw/news/", r"Parser=9,4,(\d+)")
        self.assertEqual([e["stable_key"] for e in entries], ["632", "631"])
        self.assertEqual(entries[0]["published"], date(2026, 9, 11))

    def test_unparseable_list_raises_not_zero(self):
        with self.assertRaises(ValueError):
            oc.parse_news_list(_page("<li>no links</li>"), "https://x.test/", r"dataserno=(\d+)")


class ListFirstGatingTests(unittest.TestCase):
    def test_unchanged_items_skip_detail_fetch(self):
        detail_url = "https://www.police.taichung.gov.tw/home.jsp?id=1&mcustomize=news_view.jsp&dataserno=202609100003"
        # 第一次跑：全數是新 item，明細都抓
        first, session = _collect(
            "S-001", POLICE_LIST,
            **{detail_url: DETAIL},
        )
        detail_fetches = [u for u in session.fetched if "news_view" in u]
        self.assertEqual(len(detail_fetches), 3)
        self.assertEqual(first["source_health"], "PASS")

        # 第二次跑：existing 與列表列一致 → 不抓任何明細
        existing = {item["stable_key"]: item for item in first["items"]}
        second, session2 = _collect("S-001", POLICE_LIST, existing=existing)
        self.assertEqual(session2.fetched, [oc.NEWS_LIST_SOURCES["S-001"]["list_url"]])
        self.assertEqual(len(second["items"]), 3)
        for item in second["items"]:
            self.assertEqual(item["payload"]["detail"], "unchanged-skipped")
            self.assertEqual(item["content_sha256"], existing[item["stable_key"]]["content_sha256"])

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
        changed_item = next(i for i in second["items"] if i["stable_key"] == "873338")
        self.assertEqual(changed_item["payload"]["detail"], "fetched")
        self.assertIn("body_sha256", changed_item["payload"])

    def test_canary_detail_cap(self):
        result, session = _collect("S-001", POLICE_LIST, max_details=1)
        detail_fetches = [u for u in session.fetched if "news_view" in u]
        self.assertEqual(len(detail_fetches), 1)
        capped = [i for i in result["items"] if i["payload"]["detail"] == "skipped-detail-cap"]
        self.assertEqual(len(capped), 2)

    def test_window_completeness(self):
        result, _ = _collect("S-001", POLICE_LIST)
        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")
        self.assertEqual(result["window_item_count"], 2)  # 09-10, 09-08 在窗內；07-01 不在


class RocDateTests(unittest.TestCase):
    def test_hyphen_and_cjk_formats(self):
        self.assertEqual(oc.roc_date("115-09-10"), date(2026, 9, 10))
        self.assertEqual(oc.roc_date("115年9月10日"), date(2026, 9, 10))
        self.assertEqual(oc.roc_date("115.07.27"), date(2026, 7, 27))
        self.assertIsNone(oc.roc_date("沒有日期"))


if __name__ == "__main__":
    unittest.main()
