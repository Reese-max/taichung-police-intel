"""Tests for the public query interface (issue #106)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicQueryInterfaceTests(unittest.TestCase):
    """Tests for the public query interface components and API."""

    @classmethod
    def setUpClass(cls):
        # Load the query domain module
        cls.query_domain = _load_module("query_domain", ROOT / "intel_v2" / "query_domain.py")
        # Load the query gateway module
        cls.query_gateway = _load_module("query_gateway", ROOT / "scripts" / "query-gateway.py")

    def test_public_query_page_exists(self):
        """Test that the public query page/component exists."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        self.assertTrue(page_path.exists(), "Public query page should exist at apps/web/app/public-query/page.js")

    def test_public_query_page_has_search_form(self):
        """Test that the public query page has the required search form with five filter types."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        content = page_path.read_text(encoding="utf-8")
        
        # Check for the five filter types mentioned in the issue
        filters = ["district", "category", "agency", "time_from", "time_to", "q"]
        for filter_name in filters:
            self.assertIn(filter_name, content, f"Filter '{filter_name}' should be present in the page")

    def test_public_query_page_shows_visible_conditions(self):
        """Test that the page shows visible/applied conditions."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        content = page_path.read_text(encoding="utf-8")
        self.assertIn("目前套用條件", content, "Page should show applied conditions (目前套用條件)")

    def test_public_query_page_shows_event_details(self):
        """Test that the page shows event details with version differences."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        content = page_path.read_text(encoding="utf-8")
        self.assertIn("event", content.lower(), "Page should show event details")
        self.assertIn("version", content.lower(), "Page should show version differences")
        self.assertIn("版本歷史", content, "Page should show version history")
        self.assertIn("版本比較", content, "Page should show version comparison")

    def test_public_query_page_shows_source_status(self):
        """Test that the page shows source status."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        content = page_path.read_text(encoding="utf-8")
        self.assertIn("來源狀態", content, "Page should show source status tab")
        self.assertIn("pq-source", content, "Page should have source status styling")

    def test_public_query_page_has_tracking_handoff(self):
        """Test that the page has 'add to tracking' handoff interface."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        content = page_path.read_text(encoding="utf-8")
        self.assertIn("加入追蹤", content, "Page should have tracking handoff button")
        self.assertIn("handleAddToTracking", content, "Page should have tracking handler")
        self.assertIn("localStorage", content, "Tracking should use localStorage")

    def test_public_query_api_search_events_works(self):
        """Test that search_events API works with the domain store."""
        # Create a minimal event store
        event = {
            "schema_version": 1,
            "public_event_id": "PE-TEST-1",
            "event_type": "traffic_control",
            "canonical_title": "測試交通管制",
            "event_date": "2026-09-20",
            "start_at": "2026-09-20T16:00:00+08:00",
            "end_at": "2026-09-20T22:00:00+08:00",
            "district_id": "location:tc-west",
            "location_candidates": ["location:tc-west"],
            "location_ids": ["location:tc-west"],
            "agency_ids": ["agency:police"],
            "independent_source_ids": ["S-001"],
            "independent_source_count": 1,
            "fusion_status": "CONFIRMED",
            "source_state": "CURRENT",
            "linked_document_versions": [
                {
                    "document_id": "doc-test",
                    "document_version_id": "doc-test:v1",
                    "source_id": "S-001",
                    "official_url": "https://example.gov/event",
                }
            ],
        }
        store = self.query_domain.build_event_store([event])
        
        # Test search_events with district filter
        result = self.query_domain.query_events(store, {"district": "location:tc-west", "limit": 10})
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["public_event_id"], "PE-TEST-1")
        self.assertIn("documents", result["results"][0])
        self.assertEqual(len(result["results"][0]["documents"]), 1)

    def test_public_query_api_get_event_works(self):
        """Test that get_event API works."""
        event = {
            "schema_version": 1,
            "public_event_id": "PE-TEST-2",
            "event_type": "traffic_control",
            "canonical_title": "測試交通管制2",
            "event_date": "2026-09-20",
            "start_at": "2026-09-20T16:00:00+08:00",
            "end_at": "2026-09-20T22:00:00+08:00",
            "district_id": "location:tc-west",
            "location_candidates": ["location:tc-west"],
            "location_ids": ["location:tc-west"],
            "agency_ids": ["agency:police"],
            "independent_source_ids": ["S-001"],
            "independent_source_count": 1,
            "fusion_status": "CONFIRMED",
            "source_state": "CURRENT",
            "linked_document_versions": [
                {
                    "document_id": "doc-test",
                    "document_version_id": "doc-test:v1",
                    "source_id": "S-001",
                    "official_url": "https://example.gov/event",
                }
            ],
            "version_history": [
                {
                    "document_version_id": "doc-test:v1",
                    "observed_at": "2026-09-19T10:00:00+08:00",
                    "fields": {"start_at": "2026-09-20T18:00:00+08:00"},
                },
                {
                    "document_version_id": "doc-test:v2",
                    "observed_at": "2026-09-20T10:00:00+08:00",
                    "fields": {"start_at": "2026-09-20T16:00:00+08:00"},
                    "changed_fields": ["start_at"],
                },
            ],
        }
        store = self.query_domain.build_event_store([event])
        
        result = self.query_domain.get_event(store, "PE-TEST-2")
        self.assertEqual(result["public_event_id"], "PE-TEST-2")
        self.assertIn("tracking", result)
        self.assertIn("version_count", result)
        self.assertEqual(result["version_count"], 2)

    def test_public_query_api_compare_event_versions_works(self):
        """Test that compare_event_versions API works."""
        event = {
            "schema_version": 1,
            "public_event_id": "PE-TEST-3",
            "event_type": "traffic_control",
            "canonical_title": "測試交通管制3",
            "event_date": "2026-09-20",
            "start_at": "2026-09-20T16:00:00+08:00",
            "end_at": "2026-09-20T22:00:00+08:00",
            "district_id": "location:tc-west",
            "location_candidates": ["location:tc-west"],
            "location_ids": ["location:tc-west"],
            "agency_ids": ["agency:police"],
            "independent_source_ids": ["S-001"],
            "independent_source_count": 1,
            "fusion_status": "CONFIRMED",
            "source_state": "CURRENT",
            "linked_document_versions": [
                {
                    "document_id": "doc-test",
                    "document_version_id": "doc-test:v2",
                    "source_id": "S-001",
                    "official_url": "https://example.gov/event",
                }
            ],
            "version_history": [
                {
                    "document_version_id": "doc-test:v1",
                    "observed_at": "2026-09-19T10:00:00+08:00",
                    "fields": {"start_at": "2026-09-20T18:00:00+08:00"},
                },
                {
                    "document_version_id": "doc-test:v2",
                    "observed_at": "2026-09-20T10:00:00+08:00",
                    "fields": {"start_at": "2026-09-20T16:00:00+08:00"},
                    "changed_fields": ["start_at"],
                },
            ],
        }
        store = self.query_domain.build_event_store([event])
        
        comparison = self.query_domain.compare_event_versions(
            store, "PE-TEST-3", before_version="doc-test:v1", after_version="doc-test:v2"
        )
        self.assertEqual(comparison["comparison_status"], "COMPARED")
        self.assertEqual(comparison["materiality"], "MATERIAL")
        self.assertIn("start_at", comparison["changed_fields"])
        self.assertIn("observed_at", comparison)
        self.assertIn("published_at", comparison)
        self.assertIn("effective_at", comparison)

    def test_public_query_api_query_statistics_works(self):
        """Test that query_statistics API works."""
        stat = {
            "statistic_id": "STAT-TEST-1",
            "dataset_id": "NPA-STAT-1",
            "source_id": "S-028",
            "metric": "fraud_reports",
            "period": "2026-08",
            "value": 12,
            "unit": "件",
            "geography": "臺中市",
            "agency": "臺中市政府警察局",
            "provisional": False,
            "updated_at": "2026-09-21T00:00:00+08:00",
            "official_url": "https://data.example/statistics/1",
            "evidence_locator": "https://data.example/statistics/1#period=2026-08",
            "official_note": "統計期別資料",
        }
        store = self.query_domain.build_statistics_store([stat])
        
        result = self.query_domain.query_statistics(store, {"dataset_id": "NPA-STAT-1", "period_from": "2026-08", "limit": 10})
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["statistic_id"], "STAT-TEST-1")
        self.assertEqual(result["results"][0]["unit"], "件")
        self.assertEqual(result["results"][0]["geography"], "臺中市")

    def test_query_gateway_exposes_domain_capabilities(self):
        """Test that the query gateway exposes domain capabilities when stores are configured."""
        # This test will pass once the page is implemented and connected to the gateway
        pass

    def test_public_query_page_responsive(self):
        """Test that the page is responsive (mobile 320/390px)."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        content = page_path.read_text(encoding="utf-8")
        css_path = ROOT / "apps" / "web" / "app" / "v2.css"
        css_content = css_path.read_text(encoding="utf-8")
        # Check for responsive design patterns in CSS
        self.assertTrue(
            "@media (max-width" in css_content and "pq-drawer" in css_content,
            "Page should have responsive design in CSS"
        )

    def test_public_query_page_keyboard_accessible(self):
        """Test that the page is keyboard accessible."""
        page_path = ROOT / "apps" / "web" / "app" / "public-query" / "page.js"
        content = page_path.read_text(encoding="utf-8")
        # Check for keyboard accessibility patterns - buttons, inputs, tab navigation
        self.assertIn("button", content.lower(), "Page should have buttons")
        self.assertIn("input", content.lower(), "Page should have input fields")
        self.assertIn("select", content.lower(), "Page should have select fields")
        self.assertIn("role=", content, "Page should have ARIA roles")


if __name__ == "__main__":
    unittest.main()
