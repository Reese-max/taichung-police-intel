#!/usr/bin/env python3
"""Small read-only HTTP/MCP adapter over the canonical publication snapshot."""
from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import shutil
import subprocess
import time
from typing import Any, Callable
from urllib.parse import urlsplit
import uuid

ROOT = Path(__file__).resolve().parents[1]
QUERY_STORE_PATH = ROOT / "scripts" / "query-store.py"
RETENTION_POLICY_PATH = ROOT / "scripts" / "retention-policy.py"
ANSWER_GATE_RUNNER = ROOT / "scripts" / "answer-gate-runner.mjs"
MAX_REQUEST_BYTES = 64 * 1024
SERVER_VERSION = "query-gateway-v1"
DEFAULT_RATE_LIMIT = 60

_query_store_spec = importlib.util.spec_from_file_location("govintel_query_store", QUERY_STORE_PATH)
if _query_store_spec is None or _query_store_spec.loader is None:
    raise RuntimeError("query store module is unavailable")
qs = importlib.util.module_from_spec(_query_store_spec)
_query_store_spec.loader.exec_module(qs)
_retention_policy_spec = importlib.util.spec_from_file_location("govintel_retention_policy", RETENTION_POLICY_PATH)
if _retention_policy_spec is None or _retention_policy_spec.loader is None:
    raise RuntimeError("retention policy module is unavailable")
retention_policy_module = importlib.util.module_from_spec(_retention_policy_spec)
_retention_policy_spec.loader.exec_module(retention_policy_module)
RETENTION_POLICY = retention_policy_module.compile_policy()

CAPABILITIES = (
    "search_evidence",
    "get_current_brief",
    "get_source_health",
    "validate_answer",
)
UNIMPLEMENTED = frozenset({
    "search_events",
    "get_event",
    "compare_event_versions",
    "query_statistics",
})

MCP_TOOLS = [
    {
        "name": "search_evidence",
        "description": "Search approved publication metadata; this is not full-text or PublicEvent search.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "q": {"type": "string", "maxLength": 512},
                "source_id": {"type": "string", "maxLength": 64},
                "change_type": {"type": "string", "maxLength": 64},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": {"type": "string", "maxLength": 1024},
                "expected_generation": {"type": "string", "maxLength": 128},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "get_current_brief",
        "description": "Read the checked-in canonical brief with its freshness and publication receipt.",
        "inputSchema": {"type": "object", "additionalProperties": False, "properties": {}},
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "get_source_health",
        "description": "Read approved source health, freshness, completeness, and gaps.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"source_id": {"type": "string", "maxLength": 64}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "validate_answer",
        "description": "Validate structured answer claims against the server-controlled canonical evidence catalog.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["claims"],
            "properties": {
                "claims": {"type": "array", "maxItems": 32, "items": {"type": "object"}},
                "expected_generation": {"type": "string", "maxLength": 128},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
]


class GatewayError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class RateLimiter:
    """Process-local per-client cap; replace with shared enforcement when horizontally deployed."""

    def __init__(self, limit: int = DEFAULT_RATE_LIMIT, window_seconds: int = 60):
        if type(limit) is not int or limit < 1:
            raise ValueError("rate limit must be a positive integer")
        self.limit = limit
        self.window_seconds = window_seconds
        self.hits: dict[str, deque[float]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        stamp = time.monotonic() if now is None else now
        hits = self.hits.setdefault(key, deque())
        while hits and stamp - hits[0] >= self.window_seconds:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(stamp)
        return True


def load_snapshot() -> dict[str, Any]:
    feed, feed_hash = qs.load_json(qs.DEFAULT_FEED)
    status, status_hash = qs.load_json(qs.DEFAULT_STATUS)
    brief, brief_hash = qs.load_json(qs.DEFAULT_BRIEF)
    store = qs.build_store(
        feed,
        status,
        brief,
        {"feed": feed_hash, "status": status_hash, "brief": brief_hash},
    )
    return {"store": store, "status": status, "brief": brief}


def _json_hash(value: Any) -> str:
    return hashlib.sha256(qs.canonical_json(value)).hexdigest()


def _now(value: datetime | None) -> datetime:
    result = datetime.now(timezone.utc) if value is None else value
    if result.tzinfo is None:
        raise ValueError("gateway clock must be timezone-aware")
    return result.astimezone(timezone.utc)


def _freshness(data_status: str) -> str:
    return {
        "SNAPSHOT_RECENT": "RECENT",
        "SOURCE_NOT_AVAILABLE": "UNKNOWN",
    }.get(data_status, data_status)


def _verification_summary(data_status: str) -> str:
    return {
        "RECENT": "僅代表已核對的公開快照範圍，不代表所有現實事件。",
        "STALE": "公開快照已過期，不能宣稱目前最新，也不能用零結果代表沒有事件。",
        "PARTIAL": "監測或發布範圍不完整，不能用零結果排除其他事件。",
        "UNKNOWN": "資料時效或完整性無法核對，不能把結果解讀成完整現況。",
        "SOURCE_NOT_AVAILABLE": "指定來源不在核准快照，不能把結果解讀成沒有資料。",
    }.get(data_status, "資料狀態未能核對，不能作出完整現況結論。")


def _public_source(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "source_id", "source_name", "source_url", "source_health", "window_completeness",
        "result", "freshness_status", "data_as_of", "last_checked_at", "last_success_at",
        "intelligence_gaps", "current_source_run_id", "manifest_sha256",
    )
    return {key: row.get(key) for key in fields if key in row}


class QueryGateway:
    def __init__(
        self,
        snapshot: dict[str, Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ):
        self.snapshot = load_snapshot() if snapshot is None else snapshot
        self.store = self.snapshot["store"]
        self.status = self.snapshot["status"]
        self.brief = self.snapshot["brief"]
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _scope(
        self,
        source_id: str | None = None,
        now: datetime | None = None,
        capability_id: str = "publication_metadata",
        expected_generation: str | None = None,
    ) -> dict[str, Any]:
        return qs.query_store(
            self.store,
            source_id=source_id,
            limit=1,
            now=_now(now),
            capability_id=capability_id,
            expected_generation=expected_generation,
        )

    @staticmethod
    def _trusted_evidence_catalog(
        store: dict[str, Any],
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        sources = {source["source_id"]: source for source in store["sources"]}
        source_status = {
            source_id: qs.assess_scope(store, source_id, _now(now))[0]
            for source_id in sources
        } if now is not None else {}
        catalog = []
        for item in store["items"]:
            source = sources.get(item["source_id"], {})
            freshness = str(item.get("freshness_status") or source.get("freshness_status") or "UNKNOWN").upper()
            current = (
                (not source_status or source_status.get(item["source_id"]) == "SNAPSHOT_RECENT")
                and
                source.get("source_health") == "PASS"
                and source.get("window_completeness") in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
                and freshness in {"FRESH", "RECENT"}
            )
            canonical_id = item["canonical_id"]
            official_url = item.get("official_url")
            if not isinstance(official_url, str) or not official_url.startswith("https://"):
                continue
            catalog.append({
                "schema_version": 1,
                "evidence_id": f"PUB-{canonical_id}",
                "evidence_type": "WRITTEN_OFFICIAL",
                "source_id": item["source_id"],
                "locator": f"{official_url}#publication:{canonical_id}",
                "document_version": item["content_sha256"],
                "content_sha256": item["content_sha256"],
                "trust_tier": item["trust_tier"],
                "verification_status": "CONFIRMED_OFFICIAL",
                "freshness": freshness,
                "is_current": current,
                "published_at": item.get("published_at") or item.get("data_as_of") or item.get("fetched_at"),
                "assertions": [
                    {"subject": f"publication:{canonical_id}:title", "value": item["title"]},
                    {"subject": f"publication:{canonical_id}:source_id", "value": item["source_id"]},
                ],
            })
        return sorted(catalog, key=lambda row: row["evidence_id"])

    def _run_answer_gate(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        node = shutil.which("node")
        if not node:
            raise GatewayError("GATE_UNAVAILABLE", "answer evidence gate runtime is unavailable", 503)
        evidence = self._trusted_evidence_catalog(self.store, self.clock())
        catalog_hash = _json_hash(evidence)
        payload = {
            "claims": claims,
            "evidence": evidence,
            "generated_at": self.brief.get("generated_at"),
            "publication_hash": self.store["generated_from"]["brief_sha256"],
            "evidence_catalog_hash": catalog_hash,
        }
        try:
            # ponytail: per-request Node subprocess keeps the shared JS gate authoritative; move to a long-lived service if throughput matters.
            result = subprocess.run(
                [node, str(ANSWER_GATE_RUNNER)],
                cwd=ROOT,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GatewayError("GATE_UNAVAILABLE", "answer evidence gate did not complete", 503) from error
        if result.returncode != 0:
            detail = (result.stderr or "answer evidence gate failed").strip()[:256]
            raise GatewayError("GATE_FAILED", detail, 503)
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise GatewayError("GATE_FAILED", "answer evidence gate returned invalid JSON", 503) from error
        receipt = output.get("receipt") if isinstance(output, dict) else None
        if not isinstance(receipt, dict) or receipt.get("publication_hash") != payload["publication_hash"] or receipt.get("evidence_catalog_hash") != catalog_hash:
            raise GatewayError("GATE_FAILED", "answer evidence receipt is not bound to this publication", 503)
        return output

    def _envelope(
        self,
        tool: str,
        arguments: dict[str, Any],
        scope: dict[str, Any],
        payload: dict[str, Any],
        *,
        result_count: int = 0,
        truncated: bool = False,
        result_type: str = "publication_metadata",
    ) -> dict[str, Any]:
        queried_at = scope.get("queried_at") or _now(None).isoformat()
        data_status = scope["data_status"]
        publication = self.store["generated_from"]
        return {
            "schema_version": 1,
            "query_id": uuid.uuid4().hex,
            "tool_name": tool,
            "publication_id": publication["collection_run_id"],
            "publication_hash": publication["brief_sha256"],
            "query_generation_id": self.store["generation_id"],
            "generated_at": self.brief["generated_at"],
            "queried_at": queried_at,
            "freshness": _freshness(data_status),
            "verification_summary": _verification_summary(data_status),
            "event_ids": [],
            "evidence_ids": [],
            "source_gaps": scope["source_gaps"],
            "query_coverage": scope["query_coverage"],
            "discovery_unverified_count": 0,
            "truncated": truncated,
            "policy": self.store["policy"],
            "retention": {
                "policy_version": RETENTION_POLICY["policy_version"],
                "policy_hash": RETENTION_POLICY["policy_hash"],
                "public_projection": "METADATA_LINK_ONLY",
                "full_text_allowed": False,
            },
            "result_type": result_type,
            "receipt": {
                "schema_version": 1,
                "tool_name": tool,
                "arguments_sha256": _json_hash(arguments),
                "publication_hash": publication["brief_sha256"],
                "query_generation_id": self.store["generation_id"],
                "result_count": result_count,
                "truncated": truncated,
                "server_version": SERVER_VERSION,
                "issued_at": queried_at,
            },
            **payload,
        }

    @staticmethod
    def _arguments(tool: str, arguments: Any) -> dict[str, Any]:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise GatewayError("INVALID_ARGUMENTS", "arguments must be an object")
        allowed = {
            "search_evidence": {"q", "source_id", "change_type", "limit", "cursor", "expected_generation"},
            "get_current_brief": set(),
            "get_source_health": {"source_id"},
            "validate_answer": {"claims", "expected_generation"},
        }.get(tool)
        if allowed is None:
            raise GatewayError(
                "CAPABILITY_NOT_AVAILABLE",
                f"{tool} is not implemented; available capabilities: {', '.join(CAPABILITIES)}",
                422,
            )
        unknown = sorted(set(arguments) - allowed)
        if unknown:
            raise GatewayError("INVALID_ARGUMENTS", f"unsupported argument(s): {', '.join(unknown)}")
        return dict(arguments)

    def execute(self, tool: str, arguments: Any = None) -> dict[str, Any]:
        args = self._arguments(tool, arguments)
        now = _now(self.clock())
        if tool == "validate_answer":
            claims = args.get("claims")
            if not isinstance(claims, list) or not 1 <= len(claims) <= 32:
                raise GatewayError("INVALID_ARGUMENTS", "validate_answer requires 1 to 32 structured claims")
            claim_keys = {
                "schema_version", "claim_id", "text", "claim_type", "temporal_scope",
                "proposition", "cited_evidence_ids",
            }
            if any(not isinstance(claim, dict) or set(claim) - claim_keys for claim in claims):
                raise GatewayError("INVALID_ARGUMENTS", "claims may not include evidence or trust fields")
            scope = self._scope(
                now=now,
                capability_id="publication_metadata",
                expected_generation=args.get("expected_generation"),
            )
            gate = self._run_answer_gate(claims)
            gate_receipt = gate.get("receipt")
            if not isinstance(gate_receipt, dict):
                raise GatewayError("GATE_FAILED", "answer evidence receipt is missing", 503)
            return self._envelope(
                tool,
                args,
                scope,
                {
                    "gate_status": gate.get("gate_status"),
                    "answer": gate.get("answer", []),
                    "final_claims": gate.get("final_claims", []),
                    "evidence_ids": gate_receipt.get("evidence_ids", []),
                    "answer_evidence_receipt": gate_receipt,
                },
                result_count=len(gate.get("final_claims", [])),
                result_type="answer_evidence",
            )
        if tool == "search_evidence":
            query_args = {"text": args.get("q"), **{key: value for key, value in args.items() if key != "q"}}
            result = qs.query_store(self.store, now=now, **query_args)
            return self._envelope(
                tool,
                args,
                result,
                {
                    "search_scope": result["search_scope"],
                    "answerable_no_match": result["answerable_no_match"],
                    "total_matches": result["total_matches"],
                    "result_count": result["result_count"],
                    "offset": result["offset"],
                    "has_more": result["has_more"],
                    "next_cursor": result["next_cursor"],
                    "results": result["results"],
                },
                result_count=result["result_count"],
                truncated=result["truncated"],
            )
        if tool == "get_current_brief":
            scope = self._scope(now=now, capability_id="publication_metadata")
            allowed = (
                "schema_version", "mode", "generator_version", "generated_at", "source_collection_run_id",
                "source_status_generated_at", "publication_status", "snapshot_complete", "status_message",
                "overview", "priority_items", "tracking_items", "other_changes", "source_health",
            )
            brief = {key: self.brief.get(key) for key in allowed}
            return self._envelope(
                tool,
                args,
                scope,
                {
                    "current_as_of_server_clock": scope["data_status"] == "SNAPSHOT_RECENT",
                    "brief": brief,
                },
                result_count=1,
            )
        source_id = args.get("source_id")
        scope = self._scope(source_id=source_id, now=now, capability_id="source_health")
        selected = [
            _public_source(row)
            for row in self.status.get("sources", [])
            if source_id is None or row.get("source_id") == source_id
        ]
        return self._envelope(
            tool,
            args,
            scope,
            {"sources": selected},
            result_count=len(selected),
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "server_version": SERVER_VERSION,
            "read_only": True,
            "capabilities": list(CAPABILITIES),
            "unavailable_capabilities": sorted(UNIMPLEMENTED),
            "policy": self.store["policy"],
            "retention": {
                "policy_version": RETENTION_POLICY["policy_version"],
                "policy_hash": RETENTION_POLICY["policy_hash"],
                "public_projection": "METADATA_LINK_ONLY",
                "full_text_allowed": False,
            },
        }

    def health(self) -> dict[str, Any]:
        scope = self._scope()
        return {
            "schema_version": 1,
            "service": "govintel-query-gateway",
            "server_version": SERVER_VERSION,
            "status": "ok",
            "publication_freshness": _freshness(scope["data_status"]),
            "publication_id": self.store["generated_from"]["collection_run_id"],
            "publication_hash": self.store["generated_from"]["brief_sha256"],
            "query_coverage": scope["query_coverage"],
            "policy": self.store["policy"],
            "retention": {
                "policy_version": RETENTION_POLICY["policy_version"],
                "policy_hash": RETENTION_POLICY["policy_hash"],
                "public_projection": "METADATA_LINK_ONLY",
                "full_text_allowed": False,
            },
            "source_gaps": scope["source_gaps"],
            "read_only": True,
        }


def _error_payload(error: GatewayError) -> dict[str, Any]:
    return {"schema_version": 1, "error": {"code": error.code, "message": error.message}}


def dispatch_mcp(gateway: QueryGateway, request: dict[str, Any]) -> dict[str, Any]:
    if request.get("jsonrpc") != "2.0" or "id" not in request:
        raise GatewayError("INVALID_JSON_RPC", "request must be JSON-RPC 2.0 with an id")
    request_id = request["id"]
    method = request.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "govintel-query-gateway", "version": SERVER_VERSION},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": MCP_TOOLS}}
    if method != "tools/call":
        raise GatewayError("METHOD_NOT_FOUND", f"unsupported MCP method: {method}", 404)
    params = request.get("params")
    if not isinstance(params, dict) or not isinstance(params.get("name"), str):
        raise GatewayError("INVALID_ARGUMENTS", "tools/call requires params.name")
    try:
        payload = gateway.execute(params["name"], params.get("arguments"))
    except GatewayError as error:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": json.dumps(_error_payload(error), ensure_ascii=False)}],
            },
        }
    except (TypeError, ValueError) as error:
        gateway_error = GatewayError("INVALID_ARGUMENTS", str(error))
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "isError": True,
                "content": [{"type": "text", "text": json.dumps(_error_payload(gateway_error), ensure_ascii=False)}],
            },
        }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "isError": False,
            "content": [{"type": "text", "text": encoded}],
            "structuredContent": payload,
        },
    }


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "GovIntelQueryGateway/1"

    def _send(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        allow_origin = getattr(self.server, "allow_origin", None)
        if allow_origin:
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Vary", "Origin")
        self.end_headers()
        self.wfile.write(body)

    def _within_rate_limit(self) -> bool:
        limiter = getattr(self.server, "rate_limiter", None)
        if limiter is None or limiter.allow(self.client_address[0]):
            return True
        self._send(_error_payload(GatewayError("RATE_LIMITED", "request rate limit exceeded", 429)), 429)
        return False

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError as error:
            raise GatewayError("INVALID_REQUEST", "Content-Length must be an integer") from error
        if length < 0 or length > MAX_REQUEST_BYTES:
            raise GatewayError("REQUEST_TOO_LARGE", f"request body must be <= {MAX_REQUEST_BYTES} bytes", 413)
        try:
            value = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GatewayError("INVALID_JSON", "request body must be valid UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise GatewayError("INVALID_REQUEST", "request body must be an object")
        return value

    def do_OPTIONS(self) -> None:
        allow_origin = getattr(self.server, "allow_origin", None)
        self.send_response(204)
        if allow_origin:
            self.send_header("Access-Control-Allow-Origin", allow_origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Vary", "Origin")
        self.end_headers()

    def do_GET(self) -> None:
        if not self._within_rate_limit():
            return
        gateway = self.server.gateway
        path = urlsplit(self.path).path
        try:
            if path == "/health":
                self._send(gateway.health())
            elif path == "/capabilities":
                self._send(gateway.capabilities())
            else:
                raise GatewayError("NOT_FOUND", "unknown gateway route", 404)
        except GatewayError as error:
            self._send(_error_payload(error), error.status)

    def do_POST(self) -> None:
        if not self._within_rate_limit():
            return
        gateway = self.server.gateway
        path = urlsplit(self.path).path
        try:
            body = self._body()
            if path == "/query":
                tool = body.get("tool")
                if not isinstance(tool, str):
                    raise GatewayError("INVALID_ARGUMENTS", "query requires a string tool")
                self._send(gateway.execute(tool, body.get("arguments")))
            elif path == "/mcp":
                self._send(dispatch_mcp(gateway, body))
            else:
                raise GatewayError("NOT_FOUND", "unknown gateway route", 404)
        except GatewayError as error:
            self._send(_error_payload(error), error.status)
        except (TypeError, ValueError) as error:
            self._send(_error_payload(GatewayError("INVALID_ARGUMENTS", str(error))), 400)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep request bodies and query text out of the service log.
        super().log_message(format, *args)


class ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def build_server(
    host: str,
    port: int,
    gateway: QueryGateway,
    allow_origin: str | None = None,
    rate_limit: int = DEFAULT_RATE_LIMIT,
) -> HTTPServer:
    server = ReusableHTTPServer((host, port), GatewayHandler)
    server.gateway = gateway
    server.allow_origin = allow_origin
    server.rate_limiter = RateLimiter(rate_limit)
    return server


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the read-only GovIntel Query Gateway")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--allow-origin", default=None)
    parser.add_argument("--rate-limit", type=int, default=DEFAULT_RATE_LIMIT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = build_server(args.host, args.port, QueryGateway(), args.allow_origin, args.rate_limit)
    print(f"QUERY_GATEWAY_LISTENING http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
