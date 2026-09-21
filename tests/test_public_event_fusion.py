import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "public-event-fusion.py"
spec = importlib.util.spec_from_file_location("public_event_fusion", SCRIPT)
fusion = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(fusion)


def document(
    source: str,
    *,
    day: str = "2026-09-20",
    district: str = "location:tc-xitun",
    named: str | None = "event:demo",
    version: str = "v1",
    independent: str | None = None,
    authority: str | None = "official",
    start: str = "18:00",
) -> dict:
    value = {
        "document_id": f"doc-{source}",
        "document_version_id": f"doc-{source}:{version}",
        "source_id": source,
        "independent_source_id": independent or source,
        "authority": authority,
        "title": f"示範活動 {source}",
        "event_type": "large_event",
        "named_event_id": named,
        "event_start_at": f"{day}T{start}:00+08:00",
        "event_end_at": f"{day}T22:00:00+08:00",
        "district_id": district,
        "agency_ids": [f"agency:{source.lower()}"],
        "location_ids": [district],
        "official_url": f"https://example.gov/{source}",
    }
    if named is None:
        value.pop("named_event_id")
    if authority is None:
        value.pop("authority")
    return value


class PublicEventFusionTests(unittest.TestCase):
    def test_three_official_sources_merge_into_one_confirmed_event(self):
        events = fusion.fuse_documents([document("CITY"), document("POLICE"), document("TRAFFIC")])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["fusion_status"], "CONFIRMED")
        self.assertEqual(events[0]["independent_source_count"], 3)
        self.assertEqual(len(events[0]["linked_document_versions"]), 3)

    def test_same_named_event_on_different_dates_does_not_merge(self):
        events = fusion.fuse_documents([document("CITY", day="2026-09-20"), document("CITY2", day="2026-10-18")])
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event["public_event_id"] for event in events}), 2)

    def test_same_identity_with_district_conflict_stays_conflict(self):
        event = fusion.fuse_documents([document("POLICE"), document("TRAFFIC", district="location:tc-fengyuan")])[0]
        self.assertEqual(event["fusion_status"], "CONFLICT")
        self.assertIn("district_id", event["conflict_fields"])
        self.assertIsNone(event["district_id"])

    def test_direct_and_dashboard_paths_do_not_add_independent_evidence(self):
        events = fusion.fuse_documents([
            document("DIRECT", independent="official:cwa:alert-1"),
            document("DASHBOARD", independent="official:cwa:alert-1"),
        ])
        self.assertEqual(events[0]["independent_source_count"], 1)
        self.assertEqual(events[0]["fusion_status"], "CANDIDATE")

    def test_equivalent_timezone_values_do_not_conflict(self):
        left = document("CITY")
        right = document("POLICE")
        right["event_start_at"] = "2026-09-20T10:00:00Z"
        right["event_end_at"] = "2026-09-20T14:00:00Z"
        event = fusion.fuse_documents([left, right])[0]
        self.assertEqual(event["fusion_status"], "CONFIRMED")

    def test_different_fact_scopes_do_not_create_fake_time_conflict(self):
        activity = document("CITY")
        traffic = document("POLICE", start="16:00")
        traffic["fact_scope"] = "traffic_control"
        event = fusion.fuse_documents([activity, traffic])[0]
        self.assertEqual(event["fusion_status"], "CONFIRMED")
        self.assertEqual(event["conflict_fields"], [])
        self.assertIsNone(event["start_at"])

    def test_document_version_update_preserves_public_event_identity(self):
        before = fusion.fuse_documents([document("POLICE"), document("TRAFFIC")])[0]
        after = fusion.fuse_documents([document("POLICE", version="v2"), document("TRAFFIC")])[0]
        self.assertEqual(before["public_event_id"], after["public_event_id"])
        self.assertNotEqual(before["linked_document_versions"], after["linked_document_versions"])

    def test_missing_named_event_identity_never_auto_merges(self):
        events = fusion.fuse_documents([document("POLICE", named=None), document("TRAFFIC", named=None)])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event["fusion_status"] == "CANDIDATE" for event in events))

    def test_non_official_or_missing_authority_cannot_enter_fusion(self):
        with self.assertRaisesRegex(ValueError, "affirmatively official"):
            fusion.fuse_documents([document("MEDIA", authority="media")])
        with self.assertRaisesRegex(ValueError, "affirmatively official"):
            fusion.fuse_documents([document("UNKNOWN", authority=None)])

    def test_missing_or_insecure_official_url_is_rejected(self):
        missing = document("POLICE")
        missing.pop("official_url")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            fusion.fuse_documents([missing])
        insecure = document("POLICE")
        insecure["official_url"] = "http://example.gov/POLICE"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            fusion.fuse_documents([insecure])

    def test_partial_snapshot_carries_forward_last_known_good_event(self):
        previous = fusion.fuse_documents([document("CITY"), document("POLICE"), document("TRAFFIC")])
        current = fusion.reconcile_public_events(previous, [document("CITY")], snapshot_complete=False)
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["public_event_id"], previous[0]["public_event_id"])
        self.assertTrue(current[0]["lkg"])
        self.assertEqual(current[0]["source_state"], "PARTIAL_LKG")
        self.assertEqual(len(current[0]["linked_document_versions"]), 3)

    def test_background_requires_confirmed_exact_geography_and_period(self):
        event = fusion.fuse_documents([document("CITY"), document("POLICE")])[0]
        record = {
            "dataset_id": "CTX-POP",
            "period": "2026-08",
            "value": 230000,
            "unit": "persons",
            "district_id": "location:tc-xitun",
            "source_url": "https://data.gov.tw/dataset/example",
        }
        enriched = fusion.attach_background(event, record)
        self.assertEqual(enriched["background"][0]["role"], "BACKGROUND_ONLY")
        with self.assertRaisesRegex(ValueError, "geography"):
            fusion.attach_background(event, dict(record, district_id="location:tc-fengyuan"))
        candidate = fusion.fuse_documents([document("CITY")])[0]
        with self.assertRaisesRegex(ValueError, "CONFIRMED"):
            fusion.attach_background(candidate, record)

    def test_manual_confirm_merge_and_split_keep_history(self):
        candidate = fusion.fuse_documents([document("CITY")])[0]
        confirmed = fusion.confirm_candidate(candidate, operator="operator-1", decided_at="2026-09-21T10:00:00+08:00")
        self.assertEqual(confirmed["fusion_status"], "CONFIRMED")
        self.assertEqual(confirmed["manual_history"][0]["action"], "CONFIRM")

        left = fusion.fuse_documents([document("POLICE")])[0]
        right = fusion.fuse_documents([document("TRAFFIC", named="event:other")])[0]
        merged = fusion.merge_public_events([left, right], operator="operator-1", decided_at="2026-09-21T10:01:00+08:00")
        merged_ids = merged["public_event"]["merged_from_public_event_ids"]
        self.assertEqual(set(merged_ids), {left["public_event_id"], right["public_event_id"]})
        self.assertEqual(set(merged["retired_public_event_ids"]), set(merged_ids[1:]))
        self.assertEqual(len(merged["retired_events"]), 1)

        split = fusion.split_public_event(
            confirmed,
            [[confirmed["linked_document_versions"][0]["document_version_id"]], []],
            operator="operator-1",
            decided_at="2026-09-21T10:02:00+08:00",
        ) if len(confirmed["linked_document_versions"]) > 1 else None
        self.assertIsNone(split)

        multi = fusion.fuse_documents([document("CITY"), document("POLICE")])[0]
        groups = [[multi["linked_document_versions"][0]["document_version_id"]], [multi["linked_document_versions"][1]["document_version_id"]]]
        split = fusion.split_public_event(multi, groups, operator="operator-1", decided_at="2026-09-21T10:03:00+08:00")
        self.assertEqual(split["original"]["fusion_status"], "SPLIT_REQUIRED")
        self.assertEqual(len(split["events"]), 2)
        self.assertEqual(split["events"][0]["split_from_public_event_id"], multi["public_event_id"])


if __name__ == "__main__":
    unittest.main()
