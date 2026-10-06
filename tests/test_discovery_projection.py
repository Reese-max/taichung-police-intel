from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from intel_v2.discovery_adapter import ingest_feed, load_feed
from intel_v2.discovery_projection import DiscoveryFeedError, project_discovery_signals


ROOT = Path(__file__).resolve().parents[1]
FEED_PATH = ROOT / "tests/fixtures/discovery/dashboard-feed.v1.json"
DOC_PATH = ROOT / "tests/fixtures/discovery/official-matches.json"
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


class DiscoveryProjectionTests(unittest.TestCase):
    def setUp(self):
        self.feed = load_feed(str(FEED_PATH))
        self.documents = json.loads(DOC_PATH.read_text(encoding="utf-8"))
        self.result = ingest_feed(self.feed, official_documents=self.documents, now=NOW)

    def test_payload_separates_confirmed_and_pending_with_denominators(self):
        payload = project_discovery_signals(self.result, now=NOW)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["kind"], "GOVINTEL_DISCOVERY_SIGNALS")
        self.assertEqual(payload["status"], "FIXTURE_ONLY")
        self.assertIs(payload["production_verified"], False)
        confirmed_ids = [row["candidate_id"] for row in payload["confirmed"]]
        pending_ids = [row["candidate_id"] for row in payload["pending"]]
        self.assertEqual(confirmed_ids, ["gd-2222222222222222"])
        self.assertEqual(pending_ids, ["gd-1111111111111111", "gd-4444444444444444"])
        self.assertEqual(payload["counts"]["confirmed_count"], 1)
        self.assertEqual(payload["counts"]["pending_total"], 2)
        self.assertEqual(payload["counts"]["pending_shown"], 2)
        self.assertEqual(payload["counts"]["relevant_count"], 3)
        self.assertEqual(payload["counts"]["feed_item_count"], 4)
        self.assertIs(payload["counts"]["truncated"], False)

    def test_confirmed_rows_expose_official_evidence_not_media_claim(self):
        payload = project_discovery_signals(self.result, now=NOW)
        media_row = next(row for row in payload["pending"] if row["authority"] == "media")
        self.assertEqual(media_row["verification_status"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(media_row["official_document_versions"], ["DOCV-OFFICIAL-ROAD-1"])
        self.assertEqual(media_row["official_urls"], ["https://www.traffic.taichung.gov.tw/news/road-1"])
        self.assertTrue(all(row["official_document_versions"] for row in payload["confirmed"]))
        self.assertFalse(any(row.get("canonical_write") for row in payload["confirmed"] + payload["pending"]))

    def test_pending_rows_cap_at_five_and_keep_total(self):
        result = ingest_feed(self.feed, official_documents=[], now=NOW)
        base_candidates = result["candidates"]
        synthetic = []
        for index in range(9):
            clone = dict(base_candidates[index % len(base_candidates)])
            clone["candidate_id"] = f"gd-{index:016x}"
            clone["event_time"] = f"2026-09-2{(index % 9):01d}T10:00:00Z"
            synthetic.append(clone)
        payload = project_discovery_signals({**result, "candidates": synthetic}, now=NOW)
        self.assertEqual(len(payload["pending"]), 5)
        self.assertEqual(payload["counts"]["pending_total"], 9)
        self.assertEqual(payload["counts"]["pending_shown"], 5)
        self.assertEqual(payload["limits"]["pending_max"], 5)

    def test_expired_candidates_leave_pending_but_keep_denominator(self):
        result = ingest_feed(self.feed, official_documents=[], now=NOW)
        later = datetime(2026, 9, 30, tzinfo=timezone.utc)
        payload = project_discovery_signals(result, now=later)
        self.assertEqual(payload["pending"], [])
        self.assertEqual(payload["counts"]["pending_total"], 0)
        self.assertEqual(payload["counts"]["expired_count"], 3)

    def test_official_match_does_not_make_discovery_signal_permanent(self):
        payload = project_discovery_signals(self.result, now=datetime(2026, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(payload["confirmed"], [])
        self.assertEqual(payload["pending"], [])
        self.assertEqual(payload["counts"]["expired_count"], 3)

    def test_upstream_states_map_to_signal_modes(self):
        for state, mode in (
            ("ACTIVE", "CURRENT"),
            ("PAUSED", "HISTORICAL_REPLAY_ONLY"),
            ("RESTORING", "CANARY_ONLY"),
            ("DEGRADED", "GAP_VISIBLE"),
        ):
            feed = copy.deepcopy(self.feed)
            feed["operating_state"] = state
            feed["stale"] = state != "ACTIVE"
            result = ingest_feed(feed, official_documents=self.documents, now=NOW)
            payload = project_discovery_signals(result, now=NOW)
            self.assertEqual(payload["upstream"]["operating_state"], state)
            self.assertEqual(payload["upstream"]["signal_mode"], mode)
            self.assertEqual(payload["upstream"]["current"], state == "ACTIVE")

    def test_live_status_fails_closed_when_upstream_not_current(self):
        feed = copy.deepcopy(self.feed)
        feed["operating_state"] = "DEGRADED"
        feed["stale"] = True
        result = ingest_feed(feed, official_documents=self.documents, now=NOW)
        with self.assertRaises(DiscoveryFeedError):
            project_discovery_signals(result, now=NOW, status="LIVE")

    def test_forged_media_confirmation_is_rejected_even_with_versions(self):
        media = next(row for row in self.result["candidates"] if row["authority"] == "media")
        forged = copy.deepcopy(media)
        forged["verification_status"] = "VERIFIED_OFFICIAL"
        forged["official_document_versions"] = ["DOCV-OFFICIAL-ROAD-1"]
        forged["official_urls"] = ["https://www.traffic.taichung.gov.tw/news/road-1"]
        bad = {**self.result, "candidates": [forged]}
        with self.assertRaises(DiscoveryFeedError):
            project_discovery_signals(bad, now=NOW)

    def test_projection_fails_closed_on_invalid_or_inconsistent_input(self):
        with self.assertRaises(DiscoveryFeedError):
            project_discovery_signals(None)
        with self.assertRaises(DiscoveryFeedError):
            project_discovery_signals({})
        with self.assertRaises(DiscoveryFeedError):
            project_discovery_signals({"state": {}, "candidates": [], "receipt": {"schema_version": 2}})
        bad = copy.deepcopy(self.result)
        bad["candidates"][0]["verification_status"] = "VERIFIED_OFFICIAL"
        bad["candidates"][0]["official_document_versions"] = []
        with self.assertRaises(DiscoveryFeedError):
            project_discovery_signals(bad)
        unknown = copy.deepcopy(self.result)
        unknown["candidates"][0]["verification_status"] = "CONFIRMED_BY_MEDIA"
        with self.assertRaises(DiscoveryFeedError):
            project_discovery_signals(unknown)

    def test_pending_notes_never_claim_confirmation(self):
        result = ingest_feed(self.feed, official_documents=[], now=NOW)
        payload = project_discovery_signals(result, now=NOW)
        forbidden = ("已證實", "官方指出")
        for row in payload["pending"]:
            self.assertNotEqual(row["verification_status"], "VERIFIED_OFFICIAL")
            self.assertFalse(any(term in row["note"] for term in forbidden))
        no_match = next(row for row in payload["pending"] if row["verification_status"] == "NO_OFFICIAL_MATCH")
        self.assertEqual(no_match["note"], "尚未找到官方確認")

    def test_projection_is_deterministic(self):
        first = project_discovery_signals(self.result, now=NOW)
        second = project_discovery_signals(self.result, now=NOW)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )


if __name__ == "__main__":
    unittest.main()
