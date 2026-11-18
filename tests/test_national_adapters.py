import contextlib
import copy
import importlib.util
import io
import json
from hashlib import sha256
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from intel_v2 import http_fetch
from national.adapters.http import USER_AGENT
from national.adapters.kee import KeeAdapter
from national.adapters.txg import TxgAdapter
from national.loader import load
from national.store import ROOT, SqliteStore
from tests.test_http_fetch import response

REGISTRY = json.loads((ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))
SOURCES = {s["national_source_id"]: s for s in REGISTRY["sources"]}
FIXTURES = ROOT / "tests/fixtures/national"


class NationalAdapterTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteStore(":memory:")
        self.store.apply_schema()
        self.addCleanup(self.store.close)
        self.sleep = patch.object(http_fetch.time, "sleep")
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        # Any accidental real requests fail before opening a socket.
        network = patch("requests.sessions.Session.request", side_effect=AssertionError("real network forbidden"))
        network.start()
        self.addCleanup(network.stop)

    def adapter(self, code, payload=None, *, robots=b"User-agent: *\nDisallow:\n", robots_status=200):
        source = copy.deepcopy(SOURCES["N-COUNCIL-" + code])
        fixture = json.loads((FIXTURES / (code.lower() + "-api.json")).read_text(encoding="utf-8"))
        payload = fixture if payload is None else payload
        session = Mock(headers={})
        robots_url = source["robots"]["url"]
        api_url = source["endpoints"][0]
        def get(url, **kwargs):
            if url == robots_url:
                return response(robots_status, url=url, body=robots)
            from urllib.parse import parse_qs, urlsplit
            page = int(parse_qs(urlsplit(url).query)["pageNumber"][0])
            return response(url=url, body=json.dumps(payload["pages"][page - 1]).encode("utf-8"))
        session.get.side_effect = get
        session.post.side_effect = lambda url, **kw: response(url=url, body=json.dumps(payload).encode("utf-8"))
        adapter = (TxgAdapter if code == "TXG" else KeeAdapter)(source, session=session)
        return source, adapter, session, payload

    def run_load(self, source, adapter, **kwargs):
        return load(self.store, source, adapter, runner="github-actions", **kwargs)

    def test_mapping_transport_and_two_complete_sqlite_syncs(self):
        for code in ("TXG", "KEE"):
            with self.subTest(code=code):
                source, adapter, session, payload = self.adapter(code)
                first = self.run_load(source, adapter)
                self.assertEqual((first["status"], first["changed"], first["fetched"]), ("OK", 4, 4), first)
                source, fresh, _, _ = self.adapter(code)
                second = self.run_load(source, fresh)
                self.assertEqual((second["status"], second["changed"], second["rows_written"]), ("OK", 0, 0), second)
                rows = self.store.query("SELECT * FROM records WHERE source_id='" + source["national_source_id"] + "' ORDER BY id")
                self.assertTrue(all(row["event_date"] is None and row["kind"] == "bill" for row in rows))
                self.assertEqual(session.headers["User-Agent"], USER_AGENT)
                self.assertNotIn("禁止儲存", json.dumps(rows, ensure_ascii=False))
                if code == "TXG":
                    self.assertEqual(rows[0]["title"], "改善通學道路安全")
                    self.assertEqual(rows[1]["title"], "議員提案第100號")
                    self.assertEqual(rows[0]["case_number"], "第4屆 第8次 定期會 第99號")
                    self.assertEqual(rows[0]["external_id"], payload["pages"][0]["data"]["data"][0]["billId"])
                    self.assertEqual(rows[0]["official_url"], "https://yishi.tccc.gov.tw/proposals/" + rows[0]["external_id"])
                    self.assertEqual(session.get.call_count, 3)  # robots + two pages
                    self.assertIn("pageSize=200", session.get.call_args.args[0])
                    session.post.assert_not_called()
                else:
                    self.assertEqual(rows[0]["title"], "改善通學步道")
                    self.assertEqual(rows[0]["case_number"], "第20屆第22、23次臨時會")
                    self.assertEqual(rows[0]["official_url"], "https://www.kmc.gov.tw/kmcweb/#/motion")
                    session.post.assert_called_once_with(source["endpoints"][0], json={}, timeout=60,
                                                         allow_redirects=False, stream=True)

    def test_only_public_fields_and_bounded_titles(self):
        for code, limit in (("TXG", 200), ("KEE", 300)):
            source, adapter, _, payload = self.adapter(code)
            row = payload["pages"][0]["data"]["data"][0] if code == "TXG" else payload["data"][0]
            row["proposalRationale" if code == "TXG" else "sm1_3c"] = " " + "長" * 400 + " "
            records, _ = adapter.fetch(source, None)
            self.assertEqual(len(records[0]["title"]), limit)
            self.assertEqual(set(records[0]), {"kind", "external_id", "title", "case_number", "official_url", "event_date"})

    def test_txg_mismatch_or_bad_later_page_never_writes_metadata(self):
        for fault in ("count", "totals", "duplicate", "page", "guard", "missing_id"):
            source, adapter, _, payload = self.adapter("TXG")
            first, last = [page["data"] for page in payload["pages"]]
            if fault == "count":
                first["totalCount"] = last["totalCount"] = 5
            elif fault == "totals":
                last["totalCount"] = 5
            elif fault == "duplicate":
                last["data"][0]["billId"] = first["data"][0]["billId"]
            elif fault == "page":
                last["pageNumber"] = 1
            elif fault == "guard":
                first["totalPages"] = 101
            else:
                last["data"][0].pop("billId")
            with self.subTest(fault=fault):
                result = self.run_load(source, adapter)
                self.assertEqual((result["status"], result["changed"], result["rows_written"]), ("ERROR", 0, 0), result)
                self.assertEqual(self.store.query("SELECT * FROM records"), [])
                self.assertEqual(self.store.query("SELECT * FROM sources"), [])

    def test_txg_budget_page_cursor_restarts_with_full_validation(self):
        source, adapter, _, _ = self.adapter("TXG")
        first = self.run_load(source, adapter, daily_limit=132)
        self.assertEqual((first["status"], first["changed"], first["cursor_after"]), ("BUDGET_STOPPED", 2, 2), first)
        source, adapter, session, _ = self.adapter("TXG")
        second = self.run_load(source, adapter)
        self.assertEqual((second["status"], second["changed"], second["cursor_before"]), ("OK", 2, 2), second)
        self.assertEqual(session.get.call_count, 3)
        source, adapter, _, _ = self.adapter("TXG")
        self.assertEqual(self.run_load(source, adapter)["changed"], 0)

    def test_kee_duplicate_ids_are_stable_unique_and_exact_hash(self):
        source, adapter, _, payload = self.adapter("KEE")
        first, cursor = adapter.fetch(source, None)
        second, _ = adapter.fetch(source, None)
        self.assertIsNone(cursor)
        ids = [r["external_id"] for r in first]
        self.assertEqual(ids, [r["external_id"] for r in second])
        self.assertEqual(len(set(ids)), 4)
        row = payload["data"][0]
        expected = "kmc-" + sha256("|".join(row["sm1_" + k] for k in ("expkd", "seqkd", "seqno", "3c", "1c")).encode()).hexdigest()[:24]
        self.assertEqual(ids[:2], [expected, expected + "-2"])

    def test_kee_null_rationale_gets_labelled_title(self):
        source, adapter, _, payload = self.adapter("KEE")
        payload["data"] = [dict(payload["data"][0], sm1_3c=None, sm1_1c="基隆市政府")]
        records, _ = adapter.fetch(source, None)
        self.assertEqual(records[0]["title"], "基隆市政府提案（官方未提供案由）")

    def test_kee_row_cap_warning_in_run_and_reset(self):
        source, adapter, _, payload = self.adapter("KEE")
        rows = payload["data"]
        for count in (4989, 4990, 4998, 4):
            payload["data"] = (rows * 1250)[:count]
            result = self.run_load(source, adapter, dry_run=True, emit=lambda sql: None)
            self.assertEqual(result["status"], "OK", result)
            self.assertEqual(result["warnings"], ["POSSIBLE_ROW_CAP"] if count >= 4990 else [])

    def test_live_and_registered_robots_denials_do_not_write_metadata(self):
        for code in ("TXG", "KEE"):
            for mode in ("disallow", "500", "registry"):
                source, adapter, session, _ = self.adapter(code, robots=b"User-agent: *\nDisallow: /",
                                                          robots_status=500 if mode == "500" else 200)
                if mode == "registry":
                    source["robots"]["status"] = "ROBOTS_SERVER_ERROR"
                with self.subTest(code=code, mode=mode):
                    with self.assertRaises(http_fetch.RobotsDisallowed):
                        adapter.fetch(source, None)
                    result = self.run_load(source, adapter)
                    self.assertEqual((result["status"], result["changed"], result["rows_written"]), ("ERROR", 0, 0))
                    self.assertEqual(self.store.query("SELECT * FROM records"), [])
                    self.assertEqual(self.store.query("SELECT * FROM sources"), [])
                    session.post.assert_not_called()
                    self.assertEqual(session.get.call_count, 0 if mode == "registry" else 1)

    def test_cli_selects_registered_sources_for_runner_and_reports_warnings(self):
        spec = importlib.util.spec_from_file_location("national_sync", ROOT / "scripts/national-sync.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        for runner, expected in (("github-actions", ["N-COUNCIL-TXG", "N-COUNCIL-KEE"]), ("xisx-pc", [])):
            adapters = {code.lower(): self.adapter(code)[1] for code in ("TXG", "KEE")}
            adapters["kee"].session.post.side_effect = None
            payload = json.loads((FIXTURES / "kee-api.json").read_text(encoding="utf-8"))
            payload["data"] *= 1250
            adapters["kee"].session.post.return_value = response(url=SOURCES["N-COUNCIL-KEE"]["endpoints"][0],
                                                               body=json.dumps(payload).encode())
            constructors = {name: Mock(return_value=adapter) for name, adapter in adapters.items()}
            output = io.StringIO()
            with patch.object(cli, "ADAPTERS", constructors), patch.object(cli, "SOURCE_ADAPTERS", {
                    "N-COUNCIL-" + name.upper(): name for name in constructors}), patch.dict(cli.os.environ, {"NATIONAL_RUNNER": runner}), patch.object(cli.sys, "argv", ["national-sync", "--dry-run"]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(output):
                self.assertEqual(cli.main(), 0)
            report = json.loads(output.getvalue())
            self.assertEqual([run["source_id"] for run in report["runs"]], expected)
            if expected:
                self.assertEqual(report["runs"][1]["warnings"], ["POSSIBLE_ROW_CAP"])
                for adapter in adapters.values():
                    adapter.session.close.assert_called_once()
            else:
                for constructor in constructors.values():
                    constructor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
