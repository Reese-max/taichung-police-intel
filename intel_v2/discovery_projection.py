"""UI-facing projection for the Taiwan Intel Dashboard discovery layer.

Turns a completed ``ingest_feed`` result into the bounded, read-only payload the
site renders. Confirmed rows must carry server-controlled official document
versions; pending rows keep the full TTL'd denominator and never reuse wording
that would read as official confirmation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .discovery_adapter import DiscoveryFeedError, VALID_AUTHORITIES, VALID_STATES


KIND = "GOVINTEL_DISCOVERY_SIGNALS"
SCHEMA_VERSION = 1
PENDING_STATUSES = {"DISCOVERY_UNVERIFIED", "OFFICIAL_CANDIDATE", "NO_OFFICIAL_MATCH", "CONFLICT"}
KNOWN_STATUSES = PENDING_STATUSES | {"VERIFIED_OFFICIAL", "EXPIRED"}
SIGNAL_MODES = {
    "ACTIVE": "CURRENT",
    "PAUSED": "HISTORICAL_REPLAY_ONLY",
    "RESTORING": "CANARY_ONLY",
    "DEGRADED": "GAP_VISIBLE",
}
VALID_PAYLOAD_STATUSES = {"FIXTURE_ONLY", "LIVE"}
DEFAULT_PENDING_LIMIT = 5


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


def _note(candidate: dict[str, Any]) -> str:
    if candidate.get("verification_blocked_reason") == "UPSTREAM_NOT_CURRENT":
        return "上游非現行狀態，僅保留候選"
    if candidate.get("authority") == "media" and candidate.get("official_match_status") == "VERIFIED_OFFICIAL":
        return "已找到官方文件線索；媒體內容仍待核對"
    status = candidate["verification_status"]
    if status == "NO_OFFICIAL_MATCH" or candidate.get("official_match_status") == "NO_OFFICIAL_MATCH":
        return "尚未找到官方確認"
    if status == "CONFLICT":
        return "官方來源不一致，待人工判定"
    if status == "OFFICIAL_CANDIDATE":
        return "官方候選，待回查原始來源"
    return "尚未完成官方比對"


def _confirmed_row(candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("authority") != "official":
        raise DiscoveryFeedError("media cannot be promoted to official confirmation")
    versions = candidate.get("official_document_versions")
    if not isinstance(versions, list) or not versions:
        raise DiscoveryFeedError("confirmed candidate carries no official document version")
    return {
        "candidate_id": candidate["candidate_id"],
        "headline": candidate.get("headline"),
        "event_time": candidate.get("event_time"),
        "discovered_at": candidate.get("observed_at"),
        "region": candidate.get("region"),
        "category": candidate.get("category"),
        "authority": candidate["authority"],
        "publisher": candidate.get("publisher"),
        "verification_status": candidate["verification_status"],
        "verification_reason": candidate.get("verification_reason"),
        "official_document_versions": list(versions),
        "official_urls": list(candidate.get("official_urls") or []),
        "public_event_ids": list(candidate.get("public_event_ids") or []),
        "matched_existing": bool(candidate.get("matched_existing")),
        "canonical_write": bool(candidate.get("canonical_write")),
    }


def _pending_row(candidate: dict[str, Any]) -> dict[str, Any]:
    row = {
        "candidate_id": candidate["candidate_id"],
        "headline": candidate.get("headline"),
        "event_time": candidate.get("event_time"),
        "discovered_at": candidate.get("observed_at") or candidate.get("event_time"),
        "region": candidate.get("region"),
        "category": candidate.get("category"),
        "authority": candidate["authority"],
        "publisher": candidate.get("publisher"),
        "verification_status": candidate["verification_status"],
        "note": _note(candidate),
        "expires_at": candidate.get("expires_at"),
        "canonical_write": bool(candidate.get("canonical_write")),
        "official_document_versions": list(candidate.get("official_document_versions") or []),
        "official_urls": list(candidate.get("official_urls") or []),
    }
    if candidate.get("verification_blocked_reason"):
        row["verification_blocked_reason"] = candidate["verification_blocked_reason"]
    return row


def _check_candidate(candidate: Any, index: int) -> dict[str, Any]:
    if not isinstance(candidate, dict):
        raise DiscoveryFeedError(f"candidates[{index}] must be an object")
    if not isinstance(candidate.get("candidate_id"), str) or not candidate["candidate_id"]:
        raise DiscoveryFeedError(f"candidates[{index}] missing candidate_id")
    if candidate.get("authority") not in VALID_AUTHORITIES:
        raise DiscoveryFeedError(f"candidates[{index}] has invalid authority")
    status = candidate.get("verification_status")
    if status not in KNOWN_STATUSES:
        raise DiscoveryFeedError(f"candidates[{index}] has unknown verification_status")
    return candidate


def project_discovery_signals(
    result: dict[str, Any],
    *,
    now: datetime | None = None,
    pending_limit: int = DEFAULT_PENDING_LIMIT,
    status: str = "FIXTURE_ONLY",
    production_verified: bool = False,
) -> dict[str, Any]:
    """Project one ingest result into the bounded UI payload.

    The projection is read-only: it never promotes candidates, never writes
    canonical state, and fails closed on anything the adapter did not produce.
    """

    if not isinstance(result, dict):
        raise DiscoveryFeedError("ingest result must be an object")
    for key in ("state", "candidates", "receipt"):
        if key not in result:
            raise DiscoveryFeedError(f"ingest result missing {key}")
    receipt = result["receipt"]
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1:
        raise DiscoveryFeedError("ingest result receipt is not schema_version 1")
    candidates = result["candidates"]
    if not isinstance(candidates, list):
        raise DiscoveryFeedError("ingest result candidates must be a list")
    if not isinstance(result["state"], dict):
        raise DiscoveryFeedError("ingest result state must be an object")
    if status not in VALID_PAYLOAD_STATUSES:
        raise DiscoveryFeedError("unknown payload status")
    if status == "FIXTURE_ONLY" and production_verified:
        raise DiscoveryFeedError("fixture replay cannot establish production verification")
    if not isinstance(pending_limit, int) or isinstance(pending_limit, bool) or pending_limit < 1:
        raise ValueError("pending_limit must be a positive integer")

    for key in ("upstream_feed_id", "upstream_generation_id", "upstream_operating_state", "upstream_current"):
        if key not in receipt:
            raise DiscoveryFeedError(f"receipt missing {key}")
    operating_state = receipt["upstream_operating_state"]
    if operating_state not in VALID_STATES:
        raise DiscoveryFeedError("receipt has invalid upstream_operating_state")
    if not isinstance(receipt["upstream_current"], bool):
        raise DiscoveryFeedError("receipt upstream_current must be a boolean")
    upstream_current = receipt["upstream_current"]
    if status == "LIVE" and not upstream_current:
        raise DiscoveryFeedError("non-current upstream cannot be published as a live production signal")

    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        raise DiscoveryFeedError("projection clock must include a timezone")
    confirmed: list[dict[str, Any]] = []
    pending_all: list[dict[str, Any]] = []
    expired = 0
    for index, candidate in enumerate(candidates):
        candidate = _check_candidate(candidate, index)
        candidate_status = candidate["verification_status"]
        confirmed_row = _confirmed_row(candidate) if candidate_status == "VERIFIED_OFFICIAL" else None
        if candidate_status == "EXPIRED":
            expired += 1
            continue
        expires = _timestamp(candidate.get("expires_at"), f"candidates[{index}].expires_at")
        if expires is not None and reference >= expires:
            expired += 1
            continue
        if candidate_status == "VERIFIED_OFFICIAL":
            confirmed.append(confirmed_row)
            continue
        pending_all.append(_pending_row(candidate))

    pending = pending_all[:pending_limit]
    counts = {
        "feed_item_count": int(receipt["feed_item_count"]) if "feed_item_count" in receipt else len(candidates),
        "relevant_count": int(receipt["relevant_count"]) if "relevant_count" in receipt else len(candidates),
        "confirmed_count": len(confirmed),
        "pending_total": len(pending_all),
        "pending_shown": len(pending),
        "expired_count": expired,
        "conflict_count": int(receipt.get("conflict_count") or 0),
        "no_official_match_count": int(receipt.get("no_official_match_count") or 0),
        "new_public_event_count": int(receipt.get("new_public_event_count") or 0),
        "existing_event_match_count": int(receipt.get("existing_event_match_count") or 0),
        "canonical_change_count": int(receipt.get("canonical_change_count") or 0),
        "truncated": bool(receipt.get("truncated")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "status": status,
        "production_verified": bool(production_verified),
        "generated_at": _iso(reference),
        "upstream": {
            "feed_id": receipt["upstream_feed_id"],
            "generation_id": receipt["upstream_generation_id"],
            "generated_at": result["state"].get("last_seen_generated_at"),
            "operating_state": operating_state,
            "current": upstream_current,
            "signal_mode": SIGNAL_MODES[operating_state],
            "content_hash": receipt.get("upstream_content_hash"),
        },
        "confirmed": confirmed,
        "pending": pending,
        "limits": {"pending_max": pending_limit},
        "counts": counts,
    }
