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
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "get_current_brief", "arguments": {}}},
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
        self.assertEqual(response["error"]["code"], "INVALID_JSON_RPC")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
