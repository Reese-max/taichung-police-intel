from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
gateway_spec = importlib.util.spec_from_file_location("query_gateway_domain_test", ROOT / "scripts" / "query-gateway.py")
gateway_module = importlib.util.module_from_spec(gateway_spec)
assert gateway_spec and gateway_spec.loader
gateway_spec.loader.exec_module(gateway_module)


def event():
    return {
        "schema_version": 1,
        "public_event_id": "PE-DOMAIN-1",
        "event_type": "traffic_control",
        "canonical_title": "大型活動交通管制",
        "event_date": "2026-09-20",
        "start_at": "2026-09-20T16:00:00+08:00",
        "end_at": "2026-09-20T22:00:00+08:00",
        "district_id": "location:west",
        "location_candidates": ["location:west"],
        "location_ids": ["location:west"],
        "agency_ids": ["agency:police"],
        "independent_source_ids": ["S-001", "S-032"],
        "fusion_status": "CONFIRMED",
        "source_state": "CURRENT",
        "tracked": True,
        "changed": True,
        "linked_document_versions": [{
            "document_id": "doc-1",
            "document_version_id": "doc-1:v1",
            "source_id": "S-001",
            "official_url": "https://police.example/event",
        }],
    }


def statistic():
    return {
        "statistic_id": "STAT-DOMAIN-1",
        "dataset_id": "NPA-STAT-1",
        "source_id": "S-028",
        "metric": "fraud_reports",
        "period": "2026-08",
        "value": 12,
        "unit": "件",
        "geography": "臺中市",
        "provisional": False,
        "updated_at": "2026-09-21T00:00:00+08:00",
        "official_url": "https://data.example/statistics/1",
    }


class QueryGatewayDomainTests(unittest.TestCase):
    def setUp(self):
        snapshot = gateway_module.load_snapshot()
        snapshot["event_store"] = gateway_module.query_domain.build_event_store([event()])
        snapshot["statistics_store"] = gateway_module.query_domain.build_statistics_store([statistic()])
        self.gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )

    def test_domain_capabilities_are_advertised_only_when_stores_are_bound(self):
        capabilities = self.gateway.capabilities()
        self.assertIn("search_events", capabilities["capabilities"])
        self.assertIn("query_statistics", capabilities["capabilities"])
        names = [tool["name"] for tool in self.gateway.mcp_tools()]
        self.assertIn("get_event", names)
        self.assertIn("compare_event_versions", names)
        self.assertIn("query_statistics", names)

    def test_web_and_mcp_share_domain_event_projection(self):
        query = self.gateway.execute("search_events", {"district": "location:west", "limit": 10})
        mcp = gateway_module.dispatch_mcp(self.gateway, {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_events", "arguments": {"district": "location:west", "limit": 10}},
        })
        self.assertEqual(query["event_ids"], mcp["result"]["structuredContent"]["event_ids"])
        self.assertEqual(query["events"], mcp["result"]["structuredContent"]["events"])
        self.assertEqual(query["domain_query_generation_id"], mcp["result"]["structuredContent"]["domain_query_generation_id"])

    def test_get_event_and_statistics_keep_receipts_and_typed_fields(self):
        event_result = self.gateway.execute("get_event", {"event_id": "PE-DOMAIN-1"})
        self.assertEqual(event_result["event"]["public_event_id"], "PE-DOMAIN-1")
        self.assertEqual(event_result["event"]["documents"][0]["source_id"], "S-001")
        self.assertEqual(event_result["result_type"], "public_event")
        stats = self.gateway.execute("query_statistics", {"dataset_id": "NPA-STAT-1", "period_from": "2026-08", "period_to": "2026-08"})
        self.assertEqual(stats["statistics"][0]["unit"], "件")
        self.assertEqual(stats["statistics"][0]["geography"], "臺中市")
        self.assertFalse(stats["statistics"][0]["provisional"])
        self.assertEqual(stats["result_type"], "statistics")

    def test_missing_domain_store_remains_unavailable(self):
        gateway = gateway_module.QueryGateway(clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
        with self.assertRaisesRegex(gateway_module.GatewayError, "canonical domain store"):
            gateway.execute("get_event", {"event_id": "PE-DOMAIN-1"})


if __name__ == "__main__":
    unittest.main()
