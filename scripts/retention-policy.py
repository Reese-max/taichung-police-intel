#!/usr/bin/env python3
"""Compile conservative rights/retention policy for public projections."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "govintel" / "source-catalog.v2.json"
POLICY = ROOT / "docs" / "govintel" / "retention-rights-policy.v1.json"
PUBLIC_PROJECTIONS = {"LINK_ONLY", "METADATA_LINK_ONLY", "EVIDENCE_BOUND_SUMMARY_ONLY"}
NO_PUBLIC_PROJECTION = "NONE"
RETENTION_LAYERS = {"raw_snapshot", "normalized_document", "canonical_event", "publication", "query_index"}
RAW_LAYERS = {"raw_snapshot", "normalized_document"}
CANONICAL_LAYERS = RETENTION_LAYERS - RAW_LAYERS
EXPIRY_ACTIONS = {"REVIEW_REQUIRED", "PURGE_RAW_KEEP_AUDIT", "KEEP_AUDIT_LINKAGE", "KEEP_AUDIT_RECEIPT"}
HASH_CHARS = set("0123456789abcdef")
ARCHIVE_ELIGIBILITY = {"NOT_ARCHIVABLE", "PROVENANCE_ONLY", "EVIDENCE_ARCHIVE"}
WITHDRAWAL_PROJECTIONS = {"SOURCE_NO_LONGER_AVAILABLE", "RETRACTED"}
TERMS_STATUSES = {"CATALOG_ENTRYPOINT_NOT_A_LICENSE", "PROJECT_OWNED_NO_THIRD_PARTY_LICENSE"}
UNKNOWN_RIGHTS_TERMS_STATUS = "CATALOG_ENTRYPOINT_NOT_A_LICENSE"
DATA_TYPE_KINDS = {"CONTENT", "LAYER"}
LAYER_POLICY_FIELDS = {"retention_class_source", "archive_eligibility", "public_projection"}
RETENTION_CLASS_SOURCES = {"raw_retention_class", "canonical_retention_class"}
REPLAY_STATUSES = {"NOT_REQUIRED", "PRESERVED", "LIMITED", "BLOCKED"}
REPLAY_LIMITATION_SOURCE_UNAVAILABLE = "SOURCE_NO_LONGER_AVAILABLE_REPLAY_PARTIAL"
REPLAY_LIMITATION_NO_EVIDENCE = "REPLAY_EVIDENCE_INCOMPLETE"
REPLAY_REVIEW_WINDOW_PENDING = "REPLAY_REVIEW_WINDOW_PENDING"
REPLAY_LIMITATION_REASONS = {
    REPLAY_LIMITATION_SOURCE_UNAVAILABLE,
    REPLAY_LIMITATION_NO_EVIDENCE,
    REPLAY_REVIEW_WINDOW_PENDING,
}
# A retention sweep must never quietly outrank a window that still demands human review.
NON_OVERRIDABLE_EXPIRY_ACTIONS = {"REVIEW_REQUIRED"}
RIGHTS_STATUSES = {"UNKNOWN", "PROJECT_CONTROLLED"}
UNKNOWN_RIGHTS = "UNKNOWN"
LAYER_PROJECTIONS = PUBLIC_PROJECTIONS | {NO_PUBLIC_PROJECTION}
QUERY_INDEX_LAYER = "query_index"
# A query index is a projection: it can never widen or resurrect the source retention it
# reads from. One constant so the entry, the index and the binding cannot disagree.
QUERY_INDEX_EXTENDS_SOURCE_RETENTION = False
SENSITIVE_RECORD_FLAGS = ("contains_personal_data", "sensitive")
GOVERNANCE_RECORD_FLAGS = (
    *SENSITIVE_RECORD_FLAGS, "source_available", "replay_required", "aggregate_only",
)
DEFAULT_TRUE_RECORD_FLAGS = frozenset({"source_available"})
# Raw captured payloads are never publishable; the class allowlist drops them and the
# projection reports them. Every other prohibited field is a governance marker that must
# block the archive instead of being quietly projected away.
CONTENT_PAYLOAD_FIELDS = {"raw_payload", "raw_bytes", "body", "full_text"}
# Governance metadata a projection consumes itself; never re-published as content and never
# reported as a dropped payload field.
# A class is never a record field: governance is chosen by the caller, never by the payload.
RECORD_CONTROL_FIELDS = {
    "record_id", "source_id", "layer", "captured_at",
    "source_available", "replay_required", "aggregate_only", "personal_data",
    *SENSITIVE_RECORD_FLAGS,
}
REQUIRED_CLASS_FIELDS = {
    "rights_status", "terms_status", "public_projection", "full_text_allowed", "excerpt_allowed",
    "public_fields", "archive_eligibility", "sensitive_default",
    "raw_retention_class", "canonical_retention_class", "review_required",
    "withdrawal_behavior", "withdrawal_projection",
}
REQUIRED_POLICY_SECTIONS = (
    "rights_status_values", "terms_status_values", "replay_evidence_fields",
    "replay_limitation_reasons", "data_type_matrix", "layer_policies",
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include timezone")
    return parsed.astimezone(timezone.utc)


def _hash(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in HASH_CHARS for char in value):
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _require_documented_values(section: Any, field: str) -> dict[str, str]:
    if not isinstance(section, dict) or not section:
        raise ValueError(f"{field} must document at least one value")
    if any(not isinstance(key, str) or not key.strip() for key in section):
        raise ValueError(f"{field} keys must be non-empty strings")
    if any(not isinstance(value, str) or not value.strip() for value in section.values()):
        raise ValueError(f"{field} descriptions must be non-empty strings")
    return section


def _require_field_list(section: Any, field: str) -> list[str]:
    if not isinstance(section, list) or not section:
        raise ValueError(f"{field} must be a non-empty array of unique strings")
    if any(not isinstance(item, str) or not item.strip() for item in section):
        raise ValueError(f"{field} entries must be non-empty strings")
    if len(section) != len(set(section)):
        raise ValueError(f"{field} entries must be unique")
    return section


def _compile_layer_policies(layer_policies: Any) -> dict[str, Any]:
    if not isinstance(layer_policies, dict) or set(layer_policies) != RETENTION_LAYERS:
        raise ValueError("layer policies must cover exactly every retention layer")
    compiled_layers = {}
    for layer, value in layer_policies.items():
        if not isinstance(value, dict) or set(value) != LAYER_POLICY_FIELDS:
            raise ValueError(f"layer policy is incomplete: {layer}")
        if value["retention_class_source"] not in RETENTION_CLASS_SOURCES:
            raise ValueError(f"layer policy retention source is invalid: {layer}")
        if value["archive_eligibility"] not in ARCHIVE_ELIGIBILITY:
            raise ValueError(f"layer archive eligibility is invalid: {layer}")
        if value["public_projection"] not in LAYER_PROJECTIONS:
            raise ValueError(f"layer public projection is invalid: {layer}")
        if value["public_projection"] == NO_PUBLIC_PROJECTION and value["archive_eligibility"] != "NOT_ARCHIVABLE":
            raise ValueError(f"layer that publishes nothing must not be archivable: {layer}")
        compiled_layers[layer] = dict(value)
    if compiled_layers[QUERY_INDEX_LAYER]["public_projection"] not in PUBLIC_PROJECTIONS:
        raise ValueError("the query index layer must publish a public projection")
    return compiled_layers


def _compile_data_types(matrix: Any, classes: dict[str, Any], layer_policies: dict[str, Any]) -> set[str]:
    if not isinstance(matrix, list) or not matrix:
        raise ValueError("data type matrix must be a non-empty array")
    seen: set[str] = set()
    referenced_layers: set[str] = set()
    for entry in matrix:
        if not isinstance(entry, dict):
            raise ValueError("data type matrix entries must be objects")
        data_type = entry.get("data_type")
        if not isinstance(data_type, str) or not data_type.strip() or data_type in seen:
            raise ValueError("data type matrix entries must have unique non-empty data_type names")
        seen.add(data_type)
        if entry.get("kind") not in DATA_TYPE_KINDS:
            raise ValueError(f"data type kind is unsupported: {data_type}")
        if entry["kind"] == "CONTENT":
            if entry.get("class_id") not in classes:
                raise ValueError(f"data type references unknown retention class: {data_type}")
        elif entry["kind"] == "LAYER":
            if entry.get("layer") not in layer_policies:
                raise ValueError(f"data type references unknown retention layer: {data_type}")
            referenced_layers.add(entry["layer"])
    if referenced_layers != set(layer_policies):
        raise ValueError("every retention layer must be governed by exactly one data type matrix entry")
    return seen


def compile_policy(catalog: dict[str, Any] | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = load_json(CATALOG) if catalog is None else catalog
    policy = load_json(POLICY) if policy is None else policy
    if not isinstance(catalog, dict):
        raise ValueError("source catalog must be an object")
    if not isinstance(policy, dict):
        raise ValueError("retention policy must be an object")
    if catalog.get("schema_version") != 2 or not isinstance(catalog.get("sources"), list):
        raise ValueError("source catalog schema is unsupported")
    if policy.get("schema_version") != 1 or not isinstance(policy.get("policy_version"), int):
        raise ValueError("retention policy schema is unsupported")
    classes = policy.get("classes")
    source_classes = policy.get("source_classes")
    retention_windows = policy.get("retention_windows")
    prohibited = policy.get("prohibited_public_fields")
    if not isinstance(classes, dict) or not isinstance(source_classes, dict) or not isinstance(retention_windows, dict) or not isinstance(prohibited, list):
        raise ValueError("retention policy sections are invalid")
    if any(not isinstance(row, dict) for row in catalog["sources"]):
        raise ValueError("every catalog source must be an object")
    catalog_ids = {row.get("source_id") for row in catalog["sources"]}
    if None in catalog_ids or set(source_classes) != catalog_ids:
        raise ValueError("every catalog source must have exactly one retention class")
    _require_field_list(prohibited, "prohibited_public_fields")
    if not CONTENT_PAYLOAD_FIELDS <= set(prohibited):
        raise ValueError("every content payload field must be prohibited")
    for window_id, window in retention_windows.items():
        if not isinstance(window, dict) or set(window) != {"max_age_days", "expired_action"}:
            raise ValueError(f"retention window is invalid: {window_id}")
        if window["max_age_days"] is not None and (type(window["max_age_days"]) is not int or window["max_age_days"] < 0):
            raise ValueError(f"retention window days are invalid: {window_id}")
        if window["expired_action"] not in EXPIRY_ACTIONS:
            raise ValueError(f"retention window action is invalid: {window_id}")

    missing_sections = sorted(name for name in REQUIRED_POLICY_SECTIONS if name not in policy)
    if missing_sections:
        raise ValueError(f"retention policy is missing required sections: {','.join(missing_sections)}")
    rights_status_values = _require_documented_values(
        policy["rights_status_values"], "rights_status_values")
    terms_status_values = _require_documented_values(
        policy["terms_status_values"], "terms_status_values")
    if set(rights_status_values) - RIGHTS_STATUSES:
        raise ValueError("rights_status_values declares an undocumented rights status")
    if set(terms_status_values) - TERMS_STATUSES:
        raise ValueError("terms_status_values declares an undocumented terms status")
    replay_limitation_reasons = _require_documented_values(
        policy["replay_limitation_reasons"], "replay_limitation_reasons")
    if set(REPLAY_LIMITATION_REASONS) - set(replay_limitation_reasons):
        raise ValueError("replay limitations must document every reason the planner can emit")
    replay_evidence_fields = _require_field_list(
        policy["replay_evidence_fields"], "replay_evidence_fields")
    _require_field_list(policy["prohibited_public_fields"], "prohibited_public_fields")
    layer_policies = _compile_layer_policies(policy.get("layer_policies"))
    declared_data_types = _compile_data_types(policy.get("data_type_matrix"), classes, layer_policies)

    compiled_classes = {}
    for class_id, value in classes.items():
        if not isinstance(value, dict) or set(value) != REQUIRED_CLASS_FIELDS:
            raise ValueError(f"retention class fields do not match the compiled vocabulary: {class_id}")
        if value["rights_status"] == "UNKNOWN" and value["terms_status"] != UNKNOWN_RIGHTS_TERMS_STATUS:
            raise ValueError(f"unknown rights must not be presented as an open permission: {class_id}")
        if value["rights_status"] not in rights_status_values:
            raise ValueError(f"rights status is undocumented: {class_id}")
        if value["terms_status"] not in terms_status_values:
            raise ValueError(f"terms status is undocumented: {class_id}")
        if value["public_projection"] not in PUBLIC_PROJECTIONS:
            raise ValueError(f"unsupported public projection: {class_id}")
        if value["archive_eligibility"] not in ARCHIVE_ELIGIBILITY:
            raise ValueError(f"unsupported archive eligibility: {class_id}")
        if value["withdrawal_projection"] not in WITHDRAWAL_PROJECTIONS:
            raise ValueError(f"unsupported withdrawal projection: {class_id}")
        retracts = value["withdrawal_behavior"].startswith("RETRACT_PROJECTION")
        if retracts != (value["withdrawal_projection"] == "RETRACTED"):
            raise ValueError(f"withdrawal projection must match withdrawal behavior: {class_id}")
        if type(value["sensitive_default"]) is not bool:
            raise ValueError(f"sensitive_default must be a boolean: {class_id}")
        public_fields = _require_field_list(value["public_fields"], f"public_fields:{class_id}")
        if set(public_fields) & set(prohibited):
            raise ValueError(f"public field is prohibited: {class_id}")
        if value["full_text_allowed"] or value["excerpt_allowed"]:
            raise ValueError(f"full text/excerpts require a separately reviewed policy: {class_id}")
        if value["rights_status"] == "UNKNOWN" and not value["review_required"]:
            raise ValueError(f"unknown rights cannot skip review: {class_id}")
        for field in ("raw_retention_class", "canonical_retention_class"):
            if value[field] not in retention_windows:
                raise ValueError(f"unknown retention window: {value[field]}")
        compiled_classes[class_id] = dict(value)

    if any(class_id not in compiled_classes for class_id in source_classes.values()):
        raise ValueError("source references unknown retention class")

    catalog_terms = {}
    for row in catalog["sources"]:
        entrypoint = row.get("entrypoint") if isinstance(row, dict) else None
        if not isinstance(entrypoint, str) or not entrypoint.startswith("https://"):
            raise ValueError(f"catalog entrypoint is required as the terms reference: {row.get('source_id')}")
        catalog_terms[row["source_id"]] = entrypoint
    class_data_types: dict[str, list[str]] = {class_id: [] for class_id in compiled_classes}
    compiled_data_types: dict[str, Any] = {}
    for entry in policy["data_type_matrix"]:
        data_type = entry["data_type"]
        if entry["kind"] == "CONTENT":
            class_id = entry["class_id"]
            class_data_types[class_id].append(data_type)
            compiled_data_types[data_type] = {
                "kind": "CONTENT",
                "class_id": class_id,
                "public_projection": compiled_classes[class_id]["public_projection"],
                "archive_eligibility": compiled_classes[class_id]["archive_eligibility"],
                "rights_status": compiled_classes[class_id]["rights_status"],
            }
        else:
            layer_policy = layer_policies[entry["layer"]]
            compiled_data_types[data_type] = {
                "kind": "LAYER",
                "layer": entry["layer"],
                "retention_class_source": layer_policy["retention_class_source"],
                "archive_eligibility": layer_policy["archive_eligibility"],
                "public_projection": layer_policy["public_projection"],
            }
    if set(compiled_data_types) != declared_data_types:
        raise ValueError("data type matrix is inconsistent")

    material = {
        "schema_version": policy["schema_version"],
        "policy_version": policy["policy_version"],
        "updated_at": policy.get("updated_at"),
        "retention_windows": dict(sorted(retention_windows.items())),
        "layer_policies": dict(sorted(layer_policies.items())),
        "data_type_matrix": list(policy["data_type_matrix"]),
        "rights_status_values": dict(sorted(rights_status_values.items())),
        "terms_status_values": dict(sorted(terms_status_values.items())),
        "replay_evidence_fields": sorted(replay_evidence_fields),
        "replay_limitation_reasons": dict(sorted(replay_limitation_reasons.items())),
        "classes": compiled_classes,
        "source_classes": dict(sorted(source_classes.items())),
        "prohibited_public_fields": sorted(prohibited),
    }
    return {
        **material,
        "policy_hash": sha256(material),
        "catalog_hash": sha256(catalog),
        "retention_windows": dict(sorted(retention_windows.items())),
        "layer_policies": dict(sorted(layer_policies.items())),
        "data_types": dict(sorted(compiled_data_types.items())),
        "class_data_types": {key: sorted(value) for key, value in sorted(class_data_types.items())},
        "rights_status_values": dict(sorted(rights_status_values.items())),
        "terms_status_values": dict(sorted(terms_status_values.items())),
        "replay_evidence_fields": sorted(replay_evidence_fields),
        "replay_limitation_reasons": dict(sorted(replay_limitation_reasons.items())),
        "source_policies": {
            source_id: {
                "class_id": class_id,
                **compiled_classes[class_id],
                "terms_url": catalog_terms[source_id],
                "data_types": sorted(class_data_types[class_id]),
            }
            for source_id, class_id in sorted(source_classes.items())
        },
    }


def _record_flag(record: dict[str, Any], field: str, *, default: bool | None = None) -> bool:
    if field not in record:
        # A flag absent from the record reads as its documented default.
        return field in DEFAULT_TRUE_RECORD_FLAGS if default is None else default
    value = record[field]
    if type(value) is not bool:
        raise ValueError(f"{field} must be a boolean, not {type(value).__name__}")
    return value


def _json_safe(value: Any, field: str, record_id: str) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise ValueError(f"projection field is not JSON serializable: {record_id} -> {field}") from error
    return value


def _validate_governance_flags(record: dict[str, Any]) -> None:
    """Every governance boolean must be a real boolean; an explicit null is a contract error.

    Only the type is checked here. Whether a missing flag means ``False`` or ``True`` is
    the caller's decision, so each call site reads the flag it actually acts on.
    """
    for flag in GOVERNANCE_RECORD_FLAGS:
        _record_flag(record, flag)


def _governance(compiled: dict[str, Any], record: dict[str, Any],
                *, class_id: str | None = None, layer: str | None = None) -> dict[str, Any]:
    """Resolve the governance that applies to one record.

    ``class_id`` is a trusted caller argument, never read from the record: a stored
    payload must not be able to re-classify itself into a more permissive policy.
    """
    if not isinstance(record, dict):
        raise ValueError("retention records must be objects")
    record_id = record.get("record_id")
    if not isinstance(record_id, str) or not record_id.strip():
        raise ValueError("retention record requires a non-empty record_id")
    _validate_governance_flags(record)
    source_id = record.get("source_id")
    if not isinstance(source_id, str) or source_id not in compiled["source_policies"]:
        raise ValueError(f"unknown retention source: {source_id}")
    resolved_class = class_id or compiled["source_policies"][source_id]["class_id"]
    if resolved_class not in compiled["classes"]:
        raise ValueError(f"unknown retention class: {resolved_class}")
    resolved_layer = layer if layer is not None else record.get("layer")
    if resolved_layer not in compiled["layer_policies"]:
        raise ValueError(f"unsupported retention layer: {resolved_layer}")
    # Source-level facts (terms_url, data_types) stay bound to the source and the class
    # supplies class governance. The layer policy is kept in its own namespaced keys so
    # it can tighten what is publishable without overwriting the class's own rights.
    layer_policy = compiled["layer_policies"][resolved_layer]
    return {
        **compiled["source_policies"][source_id],
        **compiled["classes"][resolved_class],
        # Data types are a property of the class that was actually applied.
        "data_types": compiled["class_data_types"][resolved_class],
        "class_id": resolved_class,
        "record_id": record_id,
        "source_id": source_id,
        "layer": resolved_layer,
        "layer_archive_eligibility": layer_policy["archive_eligibility"],
        "layer_public_projection": layer_policy["public_projection"],
        "layer_retention_class_source": layer_policy["retention_class_source"],
    }


def _assert_no_prohibited(record: dict[str, Any], compiled: dict[str, Any], record_id: str) -> None:
    prohibited = sorted(set(record) & set(compiled["prohibited_public_fields"]))
    if prohibited:
        raise ValueError(f"prohibited field(s) present: {record_id} -> {','.join(prohibited)}")


def archive_decision(record: dict[str, Any], *, policy: dict[str, Any] | None = None,
                     class_id: str | None = None, layer: str | None = None) -> dict[str, Any]:
    """Decide whether one record may enter the public evidence archive.

    A layer declared ``NOT_ARCHIVABLE`` outranks the class, so raw snapshots and
    normalized documents can never be archived just because their source class would
    otherwise allow it.
    """
    compiled = compile_policy() if policy is None else policy
    governance = _governance(compiled, record, class_id=class_id, layer=layer)
    decision = {
        "record_id": governance["record_id"],
        "source_id": governance["source_id"],
        "class_id": governance["class_id"],
        "layer": governance["layer"],
        "archive_eligibility": governance["archive_eligibility"],
        "sensitive_default": governance["sensitive_default"],
    }
    blocked_by = sorted(set(record) & (set(compiled["prohibited_public_fields"]) - CONTENT_PAYLOAD_FIELDS))
    if blocked_by:
        return {**decision, "decision": "BLOCKED",
                "reason": f"prohibited field(s) present: {','.join(blocked_by)}"}
    for flag in SENSITIVE_RECORD_FLAGS:
        if _record_flag(record, flag):
            return {**decision, "decision": "BLOCKED", "reason": f"{flag} is true"}
    aggregate_only = _record_flag(record, "aggregate_only")
    if governance["sensitive_default"] and aggregate_only is not True:
        return {**decision, "decision": "BLOCKED",
                "reason": f"{governance['class_id']} is sensitive by default and the record does not declare aggregate_only"}
    if governance["layer_archive_eligibility"] == "NOT_ARCHIVABLE":
        return {**decision, "decision": "BLOCKED",
                "reason": f"retention layer is never publicly archivable: {governance['layer']}",
                "blocking_archive_eligibility": governance["layer_archive_eligibility"]}
    if governance["archive_eligibility"] == "NOT_ARCHIVABLE":
        return {**decision, "decision": "BLOCKED",
                "reason": f"retention class is never publicly archivable: {governance['class_id']}",
                "blocking_archive_eligibility": governance["archive_eligibility"]}
    return {**decision, "decision": "ARCHIVE",
            "reason": f"{governance['class_id']}/{governance['layer']} may enter the public evidence archive as a projection"}


def project_public_record(record: dict[str, Any], *, policy: dict[str, Any] | None = None,
                          class_id: str | None = None,
                          layer: str | None = None) -> dict[str, Any]:
    """Publish only the class-allowlisted fields of a record, then gate the result.

    Content fields outside the allowlist are dropped and reported in ``dropped_fields``
    so a rights decision is auditable instead of silent. Personal-data and sensitive
    markers survive the drop and still fail the archive gate closed. A layer that is
    ``NOT_ARCHIVABLE`` or that publishes ``NONE`` yields no fields at all.
    """
    compiled = compile_policy() if policy is None else policy
    governance = _governance(compiled, record, class_id=class_id, layer=layer)
    layer = governance["layer"]
    blocked = archive_decision(record, policy=compiled, class_id=class_id, layer=layer)
    if blocked["decision"] != "ARCHIVE":
        raise ValueError(f"record is not allowed into the public archive: {blocked['reason']}")
    source_available = _record_flag(record, "source_available")
    projected = {
        key: _json_safe(value, key, governance["record_id"])
        for key, value in record.items()
        if key in governance["public_fields"] and key not in RECORD_CONTROL_FIELDS
    }
    dropped = sorted(
        key for key in record
        if key not in governance["public_fields"] and key not in RECORD_CONTROL_FIELDS
    )
    withdrawal_projection = governance["withdrawal_projection"]
    source_availability = "AVAILABLE"
    if not source_available:
        source_availability = withdrawal_projection
        if withdrawal_projection == "RETRACTED":
            projected = {}
    payload = {
        "record_id": governance["record_id"],
        "source_id": governance["source_id"],
        "class_id": governance["class_id"],
        "layer": layer,
        "data_types": list(governance["data_types"]),
        "public_projection": governance["public_projection"],
        "layer_public_projection": governance["layer_public_projection"],
        "rights_status": governance["rights_status"],
        "terms_status": governance["terms_status"],
        "terms_url": governance["terms_url"],
        "catalog_hash": compiled["catalog_hash"],
        "review_required": governance["review_required"],
        "archive_eligibility": governance["archive_eligibility"],
        "layer_archive_eligibility": governance["layer_archive_eligibility"],
        "withdrawal_behavior": governance["withdrawal_behavior"],
        "withdrawal_projection": withdrawal_projection,
        "source_availability": source_availability,
        "currently_available": bool(source_available),
        "projected_fields": projected,
        "dropped_fields": dropped,
    }
    payload["projection_hash"] = sha256(payload)
    return payload


def project_query_index(records: list[dict[str, Any]], *, observed_at: str,
                        policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Rebuild a query index that can never widen or resurrect source retention."""
    compiled = compile_policy() if policy is None else policy
    if not isinstance(records, list):
        raise ValueError("query index records must be an array")
    now = _timestamp(observed_at, "observed_at")
    layer_policy = compiled["layer_policies"]["query_index"]
    entries = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("query index records must be objects")
        # Gate the record as stored, against the layer this rebuild writes into, before
        # anything else. Scrubbing first, or trusting the record's own layer claim,
        # would downgrade a governance marker into a silent success.
        blocked = archive_decision(record, policy=compiled, layer=QUERY_INDEX_LAYER)
        if blocked["decision"] != "ARCHIVE":
            raise ValueError(
                f"record is not allowed into the public archive: {blocked['reason']}")
        if record.get("layer", QUERY_INDEX_LAYER) != QUERY_INDEX_LAYER:
            # A record that claims another layer is either mis-filed or probing for a
            # weaker gate; either way it does not belong in this index.
            raise ValueError(f"unsupported retention layer for the query index: {record.get('layer')}")
        governance = _governance(compiled, record, layer=QUERY_INDEX_LAYER)
        suppressed = sorted(set(record) & set(compiled["prohibited_public_fields"]))
        sanitized = {key: value for key, value in record.items() if key not in suppressed}
        entry = project_public_record(sanitized, policy=compiled, layer="query_index")
        retention_class = governance[layer_policy["retention_class_source"]]
        raw_retention_class = governance["raw_retention_class"]
        max_age_days = compiled["retention_windows"][raw_retention_class]["max_age_days"]
        captured = _timestamp(record.get("captured_at"), "captured_at")
        entry.update({
            "retention_class": retention_class,
            "source_raw_retention_class": raw_retention_class,
            "source_raw_retention_expired": max_age_days is not None and captured + timedelta(days=max_age_days) <= now,
            "extends_source_retention": QUERY_INDEX_EXTENDS_SOURCE_RETENTION,
            "suppressed_fields": suppressed,
        })
        # Hash the entry the caller receives, not the pre-merge projection.
        entry.pop("projection_hash", None)
        entry["projection_hash"] = sha256(entry)
        entries.append(entry)
    index = {
        "schema_version": 1,
        "projection": "query-index-v1",
        "policy_version": compiled["policy_version"],
        "policy_hash": compiled["policy_hash"],
        "observed_at": now.isoformat(),
        "projection_only": True,
        "extends_source_retention": QUERY_INDEX_EXTENDS_SOURCE_RETENTION,
        "entry_count": len(entries),
        "entries": entries,
    }
    index["index_hash"] = sha256(index)
    return index


def plan_expiry(records: list[dict[str, Any]], *, observed_at: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    compiled = compile_policy() if policy is None else policy
    now = _timestamp(observed_at, "observed_at")
    planned = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("retention records must be objects")
        _assert_no_prohibited(record, compiled, str(record.get("record_id", "unknown")))
        _validate_governance_flags(record)
        record_id = record.get("record_id")
        source_id = record.get("source_id")
        layer = record.get("layer")
        if not all(isinstance(value, str) and value.strip() for value in (record_id, source_id, layer)):
            raise ValueError("retention record requires record_id/source_id/layer")
        if layer not in RETENTION_LAYERS:
            raise ValueError(f"unsupported retention layer: {layer}")
        if source_id not in compiled["source_policies"]:
            raise ValueError(f"unknown retention source: {source_id}")
        captured = _timestamp(record.get("captured_at"), "captured_at")
        content_sha256 = _hash(record.get("content_sha256"), "content_sha256")
        audit_refs = record.get("audit_refs", [])
        if not isinstance(audit_refs, list) or any(not isinstance(value, str) or not value.strip() for value in audit_refs):
            raise ValueError("audit_refs must be a string array")
        replay_required = _record_flag(record, "replay_required")
        source_available = _record_flag(record, "source_available")
        missing_replay_fields = sorted(
            field for field in compiled["replay_evidence_fields"] if field not in record
        ) if replay_required else []
        source_policy = compiled["source_policies"][source_id]
        retention_class = source_policy["raw_retention_class" if layer in RAW_LAYERS else "canonical_retention_class"]
        window = compiled["retention_windows"][retention_class]
        max_age_days = window["max_age_days"]
        expires_at = captured + timedelta(days=max_age_days) if max_age_days is not None else None
        expired = expires_at is not None and expires_at <= now
        action = "RETAIN"
        status = "RETAINED"
        if expired:
            status = "EXPIRED"
            action = window["expired_action"]
            if layer in CANONICAL_LAYERS:
                if not audit_refs:
                    status = "BLOCKED"
                    action = "BLOCKED_NO_AUDIT_LINKAGE"
                elif window["expired_action"] not in NON_OVERRIDABLE_EXPIRY_ACTIONS:
                    action = "KEEP_AUDIT_LINKAGE"
            elif action == "PURGE_RAW_KEEP_AUDIT" and not audit_refs:
                status = "BLOCKED"
                action = "BLOCKED_NO_AUDIT_LINKAGE"
        replay_status = "NOT_REQUIRED"
        replay_limitation = None
        overridden_expired_action = None
        if replay_required:
            if not audit_refs or missing_replay_fields:
                # Nothing is linked, so retention must not claim it is preserving replay.
                replay_status = "BLOCKED"
                replay_limitation = REPLAY_LIMITATION_NO_EVIDENCE
            elif not expired:
                replay_status = "PRESERVED"
            elif window["expired_action"] in NON_OVERRIDABLE_EXPIRY_ACTIONS:
                # A window that still demands human review keeps demanding it, so the
                # evidence is retained but replay is pending that review, not preserved.
                replay_status = "LIMITED"
                replay_limitation = REPLAY_REVIEW_WINDOW_PENDING
            else:
                # #36 replay must never lose evidence to a silent retention sweep.
                if action != "KEEP_AUDIT_LINKAGE":
                    overridden_expired_action = action
                    action = "KEEP_AUDIT_LINKAGE"
                if source_available:
                    replay_status = "PRESERVED"
                else:
                    replay_status = "LIMITED"
                    replay_limitation = REPLAY_LIMITATION_SOURCE_UNAVAILABLE
        planned.append({
            "record_id": record_id,
            "source_id": source_id,
            "layer": layer,
            "retention_class": retention_class,
            "rights_status": source_policy["rights_status"],
            "captured_at": captured.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
            "status": status,
            "action": action,
            "content_sha256": content_sha256,
            "audit_refs": list(audit_refs),
            "replay_required": replay_required,
            "replay_status": replay_status,
            "replay_limitation": replay_limitation,
            "replay_missing_fields": missing_replay_fields,
            "overridden_expired_action": overridden_expired_action,
            "source_available": source_available,
        })
    receipt = {
        "schema_version": 1,
        "receipt_type": "RETENTION_EXPIRY_DRY_RUN",
        "dry_run": True,
        "observed_at": now.isoformat(),
        "policy_version": compiled["policy_version"],
        "policy_hash": compiled["policy_hash"],
        "replay_evidence_fields": compiled["replay_evidence_fields"],
        "records": planned,
        "counts": {
            "total": len(planned),
            "retained": sum(row["status"] == "RETAINED" for row in planned),
            "expired": sum(row["status"] == "EXPIRED" for row in planned),
            "blocked": sum(row["status"] == "BLOCKED" for row in planned),
            "replay_preserved": sum(row["replay_status"] == "PRESERVED" for row in planned),
            "replay_limited": sum(row["replay_status"] == "LIMITED" for row in planned),
            "replay_blocked": sum(row["replay_status"] == "BLOCKED" for row in planned),
        },
    }
    if any(row["replay_status"] not in REPLAY_STATUSES for row in planned):
        raise ValueError("planner emitted an undocumented replay status")
    if any(row["replay_limitation"] is not None
           and row["replay_limitation"] not in compiled["replay_limitation_reasons"] for row in planned):
        raise ValueError("planner emitted an undocumented replay limitation")
    # A row may never claim replay is preserved while also declaring a limitation, and a
    # limitation may never be attached to a record that does not require replay.
    for row in planned:
        if row["replay_status"] == "PRESERVED" and row["replay_limitation"] is not None:
            raise ValueError(f"preserved replay cannot carry a limitation: {row['record_id']}")
        if row["replay_limitation"] is not None and not row["replay_required"]:
            raise ValueError(f"replay limitation on a record that does not require replay: {row['record_id']}")
    receipt["receipt_sha256"] = sha256(receipt)
    return receipt


def _json_bool(value: Any) -> str:
    """Receipt lines are machine-readable, so booleans print as JSON true/false."""
    return "true" if value else "false"


def policy_binding(compiled: dict[str, Any] | None = None) -> dict[str, Any]:
    """The minimum retention facts any outward surface may advertise.

    Derived from the compiled policy so a stale hand-written binding cannot be served,
    and conservative by construction: it reports the rights status and terms status so
    an UNKNOWN permission can never be rendered as an open one.
    """
    compiled = compile_policy() if compiled is None else compiled
    rights_statuses = {value["rights_status"] for value in compiled["classes"].values()}
    terms_statuses = {value["terms_status"] for value in compiled["classes"].values()}
    # A public surface must quote the most conservative class in the policy, never the
    # most permissive one, so an unverified permission can never render as an open one.
    return {
        "schema_version": 1,
        "policy_version": compiled["policy_version"],
        "policy_hash": compiled["policy_hash"],
        "catalog_hash": compiled["catalog_hash"],
        "public_projection": compiled["layer_policies"][QUERY_INDEX_LAYER]["public_projection"],
        "full_text_allowed": all(value["full_text_allowed"] for value in compiled["classes"].values()),
        "excerpt_allowed": all(value["excerpt_allowed"] for value in compiled["classes"].values()),
        "rights_status": UNKNOWN_RIGHTS if UNKNOWN_RIGHTS in rights_statuses else sorted(rights_statuses)[0],
        "rights_status_values": sorted(rights_statuses),
        "terms_status": (
            UNKNOWN_RIGHTS_TERMS_STATUS if UNKNOWN_RIGHTS_TERMS_STATUS in terms_statuses
            else sorted(terms_statuses)[0]
        ),
        "terms_status_values": sorted(terms_statuses),
        "review_required": any(value["review_required"] for value in compiled["classes"].values()),
        "query_index_extends_source_retention": QUERY_INDEX_EXTENDS_SOURCE_RETENTION,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--binding", action="store_true",
                        help="emit the smallest retention binding a public surface may advertise")
    parser.add_argument("--plan", type=Path, help="JSON retention manifest for a read-only expiry dry-run")
    parser.add_argument("--project", type=Path, help="JSON manifest of records to project into the public query index")
    parser.add_argument("--at", help="Timezone-aware observation time for --plan/--project")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    compiled = compile_policy()
    if args.plan:
        payload = load_json(args.plan)
        records = payload.get("records")
        if not isinstance(records, list):
            raise ValueError("retention plan input must contain a records array")
        receipt = plan_expiry(records, observed_at=args.at or datetime.now(timezone.utc).isoformat(), policy=compiled)
        text = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        print(f"RETENTION_DRY_RUN_OK total={receipt['counts']['total']} expired={receipt['counts']['expired']} blocked={receipt['counts']['blocked']}")
        return 0
    if args.project:
        payload = load_json(args.project)
        records = payload.get("records")
        if not isinstance(records, list):
            raise ValueError("retention projection input must contain a records array")
        index = project_query_index(
            records,
            observed_at=args.at or datetime.now(timezone.utc).isoformat(),
            policy=compiled,
        )
        text = json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
        print(
            "RETENTION_PROJECTION_OK "
            f"entries={index['entry_count']} projection_only={index['projection_only']} "
            f"extends_source_retention={index['extends_source_retention']}"
        )
        return 0
    if args.self_check and (args.json or args.binding):
        raise ValueError("--self-check cannot be combined with --json or --binding; pick one mode")
    if args.json:
        print(json.dumps(compiled, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.binding:
        print(json.dumps(policy_binding(compiled), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "RETENTION_POLICY_SELF_CHECK_OK "
            f"version={compiled['policy_version']} "
            f"policy_hash={compiled['policy_hash'][:12]} sources={len(compiled['source_policies'])} "
            f"data_types={len(compiled['data_types'])} layers={len(compiled['layer_policies'])} "
            f"full_text_allowed={_json_bool(policy_binding(compiled)['full_text_allowed'])} "
            f"rights_status={policy_binding(compiled)['rights_status']} "
            f"terms_status={policy_binding(compiled)['terms_status']} "
            "purge_executed=false dry_run=true"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
