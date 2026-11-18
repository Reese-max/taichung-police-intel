import contextlib
import copy
import importlib.util
import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import requests

from intel_v2 import http_fetch
from national.adapters.odp import OdpCatalogAdapter
from national.adapters.tdx import TdxAdapter
from national.loader import load, normalize, record_sql, source_sql, RECORD_WRITE_ESTIMATE
from national.store import ROOT, SqliteStore, RUN_WRITE_RESERVE
from tests.test_http_fetch import response

FIXTURES = ROOT / "tests/fixtures/national"
SOURCES = {s["national_source_id"]: s for s in json.loads(
    (ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))["sources"]}
SECRETS = {"TDX_CLIENT_ID": "fixture-client", "TDX_CLIENT_SECRET": "fixture-secret"}
TOKEN = "fixture-access-token"
NOW = "2026-11-18T10:00:00+00:00"


class StreamResponse(requests.Response):
    @property
    def content(self):
        raise AssertionError("must not buffer response.content")


def streamed(url, chunks):
    result = StreamResponse()
    result.url, result.status_code = url, 200
    result.headers["Content-Type"] = "text/html"
    result.iter_content = Mock(return_value=iter(chunks))
    result.close = Mock()
    return result


class NationalTdxOdpTests(unittest.TestCase):
    def setUp(self):
        for target, kwargs in (
            ("requests.sessions.Session.request", {"side_effect": AssertionError("real network forbidden")}),
            ("intel_v2.http_fetch.time.sleep", {}),
            ("national.loader.utc_now", {"return_value": NOW}),
            ("national.store.utc_now", {"return_value": NOW}),
            ("tempfile.tempdir", {"new": str(ROOT)}),
        ):
            state = patch(target, **kwargs)
            state.start()
            self.addCleanup(state.stop)
        state = patch.dict(os.environ, SECRETS)
        state.start()
        self.addCleanup(state.stop)
        self.store = SqliteStore(":memory:")
        self.store.apply_schema()
        self.addCleanup(self.store.close)

    def tdx(self):
        source = copy.deepcopy(SOURCES["N-TDX-EVENTS"])
        pages = json.loads((FIXTURES / "tdx-liveevents.json").read_text(encoding="utf-8"))
        session = Mock(headers={})
        def get(url, **kwargs):
            if url == source["robots"]["url"]:
                return response(url=url, body=b"<html>SPA fallback</html>")
            city = urlsplit(url).path.rsplit("/", 1)[1]
            self.assertIn(city, TdxAdapter.CITIES)
            self.assertEqual(parse_qs(urlsplit(url).query), {"$top": ["1000"], "$format": ["JSON"]})
            self.assertEqual(kwargs["headers"], {"Authorization": "Bearer " + TOKEN})
            payload = pages.get(city, {"AuthorityCode": city, "Count": 0, "Items": []})
            return response(url=url, body=json.dumps(payload).encode())
        session.get.side_effect = get
        session.post.side_effect = lambda url, **kw: response(url=url, body=json.dumps({"access_token": TOKEN}).encode())
        return source, TdxAdapter(source, session=session), session, pages

    def odp(self, body=None):
        source = copy.deepcopy(SOURCES["N-ODP-CATALOG"])
        body = (FIXTURES / "odp-catalog.csv").read_bytes() if body is None else body
        session = Mock(headers={})
        def get(url, **kwargs):
            if url == source["robots"]["url"]:
                return response(500, url=url)
            self.assertEqual(url, source["endpoints"][0])
            return streamed(url, (body[i:i + 17] for i in range(0, len(body), 17)))
        session.get.side_effect = get
        return source, OdpCatalogAdapter(source, session=session), session

    def run_load(self, source, adapter, **kwargs):
        return load(self.store, source, adapter, runner="github-actions", **kwargs)

    def test_tdx_mapping_six_cities_token_once_and_two_syncs(self):
        source, adapter, session, pages = self.tdx()
        pages["Taipei"]["Items"][0]["EventTitle"] = "道路交通測試" * 100
        pages["NewTaipei"]["Items"][0]["EventType"] = 99
        first = self.run_load(source, adapter)
        self.assertEqual((first["status"], first["changed"]), ("OK", 4), first)
        session.post.assert_called_once_with(source["endpoints"][0], data={
            "grant_type": "client_credentials", "client_id": SECRETS["TDX_CLIENT_ID"],
            "client_secret": SECRETS["TDX_CLIENT_SECRET"]}, timeout=60, allow_redirects=False, stream=True)
        self.assertEqual([urlsplit(c.args[0]).path.rsplit("/", 1)[1] for c in session.get.call_args_list[1:]], list(TdxAdapter.CITIES))
        self.assertNotIn("headers", session.get.call_args_list[0].kwargs)
        rows = self.store.query("SELECT * FROM records ORDER BY id")
        self.assertEqual(len(rows[0]["title"]), 300)
        self.assertEqual(rows[0]["external_id"], "tdx-Taipei-fixture-1")
        self.assertEqual(rows[0]["authority"], "TPE 測試通報機關")
        self.assertEqual([r["jurisdiction"] for r in rows], ["臺北市"] * 2 + ["新北市"] * 2)
        self.assertEqual([r["case_number"] for r in rows], ["事故", "施工", "類型99", "施工"])
        self.assertEqual(rows[1]["event_date"], "2026-11-17")
        self.assertTrue(all(r["official_url"] == "https://tdx.transportdata.tw/" for r in rows))
        self.assertNotIn("禁止儲存", json.dumps(rows, ensure_ascii=False))
        second = self.run_load(source, adapter)
        self.assertEqual((second["status"], second["changed"], second["rows_written"]), ("OK", 0, 0), second)
        self.assertEqual(session.post.call_count, 2)

    def test_tdx_missing_secret_cli_skips_session_and_store(self):
        spec = importlib.util.spec_from_file_location("national_sync_w56", ROOT / "scripts/national-sync.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        for missing in ("TDX_CLIENT_ID", "TDX_CLIENT_SECRET"):
            source, adapter, session, _ = self.tdx()
            output = io.StringIO()
            with self.subTest(missing=missing), patch.dict(os.environ, {missing: "", "NATIONAL_RUNNER": "github-actions"}), \
                    patch.object(cli.sys, "argv", ["national-sync", "--source", "N-TDX-EVENTS", "--store", "d1-remote"]), \
                    patch.object(cli, "ADAPTERS", {"tdx": Mock(return_value=adapter)}), \
                    patch.object(cli, "WranglerD1Store", side_effect=AssertionError("no store needed")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(), 0)
                result = json.loads(output.getvalue())["runs"][0]
                self.assertEqual((result["status"], result["reason"]), ("NOT_CONFIGURED", "MISSING_SECRET"))
                cli.ADAPTERS["tdx"].assert_not_called()
                with self.assertRaisesRegex(ValueError, "MISSING_SECRET"):
                    adapter.fetch(source, None)
            session.get.assert_not_called()
            session.post.assert_not_called()

    def test_tdx_token_and_event_errors_do_not_leak_credentials(self):
        leak = SECRETS["TDX_CLIENT_SECRET"] + TOKEN
        for stage, status in (("token", 400), ("token", 401), ("event", 401), ("exception", None)):
            source, adapter, session, _ = self.tdx()
            bad = response(status or 200, url=source["endpoints"][0] + "?reflected=" + leak, body=leak.encode())
            if stage == "event":
                session.get.side_effect = [response(url=source["robots"]["url"], body=b""), bad]
            else:
                session.post.side_effect = requests.ConnectionError(leak) if stage == "exception" else None
                session.post.return_value = bad
            result = self.run_load(source, adapter)
            with self.subTest(stage=stage, status=status):
                self.assertEqual(result["status"], "ERROR")
                for secret in (SECRETS["TDX_CLIENT_SECRET"], TOKEN):
                    self.assertNotIn(secret, json.dumps(result))
                    self.assertNotIn(secret, json.dumps(self.store.query("SELECT * FROM runs")))
                self.assertEqual(result["changed"], 0)

    def test_tdx_bad_tokens_counts_fields_and_cursor_fail_closed(self):
        for token in (None, "", " ", 7, {}, "bad\ntoken"):
            source, adapter, session, _ = self.tdx()
            session.post.side_effect = None
            session.post.return_value = response(url=source["endpoints"][0], body=json.dumps({"access_token": token}).encode())
            with self.subTest(token=token), self.assertRaisesRegex(RuntimeError, "access_token"):
                adapter.fetch(source, None)
        for count in (1, 3, None, True, "2"):
            source, adapter, _, pages = self.tdx()
            pages["Taipei"]["Count"] = count
            with self.subTest(count=count), self.assertRaisesRegex(RuntimeError, "Count"):
                adapter.fetch(source, None)
        for field, value in (("EventID", ""), ("EventType", "1"), ("EffectiveTime", "invalid-date")):
            source, adapter, _, pages = self.tdx()
            pages["Taipei"]["Items"][0][field] = value
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                adapter.fetch(source, None)
        source, adapter, session, _ = self.tdx()
        with self.assertRaisesRegex(ValueError, "cursor"):
            adapter.fetch(source, "outside")
        session.get.assert_not_called()
        session.post.assert_not_called()

    def test_tdx_budget_resume_uses_city_cursor_and_fresh_run_token(self):
        source, adapter, session, _ = self.tdx()
        first = self.run_load(source, adapter, daily_limit=132)
        self.assertEqual((first["status"], first["changed"], first["cursor_after"]), ("BUDGET_STOPPED", 2, "NewTaipei"), first)
        session.get.reset_mock()
        second = self.run_load(source, adapter)
        self.assertEqual((second["status"], second["changed"], second["cursor_before"]), ("OK", 2, "NewTaipei"), second)
        self.assertEqual(session.post.call_count, 2)
        self.assertTrue(session.get.call_args_list[0].args[0].split("?")[0].endswith("/NewTaipei"))
        self.assertEqual(self.run_load(source, adapter)["changed"], 0)

    def seed_retention(self):
        source = copy.deepcopy(SOURCES["N-TDX-EVENTS"])
        other = copy.deepcopy(source)
        other["national_source_id"] = "N-OTHER"
        self.store.execute_batch(source_sql(source) + source_sql(other))
        for target, external_id, day in ((source, "old", "2026-10-18"), (source, "boundary", "2026-10-19"),
                                         (source, "unknown", None), (other, "other", "2000-01-01")):
            row = dict(kind="traffic_event", external_id=external_id, title="過期交通事件測試" if external_id == "old" else external_id,
                       official_url="https://tdx.transportdata.tw/", event_date=day)
            self.store.execute_batch(record_sql(normalize(target, row)))
        return source

    def test_retention_deletes_only_old_source_rows_and_accounts_fts(self):
        source = self.seed_retention()
        before = self.store.connection.total_changes
        result = self.run_load(source, Mock(fetch=Mock(return_value=([], None)), warnings=[]))
        self.assertEqual((result["status"], result["deleted"]), ("OK", 1), result)
        self.assertGreater(result["rows_written"], 1)
        self.assertEqual(result["rows_written"], self.store.connection.total_changes - before - 1)
        self.assertEqual(self.store.rows_written_today(), result["rows_written"] + RUN_WRITE_RESERVE)
        self.assertEqual({r["external_id"] for r in self.store.query("SELECT external_id FROM records")}, {"boundary", "unknown", "other"})
        self.assertEqual(self.store.query("SELECT rowid FROM records_fts WHERE records_fts MATCH '過期交通'"), [])

    def test_retention_budget_stop_error_and_dry_run_do_not_delete(self):
        source = self.seed_retention()
        adapter = Mock(fetch=Mock(return_value=([], None)), warnings=[])
        result = self.run_load(source, adapter, daily_limit=RECORD_WRITE_ESTIMATE + RUN_WRITE_RESERVE - 1)
        self.assertEqual((result["status"], result["rows_written"]), ("BUDGET_STOPPED", 0))
        self.assertEqual(len(self.store.query("SELECT * FROM records")), 4)
        adapter.fetch.side_effect = ValueError("failed page")
        self.assertEqual(self.run_load(source, adapter)["status"], "ERROR")
        self.assertEqual(len(self.store.query("SELECT * FROM records")), 4)
        adapter.fetch.side_effect = None
        output = []
        self.assertEqual(self.run_load(source, adapter, dry_run=True, emit=output.append)["status"], "OK")
        self.assertIn("DELETE FROM records", "".join(output))
        self.assertEqual(len(self.store.query("SELECT * FROM records")), 4)
        self.assertEqual(self.run_load(source, adapter)["deleted"], 1)

    def test_retention_absent_and_invalid_setting(self):
        source = self.seed_retention()
        source["scope"].pop("retention_days")
        adapter = Mock(fetch=Mock(return_value=([], None)), warnings=[])
        self.assertEqual(self.run_load(source, adapter)["status"], "OK")
        self.assertEqual(len(self.store.query("SELECT * FROM records")), 4)
        adapter.fetch.reset_mock()
        for value in (-1, True, "30"):
            source["scope"]["retention_days"] = value
            self.assertEqual(self.run_load(source, adapter)["status"], "ERROR")
        adapter.fetch.assert_not_called()

    def test_retention_large_backlog_makes_bounded_progress(self):
        source = self.seed_retention()
        records = [normalize(source, dict(kind="traffic_event", external_id="old-" + str(i),
                                         title="過期交通事件測試", event_date="2000-01-01",
                                         official_url="https://tdx.transportdata.tw/")) for i in range(1500)]
        self.store.execute_batch("".join(record_sql(r) for r in records))
        adapter = Mock(fetch=Mock(return_value=([], None)), warnings=[])
        result = self.run_load(source, adapter, daily_limit=100 * RECORD_WRITE_ESTIMATE + RUN_WRITE_RESERVE)
        self.assertEqual(result["status"], "BUDGET_STOPPED")
        self.assertEqual(result["deleted"], 100)
        self.assertGreater(result["rows_written"], 100)
        result = self.run_load(source, adapter)
        self.assertEqual((result["status"], result["deleted"]), ("OK", 1401), result)

    def test_odp_bom_filter_aliases_metadata_only_stream_and_two_syncs(self):
        source, adapter, session = self.odp()
        with patch("national.adapters.odp.io.TextIOWrapper", wraps=io.TextIOWrapper) as wrapper:
            first = self.run_load(source, adapter)
        self.assertEqual((first["status"], first["changed"]), ("OK", 8), first)
        self.assertEqual(wrapper.call_args.kwargs, {"encoding": "utf-8-sig", "newline": ""})
        self.assertTrue(wrapper.call_args.args[0].closed)
        self.assertEqual(session.get.call_count, 2)
        self.assertTrue(session.get.call_args.kwargs["stream"])
        session.post.assert_not_called()
        rows = self.store.query("SELECT * FROM records ORDER BY id")
        self.assertEqual({r["jurisdiction"] for r in rows}, set(OdpCatalogAdapter.CITIES))
        self.assertEqual(rows[2]["license"], "未標示（不推定開放）")
        self.assertEqual(rows[0]["external_id"], "odp-136493")
        self.assertTrue(all(r["official_url"] == "https://data.gov.tw/dataset/" + r["external_id"][4:] for r in rows))
        self.assertNotIn("禁止儲存", json.dumps(rows, ensure_ascii=False))
        self.assertNotIn("blocked-one", json.dumps(rows))
        second = self.run_load(source, adapter)
        self.assertEqual((second["status"], second["changed"], second["rows_written"]), ("OK", 0, 0), second)
        source["scope"]["keywords"] = ["不存在的關鍵字"]
        self.assertEqual(adapter.fetch(source, None), ([], None))

    def test_odp_missing_required_columns_invalid_csv_id_and_byte_limit(self):
        body = (FIXTURES / "odp-catalog.csv").read_text(encoding="utf-8-sig")
        for field in OdpCatalogAdapter.REQUIRED_FIELDS:
            invalid = body.replace(field, "missing", 1).encode("utf-8-sig")
            source, adapter, _ = self.odp(invalid)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "columns"):
                adapter.fetch(source, None)
        for invalid in (body.replace('136493', '../escape'), body + 'truncated-row\n'):
            source, adapter, _ = self.odp(invalid.encode("utf-8-sig"))
            self.assertEqual(self.run_load(source, adapter)["status"], "ERROR")
            self.assertEqual(self.store.query("SELECT * FROM records"), [])
        source, adapter, _ = self.odp()
        adapter.MAX_BYTES = 20
        with self.assertRaisesRegex(ValueError, "max_bytes"):
            adapter.fetch(source, None)
        with self.assertRaisesRegex(ValueError, "cursor"):
            adapter.fetch(source, "1")

    def test_stream_file_retries_replace_partial_bytes_and_limits_clear_output(self):
        url = "https://official.test/data"
        def broken():
            yield b"partial"
            raise requests.ConnectionError("offline")
        replies = [streamed(url, broken()), streamed(url, [b"new", b" data"])]
        session = Mock(get=Mock(side_effect=replies))
        with tempfile.TemporaryFile() as target:
            result = http_fetch.get(session, url, {"official.test"}, max_bytes=20, output_file=target)
            self.assertEqual(target.read(), b"new data")
            self.assertEqual(result._content, b"")
            self.assertEqual(session.get.call_count, 2)
            self.assertTrue(all(r.close.called for r in replies))
            session.get.side_effect = [streamed(url, [b"123", b"456"])]
            with self.assertRaisesRegex(ValueError, "max_bytes"):
                http_fetch.get(session, url, {"official.test"}, max_bytes=5, output_file=target)
            self.assertEqual(target.read(), b"")
            with self.assertRaisesRegex(ValueError, "requires max_bytes"):
                http_fetch.get(session, url, {"official.test"}, output_file=target)


if __name__ == "__main__":
    unittest.main()
