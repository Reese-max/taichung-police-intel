import contextlib
import importlib.util
import io
import os
from datetime import datetime, time, timezone
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "national_sync_watchdog", ROOT / "scripts/national-sync-watchdog.py"
)
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


class NationalSyncWatchdogTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 20, 2, 0, tzinfo=timezone.utc)
        self.latest_start = time(1, 30)

    def run_record(self, **overrides):
        run = {
            "event": "schedule",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-11-20T01:00:00Z",
        }
        run.update(overrides)
        return run

    def test_successful_run_is_ok(self):
        run = self.run_record(conclusion="success")
        self.assertEqual(watchdog.decide(self.now, [run], self.latest_start), "OK")

    def test_in_progress_run_is_ok(self):
        run = self.run_record(status="in_progress", conclusion=None)
        self.assertEqual(watchdog.decide(self.now, [run], self.latest_start), "OK")

    def test_before_latest_start_waits(self):
        now = datetime(2026, 11, 20, 1, 29, tzinfo=timezone.utc)
        self.assertEqual(watchdog.decide(now, [], self.latest_start), "WAIT")

    def test_missing_run_after_latest_start_dispatches(self):
        self.assertEqual(watchdog.decide(self.now, [], self.latest_start), "DISPATCH")

    def test_failed_scheduled_run_after_latest_start_dispatches(self):
        run = self.run_record()
        self.assertEqual(watchdog.decide(self.now, [run], self.latest_start), "DISPATCH")

    def test_failed_watchdog_dispatch_alerts_without_retry(self):
        run = self.run_record(
            event="workflow_dispatch",
            triggering_actor={"login": "github-actions[bot]"},
        )
        self.assertEqual(watchdog.decide(self.now, [run], self.latest_start), "ALERT")

    def test_alert_turns_the_watchdog_run_red(self):
        # On 2026-11-23 two ALERTs ended as green runs with a warning and a day without sync went unnoticed.
        def run_main(decision):
            output = io.StringIO()
            with patch.dict(os.environ, {"GH_TOKEN": "x", "GITHUB_REPOSITORY": "o/r"}), \
                    patch.object(watchdog, "fetch_runs", return_value=[]), \
                    patch.object(watchdog, "decide", return_value=decision), \
                    patch.object(watchdog, "dispatch_workflow") as dispatch, \
                    contextlib.redirect_stdout(output):
                return watchdog.main(), output.getvalue(), dispatch

        code, output, dispatch = run_main("ALERT")
        self.assertEqual(code, 1)
        self.assertIn("::error::national-sync.yml", output)
        dispatch.assert_not_called()
        self.assertEqual(run_main("OK")[0], 0)

    def test_previous_day_run_does_not_count_today(self):
        run = self.run_record(conclusion="success", created_at="2026-11-19T23:59:00Z")
        self.assertEqual(watchdog.decide(self.now, [run], self.latest_start), "DISPATCH")


if __name__ == "__main__":
    unittest.main()
