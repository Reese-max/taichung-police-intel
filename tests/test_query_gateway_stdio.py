import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "query-gateway-stdio.py"


class QueryGatewayStdioTests(unittest.TestCase):
    def test_mcp_stdio_reuses_read_only_gateway_contract(self):
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_source_health", "arguments": {}}},
        ]
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT)],
            cwd=ROOT,
            input="\n".join(json.dumps(request, ensure_ascii=False) for request in requests) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([response["id"] for response in responses], [1, 2, 3])
        self.assertTrue(all(response["jsonrpc"] == "2.0" for response in responses))
        self.assertTrue(all(tool["annotations"]["readOnlyHint"] for tool in responses[1]["result"]["tools"]))
        self.assertFalse(responses[2]["result"]["isError"])
        self.assertTrue(responses[2]["result"]["structuredContent"]["publication_hash"])
        self.assertEqual(len(responses[2]["result"]["structuredContent"]["sources"]), 5)

    def test_invalid_json_is_a_protocol_error_without_gateway_traceback(self):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT)],
            cwd=ROOT,
            input="{not-json}\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["error"]["code"], -32700)
        self.assertEqual(result.stderr, "")

    def test_non_object_json_is_rejected(self):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT)],
            cwd=ROOT,
            input="[]\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["error"]["code"], -32600)
        self.assertEqual(response["error"]["data"]["code"], "INVALID_JSON_RPC")
        self.assertEqual(result.stderr, "")

    def test_oversized_request_line_is_rejected(self):
        result = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT)],
            cwd=ROOT,
            input=json.dumps({"jsonrpc": "2.0", "id": 4, "method": "initialize", "padding": "x" * 70000}) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["error"]["code"], -32000)
        self.assertEqual(response["error"]["data"]["code"], "REQUEST_TOO_LARGE")
        self.assertEqual(result.stderr, "")

    def test_rate_limit_counts_requests_but_not_initialized_notification(self):
        requests = [
            {"jsonrpc": "2.0", "id": 0, "method": "initialize"},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--rate-limit", "2"],
                                input="".join(json.dumps(row) + "\n" for row in requests),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["id"] for row in responses], [0, 1, 2])
        self.assertEqual(len(responses[1]["result"]["tools"]), 5)
        self.assertEqual(responses[2]["error"]["code"], -32000)
        self.assertEqual(responses[2]["error"]["data"]["code"], "RATE_LIMITED")
        self.assertEqual(result.stderr, "")

    def test_default_rate_limit_rejects_actual_sixty_one_request_burst(self):
        requests = [{"jsonrpc": "2.0", "id": 0, "method": "initialize"}]
        requests.extend({"jsonrpc": "2.0", "id": i, "method": "tools/list"} for i in range(1, 62))
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)],
                                input="".join(json.dumps(row) + "\n" for row in requests),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(responses), 62)
        self.assertEqual(sum("result" in row for row in responses), 60)
        self.assertEqual([row["id"] for row in responses if "error" in row], [60, 61])
        self.assertTrue(all(row["error"]["data"]["code"] == "RATE_LIMITED"
                            for row in responses if "error" in row))
        self.assertEqual(result.stderr, "")

    def test_oversized_unterminated_input_closes_without_draining_open_stream(self):
        process = subprocess.Popen([sys.executable, "-B", str(SCRIPT)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.stdin.write(b"x" * (64 * 1024 + 1))
            process.stdin.flush()  # Keep stdin open; old code waits for newline/EOF.
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.fail("oversized input was drained without a bounded channel close")
            self.assertEqual(process.returncode, 0)
            response = json.loads(process.stdout.read())
            self.assertEqual(response["error"]["data"]["code"], "REQUEST_TOO_LARGE")
            self.assertEqual(process.stderr.read(), b"")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    def test_malformed_tool_request_has_standard_json_rpc_error(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT)],
                                input=json.dumps({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                                                  "params": {"name": 123}}) + "\n",
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        response = json.loads(result.stdout)
        self.assertEqual(response["id"], 9)
        self.assertEqual(response["error"]["code"], -32602)
        self.assertEqual(response["error"]["data"]["code"], "INVALID_ARGUMENTS")
        self.assertEqual(result.stderr, "")

    def test_malformed_json_and_non_object_messages_share_request_budget(self):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), "--rate-limit", "2"],
                                input="{broken}\n[]\n{broken}\n", text=True,
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([row["error"]["code"] for row in responses], [-32700, -32600, -32000])
        self.assertEqual(responses[2]["error"]["data"]["code"], "RATE_LIMITED")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
