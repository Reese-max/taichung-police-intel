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


def emit(response: dict) -> None:
    # JSON-RPC error codes are integers. Keep application diagnostics in data
    # so official clients can decode errors carrying a known request ID.
    error = response.get("error")
    if isinstance(error, dict) and type(error.get("code")) is not int:
        application_code = error.get("code")
        response = {**response, "error": {
            "code": {"INVALID_JSON_RPC": -32600, "METHOD_NOT_FOUND": -32601,
                     "INVALID_ARGUMENTS": -32602}.get(application_code, -32000),
            "message": error.get("message", "request failed"),
            "data": {"code": application_code},
        }}
    encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > gateway_module.MAX_RESPONSE_BYTES:
        encoded = json.dumps({
            "jsonrpc": "2.0",
            "id": response.get("id"),
            "error": {"code": -32000, "message": "response exceeds the byte limit",
                      "data": {"code": "RESPONSE_TOO_LARGE"}},
        }, ensure_ascii=False, separators=(",", ":"))
    print(encoded, flush=True)


def rate_error(request_id=None) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {
        "code": "RATE_LIMITED", "message": "request rate limit exceeded",
    }}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the read-only GovIntel MCP gateway over stdio")
    parser.add_argument("--located-facts-bundle", type=Path)
    parser.add_argument("--query-store", type=Path)
    parser.add_argument("--public-events", type=Path)
    parser.add_argument("--statistics", type=Path)
    parser.add_argument("--last-good-snapshot", type=Path)
    parser.add_argument("--rate-limit", type=int, default=gateway_module.DEFAULT_RATE_LIMIT)
    args = parser.parse_args()
    if args.rate_limit < 1:
        parser.error("--rate-limit must be a positive integer")
    limiter = gateway_module.RateLimiter(args.rate_limit)
    gateway = gateway_module.QueryGateway(
        gateway_module.load_snapshot(args.located_facts_bundle, args.query_store, args.public_events, args.statistics,
                                     last_good_path=args.last_good_snapshot)
    )
    stdin = getattr(sys.stdin, "buffer", sys.stdin)
    while True:
        raw_line = stdin.readline(gateway_module.MAX_REQUEST_BYTES + 1)
        if not raw_line:
            break
        raw_bytes = raw_line if isinstance(raw_line, bytes) else raw_line.encode("utf-8")
        has_newline = raw_bytes.endswith(b"\n")
        content_bytes = raw_bytes[:-1] if has_newline else raw_bytes
        if len(content_bytes) > gateway_module.MAX_REQUEST_BYTES:
            emit({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": "REQUEST_TOO_LARGE", "message": "request line exceeds the byte limit"},
            })
            # A poisoned line is not resynchronizable within a bounded read.
            # Close this process after one error rather than drain an arbitrary
            # stream looking for a newline. Normal EOF remains graceful.
            return 0
        try:
            line = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(error)},
            }
            emit(response if limiter.allow("stdio") else rate_error())
            continue
        if not line.strip():
            continue
        request = None
        try:
            request = json.loads(line)
            if isinstance(request, dict) and request.get("method") == "notifications/initialized" and "id" not in request:
                continue
            if not limiter.allow("stdio"):
                if not isinstance(request, dict) or "id" in request:
                    emit(rate_error(request.get("id") if isinstance(request, dict) else None))
                continue
            if not isinstance(request, dict):
                raise gateway_module.GatewayError("INVALID_JSON_RPC", "request must be a JSON object")
            response = gateway_module.dispatch_mcp(gateway, request)
        except json.JSONDecodeError as error:
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(error)}}
            if not limiter.allow("stdio"):
                response = rate_error()
        except gateway_module.GatewayError as error:
            if not isinstance(request, dict):
                response = {"jsonrpc": "2.0", "id": None, "error": gateway_module._error_payload(error)["error"]}
            elif "id" not in request:
                continue
            else:
                response = {"jsonrpc": "2.0", "id": request["id"], "error": gateway_module._error_payload(error)["error"]}
        emit(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
