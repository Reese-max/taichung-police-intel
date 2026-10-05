#!/usr/bin/env python3
"""Small read-only HTTP/MCP adapter over the canonical publication snapshot."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
import unicodedata
import re
from pathlib import Path
from http.server import BaseHTTPRequestHandler, HTTPServer
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib.parse import quote, urlsplit
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2 import query_domain
from intel_v2.located_facts import validate_document_url
from intel_v2.public_brief import project_public_brief, project_public_source

QUERY_STORE_PATH = ROOT / "scripts" / "query-store.py"
RETENTION_POLICY_PATH = ROOT / "scripts" / "retention-policy.py"
ANSWER_GATE_RUNNER = ROOT / "scripts" / "answer-gate-runner.mjs"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
SERVER_VERSION = "query-gateway-v1"
MCP_PROTOCOL_VERSION = "2025-06-18"
DEFAULT_RATE_LIMIT = 60
DEFAULT_QUERY_TIMEZONE = "Asia/Taipei"
SOURCE_CATALOG = ROOT / "docs" / "govintel" / "source-catalog.v2.json"
PUBLIC_EVIDENCE_SOURCE_STATUSES = frozenset({"PRODUCTION_ACTIVE", "AUDITED_EXISTING"})
OFFICIAL_EVIDENCE_SOURCE_ROLES = frozenset({"PRIMARY_EVENT", "PRIMARY_REFERENCE"})
ANSWER_VALIDATOR_VERSION = "answer-evidence-gate/3"
STATISTIC_DECIMAL_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")

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
    "get_publication_receipt",
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
                "canonical_id": {"type": "string", "maxLength": 256},
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
        "name": "get_publication_receipt",
        "description": "Read the current publication and canonical artifact hashes without exposing raw content.",
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

DOMAIN_TOOL_ARGUMENTS = {
    "search_events": {
        "q", "region", "district", "agency", "category", "time_from", "time_to",
        "time_zone",
        "verification_status", "event_status", "tracked", "changed_only", "public_event_id",
        "limit", "cursor", "expected_generation",
    },
    "get_event": {"event_id"},
    "compare_event_versions": {"event_id", "before_version", "after_version"},
    "query_statistics": {
        "dataset_id", "metric", "geography", "agency", "period_from", "period_to", "provisional",
        "limit", "cursor", "expected_generation",
    },
}

DOMAIN_MCP_TOOLS = [
    {
        "name": "search_events",
        "description": "Search the validated PublicEvent store with typed, read-only filters.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "q": {"type": "string", "maxLength": 512},
                "region": {"type": "string", "maxLength": 256},
                "district": {"type": "string", "maxLength": 256},
                "agency": {"type": "string", "maxLength": 256},
                "category": {"type": "string", "maxLength": 128},
                "time_from": {"type": "string", "maxLength": 64},
                "time_to": {"type": "string", "maxLength": 64},
                "time_zone": {"type": "string", "maxLength": 64},
                "verification_status": {"type": "string", "maxLength": 64},
                "event_status": {"type": "string", "maxLength": 64},
                "tracked": {"type": "boolean"},
                "changed_only": {"type": "boolean"},
                "public_event_id": {"type": "string", "maxLength": 256},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": {"type": "string", "maxLength": 1024},
                "expected_generation": {"type": "string", "maxLength": 128},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "get_event",
        "description": "Read one validated PublicEvent and its official document locators.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "required": ["event_id"],
            "properties": {"event_id": {"type": "string", "maxLength": 256}},
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "compare_event_versions",
        "description": "Compare two stored PublicEvent document versions without changing canonical state.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "required": ["event_id"],
            "properties": {
                "event_id": {"type": "string", "maxLength": 256},
                "before_version": {"type": "string", "maxLength": 256},
                "after_version": {"type": "string", "maxLength": 256},
            },
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False, "destructiveHint": False},
    },
    {
        "name": "query_statistics",
        "description": "Query typed, period-bound statistics with source, unit, geography, and provisional state.",
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "dataset_id": {"type": "string", "maxLength": 512},
                "metric": {"type": "string", "maxLength": 512},
                "geography": {"type": "string", "maxLength": 512},
                "agency": {"type": "string", "maxLength": 256},
                "period_from": {"type": "string", "maxLength": 128},
                "period_to": {"type": "string", "maxLength": 128},
                "provisional": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "cursor": {"type": "string", "maxLength": 1024},
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


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _resolve_event_time_bounds(arguments: dict[str, Any], server_now: datetime) -> tuple[dict[str, Any], dict[str, Any] | None]:
    time_zone_name = arguments.get("time_zone") or DEFAULT_QUERY_TIMEZONE
    try:
        time_zone = ZoneInfo(time_zone_name)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("time_zone must be a valid IANA timezone") from error
    if not arguments.get("time_from") and not arguments.get("time_to"):
        return dict(arguments), None

    resolved = dict(arguments)
    requested: dict[str, str] = {}
    normalized: dict[str, str] = {}
    for name, end_of_day in (("time_from", False), ("time_to", True)):
        raw = arguments.get(name)
        if raw is None:
            continue
        requested[name] = raw
        if len(raw) == 10:
            try:
                local_date = date.fromisoformat(raw)
            except ValueError as error:
                raise ValueError(f"{name} must be an ISO-8601 date or timestamp") from error
            local = datetime.combine(local_date, datetime.min.time(), tzinfo=time_zone)
            if end_of_day:
                next_day = local_date + timedelta(days=1)
                local = datetime.combine(next_day, datetime.min.time(), tzinfo=time_zone) - timedelta(microseconds=1)
        else:
            try:
                local = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError as error:
                raise ValueError(f"{name} must be an ISO-8601 date or timestamp") from error
            if local.tzinfo is None:
                raise ValueError(f"{name} timestamp must include a timezone")
        normalized[name] = local.astimezone(timezone.utc).isoformat()
        resolved[name] = normalized[name]
    return resolved, {
        "schema_version": 1,
        "time_zone": time_zone_name,
        "requested": requested,
        "resolved": normalized,
        "server_clock": server_now.isoformat(),
    }


def _valid_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _valid_locator(locator: Any, raw_hash: str, text_hash: str) -> bool:
    if not isinstance(locator, dict) or locator.get("document_sha256") != raw_hash or locator.get("text_sha256") != text_hash:
        return False
    if locator.get("type") == "HTML_TEXT_RANGE":
        return (
            type(locator.get("start")) is int
            and type(locator.get("end")) is int
            and 0 <= locator["start"] <= locator["end"]
            and _nonempty_string(locator.get("quote"))
        )
    if locator.get("type") == "JSON_POINTER":
        return isinstance(locator.get("pointer"), str) and (
            locator["pointer"] == "" or locator["pointer"].startswith("/")
        )
    return False


def _valid_valid_time_source(source: Any, valid_time: Any, raw_hash: str, text_hash: str) -> bool:
    if source is None:
        return valid_time is None
    if not isinstance(source, dict):
        return False
    if source.get("type") == "JSON_VALUE":
        return True
    return source.get("type") == "HTML_TEXT_RANGE" and _valid_locator(source, raw_hash, text_hash)


def _valid_fact_review(review: Any) -> bool:
    if not isinstance(review, dict):
        return False
    if review.get("decision") != "CONFIRMED_OFFICIAL" or review.get("method") != "EXACT_LOCATOR_RECHECK":
        return False
    if not isinstance(review.get("reviewer_ref"), str) or not review["reviewer_ref"].strip() or len(review["reviewer_ref"]) > 128:
        return False
    verified_at = review.get("verified_at")
    if not isinstance(verified_at, str) or not verified_at:
        return False
    try:
        parsed = datetime.fromisoformat(verified_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def approved_source_origins() -> dict[str, str]:
    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("approved source catalog shape is invalid")
    return {
        str(row["source_id"]): str(urlsplit(row["entrypoint"]).hostname)
        for row in catalog.get("sources", [])
        if (
            isinstance(row, dict)
            and row.get("source_id")
            and row.get("entrypoint")
            and row.get("status") in PUBLIC_EVIDENCE_SOURCE_STATUSES
        )
    }


def official_evidence_source_ids() -> set[str]:
    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    if not isinstance(catalog, dict):
        raise ValueError("official source catalog shape is invalid")
    return {
        str(row["source_id"])
        for row in catalog.get("sources", [])
        if (
            isinstance(row, dict)
            and row.get("source_id")
            and row.get("role") in OFFICIAL_EVIDENCE_SOURCE_ROLES
            and row.get("status") in PUBLIC_EVIDENCE_SOURCE_STATUSES
        )
    }


def approved_evidence_source_ids() -> set[str]:
    # Discovery and enrichment origins may host navigation links but cannot
    # publish facts into the answer-evidence catalog.
    return set(approved_source_origins()) & official_evidence_source_ids()


def validate_located_facts_bundle(bundle: Any, approved_source_ids: set[str]) -> dict[str, Any]:
    document = bundle.get("document_version") if isinstance(bundle, dict) else None
    if not isinstance(document, dict) or type(document.get("schema_version")) is not int or document.get("schema_version") != 1:
        raise ValueError("located-facts bundle document version is invalid")
    source_id = document.get("source_id")
    requested_url = document.get("requested_url")
    final_url = document.get("final_url")
    if not _nonempty_string(source_id) or source_id not in approved_source_ids:
        raise ValueError("located-facts bundle source is not approved and HTTPS")
    try:
        validate_document_url(source_id, requested_url)
        validate_document_url(source_id, final_url)
    except (TypeError, ValueError) as error:
        raise ValueError("located-facts bundle source is not approved and HTTPS") from error
    raw_hash = document.get("raw_bytes_sha256")
    text_hash = document.get("extracted_text_sha256")
    if (
        not _nonempty_string(document.get("document_id"))
        or not _nonempty_string(document.get("original_source_identity"))
        or not _nonempty_string(requested_url)
        or not _nonempty_string(final_url)
        or not _valid_timestamp(document.get("fetched_at"))
        or not _nonempty_string(document.get("content_type"))
        or document.get("rights_status") not in {"UNKNOWN", "METADATA_LINK_ONLY", "PUBLIC_DERIVED"}
        or not _nonempty_string(document.get("snapshot_ref"))
        or not _nonempty_string(document.get("extractor_version"))
        or not _nonempty_string(document.get("document_version_id"))
        or not _is_sha256(raw_hash)
        or not _is_sha256(text_hash)
        or document["snapshot_ref"] != f"sha256:{raw_hash}"
        or document["document_version_id"] != f"DOCV-{raw_hash[:20].upper()}"
    ):
        raise ValueError("located-facts bundle document hash/version is invalid")
    facts = bundle.get("facts")
    evidence = bundle.get("evidence_catalog")
    events = bundle.get("public_event_inputs")
    receipt = bundle.get("receipt")
    if not isinstance(facts, list) or not isinstance(evidence, list) or not isinstance(events, list) or not isinstance(receipt, dict):
        raise ValueError("located-facts bundle shape is invalid")
    required_receipt = {
        "source_id": document["source_id"],
        "document_version_id": document["document_version_id"],
        "raw_bytes_sha256": raw_hash,
        "extracted_text_sha256": text_hash,
        "extractor_version": document.get("extractor_version"),
        "fact_count": len(facts),
    }
    if any(receipt.get(key) != value for key, value in required_receipt.items()):
        raise ValueError("located-facts receipt is not bound to its document version")
    if receipt.get("bundle_sha256") != _json_hash({"facts": facts, "evidence_catalog": evidence, "public_event_inputs": events}):
        raise ValueError("located-facts bundle receipt hash mismatch")
    fact_by_id = {}
    for fact in facts:
        if not isinstance(fact, dict) or not _nonempty_string(fact.get("fact_id")) or fact["fact_id"] in fact_by_id:
            raise ValueError("located-facts bundle fact IDs must be unique")
        if (
            fact.get("source_id") != document["source_id"]
            or fact.get("document_id") != document["document_id"]
            or fact.get("document_version_id") != document["document_version_id"]
            or fact.get("original_source_identity") != document["original_source_identity"]
            or fact.get("raw_bytes_sha256") != raw_hash
            or fact.get("extracted_text_sha256") != text_hash
            or fact.get("extractor_version") != document.get("extractor_version")
            or not _nonempty_string(fact.get("subject_id"))
            or not _nonempty_string(fact.get("predicate"))
            or not _nonempty_string(fact.get("normalizer"))
            or "raw_value" not in fact
            or "normalized_value" not in fact
            or not _valid_scalar(fact.get("normalized_value"))
            or "valid_time" not in fact
            or (fact.get("valid_time") is not None and not _nonempty_string(fact.get("valid_time")))
            or not _valid_valid_time_source(fact.get("valid_time_source"), fact.get("valid_time"), raw_hash, text_hash)
        ):
            raise ValueError("located-facts fact is not bound to its document version")
        locator = fact.get("locator")
        if not _valid_locator(locator, raw_hash, text_hash):
            raise ValueError("located-facts fact locator hash binding is invalid")
        fact_material = {
            'document_version_id': fact.get('document_version_id'),
            'subject_id': fact.get('subject_id'),
            'predicate': fact.get('predicate'),
            'normalizer': fact.get('normalizer'),
            'raw_value': fact.get('raw_value'),
            'normalized_value': fact.get('normalized_value'),
            'valid_time': fact.get('valid_time'),
            'valid_time_source': fact.get('valid_time_source'),
            'locator': locator,
        }
        expected_fact_id = f"FACT-{_json_hash(fact_material)[:20].upper()}"
        if fact.get("fact_id") != expected_fact_id:
            raise ValueError("located-facts fact ID binding is invalid")
        if fact.get("verification_status") not in {"FACT_CANDIDATE", "NEEDS_REVIEW", "CONFIRMED_OFFICIAL"}:
            raise ValueError("located-facts fact verification status is invalid")
        if fact.get("verification_status") == "CONFIRMED_OFFICIAL" and not _valid_fact_review(fact.get("review")):
            raise ValueError("located-facts confirmed fact requires a bound review")
        fact_by_id[fact["fact_id"]] = fact
    seen = set()
    evidence_fact_ids = set()
    for row in evidence:
        if not isinstance(row, dict) or not _nonempty_string(row.get("evidence_id")) or row["evidence_id"] in seen:
            raise ValueError("located-facts evidence IDs must be unique")
        if not _nonempty_string(row.get("fact_id")) or row.get("fact_id") not in fact_by_id or row.get("source_id") != document["source_id"]:
            raise ValueError("located-facts evidence is not bound to a fact")
        fact = fact_by_id[row["fact_id"]]
        if (
            row.get("document_version_id") != document["document_version_id"]
            or not _is_sha256(row.get("content_sha256"))
            or row.get("content_sha256") != raw_hash
            or not _nonempty_string(row.get("official_url"))
            or row.get("official_url") != document["final_url"]
            or not _valid_locator(row.get("locator"), raw_hash, text_hash)
            or row.get("locator") != fact.get("locator")
        ):
            raise ValueError("located-facts evidence hash/version binding is invalid")
        if row.get("verification_status") != fact.get("verification_status"):
            raise ValueError("located-facts evidence status is not bound to its fact")
        if row.get("verification_status") == "CONFIRMED_OFFICIAL":
            if not _valid_fact_review(row.get("review")) or fact.get("review") != row.get("review"):
                raise ValueError("located-facts confirmed evidence requires a bound review")
        seen.add(row["evidence_id"])
        evidence_fact_ids.add(row["fact_id"])
    if evidence_fact_ids != set(fact_by_id):
        raise ValueError("located-facts evidence must link exactly one row to every fact")
    event_by_id = {}
    for event in events:
        if not isinstance(event, dict) or not _nonempty_string(event.get("stable_id")):
            raise ValueError("located-facts event IDs must be valid")
        if event["stable_id"] in event_by_id:
            raise ValueError("located-facts event IDs must be unique")
        if event["stable_id"] not in fact_by_id:
            raise ValueError("located-facts event is not bound to a fact")
        event_by_id[event["stable_id"]] = event
        fact = fact_by_id[event["stable_id"]]
        if (
            event.get("source_id") != document["source_id"]
            or event.get("source_snapshot_ref") != document["snapshot_ref"]
            or not _is_sha256(event.get("content_sha256"))
            or event.get("content_sha256") != raw_hash
            or not _nonempty_string(event.get("official_url"))
            or event.get("official_url") != document["final_url"]
            or event.get("subject_id") != fact.get("subject_id")
            or event.get("predicate") != fact.get("predicate")
            or "normalized_value" not in event
            or event.get("normalized_value") != fact.get("normalized_value")
            or "valid_time" not in event
            or event.get("valid_time") != fact.get("valid_time")
        ):
            raise ValueError("located-facts event is not bound to its document version")
        if event.get("verification_status") != fact.get("verification_status"):
            raise ValueError("located-facts event status is not bound to its fact")
        if fact.get("verification_status") == "CONFIRMED_OFFICIAL":
            if not _valid_fact_review(event.get("review")) or event.get("review") != fact.get("review"):
                raise ValueError("located-facts confirmed event requires a bound review")
    if set(event_by_id) != set(fact_by_id):
        raise ValueError("located-facts events must link exactly one row to every fact")
    return bundle


def _load_current_snapshot(
    located_facts_path: Path | None = None,
    query_store_path: Path | None = None,
    public_events_path: Path | None = None,
    statistics_path: Path | None = None,
) -> dict[str, Any]:
    captured = qs.canonical_artifacts_from_paths()
    expected_store = qs.build_from_canonical_artifacts(captured)
    status, brief = (json.loads(captured[name].decode("utf-8")) for name in ("status", "brief"))
    if query_store_path is None:
        store = expected_store
    else:
        store, _ = qs.load_json(query_store_path)
        qs.validate_store(store, canonical_artifacts=captured)
        if store != expected_store:
            raise ValueError("query store does not match canonical publication artifacts")
    snapshot = {"store": store, "status": status, "brief": brief,
                "canonical_artifacts": {name: raw.decode("utf-8") for name, raw in captured.items()}}
    # Preserve the exact bytes, rather than re-serializing JSON and inventing a
    # different publication hash. These copies are derived restart evidence.
    if located_facts_path is not None:
        bundle = json.loads(located_facts_path.read_text(encoding="utf-8"))
        snapshot["located_facts"] = validate_located_facts_bundle(bundle, approved_evidence_source_ids())
    if public_events_path is not None:
        snapshot["event_store"] = query_domain.load_event_store(public_events_path)
    if statistics_path is not None:
        snapshot["statistics_store"] = query_domain.load_statistics_store(statistics_path)
    return snapshot


def _cached_snapshot(path: Path) -> dict[str, Any]:
    cached, _ = qs.load_json(path)
    if not isinstance(cached, dict) or cached.get("schema_version") != 1:
        raise ValueError("unsupported query generation cache")
    snapshot = cached.get("snapshot")
    if not isinstance(snapshot, dict) or cached.get("snapshot_sha256") != _json_hash(snapshot):
        raise ValueError("query generation cache hash mismatch")
    artifacts = snapshot.get("canonical_artifacts")
    if not isinstance(artifacts, dict) or set(artifacts) != {"feed", "status", "brief"} or any(
        not isinstance(raw, str) for raw in artifacts.values()
    ):
        raise ValueError("query generation cache lacks canonical artifact bytes")
    documents = {name: json.loads(raw) for name, raw in artifacts.items()}
    hashes = {name: qs.sha256_bytes(raw.encode("utf-8")) for name, raw in artifacts.items()}
    # Current approved policy still applies. A policy demotion or incompatible
    # schema cannot be bypassed by falling back to an old persisted generation.
    expected = qs.build_store(documents["feed"], documents["status"], documents["brief"], hashes)
    if snapshot.get("store") != expected or snapshot.get("status") != documents["status"] or snapshot.get("brief") != documents["brief"]:
        raise ValueError("query generation cache does not match its canonical publication")
    QueryGateway(snapshot)  # revalidate optional evidence/domain stores as well
    return snapshot


def _write_generation_cache(path: Path, snapshot: dict[str, Any]) -> None:
    cached = {"schema_version": 1, "snapshot": snapshot, "snapshot_sha256": _json_hash(snapshot)}
    payload = qs.canonical_json(cached) + b"\n"
    if len(payload) > qs.MAX_BYTES:
        raise ValueError("query generation cache exceeds byte limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name != "nt":
            # File fsync does not persist the directory entry created by rename.
            # Flush that entry before reporting a successful retained generation.
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


@contextmanager
def _generation_lock(path: Path):
    """Only one startup rebuild may replace a service's retained generation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_name(path.name + ".lock").open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if handle.tell() == 0:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_snapshot(
    located_facts_path: Path | None = None,
    query_store_path: Path | None = None,
    public_events_path: Path | None = None,
    statistics_path: Path | None = None,
    *,
    last_good_path: Path | None = None,
) -> dict[str, Any]:
    """Build one generation; an opt-in cache preserves a validated restart fallback.

    No public tool writes this cache. A failed build or pre-rename write leaves
    the old bytes intact; fallback is identified as a retained historical index.
    """
    if last_good_path is None:
        return _load_current_snapshot(located_facts_path, query_store_path, public_events_path, statistics_path)
    inputs = (qs.DEFAULT_FEED, qs.DEFAULT_STATUS, qs.DEFAULT_BRIEF,
              located_facts_path, query_store_path, public_events_path, statistics_path)
    if last_good_path.resolve() in {path.resolve() for path in inputs if path is not None}:
        raise ValueError("query generation cache must not overwrite a canonical or query input")
    try:
        with _generation_lock(last_good_path):
            snapshot = _load_current_snapshot(located_facts_path, query_store_path, public_events_path, statistics_path)
            QueryGateway(snapshot)
            _write_generation_cache(last_good_path, snapshot)
        return snapshot
    except (ValueError, OSError) as error:
        snapshot = _cached_snapshot(last_good_path)
        snapshot["rebuild_failure"] = {
            "error_class": type(error).__name__,
            "serving_generation_id": snapshot["store"]["generation_id"],
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }
        return snapshot


def _json_hash(value: Any) -> str:
    return hashlib.sha256(qs.canonical_json(value)).hexdigest()


def _json_portable(value: Any) -> bool:
    # The gate runs in Node, so values must survive a JSON round trip without
    # changing their meaning or their canonical receipt hash.
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return -(2**53) < value < 2**53
    if isinstance(value, list):
        return all(_json_portable(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_portable(item) for key, item in value.items())
    return False


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    try:
        return project_public_source(row)
    except ValueError as error:
        raise GatewayError("PUBLIC_PROJECTION_INVALID", "canonical source violates public projection schema", 503) from error


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
        captured = self.snapshot.get("canonical_artifacts")
        if captured is None:
            captured = qs.canonical_artifacts_from_paths()
        qs.validate_store(self.store, canonical_artifacts=captured)
        if (self.status != json.loads(captured["status"]) or self.brief != json.loads(captured["brief"])):
            raise ValueError("gateway snapshot does not match captured canonical publication artifacts")
        self.canonical_artifacts = dict(captured)
        self.located_facts = (
            validate_located_facts_bundle(
                self.snapshot["located_facts"], approved_evidence_source_ids()
            )
            if self.snapshot.get("located_facts") is not None else None
        )
        self.event_store = (
            query_domain.validate_event_store(self.snapshot["event_store"])
            if self.snapshot.get("event_store") is not None else None
        )
        self.statistics_store = (
            query_domain.validate_statistics_store(self.snapshot["statistics_store"])
            if self.snapshot.get("statistics_store") is not None else None
        )
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _scope(
        self,
        source_id: str | None = None,
        now: datetime | None = None,
        capability_id: str = "publication_metadata",
        expected_generation: str | None = None,
    ) -> dict[str, Any]:
        return self._index_scope(qs.query_store(
            self.store,
            source_id=source_id,
            limit=1,
            now=_now(now),
            capability_id=capability_id,
            expected_generation=expected_generation,
            canonical_artifacts=self.canonical_artifacts,
        ))

    def _index_scope(self, scope: dict[str, Any]) -> dict[str, Any]:
        if not self.snapshot.get("rebuild_failure"):
            return scope
        scope["data_status"] = "STALE"
        scope["answerable_no_match"] = False
        scope["source_gaps"] = [*scope["source_gaps"], {"reason": "QUERY_INDEX_REBUILD_FAILED"}]
        scope["query_coverage"] = {**scope["query_coverage"], "can_state_bounded_no_match": False,
                                   "status": "STALE", "index_status": "STALE_INDEX"}
        return scope

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
        try:
            official_sources = official_evidence_source_ids()
        except (OSError, ValueError) as error:
            raise GatewayError("GATE_FAILED", "official source catalog is unavailable", 503) from error
        catalog = []
        for item in store["items"]:
            # An HTTPS locator is not proof of authority. The producer marker
            # and the server-owned source catalog must agree before admission.
            if item.get("source_role") != "PRIMARY_OFFICIAL":
                continue
            if item.get("source_id") not in official_sources:
                continue
            source = sources.get(item["source_id"], {})
            freshness = str(item.get("freshness_status") or source.get("freshness_status") or "UNKNOWN").upper()
            current = (
                (not source_status or source_status.get(item["source_id"]) == "SNAPSHOT_RECENT")
                and
                source.get("source_health") == "PASS"
                and source.get("window_completeness") in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
                and freshness in {"FRESH", "RECENT"}
                # The projected status already resolved the item/source freshness
                # fallback, so the label on the row and this decision cannot drift.
                and item["verification_status"] == "VERIFIED"
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
                # Mirror the projected status: a stale publication item is never
                # confirmed-official evidence for the answer gate.
                "verification_status": item["verification_status"],
                "freshness": freshness,
                "is_current": current,
                "published_at": item.get("published_at"),
                "observed_at": item.get("fetched_at"),
                **{key: item[key] for key in ("document_revision_at", "date_basis") if item.get(key) is not None},
                "assertions": [
                    {"subject": f"publication:{canonical_id}:title", "value": item["title"]},
                    {"subject": f"publication:{canonical_id}:source_id", "value": item["source_id"]},
                ],
            })
        return sorted(catalog, key=lambda row: row["evidence_id"])

    def _trusted_statistics_evidence(self) -> list[dict[str, Any]]:
        if self.statistics_store is None:
            return []
        try:
            official_sources = official_evidence_source_ids()
        except (OSError, ValueError) as error:
            raise GatewayError("GATE_FAILED", "official source catalog is unavailable", 503) from error
        store_version = self.statistics_store["store_sha256"]
        catalog = []
        for row in self.statistics_store["statistics"]:
            value_text = row.get("value_text")
            locator = row.get("evidence_locator")
            if (
                row.get("source_id") not in official_sources
                or not isinstance(value_text, str)
                or not STATISTIC_DECIMAL_RE.fullmatch(value_text)
                or not isinstance(locator, str)
                or not locator.strip()
            ):
                continue
            catalog.append({
                "schema_version": 1,
                "evidence_id": f"STAT-{row['statistic_id']}",
                "evidence_type": "WRITTEN_OFFICIAL",
                "source_id": row["source_id"],
                "locator": f"{row['official_url']}#statistic:{quote(row['statistic_id'])}&locator={quote(locator)}",
                "document_version": store_version,
                "content_sha256": store_version,
                "trust_tier": "PRIMARY_REFERENCE",
                "verification_status": "CONFIRMED_OFFICIAL",
                "freshness": "STALE",
                "is_current": False,
                "published_at": row["updated_at"],
                "assertions": [{
                    "subject": f"statistic:{row['dataset_id']}:{row['metric']}",
                    "value": {
                        "value": value_text,
                        "period": row["period"],
                        "geography": row["geography"],
                        "unit": row["unit"],
                    },
                }],
            })
        return sorted(catalog, key=lambda row: row["evidence_id"])

    def _run_answer_gate(self, claims: list[dict[str, Any]]) -> dict[str, Any]:
        formal = qs.load_policy_module().formal_admission(qs.load_current_policy())
        if formal["status"] != "ADMITTED":
            raise GatewayError("RIGHTS_BLOCKED", "Current formal answer evidence lacks approved governance or publication rights", 503)
        for claim in claims:
            proposition = claim.get("proposition")
            subject = proposition.get("subject", "") if isinstance(proposition, dict) else ""
            normalized = "".join(unicodedata.normalize("NFC", str(subject)).split())
            if re.fullmatch(r"publication:.*:(?:title|source_id)", normalized) and claim.get("claim_type") != "STATUS":
                raise GatewayError("INVALID_ARGUMENTS", "Publication metadata cannot authorize other factual claim types")
        node = shutil.which("node")
        if not node:
            raise GatewayError("GATE_UNAVAILABLE", "answer evidence gate runtime is unavailable", 503)
        evidence = self._trusted_evidence_catalog(self.store, self.clock())
        if self.statistics_store is not None:
            self._require_formal_admission(self.statistics_store["source_ids"], domain=True)
            self._require_source_origins(self.statistics_store["statistics"])
        evidence.extend(self._trusted_statistics_evidence())
        if self.located_facts is not None:
            self._require_formal_admission({self.located_facts["document_version"]["source_id"]}, domain=True)
            document = self.located_facts["document_version"]
            self._require_source_origins([{"source_id": document["source_id"], "official_url": document[field]}
                                          for field in ("requested_url", "final_url")])
            # Re-check the live source catalog at request time: a source
            # demotion must stop feeding the gate without restarting the server.
            try:
                located_official = official_evidence_source_ids()
            except (OSError, ValueError) as error:
                raise GatewayError("GATE_FAILED", "official source catalog is unavailable", 503) from error
            document = self.located_facts["document_version"]
            facts = {fact["fact_id"]: fact for fact in self.located_facts["facts"]}
            for row in self.located_facts["evidence_catalog"]:
                if row.get("verification_status") != "CONFIRMED_OFFICIAL":
                    continue
                if row["source_id"] not in located_official:
                    continue
                fact = facts.get(row["fact_id"])
                if fact is None:
                    raise GatewayError("GATE_FAILED", "confirmed evidence references an unknown fact", 503)
                locator = quote(json.dumps(row["locator"], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
                value = fact.get("normalized_value")
                if value is None:
                    continue
                evidence.append({
                    "schema_version": 1,
                    "evidence_id": row["evidence_id"],
                    "evidence_type": "WRITTEN_OFFICIAL",
                    "source_id": row["source_id"],
                    "locator": f"{document['final_url']}#located-fact={quote(row['evidence_id'])}&locator={locator}",
                    "document_version": row["document_version_id"],
                    "content_sha256": row["content_sha256"],
                    "trust_tier": "PRIMARY_REFERENCE",
                    "verification_status": row["verification_status"],
                    "freshness": "STALE",
                    "is_current": False,
                    "published_at": fact.get("valid_time") or document.get("fetched_at"),
                    "assertions": [{
                        "subject": f"{fact['subject_id']}:{fact['predicate']}",
                        "value": value,
                    }],
                })
        evidence_ids = [row["evidence_id"] for row in evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise GatewayError("GATE_FAILED", "evidence catalog contains duplicate IDs", 503)
        if not _json_portable(evidence):
            raise GatewayError("GATE_FAILED", "evidence catalog contains non-portable JSON scalars", 503)
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
            raise GatewayError("GATE_FAILED", "answer evidence gate failed", 503)
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise GatewayError("GATE_FAILED", "answer evidence gate returned invalid JSON", 503) from error
        receipt = output.get("receipt") if isinstance(output, dict) else None
        if not isinstance(receipt, dict) or receipt.get("publication_hash") != payload["publication_hash"] or receipt.get("evidence_catalog_hash") != catalog_hash:
            raise GatewayError("GATE_FAILED", "answer evidence receipt is not bound to this publication", 503)
        # A host binding cannot prove the validator indexed the full catalog.
        if output.get("gate_status") == "BLOCKED":
            raise GatewayError("GATE_FAILED", "answer evidence gate refused the draft", 503)
        if receipt.get("indexed_evidence_count") != len(evidence):
            raise GatewayError("GATE_FAILED", "answer evidence gate did not index the full evidence catalog", 503)
        if receipt.get("validator_version") != ANSWER_VALIDATOR_VERSION:
            raise GatewayError("GATE_FAILED", "answer evidence validator version is not recognized", 503)
        receipt.pop("error_detail", None)
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
            **({"query_index_status": "STALE_INDEX", "rebuild_failure": self.snapshot["rebuild_failure"]}
               if self.snapshot.get("rebuild_failure") else {}),
        }

    @staticmethod
    def _arguments(tool: str, arguments: Any) -> dict[str, Any]:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise GatewayError("INVALID_ARGUMENTS", "arguments must be an object")
        allowed = {
            "search_evidence": {"q", "canonical_id", "source_id", "change_type", "limit", "cursor", "expected_generation"},
            "get_current_brief": set(),
            "get_publication_receipt": set(),
            "get_source_health": {"source_id"},
            "validate_answer": {"claims", "expected_generation"},
            **DOMAIN_TOOL_ARGUMENTS,
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

    def _domain_scope(self, store: dict[str, Any], now: datetime) -> dict[str, Any]:
        scope = self._scope(now=now, capability_id="publication_metadata")
        scope["data_status"] = "UNKNOWN"
        scope["source_gaps"] = [
            *scope["source_gaps"],
            {
                "source_id": None,
                "reason": "DOMAIN_STORE_SOURCE_HEALTH_UNBOUND",
                "domain_store_generation_id": store["generation_id"],
            },
        ]
        scope["query_coverage"] = {
            **scope["query_coverage"],
            "can_state_bounded_no_match": False,
            "domain_store_coverage_status": "UNVERIFIED",
            "domain_store_freshness": "UNKNOWN",
            "domain_store_source_health_bound": False,
            "domain_store_type": store["store_type"],
            "domain_store_generation_id": store["generation_id"],
            "domain_store_sha256": store["store_sha256"],
            "domain_source_ids": store["source_ids"],
        }
        return scope

    @staticmethod
    def _require_string(arguments: dict[str, Any], name: str, *, required: bool = False, max_length: int = 512) -> str | None:
        value = arguments.get(name)
        if value is None:
            if required:
                raise GatewayError("INVALID_ARGUMENTS", f"{name} is required")
            return None
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise GatewayError("INVALID_ARGUMENTS", f"{name} must be a bounded non-empty string")
        return value.strip()

    def _execute_domain(self, tool: str, args: dict[str, Any], now: datetime) -> dict[str, Any]:
        store = self.event_store if tool in {"search_events", "get_event", "compare_event_versions"} else self.statistics_store
        if store is None:
            raise GatewayError(
                "CAPABILITY_NOT_AVAILABLE",
                f"{tool} requires a validated canonical domain store; no such store is configured",
                422,
            )
        source_ids = set(store["source_ids"])
        for event in store["public_events"]:
            source_ids.update(event.get("independent_source_ids", []))
        self._require_formal_admission(source_ids, domain=True)
        self._require_source_origins([link for event in store["public_events"]
                                      for link in event["linked_document_versions"]])
        if tool == "search_events":
            for name in ("q", "region", "district", "agency", "category", "time_from", "time_to", "verification_status", "event_status", "public_event_id"):
                args[name] = self._require_string(args, name)
            if "time_zone" in args:
                args["time_zone"] = self._require_string(args, "time_zone", max_length=64)
            for name in ("tracked", "changed_only"):
                if name in args and not isinstance(args[name], bool):
                    raise GatewayError("INVALID_ARGUMENTS", f"{name} must be boolean")
            try:
                query_args, time_resolution = _resolve_event_time_bounds(args, now)
                result = query_domain.query_events(store, query_args)
            except ValueError as error:
                raise GatewayError("INVALID_ARGUMENTS", str(error)) from error
            scope = self._domain_scope(store, now)
            event_ids = [row["public_event_id"] for row in result["results"]]
            payload = {
                "event_ids": event_ids, "events": result["results"],
                **{key: value for key, value in result.items() if key != "results"},
                "domain_query_generation_id": result["query_generation_id"],
            }
            self._require_public_fields(result["results"], source_ids)
            if time_resolution is not None:
                payload["time_resolution"] = time_resolution
            return self._envelope(
                tool, args, scope,
                payload,
                result_count=result["result_count"], truncated=result["truncated"], result_type="public_events",
            )
        event_id = self._require_string(args, "event_id", required=True, max_length=256)
        try:
            if tool == "get_event":
                event = query_domain.get_event(store, event_id)
                self._require_public_fields(event, source_ids)
                scope = self._domain_scope(store, now)
                evidence_ids = [document["evidence_id"] for document in event["documents"]]
                return self._envelope(
                    tool, args, scope,
                    {"event_ids": [event_id], "evidence_ids": evidence_ids, "event": event,
                     **query_domain.trust_tier_counts([event]),
                     "domain_query_generation_id": store["generation_id"]},
                    result_count=1, result_type="public_event",
                )
            for name in ("before_version", "after_version"):
                args[name] = self._require_string(args, name, max_length=256)
            comparison = query_domain.compare_event_versions(
                store, event_id, before_version=args.get("before_version"), after_version=args.get("after_version")
            )
            self._require_public_fields(comparison, source_ids)
        except KeyError as error:
            missing = error.args[0] if error.args else event_id
            code = "EVENT_NOT_FOUND" if missing == event_id or tool == "get_event" else "EVENT_VERSION_NOT_FOUND"
            raise GatewayError(code, f"unknown event or version: {missing}", 404) from error
        except ValueError as error:
            raise GatewayError("INVALID_ARGUMENTS", str(error)) from error
        scope = self._domain_scope(store, now)
        return self._envelope(
            tool, args, scope,
            {"event_ids": [event_id], "comparison": comparison,
             **query_domain.trust_tier_counts([comparison]),
             "domain_query_generation_id": store["generation_id"]},
            result_count=1, result_type="event_comparison",
        )

    def _execute_statistics(self, args: dict[str, Any], now: datetime) -> dict[str, Any]:
        if self.statistics_store is None:
            raise GatewayError(
                "CAPABILITY_NOT_AVAILABLE",
                "query_statistics requires a validated canonical statistics store; no such store is configured",
                422,
            )
        self._require_formal_admission(self.statistics_store["source_ids"], domain=True)
        self._require_source_origins(self.statistics_store["statistics"])
        for name in ("dataset_id", "metric", "geography", "agency", "period_from", "period_to"):
            args[name] = self._require_string(args, name, max_length=512 if name in {"dataset_id", "metric", "geography"} else 128)
        if "provisional" in args and not isinstance(args["provisional"], bool):
            raise GatewayError("INVALID_ARGUMENTS", "provisional must be boolean")
        try:
            result = query_domain.query_statistics(self.statistics_store, args)
            self._require_public_fields(result["results"], self.statistics_store["source_ids"])
        except ValueError as error:
            raise GatewayError("INVALID_ARGUMENTS", str(error)) from error
        scope = self._domain_scope(self.statistics_store, now)
        return self._envelope(
            "query_statistics", args, scope,
            {"statistics": result["results"], **{key: value for key, value in result.items() if key != "results"},
             "domain_query_generation_id": result["query_generation_id"]},
            result_count=result["result_count"], truncated=result["truncated"], result_type="statistics",
        )

    def _require_public_fields(self, value, source_ids):
        rows = {row["source_id"]: row for row in qs.load_current_policy()["active_sources"]}
        permitted = [set(rows[sid]["rights_retention_public_policy_refs"]["public_fields"]) for sid in source_ids]
        allowed = set.intersection(*permitted) if permitted else set()
        def fields_allowed(item):
            if isinstance(item, dict):
                return set(item) <= allowed and all(fields_allowed(child) for child in item.values())
            if isinstance(item, list):
                return all(fields_allowed(child) for child in item)
            return True
        if not fields_allowed(value):
            raise GatewayError("SOURCE_GOVERNANCE_UNVERIFIED", "Domain projection contains fields outside the reviewed source whitelist", 503)

    def _require_source_origins(self, records):
        rows = {row["source_id"]: row for row in qs.load_current_policy()["active_sources"]}
        for record in records:
            source = rows.get(record["source_id"])
            parsed = urlsplit(record["official_url"])
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if source is None or origin not in source.get("approved_origins", []):
                raise GatewayError("SOURCE_GOVERNANCE_UNVERIFIED", "Domain document origin is not approved for its source", 503)

    def _require_formal_admission(self, source_ids=None, *, domain=False):
        policy_module = qs.load_policy_module()
        policy = qs.load_current_policy()
        formal = policy_module.formal_admission(policy)
        if formal["status"] != "ADMITTED":
            raise GatewayError("RIGHTS_BLOCKED", "Current formal query lacks approved governance or publication rights", 503)
        if source_ids is not None:
            rows = {row["source_id"]: row for row in policy["active_sources"]}
            if not set(source_ids) <= set(rows):
                raise GatewayError("SOURCE_GOVERNANCE_UNVERIFIED", "Domain records reference a source outside the approved active policy", 503)
            if domain and any(rows[sid]["rights_retention_public_policy_refs"]["public_projection"] != "EVIDENCE_BOUND_SUMMARY_ONLY" for sid in source_ids):
                raise GatewayError("SOURCE_GOVERNANCE_UNVERIFIED", "Metadata-only source permission cannot authorize domain facts", 503)

    def execute(self, tool: str, arguments: Any = None) -> dict[str, Any]:
        args = self._arguments(tool, arguments)
        now = _now(self.clock())
        if tool in {"search_events", "get_event", "compare_event_versions"}:
            return self._execute_domain(tool, args, now)
        if tool == "query_statistics":
            return self._execute_statistics(args, now)
        if tool == "validate_answer":
            if self.snapshot.get("rebuild_failure"):
                raise GatewayError("STALE_INDEX", "query rebuild failed; retained historical index cannot validate current answers", 503)
            claims = args.get("claims")
            if not isinstance(claims, list) or not 1 <= len(claims) <= 32:
                raise GatewayError("INVALID_ARGUMENTS", "validate_answer requires 1 to 32 structured claims")
            claim_keys = {
                "schema_version", "claim_id", "text", "claim_type", "temporal_scope",
                "proposition", "cited_evidence_ids",
            }
            if any(not isinstance(claim, dict) or set(claim) - claim_keys for claim in claims):
                raise GatewayError("INVALID_ARGUMENTS", "claims may not include evidence or trust fields")
            if not _json_portable(claims):
                raise GatewayError("INVALID_ARGUMENTS", "claims contain non-portable JSON scalars")
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
            result = self._index_scope(qs.query_store(self.store, now=now, **query_args,
                                                    canonical_artifacts=self.canonical_artifacts))
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
            self._require_formal_admission()
            scope = self._scope(now=now, capability_id="publication_metadata")
            try:
                brief = project_public_brief(self.brief)
            except ValueError as error:
                raise GatewayError("PUBLIC_PROJECTION_INVALID", "canonical brief violates public projection schema", 503) from error
            if not qs.load_policy_module().governance_module().brief_admission(qs.load_current_policy(), brief):
                raise GatewayError("RIGHTS_BLOCKED", "Current derived brief lacks reviewed summary fields and source permissions", 503)
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
        if tool == "get_publication_receipt":
            scope = self._scope(now=now, capability_id="publication_metadata")
            publication = self.store["generated_from"]
            publication_receipt = {
                "schema_version": 1,
                "receipt_type": "PUBLICATION_PROJECTION",
                "publication_id": publication["collection_run_id"],
                "publication_hash": publication["brief_sha256"],
                "generation_id": self.store["generation_id"],
                "artifact_hashes": {
                    "feed": publication["feed_sha256"],
                    "status": publication["status_sha256"],
                    "brief": publication["brief_sha256"],
                },
                "generated_at": publication["brief_generated_at"],
                "collection_status": publication["collection_status"],
                "publication_status": publication["publication_status"],
                "snapshot_complete": publication["snapshot_complete"],
                "freshness": _freshness(scope["data_status"]),
                "current_as_of_server_clock": scope["data_status"] == "SNAPSHOT_RECENT",
                "source_gaps": scope["source_gaps"],
                "policy_hash": self.store["policy"]["policy_hash"],
            }
            return self._envelope(
                tool,
                args,
                scope,
                {"publication_receipt": publication_receipt},
                result_count=1,
                result_type="publication_receipt",
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
            {"sources": selected,
             "source_rights": [{"source_id": row["source_id"],
                 "rights_status": RETENTION_POLICY["source_policies"][row["source_id"]]["rights_status"],
                 "review_required": RETENTION_POLICY["source_policies"][row["source_id"]]["review_required"]}
                for row in selected],
             "formal_admission": qs.load_policy_module().formal_admission(qs.load_current_policy())},
            result_count=len(selected),
        )

    def capabilities(self) -> dict[str, Any]:
        available = list(CAPABILITIES)
        if self.event_store is not None:
            available.extend(["search_events", "get_event", "compare_event_versions"])
        if self.statistics_store is not None:
            available.append("query_statistics")
        return {
            "schema_version": 1,
            "server_version": SERVER_VERSION,
            "read_only": True,
            "capabilities": available,
            "unavailable_capabilities": sorted(set(UNIMPLEMENTED) - set(available)),
            "policy": self.store["policy"],
            "retention": {
                "policy_version": RETENTION_POLICY["policy_version"],
                "policy_hash": RETENTION_POLICY["policy_hash"],
                "public_projection": "METADATA_LINK_ONLY",
                "full_text_allowed": False,
            },
        }

    def mcp_tools(self) -> list[dict[str, Any]]:
        tools = list(MCP_TOOLS)
        if self.event_store is not None:
            tools.extend(DOMAIN_MCP_TOOLS[:3])
        if self.statistics_store is not None:
            tools.append(DOMAIN_MCP_TOOLS[3])
        return tools

    def health(self) -> dict[str, Any]:
        scope = self._scope()
        return {
            "schema_version": 1,
            "service": "govintel-query-gateway",
            "server_version": SERVER_VERSION,
            "status": "degraded" if self.snapshot.get("rebuild_failure") else "ok",
            **({"query_index_status": "STALE_INDEX", "rebuild_failure": self.snapshot["rebuild_failure"]}
               if self.snapshot.get("rebuild_failure") else {}),
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
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "govintel-query-gateway", "version": SERVER_VERSION},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": gateway.mcp_tools()}}
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
        body = _json_bytes(payload)
        if len(body) > MAX_RESPONSE_BYTES:
            error = _error_payload(GatewayError("RESPONSE_TOO_LARGE", "response exceeds the byte limit", 500))
            if isinstance(payload, dict) and payload.get("jsonrpc") == "2.0":
                error = {"jsonrpc": "2.0", "id": payload.get("id"), "error": error["error"]}
            body = _json_bytes(error)
            status = 500
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if urlsplit(self.path).path == "/mcp":
            self.send_header("MCP-Protocol-Version", MCP_PROTOCOL_VERSION)
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

    def _mcp_origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        allowed = getattr(self.server, "allow_origin", None)
        return bool(allowed and allowed != "*" and origin == allowed)

    def _require_mcp_origin(self) -> bool:
        if self._mcp_origin_allowed():
            return True
        self._send(_error_payload(GatewayError("ORIGIN_NOT_ALLOWED", "MCP Origin is not allowed", 403)), 403)
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
        if urlsplit(self.path).path == "/mcp" and not self._require_mcp_origin():
            return
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
            if path == "/mcp" and not self._require_mcp_origin():
                return
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
    parser.add_argument("--located-facts-bundle", type=Path)
    parser.add_argument("--query-store", type=Path)
    parser.add_argument("--public-events", type=Path)
    parser.add_argument("--statistics", type=Path)
    parser.add_argument("--last-good-snapshot", type=Path, help="Operator-managed persistent generation cache; failed rebuilds serve a validated stale snapshot")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    server = build_server(
        args.host, args.port,
        QueryGateway(load_snapshot(args.located_facts_bundle, args.query_store, args.public_events, args.statistics,
                                   last_good_path=args.last_good_snapshot)),
        args.allow_origin, args.rate_limit,
    )
    print(f"QUERY_GATEWAY_LISTENING http://{args.host}:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
