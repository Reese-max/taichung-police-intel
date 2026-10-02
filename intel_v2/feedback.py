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
EFFECT_SCOPES = frozenset({"TRUTH_CORRECTION", "RELEVANCE_ONLY"})
PROMOTION_TARGETS = (
    "ANSWER_EVIDENCE_GATE",
    "ENTITY_REGISTRY",
    "EVENT_FUSION",
    "GOLD_DATASET",
    "RELEVANCE_EVALUATION",
    "SOURCE_POLICY",
)
# Accepted feedback may only be *proposed* to these downstream systems; every
# promotion still requires the human approval recorded on the fixture.
PROMOTION_ROUTES: dict[str, tuple[str, ...]] = {
    "FALSE_MERGE": ("ENTITY_REGISTRY", "EVENT_FUSION"),
    "MISSED_MERGE": ("ENTITY_REGISTRY", "EVENT_FUSION"),
    "WRONG_ENTITY": ("ENTITY_REGISTRY", "EVENT_FUSION"),
    "NOT_RELEVANT": ("RELEVANCE_EVALUATION",),
    "MISSING_EVENT": ("EVENT_FUSION", "GOLD_DATASET"),
    "WRONG_CHANGE_CLASSIFICATION": ("EVENT_FUSION", "GOLD_DATASET"),
    "UNSUPPORTED_ANSWER": ("ANSWER_EVIDENCE_GATE", "GOLD_DATASET"),
    "WRONG_STATISTIC_SCOPE": ("ANSWER_EVIDENCE_GATE", "GOLD_DATASET"),
    "BAD_SOURCE_MAPPING": ("EVENT_FUSION", "SOURCE_POLICY"),
}
# "Not relevant" is a relevance/evaluation judgement, not a truth judgement. Its
# corrected state may therefore only carry relevance keys; an allowlist keeps a
# nested or renamed truth assertion from reaching a downstream promotion target.
REASON_EFFECT_SCOPES: dict[str, str] = {"NOT_RELEVANT": "RELEVANCE_ONLY"}
RELEVANCE_ALLOWED_KEYS = frozenset({"relevance", "relevant", "relevance_reason", "relevance_score", "relevance_note", "profile_relevance", "local_police_relevance"})
ROUTE_FIELDS = ("effect_scope", "mutates_verified_truth", "promotion_targets", "requires_human_approval")
FIXTURE_KINDS = frozenset({"FEEDBACK_REGRESSION", "LINKED_REGRESSION"})
# The promotion payload and the record are closed schemas: an unrecognised key is
# the cheapest way to smuggle an "auto_apply" claim past a validator that only
# checks known fields, so anything unexpected fails closed.
FIXTURE_KEYS = {
    "FEEDBACK_REGRESSION": frozenset(
        {"fixture_id", "fixture_kind", "feedback_id", "target", "reason", "expected", "reviewer_ref", "decided_at", "requires_gold_promotion_review"}
    ),
    "LINKED_REGRESSION": frozenset(
        {"fixture_id", "fixture_kind", "feedback_id", "reason", "reviewer_ref", "linked_at", "requires_gold_promotion_review"}
    ),
}
STATE_KEYS = frozenset({"schema_version", "mode", "items", "last_updated_at"})
RECORD_KEYS = frozenset(
    {
        "feedback_id",
        "fingerprint",
        "target",
        "reason",
        "original_output_sha256",
        "corrected_expected_state",
        "evidence_refs",
        "linked_review_id",
        "review_status",
        "created_at",
        "updated_at",
        "trace",
        "regression_fixture",
        "decision",
        "audit",
    }
)
TARGET_KEYS = frozenset({"type", "id", "version"})
TRACE_KEYS = frozenset({"model_version", "parser_version", "registry_hash"})
DECISION_KEYS = frozenset({"status", "reviewer_ref", "decided_at"})
AUDIT_KEYS = frozenset({"audit_id", "feedback_id", "sequence", "action", "at", "payload"})
JSON_SCALARS = (str, int, float, bool)


def promotion_targets_for(reason: str) -> tuple[str, ...]:
    if not isinstance(reason, str) or reason not in PROMOTION_ROUTES:
        raise ValueError(f"unsupported feedback reason: {reason}")
    return PROMOTION_ROUTES[reason]


def effect_scope_for(reason: str) -> str:
    promotion_targets_for(reason)
    return REASON_EFFECT_SCOPES.get(reason, "TRUTH_CORRECTION")


def _reject_truth_assertion(reason: str, corrected_expected_state: Any) -> None:
    if effect_scope_for(reason) != "RELEVANCE_ONLY":
        return
    pending: list[Any] = [corrected_expected_state]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key, value in current.items():
                if key not in RELEVANCE_ALLOWED_KEYS:
                    raise ValueError(
                        "relevance-only feedback cannot change verified truth; "
                        f"corrected_expected_state allows only {sorted(RELEVANCE_ALLOWED_KEYS)}, got: {str(key)[:60]!r}"
                    )
                pending.append(value)
        elif isinstance(current, list):
            pending.extend(current)
        elif isinstance(current, JSON_SCALARS) or current is None:
            continue
        else:
            # A container that JSON cannot round-trip (tuple, set, ...) is not a
            # relevance assertion; refusing it keeps the stored state readable.
            raise ValueError(
                f"relevance-only feedback corrected_expected_state must be JSON-compatible, got: {type(current).__name__}"
            )


def _reject_truth_refs(reason: str, evidence_refs: Any) -> None:
    if effect_scope_for(reason) != "RELEVANCE_ONLY":
        return
    if any(not isinstance(ref, str) or not ref.strip() for ref in evidence_refs):
        raise ValueError("relevance-only feedback evidence_refs must be non-empty reference strings")


def route_fields_for(reason: str) -> dict[str, Any]:
    """Downstream proposal metadata every accepted fixture must carry for `reason`."""
    targets = promotion_targets_for(reason)
    effect_scope = REASON_EFFECT_SCOPES.get(reason, "TRUTH_CORRECTION")
    return {
        "effect_scope": effect_scope,
        "mutates_verified_truth": effect_scope == "TRUTH_CORRECTION",
        "promotion_targets": list(targets),
        "requires_human_approval": True,
    }


def _bind_route_fields(fixture: dict[str, Any], reason: str) -> None:
    missing = [field for field in ROUTE_FIELDS if field not in fixture]
    if missing and len(missing) < len(ROUTE_FIELDS):
        raise ValueError(f"regression_fixture route fields are incomplete: {', '.join(missing)}")
    if missing:
        raise ValueError("regression_fixture is missing route metadata")
    for field, value in route_fields_for(reason).items():
        # `is` for the booleans so a JSON 1/0 cannot stand in for True/False.
        matches = fixture.get(field) is value if isinstance(value, bool) else fixture.get(field) == value
        if not matches:
            raise ValueError(f"regression_fixture {field} must match the reviewed reason")


def fixture_id_for(feedback_id: str) -> str:
    return f"FEEDBACK-REGRESSION-{feedback_id.removeprefix('FEEDBACK-')}"


def fingerprint_for(
    target: dict[str, Any],
    reason: str,
    original_output_sha256: str,
    corrected_expected_state: dict[str, Any],
    evidence_refs: list[Any],
    linked_review_id: str | None,
) -> str:
    return sha256(
        {
            "target": {"type": target["type"], "id": target["id"], "version": target["version"]},
            "reason": reason,
            "original_output_sha256": original_output_sha256,
            "corrected_expected_state": corrected_expected_state,
            "evidence_refs": evidence_refs,
            "linked_review_id": linked_review_id,
        }
    )


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


def _audit_id(entry: dict[str, Any]) -> str:
    material = {key: entry.get(key) for key in ("feedback_id", "sequence", "action", "at", "payload")}
    return f"AUDIT-{sha256(material).upper()[:20]}"


def feedback_id_for(fingerprint: str) -> str:
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise ValueError("feedback fingerprint is required")
    return f"FEEDBACK-{sha256(fingerprint.strip().encode('utf-8')).upper()[:20]}"


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("feedback state must be an object")
    unknown_state_fields = sorted(str(key)[:60] for key in set(state) - STATE_KEYS)
    if unknown_state_fields:
        raise ValueError(f"feedback state has unsupported fields: {', '.join(unknown_state_fields)}")
    if type(state.get("schema_version")) is not int or state["schema_version"] != 1 or state.get("mode") != "FEEDBACK_LOOP":
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
            # Redundant while feedback_id is derived from the fingerprint; kept so
            # a hash-derivation regression cannot silently dedupe two records.
            if item["fingerprint"] in fingerprints:
                raise ValueError("non-duplicate feedback fingerprints must be unique")
            fingerprints.add(item["fingerprint"])
    if state.get("last_updated_at") is not None:
        timestamp(state["last_updated_at"])
    return state


def validate_record(item: dict[str, Any]) -> None:
    if not isinstance(item, dict):
        raise ValueError("feedback record must be an object")
    for forbidden in ("raw_prompt", "conversation", "full_text", "private_notes"):
        if forbidden in item:
            raise ValueError(f"feedback record must not contain {forbidden}")
    unknown_record_fields = sorted(str(key)[:60] for key in set(item) - RECORD_KEYS)
    if unknown_record_fields:
        raise ValueError(f"feedback record has unsupported fields: {', '.join(unknown_record_fields)}")
    target = item.get("target")
    if not isinstance(target, dict) or not isinstance(target.get("type"), str) or target.get("type") not in TARGET_TYPES:
        raise ValueError("feedback target type is invalid")
    if any(not isinstance(target.get(field), str) or not target[field].strip() for field in ("type", "id", "version")):
        raise ValueError("feedback target requires type/id/version")
    if set(target) - TARGET_KEYS:
        raise ValueError("feedback target has unsupported fields")
    if not isinstance(item.get("reason"), str) or not isinstance(item.get("review_status"), str):
        raise ValueError("feedback reason/status is invalid")
    if item.get("reason") not in REASONS or item.get("review_status") not in STATUSES:
        raise ValueError("feedback reason/status is invalid")
    output_hash = item.get("original_output_sha256")
    if not isinstance(output_hash, str) or len(output_hash) != 64 or any(char not in "0123456789abcdef" for char in output_hash):
        raise ValueError("original_output_sha256 must be a lowercase SHA-256")
    if not isinstance(item.get("corrected_expected_state"), dict):
        raise ValueError("corrected_expected_state must be an object")
    _reject_truth_assertion(item["reason"], item["corrected_expected_state"])
    if not isinstance(item.get("evidence_refs"), list) or any(not isinstance(ref, (str, dict)) for ref in item["evidence_refs"]):
        raise ValueError("evidence_refs must be a string/object array")
    _reject_truth_refs(item["reason"], item["evidence_refs"])
    linked_review_id = item.get("linked_review_id")
    if linked_review_id is not None and (not isinstance(linked_review_id, str) or not linked_review_id.strip()):
        raise ValueError("linked_review_id must be a non-empty reference string or null")
    trace = item.get("trace")
    if not isinstance(trace, dict) or any(not isinstance(trace.get(field), str) or not trace[field].strip() for field in ("model_version", "parser_version", "registry_hash")):
        raise ValueError("feedback trace requires model/parser/registry values")
    if set(trace) - TRACE_KEYS:
        raise ValueError("feedback trace has unsupported fields")
    if not isinstance(item.get("fingerprint"), str) or not item["fingerprint"]:
        raise ValueError("feedback fingerprint is required")
    if item.get("feedback_id") != feedback_id_for(item["fingerprint"]):
        raise ValueError("feedback ID does not match fingerprint")
    if item["fingerprint"] != fingerprint_for(
        target, item["reason"], output_hash, item["corrected_expected_state"], item["evidence_refs"], linked_review_id
    ):
        raise ValueError("feedback fingerprint does not match the record content")
    timestamp(item.get("created_at"))
    timestamp(item.get("updated_at"))
    audit = item.get("audit")
    if not isinstance(audit, list) or not audit:
        raise ValueError("feedback record requires audit history")
    for sequence, entry in enumerate(audit, start=1):
        if not isinstance(entry, dict) or set(entry) - AUDIT_KEYS:
            raise ValueError("feedback audit entry has unsupported fields")
        if entry.get("feedback_id") != item["feedback_id"]:
            raise ValueError("feedback audit identity is invalid")
        if entry.get("sequence") != sequence or not isinstance(entry.get("action"), str) or not entry["action"].strip():
            raise ValueError("feedback audit sequence is invalid")
        timestamp(entry.get("at"))
        if not isinstance(entry.get("payload"), dict) or entry.get("audit_id") != _audit_id(entry):
            raise ValueError("feedback audit binding is invalid")
    decision = item.get("decision")
    if item["review_status"] == "NEW":
        if decision is not None:
            raise ValueError("new feedback cannot contain a review decision")
    elif not isinstance(decision, dict):
        raise ValueError("reviewed feedback requires a bound decision")
    elif set(decision) - DECISION_KEYS:
        raise ValueError("feedback decision has unsupported fields")
    elif (
        decision.get("status") != item["review_status"]
        or not isinstance(decision.get("reviewer_ref"), str)
        or not decision["reviewer_ref"].strip()
    ):
        raise ValueError("reviewed feedback requires a bound decision")
    if isinstance(decision, dict):
        timestamp(decision.get("decided_at"))

    regression = item.get("regression_fixture")
    if regression is not None and (not isinstance(regression, dict) or not isinstance(regression.get("fixture_id"), str) or not regression["fixture_id"].strip()):
        raise ValueError("regression_fixture must contain a non-empty fixture_id")
    if isinstance(regression, dict):
        kind = regression.get("fixture_kind")
        if not isinstance(kind, str) or kind not in FIXTURE_KINDS:
            raise ValueError("regression_fixture fixture_kind is invalid")
        unknown = sorted(str(key)[:60] for key in set(regression) - FIXTURE_KEYS[kind] - set(ROUTE_FIELDS))
        if unknown:
            raise ValueError(f"regression_fixture has unsupported fields: {', '.join(unknown)}")
        absent = sorted(FIXTURE_KEYS[kind] - set(regression))
        if absent:
            raise ValueError(f"regression_fixture is missing required fields: {', '.join(absent)}")
        if regression.get("reason") != item["reason"]:
            raise ValueError("regression_fixture reason must match the reviewed feedback")
        if item["review_status"] != "ACCEPTED":
            raise ValueError("only accepted feedback may carry a regression fixture")
        if not isinstance(regression.get("reviewer_ref"), str) or not regression["reviewer_ref"].strip():
            raise ValueError("regression_fixture reviewer_ref must name the approving human")
        if regression.get("requires_gold_promotion_review") is not True:
            raise ValueError("regression_fixture requires_gold_promotion_review must stay true")
        if regression.get("feedback_id") != item["feedback_id"]:
            raise ValueError("regression_fixture feedback_id must match the reviewed feedback")
        # `FIXTURE_KEYS` is what keeps the two kinds apart: a relabelled fixture
        # carries the other kind's marker and payload keys, which are unknown here.
        if regression["reviewer_ref"] != item["decision"].get("reviewer_ref"):
            raise ValueError("regression_fixture reviewer_ref must match the approving reviewer")
        if kind == "FEEDBACK_REGRESSION":
            if regression.get("fixture_id") != fixture_id_for(item["feedback_id"]):
                raise ValueError("regression_fixture fixture_id must be derived from the feedback ID")
            if regression.get("target") != item["target"] or regression.get("expected") != item["corrected_expected_state"]:
                raise ValueError("regression_fixture target/expected must match the reviewed feedback")
            timestamp(regression["decided_at"])
        else:
            timestamp(regression["linked_at"])
        _bind_route_fields(regression, item["reason"])


def copy_state(state: dict[str, Any] | None) -> dict[str, Any]:
    return normalize_state(empty_state() if state is None else state)


def _backfill_legacy_route_fields(item: Any) -> None:
    regression = item.get("regression_fixture") if isinstance(item, dict) else None
    if not isinstance(regression, dict) or any(field in regression for field in ROUTE_FIELDS):
        return
    if not isinstance(item.get("reason"), str) or item["reason"] not in PROMOTION_ROUTES:
        return
    # Schema-version-1 fixture written before route metadata existed. Every value
    # is derived from the reviewed reason, so this grants no extra authority; a
    # partially deleted set is left alone so validation fails closed below.
    regression.update(route_fields_for(item["reason"]))


def normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a validated copy of `state`, backfilling pre-route-metadata fixtures.

    The input is never mutated, so a rejected candidate cannot leave a
    half-normalised state behind for the caller to persist. `validate_state` is
    the pure validator: it refuses accepted fixtures written before route
    metadata existed, and this is the load path that upgrades them.
    """
    result = copy.deepcopy(state)
    if isinstance(result, dict) and isinstance(result.get("items"), dict):
        for item in result["items"].values():
            _backfill_legacy_route_fields(item)
    return validate_state(result)


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
    if any(not isinstance(value, str) or not value.strip() for value in (target_type, target_id, target_version)):
        raise ValueError("target type/id/version are required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("feedback reason is required")
    target_type = target_type.upper()
    reason = reason.upper()
    if target_type not in TARGET_TYPES:
        raise ValueError("target type/id/version are required")
    if reason not in REASONS:
        raise ValueError(f"unsupported feedback reason: {reason}")
    if not isinstance(original_output_sha256, str) or len(original_output_sha256) != 64 or any(char not in "0123456789abcdef" for char in original_output_sha256):
        raise ValueError("original_output_sha256 must be a lowercase SHA-256")
    refs = [] if evidence_refs is None else copy.deepcopy(evidence_refs)
    if not isinstance(refs, list) or any(not isinstance(ref, (str, dict)) for ref in refs):
        raise ValueError("evidence_refs must be a string/object array")
    stamp = timestamp(created_at)
    if linked_review_id is not None and (not isinstance(linked_review_id, str) or not linked_review_id.strip()):
        raise ValueError("linked_review_id must be a non-empty reference string or null")
    corrected = copy.deepcopy({} if corrected_expected_state is None else corrected_expected_state)
    if not isinstance(corrected, dict):
        raise ValueError("corrected_expected_state must be an object")
    _reject_truth_assertion(reason, corrected)
    _reject_truth_refs(reason, refs)
    fingerprint = fingerprint_for(
        {"type": target_type, "id": target_id, "version": target_version},
        reason,
        original_output_sha256,
        corrected,
        refs,
        linked_review_id,
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
        "fixture_id": fixture_id_for(item["feedback_id"]),
        "fixture_kind": "FEEDBACK_REGRESSION",
        "feedback_id": item["feedback_id"],
        "target": copy.deepcopy(item["target"]),
        "reason": item["reason"],
        "expected": copy.deepcopy(item["corrected_expected_state"]),
        "reviewer_ref": reviewer_ref,
        "decided_at": timestamp(decided_at),
        "requires_gold_promotion_review": True,
        **route_fields_for(item["reason"]),
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
    if not isinstance(feedback_id, str) or not feedback_id.strip():
        raise ValueError("feedback_id is required")
    item = result["items"].get(feedback_id)
    if item is None:
        raise ValueError(f"unknown feedback ID: {feedback_id}")
    if not isinstance(status, str):
        raise ValueError("review status is required")
    if not isinstance(reviewer_ref, str) or not reviewer_ref.strip():
        raise ValueError("reviewer_ref is required")
    status = status.upper()
    if status not in {"ACCEPTED", "REJECTED", "DUPLICATE"}:
        raise ValueError("review status must be ACCEPTED, REJECTED, or DUPLICATE")
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
    if not isinstance(feedback_id, str) or not feedback_id.strip():
        raise ValueError("feedback_id is required")
    item = result["items"].get(feedback_id)
    if item is None or item.get("review_status") != "ACCEPTED":
        raise ValueError("only accepted feedback can link a regression fixture")
    if not isinstance(fixture_id, str) or not fixture_id.strip() or not isinstance(reviewer_ref, str) or not reviewer_ref.strip():
        raise ValueError("fixture_id and reviewer_ref are required")
    stamp = timestamp(linked_at)
    item["regression_fixture"] = {
        "fixture_id": fixture_id,
        "fixture_kind": "LINKED_REGRESSION",
        "feedback_id": feedback_id,
        "reason": item["reason"],
        "reviewer_ref": reviewer_ref,
        "linked_at": stamp,
        "requires_gold_promotion_review": True,
        **route_fields_for(item["reason"]),
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
    # Routes and effect scopes describe what an operator may be asked to approve,
    # so only reviewed-accepted feedback is counted; `by_reason`/`by_status` keep
    # the unfiltered "most common error type" view.
    accepted = [item for item in items if item["review_status"] == "ACCEPTED"]
    by_effect_scope = {scope: sum(effect_scope_for(item["reason"]) == scope for item in accepted) for scope in sorted(EFFECT_SCOPES)}
    by_route = {
        target: sum(target in promotion_targets_for(item["reason"]) for item in accepted)
        for target in PROMOTION_TARGETS
    }
    versions = sorted({item["trace"]["model_version"] for item in items})
    parsers = sorted({item["trace"]["parser_version"] for item in items})
    registries = sorted({item["trace"]["registry_hash"] for item in items})
    return {
        "total": len(items),
        "accepted": len(accepted),
        "by_reason": by_reason,
        "by_status": by_status,
        "by_effect_scope": by_effect_scope,
        "by_route": by_route,
        "model_versions": versions,
        "parser_versions": parsers,
        "registry_hashes": registries,
    }
