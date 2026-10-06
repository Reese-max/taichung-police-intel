from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import unittest
import tempfile
from governed_policy_fixture import make_governed_policy_fixture, load_fixture_module, bind_checked_in_publication

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
            "official_url": "https://www.police.taichung.gov.tw/fictional/event",
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
        "official_url": "https://data.gov.tw/fictional/statistics/1",
    }


class QueryGatewayDomainTests(unittest.TestCase):
    def setUp(self):
        global gateway_module
        directory = tempfile.TemporaryDirectory(prefix="fictional-domain-permissions-")
        self.addCleanup(directory.cleanup)
        fixture = bind_checked_in_publication(make_governed_policy_fixture(
            directory.name, domain_source_ids=("S-001", "S-028", "S-032")))
        gateway_module = load_fixture_module(fixture, "fictional_domain_permissions", "scripts/query-gateway.py")
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

    def test_discovery_event_is_counted_and_not_promoted_to_verified(self):
        candidate = event()
        candidate["public_event_id"] = "PE-DOMAIN-CANDIDATE"
        candidate["fusion_status"] = "CANDIDATE"
        second = event()
        second["public_event_id"] = "PE-DOMAIN-CANDIDATE-2"
        second["fusion_status"] = "CANDIDATE"
        snapshot = gateway_module.load_snapshot()
        snapshot["event_store"] = gateway_module.query_domain.build_event_store([event(), candidate, second])
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )
        result = gateway.execute("search_events", {"limit": 10})
        tiers = {row["public_event_id"]: row["trust_tier"] for row in result["events"]}
        self.assertEqual(tiers["PE-DOMAIN-CANDIDATE"], "DISCOVERY_UNVERIFIED")
        self.assertEqual(result["discovery_unverified_count"], 2)

        # The count covers the whole matched set, not just the returned page.
        paged = gateway.execute("search_events", {"limit": 1})
        self.assertEqual(paged["result_count"], 1)
        self.assertEqual(paged["discovery_unverified_count"], 2)

    def test_single_event_envelopes_never_report_a_non_verified_event_as_clean(self):
        snapshot = gateway_module.load_snapshot()
        conflict = event()
        conflict["public_event_id"] = "PE-DOMAIN-CONFLICT"
        conflict["fusion_status"] = "CONFLICT"
        stale = event()
        stale["public_event_id"] = "PE-DOMAIN-LKG"
        stale["fusion_status"] = "PARTIAL_LKG"
        snapshot["event_store"] = gateway_module.query_domain.build_event_store([conflict, stale])
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )
        for event_id, tier, counter in (
            ("PE-DOMAIN-CONFLICT", "CONFLICT", "conflict_count"),
            ("PE-DOMAIN-LKG", "STALE", "stale_count"),
        ):
            result = gateway.execute("get_event", {"event_id": event_id})
            self.assertEqual(result["event"]["trust_tier"], tier)
            self.assertEqual(result[counter], 1, event_id)
            self.assertEqual(result["discovery_unverified_count"], 0, event_id)
            self.assertEqual(result["trust_tier_counts"][tier], 1, event_id)

        comparison = gateway.execute("compare_event_versions", {"event_id": "PE-DOMAIN-LKG"})
        self.assertEqual(comparison["comparison"]["trust_tier"], "STALE")
        self.assertEqual(comparison["stale_count"], 1)

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

    def test_unpromoted_secondary_identity_and_unapproved_document_origin_are_refused(self):
        for mutation in (lambda row: row["independent_source_ids"].append("S-034"),
                         lambda row: row["linked_document_versions"][0].update(official_url="https://unapproved.example.test/fictional")):
            with self.subTest(mutation=mutation):
                candidate = event()
                mutation(candidate)
                snapshot = gateway_module.load_snapshot()
                snapshot["event_store"] = gateway_module.query_domain.build_event_store([candidate])
                with self.assertRaises(gateway_module.GatewayError) as denied:
                    gateway_module.QueryGateway(snapshot).execute("search_events", {})
                self.assertEqual(denied.exception.code, "SOURCE_GOVERNANCE_UNVERIFIED")

    def test_version_comparison_cannot_publish_an_unreviewed_nested_field(self):
        candidate = event()
        candidate["version_history"] = [
            {"version_id": "v1", "observed_at": "2026-09-20T08:00:00+08:00", "fields": {"private_case": "PRIVATE_SYNTHETIC"}},
            {"version_id": "v2", "observed_at": "2026-09-21T08:00:00+08:00", "fields": {"private_case": "PRIVATE_SYNTHETIC_CHANGED"}},
        ]
        snapshot = gateway_module.load_snapshot()
        snapshot["event_store"] = gateway_module.query_domain.build_event_store([candidate])
        result = gateway_module.dispatch_mcp(gateway_module.QueryGateway(snapshot), {
            "jsonrpc": "2.0", "id": 49, "method": "tools/call",
            "params": {"name": "compare_event_versions", "arguments": {"event_id": candidate["public_event_id"]}},
        })["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["error"]["code"], "SOURCE_GOVERNANCE_UNVERIFIED")
        self.assertNotIn("PRIVATE_SYNTHETIC", str(result))


def chat_event(event_id="PE-CHAT-1", district="location:tc-xitun", *, fusion="CONFIRMED",
               start="2026-09-26T10:00:00+08:00", end="2026-09-26T18:00:00+08:00"):
    row = {
        "schema_version": 1,
        "public_event_id": event_id,
        "event_type": "traffic_control",
        "canonical_title": f"大型活動交通管制 {event_id}",
        "event_date": "2026-09-26",
        "start_at": start,
        "end_at": end,
        "district_id": district,
        "location_candidates": [district],
        "location_ids": [district],
        "agency_ids": ["agency:tc-police"],
        "independent_source_ids": ["S-001", "S-032"],
        "fusion_status": fusion,
        "source_state": "CURRENT",
        "tracked": True,
        "changed": True,
        "linked_document_versions": [{
            "document_id": f"doc-{event_id}",
            "document_version_id": f"{event_id}:v2",
            "source_id": "S-001",
            "official_url": f"https://www.police.taichung.gov.tw/fictional/events/{event_id}",
            "evidence_id": f"EV-{event_id}",
        }],
    }
    if event_id == "PE-CHAT-1":
        row["version_history"] = [
            {
                "document_version_id": "PE-CHAT-1:v1",
                "observed_at": "2026-09-24T09:00:00+08:00",
                "published_at": "2026-09-24T08:00:00+08:00",
                "effective_at": "2026-09-26T17:00:00+08:00",
                "fields": {"control_start": "2026-09-26T17:00:00+08:00", "road": "測試路"},
            },
            {
                "document_version_id": "PE-CHAT-1:v2",
                "observed_at": "2026-09-25T09:00:00+08:00",
                "published_at": "2026-09-25T08:00:00+08:00",
                "effective_at": "2026-09-26T16:00:00+08:00",
                "fields": {"control_start": "2026-09-26T16:00:00+08:00", "road": "測試路"},
                "changed_fields": ["control_start"],
            },
        ]
    return row


def chat_statistics():
    rows = []
    for period, value, provisional in (("2026-07", 10, False), ("2026-08", 12, False), ("2026-09", 9, True)):
        rows.append({
            "statistic_id": f"STAT-FRAUD-{period}",
            "dataset_id": "NPA-FRAUD",
            "source_id": "S-028",
            "metric": "fraud_reports",
            "period": period,
            "value": value,
            "unit": "件",
            "geography": "臺中市",
            "provisional": provisional,
            "updated_at": "2026-09-21T00:00:00+08:00",
            "official_url": f"https://data.gov.tw/fictional/statistics/fraud-{period}",
        })
    rows.append({
        "statistic_id": "STAT-FRAUD-NATIONAL",
        "dataset_id": "NPA-FRAUD",
        "source_id": "S-028",
        "metric": "fraud_reports",
        "period": "2026-09",
        "value": 1000,
        "unit": "件",
        "geography": "全國",
        "provisional": True,
        "updated_at": "2026-09-21T00:00:00+08:00",
        "official_url": "https://data.gov.tw/fictional/statistics/fraud-national",
    })
    return rows


class QueryGatewayConversationTests(unittest.TestCase):
    """Issue #29 — conversational query surface over the shared Query Gateway."""

    def setUp(self):
        global gateway_module
        directory = tempfile.TemporaryDirectory(prefix="fictional-chat-permissions-")
        self.addCleanup(directory.cleanup)
        fixture = bind_checked_in_publication(make_governed_policy_fixture(
            directory.name, domain_source_ids=("S-001", "S-028", "S-032")))
        gateway_module = load_fixture_module(fixture, "fictional_chat_permissions", "scripts/query-gateway.py")


    def build(self, events=None, statistics=None):
        snapshot = gateway_module.load_snapshot()
        snapshot["event_store"] = gateway_module.query_domain.build_event_store(
            events if events is not None else [chat_event(), chat_event("PE-CHAT-2", "location:tc-nantun")])
        snapshot["statistics_store"] = gateway_module.query_domain.build_statistics_store(
            statistics if statistics is not None else chat_statistics())
        return gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )

    def assert_chat_error(self, gateway, arguments, code):
        with self.assertRaises(gateway_module.GatewayError) as raised:
            gateway.execute("chat_turn", arguments)
        self.assertEqual(raised.exception.code, code)

    def test_chat_turn_requires_text_or_quick_action(self):
        gateway = self.build()
        self.assert_chat_error(gateway, {}, "INVALID_ARGUMENTS")
        self.assert_chat_error(gateway, {"text": "  "}, "INVALID_ARGUMENTS")
        self.assert_chat_error(gateway, {"text": "今天有什麼事件", "injected": True}, "INVALID_ARGUMENTS")

    def test_unreviewed_domain_chat_cannot_bypass_formal_admission(self):
        spec = importlib.util.spec_from_file_location("unreviewed_chat_control", ROOT / "scripts/query-gateway.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        snapshot = module.load_snapshot()
        snapshot["event_store"] = module.query_domain.build_event_store([chat_event()])
        gateway = module.QueryGateway(snapshot, clock=lambda: datetime(2026, 9, 21, 9, tzinfo=timezone.utc))
        with self.assertRaises(module.GatewayError) as denied:
            gateway.execute("chat_turn", {"text": "這週末有哪些活動？"})
        self.assertEqual(denied.exception.code, "RIGHTS_BLOCKED")

    def test_multi_turn_narrow_detail_compare_evidence_keeps_event_identity(self):
        gateway = self.build()
        turn = gateway.execute("chat_turn", {"text": "這週末臺中有哪些活動？"})
        chat = turn["chat"]
        self.assertEqual(chat["schema_version"], 1)
        self.assertEqual(chat["intent"], "search_events")
        request = chat["resolved_request"]
        self.assertEqual(request["schema_version"], 1)
        self.assertEqual(request["tool"], "search_events")
        self.assertEqual(request["arguments"]["time_from"], "2026-09-26")
        self.assertEqual(request["arguments"]["time_to"], "2026-09-27")
        self.assertEqual(request["arguments"]["time_zone"], "Asia/Taipei")
        self.assertEqual(sorted(chat["context"]["last_result_event_ids"]), ["PE-CHAT-1", "PE-CHAT-2"])
        self.assertEqual(chat["context"]["selected_time_window"]["time_from"], "2026-09-26")

        narrowed = gateway.execute("chat_turn", {"text": "只看西屯", "context": chat["context"]})
        narrowed_chat = narrowed["chat"]
        narrowed_args = narrowed_chat["resolved_request"]["arguments"]
        self.assertEqual(narrowed_chat["intent"], "search_events")
        self.assertEqual(narrowed_args["district"], "location:tc-xitun")
        self.assertEqual(narrowed_args["time_from"], "2026-09-26")
        self.assertEqual(narrowed_args["time_to"], "2026-09-27")
        self.assertEqual(narrowed_chat["context"]["last_result_event_ids"], ["PE-CHAT-1"])
        self.assertEqual(narrowed_chat["context"]["selected_region"], "location:tc-xitun")

        detail = gateway.execute("chat_turn", {"text": "第一件有交通管制嗎？", "context": narrowed_chat["context"]})
        detail_chat = detail["chat"]
        self.assertEqual(detail_chat["intent"], "get_event")
        self.assertEqual(detail_chat["resolved_request"]["arguments"], {"event_id": "PE-CHAT-1"})
        self.assertEqual(detail_chat["context"]["selected_event_id"], "PE-CHAT-1")

        compared = gateway.execute("chat_turn", {"text": "跟昨天比有沒有改？", "context": detail_chat["context"]})
        compared_chat = compared["chat"]
        self.assertEqual(compared_chat["intent"], "compare_event_versions")
        self.assertEqual(compared_chat["resolved_request"]["arguments"]["event_id"], "PE-CHAT-1")
        self.assertEqual(compared_chat["comparison"]["changed_fields"], ["control_start"])
        self.assertEqual(compared_chat["comparison"]["source_document_versions"], ["PE-CHAT-1:v1", "PE-CHAT-1:v2"])
        self.assertEqual(compared_chat["comparison"]["observed_at"]["before"], "2026-09-24T09:00:00+08:00")
        self.assertEqual(compared_chat["comparison"]["observed_at"]["after"], "2026-09-25T09:00:00+08:00")

        evidence = gateway.execute("chat_turn", {"text": "把官方證據給我", "context": compared_chat["context"]})
        evidence_chat = evidence["chat"]
        self.assertEqual(evidence_chat["resolved_request"]["tool"], "get_event")
        self.assertEqual(evidence_chat["resolved_request"]["arguments"]["event_id"], "PE-CHAT-1")
        self.assertTrue(evidence_chat["evidence_links"])
        link = evidence_chat["evidence_links"][0]
        self.assertTrue(link["official_url"].startswith("https://"))
        self.assertEqual(link["evidence_id"], "EV-PE-CHAT-1")
        self.assertEqual(link["event_id"], "PE-CHAT-1")

    def test_chat_and_mcp_share_one_gateway_and_resolution(self):
        gateway = self.build()
        tool_names = [tool["name"] for tool in gateway.mcp_tools()]
        self.assertIn("chat_turn", tool_names)
        web = gateway.execute("chat_turn", {"text": "只看西屯"})
        mcp = gateway_module.dispatch_mcp(gateway, {
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "chat_turn", "arguments": {"text": "只看西屯"}},
        })
        mcp_chat = mcp["result"]["structuredContent"]["chat"]
        self.assertEqual(
            web["chat"]["resolved_request"]["arguments"],
            mcp_chat["resolved_request"]["arguments"],
        )
        self.assertEqual(web["chat"]["event_ids"], mcp_chat["event_ids"])
        self.assertEqual(web["publication_hash"], mcp["result"]["structuredContent"]["publication_hash"])

    def test_source_failure_is_not_rewritten_as_no_events(self):
        gateway = self.build()
        chat = gateway.execute("chat_turn", {"text": "今天有沒有交通事件？"})["chat"]
        self.assertEqual(chat["resolved_request"]["tool"], "search_events")
        self.assertEqual(chat["no_match"]["status"], "UNBOUNDED_NO_MATCH")
        self.assertTrue(chat["gaps"])
        self.assertNotIn("沒有事件", "".join(chat["lines"]))
        health = gateway.execute("chat_turn", {"text": "為什麼今天沒有資料？"})["chat"]
        self.assertEqual(health["resolved_request"]["tool"], "get_source_health")
        self.assertTrue(health["sources"])

    def test_discovery_candidates_stay_out_of_verified_section(self):
        gateway = self.build(events=[
            chat_event(),
            chat_event("PE-CHAT-3", "location:tc-xitun", fusion="CANDIDATE"),
        ])
        chat = gateway.execute("chat_turn", {"text": "這週末有哪些活動？"})["chat"]
        self.assertEqual([row["public_event_id"] for row in chat["verified"]], ["PE-CHAT-1"])
        self.assertEqual([row["public_event_id"] for row in chat["unverified"]], ["PE-CHAT-3"])
        self.assertEqual(chat["trust"]["discovery_unverified_count"], 1)

    def test_statistics_returns_period_unit_geography_and_provisional(self):
        gateway = self.build()
        chat = gateway.execute("chat_turn", {"text": "臺中詐欺最近三個月趨勢"})["chat"]
        self.assertEqual(chat["resolved_request"]["tool"], "query_statistics")
        args = chat["resolved_request"]["arguments"]
        self.assertEqual(args["metric"], "fraud_reports")
        self.assertEqual(args["geography"], "臺中市")
        self.assertEqual(args["period_from"], "2026-07")
        self.assertEqual(args["period_to"], "2026-09")
        geographies = {row["geography"] for row in chat["statistics"]}
        self.assertEqual(geographies, {"臺中市"})
        self.assertEqual({row["period"] for row in chat["statistics"]}, {"2026-07", "2026-08", "2026-09"})
        self.assertTrue(all(row["unit"] == "件" for row in chat["statistics"]))
        provisional = {row["period"]: row["provisional"] for row in chat["statistics"]}
        self.assertEqual(provisional, {"2026-07": False, "2026-08": False, "2026-09": True})
        self.assertTrue(all(row["official_url"].startswith("https://") for row in chat["statistics"]))

    def test_clarification_never_executes_a_query(self):
        gateway = self.build()
        chat = gateway.execute("chat_turn", {"text": "你好，今天天氣好嗎"})["chat"]
        self.assertEqual(chat["intent"], "clarify")
        self.assertEqual(chat["status"], "CLARIFICATION_NEEDED")
        self.assertIsNone(chat["resolved_request"])
        self.assertTrue(chat["quick_action_hints"])

    def test_missing_selected_event_gives_clarification_not_guessed_context(self):
        gateway = self.build()
        chat = gateway.execute("chat_turn", {"text": "跟昨天比有沒有改？"})["chat"]
        self.assertEqual(chat["status"], "CLARIFICATION_NEEDED")
        self.assertIsNone(chat["resolved_request"])
        stale = gateway.execute("chat_turn", {
            "text": "跟昨天比有沒有改？",
            "context": {"schema_version": 1, "selected_event_id": "PE-GONE"},
        })["chat"]
        self.assertEqual(stale["status"], "NOT_FOUND")
        self.assertIsNone(stale["context"].get("selected_event_id"))

    def test_context_is_bounded_validated_and_never_executes_tools(self):
        gateway = self.build()
        self.assert_chat_error(gateway, {"text": "查事件", "context": "not-an-object"}, "INVALID_ARGUMENTS")
        self.assert_chat_error(gateway, {"text": "查事件", "context": {"selected_event_id": 42}}, "INVALID_ARGUMENTS")
        self.assert_chat_error(gateway, {
            "text": "查事件",
            "context": {"last_result_event_ids": [f"PE-{index}" for index in range(200)]},
        }, "INVALID_ARGUMENTS")
        chat = gateway.execute("chat_turn", {
            "text": "只看西屯",
            "context": {
                "schema_version": 99,
                "selected_event_id": "PE-CHAT-2",
                "selected_region": "location:tc-nantun",
                "last_result_event_ids": ["PE-CHAT-2"],
                "tool": "drop_all_tables",
                "arguments": {"sql": "DELETE FROM public_events"},
            },
        })["chat"]
        self.assertEqual(chat["resolved_request"]["tool"], "search_events")
        self.assertNotIn("sql", chat["resolved_request"]["arguments"].values())
        self.assertEqual(chat["context"]["schema_version"], 1)
        self.assertNotIn("tool", chat["context"])
        self.assertNotIn("arguments", chat["context"])
        self.assertNotIn("messages", chat["context"])
        self.assertNotIn("text", chat["context"])

    def test_chat_envelope_carries_publication_hash_and_receipt(self):
        gateway = self.build()
        result = gateway.execute("chat_turn", {"text": "這週末有哪些活動？"})
        self.assertTrue(result["publication_hash"])
        self.assertTrue(result["query_id"])
        self.assertTrue(result["receipt"]["arguments_sha256"])
        self.assertEqual(result["result_type"], "conversation")
        chat = result["chat"]
        self.assertTrue(chat["publication_hash"])
        self.assertTrue(chat["query_receipt"]["arguments_sha256"])
        self.assertTrue(chat["evidence_links"] or chat["verified"] or chat["unverified"])

    def test_capabilities_and_worker_style_unavailable_reporting(self):
        gateway = self.build()
        capabilities = gateway.capabilities()
        self.assertIn("chat_turn", capabilities["capabilities"])
        self.assertTrue(capabilities["read_only"])
        self.assertIn("conversation", capabilities)
        self.assertTrue(capabilities["conversation"]["quick_actions"])


if __name__ == "__main__":
    unittest.main()
