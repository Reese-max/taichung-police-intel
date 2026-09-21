"""Local-first, review-gated feedback records for GovIntel corrections."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
from typing import Any


REASONS = frozenset(
    {
        "FALSE_MERGE",
        "MISSED_MERGE",
        "WRONG_ENTITY",
        "NOT_RELEVANT",
        "MISSING_EVENT",
        "WRONG_CHANGE_CLASSIFICATION",
        "UNSUPPORTED_ANSWER",
        "WRONG_STATISTIC_SCOPE",
        "BAD_SOURCE_MAPPING",
    }
)
TARGET_TYPES = frozenset({"EVENT", "ENTITY", "QUERY", "ANSWER"})
STATUSES = frozenset({"NEW", "ACCEPTED", "REJECTED", "DUPLICATE"})


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def timestamp(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.isoformat(timespec="seconds")


def empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "mode": "FEEDBACK_LOOP", "items": {}, "last_updated_at": None}


def _audit(item: dict[str, Any], action: str, at: str, payload: dict[str, Any]) -> dict[str, Any]:
    sequence = len(item.get("audit", [])) + 1
    material = {"feedback_id": item["feedback_id"], "sequence": sequence, "action": action, "at": at, "payload": payload}
    return {"audit_id": f"AUDIT-{sha256(material).upper()[:20]}", **material}


def feedback_id_for(fingerprint: str) -> str:
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise ValueError("feedback fingerprint is required")
    return f"FEEDBACK-{sha256(fingerprint.strip().encode('utf-8')).upper()[:20]}"


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") != 1 or state.get("mode") != "FEEDBACK_LOOP":
        raise ValueError("feedback state must use schema_version=1 and mode=FEEDBACK_LOOP")
    items = state.get("items")
    if not isinstance(items, dict):
        raise ValueError("feedback items must be an object")
    fingerprints: set[str] = set()
    for feedback_id, item in items.items():
        validate_record(item)
        if item["feedback_id"] != feedback_id:
            raise ValueError("feedback IDs must be stable object keys")
        if item["review_status"] != "DUPLICATE":
            if item["fingerprint"] in fingerprints:
                raise ValueError("non-duplicate feedback fingerprints must be unique")
            fingerprints.add(item["fingerprint"])
    if state.get("last_updated_at") is not None:
        timestamp(state["last_updated_at"])
    return state


def validate_record(item: dict[str, Any]) -> None:
    if not isinstance(item, dict):
        raise ValueError("feedback record must be an object")
    target = item.get("target")
    if not isinstance(target, dict) or target.get("type") not in TARGET_TYPES:
        raise ValueError("feedback target type is invalid")
    if any(not isinstance(target.get(field), str) or not target[field].strip() for field in ("type", "id", "version")):
        raise ValueError("feedback target requires type/id/version")
    if item.get("reason") not in REASONS or item.get("review_status") not in STATUSES:
        raise ValueError("feedback reason/status is invalid")
    output_hash = item.get("original_output_sha256")
    if not isinstance(output_hash, str) or len(output_hash) != 64 or any(char not in "0123456789abcdef" for char in output_hash):
        raise ValueError("original_output_sha256 must be a lowercase SHA-256")
    if not isinstance(item.get("corrected_expected_state"), dict):
        raise ValueError("corrected_expected_state must be an object")
    if not isinstance(item.get("evidence_refs"), list) or any(not isinstance(ref, (str, dict)) for ref in item["evidence_refs"]):
        raise ValueError("evidence_refs must be a string/object array")
    trace = item.get("trace")
    if not isinstance(trace, dict) or any(not isinstance(trace.get(field), str) or not trace[field].strip() for field in ("model_version", "parser_version", "registry_hash")):
        raise ValueError("feedback trace requires model/parser/registry values")
    for forbidden in ("raw_prompt", "conversation", "full_text", "private_notes"):
        if forbidden in item:
            raise ValueError(f"feedback record must not contain {forbidden}")
    if not isinstance(item.get("fingerprint"), str) or not item["fingerprint"]:
        raise ValueError("feedback fingerprint is required")
    timestamp(item.get("created_at"))
    timestamp(item.get("updated_at"))
    if not isinstance(item.get("audit"), list) or not item["audit"]:
        raise ValueError("feedback record requires audit history")
    regression = item.get("regression_fixture")
    if regression is not None and (not isinstance(regression, dict) or not regression.get("fixture_id")):
        raise ValueError("regression_fixture must contain fixture_id")


def copy_state(state: dict[str, Any] | None) -> dict[str, Any]:
    return validate_state(copy.deepcopy(state) if state is not None else empty_state())


def create_feedback(
    state: dict[str, Any] | None,
    *,
    target_type: str,
    target_id: str,
    target_version: str,
    reason: str,
    original_output_sha256: str,
    corrected_expected_state: dict[str, Any] | None = None,
    evidence_refs: list[str | dict[str, Any]] | None = None,
    linked_review_id: str | None = None,
    created_at: str,
    model_version: str,
    parser_version: str,
    registry_hash: str,
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Create one minimal record; identical fingerprints are returned unchanged."""
    result = copy_state(state)
    target_type = target_type.upper()
    reason = reason.upper()
    if target_type not in TARGET_TYPES or not target_id.strip() or not target_version.strip():
        raise ValueError("target type/id/version are required")
    if reason not in REASONS:
        raise ValueError(f"unsupported feedback reason: {reason}")
    if not isinstance(original_output_sha256, str) or len(original_output_sha256) != 64 or any(char not in "0123456789abcdef" for char in original_output_sha256):
        raise ValueError("original_output_sha256 must be a lowercase SHA-256")
    stamp = timestamp(created_at)
    corrected = copy.deepcopy(corrected_expected_state or {})
    refs = copy.deepcopy(evidence_refs or [])
    fingerprint = sha256(
        {
            "target": {"type": target_type, "id": target_id, "version": target_version},
            "reason": reason,
            "original_output_sha256": original_output_sha256,
            "corrected_expected_state": corrected,
            "evidence_refs": refs,
        }
    )
    existing = next((item for item in result["items"].values() if item.get("fingerprint") == fingerprint), None)
    if existing is not None:
        return result, existing, False
    item = {
        "feedback_id": feedback_id_for(fingerprint),
        "fingerprint": fingerprint,
        "target": {"type": target_type, "id": target_id, "version": target_version},
        "reason": reason,
        "original_output_sha256": original_output_sha256,
        "corrected_expected_state": corrected,
        "evidence_refs": refs,
        "linked_review_id": linked_review_id,
        "review_status": "NEW",
        "created_at": stamp,
        "updated_at": stamp,
        "trace": {"model_version": model_version, "parser_version": parser_version, "registry_hash": registry_hash},
        "regression_fixture": None,
        "decision": None,
        "audit": [],
    }
    item["audit"].append(_audit(item, "CREATED", stamp, {"reason": reason, "target": item["target"]}))
    result["items"][item["feedback_id"]] = item
    result["last_updated_at"] = stamp
    return result, item, True


def build_regression_fixture(item: dict[str, Any], *, reviewer_ref: str, decided_at: str) -> dict[str, Any]:
    if item.get("review_status") != "ACCEPTED":
        raise ValueError("only accepted feedback can create a regression fixture")
    return {
        "fixture_id": f"FEEDBACK-REGRESSION-{item['feedback_id'].removeprefix('FEEDBACK-')}",
        "fixture_kind": "FEEDBACK_REGRESSION",
        "feedback_id": item["feedback_id"],
        "target": copy.deepcopy(item["target"]),
        "reason": item["reason"],
        "expected": copy.deepcopy(item["corrected_expected_state"]),
        "reviewer_ref": reviewer_ref,
        "decided_at": timestamp(decided_at),
        "requires_gold_promotion_review": True,
    }


def review(
    state: dict[str, Any] | None,
    feedback_id: str,
    status: str,
    *,
    reviewer_ref: str,
    decided_at: str,
    create_regression: bool = True,
) -> dict[str, Any]:
    result = copy_state(state)
    item = result["items"].get(feedback_id)
    status = status.upper()
    if item is None:
        raise ValueError(f"unknown feedback ID: {feedback_id}")
    if status not in {"ACCEPTED", "REJECTED", "DUPLICATE"}:
        raise ValueError("review status must be ACCEPTED, REJECTED, or DUPLICATE")
    if not reviewer_ref.strip():
        raise ValueError("reviewer_ref is required")
    if item["review_status"] != "NEW":
        raise ValueError(f"feedback is already reviewed: {feedback_id}")
    stamp = timestamp(decided_at)
    item["review_status"] = status
    item["decision"] = {"status": status, "reviewer_ref": reviewer_ref, "decided_at": stamp}
    if status == "ACCEPTED" and create_regression:
        item["regression_fixture"] = build_regression_fixture(item, reviewer_ref=reviewer_ref, decided_at=stamp)
    item["updated_at"] = stamp
    item["audit"].append(_audit(item, "REVIEWED", stamp, {"status": status, "reviewer_ref": reviewer_ref}))
    result["last_updated_at"] = stamp
    return result


def link_regression(state: dict[str, Any] | None, feedback_id: str, fixture_id: str, *, reviewer_ref: str, linked_at: str) -> dict[str, Any]:
    result = copy_state(state)
    item = result["items"].get(feedback_id)
    if item is None or item.get("review_status") != "ACCEPTED":
        raise ValueError("only accepted feedback can link a regression fixture")
    if not fixture_id.strip() or not reviewer_ref.strip():
        raise ValueError("fixture_id and reviewer_ref are required")
    stamp = timestamp(linked_at)
    item["regression_fixture"] = {
        "fixture_id": fixture_id,
        "fixture_kind": "LINKED_REGRESSION",
        "feedback_id": feedback_id,
        "reviewer_ref": reviewer_ref,
        "linked_at": stamp,
        "requires_gold_promotion_review": True,
    }
    item["updated_at"] = stamp
    item["audit"].append(_audit(item, "REGRESSION_LINKED", stamp, {"fixture_id": fixture_id, "reviewer_ref": reviewer_ref}))
    result["last_updated_at"] = stamp
    return result


def statistics(state: dict[str, Any] | None) -> dict[str, Any]:
    result = copy_state(state)
    items = list(result["items"].values())
    by_reason = {reason: sum(item["reason"] == reason for item in items) for reason in sorted(REASONS)}
    by_status = {status: sum(item["review_status"] == status for item in items) for status in sorted(STATUSES)}
    versions = sorted({item["trace"]["model_version"] for item in items})
    parsers = sorted({item["trace"]["parser_version"] for item in items})
    registries = sorted({item["trace"]["registry_hash"] for item in items})
    return {"total": len(items), "by_reason": by_reason, "by_status": by_status, "model_versions": versions, "parser_versions": parsers, "registry_hashes": registries}
