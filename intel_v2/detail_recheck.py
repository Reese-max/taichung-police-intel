"""Bounded detail-page recheck policy and version classifier.

This module deliberately does not perform HTTP.  A transport can use the
request plan, then pass a bounded response here so 304, attachment, failure,
and material-change semantics stay identical across collectors.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Taipei")
HASH_RE = r"^[0-9a-f]{64}$"
SEMANTIC_FIELDS = (
    "title",
    "published_at",
    "source_modified_at",
    "effective_at",
    "expires_at",
    "location",
    "status",
    "scope",
)


def _timestamp(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.astimezone(TZ)


def _stamp(value: datetime) -> str:
    return value.astimezone(TZ).isoformat(timespec="seconds")


def _sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(HASH_RE, value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _attachments(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("attachments must be an array")
    rows = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("attachment_id"), str):
            raise ValueError("attachment requires attachment_id")
        attachment_id = item["attachment_id"].strip()
        if not attachment_id or attachment_id in seen:
            raise ValueError("attachment IDs must be unique and non-empty")
        seen.add(attachment_id)
        row = {"attachment_id": attachment_id}
        if item.get("content_sha256") is not None:
            row["content_sha256"] = _hash(item["content_sha256"], "attachment content_sha256")
        if item.get("etag") is not None:
            row["etag"] = str(item["etag"])
        if item.get("last_modified") is not None:
            row["last_modified"] = str(item["last_modified"])
        rows.append(row)
    return sorted(rows, key=lambda item: item["attachment_id"])


def _request_headers(previous: dict[str, Any]) -> dict[str, str]:
    headers = {}
    if previous.get("etag"):
        headers["If-None-Match"] = str(previous["etag"])
    if previous.get("last_modified"):
        headers["If-Modified-Since"] = str(previous["last_modified"])
    return headers


def plan_recheck(
    previous: dict[str, Any] | None,
    now: str,
    *,
    interval_hours: float = 24,
) -> dict[str, Any]:
    """Return a due/conditional-request plan without contacting the source."""
    current = _timestamp(now)
    if not isinstance(interval_hours, (int, float)) or interval_hours <= 0:
        raise ValueError("interval_hours must be positive")
    previous = previous or {}
    last_checked = previous.get("last_checked_at")
    if not last_checked:
        return {
            "status": "DUE",
            "reason": "NEVER_CHECKED",
            "due_at": _stamp(current),
            "request_headers": _request_headers(previous),
        }
    checked = _timestamp(last_checked)
    if current < checked:
        return {
            "status": "UNKNOWN",
            "reason": "CLOCK_BEFORE_LAST_CHECK",
            "due_at": _stamp(checked),
            "request_headers": {},
        }
    expires_at = previous.get("expires_at")
    if expires_at and current >= _timestamp(expires_at):
        reason = "EXPIRED"
        status = "DUE"
    elif current - checked >= timedelta(hours=interval_hours):
        reason = "TTL_EXPIRED"
        status = "DUE"
    else:
        reason = "WITHIN_TTL"
        status = "NOT_DUE"
    return {
        "status": status,
        "reason": reason,
        "due_at": _stamp(current if status == "DUE" else checked + timedelta(hours=interval_hours)),
        "request_headers": _request_headers(previous) if status == "DUE" else {},
    }


def _version(previous: dict[str, Any] | None, observation: dict[str, Any], observed: str) -> dict[str, Any]:
    body_sha256 = _hash(observation.get("body_sha256"), "body_sha256")
    normalized_sha256 = _hash(observation.get("normalized_text_sha256"), "normalized_text_sha256")
    previous = previous or {}
    attachments = _attachments(
        observation["attachments"] if "attachments" in observation else previous.get("attachments")
    )
    times = {
        field: observation[field] if field in observation else previous.get(field)
        for field in ("published_at", "source_modified_at", "effective_at", "expires_at")
    }
    for field, value in times.items():
        if value is not None:
            _timestamp(value)
    material = {
        "body_sha256": body_sha256,
        "normalized_text_sha256": normalized_sha256,
        "attachments": attachments,
        **times,
        **{
            field: observation[field] if field in observation else previous.get(field)
            for field in SEMANTIC_FIELDS
            if field not in times
        },
    }
    return {
        "document_version_id": "DOCV-" + _sha(material).upper()[:20],
        "body_sha256": body_sha256,
        "normalized_text_sha256": normalized_sha256,
        "attachments": attachments,
        **times,
        "observed_at": observed,
        "last_checked_at": observed,
        "etag": observation["etag"] if "etag" in observation else previous.get("etag"),
        "last_modified": observation["last_modified"] if "last_modified" in observation else previous.get("last_modified"),
    }


def _before(previous: dict[str, Any] | None) -> dict[str, Any] | None:
    if not previous:
        return None
    keys = {
        "document_version_id",
        "body_sha256",
        "normalized_text_sha256",
        "attachments",
        "published_at",
        "source_modified_at",
        "effective_at",
        "expires_at",
        "observed_at",
        "last_checked_at",
        "etag",
        "last_modified",
    }
    return {key: previous[key] for key in keys if key in previous}


def _terminal(status: str, observed_at: str, *, before: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "observed_at": observed_at,
        "last_checked_at": observed_at,
        "before": _before(before),
        "after": None,
        "changed_fields": [],
        "review_required": False,
        "baseline": False,
        "preserve_last_known_good": True,
        "event_cancelled": False,
        **extra,
    }


def classify_observation(
    previous: dict[str, Any] | None,
    observation: dict[str, Any],
    *,
    observed_at: str,
) -> dict[str, Any]:
    """Classify one bounded detail response and retain only hash metadata."""
    observed = _stamp(_timestamp(observed_at))
    if not isinstance(observation, dict):
        raise ValueError("observation must be an object")
    status_code = observation.get("status_code", 200)
    if type(status_code) is not int:
        raise ValueError("status_code must be an integer")
    if status_code == 304:
        return _terminal(
            "NOT_MODIFIED",
            observed,
            before=previous,
            attachment_recheck_required=not bool(observation.get("attachments_checked")),
        )
    if status_code == 429 or status_code >= 500:
        retry = observation.get("retry_after_seconds")
        if retry is not None and (type(retry) is not int or retry < 0):
            raise ValueError("retry_after_seconds must be a non-negative integer")
        return _terminal(
            "DEFERRED",
            observed,
            before=previous,
            retry_after_seconds=min(retry, 86400) if retry is not None else None,
        )
    if status_code < 200 or status_code >= 300 or observation.get("available") is False:
        return _terminal("UNAVAILABLE", observed, before=previous)

    after = _version(previous, observation, observed)
    if previous is None:
        return {
            "schema_version": 1,
            "status": "BASELINE",
            "observed_at": observed,
            "last_checked_at": observed,
            "before": None,
            "after": after,
            "changed_fields": [],
            "review_required": False,
            "baseline": True,
            "preserve_last_known_good": True,
            "event_cancelled": False,
            "attachment_recheck_required": "attachments" not in observation,
        }

    changed_fields = [
        field for field in SEMANTIC_FIELDS
        if previous.get(field) != after.get(field)
    ]
    normalized_changed = previous.get("normalized_text_sha256") != after["normalized_text_sha256"]
    attachments_changed = previous.get("attachments", []) != after["attachments"]
    body_changed = previous.get("body_sha256") != after["body_sha256"]
    if changed_fields or normalized_changed:
        status = "MATERIAL_CHANGE"
    elif attachments_changed:
        status = "ATTACHMENT_CHANGED"
    elif body_changed:
        status = "PRESENTATION_ONLY"
    else:
        status = "UNCHANGED"
    return {
        "schema_version": 1,
        "status": status,
        "observed_at": observed,
        "last_checked_at": observed,
        "before": _before(previous),
        "after": after,
        "changed_fields": changed_fields,
        "review_required": status in {"MATERIAL_CHANGE", "ATTACHMENT_CHANGED"},
        "baseline": False,
        "preserve_last_known_good": True,
        "event_cancelled": False,
        "attachment_recheck_required": "attachments" not in observation,
    }
