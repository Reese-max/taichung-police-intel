from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts import migration_replay as mr


def bundle() -> dict:
    return {
        "schema_version": 1,
        "observed_at": "2026-09-21T00:00:00+00:00",
        "parser_version": "parser-v1",
        "semantics_version": "semantics-v1",
        "watch_state": {"schema_version": 1, "watch_items": {"WATCH-1": {"status": "WATCHING"}}},
        "handoff_state": {"schema_version": 1, "handoffs": [{"handoff_id": "H-1"}]},
        "objects": {
            "PublicEvent": [{"stable_id": "PUB-1", "source_id": "S-007", "raw_item_id": "RI-1", "content_sha256": "a" * 64}],
            "ChangeEvent": [{"stable_id": "PUB-1", "source_id": "S-007", "raw_item_id": "RI-1", "change_type": "NEW", "detected_at": "2026-09-21T00:00:00+00:00"}],
            "EvidenceEnvelope": [{"evidence_id": "EVD-1", "raw_item_id": "RI-1", "content_sha256": "b" * 64, "official_url": "https://official.test/1", "locator": "body"}],
        },
    }


class MigrationReplayTests(unittest.TestCase):
    def test_registry_migrates_all_three_objects_and_preserves_manual_state(self):
        migrated, report = mr.migrate_bundle(bundle())
        self.assertIsNotNone(migrated)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["affected_count"], 6)
        self.assertTrue(report["manual_state_hashes"]["preserved"])
        self.assertEqual(migrated["schema_version"], 3)
        for kind in mr.OBJECT_KEYS:
            self.assertEqual(migrated["objects"][kind][0]["schema_version"], 3)
            self.assertTrue(migrated["objects"][kind][0]["source_snapshot_ref"])

    def test_migration_is_idempotent_at_target(self):
        migrated, _ = mr.migrate_bundle(bundle())
        again, report = mr.migrate_bundle(migrated)
        self.assertEqual(again, migrated)
        self.assertTrue(report["idempotent_at_target"])

    def test_missing_raw_reference_reports_error_without_output(self):
        invalid = bundle()
        invalid["objects"]["PublicEvent"][0].pop("raw_item_id")
        migrated, report = mr.migrate_bundle(invalid)
        self.assertIsNone(migrated)
        self.assertEqual(report["error_count"], 1)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "existing.json"
            output.write_text("sentinel\n", encoding="utf-8")
            if migrated is not None:
                mr.atomic_write(output, migrated)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")

    def test_duplicate_migrated_identity_fails_closed(self):
        invalid = bundle()
        invalid["objects"]["PublicEvent"].append(dict(invalid["objects"]["PublicEvent"][0], raw_item_id="RI-2"))
        migrated, report = mr.migrate_bundle(invalid)
        self.assertIsNone(migrated)
        self.assertEqual(report["error_count"], 1)
        self.assertIn("duplicate PublicEvent identity", report["errors"][0]["error"])

    def test_dry_run_errors_return_nonzero_and_preserve_report(self):
        invalid = bundle()
        invalid["objects"]["PublicEvent"][0].pop("raw_item_id")
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "input.json"
            report_path = Path(directory) / "report.json"
            source.write_text(json.dumps(invalid), encoding="utf-8")
            self.assertEqual(mr.main(["dry-run", "--input", str(source), "--output", str(report_path)]), 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["error_count"], 1)

    def test_replay_is_deterministic_and_keeps_first_seen_semantics(self):
        feed = {
            "schema_version": 1,
            "collection_run_id": "CR-1",
            "generated_at": "2026-09-21T00:00:00+00:00",
            "items": [{
                "stable_id": "S-007:1",
                "source_id": "S-007",
                "title": "首次",
                "official_url": "https://official.test/1",
                "content_sha256": "c" * 64,
                "published_at": None,
            }],
        }
        previous = {"schema_version": 1, "mode": "V2_SHADOW", "baseline_established_at": feed["generated_at"], "items": {}}
        first = mr.replay_publication({"feed": feed, "previous_state": previous})
        second = mr.replay_publication({"feed": feed, "previous_state": previous})
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(first["replay_receipt"], second["replay_receipt"])
        self.assertEqual(first["events"][0]["temporal_basis"], "FIRST_SEEN")
        self.assertEqual(first["replay_receipt"]["first_seen_event_count"], 1)

    def test_replay_keeps_watch_and_handoff_hashes(self):
        feed = {
            "schema_version": 1,
            "collection_run_id": "CR-1",
            "generated_at": "2026-09-21T00:00:00+00:00",
            "items": [],
        }
        payload = {
            "feed": feed,
            "previous_state": {"schema_version": 1, "mode": "V2_SHADOW", "baseline_established_at": feed["generated_at"], "items": {}},
            "watch_state": {"watch_items": {"WATCH-1": {"status": "NEEDS_REVIEW"}}},
            "handoff_state": {"handoffs": [{"handoff_id": "H-1"}]},
        }
        result = mr.replay_publication(payload)
        self.assertEqual(result["watch_state"], payload["watch_state"])
        self.assertEqual(result["handoff_state"], payload["handoff_state"])
        self.assertEqual(result["replay_receipt"]["manual_state_hashes"]["watch_state"], mr.sha256(payload["watch_state"]))


if __name__ == "__main__":
    unittest.main()
