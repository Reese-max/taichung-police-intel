import importlib.util
import json
from datetime import datetime, timezone
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "query-gateway.py"
spec = importlib.util.spec_from_file_location("query_gateway", SCRIPT)
gateway_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway_module)


class QueryGatewayTests(unittest.TestCase):
    @staticmethod
    def located_bundle(status="CONFIRMED_OFFICIAL"):
        document = {
            "schema_version": 1,
            "document_id": "DOC-LOCATED-1",
            "document_version_id": "DOCV-AAAAAAAAAAAAAAAAAAAA",
            "source_id": "S-028",
            "original_source_identity": "S-028:dataset-88147",
            "requested_url": "https://data.gov.tw/api/v2/rest/dataset/88147",
            "final_url": "https://data.gov.tw/api/v2/rest/dataset/88147",
            "fetched_at": "2026-09-21T00:00:00+00:00",
            "content_type": "application/json",
            "raw_bytes_sha256": "a" * 64,
            "extracted_text_sha256": "b" * 64,
            "extractor_version": "located-facts-v1",
            "snapshot_ref": "sha256:" + "a" * 64,
            "rights_status": "METADATA_LINK_ONLY",
        }
        locator = {
            "type": "JSON_POINTER",
            "pointer": "/result/title",
            "raw_value": "臺中市受理刑事案件",
            "document_sha256": document["raw_bytes_sha256"],
            "text_sha256": document["extracted_text_sha256"],
        }
        fact = {
            "fact_id": "FACT-LOCATED-1",
            "document_id": document["document_id"],
            "document_version_id": document["document_version_id"],
            "source_id": document["source_id"],
            "original_source_identity": document["original_source_identity"],
            "raw_bytes_sha256": document["raw_bytes_sha256"],
            "extracted_text_sha256": document["extracted_text_sha256"],
            "extractor_version": document["extractor_version"],
            "subject_id": "dataset:88147",
            "predicate": "dataset_title",
            "normalized_value": "臺中市受理刑事案件",
            "valid_time": None,
            "locator": locator,
            "verification_status": status,
        }
        evidence = {
            "evidence_id": "EVID-LOCATED-1",
            "fact_id": fact["fact_id"],
            "source_id": document["source_id"],
            "document_version_id": document["document_version_id"],
            "official_url": document["final_url"],
            "locator": locator,
            "content_sha256": document["raw_bytes_sha256"],
            "verification_status": status,
        }
        events = [{
            "stable_id": fact["fact_id"],
            "source_id": document["source_id"],
            "source_snapshot_ref": document["snapshot_ref"],
            "content_sha256": document["raw_bytes_sha256"],
            "official_url": document["final_url"],
            "subject_id": fact["subject_id"],
            "predicate": fact["predicate"],
            "normalized_value": fact["normalized_value"],
            "valid_time": fact["valid_time"],
            "verification_status": status,
        }]
        if status == "CONFIRMED_OFFICIAL":
            review = {
                "decision": "CONFIRMED_OFFICIAL",
                "reviewer_ref": "test-reviewer",
                "verified_at": "2026-09-21T00:00:00+00:00",
                "method": "EXACT_LOCATOR_RECHECK",
            }
            fact["review"] = review
            evidence["review"] = review
            events[0]["review"] = review
        bundle = {"document_version": document, "facts": [fact], "evidence_catalog": [evidence], "public_event_inputs": events}
        bundle["receipt"] = {
            "source_id": document["source_id"],
            "document_version_id": document["document_version_id"],
            "raw_bytes_sha256": document["raw_bytes_sha256"],
            "extracted_text_sha256": document["extracted_text_sha256"],
            "extractor_version": document["extractor_version"],
            "fact_count": 1,
            "bundle_sha256": gateway_module._json_hash({"facts": [fact], "evidence_catalog": [evidence], "public_event_inputs": events}),
        }
        return bundle

    @classmethod
    def setUpClass(cls):
        clock = lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
        cls.gateway = gateway_module.QueryGateway(clock=clock)
        cls.server = gateway_module.build_server("127.0.0.1", 0, cls.gateway, "http://allowed.example")
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    @classmethod
    def request(cls, method, path, payload=None, headers=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_headers = {"Content-Type": "application/json"} if data is not None else {}
        request_headers.update(headers or {})
        request = Request(
            cls.base_url + path,
            data=data,
            method=method,
            headers=request_headers,
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_web_query_and_mcp_share_deterministic_results(self):
        arguments = {"limit": 5}
        query_status, query = self.request("POST", "/query", {"tool": "search_evidence", "arguments": arguments})
        mcp_status, mcp = self.request(
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_evidence", "arguments": arguments}},
        )
        self.assertEqual(query_status, 200)
        self.assertEqual(mcp_status, 200)
        self.assertFalse(mcp["result"]["isError"])
        structured = mcp["result"]["structuredContent"]
        self.assertEqual(
            [row["canonical_id"] for row in query["results"]],
            [row["canonical_id"] for row in structured["results"]],
        )
        self.assertEqual(query["publication_hash"], structured["publication_hash"])
        self.assertEqual(query["policy"], structured["policy"])
        self.assertEqual(query["query_coverage"]["capability_id"], "publication_metadata")
        self.assertEqual(query["query_coverage"]["policy_hash"], query["policy"]["policy_hash"])
        self.assertIn("supported_capabilities", query["query_coverage"])
        self.assertFalse(query["retention"]["full_text_allowed"])
        self.assertRegex(query["retention"]["policy_hash"], r"^[0-9a-f]{64}$")

    def test_stale_snapshot_is_not_presented_as_current(self):
        status, response = self.request("POST", "/query", {"tool": "get_current_brief", "arguments": {}})
        self.assertEqual(status, 200)
        self.assertEqual(response["freshness"], "STALE")
        self.assertFalse(response["current_as_of_server_clock"])
        self.assertIn("過期", response["verification_summary"])
        self.assertEqual(response["query_coverage"]["status"], "STALE")

    def test_no_result_source_gap_and_unsupported_capability_are_explicit(self):
        status, response = self.request(
            "POST", "/query", {"tool": "search_evidence", "arguments": {"q": "不存在的測試字串"}}
        )
        self.assertEqual(status, 200)
        self.assertEqual(response["result_count"], 0)
        self.assertFalse(response["answerable_no_match"])
        self.assertTrue(response["source_gaps"])
        self.assertFalse(response["query_coverage"]["can_state_bounded_no_match"])

        unsupported_status, unsupported = self.request(
            "POST", "/query", {"tool": "search_events", "arguments": {}}
        )
        self.assertEqual(unsupported_status, 422)
        self.assertEqual(unsupported["error"]["code"], "CAPABILITY_NOT_AVAILABLE")

    def test_mcp_exposes_only_read_only_slice(self):
        status, response = self.request("POST", "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(status, 200)
        tools = response["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], list(gateway_module.CAPABILITIES))
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in tools))
        self.assertTrue(all(tool["annotations"]["destructiveHint"] is False for tool in tools))

    def test_mcp_rejects_unapproved_origin_and_advertises_protocol_version(self):
        status, response = self.request(
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            headers={"Origin": "https://evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(response["error"]["code"], "ORIGIN_NOT_ALLOWED")

        request = Request(
            self.base_url + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 5, "method": "initialize"}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "http://allowed.example"},
        )
        with urlopen(request, timeout=5) as allowed_response:
            self.assertEqual(allowed_response.headers["MCP-Protocol-Version"], gateway_module.MCP_PROTOCOL_VERSION)
            allowed = json.loads(allowed_response.read().decode("utf-8"))
        self.assertEqual(allowed["result"]["protocolVersion"], gateway_module.MCP_PROTOCOL_VERSION)

    def test_argument_boundary_rejects_url_sql_and_bad_generation(self):
        for bad_arguments in ({"url": "https://example.invalid"}, {"sql": "select 1"}):
            status, response = self.request(
                "POST", "/query", {"tool": "search_evidence", "arguments": bad_arguments}
            )
            self.assertEqual(status, 400)
            self.assertEqual(response["error"]["code"], "INVALID_ARGUMENTS")

        status, response = self.request(
            "POST",
            "/query",
            {"tool": "search_evidence", "arguments": {"expected_generation": "wrong"}},
        )
        self.assertEqual(status, 400)
        self.assertEqual(response["error"]["code"], "INVALID_ARGUMENTS")

    def test_source_health_is_filtered_to_approved_public_fields(self):
        status, response = self.request(
            "POST", "/query", {"tool": "get_source_health", "arguments": {"source_id": "S-004"}}
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(response["sources"]), 1)
        source = response["sources"][0]
        self.assertEqual(source["source_id"], "S-004")
        self.assertTrue(source["source_url"].startswith("https://"))
        self.assertNotIn("password", source)
        self.assertEqual(response["query_coverage"]["capability_id"], "source_health")

    def test_rate_limiter_is_bounded_per_client(self):
        limiter = gateway_module.RateLimiter(limit=2, window_seconds=60)
        self.assertTrue(limiter.allow("client", now=10))
        self.assertTrue(limiter.allow("client", now=11))
        self.assertFalse(limiter.allow("client", now=12))
        self.assertTrue(limiter.allow("other-client", now=12))
        self.assertTrue(limiter.allow("client", now=71))

    def test_response_byte_limit_fails_closed(self):
        original = gateway_module.MAX_RESPONSE_BYTES
        gateway_module.MAX_RESPONSE_BYTES = 256
        try:
            status, response = self.request("POST", "/query", {"tool": "get_current_brief", "arguments": {}})
        finally:
            gateway_module.MAX_RESPONSE_BYTES = original
        self.assertEqual(status, 500)
        self.assertEqual(response["error"]["code"], "RESPONSE_TOO_LARGE")

    def test_validate_answer_uses_server_catalog_and_controlled_renderer(self):
        item = next(row for row in self.gateway.store["items"] if row["freshness_status"] == "FRESH")
        claim = {
            "claim_type": "STATUS",
            "text": "官方文件標題（因豪雨提前）",
            "temporal_scope": "CURRENT",
            "proposition": {"subject": f"publication:{item['canonical_id']}:title", "value": item["title"]},
        }
        payload = {"tool": "validate_answer", "arguments": {"claims": [claim]}}
        query_status, query = self.request("POST", "/query", payload)
        mcp_status, mcp = self.request(
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "validate_answer", "arguments": {"claims": [claim]}
            }},
        )
        self.assertEqual(query_status, 200)
        self.assertEqual(mcp_status, 200)
        self.assertEqual(query["gate_status"], "PASS")
        self.assertEqual(query["answer"], mcp["result"]["structuredContent"]["answer"])
        self.assertNotIn("因豪雨提前", query["answer"][0])
        normalized_claim = {
            **claim,
            "text": "官方文件標題\n（caller 不得控制輸出）",
            "proposition": {
                "subject": f"  publication:{item['canonical_id']}:title  ",
                "value": f" {item['title']}\n",
            },
        }
        normalized_status, normalized = self.request(
            "POST",
            "/query",
            {"tool": "validate_answer", "arguments": {"claims": [normalized_claim]}},
        )
        self.assertEqual(normalized_status, 200)
        self.assertEqual(normalized["gate_status"], "PASS")
        normalized_title = "".join(item["title"].split())
        self.assertEqual(
            normalized["answer"],
            [f"官方來源已核對：publication:{item['canonical_id']}:title={normalized_title}。"],
        )
        receipt = query["answer_evidence_receipt"]
        self.assertEqual(receipt["publication_hash"], query["publication_hash"])
        self.assertRegex(receipt["evidence_catalog_hash"], r"^[0-9a-f]{64}$")
        self.assertIn(f"PUB-{item['canonical_id']}", receipt["evidence_ids"])

        stale_gateway = gateway_module.QueryGateway(
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )
        stale = stale_gateway.execute("validate_answer", {"claims": [claim]})
        self.assertEqual(stale["gate_status"], "QUALIFIED")
        self.assertEqual(stale["final_claims"][0]["support_status"], "STALE")

        non_factual_status, non_factual = self.request(
            "POST",
            "/query",
            {"tool": "validate_answer", "arguments": {"claims": [{
                "claim_type": "OTHER", "text": "不應回顯", "proposition": {"subject": "caller", "value": "自由文字"}
            }]}},
        )
        self.assertEqual(non_factual_status, 200)
        self.assertEqual(non_factual["answer"], [])

        bad_status, bad = self.request(
            "POST",
            "/query",
            {"tool": "validate_answer", "arguments": {"claims": [{**claim, "evidence": []}]}},
        )
        self.assertEqual(bad_status, 400)
        self.assertEqual(bad["error"]["code"], "INVALID_ARGUMENTS")

    def test_located_facts_enter_gate_only_after_server_side_confirmation(self):
        snapshot = gateway_module.load_snapshot()
        snapshot["located_facts"] = self.located_bundle()
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )
        claim = {
            "claim_type": "STATISTIC",
            "text": "官方資料標題",
            "temporal_scope": "HISTORICAL",
            "proposition": {"subject": "dataset:88147:dataset_title", "value": "臺中市受理刑事案件"},
        }
        result = gateway.execute("validate_answer", {"claims": [claim]})
        self.assertEqual(result["gate_status"], "PASS")
        self.assertIn("EVID-LOCATED-1", result["answer_evidence_receipt"]["evidence_ids"])

        candidate = gateway_module.load_snapshot()
        candidate["located_facts"] = self.located_bundle("FACT_CANDIDATE")
        blocked = gateway_module.QueryGateway(snapshot=candidate, clock=gateway.clock).execute(
            "validate_answer", {"claims": [claim]}
        )
        self.assertNotIn("EVID-LOCATED-1", blocked["answer_evidence_receipt"]["evidence_ids"])
        self.assertNotEqual(blocked["gate_status"], "PASS")

    def test_located_facts_receipt_hash_is_fail_closed(self):
        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["receipt"]["bundle_sha256"] = "0" * 64
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "receipt hash mismatch"):
            gateway_module.QueryGateway(snapshot=snapshot)

    def test_located_facts_hash_bindings_are_fail_closed(self):
        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["facts"][0]["raw_bytes_sha256"] = "0" * 64
        bundle["receipt"]["bundle_sha256"] = gateway_module._json_hash({
            "facts": bundle["facts"],
            "evidence_catalog": bundle["evidence_catalog"],
            "public_event_inputs": bundle["public_event_inputs"],
        })
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "fact is not bound"):
            gateway_module.QueryGateway(snapshot=snapshot)

    def test_located_facts_confirmation_requires_review_receipt(self):
        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["evidence_catalog"][0].pop("review")
        bundle["receipt"]["bundle_sha256"] = gateway_module._json_hash({
            "facts": bundle["facts"],
            "evidence_catalog": bundle["evidence_catalog"],
            "public_event_inputs": bundle["public_event_inputs"],
        })
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "requires a bound review"):
            gateway_module.QueryGateway(snapshot=snapshot)

    def test_located_facts_source_url_rejects_credentials_and_nonstandard_ports(self):
        for url in (
            "https://user:data.gov.tw@data.gov.tw/api/v2/rest/dataset/88147",
            "https://data.gov.tw:8443/api/v2/rest/dataset/88147",
        ):
            snapshot = gateway_module.load_snapshot()
            bundle = self.located_bundle()
            bundle["document_version"]["final_url"] = url
            snapshot["located_facts"] = bundle
            with self.assertRaisesRegex(ValueError, "source is not approved"):
                gateway_module.QueryGateway(snapshot=snapshot)

    def test_located_facts_rejects_orphan_event_rows(self):
        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["public_event_inputs"].append({"stable_id": "FACT-ORPHAN"})
        bundle["receipt"]["bundle_sha256"] = gateway_module._json_hash({
            "facts": bundle["facts"],
            "evidence_catalog": bundle["evidence_catalog"],
            "public_event_inputs": bundle["public_event_inputs"],
        })
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "event is not bound"):
            gateway_module.QueryGateway(snapshot=snapshot)

    def test_located_facts_requires_evidence_for_every_fact(self):
        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["evidence_catalog"] = []
        bundle["receipt"]["bundle_sha256"] = gateway_module._json_hash({
            "facts": bundle["facts"],
            "evidence_catalog": bundle["evidence_catalog"],
            "public_event_inputs": bundle["public_event_inputs"],
        })
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "evidence must link exactly"):
            gateway_module.QueryGateway(snapshot=snapshot)


if __name__ == "__main__":
    unittest.main()
