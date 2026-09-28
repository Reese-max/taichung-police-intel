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
RETENTION_LAYERS = {"raw_snapshot", "normalized_document", "canonical_event", "publication", "query_index"}
RAW_LAYERS = {"raw_snapshot", "normalized_document"}
CANONICAL_LAYERS = RETENTION_LAYERS - RAW_LAYERS
EXPIRY_ACTIONS = {"REVIEW_REQUIRED", "PURGE_RAW_KEEP_AUDIT", "KEEP_AUDIT_LINKAGE", "KEEP_AUDIT_RECEIPT"}
HASH_CHARS = set("0123456789abcdef")
REQUIRED_CLASS_FIELDS = {
    "rights_status", "public_projection", "full_text_allowed", "excerpt_allowed",
    "public_fields",
    "raw_retention_class", "canonical_retention_class", "review_required", "withdrawal_behavior",
}


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


def compile_policy(catalog: dict[str, Any] | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = load_json(CATALOG) if catalog is None else catalog
    policy = load_json(POLICY) if policy is None else policy
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
    catalog_ids = {row.get("source_id") for row in catalog["sources"] if isinstance(row, dict)}
    if None in catalog_ids or set(source_classes) != catalog_ids:
        raise ValueError("every catalog source must have exactly one retention class")
    if len(prohibited) != len(set(prohibited)) or any(not isinstance(field, str) for field in prohibited):
        raise ValueError("prohibited public fields must be unique strings")
    for window_id, window in retention_windows.items():
        if not isinstance(window, dict) or set(window) != {"max_age_days", "expired_action"}:
            raise ValueError(f"retention window is invalid: {window_id}")
        if window["max_age_days"] is not None and (type(window["max_age_days"]) is not int or window["max_age_days"] < 0):
            raise ValueError(f"retention window days are invalid: {window_id}")
        if window["expired_action"] not in EXPIRY_ACTIONS:
            raise ValueError(f"retention window action is invalid: {window_id}")

    compiled_classes = {}
    for class_id, value in classes.items():
        if not isinstance(value, dict) or set(REQUIRED_CLASS_FIELDS) - set(value):
            raise ValueError(f"retention class is incomplete: {class_id}")
        if value["public_projection"] not in PUBLIC_PROJECTIONS:
            raise ValueError(f"unsupported public projection: {class_id}")
        if (not isinstance(value["public_fields"], list) or
                len(value["public_fields"]) != len(set(value["public_fields"])) or
                any(not isinstance(field, str) for field in value["public_fields"])):
            raise ValueError(f"public fields are invalid: {class_id}")
        if set(value["public_fields"]) & set(prohibited):
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
    material = {
        "schema_version": policy["schema_version"],
        "policy_version": policy["policy_version"],
        "updated_at": policy.get("updated_at"),
        "retention_windows": dict(sorted(retention_windows.items())),
        "classes": compiled_classes,
        "source_classes": dict(sorted(source_classes.items())),
        "prohibited_public_fields": sorted(prohibited),
    }
    return {
        **material,
        "policy_hash": sha256(material),
        "catalog_hash": sha256(catalog),
        "retention_windows": dict(sorted(retention_windows.items())),
        "source_policies": {
            source_id: {"class_id": class_id, **compiled_classes[class_id]}
            for source_id, class_id in sorted(source_classes.items())
        },
    }


def plan_expiry(records: list[dict[str, Any]], *, observed_at: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    compiled = compile_policy() if policy is None else policy
    now = _timestamp(observed_at, "observed_at")
    planned = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("retention records must be objects")
        if set(record) & set(compiled["prohibited_public_fields"]):
            raise ValueError(f"retention record contains prohibited field: {record.get('record_id', 'unknown')}")
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
                else:
                    action = "KEEP_AUDIT_LINKAGE"
            elif action == "PURGE_RAW_KEEP_AUDIT" and not audit_refs:
                status = "BLOCKED"
                action = "BLOCKED_NO_AUDIT_LINKAGE"
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
        })
    receipt = {
        "schema_version": 1,
        "receipt_type": "RETENTION_EXPIRY_DRY_RUN",
        "dry_run": True,
        "observed_at": now.isoformat(),
        "policy_version": compiled["policy_version"],
        "policy_hash": compiled["policy_hash"],
        "records": planned,
        "counts": {
            "total": len(planned),
            "retained": sum(row["status"] == "RETAINED" for row in planned),
            "expired": sum(row["status"] == "EXPIRED" for row in planned),
            "blocked": sum(row["status"] == "BLOCKED" for row in planned),
        },
    }
    receipt["receipt_sha256"] = sha256(receipt)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--plan", type=Path, help="JSON retention manifest for a read-only expiry dry-run")
    parser.add_argument("--at", help="Timezone-aware observation time for --plan")
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
    if args.json:
        print(json.dumps(compiled, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "RETENTION_POLICY_SELF_CHECK_OK "
            f"version={compiled['policy_version']} "
            f"policy_hash={compiled['policy_hash'][:12]} sources={len(compiled['source_policies'])} dry_run=true audit_preserved=true"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
