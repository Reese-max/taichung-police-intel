from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
import uuid
from datetime import date, datetime
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from collect import P0_SOURCES, TZ, run_slot


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads(
    (ROOT / "evaluation" / "ingestion-record.schema.json").read_text(encoding="utf-8")
)
EXAMPLE = json.loads(
    (ROOT / "evaluation" / "ingestion-record.example.json").read_text(encoding="utf-8")
)
MIGRATIONS = sorted((ROOT / "migrations").glob("[0-9][0-9][0-9][0-9]_*.sql"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
TABLES = {
    "sources",
    "collection_runs",
    "source_runs",
    "raw_items",
    "events",
    "validation_runs",
    "claims",
    "evidence",
    "gaps",
    "snapshot_blobs",
    "source_snapshots",
}


def first(bundle: dict, record_type: str) -> dict:
    return next(record for record in bundle["records"] if record["record_type"] == record_type)


def assert_bundle_invariants(bundle: dict) -> None:
    VALIDATOR.validate(bundle)
    records = bundle["records"]
    present = {record["record_type"] for record in records}
    expected = {
        "source",
        "collection_run",
        "source_run",
        "raw_item",
        "event",
        "validation_run",
        "claim",
        "evidence",
        "gap",
    }
    if not expected <= present:
        raise ValueError(f"missing record types: {sorted(expected - present)}")

    sources = {record["source_id"] for record in records if record["record_type"] == "source"}
    collection_runs = {
        record["collection_run_id"]
        for record in records
        if record["record_type"] == "collection_run"
    }
    source_runs = {
        record["source_run_id"]: record
        for record in records
        if record["record_type"] == "source_run"
    }
    raw_items = {
        record["raw_item_id"]: record
        for record in records
        if record["record_type"] == "raw_item"
    }
    events = {
        record["event_id"]: record for record in records if record["record_type"] == "event"
    }
    validations = {
        record["validation_run_id"]: record
        for record in records
        if record["record_type"] == "validation_run"
    }
    claims = {
        record["claim_id"]: record for record in records if record["record_type"] == "claim"
    }

    scheduled_slots: set[tuple[str, str]] = set()
    for record in records:
        if record["record_type"] == "collection_run" and record["slot"] != "MANUAL":
            key = (record["slot_date"], record["slot"])
            if key in scheduled_slots:
                raise ValueError(f"duplicate scheduled slot: {key}")
            scheduled_slots.add(key)

    for record in source_runs.values():
        if record["source_id"] not in sources or record["collection_run_id"] not in collection_runs:
            raise ValueError("source_run has an unknown source or collection_run")
        previous = record["previous_successful_source_run_id"]
        if previous and (
            previous not in source_runs or source_runs[previous]["source_id"] != record["source_id"]
        ):
            raise ValueError("previous successful run must belong to the same source")

    for record in raw_items.values():
        run = source_runs.get(record["source_run_id"])
        if not run or run["source_id"] != record["source_id"]:
            raise ValueError("raw_item must reference a source_run for the same source")

    for record in events.values():
        if record["raw_item_id"] not in raw_items:
            raise ValueError("event has an unknown raw_item")

    for record in claims.values():
        producer = validations.get(record["producer_run_id"])
        verifier = validations.get(record["verifier_run_id"])
        if record["producer_run_id"] == record["verifier_run_id"]:
            raise ValueError("producer and verifier run IDs must differ")
        if not producer or producer["run_role"] != "PRODUCER":
            raise ValueError("claim has no producer run")
        if not verifier or verifier["run_role"] != "VERIFIER":
            raise ValueError("claim has no independent verifier run")
        if record["event_id"] not in events:
            raise ValueError("claim has an unknown event")

    for record in records:
        if record["record_type"] == "evidence":
            if record["claim_id"] not in claims or record["raw_item_id"] not in raw_items:
                raise ValueError("evidence has an unknown claim or raw_item")


class SourceIngestionContractTests(unittest.TestCase):
    def test_example_bundle_is_valid_and_linked(self) -> None:
        assert_bundle_invariants(EXAMPLE)

    def test_schema_rejects_missing_provenance(self) -> None:
        cases = [
            ("raw_item", "requested_url"),
            ("raw_item", "content_sha256"),
            ("evidence", "locator"),
        ]
        for record_type, field in cases:
            with self.subTest(record_type=record_type, field=field):
                invalid = copy.deepcopy(EXAMPLE)
                first(invalid, record_type).pop(field)
                with self.assertRaises(ValidationError):
                    VALIDATOR.validate(invalid)

    def test_contract_rejects_non_independent_verifier(self) -> None:
        invalid = copy.deepcopy(EXAMPLE)
        claim = first(invalid, "claim")
        claim["verifier_run_id"] = claim["producer_run_id"]
        with self.assertRaisesRegex(ValueError, "must differ"):
            assert_bundle_invariants(invalid)

    def test_result_semantics_reject_false_updates(self) -> None:
        cases = [
            {"result": "NO_NEW_ITEM", "item_count": 1, "window_completeness": "PARTIAL"},
            {"result": "NEW_ITEMS", "item_count": 0, "window_completeness": "COMPLETE_ZERO"},
            {"result": "PARTIAL", "item_count": 0, "window_completeness": "COMPLETE_ZERO"},
            {
                "result": "NOT_RUN",
                "item_count": None,
                "change_count": None,
                "source_health": "PASS",
                "window_completeness": "NOT_RUN",
            },
        ]
        for changes in cases:
            with self.subTest(result=changes["result"]):
                invalid = copy.deepcopy(EXAMPLE)
                first(invalid, "source_run").update(changes)
                with self.assertRaises(ValidationError):
                    VALIDATOR.validate(invalid)

    def test_partial_result_preserves_health_and_window_truth(self) -> None:
        valid = copy.deepcopy(EXAMPLE)
        first(valid, "source_run").update(
            {
                "result": "PARTIAL",
                "item_count": 0,
                "change_count": 0,
                "source_health": "PASS",
                "window_completeness": "PARTIAL",
            }
        )
        VALIDATOR.validate(valid)

    def test_complete_window_can_report_no_changes_with_observed_items(self) -> None:
        valid = copy.deepcopy(EXAMPLE)
        first(valid, "source_run").update(
            {
                "result": "NO_NEW_ITEM",
                "item_count": 3,
                "change_count": 0,
                "source_health": "PASS",
                "window_completeness": "COMPLETE_WITH_ITEMS",
            }
        )
        VALIDATOR.validate(valid)

    def test_migration_declares_core_constraints(self) -> None:
        sql = "\n".join(path.read_text(encoding="utf-8") for path in MIGRATIONS)
        for table in TABLES:
            self.assertRegex(sql, rf"(?i)CREATE\s+TABLE\s+{re.escape(table)}\s*\(")
        compact = " ".join(sql.split())
        self.assertIn("producer_run_id <> verifier_run_id", compact)
        self.assertIn("WHERE slot IN ('MORNING', 'EVENING')", compact)
        self.assertIn("result <> 'NO_NEW_ITEM'", compact)
        self.assertIn("result <> 'NEW_ITEMS'", compact)
        self.assertIn("result <> 'PARTIAL'", compact)
        self.assertIn("result <> 'NOT_RUN'", compact)
        self.assertIn("change_count", compact)
        self.assertIn("status = 'RESOLVED'", compact)

    def test_d2_fixture_slot_is_idempotent_and_exposes_health_gaps_and_lkg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state, first_replayed, run_id = run_slot(
                "morning",
                date(2026, 8, 22),
                state_path,
                now=datetime(2026, 8, 22, 7, 0, tzinfo=TZ),
            )
            first_bytes = state_path.read_bytes()
            replay_state, replayed, replay_id = run_slot(
                "morning",
                date(2026, 8, 22),
                state_path,
                now=datetime(2026, 8, 22, 7, 5, tzinfo=TZ),
            )

            self.assertFalse(first_replayed)
            self.assertTrue(replayed)
            self.assertEqual(replay_id, run_id)
            self.assertEqual(state_path.read_bytes(), first_bytes)
            self.assertEqual(replay_state, state)
            self.assertEqual(state["collection_runs"][run_id]["status"], "PARTIAL")
            source_runs = [
                record
                for record in state["source_runs"].values()
                if record["collection_run_id"] == run_id
            ]
            self.assertEqual(len(source_runs), len(P0_SOURCES))
            VALIDATOR.validate(
                {
                    "contract_version": "1.0",
                    "records": [state["collection_runs"][run_id], *source_runs],
                }
            )
            for source_id, status in state["source_status"].items():
                source_run = state["source_runs"][status["current_source_run_id"]]
                self.assertTrue(status["source_url"].startswith("https://"), source_id)
                self.assertRegex(status["data_as_of"], r"^2026-08-14T", source_id)
                self.assertRegex(source_run["manifest_sha256"], r"^[0-9a-f]{64}$", source_id)
                self.assertEqual(status["next_update_at"], "2026-08-22T18:30:00+08:00")

            s009 = state["source_status"]["S-009"]
            self.assertEqual((s009["current_source_health"], s009["result"]), ("PASS", "PARTIAL"))
            self.assertIn("WINDOW_PARTIAL", s009["intelligence_gaps"])
            self.assertIsNotNone(s009["last_known_good"])
            s029 = state["source_status"]["S-029"]
            self.assertEqual(s029["freshness_status"], "VERY_STALE")
            self.assertEqual(s029["last_known_good"]["snapshot_item_count"], 19)
            self.assertEqual(len(state["current_items"]), 2)

    def test_d2_failure_is_isolated_and_does_not_overwrite_lkg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            morning, _, morning_id = run_slot(
                "morning",
                date(2026, 8, 22),
                state_path,
                now=datetime(2026, 8, 22, 7, 0, tzinfo=TZ),
            )
            previous_lkg = copy.deepcopy(morning["last_known_good"]["S-006"])
            evening, replayed, evening_id = run_slot(
                "evening",
                date(2026, 8, 22),
                state_path,
                now=datetime(2026, 8, 22, 19, 0, tzinfo=TZ),
                broken_source="S-006",
            )

            self.assertFalse(replayed)
            self.assertNotEqual(evening_id, morning_id)
            self.assertEqual(evening["last_known_good"]["S-006"], previous_lkg)
            failed = evening["source_status"]["S-006"]
            self.assertEqual(failed["current_source_health"], "FAILED")
            self.assertEqual(failed["last_known_good"], previous_lkg)
            self.assertIn("SOURCE_FAILED", failed["intelligence_gaps"])
            failed_run = evening["source_runs"][failed["current_source_run_id"]]
            self.assertEqual(failed_run["previous_successful_source_run_id"], previous_lkg["source_run_id"])
            self.assertEqual(evening["source_status"]["S-004"]["result"], "NO_NEW_ITEM")
            self.assertEqual(len(evening["current_items"]), 2)

    def test_stable_keys_no_duplicates_and_normalized_manifests(self) -> None:
        """Two fixture runs yield no duplicate stable keys and identical manifests."""
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "state.json"
            state, _, run_id = run_slot(
                "morning",
                date(2026, 8, 22),
                state_path,
                now=datetime(2026, 8, 22, 7, 0, tzinfo=TZ),
            )
            # No duplicate stable keys in current_items
            keys = list(state["current_items"].keys())
            self.assertEqual(len(keys), len(set(keys)), "duplicate stable_key in current_items")

            # Every current item has version_no=1 (first observation of fixed fixture)
            for key, item in state["current_items"].items():
                self.assertEqual(item["version_no"], 1, f"{key} should be version 1")
                self.assertIn("content_sha256", item, f"{key} missing content_sha256")

            # Collect manifest hashes from source_runs
            manifests_run1 = {
                record["source_id"]: record["manifest_sha256"]
                for record in state["source_runs"].values()
                if record["collection_run_id"] == run_id and record["manifest_sha256"]
            }

            # Second (distinct evening) slot produces identical manifests
            state2, _, run_id2 = run_slot(
                "evening",
                date(2026, 8, 22),
                state_path,
                now=datetime(2026, 8, 22, 19, 0, tzinfo=TZ),
            )
            manifests_run2 = {
                record["source_id"]: record["manifest_sha256"]
                for record in state2["source_runs"].values()
                if record["collection_run_id"] == run_id2 and record["manifest_sha256"]
            }
            self.assertEqual(manifests_run1, manifests_run2, "manifests differ between runs")

            # Still no duplicate stable keys after second run
            keys2 = list(state2["current_items"].keys())
            self.assertEqual(len(keys2), len(set(keys2)), "duplicate after evening run")
            self.assertEqual(len(keys2), 2, "item count should remain 2")

    @unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
    def test_migration_applies_to_postgresql(self) -> None:
        psql = shutil.which("psql")
        if not psql:
            self.skipTest("psql not found")
        schema = f"kiro_d1_{uuid.uuid4().hex}"
        table_names = ", ".join(f"'{name}'" for name in sorted(TABLES))
        command = [
            psql,
            "-X",
            "-qAt",
            "-v",
            "ON_ERROR_STOP=1",
            "-d",
            os.environ["TEST_DATABASE_URL"],
            "-c",
            f'BEGIN; CREATE SCHEMA "{schema}"; SET LOCAL search_path TO "{schema}";',
        ]
        for migration in MIGRATIONS:
            command.extend(["-f", str(migration)])
        command.extend([
            "-c",
            (
                "SELECT count(*) FROM information_schema.tables "
                f"WHERE table_schema = '{schema}' AND table_name IN ({table_names});"
            ),
            "-c",
            "ROLLBACK;",
        ])
        env = {**os.environ, "PGCONNECT_TIMEOUT": "5"}
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(len(TABLES)), result.stdout.splitlines())


if __name__ == "__main__":
    unittest.main()
