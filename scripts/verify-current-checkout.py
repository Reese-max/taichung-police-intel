#!/usr/bin/env python3
"""Verify the current checkout's bounded Query Gateway over ephemeral HTTP."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
GATEWAY_PATH = ROOT / "scripts" / "query-gateway.py"
LOCKFILE = ROOT / "apps" / "web" / "package-lock.json"
REQUIRED_FRESHNESS = {"RECENT", "STALE", "PARTIAL", "UNKNOWN"}

spec = importlib.util.spec_from_file_location("current_checkout_gateway", GATEWAY_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError("cannot load current checkout Query Gateway")
gateway_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway_module)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def request(base_url: str, method: str, path: str, payload=None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_value = Request(
        base_url + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request_value, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def run(output: Path | None = None) -> dict:
    gateway = gateway_module.QueryGateway()
    server = gateway_module.build_server("127.0.0.1", 0, gateway, "*")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        health_status, health = request(base_url, "GET", "/health")
        query_args = {"limit": 5}
        query_status, query = request(base_url, "POST", "/query", {
            "tool": "search_evidence",
            "arguments": query_args,
        })
        mcp_status, mcp = request(base_url, "POST", "/mcp", {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_evidence", "arguments": query_args},
        })
        unsupported_status, unsupported = request(base_url, "POST", "/query", {
            "tool": "search_events", "arguments": {},
        })
        generation_status, generation_error = request(base_url, "POST", "/query", {
            "tool": "search_evidence",
            "arguments": {"expected_generation": "old-generation"},
        })
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    if health_status != 200 or health.get("service") != "govintel-query-gateway":
        raise RuntimeError("current checkout health probe failed")
    if query_status != 200 or mcp_status != 200 or mcp.get("result", {}).get("isError"):
        raise RuntimeError("current checkout query/MCP probe failed")
    structured = mcp["result"]["structuredContent"]
    query_ids = [row["canonical_id"] for row in query.get("results", [])]
    mcp_ids = [row["canonical_id"] for row in structured.get("results", [])]
    if query_ids != mcp_ids or query.get("publication_hash") != structured.get("publication_hash"):
        raise RuntimeError("current checkout Web/MCP parity failed")
    if unsupported_status != 422 or unsupported.get("error", {}).get("code") != "CAPABILITY_NOT_AVAILABLE":
        raise RuntimeError("unsupported capability did not fail closed")
    if generation_status != 400 or generation_error.get("error", {}).get("code") != "INVALID_ARGUMENTS":
        raise RuntimeError("generation pin did not fail closed")
    if query.get("freshness") not in REQUIRED_FRESHNESS:
        raise RuntimeError("query freshness is outside the versioned envelope")

    receipt = {
        "schema_version": 1,
        "candidate_id": f"CURRENT_CHECKOUT-{git('rev-parse', 'HEAD')[:12]}",
        "code_sha": git("rev-parse", "HEAD"),
        "dirty_paths": [line for line in git("status", "--porcelain").splitlines() if line],
        "dependency_lock": {
            "path": "apps/web/package-lock.json",
            "sha256": sha256_file(LOCKFILE),
        },
        "enabled_capabilities": list(gateway_module.CAPABILITIES),
        "publication": {
            "id": query["publication_id"],
            "hash": query["publication_hash"],
            "generation_id": query["query_generation_id"],
            "freshness": query["freshness"],
        },
        "source_policy": query["policy"],
        "test_mode": "same-checkout-ephemeral-http",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "health_http_status": health_status,
            "query_http_status": query_status,
            "mcp_http_status": mcp_status,
            "web_mcp_canonical_ids_match": True,
            "unsupported_capability_status": unsupported_status,
            "generation_pin_status": generation_status,
            "static_export_configured": 'output: "export"' in (ROOT / "apps" / "web" / "next.config.mjs").read_text(encoding="utf-8"),
            "static_fallback_runtime_probe": "NOT_RUN",
        },
    }
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    receipt = run(args.output)
    print(
        "CURRENT_CHECKOUT_VERIFY_OK "
        f"candidate={receipt['candidate_id']} "
        f"generation={receipt['publication']['generation_id'][:12]} "
        f"freshness={receipt['publication']['freshness']} "
        f"capabilities={len(receipt['enabled_capabilities'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
