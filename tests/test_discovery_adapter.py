from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from intel_v2.discovery_adapter import DiscoveryFeedError, classify_change, ingest_feed, load_feed, summarize_canary


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
        self.assertEqual(receipt["relevant_count"], 2)
        self.assertEqual(receipt["official_match_count"], 2)
        self.assertEqual(receipt["new_public_event_count"], 1)
        self.assertEqual(receipt["existing_event_match_count"], 1)
        self.assertEqual(receipt["canonical_change_count"], 1)
        self.assertEqual(receipt["status"], "PENDING_PUBLICATION")
        media = next(item for item in result["candidates"] if item["authority"] == "media")
        self.assertEqual(media["verification_status"], "VERIFIED_OFFICIAL")
        self.assertFalse(media["canonical_write"])

    def test_media_without_official_match_stays_unverified_until_ttl(self):
        result = ingest_feed(self.feed, official_documents=[], now=NOW)
        media = next(item for item in result["candidates"] if item["authority"] == "media")
        self.assertEqual(media["verification_status"], "NO_OFFICIAL_MATCH")
        self.assertEqual(result["receipt"]["no_official_match_count"], 2)
        expired = ingest_feed(self.feed, official_documents=[], now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        media_expired = next(item for item in expired["candidates"] if item["authority"] == "media")
        self.assertEqual(media_expired["verification_status"], "EXPIRED")

    def test_conflicting_official_documents_are_visible(self):
        docs = copy.deepcopy(self.documents)
        docs.append({**docs[0], "document_id": "DOC-OFFICIAL-ROAD-2", "document_version_id": "DOCV-OFFICIAL-ROAD-2", "fact_fingerprint": "road-control:2026-09-21"})
        result = ingest_feed(self.feed, official_documents=docs, now=NOW)
        media = next(item for item in result["candidates"] if item["candidate_id"] == "gd-1111111111111111")
        self.assertEqual(media["verification_status"], "CONFLICT")
        self.assertEqual(result["receipt"]["conflict_count"], 1)

    def test_presentation_only_update_does_not_reverify(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)
        changed = copy.deepcopy(self.feed)
        changed["generated_at"] = "2026-09-21T00:05:00Z"
        changed["items"][0]["summary"] = "只改了雷達摘要"
        second = ingest_feed(changed, previous_state=first["state"], official_documents=[], now=NOW)
        self.assertEqual(second["receipt"]["presentation_only_updated"], ["gd-1111111111111111"])
        self.assertEqual(second["receipt"]["verification_calls"], [])
        self.assertEqual(second["candidates"][0]["verification_status"], "VERIFIED_OFFICIAL")
        self.assertEqual(classify_change(first["candidates"][0], second["candidates"][0]), "PRESENTATION_ONLY")

    def test_same_generation_is_idempotent_and_out_of_order_fails_closed(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)
        replay = ingest_feed(self.feed, previous_state=first["state"], official_documents=[], now=NOW)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["receipt"]["candidate_added"], first["receipt"]["candidate_added"])
        older = copy.deepcopy(self.feed)
        older["generated_at"] = "2026-09-20T00:00:00Z"
        with self.assertRaisesRegex(DiscoveryFeedError, "out-of-order"):
            ingest_feed(older, previous_state=first["state"], now=NOW)

    def test_schema_mismatch_and_upstream_gap_fail_closed(self):
        broken = copy.deepcopy(self.feed)
        broken["items"][0].pop("authority")
        with self.assertRaises(DiscoveryFeedError):
            ingest_feed(broken)
        paused = copy.deepcopy(self.feed)
        paused["operating_state"] = "PAUSED"
        paused["stale"] = True
        result = ingest_feed(paused, official_documents=self.documents, now=NOW)
        self.assertFalse(result["receipt"]["upstream_current"])
        self.assertTrue(all(item["verification_status"] in {"DISCOVERY_UNVERIFIED", "OFFICIAL_CANDIDATE"} for item in result["candidates"]))

    def test_canary_keeps_full_denominator(self):
        first = ingest_feed(self.feed, official_documents=self.documents, now=NOW)["receipt"]
        report = summarize_canary([first, {**first, "status": "PUBLISH_FAILED", "relevant_count": 0, "canonical_change_count": 0}], window_days=14)
        self.assertEqual(report["run_count"], 2)
        self.assertEqual(report["status_counts"]["PUBLISH_FAILED"], 1)
        self.assertEqual(report["canonical_change_count"], 1)


if __name__ == "__main__":
    unittest.main()
