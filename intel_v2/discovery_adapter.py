"""Bounded consumer for the Taiwan Intel Dashboard discovery feed.

The feed is a radar input only. This module keeps discovery candidates
separate from canonical facts/events and accepts official evidence only from a
server-controlled match list.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from intel_v2.entity_binding import registry_runtime


SCHEMA_VERSION = 1
FEED_ID = "taiwan-intel-dashboard:govintel-discovery"
DISCOVERY_SOURCE = "taiwan-intel-dashboard"
MAX_TTL_HOURS = 72
MAX_CANDIDATES_PER_SNAPSHOT = 50
MAX_RETAINED_CANDIDATES = 500
DEFAULT_MAX_RETAINED_CANDIDATES = MAX_RETAINED_CANDIDATES
DEFAULT_MAX_CANDIDATES = MAX_CANDIDATES_PER_SNAPSHOT
MAX_FEED_ITEMS = 200
MAX_FEED_BYTES = 256 * 1024
VALID_STATES = {"ACTIVE", "DEGRADED", "RESTORING", "PAUSED"}
VALID_AUTHORITIES = {"official", "media"}
VALID_RIGHTS = {"OPEN_DATA", "REVIEW_REQUIRED"}
ITEM_FIELDS = {
    "discovery_id", "title", "event_time", "observed_at", "region", "lat", "lng",
    "location_precision", "category", "risk_level", "summary", "entities", "topic",
    "source_url", "publisher_name", "authority", "source_confidence", "ingest_method",
    "original_dataset_id", "record_ref", "original_source_identity", "rights_status",
}
DISCOVERY_ID = re.compile(r"^gd-[0-9a-f]{16}$")
SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
TAICHUNG = re.compile(r"臺中|台中")
RELEVANCE_TERMS = ("警政", "警察", "反詐", "詐騙", "消防", "道路", "交通", "災防", "NCDR", "氣象")
MATERIAL_FIELDS = (
    "title", "event_time", "region", "lat", "lng", "location_precision", "category",
    "source_url", "publisher_name", "authority", "original_dataset_id", "record_ref",
    "original_source_identity",
)
PRESENTATION_FIELDS = ("summary", "risk_level", "entities", "topic")


class DiscoveryFeedError(ValueError):
    """The producer contract is invalid; callers must fail closed."""


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else _canonical(value)).hexdigest()


def _timestamp(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DiscoveryFeedError(f"{field} must be an ISO timestamp or null")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DiscoveryFeedError(f"{field} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise DiscoveryFeedError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_now(value: datetime | None) -> datetime:
    result = value or datetime.now(timezone.utc)
    if result.tzinfo is None:
        raise ValueError("now must include a timezone")
    return result.astimezone(timezone.utc)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_feed(feed: Any) -> dict[str, Any]:
    """Validate the producer envelope without silently repairing it."""

    if not isinstance(feed, dict):
        raise DiscoveryFeedError("feed must be an object")
    required = {
        "schema_version", "generated_at", "operating_state", "stale", "operating_state_source",
        "source_snapshot_hash", "window", "truncated", "item_count", "excluded_counts", "items",
    }
    missing = required - set(feed)
    if missing:
        raise DiscoveryFeedError(f"feed missing fields: {sorted(missing)}")
    unknown = set(feed) - required
    if unknown:
        raise DiscoveryFeedError(f"feed has unsupported fields: {sorted(unknown)}")
    try:
        encoded_size = len(_canonical(feed))
    except (TypeError, ValueError, RecursionError) as exc:
        raise DiscoveryFeedError("feed contains non-JSON values") from exc
    if encoded_size > MAX_FEED_BYTES:
        raise DiscoveryFeedError(f"feed exceeds {MAX_FEED_BYTES} bytes")
    if not _is_int(feed["schema_version"]) or feed["schema_version"] != SCHEMA_VERSION:
        raise DiscoveryFeedError("unsupported feed schema_version")
    _timestamp(feed["generated_at"], "generated_at")
    if feed["operating_state"] not in VALID_STATES:
        raise DiscoveryFeedError("invalid operating_state")
    if feed["operating_state_source"] not in {"contract", "contract-missing-or-invalid"}:
        raise DiscoveryFeedError("invalid operating_state_source")
    if not isinstance(feed["stale"], bool) or feed["stale"] != (feed["operating_state"] != "ACTIVE"):
        raise DiscoveryFeedError("stale does not match operating_state")
    snapshot_hash = feed["source_snapshot_hash"]
    if snapshot_hash is not None and (not isinstance(snapshot_hash, str) or not SHA256.fullmatch(snapshot_hash)):
        raise DiscoveryFeedError("invalid source_snapshot_hash")
    window = feed["window"]
    if not isinstance(window, dict) or set(window) != {"since_hours"} or not _is_int(window["since_hours"]) or window["since_hours"] < 1:
        raise DiscoveryFeedError("invalid window")
    if not isinstance(feed["truncated"], bool) or not _is_int(feed["item_count"]):
        raise DiscoveryFeedError("invalid feed counters")
    if not isinstance(feed["excluded_counts"], dict) or any(not _is_int(v) or v < 0 for v in feed["excluded_counts"].values()):
        raise DiscoveryFeedError("invalid excluded_counts")
    items = feed["items"]
    if not isinstance(items, list) or feed["item_count"] != len(items):
        raise DiscoveryFeedError("item_count does not match items")
    if len(items) > MAX_FEED_ITEMS:
        raise DiscoveryFeedError(f"feed exceeds {MAX_FEED_ITEMS} items")
    for index, item in enumerate(items):
        _validate_item(item, index)
    seen_ids: set[str] = set()
    prior_time: datetime | None = None
    prior_id: str | None = None
    prior_was_null = False
    for item in items:
        discovery_id = item["discovery_id"]
        if discovery_id in seen_ids:
            raise DiscoveryFeedError("duplicate discovery_id")
        seen_ids.add(discovery_id)
        event_time = _timestamp(item["event_time"], "event_time")
        if prior_id is not None:
            if prior_was_null and event_time is not None:
                raise DiscoveryFeedError("items are not in producer order")
            if not prior_was_null and event_time is not None:
                if event_time > prior_time or (event_time == prior_time and discovery_id < prior_id):
                    raise DiscoveryFeedError("items are not in producer order")
            if prior_was_null and event_time is None and discovery_id < prior_id:
                raise DiscoveryFeedError("items are not in producer order")
        prior_time = event_time
        prior_id = discovery_id
        prior_was_null = event_time is None
    return dict(feed)


def _validate_item(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise DiscoveryFeedError(f"items[{index}] must be an object")
    unknown = set(item) - ITEM_FIELDS
    if unknown:
        raise DiscoveryFeedError(f"items[{index}] has unsupported fields: {sorted(unknown)}")
    required = {
        "discovery_id", "title", "event_time", "observed_at", "category", "authority",
        "source_url", "original_source_identity", "rights_status",
    }
    missing = required - set(item)
    if missing:
        raise DiscoveryFeedError(f"items[{index}] missing fields: {sorted(missing)}")
    if not isinstance(item["discovery_id"], str) or not DISCOVERY_ID.fullmatch(item["discovery_id"]):
        raise DiscoveryFeedError(f"items[{index}] has invalid discovery_id")
    for field in (
        "title", "category", "source_url", "original_source_identity", "region",
        "location_precision", "risk_level", "summary", "topic", "publisher_name",
        "source_confidence", "ingest_method", "original_dataset_id", "record_ref",
    ):
        if item.get(field) is not None and not isinstance(item.get(field), str):
            raise DiscoveryFeedError(f"items[{index}].{field} must be string or null")
    for field in ("lat", "lng"):
        value = item.get(field)
        if value is not None:
            try:
                valid_number = not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
            except (OverflowError, TypeError):
                valid_number = False
            if not valid_number:
                raise DiscoveryFeedError(f"items[{index}].{field} must be a finite number or null")
    entities = item.get("entities")
    if entities is not None and (
        not isinstance(entities, list) or len(entities) > 100
        or any(not isinstance(value, str) or not value.strip() or len(value) > 256 for value in entities)
    ):
        raise DiscoveryFeedError(f"items[{index}].entities must be a bounded string array or null")
    _timestamp(item["event_time"], f"items[{index}].event_time")
    _timestamp(item["observed_at"], f"items[{index}].observed_at")
    if item["authority"] not in VALID_AUTHORITIES or item["rights_status"] not in VALID_RIGHTS:
        raise DiscoveryFeedError(f"items[{index}] has invalid authority or rights_status")
    if not item["source_url"] and not item["original_source_identity"]:
        raise DiscoveryFeedError(f"items[{index}] has no source identity")


def load_feed(path: str) -> dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_FEED_BYTES + 1)
        if len(raw) > MAX_FEED_BYTES:
            raise DiscoveryFeedError(f"feed exceeds {MAX_FEED_BYTES} bytes")
        payload = json.loads(raw.decode("utf-8"))
    except DiscoveryFeedError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryFeedError(f"cannot load feed: {path}") from exc
    return validate_feed(payload)


def generation_id(feed: dict[str, Any]) -> str:
    validated = validate_feed(feed)
    material = {
        "schema_version": validated["schema_version"],
        "generated_at": validated["generated_at"],
        "operating_state": validated["operating_state"],
        "stale": validated["stale"],
        "source_snapshot_hash": validated["source_snapshot_hash"],
        "item_ids": sorted(item["discovery_id"] for item in validated["items"]),
    }
    return "gdf-" + _digest(material)[:24]


def _relevance(item: dict[str, Any]) -> tuple[bool, str]:
    region = str(item.get("region") or "")
    if TAICHUNG.search(region):
        return True, "TAICHUNG_REGION"
    if region and ("市" in region or "縣" in region):
        return False, "OUT_OF_SCOPE_REGION"
    text = " ".join(str(item.get(field) or "") for field in ("title", "category", "summary", "topic", "publisher_name"))
    if TAICHUNG.search(text):
        return True, "TAICHUNG_REFERENCE"
    for term in RELEVANCE_TERMS:
        if term in text:
            return True, f"PUBLIC_SAFETY_TERM:{term}"
    return False, "OUT_OF_SCOPE"


def _hash_fields(item: dict[str, Any], fields: tuple[str, ...]) -> str:
    return _digest({field: item.get(field) for field in fields})


def _candidate_entities(item: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    """Annotate radar hints only; these IDs never participate in official matching."""
    module = registry_runtime()
    receipt = module.registry_receipt(registry)
    region = item.get("region")
    jurisdiction = region if isinstance(region, str) and region.strip() else None
    hints = item.get("entities")
    if hints is not None and (not isinstance(hints, list) or len(hints) > 100
                              or any(not isinstance(value, str) or not value.strip() or len(value) > 256 for value in hints)):
        raise DiscoveryFeedError("entities hints must be a bounded string array or null")
    agency_ids: set[str] = set()
    location_ids: set[str] = set()
    unresolved: list[str] = []
    ambiguous: list[str] = []
    names = list(hints or [])
    if item["authority"] == "official" and isinstance(item.get("publisher_name"), str) and item["publisher_name"].strip():
        names.append(item["publisher_name"])
    for name in names:
        matches = [module.resolve(registry, kind, name, jurisdiction) for kind in ("agency", "location")]
        agency_ids.update(match["entity_id"] for match in matches if match["status"] == "RESOLVED" and match["kind"] == "agency")
        location_ids.update(match["entity_id"] for match in matches if match["status"] == "RESOLVED" and match["kind"] == "location")
        if any(match["status"] == "AMBIGUOUS" for match in matches):
            ambiguous.append(name)
        elif all(match["status"] == "NO_MATCH" for match in matches):
            unresolved.append(name)
    return {
        **receipt,
        "status": "CANDIDATE_ONLY",
        "agency_ids": sorted(agency_ids),
        "location_ids": sorted(location_ids),
        "unresolved_hints": sorted(set(unresolved)),
        "ambiguous_hints": sorted(set(ambiguous)),
    }


def candidate_from_item(feed: dict[str, Any], item: dict[str, Any], ttl_hours: int = 72,
                        *, entity_registry: dict[str, Any] | None = None) -> dict[str, Any]:
    validated = validate_feed(feed)
    if not _is_int(ttl_hours) or not 1 <= ttl_hours <= MAX_TTL_HOURS:
        raise ValueError(f"ttl_hours must be between 1 and {MAX_TTL_HOURS}")
    relevant, relevance_reason = _relevance(item)
    observed = _timestamp(item.get("observed_at"), "observed_at")
    ttl_anchor = observed or _timestamp(item.get("event_time"), "event_time")
    if ttl_anchor is None:
        raise DiscoveryFeedError("discovery item has no source-time TTL anchor")
    status = "DISCOVERY_UNVERIFIED" if item["authority"] == "media" else "OFFICIAL_CANDIDATE"
    registry = entity_registry if entity_registry is not None else registry_runtime().load_registry()
    return {
        "candidate_id": item["discovery_id"],
        "discovery_source": DISCOVERY_SOURCE,
        "headline": item.get("title"),
        "event_time": item.get("event_time"),
        "observed_at": item.get("observed_at"),
        "region": item.get("region"),
        "location": {"lat": item.get("lat"), "lng": item.get("lng"), "precision": item.get("location_precision")},
        "category": item.get("category"),
        "source_url": item.get("source_url"),
        "publisher": item.get("publisher_name"),
        "authority": item["authority"],
        "original_dataset_id": item.get("original_dataset_id"),
        "record_ref": item.get("record_ref"),
        "original_source_identity": item.get("original_source_identity"),
        "rights_status": item["rights_status"],
        "candidate_hints": {"summary": item.get("summary"), "risk_level": item.get("risk_level"), "entities": item.get("entities"), "topic": item.get("topic")},
        "candidate_entities": _candidate_entities(item, registry),
        "upstream_generation_id": generation_id(validated),
        "upstream_content_hash": validated["source_snapshot_hash"],
        "upstream_operating_state": validated["operating_state"],
        "upstream_current": validated["operating_state"] == "ACTIVE" and not validated["stale"],
        "upstream_gap": validated["operating_state"] == "DEGRADED",
        "relevant": relevant,
        "relevance_reason": relevance_reason,
        "material_hash": _hash_fields(item, MATERIAL_FIELDS),
        "presentation_hash": _hash_fields(item, PRESENTATION_FIELDS),
        "verification_status": status,
        "official_match_status": "NOT_CHECKED",
        "verification_sources_checked": [],
        "official_document_versions": [],
        "official_document_provenance": [],
        "independent_source_count": 0,
        "canonical_write": False,
        "expires_at": _iso(ttl_anchor + timedelta(hours=ttl_hours)),
    }


def classify_change(previous: dict[str, Any] | None, current: dict[str, Any]) -> str:
    if previous is None:
        return "MATERIAL_DISCOVERY_CHANGE"
    if previous.get("material_hash") != current.get("material_hash"):
        return "MATERIAL_DISCOVERY_CHANGE"
    if previous.get("presentation_hash") != current.get("presentation_hash"):
        return "PRESENTATION_ONLY"
    return "NO_CHANGE"


def _validate_official_documents(documents: list[dict[str, Any]]) -> None:
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise DiscoveryFeedError(f"official_documents[{index}] must be an object")
        required = {"document_id", "document_version_id", "source_id", "authority"}
        if not required.issubset(document) or document["authority"] != "official":
            raise DiscoveryFeedError(f"official_documents[{index}] is not server-controlled official evidence")


def _document_matches(candidate: dict[str, Any], document: dict[str, Any]) -> bool:
    if document.get("candidate_id") == candidate["candidate_id"]:
        return True
    if document.get("original_source_identity") and document["original_source_identity"] == candidate["original_source_identity"]:
        return True
    if document.get("official_url") and document["official_url"] == candidate["source_url"]:
        return True
    return False


def _official_provenance(matched: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fields = (
        "document_id", "document_version_id", "source_id", "authority",
        "original_source_identity", "official_url", "fact_fingerprint",
        "public_event_id", "existing_public_event_id",
    )
    return [
        {field: document[field] for field in fields if field in document}
        for document in sorted(matched, key=lambda row: (str(row["document_version_id"]), str(row["source_id"])))
    ]


def verify_candidate(
    candidate: dict[str, Any],
    official_documents: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    historical_replay: bool = False,
    canary_only: bool = False,
) -> dict[str, Any]:
    """Attach only server-controlled official matches; never mutate canonical state."""

    _validate_official_documents(official_documents)
    if not isinstance(historical_replay, bool) or not isinstance(canary_only, bool):
        raise ValueError("historical_replay and canary_only must be booleans")
    if historical_replay and candidate.get("upstream_operating_state") != "PAUSED":
        raise DiscoveryFeedError("historical_replay is only valid for PAUSED snapshots")
    if canary_only and candidate.get("upstream_operating_state") != "RESTORING":
        raise DiscoveryFeedError("canary_only is only valid for RESTORING snapshots")
    reference = _utc_now(now)
    expires = _timestamp(candidate.get("expires_at"), "expires_at")
    result = dict(candidate)
    anchor = _timestamp(candidate.get("observed_at"), "observed_at")
    if anchor is None:
        anchor = _timestamp(candidate.get("event_time"), "event_time")
    if expires is not None and anchor is None:
        raise DiscoveryFeedError("candidate has no source-time TTL anchor")
    if expires is not None and anchor is not None and expires > anchor + timedelta(hours=MAX_TTL_HOURS):
        raise DiscoveryFeedError("candidate exceeds maximum TTL")
    if expires is None or reference >= expires:
        result["verification_status"] = "EXPIRED"
        result["official_match_status"] = "EXPIRED"
        result["verification_reason"] = "TTL_EXPIRED"
        result["canonical_write"] = False
        return result
    if candidate.get("upstream_operating_state") == "RESTORING" and canary_only:
        matched = [document for document in official_documents if _document_matches(candidate, document)]
        fingerprints = {str(doc.get("fact_fingerprint") or doc["document_version_id"]) for doc in matched}
        if not matched:
            canary_status = "NO_OFFICIAL_MATCH"
        elif len(fingerprints) > 1 or any(doc.get("conflict") is True for doc in matched):
            canary_status = "CONFLICT"
        else:
            canary_status = "OFFICIAL_MATCH"
        result["canary_evaluation"] = {
            "status": canary_status,
            "official_document_versions": sorted({str(doc["document_version_id"]) for doc in matched}),
            "official_document_provenance": _official_provenance(matched),
            "independent_source_count": len({str(doc.get("original_source_identity") or doc["source_id"]) for doc in matched}),
        }
        result["verification_status"] = "DISCOVERY_UNVERIFIED" if candidate["authority"] == "media" else "OFFICIAL_CANDIDATE"
        result["canonical_write"] = False
        return result
    if not candidate.get("upstream_current") and not historical_replay:
        result["verification_blocked_reason"] = "UPSTREAM_NOT_CURRENT"
        return result
    matched = [document for document in official_documents if _document_matches(candidate, document)]
    result["verification_sources_checked"] = sorted({str(doc["source_id"]) for doc in official_documents})
    result["official_document_versions"] = sorted({str(doc["document_version_id"]) for doc in matched})
    result["official_document_provenance"] = _official_provenance(matched)
    result["independent_source_count"] = len({str(doc.get("original_source_identity") or doc["source_id"]) for doc in matched})
    if not matched:
        result["official_match_status"] = "NO_OFFICIAL_MATCH"
        if candidate["authority"] != "media":
            result["verification_status"] = "NO_OFFICIAL_MATCH"
        result["verification_reason"] = "NO_OFFICIAL_SOURCE_MATCH"
        return result
    fingerprints = {str(doc.get("fact_fingerprint") or doc.get("document_version_id")) for doc in matched}
    if len(fingerprints) > 1 or any(doc.get("conflict") is True for doc in matched):
        result["official_match_status"] = "CONFLICT"
        if candidate["authority"] != "media":
            result["verification_status"] = "CONFLICT"
        result["verification_reason"] = "OFFICIAL_SOURCES_DISAGREE"
        return result
    result["official_match_status"] = "VERIFIED_OFFICIAL"
    if candidate["authority"] == "media":
        result["verification_status"] = "DISCOVERY_UNVERIFIED"
        result["verification_reason"] = "OFFICIAL_PROVENANCE_ATTACHED_FOR_FUSION"
    else:
        result["verification_status"] = "VERIFIED_OFFICIAL"
        result["verification_reason"] = "SERVER_CONTROLLED_OFFICIAL_MATCH"
    result["canonical_write"] = False
    return result


def _previous_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    if not isinstance(state, dict) or not isinstance(state.get("candidates"), dict):
        raise DiscoveryFeedError("invalid consumer state")
    return state


def _prune_candidate_store(
    candidates: dict[str, Any], *, now: datetime, limit: int
) -> dict[str, dict[str, Any]]:
    """Keep unexpired candidate records under a deterministic hard cap."""

    retained: list[tuple[datetime, str, dict[str, Any]]] = []
    for candidate_id, candidate in candidates.items():
        if not isinstance(candidate_id, str) or not isinstance(candidate, dict):
            raise DiscoveryFeedError("invalid candidate store entry")
        if candidate.get("candidate_id") != candidate_id:
            raise DiscoveryFeedError("candidate store key does not match candidate_id")
        expires = _timestamp(candidate.get("expires_at"), "candidate.expires_at")
        anchor = _timestamp(candidate.get("observed_at"), "candidate.observed_at")
        if anchor is None:
            anchor = _timestamp(candidate.get("event_time"), "candidate.event_time")
        if expires is not None and anchor is None:
            raise DiscoveryFeedError("candidate store entry has no source-time TTL anchor")
        if expires is not None and anchor is not None and expires > anchor + timedelta(hours=MAX_TTL_HOURS):
            raise DiscoveryFeedError("candidate store entry exceeds maximum TTL")
        if expires is not None and expires > now:
            retained.append((expires, candidate_id, candidate))
    retained.sort(key=lambda row: (row[0], row[1]), reverse=True)
    return {candidate_id: candidate for _, candidate_id, candidate in retained[:limit]}


def _upstream_mode(state: str, *, historical_replay: bool) -> str:
    if state == "ACTIVE":
        return "CURRENT"
    if state == "DEGRADED":
        return "DEGRADED_GAP"
    if state == "RESTORING":
        return "CANARY_ONLY"
    if state == "PAUSED":
        return "HISTORICAL_REPLAY" if historical_replay else "PAUSED"
    raise DiscoveryFeedError("invalid operating_state")


def ingest_feed(
    feed: dict[str, Any],
    *,
    previous_state: dict[str, Any] | None = None,
    official_documents: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    ttl_hours: int = 72,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    historical_replay: bool = False,
    entity_registry: dict[str, Any] | None = None,
    canary_mode: bool = False,
    max_retained_candidates: int = DEFAULT_MAX_RETAINED_CANDIDATES,
) -> dict[str, Any]:
    """Apply one full feed snapshot idempotently and return receipt plus state."""

    validated = validate_feed(feed)
    module = registry_runtime()
    registry = entity_registry if entity_registry is not None else module.load_registry()
    module.validate_registry(registry)
    registry_receipt = module.registry_receipt(registry)
    previous = _previous_state(previous_state)
    documents = official_documents or []
    _validate_official_documents(documents)
    if not _is_int(ttl_hours) or not 1 <= ttl_hours <= MAX_TTL_HOURS:
        raise ValueError(f"ttl_hours must be between 1 and {MAX_TTL_HOURS}")
    if not _is_int(max_candidates) or not 1 <= max_candidates <= MAX_CANDIDATES_PER_SNAPSHOT:
        raise ValueError(f"max_candidates must be between 1 and {MAX_CANDIDATES_PER_SNAPSHOT}")
    if not _is_int(max_retained_candidates) or not 1 <= max_retained_candidates <= MAX_RETAINED_CANDIDATES:
        raise ValueError(f"max_retained_candidates must be between 1 and {MAX_RETAINED_CANDIDATES}")
    if not isinstance(historical_replay, bool) or not isinstance(canary_mode, bool):
        raise ValueError("historical_replay and canary_mode must be booleans")
    operating_state = validated["operating_state"]
    if historical_replay and operating_state != "PAUSED":
        raise DiscoveryFeedError("historical_replay is only valid for PAUSED snapshots")
    if canary_mode and operating_state != "RESTORING":
        raise DiscoveryFeedError("canary_mode is only valid for RESTORING snapshots")
    mode = _upstream_mode(operating_state, historical_replay=historical_replay)
    reference_now = _utc_now(now)
    previous_candidates = _prune_candidate_store(
        previous.get("candidates", {}) if previous else {},
        now=reference_now,
        limit=max_retained_candidates,
    )
    current_generation = generation_id(validated)
    if previous and previous.get("last_seen_generated_at"):
        old_time = _timestamp(previous["last_seen_generated_at"], "last_seen_generated_at")
        if _timestamp(validated["generated_at"], "generated_at") < old_time:
            raise DiscoveryFeedError("out-of-order upstream snapshot")
    if previous and previous.get("last_seen_generation_id") == current_generation:
        last_receipt = previous.get("last_receipt")
        if not isinstance(last_receipt, dict):
            raise DiscoveryFeedError("same generation has no replay receipt")
        same_execution = (
            last_receipt.get("historical_replay") == historical_replay
            and last_receipt.get("canary_mode") == canary_mode
            and last_receipt.get("ttl_hours") == ttl_hours
            and last_receipt.get("max_candidates") == max_candidates
            and last_receipt.get("retained_candidate_limit") == max_retained_candidates
        )
        if (last_receipt.get("entity_registry") == registry_receipt and same_execution
                and not last_receipt.get("state_truncated")):
            previous_active = previous.get("active_candidates", [])
            retained_active = [item for item in previous_active if item in previous_candidates]
            if retained_active == previous_active:
                replay_receipt = dict(last_receipt)
                replay_receipt["replayed"] = True
                replay_state = {**previous, "candidates": previous_candidates, "active_candidates": retained_active}
                replay_candidates = [previous_candidates[item] for item in retained_active]
                return {"state": replay_state, "candidates": replay_candidates,
                        "receipt": replay_receipt, "replayed": True}

    projected = [candidate_from_item(validated, item, ttl_hours, entity_registry=registry) for item in validated["items"]]
    projected = [candidate for candidate in projected if candidate["relevant"]]
    projected.sort(key=lambda candidate: (candidate.get("event_time") is not None, candidate.get("event_time") or "", candidate["candidate_id"]), reverse=True)
    truncated = len(projected) > max_candidates
    projected = projected[:max_candidates]
    candidates: list[dict[str, Any]] = []
    added: list[str] = []
    updated: list[str] = []
    presentation_only: list[str] = []
    registry_rebound: list[str] = []
    verification_calls: list[str] = []
    expired_ids: list[str] = []
    for candidate in projected:
        candidate["upstream_mode"] = mode
        candidate["historical_only"] = mode == "HISTORICAL_REPLAY"
        old = previous_candidates.get(candidate["candidate_id"])
        change = classify_change(old, candidate)
        if (old is not None and old.get("candidate_entities", {}).get("registry_hash")
                != candidate["candidate_entities"]["registry_hash"]):
            registry_rebound.append(candidate["candidate_id"])
        candidate["change_class"] = change
        expires = _timestamp(candidate["expires_at"], "expires_at")
        if expires is None or reference_now >= expires:
            candidate["verification_status"] = "EXPIRED"
            candidate["official_match_status"] = "EXPIRED"
            candidate["verification_reason"] = "TTL_EXPIRED"
            expired_ids.append(candidate["candidate_id"])
            candidates.append(candidate)
            continue
        state_changed = old is not None and old.get("upstream_mode") != mode
        canary_only = operating_state == "RESTORING" and canary_mode
        should_verify = (
            operating_state == "ACTIVE"
            or (operating_state == "PAUSED" and historical_replay)
            or canary_only
        )
        if old is not None and change != "MATERIAL_DISCOVERY_CHANGE" and not state_changed and not historical_replay and not canary_only:
            candidate["verification_status"] = old.get("verification_status", candidate["verification_status"])
            for key in ("official_match_status", "verification_sources_checked", "official_document_versions",
                        "official_document_provenance", "independent_source_count", "verification_reason",
                        "verification_blocked_reason"):
                if key in old:
                    candidate[key] = old[key]
            if change == "PRESENTATION_ONLY":
                presentation_only.append(candidate["candidate_id"])
        else:
            if old is None:
                added.append(candidate["candidate_id"])
            else:
                updated.append(candidate["candidate_id"])
                if old.get("verification_status") == "VERIFIED_OFFICIAL" and mode != "CURRENT":
                    candidate["previous_verification_status"] = "VERIFIED_OFFICIAL"
            if should_verify:
                verification_calls.append(candidate["candidate_id"])
                candidate = verify_candidate(
                    candidate,
                    documents,
                    now=reference_now,
                    historical_replay=historical_replay,
                    canary_only=canary_only,
                )
            else:
                candidate["verification_blocked_reason"] = {
                    "PAUSED": "UPSTREAM_PAUSED",
                    "RESTORING": "RESTORING_CANARY_REQUIRED",
                    "DEGRADED": "UPSTREAM_DEGRADED_GAP",
                }[operating_state]
        candidates.append(candidate)

    verified_all = [candidate for candidate in candidates if candidate.get("official_match_status") == "VERIFIED_OFFICIAL"]
    verified = verified_all if mode == "CURRENT" else []
    historical_verified_count = len(verified_all) if mode == "HISTORICAL_REPLAY" else 0
    canary_results = [candidate.get("canary_evaluation", {}).get("status") for candidate in candidates]
    conflicts = [candidate for candidate in candidates if candidate.get("official_match_status") == "CONFLICT"]
    no_match = [candidate for candidate in candidates if candidate.get("official_match_status") == "NO_OFFICIAL_MATCH"]
    if mode == "CANARY_ONLY":
        status = "CANARY_ONLY"
    elif mode == "HISTORICAL_REPLAY":
        status = "HISTORICAL_REPLAY"
    elif mode == "PAUSED":
        status = "PAUSED"
    elif mode == "DEGRADED_GAP":
        status = "DEGRADED_GAP"
    else:
        status = "OFFICIAL_PROVENANCE_AVAILABLE" if verified else "INGESTED"
    all_candidates = dict(previous_candidates)
    for candidate in candidates:
        if candidate.get("verification_status") != "EXPIRED":
            all_candidates[candidate["candidate_id"]] = candidate
    retained_candidates = _prune_candidate_store(
        all_candidates,
        now=reference_now,
        limit=max_retained_candidates,
    )
    retained_ids = set(retained_candidates)
    state_truncated = len(all_candidates) > len(retained_candidates)
    receipt = {
        "schema_version": 1,
        "entity_registry": registry_receipt,
        "consumer_run_id": f"govintel-discovery:{current_generation}:{registry_receipt['registry_hash'][:16]}",
        "upstream_feed_id": FEED_ID,
        "upstream_generation_id": current_generation,
        "upstream_content_hash": validated["source_snapshot_hash"],
        "upstream_sequence": None,
        "candidate_added": added,
        "candidate_updated": updated,
        "presentation_only_updated": presentation_only,
        "registry_rebound": registry_rebound,
        "candidate_retracted": [],
        "verification_calls": verification_calls,
        "ttl_hours": ttl_hours,
        "max_candidates": max_candidates,
        "relevant_count": len(candidates),
        "official_match_count": len(verified),
        "historical_official_match_count": historical_verified_count,
        "canary_match_count": canary_results.count("OFFICIAL_MATCH"),
        "canary_conflict_count": canary_results.count("CONFLICT"),
        "canary_no_match_count": canary_results.count("NO_OFFICIAL_MATCH"),
        "expired_candidate_count": len(expired_ids),
        "official_provenance_candidate_count": len(verified),
        "new_public_event_count": 0,
        "existing_event_match_count": 0,
        "conflict_count": len(conflicts),
        "no_official_match_count": len(no_match),
        "canonical_change_count": 0,
        "truncated": truncated or validated["truncated"] or state_truncated,
        "state_truncated": state_truncated,
        "retained_candidate_count": len(retained_candidates),
        "retained_candidate_limit": max_retained_candidates,
        "upstream_operating_state": validated["operating_state"],
        "upstream_current": validated["operating_state"] == "ACTIVE" and not validated["stale"],
        "upstream_mode": mode,
        "upstream_gap": operating_state == "DEGRADED",
        "canary_mode": canary_mode,
        "historical_replay": historical_replay,
        "status": status,
        "govintel_publication_id": None,
        "govintel_publication_hash": None,
        "replayed": False,
    }
    state = {
        "schema_version": 1,
        "upstream_feed_id": FEED_ID,
        "last_seen_generation_id": current_generation,
        "last_seen_generated_at": validated["generated_at"],
        "last_seen_content_hash": validated["source_snapshot_hash"],
        "upstream_operating_state": validated["operating_state"],
        "candidates": retained_candidates,
        "active_candidates": [candidate["candidate_id"] for candidate in candidates if candidate["candidate_id"] in retained_ids and candidate.get("verification_status") != "EXPIRED"],
        "last_receipt": receipt,
    }
    return {"state": state, "candidates": candidates, "receipt": receipt, "replayed": False}


def summarize_canary(receipts: list[dict[str, Any]], *, window_days: int = 14) -> dict[str, Any]:
    """Produce denominator-preserving metrics for a bounded canary window."""

    if not _is_int(window_days) or window_days < 1:
        raise ValueError("window_days must be positive")
    status_values = sorted({str(item.get("status") or "UNKNOWN") for item in receipts})
    return {
        "schema_version": 1,
        "window_days": window_days,
        "run_count": len(receipts),
        "candidate_added": sum(len(item.get("candidate_added", [])) for item in receipts),
        "relevant_count": sum(int(item.get("relevant_count", 0)) for item in receipts),
        "official_match_count": sum(int(item.get("official_match_count", 0)) for item in receipts),
        "duplicate_existing_event_count": sum(int(item.get("existing_event_match_count", 0)) for item in receipts),
        "new_public_event_count": sum(int(item.get("new_public_event_count", 0)) for item in receipts),
        "conflict_count": sum(int(item.get("conflict_count", 0)) for item in receipts),
        "no_official_match_count": sum(int(item.get("no_official_match_count", 0)) for item in receipts),
        "canonical_change_count": sum(int(item.get("canonical_change_count", 0)) for item in receipts),
        "status_counts": {
            status: sum(str(item.get("status") or "UNKNOWN") == status for item in receipts)
            for status in status_values
        },
    }
