import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from intel_v2 import http_fetch
from national.adapters.http import HttpAdapter, USER_AGENT
from tests.test_http_fetch import response

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("transcripts", ROOT / "scripts/national-transcript-index.py")
cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cli)


class TranscriptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads((ROOT / "tests/fixtures/national/txg-transcripts.json").read_text(encoding="utf-8"))
        self.ids = list(self.fixture["details"])
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(cli.SCHEMA)
        self.addCleanup(self.db.close)
        for target, kwargs in (("requests.sessions.Session.request", {"side_effect": AssertionError("network forbidden")}),
                               ("intel_v2.http_fetch.time.sleep", {})):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.session = Mock(headers={})
        self.calls = []
        self.change_page = lambda data: data
        self.robots = b"\r\n"
        self.session.get.side_effect = self.get
        self.adapter = HttpAdapter(cli.source_config(), session=self.session)
        self.addCleanup(self.adapter.close)

    def get(self, url, **kwargs):
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        self.calls.append((parsed.path, query))
        if parsed.path == "/robots.txt":
            return response(url=url, body=self.robots)
        if parsed.path.endswith("/FrontDetail"):
            payload = self.fixture["details"][query["id"][0]]
        elif parsed.path.endswith("/FrontList"):
            self.assertNotIn("term", query)
            rows = [r for r in self.fixture["rows"]
                    if query.get("dateStart", ["0001"])[0] <= r["date"][:10] <= query.get("dateEnd", ["9999"])[0]]
            page, size = int(query["pageNumber"][0]), int(query["pageSize"][0])
            data = dict(pageNumber=page, pageSize=size, totalCount=len(rows),
                        totalPages=(len(rows)+size-1)//size, data=rows[(page-1)*size:page*size])
            payload = dict(success=True, data=self.change_page(data))
        else:
            raise AssertionError("unexpected URL: " + url)
        return response(url=url, body=json.dumps(payload).encode("utf-8"))

    def run_sync(self, **kwargs):
        return cli.sync(self.db, self.adapter, **kwargs)

    def test_full_incremental_explicit_dates_and_empty_range(self):
        result = self.run_sync(page_size=2)
        self.assertEqual((result["since"], result["scanned_rows"], result["added"]), (None, 6, 2))
        self.assertEqual(self.run_sync()["since"], "2026-07-27")
        self.assertEqual(self.run_sync(since="2026-08-06", until="2026-08-06")["scanned_rows"], 3)
        empty = self.run_sync(since="2026-09-01")
        self.assertEqual((empty["scanned_rows"], empty["added"], empty["complete"]), (0, 0, True))

    def test_incomplete_page_and_changed_totals_fail_before_writes(self):
        for fault in ("short", "long", "totals", "metadata"):
            def change(data):
                if data["pageNumber"] == 2:
                    if fault == "short":
                        data["data"].pop()
                    elif fault == "long":
                        data["data"].append(data["data"][0])
                    elif fault == "totals":
                        data["totalCount"] += 1
                        data["totalPages"] += 1
                    else:
                        data["pageSize"] += 1
                return data
            self.change_page = change
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                self.run_sync(page_size=2)
            self.assertEqual(self.db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0], 0)
        self.assertFalse(any(p.endswith("FrontDetail") for p, _ in self.calls))

    def test_mismatch_skips_and_preserves_existing_meeting(self):
        self.run_sync()
        self.fixture["rows"].append(copy.deepcopy(self.fixture["rows"][0]))
        result = self.run_sync()
        self.assertEqual(result["warnings"][0]["reason"], "paragraph_count_mismatch")
        self.assertEqual((result["updated"], result["complete"]), (0, False))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0], 6)
        self.assertEqual(len(cli.search(self.db, "陳淑華")), 2)

    def test_duplicates_ordinals_deleted_and_idempotence(self):
        self.run_sync()
        rows = self.db.execute("SELECT ordinal, content FROM paragraphs WHERE proceedings_id=? ORDER BY ordinal", (self.ids[0],)).fetchall()
        self.assertEqual([r[0] for r in rows], [0, 1, 3])
        self.assertEqual(rows[1][1], rows[2][1])
        self.assertEqual(cli.search(self.db, "盧市長"), [])
        self.calls.clear()
        result = self.run_sync()
        self.assertEqual((result["added"], result["updated"], result["detail_requests"]), (0, 0, 0))
        self.assertEqual([r["reason"] for r in result["skipped"]], ["unchanged", "unchanged"])
        self.assertFalse(any(p.endswith("FrontDetail") for p, _ in self.calls))

    def test_rewrite_and_update_trigger_keep_fts_synchronized(self):
        self.run_sync()
        pid = self.ids[0]
        detail = self.fixture["details"][pid]["data"]
        detail["proceedingContents"] = [dict(copy.deepcopy(detail["proceedingContents"][0]), content="新的交通安全計畫")]
        self.fixture["rows"] = [self.fixture["rows"][0]] + [r for r in self.fixture["rows"] if r["proceedingsId"] != pid]
        self.assertEqual(self.run_sync()["updated"], 1)
        self.assertEqual(cli.search(self.db, "陳淑華"), [])
        self.assertEqual(len(cli.search(self.db, "交通安全")), 1)
        with self.db:
            self.db.execute("UPDATE paragraphs SET content='更新消防局預算' WHERE proceedings_id=?", (pid,))
        self.assertEqual(cli.search(self.db, "交通安全"), [])
        self.assertEqual(len(cli.search(self.db, "消防局")), 1)

    def test_write_failure_rolls_back_entire_meeting_and_fts(self):
        self.run_sync()
        self.db.executescript("CREATE TRIGGER reject_insert BEFORE INSERT ON paragraphs BEGIN SELECT RAISE(ABORT, 'test failure'); END;")
        pid = self.ids[0]
        self.fixture["rows"].append(copy.deepcopy(self.fixture["rows"][0]))
        detail = self.fixture["details"][pid]["data"]
        detail["proceedingContents"].append(copy.deepcopy(detail["proceedingContents"][0]))
        with self.assertRaises(sqlite3.IntegrityError):
            self.run_sync()
        self.assertEqual(self.db.execute("SELECT paragraph_count FROM meetings WHERE proceedings_id=?", (pid,)).fetchone()[0], 3)
        self.assertEqual(len(cli.search(self.db, "陳淑華")), 2)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM paragraphs").fetchone()[0], 6)

    def test_search_trigram_like_literal_filters_snippets_and_url(self):
        self.run_sync()
        speaker = self.fixture["details"][self.ids[0]]["data"]["proceedingContents"][1]["speaker"]["name"]
        result = cli.search(self.db, "陳淑華", speaker=speaker, start="2026-08-10", end="2026-08-10", limit=1)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["official_url"], "https://yishi.tccc.gov.tw/meeting-records/" + self.ids[0])
        self.assertEqual(result[0]["sessionDisplay"], "第4屆第8次定期會")
        self.assertEqual(cli.search(self.db, "陳淑華", speaker="不存在"), [])
        self.assertEqual(cli.search(self.db, "陳淑華", end="2026-08-09"), [])
        self.assertEqual(len(cli.search(self.db, "議員")), 3)
        with self.db:
            self.db.execute("UPDATE paragraphs SET content=? WHERE proceedings_id=? AND ordinal=0",
                            ('前'*60 + '100%_\\ OR "交通"' + '後'*60, self.ids[0]))
        for query in ("%", "_", "\\", "%_", '"交通"', ' OR '):
            with self.subTest(query=query):
                self.assertEqual(len(cli.search(self.db, query)), 1)
        snippet = cli.search(self.db, "100")[0]["snippet"]
        self.assertEqual(snippet, "…" + '前'*40 + '100%_\\ OR "交通"' + '後'*29 + "…")
        for query in ("", "\0", "x"*1001):
            with self.assertRaises(ValueError):
                cli.search(self.db, query)

    def test_cap_resume_preserves_full_window_even_for_old_meetings(self):
        for row in self.fixture["rows"]:
            if row["proceedingsId"] == self.ids[1]:
                row["date"] = "2020-01-01T00:00:00"
        first = self.run_sync(max_meetings=1)
        self.assertEqual((first["added"], first["detail_requests"], first["complete"]), (1, 1, False))
        self.assertEqual(first["skipped"][0]["reason"], "max_meetings")
        second = self.run_sync(max_meetings=1)
        self.assertEqual((second["since"], second["added"], second["detail_requests"], second["complete"]), (None, 1, 1, True))
        self.assertEqual(self.run_sync()["since"], "2026-07-27")

    def test_interruption_preserves_committed_meeting_and_resume_window(self):
        real = self.session.get.side_effect
        def interrupted(url, **kwargs):
            if url.endswith(self.ids[1]):
                raise KeyboardInterrupt()
            return real(url, **kwargs)
        self.session.get.side_effect = interrupted
        with self.assertRaises(KeyboardInterrupt):
            self.run_sync()
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0], 1)
        self.session.get.side_effect = real
        result = self.run_sync()
        self.assertEqual((result["since"], result["added"], result["detail_requests"]), (None, 1, 1))

    def test_transport_limits_registry_interval_and_robots(self):
        with patch.object(http_fetch, "get", wraps=http_fetch.get) as transport:
            self.run_sync()
        self.assertEqual(self.session.headers["User-Agent"], USER_AGENT)
        for call in transport.call_args_list:
            self.assertEqual(call.kwargs["min_interval_seconds"], cli.source_config()["min_request_interval_seconds"])
            self.assertIs(call.kwargs["robots_policy"], self.adapter.policy)
            self.assertEqual(call.args[2], {"yishi.tccc.gov.tw"})
            self.assertEqual(call.kwargs["max_bytes"], (4 if "FrontDetail" in call.args[1] else 8)*1024*1024)
        self.adapter.policy._cache.clear()
        self.robots = b"User-agent: *\nDisallow: /"
        with self.assertRaises(http_fetch.RobotsDisallowed):
            self.run_sync()

    def test_repo_db_rejected_by_cli_before_any_access(self):
        for path in (ROOT / "test.transcripts.sqlite", ROOT / "nested/../test.sqlite"):
            with patch.object(cli.sqlite3, "connect") as connect, contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(cli.main(["sync", "--db", str(path)]), 1)
                self.assertIn("outside the repository", error.getvalue())
                connect.assert_not_called()
        self.assertFalse(cli.DEFAULT_DB.resolve().is_relative_to(ROOT))

    def test_stats_and_input_validation(self):
        self.run_sync()
        path = Mock()
        path.stat.return_value.st_size = 12345
        self.assertEqual(cli.stats(self.db, path), dict(meetings=2, paragraphs=6, earliest_date="2026-08-06",
                                                       latest_date="2026-08-10", database_bytes=12345))
        for kwargs in ({"since":"20260801"}, {"since":"2026-02-30"},
                       {"since":"2026-09-01", "until":"2026-08-01"}, {"page_size":0}, {"page_size":1001}, {"max_meetings":0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_sync(**kwargs)


if __name__ == "__main__":
    unittest.main()
