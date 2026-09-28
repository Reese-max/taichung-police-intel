from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


PROFILE_SCHEMA_VERSION = 1
RANKING_POLICY_VERSION = "role-ranking-v1"
DEFAULT_PROFILE_ID = "general"
PROFILE_FIELDS = {
    "profile_id",
    "version",
    "label",
    "responsibilities",
    "topics",
    "source_ids",
    "role_terms",
    "deadline_weight",
    "status",
}
SENSITIVE_FIELD = re.compile(
    r"(?:name|person|phone|email|address|case|incident|deployment|operational|internal|private|sensitive)",
    re.IGNORECASE,
)
PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
CHANGE_PRIORITY = {
    "STATUS_CHANGED": 0,
    "DEADLINE_CHANGED": 1,
    "NEW": 2,
    "REVISED": 3,
    "REMOVED": 4,
}
REASON_WEIGHTS = {
    "affected_role_match": 4.0,
    "topic_match": 3.0,
    "source_priority": 2.0,
    "deadline_within_72h": 5.0,
    "status_changed": 2.0,
    "explicit_follow_up": 2.0,
}


def _canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _fail(message: str) -> None:
    raise ValueError(f"invalid role profile catalog: {message}")


def _strings(value: Any, field: str, profile_id: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        _fail(f"{profile_id}.{field} must be a list of non-empty strings")
    return [item.strip() for item in value]


def _normalize(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def validate_catalog(value: dict[str, Any], *, require_general: bool = True) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("catalog must be an object")
    if value.get("schema_version") != PROFILE_SCHEMA_VERSION:
        _fail("schema_version must be 1")
    policy = value.get("ranking_policy_version")
    if not isinstance(policy, str) or not policy.strip():
        _fail("ranking_policy_version is required")
    raw_profiles = value.get("profiles")
    if not isinstance(raw_profiles, list) or not raw_profiles:
        _fail("profiles must be a non-empty list")

    profiles: dict[str, dict[str, Any]] = {}
    for raw in raw_profiles:
        if not isinstance(raw, dict):
            _fail("each profile must be an object")
        for key in raw:
            if key not in PROFILE_FIELDS:
                if SENSITIVE_FIELD.search(key):
                    _fail(f"disallowed sensitive field: {key}")
                _fail(f"unsupported field: {key}")
        missing = PROFILE_FIELDS - set(raw)
        if missing:
            _fail(f"profile is missing fields: {', '.join(sorted(missing))}")

        profile_id = raw["profile_id"]
        if not isinstance(profile_id, str) or not PROFILE_ID.fullmatch(profile_id):
            _fail("profile_id must be lowercase kebab-case")
        if profile_id in profiles:
            _fail(f"duplicate profile_id: {profile_id}")
        version = raw["version"]
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            _fail(f"{profile_id}.version must be a positive integer")
        if not isinstance(raw["label"], str) or not raw["label"].strip():
            _fail(f"{profile_id}.label is required")
        deadline_weight = raw["deadline_weight"]
        if isinstance(deadline_weight, bool) or not isinstance(deadline_weight, (int, float)):
            _fail(f"{profile_id}.deadline_weight must be a finite non-negative number")
        if not math.isfinite(float(deadline_weight)) or deadline_weight < 0:
            _fail(f"{profile_id}.deadline_weight must be a finite non-negative number")
        if raw["status"] not in {"active", "retired"}:
            _fail(f"{profile_id}.status must be active or retired")

        profile = {
            "profile_id": profile_id,
            "version": version,
            "label": raw["label"].strip(),
            "responsibilities": _strings(raw["responsibilities"], "responsibilities", profile_id),
            "topics": _strings(raw["topics"], "topics", profile_id),
            "source_ids": _strings(raw["source_ids"], "source_ids", profile_id),
            "role_terms": _strings(raw["role_terms"], "role_terms", profile_id),
            "deadline_weight": float(deadline_weight),
            "status": raw["status"],
        }
        profile["profile_hash"] = _canonical_sha256(profile)
        profiles[profile_id] = profile

    if require_general and DEFAULT_PROFILE_ID not in profiles:
        _fail("general profile is required")
    return {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "ranking_policy_version": policy,
        "profiles": profiles,
    }


def validate_profile(value: dict[str, Any], *, ranking_policy_version: str = RANKING_POLICY_VERSION) -> dict[str, Any]:
    catalog = validate_catalog({
        "schema_version": PROFILE_SCHEMA_VERSION,
        "ranking_policy_version": ranking_policy_version,
        "profiles": [value],
    }, require_general=False)
    return next(iter(catalog["profiles"].values()))


def load_catalog(path: str | Path) -> dict[str, Any]:
    profile_path = Path(path)
    return validate_catalog(json.loads(profile_path.read_text(encoding="utf-8")))


def profile_metadata(profile: dict[str, Any], ranking_policy_version: str) -> dict[str, Any]:
    return {
        "profile_id": profile["profile_id"],
        "profile_version": profile["version"],
        "profile_hash": profile["profile_hash"],
        "ranking_policy_version": ranking_policy_version,
        "label": profile["label"],
    }


def _event_reasons(
    item: dict[str, Any],
    profile: dict[str, Any],
    *,
    observed_at: str | None,
) -> list[str]:
    reasons: list[str] = []
    roles = {_normalize(role) for role in item.get("affected_roles") or []}
    role_terms = {_normalize(term) for term in profile["role_terms"]}
    if roles & role_terms:
        reasons.append("affected_role_match")

    text = " ".join(
        str(item.get(field) or "")
        for field in ("headline", "what_changed", "why_it_matters", "recommended_action")
    ).casefold()
    if any(_normalize(topic) in text for topic in profile["topics"]):
        reasons.append("topic_match")

    source_ids = set(profile["source_ids"])
    if item.get("source_id") in source_ids:
        reasons.append("source_priority")

    deadline = _parse_time(item.get("deadline"))
    now = _parse_time(observed_at) or _parse_time(item.get("detected_at"))
    if deadline and now and timedelta(0) <= deadline - now <= timedelta(hours=72):
        reasons.append("deadline_within_72h")

    if item.get("change_type") == "STATUS_CHANGED":
        reasons.append("status_changed")

    changed_fields = [str(field).casefold() for field in item.get("changed_fields") or []]
    if item.get("explicit_follow_up") is True or any(
        field.rsplit(".", 1)[-1] in {"follow_up", "next_milestone", "action_required", "owner"}
        for field in changed_fields
    ):
        reasons.append("explicit_follow_up")
    return reasons or ["default"]


def rank_items(
    items: Iterable[dict[str, Any]],
    profile: dict[str, Any],
    *,
    ranking_policy_version: str = RANKING_POLICY_VERSION,
    observed_at: str | None = None,
) -> list[dict[str, Any]]:
    def effective_change_type(item: dict[str, Any]) -> str | None:
        return item.get("change_type") or (
            "STATUS_CHANGED" if item.get("watch_status") == "NEEDS_REVIEW" else None
        )

    ranked = []
    for item in items:
        reasons = _event_reasons(item, profile, observed_at=observed_at)
        change_type = effective_change_type(item)
        base_score = max(0, len(CHANGE_PRIORITY) - CHANGE_PRIORITY.get(change_type, len(CHANGE_PRIORITY)))
        score = base_score + sum(
            REASON_WEIGHTS.get(reason, 0.0) * (
                profile["deadline_weight"] if reason == "deadline_within_72h" else 1.0
            )
            for reason in reasons
        )
        result = dict(item)
        result["profile_relevance"] = {
            **profile_metadata(profile, ranking_policy_version),
            "score": round(score, 3),
            "reason_codes": reasons,
        }
        ranked.append(result)

    ranked.sort(
        key=lambda item: (
            -item["profile_relevance"]["score"],
            CHANGE_PRIORITY.get(effective_change_type(item), 99),
            item.get("source_id") or "",
            item.get("event_id") or item.get("tracking_id") or item.get("identity") or "",
        )
    )
    return ranked


def project_profile(
    items: Iterable[dict[str, Any]],
    tracking_items: Iterable[dict[str, Any]],
    profile: dict[str, Any],
    *,
    ranking_policy_version: str,
    observed_at: str | None,
) -> dict[str, Any]:
    ranked = rank_items(
        items,
        profile,
        ranking_policy_version=ranking_policy_version,
        observed_at=observed_at,
    )
    top = [dict(item, publication_tier="TOP") for item in ranked[:3]]
    other = [dict(item, publication_tier="OTHER") for item in ranked[3:23]]
    tracking = rank_items(
        tracking_items,
        profile,
        ranking_policy_version=ranking_policy_version,
        observed_at=observed_at,
    )[:5]
    return {
        "profile": profile_metadata(profile, ranking_policy_version),
        "priority_items": top,
        "tracking_items": tracking,
        "other_changes": other,
        "overview": {
            "priority_count": len(top),
            "tracking_count": len(tracking),
            "other_change_count": len(other),
        },
    }
