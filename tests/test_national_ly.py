import contextlib
import copy
from hashlib import sha256
import importlib.util
import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from intel_v2 import http_fetch
from national.adapters.ly import Ly20Adapter, Ly6Adapter
from national.loader import load, RECORD_WRITE_ESTIMATE, SOURCE_WRITE_ESTIMATE
from national.store import ROOT, RUN_WRITE_RESERVE, SqliteStore
from tests.test_http_fetch import response

REGISTRY = json.loads((ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))
SOURCES = {s["national_source_id"]: s for s in REGISTRY["sources"]}


class NationalLyTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteStore(":memory:")
        self.store.apply_schema()
        self.addCleanup(self.store.close)
        for target, kwargs in (
                ("requests.sessions.Session.request", dict(side_effect=AssertionError("network forbidden"))),
                ("intel_v2.http_fetch.time.sleep", {}),
                ("intel_v2.http_fetch._last_request", dict(new={}))):
            mock = patch(target, **kwargs)
            mock.start()
            self.addCleanup(mock.stop)

    def adapter(self, dataset, periods=None):
        sid = "N-LY-ID20-BILLS" if dataset == 20 else "N-LY-ID6-INTERP"
        source = copy.deepcopy(SOURCES[sid])
        fixture = (ROOT / f"tests/fixtures/national/ly{dataset}-api.json").read_bytes()
        self.assertTrue(fixture.startswith(b"\n\n  {"))
        periods = {"10-01": fixture} if periods is None else periods
        session = Mock(headers={})

        def get(url, **kwargs):
            parsed = urlsplit(url)
            self.assertEqual(parsed.hostname, "data.ly.gov.tw", "ppg must never receive requests")
            if parsed.path == "/robots.txt":
                return response(url=url, body=b"User-agent: *\nDisallow:\n")
            self.assertEqual(parsed.path, f"/odw/ID{dataset}Action.action")
            query = parse_qs(parsed.query)
            self.assertEqual(query["page"], ["1"])
            self.assertEqual(query["limit"], ["20000"])
            self.assertEqual(query["fileType"], ["json"])
            self.assertRegex(query["sessionPeriod"][0], r"^0[1-8]$")
            key = query["term"][0] + "-" + query["sessionPeriod"][0]
            payload = periods.get(key, b' \n\n {"dataList": []}')
            if not isinstance(payload, bytes):
                payload = b" \n\n " + json.dumps(payload, ensure_ascii=False).encode("utf-8")
            return response(url=url, body=payload)

        session.get.side_effect = get
        adapter = (Ly20Adapter if dataset == 20 else Ly6Adapter)(source, session=session)
        return source, adapter, session

    def collect(self, source, adapter, cursor=None):
        records = []
        while True:
            page, cursor = adapter.fetch(source, cursor)
            records.extend(page)
            if cursor is None:
                return records

    def bill(self, term, period, bill, meeting="1", title="測試議案"):
        return dict(term=str(term), sessionPeriod=f"{period:02d}", sessionTimes=meeting,
                    billNo=bill, billName=title, billOrg="立法院")

    def test_mapping_dedup_whitespace_transport_and_two_sqlite_syncs(self):
        for dataset, count in ((20, 2), (6, 5)):
            source, adapter, session = self.adapter(dataset)
            with patch.object(http_fetch, "get", wraps=http_fetch.get) as transport:
                first = load(self.store, source, adapter, runner="github-actions")
            self.assertEqual((first["status"], first["changed"], first["fetched"]), ("OK", count, count), first)
            self.assertEqual(transport.call_count, 10)
            for call in transport.call_args_list:
                self.assertEqual(call.kwargs["max_bytes"], 8 * 1024 * 1024)
                self.assertEqual(call.kwargs["min_interval_seconds"], 3)
            self.assertEqual(session.get.call_count, 11)  # robots + term 10 01-08 + term 11 01-02 (not convened)
            source, fresh, _ = self.adapter(dataset)
            second = load(self.store, source, fresh, runner="github-actions")
            self.assertEqual((second["status"], second["changed"], second["rows_written"]), ("OK", 0, 0), second)
            rows = self.store.query("SELECT * FROM records WHERE source_id='" + source["national_source_id"] + "' ORDER BY id")
            self.assertTrue(all(row["event_date"] is None for row in rows))
            self.assertTrue(all(row["jurisdiction"] == "立法院" for row in rows))
            self.assertNotIn("禁止儲存", json.dumps(rows, ensure_ascii=False))
            self.assertNotIn("forbidden.doc", json.dumps(rows))
            if dataset == 20:
                self.assertEqual(rows[0]["title"], "同次會議最後版本")
                self.assertEqual(rows[0]["case_number"], "第10屆第1會期 B100")
                self.assertEqual(rows[0]["authority"], "內政委員會")
                self.assertEqual(rows[0]["official_url"], "https://ppg.ly.gov.tw/ppg/bills/B100.pdf")
                self.assertEqual(rows[1]["official_url"], "https://data.ly.gov.tw/getds.action?id=20")
                self.assertEqual(first["warnings"], [])
            else:
                self.assertEqual(rows[0]["case_number"], "第10屆第1會期第2次會議")
                self.assertEqual(first["warnings"], [f"EMPTY_PERIOD term=10 sessionPeriod={p:02d}" for p in range(2, 9)])

    def test_id6_ids_header_filter_and_exact_full_item_hash(self):
        source, adapter, _ = self.adapter(6)
        records = self.collect(source, adapter)
        ids = [r["external_id"] for r in records]
        self.assertEqual(ids, [r["external_id"] for r in self.collect(source, adapter)])
        self.assertEqual(len(set(ids)), 5)
        expected = "ly6-" + sha256("10|01|2|請改善全國通學道路並補足偏鄉交通資源".encode()).hexdigest()[:24]
        self.assertEqual(ids[:3], [expected, expected + "-2", expected + "-3"])
        self.assertTrue(records[3]["title"].startswith("乙、"))
        self.assertEqual(records[4]["title"], "短質詢仍保留")
        item = "長" * 301
        source, adapter, _ = self.adapter(6, {"10-01": {"dataList": [dict(term="10", sessionPeriod="01", sessionTimes="1", item=item)]}})
        record = self.collect(source, adapter)[0]
        self.assertEqual(record["title"], item[:300])
        self.assertEqual(record["external_id"], "ly6-" + sha256(("10|01|1|" + item).encode()).hexdigest()[:24])

    def test_global_bill_winners_and_bounds(self):
        periods = {"10-01": {"dataList": [self.bill(10, 1, "A", "12"), self.bill(10, 1, "B", "2")]},
                   "10-02": {"dataList": [self.bill(10, 2, "A", "3"), self.bill(10, 2, "B", "2", "最後版本")]}}
        source, adapter, _ = self.adapter(20, periods)
        rows = self.collect(source, adapter)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["case_number"], "第10屆第1會期 A")
        self.assertEqual(rows[1]["title"], "最後版本")
        self.assertEqual(rows[1]["case_number"], "第10屆第2會期 B")
        periods["10-01"]["dataList"] = [dict(self.bill(10, 1, "C"), billName="長" * 400, billOrg="院" * 250)]
        source, adapter, _ = self.adapter(20, periods)
        record = self.collect(source, adapter)[0]
        self.assertEqual((len(record["title"]), len(record["authority"])), (300, 200))
        self.assertEqual(set(record), {"kind", "external_id", "title", "case_number", "authority", "official_url", "event_date"})

    def test_period_rollover_cap_and_nonconsecutive_empty(self):
        periods = {f"{term}-{period:02d}": {"dataList": [self.bill(term, period, f"{term}-{period}")]}
                   for term in range(10, 13) for period in range(1, 9)}
        source, adapter, session = self.adapter(20, periods)
        self.assertEqual(len(self.collect(source, adapter)), 24)
        self.assertEqual(session.get.call_count, 25)
        self.assertIn("term=12&sessionPeriod=08", session.get.call_args.args[0])
        periods = {"10-01": periods["10-01"], "10-03": periods["10-03"]}
        source, adapter, session = self.adapter(20, periods)
        self.assertEqual(len(self.collect(source, adapter)), 2)
        self.assertEqual(session.get.call_count, 11)  # gaps never stop a convened term; term 11 01-02 empty ends
        source, adapter, session = self.adapter(20, {})
        self.assertEqual(self.collect(source, adapter), [])
        self.assertEqual(session.get.call_count, 3)

    def test_latest_term_scan_and_old_cursor_falls_back_to_full(self):
        periods = {f"{term}-{period:02d}": {"dataList": [self.bill(term, period, f"{term}-{period}")]}
                   for term in (10, 11) for period in (1, 2)}
        source, adapter, session = self.adapter(20, periods)
        adapter.full = False
        rows = self.collect(source, adapter)
        self.assertEqual(sorted(r["external_id"] for r in rows), ["11-1", "11-2"])
        # robots.txt, probes 12-01, 12-02, 11-01; scans 11-02..11-08; 12-01/02 come from the probe cache
        self.assertEqual(session.get.call_count, 11)
        self.assertFalse(any("term=10&" in call.args[0] for call in session.get.call_args_list))
        source, adapter, _ = self.adapter(20, periods)
        adapter.full = False
        rows = self.collect(source, adapter, "10-02")
        self.assertEqual(sorted(r["external_id"] for r in rows), ["10-2", "11-1", "11-2"])
        source, adapter, session = self.adapter(20, {})
        adapter.full = False
        self.assertEqual(self.collect(source, adapter), [])

    def test_cli_full_ly_scan_on_monday_or_backfill(self):
        spec = importlib.util.spec_from_file_location("national_sync_ly_full", ROOT / "scripts/national-sync.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        source, adapter, _ = self.adapter(20)
        for day, extra, full in (("2026-11-23", [], True), ("2026-11-24", [], False), ("2026-11-24", ["--backfill"], True)):
            constructor = Mock(return_value=adapter)
            clock = Mock(wraps=cli.datetime)
            clock.now.return_value = cli.datetime.fromisoformat(day + "T01:00:00+00:00")
            args = ["national-sync", "--source", "N-LY-ID20-BILLS", "--adapter", "ly20", "--dry-run", *extra]
            with patch.dict(cli.ADAPTERS, {"ly20": constructor}), patch.object(cli, "datetime", clock), patch.dict(cli.os.environ, {"NATIONAL_RUNNER": "github-actions"}), patch.object(cli.sys, "argv", args), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(constructor.call_args.kwargs, {"full": full}, day)

    def test_budget_resume_replays_period_and_preserves_empty_streak(self):
        periods = {f"10-0{p}": {"dataList": [self.bill(10, p, str(p))]} for p in (1, 2)}
        source, adapter, _ = self.adapter(20, periods)
        first = load(self.store, source, adapter, runner="github-actions", daily_limit=SOURCE_WRITE_ESTIMATE + RECORD_WRITE_ESTIMATE + RUN_WRITE_RESERVE)
        self.assertEqual((first["status"], first["changed"], first["cursor_after"]), ("BUDGET_STOPPED", 1, "10-02"), first)
        source, adapter, _ = self.adapter(20, periods)
        second = load(self.store, source, adapter, runner="github-actions")
        self.assertEqual((second["status"], second["changed"], second["cursor_before"]), ("OK", 1, "10-02"), second)
        source, adapter, _ = self.adapter(20, periods)
        self.assertEqual(load(self.store, source, adapter, runner="github-actions")["changed"], 0)
        source, adapter, session = self.adapter(6)
        self.assertEqual(self.collect(source, adapter, "10-03"), [])
        self.assertEqual(session.get.call_count, 11)
        self.assertEqual(len(adapter.warnings), 7)  # term 10 periods 02-08; unconvened term 11 adds none

    def test_invalid_payload_cursor_and_url_fallback(self):
        for payload in ({}, {"dataList": {}}, {"dataList": [None]},
                        {"dataList": [self.bill(11, 1, "wrong-period")]},
                        {"dataList": [self.bill(10, 1, "")]},
                        {"dataList": [self.bill(10, 1, "A", "invalid")]}):
            source, adapter, _ = self.adapter(20, {"10-01": payload})
            result = load(self.store, source, adapter, runner="github-actions")
            self.assertEqual((result["status"], result["changed"]), ("ERROR", 0), result)
            self.assertEqual(self.store.query("SELECT * FROM sources"), [])
        for cursor in (1, "9-01", "10-1", "10-09", "13-01"):
            source, adapter, session = self.adapter(20)
            with self.assertRaises(ValueError):
                adapter.fetch(source, cursor)
            session.get.assert_not_called()
        for url in ("https://ppg.ly.gov.tw.evil.test/a", "https://user@ppg.ly.gov.tw/a",
                    "https://ppg.ly.gov.tw:444/a", "https://ppg.ly.gov.tw:bad/a",
                    "https://ppg.ly.gov.tw/a b", "https://[bad"):
            source, adapter, _ = self.adapter(20, {"10-01": {"dataList": [dict(self.bill(10, 1, "A"), pdfUrl=url)]}})
            self.assertEqual(self.collect(source, adapter)[0]["official_url"], "https://data.ly.gov.tw/getds.action?id=20")

    def test_ppg_redirect_and_registered_block_never_request_ppg(self):
        source, adapter, session = self.adapter(20)
        original = session.get.side_effect
        session.get.side_effect = lambda url, **kw: (original(url, **kw) if url.endswith("robots.txt") else
            response(302, url=url, headers={"Location": "https://ppg.ly.gov.tw/secret"}))
        result = load(self.store, source, adapter, runner="github-actions")
        self.assertEqual(result["status"], "ERROR")
        self.assertTrue(all(urlsplit(c.args[0]).hostname == "data.ly.gov.tw" for c in session.get.call_args_list))
        with self.assertRaises(http_fetch.RobotsDisallowed):
            adapter.request("https://ppg.ly.gov.tw/secret", max_bytes=1024)
        source, adapter, session = self.adapter(20)
        session.get.side_effect = None
        session.get.return_value = response(302, url=source["robots"]["url"],
                                           headers={"Location": "https://ppg.ly.gov.tw/robots.txt"})
        result = load(self.store, source, adapter, runner="github-actions")
        self.assertEqual(result["status"], "ERROR")
        session.get.assert_called_once()
        self.assertEqual(session.get.call_args.args[0], source["robots"]["url"])

    def test_cli_registered_adapters_source_selection_and_warnings(self):
        spec = importlib.util.spec_from_file_location("national_sync_ly", ROOT / "scripts/national-sync.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        self.assertIs(cli.ADAPTERS["ly20"], Ly20Adapter)
        self.assertIs(cli.ADAPTERS["ly6"], Ly6Adapter)
        for dataset in (20, 6):
            source, adapter, session = self.adapter(dataset)
            output = io.StringIO()
            args = ["national-sync", "--source", source["national_source_id"], "--adapter", f"ly{dataset}", "--dry-run"]
            with patch.dict(cli.ADAPTERS, {f"ly{dataset}": Mock(return_value=adapter)}), patch.dict(cli.os.environ, {"NATIONAL_RUNNER": "github-actions"}), patch.object(cli.sys, "argv", args), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                self.assertEqual(cli.main(), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(len(report["runs"]), 1)
            self.assertEqual(report["runs"][0]["source_id"], source["national_source_id"])
            self.assertEqual(len(report["runs"][0]["warnings"]), 7 if dataset == 6 else 0)
            session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
