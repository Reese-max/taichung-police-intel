from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import collect
import online_collect
from intel_v2.lkg_age import last_known_good_age


NOW = datetime.fromisoformat("2026-10-05T10:00:00+00:00")
COMPLETED = "2026-10-03T10:00:00Z"


class LastKnownGoodAgeTests(unittest.TestCase):
    def test_elapsed_clock_changes_without_modifying_the_saved_snapshot(self):
        saved = {"source_run_id": "SR-RETAINED", "completed_at": COMPLETED,
                 "data_as_of": "2026-09-01T00:00:00Z"}
        original = copy.deepcopy(saved)
        first = last_known_good_age(saved, NOW)
        second = last_known_good_age(saved, NOW + timedelta(days=1))
        self.assertEqual((first["status"], first["age_hours"]), ("KNOWN", 48.0))
        self.assertEqual(second["age_hours"], 72.0)
        self.assertEqual(saved, original)
        self.assertEqual(first["completed_at"], COMPLETED)

    def test_offsets_current_and_fractional_completion(self):
        for value, expected in [("2026-10-03T18:00:00+08:00", 48.0),
                                ("2026-10-05T10:00:00Z", 0.0),
                                ("2026-10-05T09:30:00.000000Z", 0.5)]:
            with self.subTest(value=value):
                actual = last_known_good_age({"completed_at": value}, NOW)
                self.assertEqual(actual["status"], "KNOWN")
                self.assertEqual(actual["age_hours"], expected)

    def test_missing_empty_and_unrelated_clocks_never_supply_an_age(self):
        for saved in [None, [], "not-a-record", {}, {"completed_at": None},
                      {"completed_at": ""}, {"last_success_at": COMPLETED},
                      {"last_checked_at": COMPLETED}, {"data_as_of": COMPLETED},
                      {"generated_at": COMPLETED}]:
            with self.subTest(saved=saved):
                actual = last_known_good_age(saved, NOW)
                self.assertEqual(actual["status"], "UNKNOWN")
                self.assertIsNone(actual["age_hours"])

    def test_invalid_calendar_offset_types_and_future_are_unknown(self):
        for value in [True, 1, {}, "not-a-date", "2026-10-03", "2026-10-03T10:00:00",
                      "2026-02-30T10:00:00Z", "0000-01-01T00:00:00Z",
                      "2026-10-03T10:00:00+08:60", "2026-10-03T10:00:00+24:00",
                      "2026-10-03T25:00:00Z", "2026-10-03T10:00:61Z",
                      "2026-10-06T10:00:00Z"]:
            with self.subTest(value=value):
                actual = last_known_good_age({"completed_at": value}, NOW)
                self.assertEqual(actual["status"], "UNKNOWN")
                self.assertIsNone(actual["age_hours"])

    def test_invalid_observation_cannot_produce_zero_age(self):
        for observed in [None, "2026-10-05T10:00:00Z", datetime(2026, 10, 5), 1, True]:
            with self.subTest(observed=observed):
                result = last_known_good_age({"completed_at": COMPLETED}, observed)
                self.assertEqual(result["reason"], "INVALID_OBSERVED_AT")
                self.assertIsNone(result["age_hours"])

    def test_microsecond_precision_preserves_future_and_past_boundaries(self):
        for value in ["2026-10-05T10:00:00.000001Z", "2026-10-05T18:00:00.000001+08:00"]:
            self.assertEqual(last_known_good_age({"completed_at": value}, NOW)["reason"], "FUTURE_COMPLETED_AT")
        for value in ["2026-10-05T09:59:59.999999Z", "2026-10-05T17:59:59.999999+08:00"]:
            self.assertEqual(last_known_good_age({"completed_at": value}, NOW)["age_hours"], 1 / 3_600_000_000)

    def test_utc_normalization_range_boundaries_are_unknown_without_raising(self):
        for value in ["9999-12-31T23:59:59-08:00", "0001-01-01T00:00:00+08:00"]:
            self.assertEqual(last_known_good_age({"completed_at": value}, NOW)["status"], "UNKNOWN")
            observed = datetime.fromisoformat(value)
            self.assertEqual(last_known_good_age({"completed_at": COMPLETED}, observed)["reason"], "INVALID_OBSERVED_AT")
        for value in ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z"]:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            self.assertEqual(last_known_good_age({"completed_at": value}, observed)["age_hours"], 0)

    def test_official_freshness_remains_a_separate_clock(self):
        saved = {"completed_at": NOW.isoformat()}
        self.assertEqual(last_known_good_age(saved, NOW)["age_hours"], 0.0)
        official = "2026-01-01T00:00:00Z"
        self.assertEqual(collect.freshness_status(official, NOW, *collect.SOURCE_FRESHNESS_POLICY["S-004"]), "VERY_STALE")

    def test_real_fixture_success_records_existing_observation_and_failure_keeps_lkg(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            first, _, _ = collect.run_slot("MORNING", date(2026, 10, 5), path, now=NOW)
            saved = copy.deepcopy(first["last_known_good"]["S-006"])
            self.assertEqual(saved["completed_at"], saved["last_success_at"])
            self.assertEqual(saved["completed_at"], collect.timestamp(NOW.astimezone(collect.TZ)))
            self.assertEqual(first["source_status"]["S-006"]["last_known_good_age"]["age_hours"], 0.0)
            later = NOW + timedelta(hours=12)
            second, _, _ = collect.run_slot("EVENING", date(2026, 10, 5), path, now=later, broken_source="S-006")
            self.assertEqual(second["last_known_good"]["S-006"], saved)
            self.assertEqual(second["source_status"]["S-006"]["last_known_good_age"]["age_hours"], 12.0)
            self.assertEqual(second["source_status"]["S-006"]["result"], "FAILED")

    def test_legacy_lkg_without_completion_does_not_use_its_success_clock(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state.json"
            first, _, _ = collect.run_slot("MORNING", date(2026, 10, 5), path, now=NOW)
            del first["last_known_good"]["S-006"]["completed_at"]
            collect.save_state(path, first)
            legacy = copy.deepcopy(first["last_known_good"]["S-006"])
            second, _, _ = collect.run_slot("EVENING", date(2026, 10, 5), path, now=NOW + timedelta(hours=12), broken_source="S-006")
            self.assertEqual(second["last_known_good"]["S-006"], legacy)
            self.assertEqual(second["source_status"]["S-006"]["last_known_good_age"]["status"], "UNKNOWN")

    def test_actual_demo_failure_retains_age_clock_and_official_data_time(self):
        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW.astimezone(tz or timezone.utc)

        for completed, expected in [(COMPLETED, 48.0), (None, None),
                                     ("malformed", None), ("2026-10-06T10:00:00Z", None)]:
            with self.subTest(completed=completed), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "source-status.json"
                saved = {"source_run_id": "SR-OLD", "completed_at": completed, "manifest_sha256": "a" * 64}
                official = "2026-09-01T00:00:00+08:00"
                previous = {"schema_version": 1, "mode": "COMPETITION_DEMO", "sources": [
                    {"source_id": sid, "last_known_good": copy.deepcopy(saved),
                     "data_as_of": official, "last_success_at": COMPLETED}
                    for sid in online_collect.P0_SOURCES]}
                path.write_text(json.dumps(previous), encoding="utf-8")
                with patch.object(online_collect, "datetime", FixedClock), \
                     patch.object(online_collect, "http_session", return_value=object()), \
                     patch.object(online_collect, "collect_source", side_effect=RuntimeError("offline failure")), \
                     redirect_stdout(io.StringIO()):
                    result = online_collect.build_demo_status(path, "EVENING", date(2026, 10, 5), "manual")
                row = next(item for item in result["sources"] if item["source_id"] == "S-004")
                self.assertEqual(row["last_known_good"], saved)
                self.assertEqual(row["data_as_of"], official)
                self.assertEqual(row["last_success_at"], COMPLETED)
                self.assertEqual(row["last_known_good_age"]["age_hours"], expected)
                self.assertEqual(row["last_known_good_age"]["status"], "KNOWN" if expected is not None else "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
