import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("query_domain", ROOT / "intel_v2" / "query_domain.py")
domain = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(domain)


def event(event_id="PE-1", *, status="CONFIRMED", district="location:west"):
    return {
        "schema_version": 1,
        "public_event_id": event_id,
        "event_type": "traffic_control",
        "canonical_title": "大型活動交通管制",
        "event_date": "2026-09-20",
        "start_at": "2026-09-20T16:00:00+08:00",
        "end_at": "2026-09-20T22:00:00+08:00",
        "district_id": district,
        "location_candidates": [district],
        "location_ids": [district],
        "agency_ids": ["agency:police"],
        "independent_source_ids": ["S-001", "S-032"],
        "independent_source_count": 2,
        "fusion_status": status,
        "source_state": "CURRENT",
        "tracked": True,
        "changed": True,
        "linked_document_versions": [
            {
                "document_id": "doc-police",
                "document_version_id": "doc-police:v2",
                "source_id": "S-001",
                "official_url": "https://police.example/event",
            },
            {
                "document_id": "doc-traffic",
                "document_version_id": "doc-traffic:v1",
                "source_id": "S-032",
                "official_url": "https://traffic.example/event",
            },
        ],
        "version_history": [
            {
                "document_version_id": "doc-police:v1",
                "observed_at": "2026-09-19T10:00:00+08:00",
                "fields": {"start_at": "2026-09-20T18:00:00+08:00"},
            },
            {
                "document_version_id": "doc-police:v2",
                "observed_at": "2026-09-20T10:00:00+08:00",
                "fields": {"start_at": "2026-09-20T16:00:00+08:00"},
                "changed_fields": ["start_at"],
            },
        ],
        "affected_handoff_claims": ["CLAIM-1"],
    }


def statistic(statistic_id, period, *, provisional=False):
    return {
        "statistic_id": statistic_id,
        "dataset_id": "NPA-STAT-1",
        "source_id": "S-028",
        "metric": "fraud_reports",
        "period": period,
        "value": 12,
        "unit": "件",
        "geography": "臺中市",
        "agency": "臺中市政府警察局",
        "provisional": provisional,
        "updated_at": "2026-09-21T00:00:00+08:00",
        "official_url": "https://data.example/statistics/1",
        "evidence_locator": "https://data.example/statistics/1#period=" + period,
        "official_note": "統計期別資料",
    }


class QueryDomainTests(unittest.TestCase):
    def test_event_store_is_hashed_and_event_query_is_deterministic(self):
        store = domain.build_event_store([event("PE-2", district="location:east"), event("PE-1")])
        self.assertEqual([row["public_event_id"] for row in store["public_events"]], ["PE-1", "PE-2"])
        first = domain.query_events(store, {"district": "location:west", "limit": 1})
        second = domain.query_events(store, {"district": "location:west", "limit": 1})
        self.assertEqual(first, second)
        self.assertEqual(first["results"][0]["public_event_id"], "PE-1")
        self.assertEqual(first["results"][0]["documents"][0]["official_url"], "https://police.example/event")

    def test_event_get_and_compare_keep_safe_version_projection(self):
        store = domain.build_event_store([event()])
        result = domain.get_event(store, "PE-1")
        self.assertEqual(result["tracking"]["tracked"], True)
        self.assertNotIn("version_history", result)
        comparison = domain.compare_event_versions(store, "PE-1", before_version="doc-police:v1", after_version="doc-police:v2")
        self.assertEqual(comparison["materiality"], "MATERIAL")
        self.assertEqual(comparison["changed_fields"], ["start_at"])
        self.assertNotIn("private_notes", comparison["after"])
        explicit_before = domain.compare_event_versions(store, "PE-1", before_version="doc-police:v1")
        self.assertEqual(explicit_before["after"]["document_version_id"], "doc-police:v2")

    def test_statistics_store_is_typed_and_period_bound(self):
        store = domain.build_statistics_store([statistic("STAT-2", "2026-09"), statistic("STAT-1", "2026-08", provisional=True)])
        result = domain.query_statistics(store, {"dataset_id": "NPA-STAT-1", "period_from": "2026-08", "period_to": "2026-08", "limit": 10})
        self.assertEqual([row["statistic_id"] for row in result["results"]], ["STAT-1"])
        self.assertEqual(result["results"][0]["unit"], "件")
        self.assertEqual(result["results"][0]["geography"], "臺中市")
        self.assertTrue(result["results"][0]["provisional"])

    def test_untrusted_store_fields_and_urls_fail_closed(self):
        invalid = event()
        invalid["linked_document_versions"] = [dict(invalid["linked_document_versions"][0], official_url="https://user:secret@example.invalid/event")]
        with self.assertRaisesRegex(ValueError, "credential-free HTTPS"):
            domain.build_event_store([invalid])
        invalid_stat = statistic("STAT-1", "2026-08")
        invalid_stat["value"] = {"private": "payload"}
        with self.assertRaisesRegex(ValueError, "finite number"):
            domain.build_statistics_store([invalid_stat])
        invalid_event = event()
        invalid_event["tracking_id"] = {"private": "payload"}
        with self.assertRaisesRegex(ValueError, "tracking_id"):
            domain.build_event_store([invalid_event])
        invalid_stat = statistic("STAT-2", "2026-09")
        invalid_stat["official_note"] = {"private": "payload"}
        with self.assertRaisesRegex(ValueError, "official_note"):
            domain.build_statistics_store([invalid_stat])


if __name__ == "__main__":
    unittest.main()
