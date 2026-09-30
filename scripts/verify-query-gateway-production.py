"""Verify the anonymous Pages -> Worker query release binding."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
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
RELEASE_FIELDS = ("schema_version", "release_id", "code_sha", "publication_generation", "publication_hash",
                  "artifact_hashes", "source_policy_hash", "query_generation", "evidence_catalog_hash", "built_at")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def checked_base(value: str, *, pages: bool = False) -> str:
    parts = urlsplit(value.rstrip("/"))
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("production URL must be an HTTPS origin without credentials, query, or fragment")
    host = (parts.hostname or "").lower().rstrip(".")
    try:
        local = not ipaddress.ip_address(host).is_global
    except ValueError:
        try:
            # inet_aton handles legacy numeric IPv4 forms without DNS or network I/O.
            local = not ipaddress.ip_address(socket.inet_aton(host)).is_global
        except OSError:
            local = host == "localhost" or host.endswith((".localhost", ".local"))
    if local:
        raise ValueError("production URL cannot use a loopback or private host")
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
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"{url} did not return JSON") from error
        if not isinstance(document, dict):
            raise RuntimeError(f"{url} did not return a JSON object")
        return status, document, raw


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def check_release(document: dict, release: dict, worker_version: str | None = None) -> str:
    if not isinstance(document, dict):
        raise RuntimeError("Worker release response is not an object")
    bound = document.get("release")
    if not isinstance(bound, dict) or any(bound.get(field) != release[field] for field in RELEASE_FIELDS):
        raise RuntimeError("Worker and Pages release binding mismatch")
    version = bound.get("worker_version")
    if not isinstance(version, str) or not version.strip() or (worker_version and version != worker_version):
        raise RuntimeError("Worker release version is missing or changed during verification")
    return version


def check_query(document: dict, release: dict, worker_version: str) -> None:
    check_release(document, release, worker_version)
    if (document.get("query_generation_id") != release["query_generation"] or
            document.get("receipt", {}).get("query_generation_id") != release["query_generation"] or
            document.get("receipt", {}).get("publication_hash") != release["publication_hash"] or
            document.get("publication_hash") != release["publication_hash"] or
            document.get("publication_id") != release["publication_generation"] or
            document.get("policy", {}).get("policy_hash") != release["source_policy_hash"]):
        raise RuntimeError("Query receipt does not match the release")


def verify(gateway_url: str, public_url: str, client: JsonClient | None = None, *,
           pages_deployment: str | None = None, deployed_at: str | None = None,
           expected_code_sha: str | None = None) -> dict:
    gateway_url = checked_base(gateway_url)
    public_url = checked_base(public_url, pages=True)
    anonymous_http = client is None
    client = client or JsonClient()

    _, release, _ = client.request(f"{public_url}/data/release.json")
    if not isinstance(release, dict) or release.get("schema_version") != 1 or any(not release.get(field) for field in RELEASE_FIELDS):
        raise RuntimeError("Pages release manifest is missing or incomplete")
    for field, length in (("release_id", 64), ("code_sha", 40), ("publication_hash", 64),
                          ("source_policy_hash", 64), ("query_generation", 64), ("evidence_catalog_hash", 64)):
        if not isinstance(release[field], str) or not re.fullmatch(r"[a-f0-9]{%d}" % length, release[field]):
            raise RuntimeError(f"Pages release {field} is invalid")
    if expected_code_sha and release["code_sha"] != expected_code_sha:
        raise RuntimeError("Pages release code SHA does not match this workflow")
    if pages_deployment is not None and (not isinstance(pages_deployment, str) or not re.fullmatch(
            r"https://github\.com/Reese-max/taichung-police-intel/actions/runs/[1-9][0-9]*/attempts/[1-9][0-9]*", pages_deployment)):
        raise RuntimeError("release Pages deployment must identify this repository's workflow attempt")
    timestamps = []
    for value in (release["built_at"], deployed_at):
        if value is not None:
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    raise ValueError("missing timezone")
                timestamps.append(stamp)
            except (ValueError, AttributeError) as error:
                raise RuntimeError("release timestamp is invalid") from error
    if deployed_at and not timestamps[0] <= timestamps[1] <= datetime.now(timezone.utc):
        raise RuntimeError("release deployment time must follow the build and cannot be in the future")

    _, health, _ = client.request(f"{gateway_url}/health")
    worker_version = check_release(health, release)
    if health.get("publication_hash") != release["publication_hash"]:
        raise RuntimeError("Worker health publication hash does not match the release")
    if health.get("status") != "ok" or not health.get("publication_id"):
        raise RuntimeError("Worker health receipt is not usable")

    _, capabilities, _ = client.request(f"{gateway_url}/capabilities")
    check_release(capabilities, release, worker_version)
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
    _, policy, _ = client.request(f"{public_url}/data/source-policy.json")
    if policy.get("policy_hash") != release["source_policy_hash"]:
        raise RuntimeError("Public source policy does not match the release")
    for surface in (health, capabilities):
        if surface.get("policy", {}).get("policy_hash") != release["source_policy_hash"]:
            raise RuntimeError("Worker policy does not match the release")

    _, publication_query, _ = client.request(
        f"{gateway_url}/query",
        method="POST",
        payload={"tool": "get_publication_receipt", "arguments": {}, "release_id": release["release_id"]},
    )
    check_query(publication_query, release, worker_version)
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
    if (release["artifact_hashes"] != expected_hashes or release["publication_hash"] != expected_hashes["brief"] or
            receipt.get("generation_id") != release["query_generation"] or receipt.get("policy_hash") != release["source_policy_hash"] or
            public_docs["intelligence-feed.json"].get("collection_run_id") != release["publication_generation"] or
            public_docs["source-status.json"].get("latest_collection_run", {}).get("collection_run_id") != release["publication_generation"] or
            public_docs["v2-daily-brief.json"].get("source_collection_run_id") != release["publication_generation"]):
        raise RuntimeError("Public publication and query generation do not match the release")

    _, search, _ = client.request(
        f"{gateway_url}/query",
        method="POST",
        payload={"tool": "search_evidence", "arguments": {"q": "", "limit": 1, "expected_generation": release["query_generation"]}, "release_id": release["release_id"]},
    )
    check_query(search, release, worker_version)
    if search.get("tool_name") != "search_evidence" or not isinstance(search.get("receipt"), dict):
        raise RuntimeError("Worker search_evidence smoke did not return a receipt")
    results = search.get("results")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        raise RuntimeError("Worker search returned no official evidence locator")
    evidence = results[0]
    official_url = evidence.get("official_url")
    parts = urlsplit(official_url or "")
    canonical = next((row for row in public_docs["intelligence-feed.json"].get("items", [])
                      if row.get("stable_id") == evidence.get("canonical_id")), None)
    if (not canonical or canonical.get("official_url") != official_url or parts.scheme != "https" or
            not (parts.hostname or "").endswith(".gov.tw") or parts.username or parts.password or
            evidence.get("canonical_ref", {}).get("artifact_sha256") != expected_hashes["feed"]):
        raise RuntimeError("Worker evidence locator does not match the official publication")

    _, initialize, _ = client.request(
        f"{gateway_url}/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
    )
    if initialize.get("result", {}).get("protocolVersion") != "2025-06-18":
        raise RuntimeError("MCP initialize handshake failed")
    check_release(initialize["result"], release, worker_version)
    _, tools_list, _ = client.request(
        f"{gateway_url}/mcp",
        method="POST",
        payload={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    )
    tool_names = {item.get("name") for item in tools_list.get("result", {}).get("tools", [])}
    if not REQUIRED_CAPABILITIES.issubset(tool_names):
        raise RuntimeError("MCP tools/list is missing a production capability")
    check_release(tools_list["result"], release, worker_version)
    _, mcp_query, _ = client.request(
        f"{gateway_url}/mcp", method="POST",
        payload={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "release_id": release["release_id"],
                 "params": {"name": "get_publication_receipt", "arguments": {}}},
    )
    if mcp_query.get("result", {}).get("isError") is not False:
        raise RuntimeError("MCP release query failed")
    check_query(mcp_query["result"].get("structuredContent", {}), release, worker_version)

    verified_at = datetime.now(timezone.utc).isoformat()
    production_verified = bool(anonymous_http and pages_deployment and deployed_at and expected_code_sha)
    return {
        **release,
        "schema_version": 1,
        "kind": "GOVINTEL_PRODUCTION_QUERY_RECEIPT",
        "verified_at": verified_at,
        "anonymous_http_verified_at": verified_at if anonymous_http else None,
        "pages_deployment": pages_deployment,
        "deployed_at": deployed_at,
        "production_verified": production_verified,
        "evidence_level": "PRODUCTION" if production_verified else "ANONYMOUS_HTTP_ONLY" if anonymous_http else "LOCAL_TEST",
        "gateway_url": gateway_url,
        "public_url": public_url,
        "worker_version": worker_version,
        "publication_id": receipt.get("publication_id"),
        "publication_hash": receipt.get("publication_hash"),
        "artifact_hashes": expected_hashes,
        "query_generation_id": receipt.get("generation_id"),
        "publication_freshness": health.get("publication_freshness"),
        "capabilities": sorted(actual_capabilities),
        "official_evidence_url": official_url,
        "official_evidence_id": evidence["canonical_id"],
        "checks": {"health": "SUCCESS", "capabilities": "SUCCESS", "hash_binding": "SUCCESS", "release_binding": "SUCCESS", "query": "SUCCESS", "mcp": "SUCCESS", "evidence_locator": "SUCCESS"},
        "public_generation": public_docs["v2-daily-brief.json"].get("source_collection_run_id"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway-url", default=os.environ.get("QUERY_GATEWAY_URL", ""))
    parser.add_argument("--public-base-url", default=os.environ.get("PUBLICATION_BASE_URL", ""))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pages-deployment", default=os.environ.get("PAGES_DEPLOYMENT"))
    parser.add_argument("--deployed-at", default=os.environ.get("DEPLOYED_AT"))
    parser.add_argument("--expected-code-sha", default=os.environ.get("GITHUB_SHA"))
    args = parser.parse_args()
    last_error = None
    for attempt in range(3):
        try:
            receipt = verify(args.gateway_url, args.public_base_url, pages_deployment=args.pages_deployment,
                             deployed_at=args.deployed_at, expected_code_sha=args.expected_code_sha)
            if not receipt["production_verified"]:
                raise RuntimeError("production receipt requires Pages deployment, deployment time and expected code SHA")
            receipt["attempt"] = attempt + 1
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print("PRODUCTION_QUERY_RECEIPT_OK " + json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            return 0
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(5)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({
            "schema_version": 1, "kind": "GOVINTEL_PRODUCTION_QUERY_RECEIPT", "status": "FAILED",
            "production_verified": False, "evidence_level": "VERIFICATION_FAILED",
            "failed_at": datetime.now(timezone.utc).isoformat(), "error": str(last_error),
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PRODUCTION_QUERY_RECEIPT_FAIL {last_error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
