from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import migration_replay as mr

RAW_PAYLOAD = {"title": "台中捷運延誤", "body": "班距拉長", "official_url": "https://official.test/1"}
RAW_SNAPSHOT_REF = "raw_items/RI-1"


def stale_bundle(from_version: str = "raw-bytes-sha256-v1") -> dict:
    """A migrated bundle whose content hashes still carry the older semantics."""
    migrated, _ = mr.migrate_bundle(bundle())
    stale = json.loads(json.dumps(migrated))
    raw = json.dumps(RAW_PAYLOAD, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    stale["raw_snapshots"] = {RAW_SNAPSHOT_REF: {"payload": RAW_PAYLOAD, "raw_bytes_base64": base64.b64encode(raw).decode("ascii")}}
    for kind in ("PublicEvent", "EvidenceEnvelope"):
        stale["objects"][kind][0]["body_hash_version"] = from_version
        stale["objects"][kind][0]["content_sha256"] = "f" * 64
    return stale


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

    def test_missing_or_unknown_object_collection_fails_closed(self):
        missing = bundle()
        del missing["objects"]["EvidenceEnvelope"]
        migrated, report = mr.migrate_bundle(missing)
        self.assertIsNone(migrated)
        self.assertIn("missing object collection", {error["error"] for error in report["errors"]})

        unknown = bundle()
        unknown["objects"]["Unexpected"] = []
        migrated, report = mr.migrate_bundle(unknown)
        self.assertIsNone(migrated)
        self.assertIn("unknown object collection", {error["error"] for error in report["errors"]})

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
            "fusion_state": {"relations": [{"relation_id": "REL-1", "review_decision": "PENDING_REVIEW"}]},
        }
        result = mr.replay_publication(payload)
        self.assertEqual(result["watch_state"], payload["watch_state"])
        self.assertEqual(result["handoff_state"], payload["handoff_state"])
        self.assertEqual(result["fusion_state"], payload["fusion_state"])
        self.assertEqual(result["replay_receipt"]["manual_state_hashes"]["watch_state"], mr.sha256(payload["watch_state"]))
        self.assertEqual(result["replay_receipt"]["manual_state_hashes"]["fusion_state"], mr.sha256(payload["fusion_state"]))
        self.assertEqual(result["replay_receipt"]["manual_state_strategy"], "PRESERVE_SEPARATELY_NO_AUTOMATIC_MUTATION")


class MigrationRegistryExtensionTests(unittest.TestCase):
    """Schema bumps must be driven by the registry, not by a hard-coded ladder."""

    def test_every_registered_step_has_exactly_one_implementation(self):
        self.assertEqual(set(mr.MIGRATION_STEPS), set(mr.MIGRATION_REGISTRY.values()))
        self.assertEqual(len(mr.MIGRATION_REGISTRY), len(set(mr.MIGRATION_REGISTRY.values())))

    def test_dry_run_report_records_the_registry_path_that_actually_ran(self):
        _, report = mr.migrate_bundle(bundle())
        self.assertEqual(report["migration_path"], [f"{a}->{b}:{name}" for (a, b), name in sorted(mr.MIGRATION_REGISTRY.items())])
        self.assertEqual(report["migration_path"], [
            "1->2:bind_source_snapshot_and_timestamps",
            "2->3:bind_derivation_versions",
        ])
        # A legacy object does not declare which hash semantics produced it, so the report
        # records that rather than the canonical semantics nobody proved.
        self.assertEqual(report["version_binding"]["body_hash_version"], mr.LEGACY_BODY_HASH_VERSION)

    def test_registered_step_without_implementation_fails_closed(self):
        migrated, _ = mr.migrate_bundle(bundle())
        with mock.patch.dict(mr.MIGRATION_REGISTRY, {(3, 4): "future_step_not_implemented"}):
            result, report = mr.migrate_bundle(migrated, target_version=4)
        self.assertIsNone(result)
        self.assertEqual(len(report["errors"]), len(mr.OBJECT_KEYS))
        self.assertTrue(all("not implemented" in error["error"] for error in report["errors"]))

    def test_migrated_content_hashed_objects_declare_body_hash_version(self):
        migrated, report = mr.migrate_bundle(bundle())
        self.assertIsNotNone(migrated)
        self.assertEqual(report["error_count"], 0)
        for kind in ("PublicEvent", "EvidenceEnvelope"):
            self.assertEqual(migrated["objects"][kind][0]["body_hash_version"], mr.LEGACY_BODY_HASH_VERSION)
        self.assertEqual(migrated["body_hash_version"], mr.LEGACY_BODY_HASH_VERSION)

    def test_bundle_declaring_an_unregistered_body_hash_version_is_refused(self):
        invalid = bundle()
        invalid["body_hash_version"] = "totally-made-up-v9"
        migrated, report = mr.migrate_bundle(invalid)
        self.assertIsNone(migrated)
        self.assertEqual(report["error_count"], 1)
        self.assertIn("not registered", report["errors"][0]["error"])

    def test_target_object_without_body_hash_declaration_fails_closed(self):
        migrated, _ = mr.migrate_bundle(bundle())
        undeclared = json.loads(json.dumps(migrated))
        for kind in ("PublicEvent", "EvidenceEnvelope"):
            undeclared["objects"][kind][0].pop("body_hash_version")
        result, report = mr.migrate_bundle(undeclared)
        self.assertIsNone(result)
        self.assertEqual(len(report["errors"]), 2)
        self.assertTrue(all("body_hash_version" in error["error"] for error in report["errors"]))


class BodyHashSemanticsMigrationTests(unittest.TestCase):
    """Regression: a body-hash semantics bump must re-derive from retained evidence."""

    def test_body_hash_upgrade_rehashes_from_retained_snapshot_deterministically(self):
        stale = stale_bundle()
        before = json.loads(json.dumps(stale))
        first, report = mr.rehash_bundle(stale)
        second, _ = mr.rehash_bundle(stale)
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(stale, before)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["target_body_hash_version"], mr.BODY_HASH_VERSION)
        self.assertEqual(report["from_body_hash_versions"], ["raw-bytes-sha256-v1"])
        self.assertEqual(first["body_hash_version"], mr.BODY_HASH_VERSION)
        public_event = first["objects"]["PublicEvent"][0]
        self.assertEqual(public_event["body_hash_version"], mr.BODY_HASH_VERSION)
        self.assertEqual(public_event["content_sha256"], mr.sha256(RAW_PAYLOAD))
        self.assertNotEqual(public_event["stable_id"], before["objects"]["PublicEvent"][0]["stable_id"])
        self.assertEqual(len(report["rehashed"]), 2)
        self.assertEqual({row["kind"] for row in report["rehashed"]}, {"PublicEvent", "EvidenceEnvelope"})
        self.assertEqual(first["body_hash_migration"][0]["to_body_hash_version"], mr.BODY_HASH_VERSION)
        self.assertEqual(first["body_hash_migration"][0]["input_sha256"], mr.sha256(stale))

    def test_body_hash_upgrade_can_derive_raw_bytes_semantics(self):
        stale = stale_bundle(from_version=mr.BODY_HASH_VERSION)
        raw = json.dumps(RAW_PAYLOAD, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        result, report = mr.rehash_bundle(stale, target_body_hash_version="raw-bytes-sha256-v1")
        self.assertIsNotNone(result)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(result["objects"]["PublicEvent"][0]["content_sha256"], mr.sha256(raw))
        self.assertEqual(result["objects"]["PublicEvent"][0]["body_hash_version"], "raw-bytes-sha256-v1")

    def test_body_hash_upgrade_fails_closed_when_raw_snapshot_not_retained(self):
        stale = stale_bundle()
        stale["raw_snapshots"] = {}
        before = json.loads(json.dumps(stale))
        result, report = mr.rehash_bundle(stale)
        self.assertIsNone(result)
        self.assertEqual(stale, before)
        missing = [error for error in report["errors"] if error.get("reason") == mr.MISSING_EVIDENCE_REASON]
        self.assertEqual(len(missing), 2)
        self.assertEqual({error["kind"] for error in missing}, {"PublicEvent", "EvidenceEnvelope"})
        self.assertEqual(report["undetermined_body_hash_version"], mr.UNDETERMINED_BODY_HASH_VERSION)

    def test_rehash_cascades_the_new_identity_into_referencing_change_events(self):
        stale = stale_bundle()
        result, report = mr.rehash_bundle(stale)
        self.assertIsNotNone(result)
        public_ids = {row["stable_id"] for row in result["objects"]["PublicEvent"]}
        self.assertEqual(report["identity_cascade_count"], 1)
        for change_event in result["objects"]["ChangeEvent"]:
            self.assertIn(change_event["stable_id"], public_ids)

    def test_rehash_refuses_when_a_change_event_would_be_left_dangling(self):
        stale = stale_bundle()
        stale["objects"]["PublicEvent"] = []
        result, report = mr.rehash_bundle(stale)
        self.assertIsNone(result)
        self.assertTrue(any("does not resolve to a migrated PublicEvent" in error["error"] for error in report["errors"]))

    def test_rehash_validates_the_collections_it_does_not_rehash(self):
        stale = stale_bundle()
        stale["objects"]["ChangeEvent"] = [{"not": "a change event at all", "schema_version": 99}]
        result, report = mr.rehash_bundle(stale)
        self.assertIsNone(result)
        self.assertTrue(any(error["kind"] == "ChangeEvent" for error in report["errors"]))

    def test_rehash_is_a_no_op_when_every_object_already_declares_the_target(self):
        migrated, _ = mr.migrate_bundle(bundle())
        migrated["raw_snapshots"] = {RAW_SNAPSHOT_REF: {"payload": RAW_PAYLOAD}}
        result, report = mr.rehash_bundle(migrated, target_body_hash_version=mr.LEGACY_BODY_HASH_VERSION)
        self.assertEqual(result, migrated)
        self.assertEqual(report["error_count"], 0)
        self.assertEqual(report["affected_count"], 0)
        self.assertTrue(report["idempotent_at_target"])
        self.assertEqual(report["output_sha256"], report["input_sha256"])
        self.assertIsNone(report["undetermined_body_hash_version"])

    def test_rehash_never_reports_an_undetermined_hash_when_it_succeeded(self):
        stale = stale_bundle()
        _, report = mr.rehash_bundle(stale)
        self.assertEqual(report["error_count"], 0)
        self.assertIsNone(report["undetermined_body_hash_version"])

    def test_body_hash_upgrade_rejects_unregistered_semantics_version(self):
        stale = stale_bundle()
        result, report = mr.rehash_bundle(stale, target_body_hash_version="md5-of-nothing-v0")
        self.assertIsNone(result)
        self.assertIn("md5-of-nothing-v0", report["errors"][0]["error"])

    def test_body_hash_upgrade_refuses_unregistered_target_with_nothing_to_rehash(self):
        migrated, _ = mr.migrate_bundle(bundle())
        for kind in mr.OBJECT_KEYS:
            migrated["objects"][kind] = []
        result, report = mr.rehash_bundle(migrated, target_body_hash_version="md5-of-nothing-v0")
        self.assertIsNone(result)
        self.assertEqual(report["error_count"], 1)
        self.assertIn("md5-of-nothing-v0", report["errors"][0]["error"])


class PublicationVersionExplanationTests(unittest.TestCase):
    """Acceptance: an old publication receipt must still explain its versions."""

    def test_legacy_publication_receipt_explains_only_what_it_actually_carries(self):
        legacy = {
            "schema_version": 1,
            "receipt_type": "PUBLICATION_PROJECTION",
            "publication_id": "PUB-9",
            "generation_id": "gen-9",
        }
        explained = mr.explain_publication_receipt(legacy)
        self.assertEqual(explained["receipt_type"], "PUBLICATION_VERSION_EXPLANATION")
        self.assertEqual(explained["versions"]["schema_version"], {"value": 1, "source": "declared"})
        # Every other axis is reported as unresolved: the receipt never carried them, and a
        # sibling function's fallback argument is not evidence about this artifact.
        for axis in ("body_hash_version", "parser_version", "semantics_version", "projection_version", "derivation_version", "model_version"):
            self.assertEqual(explained["versions"][axis], {"value": mr.UNRESOLVED, "source": "unresolvable"}, axis)
        self.assertEqual(explained["unresolved"], [
            "body_hash_version",
            "derivation_version",
            "model_version",
            "parser_version",
            "projection_version",
            "semantics_version",
        ])
        self.assertEqual(explained["source_receipt_sha256"], mr.sha256(legacy))
        self.assertEqual(explained["replay_tool_versions"]["semantics_version"], "intel_v2.semantics-v1")

    def test_never_migrated_artifact_reports_no_migration_path(self):
        explained = mr.explain_publication_receipt({"receipt_type": "PUBLICATION_PROJECTION", "schema_version": 1, "migration_history": []})
        self.assertEqual(explained["migration_path"], [])
        self.assertEqual(explained["migration_path_source"], "absent_never_migrated")

    def test_migrated_bundle_receipt_reports_declared_versions_and_path(self):
        migrated, _ = mr.migrate_bundle(bundle())
        explained = mr.explain_publication_receipt(migrated)
        self.assertEqual(explained["unresolved"], ["model_version", "projection_version"])
        self.assertEqual(explained["versions"]["parser_version"], {"value": "parser-v1", "source": "declared"})
        self.assertEqual(explained["versions"]["body_hash_version"], {"value": mr.LEGACY_BODY_HASH_VERSION, "source": "declared"})
        self.assertEqual(explained["versions"]["schema_version"], {"value": 3, "source": "declared"})
        self.assertEqual(
            explained["migration_path"],
            ["1->2:bind_source_snapshot_and_timestamps", "2->3:bind_derivation_versions"],
        )
        self.assertEqual(explained["migration_path_source"], "artifact_migration_history")

    def test_explanation_refuses_to_choose_between_divergent_object_declarations(self):
        migrated, _ = mr.migrate_bundle(bundle())
        migrated.pop("body_hash_version")
        migrated["objects"]["PublicEvent"][0]["body_hash_version"] = "raw-bytes-sha256-v1"
        explained = mr.explain_publication_receipt(migrated)
        self.assertEqual(explained["versions"]["body_hash_version"], {"value": mr.UNRESOLVED, "source": "conflicting_object_declarations"})
        self.assertIn("body_hash_version", explained["unresolved"])
        self.assertEqual(explained["versions"]["parser_version"], {"value": "parser-v1", "source": "declared"})

    def test_explanation_refuses_a_declared_value_the_preserved_objects_contradict(self):
        migrated, _ = mr.migrate_bundle(bundle())
        # Every object agrees on one semantics; the bundle-level declaration disagrees
        # with all of them. The top level must not win.
        for kind in ("PublicEvent", "EvidenceEnvelope"):
            migrated["objects"][kind][0]["body_hash_version"] = "raw-bytes-sha256-v1"
        self.assertEqual(migrated["body_hash_version"], mr.LEGACY_BODY_HASH_VERSION)
        explained = mr.explain_publication_receipt(migrated)
        self.assertEqual(explained["versions"]["body_hash_version"], {"value": mr.UNRESOLVED, "source": "conflicting_object_declarations"})
        self.assertIn("body_hash_version", explained["unresolved"])

    def test_explanation_refuses_a_declared_value_that_only_part_of_the_objects_agree_with(self):
        migrated, _ = mr.migrate_bundle(bundle())
        # The bundle level and one object say raw-bytes while another object still says the
        # legacy semantics: a matching top-level value does not make the split disappear.
        migrated["objects"]["PublicEvent"][0]["body_hash_version"] = "raw-bytes-sha256-v1"
        migrated["body_hash_version"] = "raw-bytes-sha256-v1"
        explained = mr.explain_publication_receipt(migrated)
        self.assertEqual(explained["versions"]["body_hash_version"], {"value": mr.UNRESOLVED, "source": "conflicting_object_declarations"})
        self.assertIn("body_hash_version", explained["unresolved"])

    def test_explanation_refuses_unusable_declared_values(self):
        explained = mr.explain_publication_receipt({
            "receipt_type": "PUBLICATION_PROJECTION",
            "schema_version": "banana",
            "body_hash_version": "not-a-registered-semantics",
            "parser_version": "",
        })
        self.assertEqual(explained["versions"]["schema_version"], {"value": mr.UNRESOLVED, "source": "unusable_declared_value"})
        self.assertEqual(explained["versions"]["body_hash_version"], {"value": mr.UNRESOLVED, "source": "unusable_declared_value"})
        self.assertEqual(explained["versions"]["parser_version"], {"value": mr.UNRESOLVED, "source": "unusable_declared_value"})
        self.assertEqual(
            explained["unresolved"],
            ["body_hash_version", "derivation_version", "model_version", "parser_version", "projection_version", "schema_version", "semantics_version"],
        )

    def test_explanation_falls_back_to_version_binding_when_the_top_level_key_is_null(self):
        explained = mr.explain_publication_receipt({
            "receipt_type": "PUBLICATION_PROJECTION",
            "schema_version": 1,
            "parser_version": None,
            "version_binding": {"parser_version": "parser-v1"},
        })
        self.assertEqual(explained["versions"]["parser_version"], {"value": "parser-v1", "source": "declared"})

    def test_explanation_survives_unserialisable_object_declarations(self):
        explained = mr.explain_publication_receipt({
            "receipt_type": "PUBLICATION_PROJECTION",
            "schema_version": 1,
            "objects": {"PublicEvent": [{"schema_version": float("nan")}]},
        })
        self.assertEqual(explained["versions"]["schema_version"]["source"], "declared")
        self.assertEqual(explained["receipt_type"], "PUBLICATION_VERSION_EXPLANATION")

    def test_explanation_never_invents_a_value_for_an_unresolvable_axis(self):
        explained = mr.explain_publication_receipt({"receipt_type": "PUBLICATION_PROJECTION"})
        for axis, entry in explained["versions"].items():
            self.assertIn("source", entry)
            self.assertNotEqual(entry["source"], "declared")
            self.assertNotEqual(entry["source"], "object_declarations")
            self.assertEqual(entry["value"], mr.UNRESOLVED, axis)
        self.assertEqual(
            explained["unresolved"],
            ["body_hash_version", "derivation_version", "model_version", "parser_version", "projection_version", "schema_version", "semantics_version"],
        )


class QueryStoreRebuildTests(unittest.TestCase):
    """Acceptance: the Query Store rebuilds completely from a named canonical generation."""

    def canonical_paths(self) -> tuple[Path, Path, Path]:
        query_store = mr.load_query_store_module()
        return query_store.DEFAULT_FEED, query_store.DEFAULT_STATUS, query_store.DEFAULT_BRIEF

    def test_query_store_rebuild_is_deterministic_and_generation_bound(self):
        feed, status, brief = self.canonical_paths()
        first, receipt = mr.rebuild_query_store(feed, status, brief)
        second, _ = mr.rebuild_query_store(feed, status, brief)
        self.assertEqual(first, second)
        self.assertEqual(receipt["generation_id"], first["generation_id"])
        self.assertEqual(receipt["store_schema_version"], first["schema_version"])
        self.assertEqual(receipt["projection_version"], first["projection_version"])
        self.assertEqual(receipt["artifact_hashes"], {
            "feed": first["generated_from"]["feed_sha256"],
            "status": first["generated_from"]["status_sha256"],
            "brief": first["generated_from"]["brief_sha256"],
        })
        self.assertEqual(receipt["version_explanation"]["receipt_type"], "PUBLICATION_VERSION_EXPLANATION")
        # The explanation describes the same bytes the receipt hashes.
        self.assertEqual(receipt["version_explanation"]["source_document_sha256"], receipt["artifact_hashes"]["feed"])
        self.assertIsNone(receipt["version_explanation"]["source_receipt_hash_error"])
        self.assertEqual(receipt["store_projection_sha256"], first["projection_sha256"])

    def test_query_store_rebuild_reads_each_canonical_artifact_once(self):
        feed, status, brief = self.canonical_paths()
        query_store = mr.load_query_store_module()
        real_load = query_store.load_json
        reads: list[str] = []

        def counting_load(path):
            reads.append(Path(path).name)
            return real_load(path)

        query_store.load_json = counting_load
        with mock.patch.object(mr, "load_query_store_module", return_value=query_store):
            mr.rebuild_query_store(feed, status, brief)
        self.assertEqual(reads, [feed.name, status.name, brief.name])

    def test_query_store_rebuild_accepts_the_specified_generation(self):
        feed, status, brief = self.canonical_paths()
        store, _ = mr.rebuild_query_store(feed, status, brief)
        _, receipt = mr.rebuild_query_store(feed, status, brief, expected_generation=store["generation_id"])
        self.assertEqual(receipt["expected_generation_verified"], True)

    def test_query_store_rebuild_refuses_unexpected_generation_and_keeps_previous_store(self):
        feed, status, brief = self.canonical_paths()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "query-store.json"
            output.write_text("sentinel\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                mr.rebuild_query_store_to_path(feed, status, brief, output=output, expected_generation="0" * 64)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")
            mr.rebuild_query_store_to_path(feed, status, brief, output=output)
            rebuilt = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(rebuilt["generation_id"], mr.rebuild_query_store(feed, status, brief)[0]["generation_id"])
            receipt = json.loads((Path(directory) / "query-store.migration-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["generation_id"], rebuilt["generation_id"])

    def test_query_store_rebuild_refuses_cross_generation_artifacts(self):
        feed, status, brief = self.canonical_paths()
        with tempfile.TemporaryDirectory() as directory:
            stale_brief = Path(directory) / "brief.json"
            payload = json.loads(brief.read_text(encoding="utf-8"))
            payload["source_collection_run_id"] = "CR-OTHER"
            stale_brief.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError) as caught:
                mr.rebuild_query_store(feed, status, stale_brief)
            self.assertIn("cross-generation", str(caught.exception))

    def test_query_store_rebuild_receipt_may_not_be_written_into_published_data(self):
        feed, status, brief = self.canonical_paths()
        published_receipt = mr.PUBLISHED_DATA_DIR / "data" / "query-store.migration-receipt.json"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "query-store.json"
            with self.assertRaises(ValueError) as caught:
                mr.rebuild_query_store_to_path(feed, status, brief, output=output, receipt_output=published_receipt)
            self.assertIn("must not be written inside", str(caught.exception))
            self.assertFalse(published_receipt.exists())
            self.assertFalse(output.exists())
            explicit = Path(directory) / "receipt.json"
            mr.rebuild_query_store_to_path(feed, status, brief, output=output, receipt_output=explicit)
            self.assertTrue(explicit.is_file())
            self.assertTrue(output.is_file())

    def test_rebuild_writes_the_store_before_the_receipt_so_a_crash_cannot_invent_a_generation(self):
        feed, status, brief = self.canonical_paths()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "query-store.json"
            real_atomic_write = mr.atomic_write

            def crash_on_receipt(path, value):
                if Path(path).name.endswith(".migration-receipt.json"):
                    raise OSError("simulated crash before the receipt landed")
                return real_atomic_write(path, value)

            with mock.patch.object(mr, "atomic_write", side_effect=crash_on_receipt):
                with self.assertRaises(OSError):
                    mr.rebuild_query_store_to_path(feed, status, brief, output=output)
            # The store is complete and self-verifying; only the derived receipt is missing,
            # so no receipt claims a generation that is not on disk.
            store = json.loads(output.read_text(encoding="utf-8"))
            self.assertIsNone(mr.load_query_store_module().validate_store(store))
            self.assertEqual(store["generation_id"], mr.rebuild_query_store(feed, status, brief)[0]["generation_id"])
            self.assertFalse((root / "query-store.migration-receipt.json").exists())

    def test_rehash_refuses_a_bundle_whose_collections_are_not_sound(self):
        stale = stale_bundle()
        not_an_array = json.loads(json.dumps(stale))
        not_an_array["objects"]["EvidenceEnvelope"] = {"evidence_id": "EVD-1"}
        result, report = mr.rehash_bundle(not_an_array)
        self.assertIsNone(result)
        self.assertTrue(any("objects value must be an array" in error["error"] for error in report["errors"]))

    def test_rehash_refuses_a_bundle_missing_an_object_collection(self):
        stale = stale_bundle()
        stale["objects"].pop("PublicEvent")
        stale["objects"].pop("ChangeEvent")
        result, report = mr.rehash_bundle(stale)
        self.assertIsNone(result)
        missing = [error for error in report["errors"] if error["error"] == "missing object collection"]
        self.assertEqual(len(missing), 2)

    def test_rehash_never_claims_success_on_an_unsound_bundle_via_the_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "rehashed.json"
            invalid = stale_bundle()
            invalid["objects"]["EvidenceEnvelope"] = {"evidence_id": "EVD-1"}
            source.write_text(json.dumps(invalid), encoding="utf-8")
            output.write_text("sentinel\n", encoding="utf-8")
            self.assertEqual(mr.main(["rehash", "--input", str(source), "--output", str(output)]), 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")
            report = json.loads((root / "rehashed.body-hash-report.json").read_text(encoding="utf-8"))
            self.assertTrue(any("objects value must be an array" in error["error"] for error in report["errors"]))

    def test_rehash_reconciles_a_bundle_level_declaration_the_objects_contradict(self):
        migrated, _ = mr.migrate_bundle(bundle())
        migrated["raw_snapshots"] = {RAW_SNAPSHOT_REF: {"payload": RAW_PAYLOAD}}
        for kind in ("PublicEvent", "EvidenceEnvelope"):
            migrated["objects"][kind][0]["body_hash_version"] = mr.BODY_HASH_VERSION
        migrated["body_hash_version"] = "raw-bytes-sha256-v1"
        result, report = mr.rehash_bundle(migrated)
        self.assertIsNotNone(result)
        self.assertEqual(report["affected_count"], 0)
        self.assertEqual(result["body_hash_version"], mr.BODY_HASH_VERSION)
        self.assertEqual(
            mr.explain_publication_receipt(result)["versions"]["body_hash_version"],
            {"value": mr.BODY_HASH_VERSION, "source": "declared"},
        )

    def test_rehash_refuses_when_re_derived_identities_would_collide(self):
        migrated, _ = mr.migrate_bundle(bundle())
        stale = json.loads(json.dumps(migrated))
        stale["raw_snapshots"] = {RAW_SNAPSHOT_REF: {"payload": RAW_PAYLOAD}}
        # Two PublicEvents that hashed differently collapse onto one identity once both are
        # re-derived from the same retained payload.
        twin = json.loads(json.dumps(migrated["objects"]["PublicEvent"][0]))
        stale["objects"]["PublicEvent"].append(twin)
        for row in stale["objects"]["PublicEvent"]:
            row["body_hash_version"] = "raw-bytes-sha256-v1"
            row["content_sha256"] = "f" * 64
        result, report = mr.rehash_bundle(stale)
        self.assertIsNone(result)
        self.assertTrue(any("duplicate PublicEvent identity" in error["error"] for error in report["errors"]))

    def test_duplicate_identity_error_points_at_the_input_row_not_the_post_drop_position(self):
        migrated, _ = mr.migrate_bundle(bundle())
        stale = json.loads(json.dumps(migrated))
        stale["raw_snapshots"] = {RAW_SNAPSHOT_REF: {"payload": RAW_PAYLOAD}}
        duplicate = json.loads(json.dumps(migrated["objects"]["PublicEvent"][0]))
        # Two unusable rows are dropped first, so output positions 0 and 1 are really the
        # input rows 2 and 3; the audit trail must still name the input position.
        stale["objects"]["PublicEvent"] = [
            {"stable_id": "BAD-1", "source_id": "S", "raw_item_id": "R", "content_sha256": "nope"},
            {"stable_id": "BAD-2", "source_id": "S", "raw_item_id": "R", "content_sha256": "nope"},
            stale["objects"]["PublicEvent"][0],
            duplicate,
        ]
        for row in stale["objects"]["PublicEvent"][2:]:
            row["body_hash_version"] = "raw-bytes-sha256-v1"
            row["content_sha256"] = "f" * 64
        result, report = mr.rehash_bundle(stale)
        self.assertIsNone(result)
        duplicates = [error for error in report["errors"] if "duplicate PublicEvent identity" in error["error"]]
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(duplicates[0]["index"], 3)


class ManualDecisionReapplyPlanTests(unittest.TestCase):
    """Acceptance: replay must preserve human merge/split/watch/handoff decisions explicitly."""

    def payload(self) -> dict:
        feed = {
            "schema_version": 1,
            "collection_run_id": "CR-1",
            "generated_at": "2026-09-21T00:00:00+00:00",
            "items": [],
        }
        # The shapes below mirror the ones this repository's owners actually write:
        # intel_v2/handoff.py watch_items+handoffs, intel_v2/review.py items, and a
        # public-event-fusion.py event's manual_history.
        return {
            "feed": feed,
            "previous_state": {"schema_version": 1, "mode": "V2_SHADOW", "baseline_established_at": feed["generated_at"], "items": {}},
            "watch_state": {"schema_version": 1, "watch_items": {"W-1": {"status": "NEEDS_REVIEW"}, "W-2": {"status": "WATCHING"}}},
            "handoff_state": {"schema_version": 1, "watch_items": {"W-1": {}}, "handoffs": [{"handoff_id": "H-1"}, {"handoff_id": "H-2"}, {"handoff_id": "H-3"}]},
            "fusion_state": {"manual_history": [{"action": "MERGE"}, {"action": "SPLIT"}, {"action": "CONFIRM"}]},
            "review_state": {"schema_version": 1, "items": {"REVIEW-1": {"state": "OPEN"}}},
        }

    def test_replay_reports_preserved_manual_decisions_and_reapply_plan(self):
        payload = self.payload()
        result = mr.replay_publication(payload)
        plan = {entry["state_key"]: entry for entry in result["replay_receipt"]["manual_state_reapply_plan"]}
        self.assertEqual(set(plan), {"watch_state", "handoff_state", "fusion_state", "review_state"})
        for key, entry in plan.items():
            self.assertEqual(entry["strategy"], "PRESERVE_SEPARATELY_NO_AUTOMATIC_MUTATION")
            self.assertEqual(entry["sha256"], mr.sha256(payload[key]))
            self.assertTrue(entry["shape_recognised"], key)
            self.assertTrue(entry["requires_human_confirmation"], key)
            self.assertIsNotNone(entry["reapply_via"], key)
        self.assertEqual(plan["fusion_state"]["decision_count"], 3)
        self.assertEqual(plan["watch_state"]["decision_count"], 2)
        self.assertEqual(plan["handoff_state"]["decision_count"], 4)
        self.assertEqual(plan["review_state"]["decision_count"], 1)
        self.assertEqual(plan["fusion_state"]["reapply_via"], "scripts/public-event-fusion.py")
        self.assertEqual(plan["watch_state"]["reapply_via"], "scripts/handoff-state.py watch")
        self.assertEqual(plan["handoff_state"]["reapply_via"], "scripts/handoff-state.py confirm")
        self.assertEqual(plan["review_state"]["reapply_via"], "scripts/review-inbox.py decide")
        for key in plan:
            self.assertEqual(result[key], payload[key])

    def test_every_named_reapply_writer_actually_exposes_that_command(self):
        for state_key, command in mr.MANUAL_STATE_REAPPLY.items():
            if command is None:
                self.assertNotIn(state_key, mr.MANUAL_STATE_DECISION_FIELDS)
                continue
            parts = command.split(" ")
            script = parts[0]
            source = (mr.ROOT / script).read_text(encoding="utf-8")
            if len(parts) == 1:
                # No subcommand to match, so assert the writer really produces the fields
                # whose decisions the plan counted.
                for field in mr.MANUAL_STATE_DECISION_FIELDS.get(state_key, ()):
                    self.assertIn(field, source, f"{script} never writes `{field}` for {state_key}")
                continue
            self.assertIn(f'add_parser("{parts[1]}")', source, f"{script} has no `{parts[1]}` command for {state_key}")

    def test_manual_state_without_a_recognised_shape_is_not_counted_as_a_decision(self):
        count, recognised = mr.count_manual_decisions("manual_state", {"anything": True})
        self.assertEqual(count, 0)
        self.assertFalse(recognised)
        count, recognised = mr.count_manual_decisions("fusion_state", {"relations": [{"relation_id": "REL-1"}]})
        self.assertEqual(count, 0)
        self.assertFalse(recognised)
        count, recognised = mr.count_manual_decisions("review_state", {})
        self.assertEqual(count, 0)
        self.assertFalse(recognised)

    def test_replay_reports_absent_manual_state_as_no_decision_to_reapply(self):
        feed = {
            "schema_version": 1,
            "collection_run_id": "CR-1",
            "generated_at": "2026-09-21T00:00:00+00:00",
            "items": [],
        }
        result = mr.replay_publication({"feed": feed, "previous_state": {"schema_version": 1, "items": {}}})
        self.assertEqual(result["replay_receipt"]["manual_state_reapply_plan"], [])
        self.assertIsNone(result["replay_receipt"]["manual_state_hashes"]["watch_state"])


class CrashSafeApplyTests(unittest.TestCase):
    """Regression: a crash mid-migration must not corrupt the previous data or lose the audit trail."""

    def test_apply_writes_receipt_with_the_data_in_one_atomic_write(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "out.json"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            self.assertEqual(mr.main(["apply", "--input", str(source), "--output", str(output)]), 0)
            applied = json.loads(output.read_text(encoding="utf-8"))
            sidecar = json.loads((root / "out.migration-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(applied["apply_receipt"], sidecar)
            self.assertEqual(applied["apply_receipt"]["report_type"], "MIGRATION_DRY_RUN")
            self.assertEqual(applied["apply_receipt"]["error_count"], 0)
            self.assertEqual(applied["schema_version"], 3)
            # The receipt has to describe the data it travelled with, not merely match itself.
            without_receipt = {key: value for key, value in applied.items() if key != "apply_receipt"}
            self.assertEqual(mr.sha256(without_receipt), applied["apply_receipt"]["output_sha256"])

    def test_applying_twice_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            first_output = root / "first.json"
            second_output = root / "second.json"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            self.assertEqual(mr.main(["apply", "--input", str(source), "--output", str(first_output)]), 0)
            # The second run consumes the first run's output, receipt included.
            self.assertEqual(mr.main(["apply", "--input", str(first_output), "--output", str(second_output)]), 0)
            first = json.loads(first_output.read_text(encoding="utf-8"))
            second = json.loads(second_output.read_text(encoding="utf-8"))
            strip = lambda value: {key: item for key, item in value.items() if key != "apply_receipt"}
            self.assertEqual(strip(first), strip(second))
            self.assertEqual(first["apply_receipt"]["output_sha256"], second["apply_receipt"]["output_sha256"])
            self.assertEqual(mr.sha256(strip(second)), second["apply_receipt"]["output_sha256"])

    def test_rehashing_twice_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            migrated_path = root / "migrated.json"
            first_output = root / "first.json"
            second_output = root / "second.json"
            payload = bundle()
            payload["raw_snapshots"] = {RAW_SNAPSHOT_REF: {"payload": RAW_PAYLOAD}}
            for kind in ("PublicEvent", "EvidenceEnvelope"):
                payload["objects"][kind][0]["body_hash_version"] = "raw-bytes-sha256-v1"
            source.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(mr.main(["apply", "--input", str(source), "--output", str(migrated_path)]), 0)
            self.assertEqual(mr.main(["rehash", "--input", str(migrated_path), "--output", str(first_output)]), 0)
            self.assertEqual(mr.main(["rehash", "--input", str(first_output), "--output", str(second_output)]), 0)
            first = json.loads(first_output.read_text(encoding="utf-8"))
            second = json.loads(second_output.read_text(encoding="utf-8"))
            strip = lambda value: {key: item for key, item in value.items() if key != "body_hash_migration_receipt"}
            self.assertEqual(strip(first), strip(second))
            self.assertEqual(first["body_hash_migration_receipt"]["output_sha256"], second["body_hash_migration_receipt"]["output_sha256"])
            self.assertEqual(mr.sha256(strip(second)), second["body_hash_migration_receipt"]["output_sha256"])

    def test_interrupted_apply_leaves_previous_output_and_claims_no_success(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "out.json"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            output.write_text("sentinel\n", encoding="utf-8")
            with mock.patch.object(mr.os, "replace", side_effect=OSError("simulated crash")):
                with self.assertRaises(OSError):
                    mr.main(["apply", "--input", str(source), "--output", str(output)])
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")
            self.assertFalse((root / "out.migration-receipt.json").exists())
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["input.json", "out.json"])

    def test_crash_after_data_write_still_leaves_an_explainable_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "out.json"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            output.write_text("sentinel\n", encoding="utf-8")
            real_atomic_write = mr.atomic_write

            def crash_on_sidecar(path, value):
                if Path(path).name.endswith(".migration-receipt.json"):
                    raise OSError("simulated crash before the audit receipt landed")
                return real_atomic_write(path, value)

            with mock.patch.object(mr, "atomic_write", side_effect=crash_on_sidecar):
                with self.assertRaises(OSError):
                    mr.main(["apply", "--input", str(source), "--output", str(output)])
            applied = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(applied["apply_receipt"]["report_type"], "MIGRATION_DRY_RUN")
            self.assertEqual(applied["apply_receipt"]["error_count"], 0)
            self.assertFalse((root / "out.migration-receipt.json").exists())

    def test_explanation_holds_object_declarations_to_the_same_usable_bar(self):
        for objects, axis in (
            ({"PublicEvent": [{"schema_version": "banana"}]}, "schema_version"),
            ({"PublicEvent": [{"body_hash_version": "totally-made-up-v9"}]}, "body_hash_version"),
            ({"PublicEvent": [{"model_version": {"name": "gpt", "temperature": 0.2}}]}, "model_version"),
        ):
            explained = mr.explain_publication_receipt({"receipt_type": "PUBLICATION_PROJECTION", "objects": objects})
            self.assertEqual(explained["versions"][axis], {"value": mr.UNRESOLVED, "source": "unusable_declared_value"}, axis)
            self.assertIn(axis, explained["unresolved"])

    def test_explanation_survives_a_declared_value_that_cannot_be_encoded(self):
        explained = mr.explain_publication_receipt(json.loads('{"receipt_type":"X","parser_version":"\\ud800","objects":{"PublicEvent":[{"parser_version":"v1"}]}}'))
        self.assertEqual(explained["versions"]["parser_version"], {"value": mr.UNRESOLVED, "source": "unusable_declared_value"})
        self.assertEqual(explained["receipt_type"], "PUBLICATION_VERSION_EXPLANATION")

    def test_migrating_an_already_current_bundle_without_history_is_a_no_op(self):
        migrated, _ = mr.migrate_bundle(bundle())
        migrated.pop("migration_history", None)
        again, report = mr.migrate_bundle(migrated)
        self.assertEqual(again, migrated)
        self.assertTrue(report["idempotent_at_target"])
        self.assertEqual(report["affected_count"], 0)

    def test_refused_apply_cli_never_overwrites_the_previous_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            output = root / "out.json"
            invalid = bundle()
            invalid["objects"]["PublicEvent"][0].pop("raw_item_id")
            source.write_text(json.dumps(invalid), encoding="utf-8")
            output.write_text("sentinel\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                mr.main(["apply", "--input", str(source), "--output", str(output)])
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["input.json", "out.json"])

    def test_dry_run_never_writes_the_migrated_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            report_path = root / "report.json"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            self.assertEqual(mr.main(["dry-run", "--input", str(source), "--output", str(report_path)]), 0)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["error_count"], 0)
            self.assertIsNotNone(report["output_sha256"])
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["input.json", "report.json"])

    def test_refused_rehash_cli_keeps_the_data_path_and_reports_beside_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "migrated.json"
            output = root / "rehashed.json"
            source.write_text(json.dumps(bundle()), encoding="utf-8")
            self.assertEqual(mr.main(["apply", "--input", str(source), "--output", str(source)]), 0)
            output.write_text("sentinel\n", encoding="utf-8")
            exit_code = mr.main(["rehash", "--input", str(source), "--output", str(output), "--body-hash-version", "raw-bytes-sha256-v1"])
            self.assertEqual(exit_code, 1)
            self.assertEqual(output.read_text(encoding="utf-8"), "sentinel\n")
            report = json.loads((root / "rehashed.body-hash-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["undetermined_body_hash_version"], mr.UNDETERMINED_BODY_HASH_VERSION)
            self.assertEqual(
                len([error for error in report["errors"] if error.get("reason") == mr.MISSING_EVIDENCE_REASON]),
                2,
            )

    def test_rehash_and_explain_cli_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"
            migrated_path = root / "migrated.json"
            rehashed_path = root / "rehashed.json"
            explained_path = root / "explained.json"
            payload = bundle()
            payload["raw_snapshots"] = {RAW_SNAPSHOT_REF: {"payload": RAW_PAYLOAD}}
            for kind in ("PublicEvent", "EvidenceEnvelope"):
                payload["objects"][kind][0]["body_hash_version"] = "raw-bytes-sha256-v1"
            source.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(mr.main(["apply", "--input", str(source), "--output", str(migrated_path)]), 0)
            self.assertEqual(mr.main(["rehash", "--input", str(migrated_path), "--output", str(rehashed_path)]), 0)
            rehashed = json.loads(rehashed_path.read_text(encoding="utf-8"))
            self.assertEqual(rehashed["body_hash_migration_receipt"]["error_count"], 0)
            self.assertEqual(rehashed["objects"]["PublicEvent"][0]["content_sha256"], mr.sha256(RAW_PAYLOAD))
            self.assertEqual(mr.main(["explain-receipt", "--input", str(rehashed_path), "--output", str(explained_path)]), 0)
            explained = json.loads(explained_path.read_text(encoding="utf-8"))
            self.assertEqual(explained["receipt_type"], "PUBLICATION_VERSION_EXPLANATION")
            self.assertEqual(explained["source_receipt_sha256"], mr.sha256(rehashed))

    def test_rebuild_query_store_cli_writes_store_and_receipt(self):
        feed, status, brief = QueryStoreRebuildTests().canonical_paths()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "query-store.json"
            exit_code = mr.main([
                "rebuild-query-store", "--feed", str(feed), "--status", str(status),
                "--brief", str(brief), "--output", str(output),
            ])
            self.assertEqual(exit_code, 0)
            store = json.loads(output.read_text(encoding="utf-8"))
            receipt = json.loads((Path(directory) / "query-store.migration-receipt.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["generation_id"], store["generation_id"])
            self.assertEqual(receipt["counts"]["publication_items"], store["counts"]["publication_items"])


if __name__ == "__main__":
    unittest.main()
