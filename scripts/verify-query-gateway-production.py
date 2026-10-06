"""Verify the anonymous Pages -> Worker query release binding."""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import importlib.util
import json
import os
import re
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_BYTES = 2 * 1024 * 1024
# Worker artifact fetches cache for 30 seconds. Three attempts span 40 seconds
# while preserving every release/hash/admission check and the failure receipt.
RELEASE_BINDING_RETRY_DELAY_SECONDS = 20
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

    def request(self, url: str, *, method: str = "GET", payload: dict | None = None, expected_status: int = 200) -> tuple[int, dict, bytes]:
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
            if error.code != expected_status:
                raise RuntimeError(f"{url} returned HTTP {error.code}") from error
            status = error.code
            raw = error.read(MAX_BYTES + 1)
        if status != expected_status:
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


def expected_admission(policy: dict) -> dict:
    ids = policy.get("active_source_ids")
    if (not isinstance(ids, list) or not ids or
            any(not isinstance(sid, str) or not re.fullmatch(r"S-[0-9]{3}", sid) for sid in ids) or
            len(set(ids)) != len(ids)):
        raise RuntimeError("Public source policy has invalid active source identities")
    spec = importlib.util.spec_from_file_location("release_source_policy", Path(__file__).with_name("source-policy.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    approved = json.loads(module.APPROVED_POLICY.read_text(encoding="utf-8"))
    module.validate_policy(approved)
    if any(policy.get(key) != approved.get(key) for key in ("policy_version", "policy_hash", "catalog_hash", "active_source_ids")):
        raise RuntimeError("Public policy does not match the approved repository snapshot")
    if type(policy.get("schema_version")) is int and policy["schema_version"] == 1 and type(policy.get("policy_version")) is int and policy["policy_version"] == 1:
        if approved["schema_version"] != 1:
            raise RuntimeError("Public legacy policy does not match the approved repository snapshot")
        return module.formal_admission(approved)
    if type(policy.get("schema_version")) is not int or policy["schema_version"] != 2 or policy != approved:
        raise RuntimeError("Public governed policy is not the approved repository snapshot")
    expected = module.formal_admission(approved)
    if expected["status"] != "ADMITTED":
        # Only the exact legacy refusal contract has an operational-only oracle.
        raise RuntimeError("Public governed policy is not formally admitted")
    return expected


def check_policy(document: dict, policy: dict) -> None:
    bound = document.get("policy")
    fields = ("policy_version", "policy_hash", "catalog_hash", "active_source_ids")
    if not isinstance(bound, dict) or any(bound.get(key) != policy.get(key) for key in fields):
        raise RuntimeError("Worker and public policy binding mismatch")
    if policy.get("schema_version") == 2 and (bound.get("governance_hash") != policy["governance_binding"]["governance_hash"] or bound.get("retention_policy_hash") != policy["governance_binding"]["retention_policy"]["policy_hash"]):
        raise RuntimeError("Worker governance binding mismatch")


def check_admission(document: dict, expected: dict, policy: dict) -> dict:
    check_policy(document, policy)
    coverage = document.get("query_coverage")
    if (not isinstance(coverage, dict) or coverage.get("formal_admission") != expected or
            coverage.get("required_sources") != policy["active_source_ids"] or
            coverage.get("policy_hash") != policy["policy_hash"] or
            coverage.get("policy_version") != policy["policy_version"] or
            coverage.get("capability_id") != "publication_metadata"):
        raise RuntimeError("Worker formal query admission does not match the public policy")
    if expected["status"] == "UNKNOWN":
        if (coverage.get("status") != "UNKNOWN" or
                coverage.get("rights_blocked_sources") != policy["active_source_ids"] or
                coverage.get("formally_admitted_sources") != [] or coverage.get("covered_sources") != [] or
                coverage.get("can_state_bounded_no_match") is not False):
            raise RuntimeError("Rights-blocked query falsely claims formal coverage or bounded no match")
    elif coverage.get("rights_blocked_sources") != []:
        raise RuntimeError("Admitted query has contradictory rights blockers")
    return coverage


def check_blocked_search(document: dict) -> None:
    if (document.get("results") != [] or type(document.get("result_count")) is not int or document["result_count"] != 0 or
            type(document.get("total_matches")) is not int or document["total_matches"] != 0 or
            type(document.get("receipt", {}).get("result_count")) is not int or document["receipt"]["result_count"] != 0 or
            document.get("answerable_no_match") is not False or document.get("has_more") is not False or
            document.get("next_cursor") is not None or "next_cursor" not in document or
            document.get("truncated") is not False or document.get("receipt", {}).get("truncated") is not False or
            type(document.get("offset")) is not int or document["offset"] != 0 or
            document.get("evidence_ids") != [] or document.get("event_ids") != []):
        raise RuntimeError("Rights-blocked search must return explicit zero evidence without a bounded absence claim")


def check_blocked_sources(document: dict, policy: dict, expected: dict, public_status: dict, formal_coverage: dict) -> None:
    check_policy(document, policy)
    ids = policy["active_source_ids"]
    sources, rights = document.get("sources"), document.get("source_rights")
    if (document.get("formal_admission") != expected or not isinstance(sources, list) or
            [row.get("source_id") for row in sources if isinstance(row, dict)] != ids or
            len(sources) != len(ids) or rights != [{"source_id": sid, "rights_status": "UNKNOWN", "review_required": True} for sid in ids] or
            document.get("receipt", {}).get("result_count") != len(ids)):
        raise RuntimeError("Rights-blocked source health does not preserve the exact approved source identities")
    original = {row["source_id"]: row for row in public_status.get("sources", [])}
    if formal_coverage.get("collection_completeness") != {sid: original.get(sid, {}).get("window_completeness") for sid in ids}:
        raise RuntimeError("Coverage completeness does not match the public canonical status")
    for key in ("missing_required_sources", "stale_required_sources"):
        value = formal_coverage.get(key)
        if not isinstance(value, list) or len(value) != len(set(value)) or not set(value) <= set(ids):
            raise RuntimeError("Coverage diagnostics contain unapproved or duplicate source identities")
    if formal_coverage.get("collection_coverage_status") not in {"COVERED_BOUNDED_SCOPE", "PARTIAL", "STALE", "UNKNOWN"}:
        raise RuntimeError("Collection coverage status is not a bounded typed status")
    for row in sources:
        if row["source_id"] not in original:
            raise RuntimeError("Source health is absent from the public canonical status")
        for key in ("source_health", "window_completeness", "result", "freshness_status", "data_as_of", "last_checked_at", "last_success_at"):
            if row.get(key) != original[row["source_id"]].get(key):
                raise RuntimeError("Source health does not match the public canonical status")
    coverage = document.get("query_coverage", {})
    for key in ("policy_hash", "policy_version", "required_sources", "missing_required_sources", "stale_required_sources", "collection_completeness"):
        if coverage.get(key) != formal_coverage.get(key):
            raise RuntimeError("Source health and formal query coverage diagnostics disagree")
    if coverage.get("capability_id") != "source_health":
        raise RuntimeError("Source health capability scope is invalid")


def canonical_diagnostics(public_docs: dict, hashes: dict) -> tuple:
    # Reuse the actual current reader's projection, admission, completeness and
    # scope rules. Responses agreeing with one another are not a canonical proof.
    spec = importlib.util.spec_from_file_location("release_canonical_store", Path(__file__).with_name("query-store.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        policy = module.load_current_policy()
        store = module.build_store(public_docs["intelligence-feed.json"], public_docs["source-status.json"],
                                   public_docs["v2-daily-brief.json"], hashes, policy=policy)
    except (ValueError, KeyError, TypeError) as error:
        raise RuntimeError(f"Canonical evidence/publication rederivation failed: {error}") from error
    formal = module._query_coverage(store, "publication_metadata", policy=policy)
    formal.setdefault("collection_coverage_status", formal["status"])
    health = module._query_coverage(store, "source_health", policy=policy)
    return module, store, formal, health


def check_canonical_diagnostics(document: dict, canonical: tuple, verification_start: datetime, *,
                                health_interval: tuple | None = None, source_health: bool = False) -> None:
    module, store, formal, source = canonical
    expected = source if source_health else formal
    actual = document.get("query_coverage", {})
    for key in expected.keys() - {"coverage_limitations", "requested_scope"}:
        if actual.get(key) != expected[key]:
            raise RuntimeError(f"Worker canonical coverage diagnostic mismatch: {key}")
    if health_interval is not None:
        # /health has no server timestamp. Its exact scope must fit the bounded
        # request interval, with five seconds of clock skew at either endpoint.
        clocks = [health_interval[0] - timedelta(seconds=5), health_interval[1] + timedelta(seconds=5)]
    else:
        try:
            clock = module.instant(document.get("queried_at"))
            issued = module.instant(document.get("receipt", {}).get("issued_at"))
        except ValueError as error:
            raise RuntimeError("Query diagnostic clock is missing or invalid") from error
        if (clock < verification_start - timedelta(seconds=5) or clock > datetime.now(timezone.utc) + timedelta(seconds=5) or
                abs((clock - issued).total_seconds()) > 1):
            raise RuntimeError("Query diagnostic clock is outside this verification interval")
        # Native Worker records the envelope just after assessing scope. Allow
        # only this one-second crossing window; no artifact times are retimed.
        clocks = [clock - timedelta(seconds=1), clock]
    assessments = [module.assess_scope(store, None, clock) for clock in clocks]
    freshness_key = "publication_freshness" if health_interval is not None else "freshness"
    matching = [(status, gaps) for status, gaps in assessments if document.get("source_gaps") == gaps and
                document.get(freshness_key) == ("RECENT" if status == "SNAPSHOT_RECENT" else status)]
    if not matching:
        raise RuntimeError("Worker canonical source gaps or snapshot freshness mismatch")
    receipt = document.get("publication_receipt")
    if receipt is not None:
        meta = store["generated_from"]
        if (receipt.get("source_gaps") != document.get("source_gaps") or receipt.get("freshness") != document.get("freshness") or
                receipt.get("current_as_of_server_clock") is not (document.get("freshness") == "RECENT") or
                any(receipt.get(key) != meta.get(key) for key in ("collection_status", "publication_status", "snapshot_complete"))):
            raise RuntimeError("Publication receipt canonical diagnostic mismatch")


def check_rights_error(document: dict) -> None:
    error = document.get("error")
    if (set(document) != {"schema_version", "error"} or type(document.get("schema_version")) is not int or document["schema_version"] != 1 or
            not isinstance(error, dict) or set(error) != {"code", "message"} or error.get("code") != "RIGHTS_BLOCKED" or
            not isinstance(error.get("message"), str) or not error["message"] or len(error["message"]) > 1024):
        raise RuntimeError("Protected current query did not return only a typed RIGHTS_BLOCKED refusal")


def mcp_content(document: dict) -> dict:
    result = document.get("result", {})
    if result.get("isError") is not False or not isinstance(result.get("structuredContent"), dict):
        raise RuntimeError("MCP release query failed")
    content = result.get("content")
    if (not isinstance(content, list) or len(content) != 1 or not isinstance(content[0], dict) or
            set(content[0]) != {"type", "text"} or content[0].get("type") != "text" or not isinstance(content[0].get("text"), str)):
        raise RuntimeError("MCP release query requires one JSON text receipt")
    try:
        def unique_object(pairs):
            document = {}
            for key, value in pairs:
                if key in document:
                    raise ValueError("duplicate MCP text object key")
                document[key] = value
            return document
        text = json.loads(content[0]["text"], object_pairs_hook=unique_object)
        if not isinstance(text, dict):
            raise ValueError("text receipt is not an object")
        def exact_json(value):
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if exact_json(text) != exact_json(result["structuredContent"]):
            raise ValueError("text and structured receipts disagree")
    except (ValueError, TypeError) as error:
        raise RuntimeError("MCP release text and structured receipts must be finite, identical JSON objects") from error
    return result["structuredContent"]


def verify(gateway_url: str, public_url: str, client: JsonClient | None = None, *,
           pages_deployment: str | None = None, deployed_at: str | None = None,
           expected_code_sha: str | None = None) -> dict:
    gateway_url = checked_base(gateway_url)
    public_url = checked_base(public_url, pages=True)
    verification_start = datetime.now(timezone.utc)
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

    health_start = datetime.now(timezone.utc)
    _, health, _ = client.request(f"{gateway_url}/health")
    health_end = datetime.now(timezone.utc)
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
    admission = expected_admission(policy)
    for surface in (health, capabilities):
        check_policy(surface, policy)
    health_coverage = check_admission(health, admission, policy)

    _, publication_query, _ = client.request(
        f"{gateway_url}/query",
        method="POST",
        payload={"tool": "get_publication_receipt", "arguments": {}, "release_id": release["release_id"]},
    )
    check_query(publication_query, release, worker_version)
    publication_coverage = check_admission(publication_query, admission, policy)
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

    derived_diagnostics = canonical_diagnostics(public_docs, expected_hashes)
    if derived_diagnostics[1]["generation_id"] != release["query_generation"]:
        raise RuntimeError("Query generation does not match canonical rederivation")
    check_canonical_diagnostics(health, derived_diagnostics, verification_start, health_interval=(health_start, health_end))
    check_canonical_diagnostics(publication_query, derived_diagnostics, verification_start)

    _, search, _ = client.request(
        f"{gateway_url}/query",
        method="POST",
        payload={"tool": "search_evidence", "arguments": {"q": "", "limit": 1, "expected_generation": release["query_generation"]}, "release_id": release["release_id"]},
    )
    check_query(search, release, worker_version)
    if search.get("tool_name") != "search_evidence" or not isinstance(search.get("receipt"), dict):
        raise RuntimeError("Worker search_evidence smoke did not return a receipt")
    check_canonical_diagnostics(search, derived_diagnostics, verification_start)
    search_coverage = check_admission(search, admission, policy)
    blocked = admission["status"] == "UNKNOWN"
    official_url, evidence = None, None
    if blocked:
        check_blocked_search(search)
        for surface in (publication_coverage, search_coverage):
            for key in ("status", "formal_admission", "rights_blocked_sources", "required_sources", "missing_required_sources", "stale_required_sources", "collection_coverage_status", "collection_completeness", "formally_admitted_sources", "can_state_bounded_no_match"):
                if surface.get(key) != health_coverage.get(key):
                    raise RuntimeError("Rights-blocked query coverage differs across release surfaces")
        if any(surface.get("source_gaps") != health.get("source_gaps") for surface in (publication_query, search)) or receipt.get("source_gaps") != health.get("source_gaps"):
            raise RuntimeError("Rights-blocked source gaps differ across release surfaces")
        _, source_health, _ = client.request(f"{gateway_url}/query", method="POST",
            payload={"tool": "get_source_health", "arguments": {}, "release_id": release["release_id"]})
        check_query(source_health, release, worker_version)
        check_blocked_sources(source_health, policy, admission, public_docs["source-status.json"], health_coverage)
        check_canonical_diagnostics(source_health, derived_diagnostics, verification_start, source_health=True)
    else:
        results = search.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            raise RuntimeError("Worker search returned no official evidence locator")
        evidence = results[0]
        official_url = evidence.get("official_url")
        parts = urlsplit(official_url or "")
        canonical = next((row for row in public_docs["intelligence-feed.json"].get("items", [])
                          if row.get("stable_id") == evidence.get("canonical_id")), None)
        approved_source = next((row for row in policy["active_sources"] if canonical and row["source_id"] == canonical.get("source_id")), None)
        origin = f"{parts.scheme}://{parts.netloc}"
        if (not approved_source or origin not in approved_source["approved_origins"] or not canonical or canonical.get("official_url") != official_url or parts.scheme != "https" or
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
    mcp_publication = mcp_content(mcp_query)
    check_query(mcp_publication, release, worker_version)
    check_admission(mcp_publication, admission, policy)
    check_canonical_diagnostics(mcp_publication, derived_diagnostics, verification_start)
    if blocked:
        if (mcp_publication.get("publication_receipt") != receipt or
                mcp_publication.get("source_gaps") != publication_query.get("source_gaps")):
            raise RuntimeError("MCP and HTTP publication receipts disagree")
        for number, tool in enumerate(("search_evidence", "get_source_health"), start=4):
            args = {"q": "", "limit": 1, "expected_generation": release["query_generation"]} if tool == "search_evidence" else {}
            _, response, _ = client.request(f"{gateway_url}/mcp", method="POST", payload={"jsonrpc": "2.0", "id": number,
                "method": "tools/call", "release_id": release["release_id"], "params": {"name": tool, "arguments": args}})
            content = mcp_content(response)
            check_query(content, release, worker_version)
            check_canonical_diagnostics(content, derived_diagnostics, verification_start, source_health=tool == "get_source_health")
            if tool == "search_evidence":
                check_admission(content, admission, policy)
                check_blocked_search(content)
                if content.get("query_coverage") != search.get("query_coverage") or content.get("source_gaps") != search.get("source_gaps"):
                    raise RuntimeError("MCP and HTTP rights-blocked searches disagree")
            else:
                check_blocked_sources(content, policy, admission, public_docs["source-status.json"], health_coverage)
        claims = [{"schema_version": 1, "claim_id": "release-refusal-probe", "text": "發布標題",
            "claim_type": "STATUS", "temporal_scope": "CURRENT", "proposition": {"subject": "publication:release-refusal-probe:title", "predicate": "IS", "value": "發布標題"}, "cited_evidence_ids": []}]
        for number, tool in enumerate(("get_current_brief", "validate_answer"), start=6):
            args = {} if tool == "get_current_brief" else {"claims": claims, "expected_generation": release["query_generation"]}
            status, refusal, _ = client.request(f"{gateway_url}/query", method="POST", expected_status=503,
                payload={"tool": tool, "arguments": args, "release_id": release["release_id"]})
            if status != 503:
                raise RuntimeError("Protected current query did not return HTTP 503")
            check_rights_error(refusal)
            _, response, _ = client.request(f"{gateway_url}/mcp", method="POST", payload={"jsonrpc": "2.0", "id": number,
                "method": "tools/call", "release_id": release["release_id"], "params": {"name": tool, "arguments": args}})
            result = response.get("result", {})
            content = result.get("content")
            if set(result) != {"isError", "content"} or result.get("isError") is not True or not isinstance(content, list) or len(content) != 1 or content[0].get("type") != "text":
                raise RuntimeError("MCP protected current query did not return a typed refusal")
            typed = json.loads(content[0]["text"])
            check_rights_error(typed)

    verified_at = datetime.now(timezone.utc).isoformat()
    deployment_verified = bool(anonymous_http and pages_deployment and deployed_at and expected_code_sha)
    production_verified = deployment_verified and not blocked
    return {
        **release,
        "schema_version": 1,
        "kind": "GOVINTEL_PRODUCTION_QUERY_RECEIPT",
        "verified_at": verified_at,
        "anonymous_http_verified_at": verified_at if anonymous_http else None,
        "pages_deployment": pages_deployment,
        "deployed_at": deployed_at,
        "deployment_verified": deployment_verified,
        "query_readiness": "RIGHTS_BLOCKED" if blocked else "EVIDENCE_LOCATOR_VERIFIED",
        "formal_admission": admission,
        "production_verified": production_verified,
        "evidence_level": "DEPLOYMENT_BOUND_RIGHTS_BLOCKED" if deployment_verified and blocked else "PRODUCTION" if production_verified else "ANONYMOUS_HTTP_ONLY" if anonymous_http else "LOCAL_TEST",
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
        "official_evidence_id": evidence["canonical_id"] if evidence else None,
        "checks": {"health": "SUCCESS", "capabilities": "SUCCESS", "hash_binding": "SUCCESS", "release_binding": "SUCCESS", "query": "SUCCESS", "mcp": "SUCCESS", "evidence_locator": "RIGHTS_BLOCKED" if blocked else "SUCCESS", "formal_query_admission": "UNKNOWN" if blocked else "ADMITTED", "protected_query_refusal": "SUCCESS" if blocked else "NOT_CHECKED"},
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
    attempts = []
    for attempt in range(3):
        try:
            receipt = verify(args.gateway_url, args.public_base_url, pages_deployment=args.pages_deployment,
                             deployed_at=args.deployed_at, expected_code_sha=args.expected_code_sha)
            if not receipt["deployment_verified"]:
                raise RuntimeError("production receipt requires Pages deployment, deployment time and expected code SHA")
            receipt["attempt"] = attempt + 1
            attempts.append({"attempt": attempt + 1, "status": "PASS", "observed_at": datetime.now(timezone.utc).isoformat()})
            receipt["verification_attempts"] = attempts
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            target = os.environ.get("GITHUB_OUTPUT")
            if target:
                with Path(target).open("a", encoding="utf-8") as handle:
                    handle.write(f"query_readiness={receipt['query_readiness']}\nproduction_verified={str(receipt['production_verified']).lower()}\n")
            print("DEPLOYMENT_QUERY_RECEIPT_OK " + json.dumps(receipt, ensure_ascii=False, sort_keys=True))
            return 0
        except (OSError, RuntimeError, ValueError, TypeError, AttributeError, KeyError) as error:
            last_error = error
            attempts.append({"attempt": attempt + 1, "status": "FAILED", "observed_at": datetime.now(timezone.utc).isoformat(),
                             "error_type": type(error).__name__, "error": str(error)[:1024]})
            if attempt < 2:
                time.sleep(RELEASE_BINDING_RETRY_DELAY_SECONDS)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({
            "schema_version": 1, "kind": "GOVINTEL_PRODUCTION_QUERY_RECEIPT", "status": "FAILED",
            "deployment_verified": False, "production_verified": False, "query_readiness": "VERIFICATION_FAILED", "evidence_level": "VERIFICATION_FAILED",
            "failed_at": datetime.now(timezone.utc).isoformat(), "error": str(last_error),
            "verification_attempts": attempts,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"PRODUCTION_QUERY_RECEIPT_FAIL {last_error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
