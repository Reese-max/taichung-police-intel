from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from intel_v2.discovery_adapter import (
    MAX_FEED_BYTES,
    MAX_RETAINED_CANDIDATES,
    MAX_TTL_HOURS,
    DiscoveryFeedError,
    classify_change,
    ingest_feed,
    load_feed,
    summarize_canary,
    validate_feed,
    verify_candidate,
)
from intel_v2.entity_binding import registry_runtime


ROOT = Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "tests/fixtures/discovery/dashboard-feed.v1.json"
DOC_PATH = ROOT / "tests/fixtures/discovery/official-matches.json"
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


class DiscoveryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.feed = load_feed(str(FEED_PATH))
        self.documents = json.loads(DOC_PATH.read_text(encoding="utf-8"))

    def test_three_step_media_to_official_and_existing_new_event_receipt(self):
        result = ingest_feed(self.feed, official_documents=self.documents, now=NOW)
        receipt = result["receipt"]
        self.assertEqual(receipt["relevant_count"], 3)
        self.assertEqual(receipt["official_match_count"], 2)
        self.assertEqual(receipt["official_provenance_candidate_count"], 2)
        self.assertEqual(receipt["new_public_event_count"], 0)
        self.assertEqual(receipt["existing_event_match_count"], 0)
        self.assertEqual(receipt["canonical_change_count"], 0)
        self.assertEqual(receipt["status"], "OFFICIAL_PROVENANCE_AVAILABLE")
        media = next(item for item in result["candidates"] if item["authority"] == "media")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(media["official_match_status"], "VERIFIED_OFFICIAL")
        self.assertEqual(media["discovery_source"], "taiwan-intel-dashboard")
        self.assertFalse(media["canonical_write"])
        self.assertEqual(media["official_document_versions"], ["DOCV-OFFICIAL-ROAD-1"])
        self.assertTrue(all("summary" not in row and "risk_level" not in row for row in media["official_document_provenance"]))
        existing_ref = next(
            provenance
            for row in result["candidates"]
            for provenance in row["official_document_provenance"]
            if provenance.get("public_event_id")
        )
        self.assertEqual(existing_ref["public_event_id"], "PUB-EXISTING-1")
        self.assertEqual(sum(not any(p.get("public_event_id") for p in row["official_document_provenance"]) for row in result["candidates"]), 2)

    def test_media_without_official_match_stays_unverified_until_ttl(self):
        result = ingest_feed(self.feed, official_documents=[], now=NOW)
        media = next(item for item in result["candidates"] if item["authority"] == "media")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(media["official_match_status"], "NO_OFFICIAL_MATCH")
        self.assertEqual(result["receipt"]["no_official_match_count"], 3)

        expired = ingest_feed(self.feed, official_documents=[], now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        media_expired = next(item for item in expired["candidates"] if item["authority"] == "media")
        self.assertEqual(media_expired["verification_status"], "EXPIRED")

    def test_official_candidate_without_match_never_confirms(self):
        result = ingest_feed(self.feed, official_documents=[], now=NOW)
        official = next(item for item in result["candidates"] if item["authority"] == "official")
        self.assertEqual(official["candidate_id"], "gd-2222222222222222")
        self.assertNotEqual(official["verification_status"], "VERIFIED_OFFICIAL")
        self.assertEqual(official["official_match_status"], "NO_OFFICIAL_MATCH")
        self.assertEqual(official["official_document_versions"], [])
        self.assertFalse(official["canonical_write"])
        self.assertEqual(result["receipt"]["official_match_count"], 0)
        self.assertEqual(result["receipt"]["canonical_change_count"], 0)

    def test_conflicting_official_documents_are_visible(self):
        docs = copy.deepcopy(self.documents)
        docs.append({**docs[0], "document_id": "DOC-OFFICIAL-ROAD-2", "document_version_id": "DOCV-OFFICIAL-ROAD-2", "fact_fingerprint": "road-control:2026-09-21"})
        result = ingest_feed(self.feed, official_documents=docs, now=NOW)
        media = next(item for item in result["candidates"] if item["candidate_id"] == "gd-1111111111111111")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(media["official_match_status"], "CONFLICT")
        self.assertEqual(result["receipt"]["conflict_count"], 1)

    def test_presentation_only_update_does_not_reverify(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)
        changed = copy.deepcopy(self.feed)
        changed["generated_at"] = "2026-09-21T00:05:00Z"
        changed["items"][0]["summary"] = "只改了雷達摘要"
        second = ingest_feed(changed, previous_state=first["state"], official_documents=[], now=NOW)
        self.assertEqual(second["receipt"]["presentation_only_updated"], ["gd-1111111111111111"])
        self.assertEqual(second["receipt"]["verification_calls"], [])
        self.assertEqual(second["candidates"][0]["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(second["candidates"][0]["official_match_status"], "VERIFIED_OFFICIAL")
        self.assertEqual(classify_change(first["candidates"][0], second["candidates"][0]), "PRESENTATION_ONLY")

    def test_same_generation_is_idempotent_and_out_of_order_fails_closed(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)
        replay = ingest_feed(self.feed, previous_state=first["state"], official_documents=[], now=NOW)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["receipt"]["candidate_added"], first["receipt"]["candidate_added"])
        self.assertEqual({row["candidate_id"] for row in replay["candidates"]}, set(first["state"]["active_candidates"]))
        older = copy.deepcopy(self.feed)
        older["generated_at"] = "2026-09-20T00:00:00Z"
        with self.assertRaisesRegex(DiscoveryFeedError, "out-of-order"):
            ingest_feed(older, previous_state=first["state"], now=NOW)

    def test_candidate_entity_ids_are_scoped_and_never_become_official_evidence(self):
        registry = json.loads((ROOT / "tests/fixtures/entity-registry/false-positives.v1.json").read_text(encoding="utf-8"))
        feed = copy.deepcopy(self.feed)
        feed["items"][0]["entities"] = ["中正路", "中市警"]
        first = ingest_feed(feed, official_documents=[], now=NOW, entity_registry=registry)
        media = next(row for row in first["candidates"] if row["authority"] == "media")
        self.assertEqual(media["candidate_entities"]["location_ids"], ["location:tc-zhongzheng"])
        self.assertEqual(media["candidate_entities"]["agency_ids"], ["agency:tc-police-fixture"])
        self.assertEqual(media["candidate_entities"]["status"], "CANDIDATE_ONLY")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(media["official_match_status"], "NO_OFFICIAL_MATCH")
        self.assertFalse(media["canonical_write"])
        self.assertEqual(first["receipt"]["entity_registry"], registry_runtime().registry_receipt(registry))
        second_registry = copy.deepcopy(registry)
        second_registry["registry_version"] += 1
        rebound = ingest_feed(feed, previous_state=first["state"], official_documents=[], now=NOW, entity_registry=second_registry)
        self.assertFalse(rebound["replayed"])
        self.assertNotEqual(first["receipt"]["consumer_run_id"], rebound["receipt"]["consumer_run_id"])
        self.assertEqual(rebound["receipt"]["verification_calls"], [])
        self.assertEqual(set(rebound["receipt"]["registry_rebound"]), {row["candidate_id"] for row in rebound["candidates"]})
        unscoped = copy.deepcopy(feed)
        unscoped["items"][0]["region"] = None
        ambiguous = ingest_feed(unscoped, official_documents=[], now=NOW, entity_registry=registry)
        media = next(row for row in ambiguous["candidates"] if row["authority"] == "media")
        self.assertEqual(media["candidate_entities"]["agency_ids"], [])
        self.assertEqual(media["candidate_entities"]["location_ids"], [])
        self.assertEqual(media["candidate_entities"]["ambiguous_hints"], ["中市警", "中正路"])

    def test_schema_mismatch_and_upstream_gap_fail_closed(self):
        broken = copy.deepcopy(self.feed)
        broken["items"][0].pop("authority")
        with self.assertRaises(DiscoveryFeedError):
            ingest_feed(broken)
        unknown_item_field = copy.deepcopy(self.feed)
        unknown_item_field["items"][0]["model_priority"] = "high"
        with self.assertRaisesRegex(DiscoveryFeedError, "unsupported fields"):
            ingest_feed(unknown_item_field)
        invalid_coordinate = copy.deepcopy(self.feed)
        invalid_coordinate["items"][0]["lat"] = True
        with self.assertRaisesRegex(DiscoveryFeedError, "finite number"):
            ingest_feed(invalid_coordinate)
        paused = copy.deepcopy(self.feed)
        paused["operating_state"] = "PAUSED"
        paused["stale"] = True
        result = ingest_feed(paused, official_documents=self.documents, now=NOW)
        self.assertFalse(result["receipt"]["upstream_current"])
        self.assertTrue(all(item["verification_status"] in {"DISCOVERY_UNVERIFIED", "OFFICIAL_CANDIDATE"} for item in result["candidates"]))

    def test_ttl_uses_source_timestamp_and_expired_candidates_cannot_promote(self):
        feed = copy.deepcopy(self.feed)
        feed["items"][0]["observed_at"] = None
        expiry = datetime(2026, 9, 23, 10, tzinfo=timezone.utc)
        at_expiry = ingest_feed(
            feed,
            official_documents=self.documents,
            now=expiry,
        )
        media = next(row for row in at_expiry["candidates"] if row["authority"] == "media")
        self.assertEqual(media["expires_at"], "2026-09-23T10:00:00Z")
        self.assertEqual(media["verification_status"], "EXPIRED")
        self.assertEqual(media["official_document_versions"], [])
        self.assertEqual(at_expiry["receipt"]["verification_calls"], [])
        for item in feed["items"]:
            item["event_time"] = None
        with self.assertRaisesRegex(DiscoveryFeedError, "TTL anchor"):
            ingest_feed(feed, official_documents=self.documents, now=NOW)

        candidate = dict(media, upstream_operating_state="ACTIVE", expires_at="2026-09-23T10:00:00Z")
        promoted = verify_candidate(candidate, self.documents, now=expiry)
        self.assertEqual(promoted["verification_status"], "EXPIRED")
        self.assertEqual(promoted["official_document_versions"], [])
        self.assertFalse(promoted["canonical_write"])

    def test_schema_and_runtime_limits_reject_booleans_and_unbounded_overrides(self):
        boolean_schema = copy.deepcopy(self.feed)
        boolean_schema["schema_version"] = True
        with self.assertRaisesRegex(DiscoveryFeedError, "schema_version"):
            validate_feed(boolean_schema)
        for options, message in (
            ({"ttl_hours": MAX_TTL_HOURS + 1}, "ttl_hours"),
            ({"max_candidates": 51}, "max_candidates"),
            ({"max_retained_candidates": MAX_RETAINED_CANDIDATES + 1}, "max_retained_candidates"),
        ):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, message):
                ingest_feed(self.feed, now=NOW, **options)

    def test_expiry_pruning_runs_even_for_same_generation_replay(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)
        expired = ingest_feed(
            self.feed,
            previous_state=first["state"],
            official_documents=self.documents,
            now=datetime(2026, 9, 25, tzinfo=timezone.utc),
        )
        self.assertFalse(expired["replayed"])
        self.assertEqual(expired["receipt"]["expired_candidate_count"], 3)
        self.assertEqual(expired["receipt"]["official_match_count"], 0)
        self.assertEqual(expired["state"]["candidates"], {})
        self.assertEqual(expired["state"]["active_candidates"], [])

    def test_retained_candidate_store_has_a_deterministic_hard_cap(self):
        result = ingest_feed(
            self.feed,
            official_documents=self.documents,
            now=NOW,
            max_retained_candidates=1,
        )
        self.assertEqual(result["receipt"]["retained_candidate_count"], 1)
        self.assertEqual(result["receipt"]["retained_candidate_limit"], 1)
        self.assertTrue(result["receipt"]["state_truncated"])
        self.assertEqual(set(result["state"]["candidates"]), {"gd-1111111111111111"})
        self.assertEqual(result["state"]["active_candidates"], ["gd-1111111111111111"])
        replay = ingest_feed(
            self.feed,
            previous_state=result["state"],
            official_documents=self.documents,
            now=NOW,
            max_retained_candidates=1,
        )
        self.assertFalse(replay["replayed"])
        self.assertEqual(len(replay["candidates"]), 3)
        self.assertEqual(replay["receipt"]["official_provenance_candidate_count"], 2)

    def test_feed_item_and_byte_caps_fail_closed(self):
        oversized_items = copy.deepcopy(self.feed)
        template = copy.deepcopy(oversized_items["items"][0])
        oversized_items["items"] = []
        for index in range(201):
            item = copy.deepcopy(template)
            item["discovery_id"] = f"gd-{index:016x}"
            item["event_time"] = "2026-09-20T10:00:00Z"
            oversized_items["items"].append(item)
        oversized_items["item_count"] = len(oversized_items["items"])
        with self.assertRaisesRegex(DiscoveryFeedError, "exceeds 200 items"):
            validate_feed(oversized_items)

        oversized_object = copy.deepcopy(self.feed)
        oversized_object["items"][0]["summary"] = "x" * MAX_FEED_BYTES
        with self.assertRaisesRegex(DiscoveryFeedError, "bytes"):
            ingest_feed(oversized_object, now=NOW)

        with tempfile.TemporaryDirectory() as directory:
            oversized_path = Path(directory) / "oversized.json"
            raw = FEED_PATH.read_bytes()
            oversized_path.write_bytes(raw + b" " * (MAX_FEED_BYTES + 1 - len(raw)))
            with self.assertRaisesRegex(DiscoveryFeedError, "bytes"):
                load_feed(str(oversized_path))

    def test_paused_history_replay_verifies_for_history_without_promotion(self):
        paused = copy.deepcopy(self.feed)
        paused["operating_state"] = "PAUSED"
        paused["stale"] = True
        blocked = ingest_feed(paused, official_documents=self.documents, now=NOW)
        self.assertEqual(blocked["receipt"]["upstream_mode"], "PAUSED")
        self.assertEqual(blocked["receipt"]["verification_calls"], [])
        self.assertEqual(blocked["receipt"]["official_match_count"], 0)

        replay = ingest_feed(
            paused,
            previous_state=blocked["state"],
            official_documents=self.documents,
            now=NOW,
            historical_replay=True,
        )
        self.assertFalse(replay["replayed"])
        self.assertEqual(replay["receipt"]["upstream_mode"], "HISTORICAL_REPLAY")
        self.assertEqual(replay["receipt"]["historical_official_match_count"], 2)
        self.assertEqual(replay["receipt"]["official_match_count"], 0)
        self.assertEqual(replay["receipt"]["new_public_event_count"], 0)
        self.assertEqual(replay["receipt"]["canonical_change_count"], 0)
        self.assertTrue(all(row["historical_only"] for row in replay["candidates"]))
        self.assertEqual([row["official_match_status"] for row in replay["candidates"]], ["VERIFIED_OFFICIAL", "VERIFIED_OFFICIAL", "NO_OFFICIAL_MATCH"])
        media = next(row for row in replay["candidates"] if row["authority"] == "media")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(replay["receipt"]["official_provenance_candidate_count"], 0)

        with self.assertRaisesRegex(DiscoveryFeedError, "only valid for PAUSED"):
            ingest_feed(self.feed, historical_replay=True, now=NOW)

    def test_restoring_canary_reports_matches_without_changing_candidate_state(self):
        restoring = copy.deepcopy(self.feed)
        restoring["operating_state"] = "RESTORING"
        restoring["stale"] = True
        withheld = ingest_feed(restoring, official_documents=self.documents, now=NOW)
        self.assertEqual(withheld["receipt"]["upstream_mode"], "CANARY_ONLY")
        self.assertEqual(withheld["receipt"]["canary_match_count"], 0)
        self.assertEqual(withheld["receipt"]["official_match_count"], 0)
        self.assertTrue(all(row["verification_status"] in {"DISCOVERY_UNVERIFIED", "OFFICIAL_CANDIDATE"} for row in withheld["candidates"]))

        canary = ingest_feed(
            restoring,
            previous_state=withheld["state"],
            official_documents=self.documents,
            now=NOW,
            canary_mode=True,
        )
        self.assertFalse(canary["replayed"])
        self.assertEqual(canary["receipt"]["canary_match_count"], 2)
        self.assertEqual(canary["receipt"]["official_match_count"], 0)
        self.assertEqual(canary["receipt"]["new_public_event_count"], 0)
        self.assertEqual(canary["receipt"]["canonical_change_count"], 0)
        for row in canary["candidates"]:
            self.assertIn(row["verification_status"], {"DISCOVERY_UNVERIFIED", "OFFICIAL_CANDIDATE"})
            self.assertEqual(row["canary_evaluation"]["status"], "NO_OFFICIAL_MATCH" if row["candidate_id"] == "gd-4444444444444444" else "OFFICIAL_MATCH")
            self.assertEqual(row["canonical_write"], False)

        with self.assertRaisesRegex(DiscoveryFeedError, "only valid for RESTORING"):
            ingest_feed(self.feed, canary_mode=True, now=NOW)

    def test_degraded_feed_preserves_gap_and_blocks_automatic_verification(self):
        degraded = copy.deepcopy(self.feed)
        degraded["operating_state"] = "DEGRADED"
        degraded["stale"] = True
        result = ingest_feed(degraded, official_documents=self.documents, now=NOW)
        self.assertEqual(result["receipt"]["status"], "DEGRADED_GAP")
        self.assertEqual(result["receipt"]["upstream_mode"], "DEGRADED_GAP")
        self.assertTrue(result["receipt"]["upstream_gap"])
        self.assertEqual(result["receipt"]["verification_calls"], [])
        self.assertEqual(result["receipt"]["official_match_count"], 0)
        self.assertTrue(all(row["upstream_gap"] for row in result["candidates"]))
        self.assertTrue(all(row["verification_blocked_reason"] == "UPSTREAM_DEGRADED_GAP" for row in result["candidates"]))

    def test_same_original_source_does_not_add_independent_count(self):
        duplicated = copy.deepcopy(self.documents[0])
        duplicated["document_id"] = "DOC-OFFICIAL-ROAD-DUP"
        duplicated["document_version_id"] = "DOCV-OFFICIAL-ROAD-DUP"
        duplicated["source_id"] = "S-032-ALIAS"
        result = ingest_feed(
            self.feed,
            official_documents=[self.documents[0], duplicated],
            now=NOW,
        )
        media = next(row for row in result["candidates"] if row["authority"] == "media")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(media["official_match_status"], "VERIFIED_OFFICIAL")
        self.assertEqual(media["independent_source_count"], 1)
        self.assertEqual(media["official_document_versions"], ["DOCV-OFFICIAL-ROAD-1", "DOCV-OFFICIAL-ROAD-DUP"])

    def test_generation_identity_tracks_operating_state(self):
        active = ingest_feed(self.feed, now=NOW)["receipt"]["upstream_generation_id"]
        degraded = copy.deepcopy(self.feed)
        degraded["operating_state"] = "DEGRADED"
        degraded["stale"] = True
        gap = ingest_feed(degraded, now=NOW)["receipt"]["upstream_generation_id"]
        self.assertNotEqual(active, gap)

    def test_canary_keeps_full_denominator(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)["receipt"]
        report = summarize_canary([
            first,
            {**first, "status": "PUBLISH_FAILED", "relevant_count": 0, "canonical_change_count": 0},
            {"relevant_count": 0},
        ], window_days=14)
        self.assertEqual(report["run_count"], 3)
        self.assertEqual(report["status_counts"]["PUBLISH_FAILED"], 1)
        self.assertEqual(report["status_counts"]["OFFICIAL_PROVENANCE_AVAILABLE"], 1)
        self.assertEqual(report["status_counts"]["UNKNOWN"], 1)
        self.assertEqual(sum(report["status_counts"].values()), report["run_count"])
        self.assertEqual(report["canonical_change_count"], 0)


if __name__ == "__main__":
    unittest.main()
