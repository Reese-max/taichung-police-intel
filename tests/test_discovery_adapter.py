from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from intel_v2 import discovery_adapter as adapter


ROOT = Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "tests/fixtures/discovery/dashboard-feed.v1.json"
DOC_PATH = ROOT / "tests/fixtures/discovery/official-matches.json"
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


class DiscoveryAdapterTests(unittest.TestCase):
    def setUp(self):
        self.feed = load_feed(str(FEED_PATH))
        self.documents = json.loads(DOC_PATH.read_text(encoding="utf-8"))

    def test_index_attaches_official_provenance_without_fusing_events(self):
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

    def test_late_official_match_and_conflict_refresh_an_unchanged_feed(self):
        first = ingest_feed(self.feed, official_documents=[], now=NOW)
        later = NOW + timedelta(hours=1)
        matched = ingest_feed(self.feed, previous_state=first["state"],
                              official_documents=self.documents, now=later)
        self.assertFalse(matched["replayed"])
        self.assertEqual(matched["receipt"]["official_match_count"], 2)
        self.assertEqual(matched["receipt"]["confirmation_latency_seconds"], [3600, 3600])
        media = next(row for row in matched["candidates"] if row["authority"] == "media")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        conflict = {**self.documents[0], "document_id": "ROAD-OTHER",
                    "document_version_id": "ROAD-OTHER:v1", "fact_fingerprint": "disagrees"}
        refreshed = ingest_feed(self.feed, previous_state=matched["state"],
                                official_documents=[*self.documents, conflict], now=later)
        self.assertEqual(refreshed["receipt"]["conflict_count"], 1)
        self.assertEqual(refreshed["receipt"]["confirmation_latency_seconds"], [])

    def test_same_envelope_with_changed_items_is_not_a_cached_replay(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)
        changed = copy.deepcopy(self.feed)
        changed["items"][0]["title"] = "臺中市道路管制範圍變更"
        result = ingest_feed(changed, previous_state=first["state"], now=NOW)
        self.assertFalse(result["replayed"])
        self.assertIn("gd-1111111111111111", result["receipt"]["verification_calls"])

    def test_relevance_requires_a_supported_category_and_public_service_impact(self):
        for category, title, region, expected in (
            ("娛樂", "臺中市演唱會花絮", "臺中市", 0),
            ("社會", "臺中市藝人生活近況", "臺中市", 0),
            ("社會", "臺中市警察反詐宣導", "臺中市", 1),
            ("交通", "全國道路交通政策適用各縣市", "臺北市", 1),
            ("交通", "道路施工消息", None, 0),
        ):
            with self.subTest(category=category, title=title):
                feed = copy.deepcopy(self.feed)
                item = {**feed["items"][0], "category": category, "title": title,
                        "region": region, "summary": None, "topic": None}
                feed.update(items=[item], item_count=1)
                self.assertEqual(ingest_feed(feed, now=NOW)["receipt"]["relevant_count"], expected)

    def test_cap_and_replay_do_not_erase_canary_denominators(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW, max_candidates=1)
        self.assertEqual(first["receipt"]["relevant_count"], 3)
        self.assertEqual(first["receipt"]["candidate_admitted_count"], 1)
        replay = ingest_feed(self.feed, previous_state=first["state"], now=NOW, max_candidates=1)
        report = summarize_canary([first["receipt"], replay["receipt"], {"status": "PUBLISH_FAILED"}])
        self.assertEqual(report["run_count"], 3)
        self.assertEqual(report["replayed_run_count"], 1)
        self.assertEqual(report["feed_item_count"], 4)
        self.assertEqual(report["relevant_count"], 3)
        self.assertEqual(report["candidate_admitted_count"], 1)
        self.assertEqual(report["official_match_count"], 1)
        self.assertEqual(report["actionable_change_count"], 0)
        self.assertEqual(report["tracking_count"], 0)
        self.assertEqual(report["false_positive_count"], 0)
        self.assertEqual(report["confirmation_latency_seconds"], {"count": 1, "total": 0, "mean": 0})
        self.assertEqual(report["missing_denominator_run_count"], 1)

    def test_renewed_generated_at_cannot_revive_expired_source_data(self):
        feed = copy.deepcopy(self.feed)
        feed["generated_at"] = "2026-09-25T00:00:00Z"
        result = ingest_feed(feed, official_documents=self.documents,
                             now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        self.assertEqual(result["receipt"]["expired_candidate_count"], 3)
        self.assertEqual(result["receipt"]["official_match_count"], 0)
        self.assertEqual(result["receipt"]["verification_calls"], [])

    def test_official_index_requires_nonempty_identity_and_original_https_url(self):
        for field, value in (("document_id", ""), ("document_version_id", None),
                             ("source_id", []), ("official_url", "http://example.test/news")):
            with self.subTest(field=field), self.assertRaises(DiscoveryFeedError):
                ingest_feed(self.feed, official_documents=[{**self.documents[0], field: value}], now=NOW)

    def test_media_to_official_versions_to_new_and_existing_public_event(self):
        documents = json.loads((ROOT / "tests/fixtures/discovery/event-documents.v1.json").read_text(encoding="utf-8"))
        original_documents = copy.deepcopy(documents)
        discovery = ingest_feed(self.feed, official_documents=[], now=NOW)
        matched = ingest_feed(self.feed, previous_state=discovery["state"],
                              official_documents=documents, now=NOW + timedelta(hours=1))
        linked = adapter.link_official_events(matched, documents)
        self.assertEqual(linked["receipt"]["new_public_event_count"], 1)
        self.assertEqual(linked["receipt"]["canonical_change_count"], 0)
        event = linked["public_events"][0]
        self.assertEqual(event["fusion_status"], "CONFIRMED")
        self.assertEqual(event["independent_source_count"], 2)
        self.assertEqual(len(event["linked_document_versions"]), 2)
        self.assertEqual(event["source_provenance"][0]["acquisition_paths"],
                         ["DIRECT_OFFICIAL", "taiwan-intel-dashboard"])
        media = next(row for row in linked["candidates"] if row["candidate_id"] == "gd-1111111111111111")
        self.assertEqual(media["public_event_ids"], [event["public_event_id"]])
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertFalse(media["canonical_write"])
        existing = copy.deepcopy(linked["public_events"])
        again = adapter.link_official_events(matched, documents, existing_public_events=existing)
        self.assertEqual(again["receipt"]["new_public_event_count"], 0)
        self.assertEqual(again["receipt"]["existing_event_match_count"], 1)
        self.assertEqual(existing, linked["public_events"])
        self.assertEqual(documents, original_documents)
        self.assertEqual(matched["receipt"]["new_public_event_count"], 0)

    def test_event_link_rejects_unmatched_or_changed_official_document_identity(self):
        documents = json.loads((ROOT / "tests/fixtures/discovery/event-documents.v1.json").read_text(encoding="utf-8"))
        result = ingest_feed(self.feed, official_documents=documents, now=NOW)
        forged = copy.deepcopy(documents)
        forged[0]["official_url"] = "https://another.example.test/changed"
        with self.assertRaises(DiscoveryFeedError):
            adapter.link_official_events(result, forged)
        without_match = ingest_feed(self.feed, official_documents=[], now=NOW)
        self.assertEqual(adapter.link_official_events(without_match, documents)["public_events"], [])

    def test_official_conflict_reaches_event_preview_without_promoting_media(self):
        documents = json.loads((ROOT / "tests/fixtures/discovery/event-documents.v1.json").read_text(encoding="utf-8"))
        documents[1]["event_end_at"] = "2026-09-20T23:00:00+08:00"
        documents[1]["fact_fingerprint"] = "official-time-disagrees"
        result = ingest_feed(self.feed, official_documents=documents, now=NOW)
        linked = adapter.link_official_events(result, documents)
        self.assertEqual(linked["public_events"][0]["fusion_status"], "CONFLICT")
        media = next(row for row in linked["candidates"] if row["candidate_id"] == "gd-1111111111111111")
        self.assertEqual(media["official_match_status"], "CONFLICT")
        self.assertEqual(media["verification_status"], "DISCOVERY_UNVERIFIED")

    def test_event_preview_preserves_official_conflicts_beyond_time_location_fields(self):
        documents = json.loads((ROOT / "tests/fixtures/discovery/event-documents.v1.json").read_text(encoding="utf-8"))
        for conflict_fields in ({"fact_fingerprint": "different-official-claim"}, {"conflict": True}):
            with self.subTest(conflict_fields=conflict_fields):
                conflicting = [documents[0], {**documents[1], **conflict_fields}]
                result = ingest_feed(self.feed, official_documents=conflicting, now=NOW)
                linked = adapter.link_official_events(result, conflicting)
                self.assertEqual(linked["public_events"][0]["fusion_status"], "CONFLICT")
                self.assertIn("official_provenance", linked["public_events"][0]["conflict_fields"])

    def test_existing_document_version_ownership_survives_manual_event_identity_change(self):
        documents = json.loads((ROOT / "tests/fixtures/discovery/event-documents.v1.json").read_text(encoding="utf-8"))
        result = ingest_feed(self.feed, official_documents=documents, now=NOW)
        baseline = adapter.link_official_events(result, documents)
        existing = copy.deepcopy(baseline["public_events"])
        existing[0]["public_event_id"] = "PE-canonical-manual-merge"
        existing[0]["fusion_status"] = "CONFLICT"
        existing[0]["conflict_fields"] = ["status"]
        original_existing = copy.deepcopy(existing)
        linked = adapter.link_official_events(result, documents, existing_public_events=existing)
        media = next(row for row in linked["candidates"] if row["candidate_id"] == "gd-1111111111111111")
        self.assertEqual(media["public_event_ids"], ["PE-canonical-manual-merge"])
        self.assertTrue(media["matched_existing"])
        self.assertEqual(linked["receipt"]["new_public_event_count"], 0)
        self.assertEqual(linked["receipt"]["existing_event_match_count"], 1)
        self.assertEqual(linked["public_events"][0]["fusion_status"], "CONFLICT")
        self.assertEqual(existing, original_existing)
        ambiguous = [*existing, {**existing[0], "public_event_id": "PE-other-owner"}]
        with self.assertRaisesRegex(DiscoveryFeedError, "ambiguous"):
            adapter.link_official_events(result, documents, existing_public_events=ambiguous)

    def test_canary_counts_only_the_requested_calendar_window(self):
        receipts = []
        for day in range(16):
            receipts.append({"consumed_at": (NOW + timedelta(days=day)).isoformat(),
                             "status": "INGESTED", "feed_item_count": 4, "relevant_count": 3,
                             "candidate_admitted_count": 3, "official_match_count": 1,
                             "no_official_match_count": 2, "confirmation_latency_seconds": [120]})
        report = summarize_canary(receipts, window_days=14)
        self.assertEqual(report["run_count"], 14)
        self.assertEqual(report["feed_item_count"], 56)
        self.assertEqual(report["relevant_count"], 42)
        self.assertEqual(report["official_match_count"], 14)
        self.assertEqual(report["no_official_match_count"], 28)
        self.assertEqual(report["observed_day_count"], 14)
        self.assertEqual(report["coverage_start"], "2026-09-23")
        self.assertEqual(report["coverage_end"], "2026-10-06")
        self.assertTrue(report["window_complete"])
        self.assertFalse(report["production_verified"])
        self.assertEqual(report["confirmation_latency_seconds"], {"count": 14, "total": 1680, "mean": 120})
        incomplete = summarize_canary([receipts[-1], {"status": "FAILED"}])
        self.assertFalse(incomplete["window_complete"])
        self.assertEqual(incomplete["missing_timestamp_run_count"], 1)

    def test_cli_replays_event_link_and_writes_canary_report(self):
        script = str(ROOT / "scripts/discovery-adapter.py")
        documents = str(ROOT / "tests/fixtures/discovery/event-documents.v1.json")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "ingested.json"
            process = subprocess.run([sys.executable, "-X", "utf8", script, "ingest", "--feed", str(FEED_PATH),
                                      "--official-documents", documents, "--event-documents", documents,
                                      "--now", NOW.isoformat(), "--output", str(output)],
                                     capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(process.returncode, 0, process.stderr)
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(result["public_events"]), 1)
            receipts = Path(directory) / "receipts.json"
            receipts.write_text(json.dumps([result["receipt"]]), encoding="utf-8")
            report_path = Path(directory) / "report.json"
            process = subprocess.run([sys.executable, "-X", "utf8", script, "canary-report", "--receipts", str(receipts),
                                      "--window-days", "14", "--output", str(report_path)],
                                     capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(process.returncode, 0, process.stderr)
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["feed_item_count"], 4)
            self.assertEqual(report["new_public_event_count"], 1)
            self.assertFalse(report["window_complete"])


if __name__ == "__main__":
    unittest.main()
