#!/usr/bin/env python3
"""Compile conservative rights/retention policy for public projections."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "govintel" / "source-catalog.v2.json"
POLICY = ROOT / "docs" / "govintel" / "retention-rights-policy.v1.json"
PUBLIC_PROJECTIONS = {"LINK_ONLY", "METADATA_LINK_ONLY", "EVIDENCE_BOUND_SUMMARY_ONLY"}
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


def compile_policy(catalog: dict[str, Any] | None = None, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog = load_json(CATALOG) if catalog is None else catalog
    policy = load_json(POLICY) if policy is None else policy
    if catalog.get("schema_version") != 2 or not isinstance(catalog.get("sources"), list):
        raise ValueError("source catalog schema is unsupported")
    if policy.get("schema_version") != 1 or not isinstance(policy.get("policy_version"), int):
        raise ValueError("retention policy schema is unsupported")
    classes = policy.get("classes")
    source_classes = policy.get("source_classes")
    prohibited = policy.get("prohibited_public_fields")
    if not isinstance(classes, dict) or not isinstance(source_classes, dict) or not isinstance(prohibited, list):
        raise ValueError("retention policy sections are invalid")
    catalog_ids = {row.get("source_id") for row in catalog["sources"] if isinstance(row, dict)}
    if None in catalog_ids or set(source_classes) != catalog_ids:
        raise ValueError("every catalog source must have exactly one retention class")
    if len(prohibited) != len(set(prohibited)) or any(not isinstance(field, str) for field in prohibited):
        raise ValueError("prohibited public fields must be unique strings")

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
        compiled_classes[class_id] = dict(value)

    if any(class_id not in compiled_classes for class_id in source_classes.values()):
        raise ValueError("source references unknown retention class")
    material = {
        "schema_version": policy["schema_version"],
        "policy_version": policy["policy_version"],
        "updated_at": policy.get("updated_at"),
        "classes": compiled_classes,
        "source_classes": dict(sorted(source_classes.items())),
        "prohibited_public_fields": sorted(prohibited),
    }
    return {
        **material,
        "policy_hash": sha256(material),
        "catalog_hash": sha256(catalog),
        "source_policies": {
            source_id: {"class_id": class_id, **compiled_classes[class_id]}
            for source_id, class_id in sorted(source_classes.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    compiled = compile_policy()
    if args.json:
        print(json.dumps(compiled, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(
            "RETENTION_POLICY_SELF_CHECK_OK "
            f"version={compiled['policy_version']} "
            f"policy_hash={compiled['policy_hash'][:12]} sources={len(compiled['source_policies'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
