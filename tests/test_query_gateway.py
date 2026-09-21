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
    @classmethod
    def setUpClass(cls):
        clock = lambda: datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc)
        cls.gateway = gateway_module.QueryGateway(clock=clock)
        cls.server = gateway_module.build_server("127.0.0.1", 0, cls.gateway, "*")
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    @classmethod
    def request(cls, method, path, payload=None):
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            cls.base_url + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
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


if __name__ == "__main__":
    unittest.main()
