import contextlib
from datetime import date, datetime, timezone
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from collect import TZ, resolve_collection_date, scheduled_time
import online_collect as collector


class CollectionSlotDateTests(unittest.TestCase):
    def test_observed_evening_schedule_delayed_across_taipei_midnight(self):
        clock = datetime.fromisoformat("2026-10-06T03:20:19+08:00")
        selected = resolve_collection_date("evening", "schedule", now=clock)
        self.assertEqual(selected, date(2026, 10, 5))
        self.assertEqual(scheduled_time(selected, "EVENING").isoformat(), "2026-10-05T18:30:00+08:00")
        self.assertLessEqual(scheduled_time(selected, "EVENING"), clock)

    def test_scheduled_slots_use_taipei_clock_and_exact_due_boundaries(self):
        cases = [
            ("morning", "2026-10-05T22:29:59+00:00", date(2026, 10, 5)),
            ("morning", "2026-10-05T22:30:00+00:00", date(2026, 10, 6)),
            ("morning", "2026-10-06T00:17:00+00:00", date(2026, 10, 6)),
            ("evening", "2026-10-06T10:29:59+00:00", date(2026, 10, 5)),
            ("evening", "2026-10-06T10:30:00+00:00", date(2026, 10, 6)),
            ("evening", "2026-10-06T16:01:00+00:00", date(2026, 10, 6)),
        ]
        for slot, value, expected in cases:
            with self.subTest(slot=slot, clock=value):
                clock = datetime.fromisoformat(value)
                selected = resolve_collection_date(slot, "schedule", now=clock)
                self.assertEqual(selected, expected)
                self.assertLessEqual(scheduled_time(selected, slot.upper()), clock)

    def test_manual_explicit_prior_date_is_preserved_after_midnight(self):
        clock = datetime.fromisoformat("2026-10-06T00:16:44+08:00")
        self.assertEqual(resolve_collection_date("evening", "manual", date(2026, 10, 5), now=clock), date(2026, 10, 5))

    def test_manual_date_defaults_to_today_without_silently_backdating(self):
        clock = datetime.fromisoformat("2026-10-06T08:17:00+08:00")
        self.assertEqual(resolve_collection_date("morning", "manual", now=clock), date(2026, 10, 6))
        with self.assertRaisesRegex(ValueError, "not due"):
            resolve_collection_date("evening", "manual", now=clock)

    def test_explicit_future_date_is_rejected_for_both_triggers(self):
        clock = datetime.fromisoformat("2026-10-06T08:17:00+08:00")
        for trigger in ("manual", "schedule"):
            with self.subTest(trigger=trigger), self.assertRaisesRegex(ValueError, "not due"):
                resolve_collection_date("morning", trigger, date(2026, 10, 7), now=clock)

    def test_naive_clock_and_unknown_slot_or_trigger_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            resolve_collection_date("morning", "schedule", now=datetime(2026, 10, 6))
        for slot, trigger in [("code", "schedule"), ("morning", "push")]:
            with self.subTest(slot=slot, trigger=trigger), self.assertRaisesRegex(ValueError, "unsupported"):
                resolve_collection_date(slot, trigger, now=datetime(2026, 10, 6, tzinfo=timezone.utc))

    def test_future_demo_window_fails_before_transport_or_state_read_and_write(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "source-status.json"
            feed = output.with_name("intelligence-feed.json")
            output.write_bytes(b"unchanged prior status")
            feed.write_bytes(b"unchanged prior feed")
            with mock.patch.object(collector, "datetime", wraps=datetime) as clock, mock.patch.object(collector, "http_session") as session:
                clock.now.return_value = datetime(2026, 10, 6, 3, 20, 19, tzinfo=TZ)
                with self.assertRaisesRegex(ValueError, "not due"):
                    collector.build_demo_status(output, "EVENING", date(2026, 10, 6), "schedule")
                session.assert_not_called()
            self.assertEqual(output.read_bytes(), b"unchanged prior status")
            self.assertEqual(feed.read_bytes(), b"unchanged prior feed")

    def test_actual_cli_resolves_delayed_schedule_before_collecting(self):
        with mock.patch.object(collector, "datetime", wraps=datetime) as clock, mock.patch.object(collector, "build_demo_status") as build, mock.patch(
            "sys.argv", ["online_collect.py", "--slot", "evening", "--trigger", "schedule", "--demo-output", "/unused-status.json"]
        ):
            clock.now.return_value = datetime(2026, 10, 6, 3, 20, 19, tzinfo=TZ)
            self.assertEqual(collector.main(), 0)
            build.assert_called_once_with(Path("/unused-status.json"), "EVENING", date(2026, 10, 5), "schedule")

    def test_actual_cli_refuses_future_manual_window_before_collecting(self):
        with mock.patch.object(collector, "datetime", wraps=datetime) as clock, mock.patch.object(collector, "build_demo_status") as build, mock.patch(
            "sys.argv", ["online_collect.py", "--slot", "evening", "--slot-date", "2026-10-06", "--demo-output", "/unused-status.json"]
        ), contextlib.redirect_stderr(io.StringIO()) as stderr:
            clock.now.return_value = datetime(2026, 10, 6, 3, 20, 19, tzinfo=TZ)
            with self.assertRaises(SystemExit) as exit_result:
                collector.main()
            self.assertEqual(exit_result.exception.code, 2)
            self.assertIn("not due", stderr.getvalue())
            build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
