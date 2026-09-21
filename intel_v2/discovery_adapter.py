"""Bounded consumer for the Taiwan Intel Dashboard discovery feed.

The feed is a radar input only. This module keeps discovery candidates
separate from canonical facts/events and accepts official evidence only from a
server-controlled match list.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any


SCHEMA_VERSION = 1
FEED_ID = "taiwan-intel-dashboard:govintel-discovery"
VALID_STATES = {"ACTIVE", "DEGRADED", "RESTORING", "PAUSED"}
VALID_AUTHORITIES = {"official", "media"}
VALID_RIGHTS = {"OPEN_DATA", "REVIEW_REQUIRED"}
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
    if feed["schema_version"] != SCHEMA_VERSION:
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
    for index, item in enumerate(items):
        _validate_item(item, index)
    return dict(feed)


def _validate_item(item: Any, index: int) -> None:
    if not isinstance(item, dict):
        raise DiscoveryFeedError(f"items[{index}] must be an object")
    required = {
        "discovery_id", "title", "event_time", "observed_at", "category", "authority",
        "source_url", "original_source_identity", "rights_status",
    }
    missing = required - set(item)
    if missing:
        raise DiscoveryFeedError(f"items[{index}] missing fields: {sorted(missing)}")
    if not isinstance(item["discovery_id"], str) or not DISCOVERY_ID.fullmatch(item["discovery_id"]):
        raise DiscoveryFeedError(f"items[{index}] has invalid discovery_id")
    for field in ("title", "category", "source_url", "original_source_identity"):
        if item[field] is not None and not isinstance(item[field], str):
            raise DiscoveryFeedError(f"items[{index}].{field} must be string or null")
    _timestamp(item["event_time"], f"items[{index}].event_time")
    _timestamp(item["observed_at"], f"items[{index}].observed_at")
    if item["authority"] not in VALID_AUTHORITIES or item["rights_status"] not in VALID_RIGHTS:
        raise DiscoveryFeedError(f"items[{index}] has invalid authority or rights_status")
    if not item["source_url"] and not item["original_source_identity"]:
        raise DiscoveryFeedError(f"items[{index}] has no source identity")


def load_feed(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryFeedError(f"cannot load feed: {path}") from exc
    return validate_feed(payload)


def generation_id(feed: dict[str, Any]) -> str:
    validated = validate_feed(feed)
    material = {
        "schema_version": validated["schema_version"],
        "generated_at": validated["generated_at"],
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


def candidate_from_item(feed: dict[str, Any], item: dict[str, Any], ttl_hours: int = 72) -> dict[str, Any]:
    validated = validate_feed(feed)
    if not _is_int(ttl_hours) or ttl_hours < 1:
        raise ValueError("ttl_hours must be a positive integer")
    relevant, relevance_reason = _relevance(item)
    observed = _timestamp(item.get("observed_at"), "observed_at") or _timestamp(validated["generated_at"], "generated_at")
    status = "DISCOVERY_UNVERIFIED" if item["authority"] == "media" else "OFFICIAL_CANDIDATE"
    return {
        "candidate_id": item["discovery_id"],
        "discovery_source": FEED_ID,
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
        "upstream_generation_id": generation_id(validated),
        "upstream_content_hash": validated["source_snapshot_hash"],
        "upstream_operating_state": validated["operating_state"],
        "upstream_current": validated["operating_state"] == "ACTIVE" and not validated["stale"],
        "relevant": relevant,
        "relevance_reason": relevance_reason,
        "material_hash": _hash_fields(item, MATERIAL_FIELDS),
        "presentation_hash": _hash_fields(item, PRESENTATION_FIELDS),
        "verification_status": status,
        "verification_sources_checked": [],
        "official_document_versions": [],
        "matched_existing": False,
        "independent_source_count": 0,
        "canonical_write": False,
        "expires_at": _iso(observed + timedelta(hours=ttl_hours)),
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


def verify_candidate(
    candidate: dict[str, Any],
    official_documents: list[dict[str, Any]],
    *,
    now: datetime | None = None,
    historical_replay: bool = False,
) -> dict[str, Any]:
    """Attach only server-controlled official matches; never mutate canonical state."""

    _validate_official_documents(official_documents)
    if not candidate.get("upstream_current") and not historical_replay:
        result = dict(candidate)
        result["verification_blocked_reason"] = "UPSTREAM_NOT_CURRENT"
        return result
    matched = [document for document in official_documents if _document_matches(candidate, document)]
    result = dict(candidate)
    result["verification_sources_checked"] = sorted({str(doc["source_id"]) for doc in official_documents})
    result["official_document_versions"] = sorted({str(doc["document_version_id"]) for doc in matched})
    result["independent_source_count"] = len({str(doc.get("original_source_identity") or doc["source_id"]) for doc in matched})
    if not matched:
        expires = _timestamp(candidate["expires_at"], "expires_at")
        reference = now or datetime.now(timezone.utc)
        result["verification_status"] = "EXPIRED" if expires and reference >= expires else "NO_OFFICIAL_MATCH"
        result["verification_reason"] = "NO_OFFICIAL_SOURCE_MATCH"
        return result
    fingerprints = {str(doc.get("fact_fingerprint") or doc.get("document_version_id")) for doc in matched}
    if len(fingerprints) > 1 or any(doc.get("conflict") is True for doc in matched):
        result["verification_status"] = "CONFLICT"
        result["verification_reason"] = "OFFICIAL_SOURCES_DISAGREE"
        return result
    result["verification_status"] = "VERIFIED_OFFICIAL"
    result["verification_reason"] = "SERVER_CONTROLLED_OFFICIAL_MATCH"
    result["matched_existing"] = any(doc.get("public_event_id") or doc.get("existing_public_event_id") for doc in matched)
    result["public_event_ids"] = sorted({str(doc.get("public_event_id") or doc.get("existing_public_event_id")) for doc in matched if doc.get("public_event_id") or doc.get("existing_public_event_id")})
    result["canonical_write"] = False
    return result


def _previous_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if state is None:
        return None
    if not isinstance(state, dict) or not isinstance(state.get("candidates"), dict):
        raise DiscoveryFeedError("invalid consumer state")
    return state


def ingest_feed(
    feed: dict[str, Any],
    *,
    previous_state: dict[str, Any] | None = None,
    official_documents: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
    ttl_hours: int = 72,
    max_candidates: int = 50,
    historical_replay: bool = False,
) -> dict[str, Any]:
    """Apply one full feed snapshot idempotently and return receipt plus state."""

    validated = validate_feed(feed)
    previous = _previous_state(previous_state)
    documents = official_documents or []
    _validate_official_documents(documents)
    if not _is_int(max_candidates) or max_candidates < 1:
        raise ValueError("max_candidates must be a positive integer")
    current_generation = generation_id(validated)
    if previous and previous.get("last_seen_generated_at"):
        old_time = _timestamp(previous["last_seen_generated_at"], "last_seen_generated_at")
        if _timestamp(validated["generated_at"], "generated_at") < old_time:
            raise DiscoveryFeedError("out-of-order upstream snapshot")
    if previous and previous.get("last_seen_generation_id") == current_generation:
        last_receipt = previous.get("last_receipt")
        if not isinstance(last_receipt, dict):
            raise DiscoveryFeedError("same generation has no replay receipt")
        replay_receipt = dict(last_receipt)
        replay_receipt["replayed"] = True
        return {"state": previous, "candidates": list(previous.get("active_candidates", [])), "receipt": replay_receipt, "replayed": True}

    projected = [candidate_from_item(validated, item, ttl_hours) for item in validated["items"]]
    projected = [candidate for candidate in projected if candidate["relevant"]]
    projected.sort(key=lambda candidate: (candidate.get("event_time") is not None, candidate.get("event_time") or "", candidate["candidate_id"]), reverse=True)
    truncated = len(projected) > max_candidates
    projected = projected[:max_candidates]
    previous_candidates = previous.get("candidates", {}) if previous else {}
    candidates: list[dict[str, Any]] = []
    added: list[str] = []
    updated: list[str] = []
    presentation_only: list[str] = []
    verification_calls: list[str] = []
    for candidate in projected:
        old = previous_candidates.get(candidate["candidate_id"])
        change = classify_change(old, candidate)
        candidate["change_class"] = change
        if old is not None and change != "MATERIAL_DISCOVERY_CHANGE":
            candidate["verification_status"] = old.get("verification_status", candidate["verification_status"])
            for key in ("verification_sources_checked", "official_document_versions", "matched_existing", "independent_source_count", "public_event_ids", "verification_reason", "verification_blocked_reason"):
                if key in old:
                    candidate[key] = old[key]
            if change == "PRESENTATION_ONLY":
                presentation_only.append(candidate["candidate_id"])
        else:
            if old is None:
                added.append(candidate["candidate_id"])
            else:
                updated.append(candidate["candidate_id"])
            verification_calls.append(candidate["candidate_id"])
            candidate = verify_candidate(candidate, documents, now=now, historical_replay=historical_replay)
        candidates.append(candidate)

    verified = [candidate for candidate in candidates if candidate.get("verification_status") == "VERIFIED_OFFICIAL"]
    conflicts = [candidate for candidate in candidates if candidate.get("verification_status") == "CONFLICT"]
    no_match = [candidate for candidate in candidates if candidate.get("verification_status") == "NO_OFFICIAL_MATCH"]
    new_events = [candidate for candidate in verified if not candidate.get("matched_existing")]
    existing_events = [candidate for candidate in verified if candidate.get("matched_existing")]
    status = "PENDING_PUBLICATION" if new_events else "VERIFIED" if verified else "INGESTED"
    receipt = {
        "schema_version": 1,
        "consumer_run_id": f"govintel-discovery:{current_generation}",
        "upstream_feed_id": FEED_ID,
        "upstream_generation_id": current_generation,
        "upstream_content_hash": validated["source_snapshot_hash"],
        "upstream_sequence": None,
        "candidate_added": added,
        "candidate_updated": updated,
        "presentation_only_updated": presentation_only,
        "candidate_retracted": [],
        "verification_calls": verification_calls,
        "relevant_count": len(candidates),
        "official_match_count": len(verified),
        "new_public_event_count": len(new_events),
        "existing_event_match_count": len(existing_events),
        "conflict_count": len(conflicts),
        "no_official_match_count": len(no_match),
        "canonical_change_count": len(new_events),
        "truncated": truncated or validated["truncated"],
        "upstream_operating_state": validated["operating_state"],
        "upstream_current": validated["operating_state"] == "ACTIVE" and not validated["stale"],
        "status": status,
        "govintel_publication_id": None,
        "govintel_publication_hash": None,
        "replayed": False,
    }
    all_candidates = dict(previous_candidates)
    for candidate in candidates:
        all_candidates[candidate["candidate_id"]] = candidate
    state = {
        "schema_version": 1,
        "upstream_feed_id": FEED_ID,
        "last_seen_generation_id": current_generation,
        "last_seen_generated_at": validated["generated_at"],
        "last_seen_content_hash": validated["source_snapshot_hash"],
        "upstream_operating_state": validated["operating_state"],
        "candidates": all_candidates,
        "active_candidates": [candidate["candidate_id"] for candidate in candidates],
        "last_receipt": receipt,
    }
    return {"state": state, "candidates": candidates, "receipt": receipt, "replayed": False}


def summarize_canary(receipts: list[dict[str, Any]], *, window_days: int = 14) -> dict[str, Any]:
    """Produce denominator-preserving metrics for a bounded canary window."""

    if not _is_int(window_days) or window_days < 1:
        raise ValueError("window_days must be positive")
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
            status: sum(item.get("status") == status for item in receipts)
            for status in ("INGESTED", "VERIFIED", "PENDING_PUBLICATION", "PUBLISHED", "PUBLISH_FAILED")
        },
    }
