import contextlib
import importlib.util
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from national.store import ROOT, SqliteStore


def load_cli():
    spec = importlib.util.spec_from_file_location("national_sync_scheduling", ROOT / "scripts/national-sync.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NationalSyncSchedulingTests(unittest.TestCase):
    def setUp(self):
        self.store = SqliteStore(":memory:")
        self.store.apply_schema()
        self.addCleanup(self.store.close)
        self.cli = load_cli()
        self.registry = json.loads((ROOT / "docs/govintel/national-sources.v1.json").read_text(encoding="utf-8"))
        self.sources = {source["national_source_id"]: source for source in self.registry["sources"]}

    def record_run(self, source_id, *, status="OK", days_ago=0, cursor_after=None):
        finished = datetime.now(timezone.utc) - timedelta(days=days_ago)
        self.store.execute_batch(
            "INSERT INTO runs (run_id,source_id,started_at,finished_at,status,fetched,changed,rows_written,"
            "cursor_before,cursor_after,error) VALUES ('" + str(uuid4()) + "',"
            "'" + source_id + "','" + finished.isoformat() + "','" + finished.isoformat() + "','" + status
            + "',0,0,0,NULL," + ("NULL" if cursor_after is None else "'{}'") + ",NULL);"
        )

    def invoke(self, argv, *, source_id="N-COUNCIL-TXG"):
        constructor = Mock(return_value=Mock())
        output = io.StringIO()
        errors = io.StringIO()
        with patch.object(self.cli, "ADAPTERS", {"txg": constructor}), \
                patch.object(self.cli, "SOURCE_ADAPTERS", {source_id: "txg"}), \
                patch.object(self.cli, "SqliteStore", return_value=self.store), \
                patch.object(self.store, "close"), \
                patch.object(self.cli, "load", return_value={"status": "OK", "exit_code": 0, "warnings": []}) as load_call, \
                patch.dict(self.cli.os.environ, {"NATIONAL_RUNNER": "github-actions"}), \
                patch.object(self.cli.sys, "argv", ["national-sync", "--store", "sqlite::memory:", "--source", source_id, *argv]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            code = self.cli.main()
        report_output = errors.getvalue() if "--dry-run" in argv else output.getvalue()
        return code, json.loads(report_output), constructor, load_call

    def test_fresh_success_skips_and_ignore_fresh_runs(self):
        source = self.sources["N-COUNCIL-TXG"]
        self.assertEqual(source["scope"]["refresh_days"], 7)
        self.record_run(source["national_source_id"], days_ago=3)
        code, report, constructor, load_call = self.invoke([])
        self.assertEqual(code, 0)
        self.assertEqual(report["runs"][0]["reason"], "FRESH")
        constructor.assert_not_called()
        load_call.assert_not_called()
        code, _, constructor, load_call = self.invoke(["--ignore-fresh"])
        self.assertEqual(code, 0)
        constructor.assert_called_once()
        load_call.assert_called_once()

    def test_fresh_requires_latest_complete_success(self):
        sid = "N-COUNCIL-TXG"
        for run in ({"status": "OK", "days_ago": 8}, {"status": "BUDGET_STOPPED", "days_ago": 1},
                    {"status": "OK", "days_ago": 1, "cursor_after": "cursor"}):
            self.record_run(sid, **run)
        _, report, constructor, _ = self.invoke([])
        self.assertNotEqual(report["runs"][0].get("reason"), "FRESH")
        constructor.assert_called_once()

    def test_fresh_window_ends_after_refresh_days(self):
        self.record_run("N-COUNCIL-TXG", days_ago=7)
        _, report, constructor, _ = self.invoke([])
        self.assertNotEqual(report["runs"][0].get("reason"), "FRESH")
        constructor.assert_called_once()

    def test_reserve_rows_skips_source_before_fetching(self):
        with patch.object(self.store, "rows_written_today", return_value=70_000):
            _, report, constructor, _ = self.invoke(["--reserve-rows", "20000"])
            self.assertEqual(report["runs"][0]["reason"], "DAILY_BUDGET")
            constructor.assert_not_called()
            _, report, constructor, _ = self.invoke([])
            self.assertNotEqual(report["runs"][0].get("reason"), "DAILY_BUDGET")
            constructor.assert_called_once()

    def test_skip_if_ran_today_ignores_other_runner_sources(self):
        self.record_run("N-COUNCIL-TPE")  # runner=xisx-pc
        _, report, constructor, _ = self.invoke(["--skip-if-ran-today"])
        self.assertNotEqual(report.get("status"), "ALREADY_RAN")
        constructor.assert_called_once()

    def test_skip_if_ran_today_is_runner_scoped_and_dry_run_is_exempt(self):
        self.record_run("N-COUNCIL-TXG")
        code, report, constructor, load_call = self.invoke(["--skip-if-ran-today"])
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "ALREADY_RAN")
        self.assertEqual(report["runs"][0]["reason"], "ALREADY_RAN")
        constructor.assert_not_called()
        load_call.assert_not_called()
        code, _, constructor, load_call = self.invoke(["--skip-if-ran-today", "--dry-run"])
        self.assertEqual(code, 0)
        constructor.assert_called_once()
        load_call.assert_called_once()

    def test_explicit_source_keeps_selected_order_and_reserve_is_passed_to_loader(self):
        _, _, _, load_call = self.invoke(["--reserve-rows", "20000"])
        self.assertEqual(load_call.call_args.kwargs["daily_limit"], 70_000)
        store = Mock(database_size_bytes=Mock(return_value=0), rows_written_today=Mock(return_value=70_000))
        self.assertEqual(self.cli.check_guards(store, daily_limit=70_000)["status"], "BUDGET_STOPPED")
        self.assertEqual(self.cli.check_guards(store)["status"], "OK")

    def test_default_order_runs_non_council_sources_before_councils(self):
        constructors = {name: Mock(return_value=Mock()) for name in ("txg", "ly20")}
        calls = []
        output = io.StringIO()
        with patch.object(self.cli, "ADAPTERS", constructors), \
                patch.object(self.cli, "SOURCE_ADAPTERS", {"N-COUNCIL-TXG": "txg", "N-LY-ID20-BILLS": "ly20"}), \
                patch.object(self.cli, "SqliteStore", return_value=self.store), \
                patch.object(self.store, "close"), \
                patch.object(self.cli, "load", side_effect=lambda store, source, adapter, **kwargs:
                             calls.append(source["national_source_id"]) or {"status": "OK", "exit_code": 0, "warnings": []}), \
                patch.dict(self.cli.os.environ, {"NATIONAL_RUNNER": "github-actions"}), \
                patch.object(self.cli.sys, "argv", ["national-sync", "--dry-run"]), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.cli.main(), 0)
        self.assertEqual(calls, ["N-LY-ID20-BILLS", "N-COUNCIL-TXG"])


if __name__ == "__main__":
    unittest.main()
