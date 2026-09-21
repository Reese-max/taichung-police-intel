#!/usr/bin/env python3
"""Line-delimited MCP stdio transport for the read-only Query Gateway."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("govintel_query_gateway", ROOT / "scripts/query-gateway.py")
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("query gateway module is unavailable")
gateway_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gateway_module)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the read-only GovIntel MCP gateway over stdio")
    parser.add_argument("--located-facts-bundle", type=Path)
    args = parser.parse_args()
    gateway = gateway_module.QueryGateway(gateway_module.load_snapshot(args.located_facts_bundle))
    for line in sys.stdin:
        if not line.strip():
            continue
        request = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise gateway_module.GatewayError("INVALID_JSON_RPC", "request must be a JSON object")
            if request.get("method") == "notifications/initialized" and "id" not in request:
                continue
            response = gateway_module.dispatch_mcp(gateway, request)
        except json.JSONDecodeError as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}}
        except gateway_module.GatewayError as error:
            if not isinstance(request, dict):
                response = {"jsonrpc": "2.0", "id": None, "error": gateway_module._error_payload(error)["error"]}
            elif "id" not in request:
                continue
            else:
                response = {"jsonrpc": "2.0", "id": request["id"], "error": gateway_module._error_payload(error)["error"]}
        print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
