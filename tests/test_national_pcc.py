"""Offline replay of reduced PCC runner responses; no detail-page requests allowed."""
import copy
from contextlib import redirect_stdout
from datetime import date
import importlib.util
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from intel_v2 import http_fetch
from national.adapters.pcc import PccGpaAdapter, PccWebAdapter, ORIGIN, TENDER, AWARD, detail_link, jurisdiction
from national.loader import load
from national.store import SqliteStore
from tests.test_http_fetch import response
from tests.test_national_sources import sources as validator

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/national/pcc"
REGISTRY = json.loads((ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))


def fixture(name):
    return (FIXTURES / name).read_bytes()


def source(kind):
    row = copy.deepcopy(next(s for s in REGISTRY["sources"] if s["national_source_id"] == "N-PCC-" + kind))
    row["scope"].update(months=1, daily_months=1)
    return row


def page(kind, indices=(0, 1, 2), total=None):
    body = fixture(kind + ".html").decode()
    rows = re.findall(r'<tr\b[^>]*class="tb_b[23]".*?</tr>', body, re.S)
    for i, row in enumerate(rows):
        if i not in indices:
            body = body.replace(row, "", 1)
    body = re.sub(r'(<span class="red">)\s*[\d,]+\s*',
                  lambda m: m[1] + str(len(indices) if total is None else total), body, count=1)
    return body.encode()


class Replay:
    def __init__(self, handler, robots_status=302, location="http://web.pcc.gov.tw/pis", robots_body=b""):
        self.handler, self.calls, self.headers = handler, [], {}
        self.robots_status, self.location, self.robots_body = robots_status, location, robots_body

    def get(self, url, **kwargs):
        if "/urlSelector/" in url or "pk=" in url:
            raise AssertionError("pk detail request forbidden")
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return response(self.robots_status, url=url, headers={"Location": self.location}, body=self.robots_body)
        answer = self.handler(url)
        return answer if hasattr(answer, "status_code") else response(url=url, body=answer)

    def close(self):
        pass


class PccTests(unittest.TestCase):
    def setUp(self):
        for target, kwargs in [("requests.sessions.Session.request", {"side_effect": AssertionError("network forbidden")}),
                               ("intel_v2.http_fetch.time.sleep", {})]:
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def web(self, handler=None, **kwargs):
        session = Replay(handler or (lambda url: page("tender" if TENDER in url else "award")))
        adapter = PccWebAdapter(source("WEB"), session=session, today=date(2026, 11, 30), **kwargs)
        return adapter, session

    def test_robots_exact_redirect_only_and_200_rules(self):
        for status in (301, 302):
            session = Replay(lambda url: page("tender"), robots_status=status)
            adapter = PccWebAdapter(source("WEB"), session=session)
            adapter.read(ORIGIN + TENDER)
            self.assertEqual(len(session.calls), 2)
            self.assertFalse(any(url.startswith("http:") for url in session.calls))
        for status, location, body in [(302, "http://web.pcc.gov.tw/other", b""),
                (307, "http://web.pcc.gov.tw/pis", b""), (500, "", b""),
                (200, "", b"User-agent: *\nDisallow: /prkms/\n")]:
            session = Replay(Mock(side_effect=AssertionError("content forbidden")), status, location, body)
            adapter = PccWebAdapter(source("WEB"), session=session)
            with self.subTest(status=status), self.assertRaises(http_fetch.RobotsDisallowed):
                adapter.read(ORIGIN + TENDER)
            self.assertEqual(len(session.calls), 1)

    def test_robots_network_error_fails_closed(self):
        session = Mock(headers={}, get=Mock(side_effect=OSError("offline")))
        adapter = PccWebAdapter(source("WEB"), session=session)
        with self.assertRaises(http_fetch.RobotsDisallowed):
            adapter.read(ORIGIN + TENDER)

    def test_registry_rejects_broad_or_unevidenced_exception(self):
        for value in ("https://evil.test/pis", "ftp://web.pcc.gov.tw/pis",
                      "https://web.pcc.gov.tw/robots.txt", "https://web.pcc.gov.tw/%72obots.txt",
                      "https://user@web.pcc.gov.tw/pis", "https://web.pcc.gov.tw/pis#fragment"):
            registry = copy.deepcopy(REGISTRY)
            row = next(s for s in registry["sources"] if s["national_source_id"] == "N-PCC-WEB")
            row["robots"]["absent_when_redirect_to"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                validator.validate(registry)

    def test_xml_scope_mapping_dedup_and_no_private_fields(self):
        adapter = PccGpaAdapter(source("GPA"))
        for kind in ("tender", "award"):
            records, count = adapter.parse_xml(fixture(kind + ".xml"), kind)
            self.assertEqual((len(records), count), (2, 3))
            for record in records:
                self.assertEqual(set(record), {"kind", "external_id", "title", "case_number", "authority", "jurisdiction", "event_date", "official_url"})
                self.assertEqual(record["jurisdiction"], jurisdiction(record["authority"]))
                self.assertEqual(record["kind"], kind)
                self.assertRegex(record["event_date"], r"^2026-\d\d-\d\d$")
            self.assertNotIn("PRIVATE_FIXTURE_SENTINEL", json.dumps(records))
        body = fixture("tender.xml").decode()
        row = re.search(r'<TENDER>.*?</TENDER>', body, re.S)[0]
        changed = re.sub(r'<TENDER_NAME>.*?</TENDER_NAME>', '<TENDER_NAME>Updated</TENDER_NAME>', row, flags=re.S)
        body = body.replace('</TENDER_LIST>', changed + '</TENDER_LIST>')
        records, _ = adapter.parse_xml(body.encode(), "tender")
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["title"], "Updated")

    def test_jurisdiction_from_authority_prefix(self):
        for authority, expected in (("臺中市政府警察局", "臺中市"), ("台中市政府地政局", "臺中市"),
                                    ("高雄市政府警察局湖內分局", "高雄市"), ("新竹縣政府", "新竹縣"),
                                    ("新竹市警察局", "新竹市"), ("內政部警政署警察廣播電臺", "全國"),
                                    ("台灣中油股份有限公司", "全國"), ("國立臺中科技大學", "全國")):
            with self.subTest(authority=authority):
                self.assertEqual(jurisdiction(authority), expected)

    def test_xml_rejects_dtd_entity_and_oversize(self):
        adapter = PccGpaAdapter(source("GPA"))
        for body in (b'<!DOCTYPE TENDER_LIST><TENDER_LIST/>', b'<!ENTITY secret "x">',
                     '<!DOCTYPE TENDER_LIST><TENDER_LIST/>'.encode('utf-16'),
                     b'x' * (adapter.MAX_BYTES + 1)):
            with self.assertRaises(ValueError):
                adapter.parse_xml(body, "tender")

    def test_half_month_names_resume_and_latest_fallback(self):
        for today, expected in [(date(2026, 11, 18), 'tender_20260902'),
                                (date(2026, 1, 3), 'tender_20251102')]:
            adapter = PccGpaAdapter(source("GPA"), today=today)
            self.assertEqual(next(adapter.filenames(adapter.source, "tender")), expected)
        for empty in (b'', b'<TENDER_LIST/>', 404):
            def handler(url):
                name = parse_qs(urlsplit(url).query)["fileName"][0]
                if name.endswith('20260902.xml'):
                    return response(404, url=url) if empty == 404 else empty
                return fixture(name.split('_')[0] + '.xml')
            session = Replay(handler)
            adapter = PccGpaAdapter(source("GPA"), session=session, today=date(2026, 11, 18))
            records, cursor = adapter.fetch(adapter.source, None)
            self.assertEqual(cursor, "tender_20260802")
            self.assertEqual(len(adapter.warnings), 2)
            # A fresh adapter can resume using the same filename cursor.
            resumed = PccGpaAdapter(source("GPA"), session=Replay(handler), today=date(2026, 11, 18))
            records2, cursor2 = resumed.fetch(resumed.source, cursor)
            self.assertEqual(cursor2, "award_20260901")
            self.assertEqual(len(records2), 2)
        session = Replay(lambda url: b'<TENDER_LIST/>')
        adapter = PccGpaAdapter(source("GPA"), session=session, today=date(2026, 11, 18))
        with self.assertRaisesRegex(ValueError, "two fallbacks"):
            adapter.fetch(adapter.source, None)
        self.assertEqual(len(session.calls), 4)  # robots + three file attempts

    def test_html_dates_correction_entities_and_script_fallback(self):
        adapter, _ = self.web()
        records, total, _ = adapter.parse_page(fixture("tender.html").decode(), "tender")
        self.assertEqual(total, 3)
        self.assertEqual(records[0]["event_date"], "2026-11-19")
        self.assertEqual(records[0]["case_number"], "116001")
        self.assertTrue(adapter.warnings[0].startswith("PCC_CORRECTION"))
        body = fixture("tender.html").decode()
        body = re.sub(r' title="檢視[^\"]*"', '', body)
        self.assertEqual(adapter.parse_page(body, "tender")[0][0]["title"], records[0]["title"])
        body = body.replace('Geps3.CNS.pageCode2Img("', 'Geps3.CNS.pageCode2Img("A&amp;B ')
        self.assertTrue(adapter.parse_page(body, "tender")[0][0]["title"].startswith("A&B "))
        with self.assertRaisesRegex(ValueError, "title missing"):
            adapter.parse_page(re.sub(r'<script>.*?</script>', '', body, flags=re.S), "tender")

    def test_pk_allowlist_and_no_detail_redirect_requests(self):
        for path in ('tpam', 'atm', 'nonAtm'):
            self.assertEqual(detail_link('/prkms/urlSelector/common/' + path + '?pk=abc%2B%2F%3D')[1], 'abc+/=')
        for url in ('https://evil.test/prkms/urlSelector/common/tpam?pk=x',
                    '/prkms/urlSelector/common/other?pk=x', '/prkms/urlSelector/common/tpam?pk=x&extra=1',
                    'http://web.pcc.gov.tw/prkms/urlSelector/common/tpam?pk=x'):
            with self.assertRaises(ValueError):
                detail_link(url)
        adapter, session = self.web(lambda url: response(302, url=url, headers={"Location": '/prkms/urlSelector/common/tpam?pk=x'}))
        with self.assertRaisesRegex(RuntimeError, "path outside"):
            adapter.read(ORIGIN + TENDER)
        self.assertEqual(len(session.calls), 2)

    def test_missing_table_range_error_and_count_mismatch(self):
        adapter, _ = self.web()
        for body, message in [('您所設定之查詢區間較長', 'range too long'), ('<html/>', 'table missing')]:
            with self.assertRaisesRegex(ValueError, message):
                adapter.parse_page(body, 'tender')
        for body in (page('tender', total=2), page('tender', indices=(), total=1)):
            adapter, _ = self.web(lambda url: body)
            with self.assertRaisesRegex(ValueError, 'count mismatch'):
                adapter.query('tender', {}, date(2026, 11, 1), date(2026, 11, 30))

    def test_tender_pagination_counts_before_scope_and_repeated_page_fails(self):
        def handler(url):
            return page('tender', (2,), 3) if 'd-49738-p=2' in url else page('tender', (0, 1), 3)
        adapter, session = self.web(handler)
        rows = adapter.query('tender', {}, date(2026, 11, 1), date(2026, 11, 30))
        self.assertEqual(len(rows), 2)
        self.assertIn('firstSearch=false', session.calls[-1])
        adapter, _ = self.web(lambda url: page('tender', (0,), 3))
        with self.assertRaisesRegex(ValueError, 'repeated page'):
            adapter.query('tender', {}, date(2026, 11, 1), date(2026, 11, 30))

    def test_award_observed_paging_and_stop_before_window(self):
        def handler(url):
            if 'd-98765-p=2' in url:
                return page('award', (2,), 3)
            return page('award', (0, 1), 3).replace(b'd-3611040-p', b'd-98765-p')
        adapter, session = self.web(handler)
        rows = adapter.query('award', {'sortCol': 'AWARD_NOTICE_DATE'}, date(2026, 1, 1), date(2026, 11, 30))
        self.assertEqual(len(rows), 2)
        self.assertIn('d-98765-p=2', session.calls[-1])
        adapter, session = self.web(lambda url: page('award-old', (0, 1), 2952))
        self.assertEqual(adapter.query('award', {}, date(2026, 10, 1), date(2026, 11, 30)), [])
        self.assertEqual(len(session.calls), 2)

    def test_live_award_dates_empty_tender_dates_and_no_award_warnings(self):
        adapter, _ = self.web()
        records, total, doc = adapter.parse_page(fixture('award-live.html').decode(), 'award')
        self.assertEqual((len(records), total), (5, 5))
        self.assertEqual([r['event_date'] for r in records], ['2026-11-18'] * 5)
        self.assertEqual({r['kind'] for r in records}, {'award'})
        self.assertEqual(adapter.warnings, [
            'PCC_NO_AWARD ' + records[i]['external_id'].removeprefix('pcc-award-') for i in (0, 4)])
        rows = [r for r in doc.one('table', id='bulletion').find('tr')
                if r.attrs.get('class') in {'tb_b2', 'tb_b3'}]
        self.assertEqual([rows[i].find('td')[4].text() for i in (2, 3)], ['', ''])

    def test_award_missing_invalid_dates_and_short_rows_fail(self):
        adapter, _ = self.web()
        body = fixture('award-live.html').decode()
        row = re.search(r'<tr\b[^>]*class="tb_b[23]".*?</tr>', body, re.S)[0]
        cells = re.findall(r'<td\b[^>]*>.*?</td>', row, re.S)
        for value in ('', 'not a date', '2026/11/18', '115-11-18', '115/02/30',
                      '115/11/180', '115/11/18/1', 'prefix 115/11/18'):
            changed = row.replace(cells[5], '<td>' + value + '</td>', 1)
            with self.subTest(value=value), self.assertRaises(ValueError):
                adapter.parse_page(body.replace(row, changed, 1), 'award')
        with self.assertRaisesRegex(ValueError, 'columns missing'):
            adapter.parse_page(body.replace(row, row.replace(cells[-1], '', 1), 1), 'award')

    def test_award_cutoff_uses_notice_date_instead_of_tender_date(self):
        # Tender dates are in 2025; award notices cross the April 2026 cutoff.
        adapter, session = self.web(lambda url: page('award-old', (0, 1), 2952))
        rows = adapter.query('award', {}, date(2026, 4, 1), date(2026, 11, 30))
        self.assertEqual([r['event_date'] for r in rows], ['2026-06-26'])
        self.assertEqual(len(session.calls), 2)  # robots + first page, no next page
        # Blank/older tender dates on page one must not prevent fetching page two.
        def handler(url):
            return (page('award-old', (0, 1), 7) if 'd-3611040-p=2' in url
                    else page('award-live', (0, 1, 2, 3, 4), 7))
        adapter, session = self.web(handler)
        rows = adapter.query('award', {}, date(2026, 11, 10), date(2026, 11, 30))
        self.assertEqual(len(rows), 5)
        self.assertEqual(len(session.calls), 3)
        self.assertIn('d-3611040-p=2', session.calls[-1])

    def test_sqlite_two_syncs_and_full_query_windows(self):
        store = SqliteStore(':memory:')
        self.addCleanup(store.close)
        store.apply_schema()
        for kind, cls in [('WEB', PccWebAdapter), ('GPA', PccGpaAdapter)]:
            for attempt in range(2):
                session = Replay(lambda url: fixture(('tender' if 'tender_' in url else 'award') + '.xml')
                    if '/OpenData/' in url else page('tender' if TENDER in url else 'award'))
                adapter = cls(source(kind), session=session, today=date(2026, 11, 30))
                run = load(store, adapter.source, adapter, runner='github-actions')
                self.assertEqual(run['status'], 'OK', run)
                self.assertEqual(run['changed'], 4 if attempt == 0 else 0)
                if kind == 'WEB':
                    queries = [parse_qs(urlsplit(u).query) for u in session.calls if TENDER in u]
                    self.assertEqual(len(queries), 4)
                    self.assertEqual({q['tenderStartDate'][0] for q in queries}, {'2026/11/01'})
        self.assertNotIn('PRIVATE_FIXTURE_SENTINEL', json.dumps(store.query('SELECT * FROM records')))

    def test_backfill_cli_only_changes_pcc_web(self):
        row = source('WEB')
        row['scope'].update(months=24, daily_months=2)
        self.assertEqual(len(list(PccWebAdapter(row).windows())), 2)
        self.assertEqual(len(list(PccWebAdapter(row, backfill=True).windows())), 24)
        spec = importlib.util.spec_from_file_location('pcc_sync', ROOT / 'scripts/national-sync.py')
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        self.assertIs(cli.ADAPTERS['pcc_gpa'], PccGpaAdapter)
        for sid, name in [('N-PCC-WEB', 'pcc_web'), ('N-PCC-GPA', 'pcc_gpa'), ('N-LY-ID20-BILLS', 'ly20')]:
            factory = Mock()
            with patch.dict(cli.ADAPTERS, {name: factory}), patch.object(cli, 'load', return_value={'exit_code': 0}), \
                    patch.object(cli, 'check_guards', return_value={'remaining_rows': 90_000}), \
                    patch('sys.argv', ['national-sync.py', '--source', sid, '--backfill', '--store', 'sqlite::memory:']), \
                    patch.dict('os.environ', {'NATIONAL_RUNNER': 'github-actions'}), redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(), 0)
            self.assertEqual(factory.call_args.kwargs, {'backfill': True} if name == 'pcc_web' else {})


if __name__ == '__main__':
    unittest.main()
