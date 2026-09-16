import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "public-event-fusion.py"
spec = importlib.util.spec_from_file_location("public_event_fusion", SCRIPT)
pe = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(pe)


def doc(source, *, day="2026-09-20", district="location:tc-xitun", named="event:demo", version="v1", independent=None, authority="official"):
    return {
        "document_id": f"doc-{source}",
        "document_version_id": f"doc-{source}:{version}",
        "source_id": source,
        "independent_source_id": independent or source,
        "authority": authority,
        "title": f"示範活動 {source}",
        "event_type": "large_event",
        "named_event_id": named,
        "event_start_at": f"{day}T18:00:00+08:00",
        "event_end_at": f"{day}T22:00:00+08:00",
        "district_id": district,
        "agency_ids": [f"agency:{source.lower()}"],
        "location_ids": [district],
        "official_url": f"https://example.gov/{source}",
    }


class PublicEventFusionTests(unittest.TestCase):
    def test_three_official_sources_merge_into_one_confirmed_event(self):
        events = pe.fuse_documents([doc("CITY"), doc("POLICE"), doc("TRAFFIC")])
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["fusion_status"], "CONFIRMED")
        self.assertEqual(event["independent_source_count"], 3)
        self.assertEqual(len(event["linked_document_versions"]), 3)
        self.assertTrue(all(link["official_url"].startswith("https://") for link in event["linked_document_versions"]))

    def test_same_named_event_on_different_dates_does_not_merge(self):
        events = pe.fuse_documents([doc("CITY", day="2026-09-20"), doc("CITY2", day="2026-10-18")])
        self.assertEqual(len(events), 2)
        self.assertEqual(len({event["public_event_id"] for event in events}), 2)

    def test_same_identity_with_district_conflict_stays_conflict(self):
        events = pe.fuse_documents([doc("POLICE"), doc("TRAFFIC", district="location:tc-fengyuan")])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["fusion_status"], "CONFLICT")
        self.assertIn("district_id", events[0]["conflict_fields"])
        self.assertIsNone(events[0]["district_id"])

    def test_direct_and_dashboard_paths_of_same_original_source_do_not_add_evidence(self):
        direct = doc("CWA-DIRECT", independent="official:cwa:alert-1")
        dashboard = doc("DASHBOARD", independent="official:cwa:alert-1")
        events = pe.fuse_documents([direct, dashboard])
        self.assertEqual(events[0]["independent_source_count"], 1)
        self.assertEqual(events[0]["fusion_status"], "CANDIDATE")

    def test_document_version_update_preserves_public_event_identity(self):
        before = pe.fuse_documents([doc("POLICE", version="v1"), doc("TRAFFIC", version="v1")])[0]
        after = pe.fuse_documents([doc("POLICE", version="v2"), doc("TRAFFIC", version="v1")])[0]
        self.assertEqual(before["public_event_id"], after["public_event_id"])
        self.assertNotEqual(before["linked_document_versions"], after["linked_document_versions"])

    def test_missing_named_event_identity_never_auto_merges(self):
        left = doc("POLICE", named=None)
        right = doc("TRAFFIC", named=None)
        left.pop("named_event_id")
        right.pop("named_event_id")
        events = pe.fuse_documents([left, right])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event["fusion_status"] == "CANDIDATE" for event in events))

    def test_non_official_or_missing_authority_cannot_enter_canonical_event_fusion(self):
        with self.assertRaisesRegex(ValueError, "affirmatively official"):
            pe.fuse_documents([doc("MEDIA", authority="media")])
        missing = doc("UNKNOWN")
        missing.pop("authority")
        with self.assertRaisesRegex(ValueError, "affirmatively official"):
            pe.fuse_documents([missing])

    def test_missing_or_insecure_official_url_is_rejected(self):
        missing = doc("POLICE")
        missing.pop("official_url")
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            pe.fuse_documents([missing])
        insecure = doc("POLICE")
        insecure["official_url"] = "http://example.gov/POLICE"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            pe.fuse_documents([insecure])

    def test_input_order_does_not_change_public_event_id_or_link_order(self):
        docs = [doc("CITY"), doc("POLICE"), doc("TRAFFIC")]
        first = pe.fuse_documents(docs)[0]
        second = pe.fuse_documents(list(reversed(docs)))[0]
        self.assertEqual(first["public_event_id"], second["public_event_id"])
        self.assertEqual(first["linked_document_versions"], second["linked_document_versions"])

    def test_background_requires_exact_geography_period_and_source(self):
        event = pe.fuse_documents([doc("CITY"), doc("POLICE")])[0]
        record = {
            "dataset_id": "CTX-POP",
            "period": "2026-08",
            "value": 230000,
            "unit": "persons",
            "district_id": "location:tc-xitun",
            "source_url": "https://data.gov.tw/dataset/example",
        }
        enriched = pe.attach_background(event, record)
        self.assertEqual(enriched["background"][0]["role"], "BACKGROUND_ONLY")
        self.assertEqual(enriched["background"][0]["period"], "2026-08")
        wrong = dict(record, district_id="location:tc-fengyuan")
        with self.assertRaisesRegex(ValueError, "geography"):
            pe.attach_background(event, wrong)
        missing_url = dict(record)
        missing_url.pop("source_url")
        with self.assertRaisesRegex(ValueError, "source_url"):
            pe.attach_background(event, missing_url)

    def test_background_rejects_candidate_and_conflict_events(self):
        record = {
            "dataset_id": "CTX-POP",
            "period": "2026-08",
            "value": 230000,
            "unit": "persons",
            "district_id": "location:tc-xitun",
            "source_url": "https://data.gov.tw/dataset/example",
        }
        candidate = pe.fuse_documents([doc("CITY")])[0]
        self.assertEqual(candidate["fusion_status"], "CANDIDATE")
        with self.assertRaisesRegex(ValueError, "CONFIRMED"):
            pe.attach_background(candidate, record)

        conflict = pe.fuse_documents([doc("POLICE"), doc("TRAFFIC", district="location:tc-fengyuan")])[0]
        self.assertEqual(conflict["fusion_status"], "CONFLICT")
        with self.assertRaisesRegex(ValueError, "CONFIRMED"):
            pe.attach_background(conflict, record)


if __name__ == "__main__":
    unittest.main()
