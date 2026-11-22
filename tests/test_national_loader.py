import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from national.adapters.fixture import FixtureAdapter
from national.loader import RECORD_WRITE_ESTIMATE, batches, digest, load, normalize, sql_value
from national.store import ROOT, RUN_WRITE_RESERVE, SqliteStore, WranglerD1Store, utc_now

FIXTURE = ROOT / "tests/fixtures/national/txg.json"
REGISTRY = json.loads((ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))
SOURCE = next(s for s in REGISTRY["sources"] if s["national_source_id"] == "N-COUNCIL-TXG")


class NationalLoaderTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteStore(":memory:")
        self.addCleanup(self.store.close)
        self.store.apply_schema()
        self.source = copy.deepcopy(SOURCE)
        self.adapter = FixtureAdapter(FIXTURE)

    def run_load(self, **kwargs):
        return load(self.store, self.source, self.adapter, runner="github-actions", **kwargs)

    def test_idempotent_schema_and_import(self):
        self.store.apply_schema()
        first = self.run_load()
        self.assertEqual((first["status"], first["changed"]), ("OK", 3))
        self.assertGreater(first["rows_written"], 3)
        self.adapter.records[0]["fetched_at"] = "2026-11-19T00:00:00Z"
        second = self.run_load()
        self.assertEqual((second["changed"], second["rows_written"]), (0, 0))
        self.assertEqual(self.store.rows_written_today(), first["rows_written"] + 2 * RUN_WRITE_RESERVE)
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM records")[0]["n"], 3)

    def test_existing_hashes_batches_by_command_size(self):
        self.run_load()
        known = [r["external_id"] for r in self.store.query("SELECT external_id FROM records")]
        filler = [f"id-{n:04d}" for n in range(1000)]
        # Known ids at chunk edges: first, around the ~155th value (first 5,000-char boundary) and last.
        ids = [known[0]] + filler[:154] + [known[1]] + filler[154:] + ["x" * 512, "y'" * 200, known[2]]
        sent = []
        real = self.store.query
        def query(sql):
            sent.append(sql)
            return real(sql)
        with patch.object(self.store, "query", side_effect=query):
            found = self.store.existing_hashes(SOURCE["national_source_id"], ids)
        self.assertEqual(set(found), set(known))
        values = [v for sql in sent for v in sql.split(" IN (", 1)[1][:-1].split(",")]
        self.assertEqual(values, [sql_value(v) for v in ids])  # every id exactly once, in order
        self.assertLessEqual(len(sent), 8)  # hex-encoded ids ~32 chars each; 10-id chunks took 101 calls
        self.assertTrue(all(len(sql.split(" IN (", 1)[1]) <= 5002 for sql in sent))
        self.assertTrue(all(len(sql) < 5200 for sql in sent), [len(sql) for sql in sent])
        self.assertEqual(self.store.existing_hashes(SOURCE["national_source_id"], []), {})

    def test_update_one_and_fts_delete_trigger(self):
        self.run_load()
        self.adapter.records[0]["title"] = "新標題：道路安全測試"
        result = self.run_load()
        self.assertEqual(result["changed"], 1)
        self.assertEqual(len(self.store.query("SELECT rowid FROM records_fts WHERE records_fts MATCH '道路安全'")), 1)
        self.assertEqual(self.store.query("SELECT rowid FROM records_fts WHERE records_fts MATCH '通學道路'"), [])
        self.store.execute_batch("DELETE FROM records WHERE external_id='fixture-txg-001';")
        self.assertEqual(self.store.query("SELECT rowid FROM records_fts WHERE records_fts MATCH '道路安全'"), [])

    def test_old_schema_migration_and_people_fts_triggers(self):
        old = SqliteStore(":memory:")
        self.addCleanup(old.close)
        old.execute_batch("""
            CREATE TABLE records (
              id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, kind TEXT NOT NULL,
              jurisdiction TEXT, external_id TEXT NOT NULL, title TEXT NOT NULL,
              case_number TEXT, event_date TEXT, authority TEXT, official_url TEXT NOT NULL,
              provider_url TEXT, fetched_at TEXT NOT NULL, content_sha256 TEXT NOT NULL,
              license TEXT, rights_class TEXT NOT NULL, UNIQUE(source_id, external_id)
            );
            CREATE VIRTUAL TABLE records_fts USING fts5(
              title, case_number, content='records', content_rowid='id', tokenize='trigram'
            );
            INSERT INTO records (source_id, kind, external_id, title, official_url, fetched_at, content_sha256, rights_class)
              VALUES ('N-COUNCIL-TXG', 'bill', 'old', '舊議案', 'https://example.org', '2026-01-01', 'hash', 'METADATA_ONLY');
            INSERT INTO records_fts(rowid, title, case_number) VALUES (1, '舊議案', NULL);
        """)
        old.apply_schema()
        old.apply_schema()
        self.assertTrue({"proposer", "cosigners", "committee", "outcome"} <=
                        {row["name"] for row in old.query("PRAGMA table_info(records)")})
        self.assertEqual(old.query("SELECT proposer FROM records")[0]["proposer"], None)
        old.execute_batch("""
            INSERT INTO records (source_id,kind,external_id,title,official_url,fetched_at,content_sha256,rights_class,proposer)
              VALUES ('N-COUNCIL-TXG','bill','new','新議案','https://example.org','2026-01-01','hash','METADATA_ONLY','李大華');
        """)
        old.execute_batch("UPDATE records SET proposer='吳瓊華', cosigners='張瀞分', committee='警察委員會' WHERE id=1;")
        def matches(term):
            return old.query("SELECT rowid FROM bill_people_fts WHERE bill_people_fts MATCH '" + term + "'")
        self.assertEqual(matches("李大華"), [{"rowid": 2}])
        self.assertEqual(matches("吳瓊華"), [{"rowid": 1}])
        self.assertEqual(matches("張瀞分"), [{"rowid": 1}])
        old.execute_batch("UPDATE records SET proposer='陳小明' WHERE id=1;")
        self.assertEqual(matches("吳瓊華"), [])
        self.assertEqual(matches("陳小明"), [{"rowid": 1}])
        old.execute_batch("UPDATE records SET proposer=NULL WHERE id=1;")
        self.assertEqual(matches("陳小明"), [])
        old.execute_batch("UPDATE records SET proposer='王大明' WHERE id=1;")
        self.assertEqual(matches("王大明"), [{"rowid": 1}])
        old.execute_batch("DELETE FROM records WHERE id=1;")
        self.assertEqual(matches("王大明"), [])

        self.run_load()
        self.adapter.records[0]["proposer"] = "林小明"
        self.assertEqual(self.run_load()["changed"], 1)
        self.assertEqual(self.run_load()["changed"], 0)

    def test_reject_nonpublic_fields_and_bad_values(self):
        for update in ({"official_url": "https://evil.example/"}, {"official_url": "http://yishi.tccc.gov.tw/"},
                       {"provider_url": "https://evil.example/"}, {"official_url": "https://u:p@yishi.tccc.gov.tw/"},
                       {"official_url": "https://yishi.tccc.gov.tw:444/"}, {"official_url": "https://yishi.tccc.gov.tw/\n"},
                       {"full_text": "private"}, {"excerpt": "private"}, {"id": "1"},
                       {"content_sha256": "forged"}, {"title": " "}, {"external_id": ""},
                       {"event_date": "2026-02-30"}, {"event_date": "20261118"}, {"title": {}},
                       {"source_id": "wrong"}, {"rights_class": "OPEN_METADATA"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                normalize(self.source, {**self.adapter.records[0], **update})
        self.source["public_fields"].remove("case_number")
        with self.assertRaises(ValueError):
            normalize(self.source, self.adapter.records[0])

    def test_sql_special_characters_round_trip(self):
        value = "單引號' 反斜線\\ NUL\0 換行\n'); DROP TABLE records;--"
        self.adapter.records[0].update(title=value, external_id=value, case_number=value)
        result = self.run_load()
        self.assertEqual(result["status"], "OK")
        row = self.store.query("SELECT title,external_id,case_number FROM records ORDER BY id LIMIT 1")[0]
        self.assertEqual(row, dict(title=value, external_id=value, case_number=value))
        self.assertEqual(self.run_load()["changed"], 0)

    def test_batches_are_under_100kb_and_oversized_record_rejected(self):
        records = [normalize(self.source, {**self.adapter.records[0], "external_id": str(i), "title": "測試中文" * 200})
                   for i in range(90)]
        sql_batches = list(batches(records))
        self.assertGreater(len(sql_batches), 1)
        self.assertEqual(sum(count for _, count in sql_batches), 90)
        for sql, _ in sql_batches:
            self.assertLess(len(sql.encode("utf-8")) + 200, 100_000)
        with self.assertRaises(ValueError):
            list(batches([normalize(self.source, {**self.adapter.records[0], "title": "大" * 30_000})]))

    def test_budget_cursor_resume_and_no_duplicates(self):
        self.adapter.page_size = 1
        # Admit source + first record, but not the next conservative estimate.
        first = self.run_load(daily_limit=RECORD_WRITE_ESTIMATE + 4)
        self.assertEqual((first["status"], first["exit_code"], first["changed"], first["cursor_after"]),
                         ("BUDGET_STOPPED", 0, 1, 1))
        self.assertEqual(self.store.resume_cursor(SOURCE["national_source_id"]), 1)
        second = self.run_load()
        self.assertEqual((second["status"], second["changed"], second["cursor_before"]), ("OK", 2, 1))
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM records")[0]["n"], 3)
        self.assertEqual(self.run_load()["changed"], 0)

    def test_capacity_guard_and_no_record_write(self):
        result = self.run_load(capacity_limit_mb=0)
        self.assertEqual((result["status"], result["exit_code"]), ("CAPACITY_BLOCKED", 1))
        self.assertEqual(self.store.query("SELECT status FROM runs")[0]["status"], "CAPACITY_BLOCKED")
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM records")[0]["n"], 0)

    def test_exhausted_budget_does_not_write_even_an_audit_row(self):
        result = self.run_load(daily_limit=0)
        self.assertEqual((result["status"], result["rows_written"]), ("BUDGET_STOPPED", 0))
        self.assertFalse(result["audit_recorded"])
        self.assertEqual(self.store.rows_written_today(), 0)

    def test_utc_budget_excludes_prior_days(self):
        self.run_load()
        self.store.execute_batch("UPDATE runs SET started_at='2000-01-01T23:59:59+00:00';")
        self.assertEqual(self.store.rows_written_today(), 0)

    def test_runner_mismatch_does_not_fetch_or_write(self):
        self.source["runner"] = "xisx-pc"
        with patch.object(self.adapter, "fetch", side_effect=AssertionError("must not fetch")):
            result = self.run_load()
        self.assertEqual(result["reason"], "RUNNER_MISMATCH")
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM runs")[0]["n"], 0)

    def test_source_registry_upsert_only_on_change(self):
        self.run_load()
        self.source["notes"].append("fixture revision")
        result = self.run_load()
        self.assertEqual((result["changed"], result["rows_written"]), (0, 1))
        self.assertEqual(self.store.source_hash(SOURCE["national_source_id"]), digest(self.source))

    def test_dry_run_does_not_execute_sql(self):
        output = []
        result = self.run_load(dry_run=True, emit=output.append)
        self.assertEqual(result["changed"], 3)
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM sources")[0]["n"], 0)
        self.assertEqual(self.store.query("SELECT COUNT(*) AS n FROM runs")[0]["n"], 0)
        self.assertIn("ON CONFLICT", "".join(output))

    def test_error_retains_cursor_and_receipts_completed_writes(self):
        self.adapter.page_size = 1
        self.adapter.records[1]["full_text"] = "private"
        result = self.run_load()
        self.assertEqual((result["status"], result["changed"], result["cursor_after"]), ("ERROR", 1, 1))
        self.assertGreater(result["rows_written"], 0)
        del self.adapter.records[1]["full_text"]
        self.assertEqual(self.run_load()["changed"], 2)

    def test_cli_dry_run_does_not_create_database(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            database = Path(directory) / "dry.db"
            result = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts/national-sync.py"),
                                     "--source", SOURCE["national_source_id"], "--adapter", "fixture",
                                     "--fixture", str(FIXTURE), "--store", "sqlite:" + str(database),
                                     "--dry-run", "--apply-schema"], capture_output=True, text=True,
                                    encoding="utf-8", env={**os.environ, "NATIONAL_RUNNER": "github-actions"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(database.exists())
            self.assertIn("CREATE TABLE IF NOT EXISTS", result.stdout)
            self.assertEqual(json.loads(result.stderr)["runs"][0]["changed"], 3)

    def test_wrangler_receipts_and_local_size_never_use_remote_info(self):
        with patch("national.store.shutil.which", return_value="wrangler"):
            remote = WranglerD1Store(remote=True)
            local = WranglerD1Store()
        with patch.object(remote, "_execute", return_value=[{"meta": {"rows_written": 7}, "results": []}]):
            self.assertEqual(remote.execute_batch("SELECT 1;"), 7)
        progress = ("├ Checking if file needs uploading\n│\n├ 🌀 Uploading x.sql\n│ 🌀 Uploading complete.\n│\n"
                    '[{"results": [], "success": true, "meta": {"rows_written": 9}}]\n')
        for stdout, expected in ((progress, 9), ('[{"results": [], "meta": {"rows_written": 2}}]', 2)):
            with patch("national.store.subprocess.run",
                       return_value=Mock(returncode=0, stdout=stdout)):
                self.assertEqual(remote.execute_batch("SELECT 1;"), expected)
        with patch("national.store.subprocess.run", return_value=Mock(returncode=0, stdout="├ no json\n")), \
                self.assertRaisesRegex(RuntimeError, "no JSON"):
            remote.query("SELECT 1")
        for info in ({"file_size": 123}, {"database_size": 456}):
            with patch.object(remote, "_call", return_value=[info]):
                self.assertEqual(remote.database_size_bytes(), next(iter(info.values())))
        with patch.object(local, "_execute", return_value=[{"results": [{"national_changes": 4}]},
                                                          {"results": []},
                                                          {"results": [{"national_changes": 17}]}]) as execute:
            self.assertEqual(local.execute_batch("SELECT 1;"), 13)
            self.assertEqual(execute.call_args.args[0], "--file")
        with tempfile.TemporaryDirectory() as directory:
            local.cwd = Path(directory)
            state = local.cwd / ".wrangler/state/v3/d1/miniflare-D1DatabaseObject"
            state.mkdir(parents=True)
            (state / "db.sqlite").write_bytes(b"x" * 4096)
            (state / "db.sqlite-wal").write_bytes(b"x" * 100)
            with patch.object(local, "_call", side_effect=AssertionError("must not call remote info")):
                self.assertEqual(local.database_size_bytes(), 4096)


if __name__ == "__main__":
    unittest.main()
