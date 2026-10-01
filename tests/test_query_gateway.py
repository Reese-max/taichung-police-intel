import importlib.util
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from threading import Thread
import tempfile
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest
from pathlib import Path

from intel_v2.located_facts import acquire_document, build_bundle, confirm_facts


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "query-gateway.py"
spec = importlib.util.spec_from_file_location("query_gateway", SCRIPT)
gateway_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway_module)


class QueryGatewayTests(unittest.TestCase):
    @staticmethod
    def located_bundle(status="CONFIRMED_OFFICIAL", source_id="S-028",
                       source_url="https://data.gov.tw/api/v2/rest/dataset/88147"):
        document = {
            "schema_version": 1,
            "document_id": "DOC-LOCATED-1",
            "document_version_id": "DOCV-AAAAAAAAAAAAAAAAAAAA",
            "source_id": source_id,
            "original_source_identity": f"{source_id}:dataset-88147",
            "requested_url": source_url,
            "final_url": source_url,
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
            "fact_id": None,
            "document_id": document["document_id"],
            "document_version_id": document["document_version_id"],
            "source_id": document["source_id"],
            "original_source_identity": document["original_source_identity"],
            "raw_bytes_sha256": document["raw_bytes_sha256"],
            "extracted_text_sha256": document["extracted_text_sha256"],
            "extractor_version": document["extractor_version"],
            "subject_id": "dataset:88147",
            "predicate": "dataset_title",
            "normalizer": "text",
            "raw_value": "臺中市受理刑事案件",
            "normalized_value": "臺中市受理刑事案件",
            "valid_time": None,
            "valid_time_source": None,
            "locator": locator,
            "verification_status": status,
        }
        fact["fact_id"] = "FACT-" + gateway_module._json_hash({
            "document_version_id": fact["document_version_id"],
            "subject_id": fact["subject_id"],
            "predicate": fact["predicate"],
            "normalizer": fact["normalizer"],
            "raw_value": fact["raw_value"],
            "normalized_value": fact["normalized_value"],
            "valid_time": fact["valid_time"],
            "valid_time_source": fact["valid_time_source"],
            "locator": fact["locator"],
        })[:20].upper()
        evidence = {
            "evidence_id": "EVID-" + fact["fact_id"][5:],
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

    def test_saved_query_store_must_match_canonical_artifacts(self):
        snapshot = gateway_module.load_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "query-store.json"
            path.write_text(json.dumps(snapshot["store"], ensure_ascii=False), encoding="utf-8")
            loaded = gateway_module.load_snapshot(query_store_path=path)
            self.assertEqual(loaded["store"], snapshot["store"])

            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["items"][0]["title"] = "不屬於 canonical artifact 的標題"
            tampered["projection_sha256"] = gateway_module.qs.sha256_bytes(
                gateway_module.qs.canonical_json({
                    key: value for key, value in tampered.items() if key != "projection_sha256"
                })
            )
            path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not match canonical"):
                gateway_module.load_snapshot(query_store_path=path)

    def test_stale_snapshot_is_not_presented_as_current(self):
        if os.getenv("GOVINTEL_PUBLICATION_WORKFLOW") == "1":
            self.skipTest("Pages publication uses generated data, not the checked-in fixture snapshot")
        status, response = self.request("POST", "/query", {"tool": "get_current_brief", "arguments": {}})
        self.assertEqual(status, 200)
        self.assertEqual(response["freshness"], "STALE")
        self.assertFalse(response["current_as_of_server_clock"])
        self.assertIn("過期", response["verification_summary"])
        self.assertEqual(response["query_coverage"]["status"], "STALE")

    def test_publication_receipt_is_bound_to_current_artifact_hashes(self):
        status, response = self.request("POST", "/query", {"tool": "get_publication_receipt", "arguments": {}})
        self.assertEqual(status, 200)
        receipt = response["publication_receipt"]
        generated_from = self.gateway.store["generated_from"]
        self.assertEqual(receipt["publication_id"], generated_from["collection_run_id"])
        self.assertEqual(receipt["publication_hash"], generated_from["brief_sha256"])
        self.assertEqual(receipt["generation_id"], self.gateway.store["generation_id"])
        self.assertEqual(receipt["artifact_hashes"], {
            "feed": generated_from["feed_sha256"],
            "status": generated_from["status_sha256"],
            "brief": generated_from["brief_sha256"],
        })
        self.assertFalse(receipt["current_as_of_server_clock"])
        self.assertNotIn("items", receipt)
        mcp_status, mcp = self.request(
            "POST",
            "/mcp",
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_publication_receipt", "arguments": {}}},
        )
        self.assertEqual(mcp_status, 200)
        self.assertFalse(mcp["result"]["isError"])
        self.assertEqual(mcp["result"]["structuredContent"]["publication_receipt"], receipt)

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
        if os.getenv("GOVINTEL_PUBLICATION_WORKFLOW") == "1":
            self.skipTest("Pages publication uses generated data, not the checked-in fixture snapshot")
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
        evidence_id = f"PUB-{item['canonical_id']}"
        self.assertIn(evidence_id, receipt["evidence_ids"])
        catalog = self.gateway._trusted_evidence_catalog(self.gateway.store, self.gateway.clock())
        self.assertEqual(receipt["evidence_catalog_hash"], gateway_module._json_hash(catalog))
        catalog_item = next(row for row in catalog if row["evidence_id"] == evidence_id)
        self.assertEqual(catalog_item["content_sha256"], item["content_sha256"])
        supporter = receipt["claims"][0]["supporting_evidence"][0]
        self.assertEqual(supporter["evidence_id"], evidence_id)
        self.assertEqual(supporter["source_id"], item["source_id"])
        self.assertEqual(supporter["document_version"], item["content_sha256"])
        self.assertEqual(supporter["locator"], f"{item['official_url']}#publication:{item['canonical_id']}")
        mcp_receipt = mcp["result"]["structuredContent"]["answer_evidence_receipt"]
        self.assertEqual(mcp_receipt["evidence_catalog_hash"], receipt["evidence_catalog_hash"])
        self.assertEqual(mcp_receipt["claims"][0]["supporting_evidence"], receipt["claims"][0]["supporting_evidence"])

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

    def test_discovery_rows_never_enter_the_local_answer_catalog(self):
        snapshot = gateway_module.load_snapshot()
        original = dict(snapshot["store"]["items"][0])
        official = {**original, "source_role": "PRIMARY_OFFICIAL"}
        discovery = {
            **original,
            "canonical_id": "ISSUE32-DISCOVERY",
            "title": "媒體影片聲稱目前封路",
            "source_role": "DISCOVERY_UNVERIFIED",
            "official_url": "https://media.example.test/watch/issue-32",
        }
        snapshot["store"]["items"] = [official, discovery]
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
        )
        catalog = gateway._trusted_evidence_catalog(gateway.store, gateway.clock())
        self.assertEqual([row["evidence_id"] for row in catalog], [f"PUB-{official['canonical_id']}"])

    def test_item_level_role_cannot_promote_a_non_official_source(self):
        snapshot = gateway_module.load_snapshot()
        official = dict(snapshot["store"]["items"][0], source_role="PRIMARY_OFFICIAL")
        forged = {
            **official,
            "canonical_id": "ISSUE32-FORGED-ROLE",
            "title": "背景資料來源偽稱官方證據",
            "source_id": "CTX-POP",
            "source_role": "PRIMARY_OFFICIAL",
        }
        snapshot["store"]["items"] = [official, forged]
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
        )
        catalog = gateway._trusted_evidence_catalog(gateway.store, gateway.clock())
        self.assertEqual([row["evidence_id"] for row in catalog], [f"PUB-{official['canonical_id']}"])

    def test_answer_gate_runner_computes_catalog_hash_from_supplied_evidence(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node runtime is unavailable")
        runner = gateway_module.ROOT / "scripts" / "answer-gate-runner.mjs"
        payload = {
            "claims": [{
                "schema_version": 1,
                "claim_id": "runner-hash-check",
                "text": "候選主張",
                "claim_type": "STATUS",
                "temporal_scope": "CURRENT",
                "proposition": {"subject": "publication:X:title", "value": "標題"},
            }],
            "evidence": [],
            "generated_at": "2026-09-11T09:00:00+00:00",
            "publication_hash": "f" * 64,
        }

        def invoke(extra):
            return subprocess.run(
                [node, str(runner)],
                input=json.dumps({**payload, **extra}, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )

        forged = invoke({"evidence_catalog_hash": "0" * 64})
        self.assertNotEqual(forged.returncode, 0)
        honest = invoke({})
        self.assertEqual(honest.returncode, 0, honest.stderr)
        output = json.loads(honest.stdout)
        self.assertEqual(output["receipt"]["evidence_catalog_hash"], gateway_module._json_hash(payload["evidence"]))

    def test_gate_subprocess_failure_does_not_leak_stderr_to_callers(self):
        marker = "INTERNAL-RUNNER-DETAIL-NOT-FOR-CLIENTS"
        failed = subprocess.CompletedProcess(args=["node"], returncode=1, stdout="", stderr=marker)
        gateway = gateway_module.QueryGateway(
            clock=lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
        )
        with mock.patch.object(gateway_module.subprocess, "run", return_value=failed):
            with self.assertRaises(gateway_module.GatewayError) as caught:
                gateway.execute("validate_answer", {"claims": [{
                    "claim_type": "STATUS",
                    "text": "觸發閘門",
                    "proposition": {"subject": "s", "value": "v"},
                }]})
        self.assertEqual(caught.exception.code, "GATE_FAILED")
        self.assertNotIn(marker, str(caught.exception))

    def test_malformed_source_catalog_fails_closed(self):
        snapshot = gateway_module.load_snapshot()
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
        )
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("[]")
            bad_catalog = handle.name
        try:
            with mock.patch.object(gateway_module, "SOURCE_CATALOG", Path(bad_catalog)):
                with self.assertRaises(gateway_module.GatewayError) as caught:
                    gateway._trusted_evidence_catalog(gateway.store, gateway.clock())
                with self.assertRaises(ValueError):
                    gateway_module.approved_source_origins()
        finally:
            os.unlink(bad_catalog)
        self.assertEqual(caught.exception.code, "GATE_FAILED")
        self.assertEqual(caught.exception.status, 503)

    def test_enrichment_source_cannot_publish_located_facts_evidence(self):
        snapshot = gateway_module.load_snapshot()
        gateway_module.validate_document_url("CTX-POP", "https://data.gov.tw/dataset/103703")
        snapshot["located_facts"] = self.located_bundle(
            source_id="CTX-POP",
            source_url="https://data.gov.tw/dataset/103703",
        )
        with self.assertRaisesRegex(ValueError, "not approved and HTTPS"):
            gateway_module.QueryGateway(snapshot=snapshot)

    def test_gate_receipt_rejects_unrecognized_validator_version(self):
        gateway = gateway_module.QueryGateway(
            clock=lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
        )
        catalog = gateway._trusted_evidence_catalog(gateway.store, gateway.clock())
        forged = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=json.dumps({
                "schema_version": 1,
                "gate_status": "PASS",
                "final_claims": [],
                "answer": [],
                "receipt": {
                    "schema_version": 1,
                    "validator_version": "answer-evidence-gate/0",
                    "publication_hash": gateway.store["generated_from"]["brief_sha256"],
                    "evidence_catalog_hash": gateway_module._json_hash(catalog),
                },
            }),
            stderr="",
        )
        with mock.patch.object(gateway_module.subprocess, "run", return_value=forged):
            with self.assertRaises(gateway_module.GatewayError) as caught:
                gateway.execute("validate_answer", {"claims": [{
                    "claim_type": "STATUS",
                    "text": "觸發閘門",
                    "proposition": {"subject": "s", "value": "v"},
                }]})
        self.assertEqual(caught.exception.code, "GATE_FAILED")
        self.assertIn("validator version", caught.exception.message)

    def test_non_portable_claim_scalar_fails_closed(self):
        gateway = gateway_module.QueryGateway(
            clock=lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(gateway_module.GatewayError, "non-portable"):
            gateway.execute("validate_answer", {"claims": [{
                "claim_type": "STATUS",
                "text": "不可精確表示的主張",
                "proposition": {"subject": "s", "value": 2**53},
            }]})

    def test_typed_statistics_use_exact_scope_and_cannot_back_current_wording(self):
        snapshot = gateway_module.load_snapshot()
        snapshot["statistics_store"] = gateway_module.query_domain.build_statistics_store([{
            "statistic_id": "STAT-ISSUE32-1",
            "dataset_id": "NPA-STAT-1",
            "source_id": "S-028",
            "metric": "fraud_reports",
            "period": "2026-08",
            "value": 120,
            "value_text": "120",
            "unit": "件",
            "geography": "臺中市",
            "provisional": False,
            "updated_at": "2026-09-21T00:00:00+08:00",
            "official_url": "https://data.gov.tw/dataset/88147",
            "evidence_locator": "json:/result/records/0/value",
        }])
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )
        proposition = {
            "subject": "statistic:NPA-STAT-1:fraud_reports",
            "value": {"value": "120", "period": "2026-08", "geography": "臺中市", "unit": "件"},
        }
        historical = gateway.execute("validate_answer", {"claims": [{
            "claim_type": "STATISTIC",
            "text": "2026-08 臺中市詐騙通報為 120 件",
            "temporal_scope": "HISTORICAL",
            "proposition": proposition,
        }]})
        self.assertEqual(historical["gate_status"], "PASS")
        self.assertEqual(historical["answer_evidence_receipt"]["evidence_ids"], ["STAT-STAT-ISSUE32-1"])
        current = gateway.execute("validate_answer", {"claims": [{
            "claim_type": "STATISTIC",
            "text": "本月臺中市詐騙通報為 120 件",
            "temporal_scope": "CURRENT",
            "proposition": proposition,
        }]})
        self.assertEqual(current["final_claims"][0]["support_status"], "STALE")

    def _gate_output(self, receipt_extra, gate_status="BLOCKED"):
        publication_hash = self.gateway.store["generated_from"]["brief_sha256"]
        catalog = self.gateway._trusted_evidence_catalog(self.gateway.store, self.gateway.clock())
        return json.dumps({
            "schema_version": 1,
            "gate_status": gate_status,
            "final_claims": [],
            "answer": [],
            "receipt": {
                "schema_version": 1,
                "validator_version": "answer-evidence-gate/3",
                "gate_status": gate_status,
                "publication_hash": publication_hash,
                "evidence_catalog_hash": gateway_module._json_hash(catalog),
                "claim_ids": [],
                "evidence_ids": [],
                "claims": [],
                **receipt_extra,
            },
        })

    def test_answer_gate_refuses_a_receipt_the_validator_never_produced(self):
        catalog = self.gateway._trusted_evidence_catalog(self.gateway.store, self.gateway.clock())
        # Each case isolates one guard: the refused receipt reports full
        # coverage, the partial one reports none, and both carry the hashes the
        # host asked for, so the binding check alone cannot reject either.
        cases = {
            "refused": self._gate_output(
                {"failure_reason": "DUPLICATE_EVIDENCE_ID", "indexed_evidence_count": len(catalog)},
            ),
            "partial": self._gate_output({"indexed_evidence_count": 0}, gate_status="QUALIFIED"),
        }
        for label, stdout in cases.items():
            completed = subprocess.CompletedProcess(
                args=["node"], returncode=0, stdout=stdout, stderr="",
            )
            with mock.patch.object(gateway_module.subprocess, "run", return_value=completed):
                with self.assertRaises(gateway_module.GatewayError) as caught:
                    self.gateway.execute("validate_answer", {"claims": [{
                        "claim_type": "STATUS",
                        "text": "官方文件標題",
                        "proposition": {"subject": "a", "value": "b"},
                    }]})
            self.assertEqual(caught.exception.code, "GATE_FAILED", label)
            self.assertEqual(caught.exception.status, 503, label)

    def test_answer_gate_accepts_a_fully_indexed_bound_receipt(self):
        catalog = self.gateway._trusted_evidence_catalog(self.gateway.store, self.gateway.clock())
        completed = subprocess.CompletedProcess(
            args=["node"],
            returncode=0,
            stdout=self._gate_output({"indexed_evidence_count": len(catalog)}, gate_status="PASS"),
            stderr="",
        )
        with mock.patch.object(gateway_module.subprocess, "run", return_value=completed):
            output = self.gateway._run_answer_gate([{"claim_type": "STATUS", "text": "t", "proposition": {"subject": "a", "value": "b"}}])
        self.assertEqual(output["gate_status"], "PASS")
        self.assertEqual(output["receipt"]["indexed_evidence_count"], len(catalog))

    def test_located_facts_enter_gate_only_after_server_side_confirmation(self):
        snapshot = gateway_module.load_snapshot()
        confirmed_bundle = self.located_bundle()
        snapshot["located_facts"] = confirmed_bundle
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
        evidence_id = confirmed_bundle["evidence_catalog"][0]["evidence_id"]
        self.assertIn(evidence_id, result["answer_evidence_receipt"]["evidence_ids"])

        candidate = gateway_module.load_snapshot()
        candidate["located_facts"] = self.located_bundle("FACT_CANDIDATE")
        blocked = gateway_module.QueryGateway(snapshot=candidate, clock=gateway.clock).execute(
            "validate_answer", {"claims": [claim]}
        )
        self.assertNotIn(evidence_id, blocked["answer_evidence_receipt"]["evidence_ids"])
        self.assertNotEqual(blocked["gate_status"], "PASS")

    def test_saved_document_runs_through_adapter_review_and_answer_gate(self):
        body = (ROOT / "tests/fixtures/located-facts/official-data.json").read_bytes()
        document = acquire_document(
            source_id="S-028",
            requested_url="https://data.gov.tw/api/v2/rest/dataset/88147",
            final_url="https://data.gov.tw/api/v2/rest/dataset/88147",
            body=body,
            content_type="application/json",
            fetched_at="2026-09-21T00:00:00+00:00",
            rights_status="METADATA_LINK_ONLY",
        )
        bundle = build_bundle(document, body, [{
            "subject_id": "dataset:88147",
            "predicate": "record_count",
            "pointer": "/event/count",
            "normalizer": "integer",
        }])
        confirmed = confirm_facts(
            bundle,
            body,
            [bundle["facts"][0]["fact_id"]],
            reviewer_ref="integration-test-reviewer",
            verified_at="2026-09-21T08:00:00+08:00",
        )
        snapshot = gateway_module.load_snapshot()
        snapshot["located_facts"] = confirmed
        gateway = gateway_module.QueryGateway(
            snapshot=snapshot,
            clock=lambda: datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
        )
        result = gateway.execute("validate_answer", {"claims": [{
            "claim_type": "STATISTIC",
            "text": "官方資料筆數",
            "temporal_scope": "HISTORICAL",
            "proposition": {"subject": "dataset:88147:record_count", "value": 1234},
        }]})
        self.assertEqual(result["gate_status"], "PASS")
        self.assertEqual(result["final_claims"][0]["support_status"], "SUPPORTED")
        self.assertEqual(result["answer_evidence_receipt"]["evidence_ids"], [confirmed["evidence_catalog"][0]["evidence_id"]])

    def test_located_facts_candidate_source_is_not_approved(self):
        self.assertIn("S-028", gateway_module.approved_source_origins())
        self.assertNotIn("S-032", gateway_module.approved_source_origins())
        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["document_version"]["source_id"] = "S-032"
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "source is not approved"):
            gateway_module.QueryGateway(snapshot=snapshot)

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

    def test_located_facts_derivation_and_id_bindings_are_fail_closed(self):
        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["facts"][0].pop("normalizer")
        bundle["receipt"]["bundle_sha256"] = gateway_module._json_hash({
            "facts": bundle["facts"],
            "evidence_catalog": bundle["evidence_catalog"],
            "public_event_inputs": bundle["public_event_inputs"],
        })
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "fact is not bound"):
            gateway_module.QueryGateway(snapshot=snapshot)

        snapshot = gateway_module.load_snapshot()
        bundle = self.located_bundle()
        bundle["facts"][0]["predicate"] = "tampered_predicate"
        bundle["receipt"]["bundle_sha256"] = gateway_module._json_hash({
            "facts": bundle["facts"],
            "evidence_catalog": bundle["evidence_catalog"],
            "public_event_inputs": bundle["public_event_inputs"],
        })
        snapshot["located_facts"] = bundle
        with self.assertRaisesRegex(ValueError, "fact ID binding"):
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
