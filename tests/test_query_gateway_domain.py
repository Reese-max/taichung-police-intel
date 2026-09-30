from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

from intel_v2.entity_binding import registry_runtime


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
        self.assertEqual(query["freshness"], "UNKNOWN")
        self.assertEqual(query["query_coverage"]["domain_store_coverage_status"], "UNVERIFIED")
        self.assertFalse(query["query_coverage"]["can_state_bounded_no_match"])
        self.assertIn("DOMAIN_STORE_SOURCE_HEALTH_UNBOUND", {gap["reason"] for gap in query["source_gaps"]})

    def test_registry_bound_aliases_share_ids_and_receipt_in_web_and_mcp(self):
        module = registry_runtime()
        registry = module.load_registry()
        receipt = module.registry_receipt(registry)
        row = event()
        row.update(district_id="location:tc-xitun", location_candidates=[], location_ids=["location:tc-xitun"],
                   agency_ids=["agency:tc-police"], entity_registry=receipt)
        snapshot = gateway_module.load_snapshot()
        snapshot["event_store"] = gateway_module.query_domain.build_event_store([row])
        gateway = gateway_module.QueryGateway(snapshot=snapshot, clock=self.gateway.clock)
        arguments = {"district": "台中市西屯區", "agency": "中市警"}
        web = gateway.execute("search_events", arguments)
        mcp = gateway_module.dispatch_mcp(gateway, {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "search_events", "arguments": arguments},
        })["result"]["structuredContent"]
        self.assertEqual(web["event_ids"], ["PE-DOMAIN-1"])
        self.assertEqual(web["event_ids"], mcp["event_ids"])
        self.assertEqual(web["receipt"]["entity_registry"], mcp["receipt"]["entity_registry"])
        self.assertEqual(web["receipt"]["entity_registry"], receipt)
        self.assertEqual(web["entity_resolution"]["agency"]["entity_id"], "agency:tc-police")
        self.assertEqual(web["entity_resolution"]["district"]["entity_id"], "location:tc-xitun")
        with self.assertRaisesRegex(gateway_module.GatewayError, "unique confirmed"):
            gateway.execute("search_events", {"district": "不存在的路"})

    def test_alias_on_unbound_store_and_stale_binding_fail_closed(self):
        with self.assertRaisesRegex(gateway_module.GatewayError, "registry-bound"):
            self.gateway.execute("search_events", {"agency": "中市警"})
        row = event()
        row["entity_registry"] = {"registry_version": 999, "registry_hash": "a" * 64}
        snapshot = gateway_module.load_snapshot()
        snapshot["event_store"] = gateway_module.query_domain.build_event_store([row])
        stale = gateway_module.QueryGateway(snapshot=snapshot, clock=self.gateway.clock)
        with self.assertRaisesRegex(gateway_module.GatewayError, "differs"):
            stale.execute("search_events", {"agency": "中市警"})
        module = registry_runtime()
        row["entity_registry"] = module.registry_receipt(module.load_registry())
        snapshot["event_store"] = gateway_module.query_domain.build_event_store([row])
        invalid = gateway_module.QueryGateway(snapshot=snapshot, clock=self.gateway.clock)
        with self.assertRaisesRegex(gateway_module.GatewayError, "inactive registry entity IDs"):
            invalid.execute("search_events", {"agency": "中市警"})

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

    def test_date_only_event_bounds_resolve_in_requested_timezone(self):
        query = self.gateway.execute("search_events", {
            "time_from": "2026-09-20",
            "time_to": "2026-09-20",
            "time_zone": "Asia/Taipei",
            "limit": 10,
        })
        self.assertEqual(query["event_ids"], ["PE-DOMAIN-1"])
        self.assertEqual(query["time_resolution"]["time_zone"], "Asia/Taipei")
        self.assertEqual(query["time_resolution"]["resolved"]["time_from"], "2026-09-19T16:00:00+00:00")
        self.assertEqual(query["time_resolution"]["resolved"]["time_to"], "2026-09-20T15:59:59.999999+00:00")
        self.assertEqual(query["time_resolution"]["server_clock"], "2026-09-21T09:00:00+00:00")

    def test_invalid_event_timezone_fails_closed(self):
        with self.assertRaisesRegex(gateway_module.GatewayError, "IANA timezone"):
            self.gateway.execute("search_events", {"time_from": "2026-09-20", "time_zone": "Mars/Phobos"})

    def test_missing_domain_store_remains_unavailable(self):
        gateway = gateway_module.QueryGateway(clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
        with self.assertRaisesRegex(gateway_module.GatewayError, "canonical domain store"):
            gateway.execute("get_event", {"event_id": "PE-DOMAIN-1"})


if __name__ == "__main__":
    unittest.main()
