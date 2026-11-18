"""Offline replay of reduced official responses captured on 2026-11-18."""
import copy
import importlib.util
import io
import json
import re
from contextlib import redirect_stdout
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from intel_v2 import http_fetch
from national.adapters.html import Document
from national.adapters.khh import KhhAdapter
from national.adapters.tnn import TnnAdapter
from national.adapters.tpe import TpeAdapter
from national.loader import load
from national.store import ROOT, SqliteStore
from tests.test_http_fetch import response

FIXTURES = ROOT / "tests/fixtures/national"
SOURCES = {s["national_source_id"]: s for s in json.loads(
    (ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))["sources"]}


def fixture(name):
    return (FIXTURES / name).read_bytes()


class Replay:
    def __init__(self, steps):
        self.steps, self.calls, self.headers = list(steps), [], {}

    def request(self, method, url, **kwargs):
        if "Detail" in url or "WEB016" in url or "motionpage1" in url:
            raise AssertionError("detail request forbidden")
        if url.endswith("/robots.txt"):
            return response(404, url=url)
        if not self.steps:
            raise AssertionError("unexpected request: " + url)
        expected, suffix, body = self.steps.pop(0)
        if method != expected or suffix not in url:
            raise AssertionError((method, url, expected, suffix))
        self.calls.append((method, url, kwargs))
        return response(url=url, body=body)

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)

    def close(self):
        pass


def steps(code):
    if code == "TNN":
        return [("GET", "WEB004", fixture("tnn-grades.json")),
                ("GET", "pagerow=1&grdno=4", fixture("tnn-count.json")),
                ("GET", "pagerow=1500&grdno=4", fixture("tnn-list.json"))]
    if code == "KHH":
        return [("GET", "Statistics.aspx", fixture("khh-stats.html")),
                ("GET", "session_code=0704", fixture("khh-stats.html")),
                ("GET", "Default_RWD.aspx", fixture("khh-entry.html")),
                *[("POST", "Default_RWD.aspx", fixture(name)) for name in
                  ("khh-selected.html", "khh-query.html", "khh-page.html", "khh-query.html")]]
    return [("GET", "TCCMIS_Front/", fixture("tpe-home.html")),
            ("POST", "TCCMIS_Front/", fixture("tpe-count.html")),
            ("POST", "searchlist.aspx", fixture("tpe-list.html"))]


class W2bTests(unittest.TestCase):
    def setUp(self):
        for target, kwargs in [("requests.sessions.Session.request", {"side_effect": AssertionError("network forbidden")}),
                               ("intel_v2.http_fetch.time.sleep", {})]:
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = SqliteStore(":memory:")
        self.store.apply_schema()
        self.addCleanup(self.store.close)

    def adapter(self, code, replay=None):
        source = copy.deepcopy(SOURCES["N-COUNCIL-" + code])
        session = Replay(steps(code) if replay is None else replay)
        adapter = {"TNN": TnnAdapter, "KHH": KhhAdapter, "TPE": TpeAdapter}[code](source, session=session)
        return source, adapter, session

    def run_load(self, code, replay=None):
        source, adapter, session = self.adapter(code, replay)
        run = load(self.store, source, adapter, runner=source["runner"])
        return run, session

    def test_each_city_two_sqlite_syncs_and_mappings(self):
        for code, count in (("TNN", 3), ("KHH", 3), ("TPE", 2)):
            with self.subTest(code=code):
                first, replay = self.run_load(code)
                self.assertEqual((first["status"], first["changed"]), ("OK", count), first)
                self.assertFalse(replay.steps)
                second, _ = self.run_load(code)
                self.assertEqual((second["status"], second["changed"], second["rows_written"]), ("OK", 0, 0), second)
        rows = self.store.query("SELECT * FROM records WHERE source_id='N-COUNCIL-TNN' ORDER BY id")
        self.assertEqual(rows[0]["event_date"], "2026-10-06")
        self.assertIsNone(rows[1]["event_date"])
        self.assertTrue(rows[0]["external_id"].startswith("tnn:"))
        rows = self.store.query("SELECT * FROM records WHERE source_id='N-COUNCIL-KHH' ORDER BY id")
        self.assertEqual([r["external_id"] for r in rows], ["khh:145223", "khh:145296", "khh:145342"])
        self.assertEqual([r["kind"] for r in rows], ["bill_gov", "bill", "bill"])
        self.assertIn("第4屆第8次定期大會議員提案 第1號", rows[1]["case_number"])
        self.assertTrue(all(r["event_date"] is None for r in rows))
        rows = self.store.query("SELECT * FROM records WHERE source_id='N-COUNCIL-TPE' ORDER BY id")
        self.assertEqual(rows[0]["external_id"], "tpe:35439")
        self.assertEqual(rows[0]["case_number"], "第14屆第08次 案號140669")
        self.assertEqual(rows[0]["official_url"], SOURCES["N-COUNCIL-TPE"]["endpoints"][0])

    def test_tnn_bom_count_mismatch_and_duplicate_ids_fail_before_writes(self):
        self.assertTrue(fixture("tnn-list.json").startswith(b"\xef\xbb\xbf"))
        for change in ("count", "duplicate"):
            replay = steps("TNN")
            rows = json.loads(replay[-1][2].decode("utf-8-sig"))
            if change == "count":
                rows.pop()
            else:
                rows[1]["ProNo"] = rows[0]["ProNo"]
            replay[-1] = (*replay[-1][:2], json.dumps(rows).encode())
            run, _ = self.run_load("TNN", replay)
            self.assertEqual(run["status"], "ERROR")
            self.assertEqual(run["changed"], 0)
        self.assertEqual(self.store.query("SELECT count(*) AS n FROM records")[0]["n"], 0)

    def test_khh_postback_chain_and_first_tab_button(self):
        run, replay = self.run_load("KHH")
        self.assertEqual(run["status"], "OK", run)
        posts = [kw["data"] for method, _, kw in replay.calls if method == "POST"]
        for form, name in zip(posts, ("khh-entry.html", "khh-selected.html", "khh-query.html", "khh-page.html")):
            expected = Document(fixture(name).decode()).form()
            for field in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__VIEWSTATEENCRYPTED", "__EVENTVALIDATION"):
                self.assertEqual(form.get(field), expected.get(field))
        self.assertEqual(posts[0]["__EVENTTARGET"], "uscPeriodSessionMeeting$ddlSession")
        self.assertEqual(posts[0]["uscPeriodSessionMeeting$ddlSession"], "0704")
        self.assertEqual(posts[1]["__EVENTTARGET"], "LinkButton1")
        self.assertEqual(posts[1]["uscPeriodSessionMeeting$ddlMeeting"], "07040800")
        self.assertEqual(posts[2]["btnGo"], " Go ")
        self.assertNotIn("btnGo1", posts[2])
        self.assertEqual(posts[3]["btnGo4"], " Go ")
        self.assertEqual(posts[3]["DataPager4$ctl02$txtPageSize"], "500")
        self.assertEqual(posts[3]["DataPager4$ctl02$txtCurrentPage"], "1")

    def test_khh_missing_state_and_wrong_count_fail(self):
        for token in (b"__VIEWSTATE", b"__EVENTVALIDATION"):
            replay = steps("KHH")
            replay[2] = (*replay[2][:2], replay[2][2].replace(token, b"REMOVED"))
            run, _ = self.run_load("KHH", replay)
            self.assertIn("KHH_POSTBACK_CHAIN_BROKEN", run["error"])
            self.assertEqual(run["changed"], 0)
        replay = steps("KHH")
        replay = [(m, u, b.replace(b'name="hidCount04" value="2"', b'name="hidCount04" value="3"')) for m, u, b in replay]
        run, _ = self.run_load("KHH", replay)
        self.assertIn("KHH_COUNT_MISMATCH", run["error"])
        self.assertEqual(run["changed"], 0)

    def test_tpe_cap_fails_and_count_warning_is_reported(self):
        replay = steps("TPE")
        replay[-1] = (*replay[-1][:2], fixture("tpe-cap.html"))
        run, _ = self.run_load("TPE", replay)
        self.assertIn("TPE_ROW_CAP term=14 session=08", run["error"])
        self.assertEqual(run["changed"], 0)
        replay = steps("TPE")
        replay[1] = (*replay[1][:2], replay[1][2].replace(b'id="OMCount">2<', b'id="OMCount">200<'))
        run, _ = self.run_load("TPE", replay)
        self.assertEqual(run["status"], "OK", run)
        self.assertEqual(run["warnings"], ["TPE_COUNT_MISMATCH term=14 got=2 expected=200"])

    def test_tpe_fallback_stops_after_two_consecutive_empty_slices(self):
        replay = steps("TPE")[:2]
        replay[1] = (*replay[1][:2], re.sub(rb'<select name="sele_DETM".*?</select>', b'', replay[1][2], flags=re.S))
        empty = re.sub(rb'<tr\b.*?</tr>', b'', fixture("tpe-list.html"), flags=re.S)
        replay.extend(("POST", "searchlist.aspx", body) for body in
                      [empty, fixture("tpe-list.html"), empty, empty])
        run, session = self.run_load("TPE", replay)
        self.assertEqual(run["status"], "OK", run)
        forms = [kw["data"] for _, url, kw in session.calls if "searchlist.aspx" in url]
        self.assertEqual([f["HDETM"] for f in forms], ["01", "02", "03", "04"])
        self.assertFalse(session.steps)

    def test_cursors_resume_metadata_pages(self):
        for code, cursor, expected in (("TNN", "4-1", 3), ("KHH", "07040800", 3), ("TPE", "14-08", 2)):
            source, adapter, replay = self.adapter(code)
            records, next_cursor = adapter.fetch(source, cursor)
            self.assertEqual(len(records), expected)
            self.assertIsNone(next_cursor)
            self.assertFalse(replay.steps)

    def test_tnn_multiple_pages_preserve_next_cursor_and_check_total(self):
        replay = steps("TNN")
        rows = json.loads(replay[-1][2].decode("utf-8-sig"))
        for row in rows:
            row["PagesAll"] = "2"
        replay[-1:] = [("GET", "topage=1&pagerow=2", json.dumps(rows[:2]).encode()),
                       ("GET", "topage=2&pagerow=2", json.dumps(rows[2:]).encode())]
        source, adapter, session = self.adapter("TNN", replay)
        adapter.PAGE_SIZE = 2
        records, cursor = adapter.fetch(source, None)
        self.assertEqual((len(records), cursor), (2, "4-2"))
        records, cursor = adapter.fetch(source, cursor)
        self.assertEqual((len(records), cursor), (1, None))
        self.assertFalse(session.steps)

    def test_khh_second_page_uses_immediately_previous_state(self):
        replay = steps("KHH")
        body = replay[-1][2].decode()
        table = re.search(r'<table id="gvIndex4">.*?</table>', body, re.S)[0]
        rows = re.findall(r'<tr\b.*?</tr>', table, re.S)
        first_page = body.replace(rows[3], "")
        second_page = fixture("khh-page.html").decode().replace(rows[2], "")
        replay[-1:] = [("POST", "Default_RWD.aspx", first_page.encode()),
                       ("POST", "Default_RWD.aspx", second_page.encode())]
        source, adapter, session = self.adapter("KHH", replay)
        adapter.PAGE_SIZE = 1
        run = load(self.store, source, adapter, runner=source["runner"])
        self.assertEqual((run["status"], run["changed"]), ("OK", 3), run)
        last = session.calls[-1][2]["data"]
        self.assertEqual(last["DataPager4$ctl02$txtCurrentPage"], "2")
        self.assertEqual(last["__VIEWSTATE"], Document(first_page).form()["__VIEWSTATE"])
        self.assertFalse(session.steps)

    def test_cli_tpe_runner_mismatch_makes_no_requests(self):
        spec = importlib.util.spec_from_file_location("w2b_sync", ROOT / "scripts/national-sync.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        for code, cls in (("tnn", TnnAdapter), ("khh", KhhAdapter), ("tpe", TpeAdapter)):
            self.assertIs(cli.ADAPTERS[code], cls)
            self.assertEqual(cli.SOURCE_ADAPTERS["N-COUNCIL-" + code.upper()], code)
        output = io.StringIO()
        with patch.dict("os.environ", {"NATIONAL_RUNNER": "github-actions"}), \
                patch("sys.argv", ["national-sync.py", "--source", "N-COUNCIL-TPE", "--store", "sqlite::memory:"]), \
                redirect_stdout(output):
            self.assertEqual(cli.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["runs"][0]["reason"], "RUNNER_MISMATCH")


if __name__ == "__main__":
    unittest.main()
