"""Verify the anonymous Pages -> Worker query release binding."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 2 * 1024 * 1024
REQUIRED_CAPABILITIES = {
    "search_evidence",
    "get_current_brief",
    "get_publication_receipt",
    "get_source_health",
    "validate_answer",
}
ARTIFACTS = ("intelligence-feed.json", "source-status.json", "v2-daily-brief.json")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_base(value: str, *, pages: bool = False) -> str:
    parts = urlsplit(value.rstrip("/"))
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("production URL must be an HTTPS origin without credentials, query, or fragment")
    if pages:
        if (parts.hostname or "").lower() != "reese-max.github.io" or parts.path.rstrip("/") != "/taichung-police-intel":
            raise ValueError("public URL is not the approved GovIntel Pages origin")
    return value.rstrip("/")


class JsonClient:
    def __init__(self):
        self.opener = build_opener(NoRedirect)

    def request(self, url: str, *, method: str = "GET", payload: dict | None = None) -> tuple[int, dict, bytes]:
        body = None
        headers = {
            "Accept": "application/json",
            "Origin": "https://reese-max.github.io",
            "User-Agent": "Mozilla/5.0 (compatible; GovIntelProductionSmoke/1.0)",
        }
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(url, data=body, method=method, headers=headers)
        try:
            response = self.opener.open(request, timeout=20)
            status = response.status
            raw = response.read(MAX_BYTES + 1)
        except HTTPError as error:
            raise RuntimeError(f"{url} returned HTTP {error.code}") from error
        if status != 200:
            raise RuntimeError(f"{url} returned HTTP {status}")
        if len(raw) > MAX_BYTES:
            raise RuntimeError(f"{url} exceeded the response byte budget")
        try:
            return status, json.loads(raw.decode("utf-8")), raw
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"{url} did not return JSON") from error


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def verify(gateway_url: str, public_url: str, client: JsonClient | None = None) -> dict:
    gateway_url = checked_base(gateway_url)
    public_url = checked_base(public_url, pages=True)
    client = client or JsonClient()

    _, health, _ = client.request(f"{gateway_url}/health")
    if health.get("status") != "ok" or not health.get("publication_id"):
        raise RuntimeError("Worker health receipt is not usable")

    _, capabilities, _ = client.request(f"{gateway_url}/capabilities")
    actual_capabilities = set(capabilities.get("capabilities", []))
    if not REQUIRED_CAPABILITIES.issubset(actual_capabilities):
        missing = sorted(REQUIRED_CAPABILITIES - actual_capabilities)
        raise RuntimeError(f"Worker capabilities missing: {', '.join(missing)}")

    public_hashes = {}
    public_docs = {}
    for name in ARTIFACTS:
        _, document, raw = client.request(f"{public_url}/data/{name}")
        public_hashes[name.removesuffix(".json")] = sha256(raw)
        public_docs[name] = document

    _, publication_query, _ = client.request(
        f"{gateway_url}/query",
        method="POST",
        payload={"tool": "get_publication_receipt", "arguments": {}},
    )
    receipt = publication_query.get("publication_receipt")
    if not isinstance(receipt, dict):
        raise RuntimeError("Worker publication receipt is missing")
    expected_hashes = {
        "feed": public_hashes["intelligence-feed"],
        "status": public_hashes["source-status"],
        "brief": public_hashes["v2-daily-brief"],
    }
    if receipt.get("artifact_hashes") != expected_hashes or receipt.get("publication_hash") != expected_hashes["brief"]:
        raise RuntimeError("Worker and public publication hashes do not match")
    if receipt.get("publication_id") != health.get("publication_id"):
        raise RuntimeError("Worker health and publication receipt use different publication IDs")

    _, search, _ = client.request(
        f"{gateway_url}/query",
        method="POST",
        payload={"tool": "search_evidence", "arguments": {"q": "", "limit": 1}},
    )
    if search.get("tool_name") != "search_evidence" or not isinstance(search.get("receipt"), dict):
        raise RuntimeError("Worker search_evidence smoke did not return a receipt")

    _, initialize, _ = client.request(
        f"{gateway_url}/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
    )
    if initialize.get("result", {}).get("protocolVersion") != "2025-06-18":
        raise RuntimeError("MCP initialize handshake failed")
    _, tools_list, _ = client.request(
        f"{gateway_url}/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    tool_names = {item.get("name") for item in tools_list.get("result", {}).get("tools", [])}
    if not REQUIRED_CAPABILITIES.issubset(tool_names):
        raise RuntimeError("MCP tools/list is missing a production capability")

    return {
        "schema_version": 1,
        "kind": "GOVINTEL_PRODUCTION_QUERY_RECEIPT",
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "gateway_url": gateway_url,
        "public_url": public_url,
        "worker_version": health.get("server_version"),
        "publication_id": receipt.get("publication_id"),
        "publication_hash": receipt.get("publication_hash"),
        "artifact_hashes": expected_hashes,
        "query_generation_id": receipt.get("generation_id"),
        "publication_freshness": health.get("publication_freshness"),
        "capabilities": sorted(actual_capabilities),
        "checks": {"health": "SUCCESS", "capabilities": "SUCCESS", "hash_binding": "SUCCESS", "query": "SUCCESS", "mcp": "SUCCESS"},
        "public_generation": public_docs["v2-daily-brief.json"].get("source_collection_run_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-url", default=os.environ.get("QUERY_GATEWAY_URL", ""))
    parser.add_argument("--public-base-url", default=os.environ.get("PUBLICATION_BASE_URL", ""))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    last_error = None
    for attempt in range(3):
        try:
            receipt = verify(args.gateway_url, args.public_base_url)
            receipt["attempt"] = attempt + 1
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print("PRODUCTION_QUERY_RECEIPT_OK " + json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            return 0
        except (OSError, RuntimeError, ValueError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(5)
    print(f"PRODUCTION_QUERY_RECEIPT_FAIL {last_error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
