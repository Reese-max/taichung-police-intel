#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
collector_path = ROOT / "online_collect.py"
test_path = ROOT / "tests" / "test_news_list_collector.py"

collector = collector_path.read_text(encoding="utf-8")
tests = test_path.read_text(encoding="utf-8")

old = '''def roc_date(value: str) -> date | None:\n    match = re.search(r"(\\d{2,3})\\s*[-/.年]\\s*(\\d{1,2})\\s*[-/.月]\\s*(\\d{1,2})\\s*日?", value)\n    if not match:\n        return None\n    year, month, day = map(int, match.groups())\n    return date(year + 1911, month, day)\n'''
new = '''def roc_date(value: str) -> date | None:\n    # Parse an explicit four-digit Gregorian date first.  The ROC matcher is\n    # bounded so it can never start in the middle of 2026 and interpret 026.\n    gregorian = re.search(\n        r"(?<!\\d)(\\d{4})\\s*[-/.年]\\s*(\\d{1,2})\\s*[-/.月]\\s*(\\d{1,2})\\s*日?(?!\\d)",\n        value,\n    )\n    if gregorian:\n        year, month, day = map(int, gregorian.groups())\n        if not 1912 <= year <= 2200:\n            return None\n        try:\n            return date(year, month, day)\n        except ValueError:\n            return None\n\n    roc = re.search(\n        r"(?<!\\d)(\\d{2,3})\\s*[-/.年]\\s*(\\d{1,2})\\s*[-/.月]\\s*(\\d{1,2})\\s*日?(?!\\d)",\n        value,\n    )\n    if not roc:\n        return None\n    year, month, day = map(int, roc.groups())\n    if not 1 <= year <= 289:\n        return None\n    try:\n        return date(year + 1911, month, day)\n    except ValueError:\n        return None\n'''
if old not in collector:
    raise SystemExit("roc_date target not found")
collector = collector.replace(old, new, 1)

old = '''    dated = [date.fromisoformat(item["published_at"][:10]) for item in items if item["published_at"]]\n    window_items = [item for item in items if item["published_at"] and start <= date.fromisoformat(item["published_at"][:10]) <= end]\n    if window_items:\n        completeness = "COMPLETE_WITH_ITEMS"\n    elif dated and max(dated) < start:\n        completeness = "COMPLETE_ZERO"\n    else:\n        completeness = "PARTIAL"\n'''
new = '''    dated = [date.fromisoformat(item["published_at"][:10]) for item in items if item["published_at"]]\n    window_items = [item for item in items if item["published_at"] and start <= date.fromisoformat(item["published_at"][:10]) <= end]\n    # A single list page is not automatically a complete date window.  We can\n    # prove coverage only when the dated rows are reverse-chronological and the\n    # page reaches strictly before the requested window start.  Otherwise a\n    # second page could still contain additional in-window items, so fail closed\n    # as PARTIAL until source-specific pagination is enumerated.\n    reverse_chronological = all(left >= right for left, right in zip(dated, dated[1:]))\n    reaches_before_window = bool(dated) and min(dated) < start\n    if window_items and reverse_chronological and reaches_before_window:\n        completeness = "COMPLETE_WITH_ITEMS"\n    elif dated and reverse_chronological and max(dated) < start:\n        completeness = "COMPLETE_ZERO"\n    else:\n        completeness = "PARTIAL"\n'''
if old not in collector:
    raise SystemExit("news completeness target not found")
collector = collector.replace(old, new, 1)

old = '''def collect_source(\n    session: requests.Session,\n    source_id: str,\n    start: date,\n    end: date,\n    existing: dict[str, dict] | None = None,\n) -> dict:\n    collector = COLLECTORS[source_id]\n    if collector is collect_download_list:\n        return collector(session, source_id, start, end)\n    if collector is collect_news_list:\n        return collector(session, source_id, start, end, existing)\n    return collector(session, start, end)\n'''
new = '''def collect_source(\n    session: requests.Session,\n    source_id: str,\n    start: date,\n    end: date,\n    existing: dict[str, dict] | None = None,\n    *,\n    max_details: int | None = None,\n) -> dict:\n    collector = COLLECTORS[source_id]\n    if collector is collect_download_list:\n        return collector(session, source_id, start, end)\n    if collector is collect_news_list:\n        return collector(session, source_id, start, end, existing, max_details=max_details)\n    if max_details is not None:\n        raise ValueError(f"max_details is only valid for list-news sources: {source_id}")\n    return collector(session, start, end)\n'''
if old not in collector:
    raise SystemExit("collect_source target not found")
collector = collector.replace(old, new, 1)

old = '''    def test_canary_detail_cap(self):\n        result, session = _collect("S-001", POLICE_LIST, max_details=1)\n        detail_fetches = [u for u in session.fetched if "news_view" in u]\n        self.assertEqual(len(detail_fetches), 1)\n        capped = [i for i in result["items"] if i["payload"]["detail"] == "skipped-detail-cap"]\n        self.assertEqual(len(capped), 2)\n\n    def test_window_completeness(self):\n'''
new = '''    def test_canary_detail_cap(self):\n        result, session = _collect("S-001", POLICE_LIST, max_details=1)\n        detail_fetches = [u for u in session.fetched if "news_view" in u]\n        self.assertEqual(len(detail_fetches), 1)\n        capped = [i for i in result["items"] if i["payload"]["detail"] == "skipped-detail-cap"]\n        self.assertEqual(len(capped), 2)\n\n    def test_collect_source_accepts_bounded_canary_option(self):\n        session = FakeSession({oc.NEWS_LIST_SOURCES["S-001"]["list_url"]: POLICE_LIST})\n        result = oc.collect_source(\n            session, "S-001", date(2026, 9, 4), date(2026, 9, 11), {}, max_details=1\n        )\n        self.assertEqual(result["source_health"], "PASS")\n        self.assertEqual(len([u for u in session.fetched if "news_view" in u]), 1)\n\n    def test_window_completeness(self):\n'''
if old not in tests:
    raise SystemExit("canary test insertion target not found")
tests = tests.replace(old, new, 1)

old = '''    def test_window_completeness(self):\n        result, _ = _collect("S-001", POLICE_LIST)\n        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")\n        self.assertEqual(result["window_item_count"], 2)  # 09-10, 09-08 在窗內；07-01 不在\n'''
new = '''    def test_window_completeness(self):\n        result, _ = _collect("S-001", POLICE_LIST)\n        self.assertEqual(result["window_completeness"], "COMPLETE_WITH_ITEMS")\n        self.assertEqual(result["window_item_count"], 2)  # 09-10, 09-08 在窗內；07-01 證明頁面跨過窗口起點\n\n    def test_single_page_that_does_not_reach_before_window_is_partial(self):\n        recent_only = _page(\n            """\n            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=1">115-09-10 A</a></li>\n            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=2">115-09-08 B</a></li>\n            """\n        )\n        result, _ = _collect("S-001", recent_only)\n        self.assertEqual(result["window_completeness"], "PARTIAL")\n\n    def test_non_monotonic_list_dates_fail_closed_as_partial(self):\n        unordered = _page(\n            """\n            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=1">115-09-08 A</a></li>\n            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=2">115-09-10 B</a></li>\n            <li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=3">115-07-01 C</a></li>\n            """\n        )\n        result, _ = _collect("S-001", unordered)\n        self.assertEqual(result["window_completeness"], "PARTIAL")\n'''
if old not in tests:
    raise SystemExit("window test target not found")
tests = tests.replace(old, new, 1)

old = '''class RocDateTests(unittest.TestCase):\n    def test_hyphen_and_cjk_formats(self):\n        self.assertEqual(oc.roc_date("115-09-10"), date(2026, 9, 10))\n        self.assertEqual(oc.roc_date("115年9月10日"), date(2026, 9, 10))\n        self.assertEqual(oc.roc_date("115.07.27"), date(2026, 7, 27))\n        self.assertIsNone(oc.roc_date("沒有日期"))\n'''
new = '''class RocDateTests(unittest.TestCase):\n    def test_hyphen_and_cjk_formats(self):\n        self.assertEqual(oc.roc_date("115-09-10"), date(2026, 9, 10))\n        self.assertEqual(oc.roc_date("115年9月10日"), date(2026, 9, 10))\n        self.assertEqual(oc.roc_date("115.07.27"), date(2026, 7, 27))\n        self.assertIsNone(oc.roc_date("沒有日期"))\n\n    def test_gregorian_dates_are_not_reinterpreted_as_roc(self):\n        self.assertEqual(oc.roc_date("2026-09-16"), date(2026, 9, 16))\n        self.assertEqual(oc.roc_date("2026/09/16"), date(2026, 9, 16))\n        self.assertEqual(oc.roc_date("2026年9月16日"), date(2026, 9, 16))\n\n    def test_invalid_dates_fail_closed(self):\n        self.assertIsNone(oc.roc_date("2026-13-40"))\n        self.assertIsNone(oc.roc_date("115-02-30"))\n'''
if old not in tests:
    raise SystemExit("date test target not found")
tests = tests.replace(old, new, 1)

collector_path.write_text(collector, encoding="utf-8")
test_path.write_text(tests, encoding="utf-8")
print("ISSUE22_PATCH_APPLIED")
