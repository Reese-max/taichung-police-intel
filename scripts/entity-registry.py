#!/usr/bin/env python3
"""Deterministic resolver for GovIntel non-person entities.

The resolver only confirms exact canonical/alias matches after normalization.  Fuzzy
matching can be surfaced separately as a candidate, but never mutates canonical IDs.
Manual correction, merge, and split helpers return a new registry version with a
hash-bound audit record; the input registry is never mutated.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import unicodedata
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "config/entity-registry.v1.json"
ALLOWED_KINDS = {"agency", "location", "named_event"}
MANUAL_ACTIONS = {"CORRECTION", "MERGE", "SPLIT"}


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).strip()
    text = "".join(text.split())
    return text.replace("台", "臺")


def registry_hash(registry: dict[str, Any]) -> str:
    payload = json.dumps(registry, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def registry_receipt(registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "registry_version": registry["registry_version"],
        "registry_hash": registry_hash(registry),
    }


def _stamp(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("decided_at must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("decided_at must be a timezone-aware timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError("decided_at must be a timezone-aware timestamp")
    return parsed.isoformat(timespec="seconds")


def _audit_id(record: dict[str, Any]) -> str:
    material = {key: record[key] for key in ("sequence", "action", "operator", "at", "payload")}
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"ENTITY-AUDIT-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def _validate_audit_history(history: Any) -> None:
    if not isinstance(history, list):
        raise ValueError("audit_history must be an array")
    for sequence, record in enumerate(history, start=1):
        if not isinstance(record, dict) or record.get("sequence") != sequence:
            raise ValueError("invalid entity audit sequence")
        if record.get("action") not in MANUAL_ACTIONS:
            raise ValueError("invalid entity audit action")
        if not isinstance(record.get("operator"), str) or not record["operator"].strip():
            raise ValueError("entity audit operator is required")
        _stamp(record.get("at"))
        if not isinstance(record.get("payload"), dict):
            raise ValueError("entity audit payload must be an object")
        if record.get("audit_id") != _audit_id(record):
            raise ValueError("entity audit hash mismatch")


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_registry(data)
    return data


def validate_registry(registry: dict[str, Any]) -> None:
    if registry.get("schema_version") != 1:
        raise ValueError("unsupported entity registry schema_version")
    if not isinstance(registry.get("registry_version"), int) or registry["registry_version"] < 1:
        raise ValueError("registry_version must be a positive integer")
    _validate_audit_history(registry.get("audit_history", []))
    entities = registry.get("entities")
    if not isinstance(entities, list):
        raise ValueError("entities must be an array")
    ids: set[str] = set()
    alias_keys: dict[tuple[str, str, str], str] = {}
    for entity in entities:
        if not isinstance(entity, dict):
            raise ValueError("entity row must be an object")
        entity_id = entity.get("entity_id")
        kind = entity.get("kind")
        label = entity.get("canonical_label")
        jurisdiction = entity.get("jurisdiction") or ""
        status = entity.get("status")
        aliases = entity.get("aliases", [])
        if not isinstance(entity_id, str) or not entity_id:
            raise ValueError("entity_id missing")
        if entity_id in ids:
            raise ValueError(f"duplicate entity_id: {entity_id}")
        ids.add(entity_id)
        if kind not in ALLOWED_KINDS:
            raise ValueError(f"unsupported entity kind: {kind}")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"canonical_label missing: {entity_id}")
        if not isinstance(jurisdiction, str):
            raise ValueError(f"jurisdiction must be a string: {entity_id}")
        if status != "CONFIRMED":
            raise ValueError(f"unconfirmed entity cannot enter canonical registry: {entity_id}")
        if not isinstance(aliases, list):
            raise ValueError(f"aliases must be an array of strings: {entity_id}")
        if any(not isinstance(alias, str) or not alias.strip() for alias in aliases):
            raise ValueError(f"aliases must be an array of strings: {entity_id}")
        alias_evidence = entity.get("alias_evidence", [])
        if not isinstance(alias_evidence, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("alias"), str)
            or not item["alias"].strip()
            or not isinstance(item.get("evidence"), str)
            or not item["evidence"].strip()
            for item in alias_evidence
        ):
            raise ValueError(f"alias_evidence must contain alias/evidence objects: {entity_id}")
        names = [label, *aliases]
        for name in names:
            key = (kind, normalize(jurisdiction), normalize(name))
            previous = alias_keys.get(key)
            if previous and previous != entity_id:
                raise ValueError(f"alias collision: {name!r} maps to {previous} and {entity_id}")
            alias_keys[key] = entity_id


def _find_entity(registry: dict[str, Any], entity_id: str) -> dict[str, Any]:
    for entity in registry["entities"]:
        if entity["entity_id"] == entity_id:
            return entity
    raise ValueError(f"unknown entity_id: {entity_id}")


def _manual_change(
    registry: dict[str, Any],
    action: str,
    operator: str,
    decided_at: Any,
    before: dict[str, Any],
    after: dict[str, Any],
    evidence: str,
) -> dict[str, Any]:
    if action not in MANUAL_ACTIONS:
        raise ValueError(f"unsupported manual action: {action}")
    if not isinstance(operator, str) or not operator.strip():
        raise ValueError("operator is required")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("manual evidence is required")
    stamp = _stamp(decided_at)
    validate_registry(registry)
    result = copy.deepcopy(registry)
    record = {
        "sequence": len(result.get("audit_history", [])) + 1,
        "action": action,
        "operator": operator.strip(),
        "at": stamp,
        "payload": {
            "before": copy.deepcopy(before),
            "after": copy.deepcopy(after),
            "evidence": evidence.strip(),
        },
    }
    record["audit_id"] = _audit_id(record)
    result["registry_version"] += 1
    result["updated_at"] = stamp
    result["audit_history"] = [*result.get("audit_history", []), record]
    validate_registry(result)
    return result


def correct_alias(
    registry: dict[str, Any],
    entity_id: str,
    alias: str,
    *,
    evidence: str,
    operator: str,
    decided_at: Any,
) -> dict[str, Any]:
    entity = _find_entity(registry, entity_id)
    if not isinstance(alias, str) or not alias.strip():
        raise ValueError("alias is required")
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError("manual evidence is required")
    normalized = normalize(alias)
    if normalized in {normalize(entity["canonical_label"]), *(normalize(item) for item in entity["aliases"])}:
        raise ValueError("alias already maps to entity")
    result = copy.deepcopy(registry)
    updated = _find_entity(result, entity_id)
    before = {"entity": copy.deepcopy(entity)}
    updated["aliases"].append(alias.strip())
    updated.setdefault("alias_evidence", []).append({"alias": alias.strip(), "evidence": evidence.strip()})
    after = {"entity": copy.deepcopy(updated)}
    return _manual_change(result, "CORRECTION", operator, decided_at, before, after, evidence)


def merge_entities(
    registry: dict[str, Any],
    target_id: str,
    source_id: str,
    *,
    evidence: str,
    operator: str,
    decided_at: Any,
) -> dict[str, Any]:
    if target_id == source_id:
        raise ValueError("manual merge requires two distinct entity IDs")
    target = _find_entity(registry, target_id)
    source = _find_entity(registry, source_id)
    if (target["kind"], normalize(target.get("jurisdiction") or "")) != (
        source["kind"], normalize(source.get("jurisdiction") or "")
    ):
        raise ValueError("manual merge requires matching kind and jurisdiction")
    result = copy.deepcopy(registry)
    merged = _find_entity(result, target_id)
    before = {"target": copy.deepcopy(target), "source": copy.deepcopy(source)}
    names = [merged["canonical_label"], *merged["aliases"], source["canonical_label"], *source["aliases"]]
    merged["aliases"] = list(dict.fromkeys(names[1:]))
    merged["alias_evidence"] = [
        *copy.deepcopy(merged.get("alias_evidence", [])),
        *copy.deepcopy(source.get("alias_evidence", [])),
    ]
    result["entities"] = [item for item in result["entities"] if item["entity_id"] != source_id]
    after = {"target": copy.deepcopy(merged), "retired_entity": copy.deepcopy(source)}
    return _manual_change(result, "MERGE", operator, decided_at, before, after, evidence)


def split_entity(
    registry: dict[str, Any],
    entity_id: str,
    groups: list[dict[str, Any]],
    *,
    evidence: str,
    operator: str,
    decided_at: Any,
) -> dict[str, Any]:
    entity = _find_entity(registry, entity_id)
    if not isinstance(groups, list) or len(groups) < 2:
        raise ValueError("manual split requires at least two groups")
    old_names = [entity["canonical_label"], *entity["aliases"]]
    assigned: list[str] = []
    children = []
    existing_ids = {item["entity_id"] for item in registry["entities"]}
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("manual split groups must be objects")
        child_id = group.get("entity_id")
        label = group.get("canonical_label")
        aliases = group.get("aliases", [])
        if not isinstance(child_id, str) or not child_id or child_id in existing_ids:
            raise ValueError("manual split child IDs must be new and unique")
        if not isinstance(label, str) or not label.strip() or not isinstance(aliases, list):
            raise ValueError("manual split group requires label and aliases")
        names = [label.strip(), *aliases]
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("manual split names must be non-empty strings")
        assigned.extend(normalize(name) for name in names)
        child = copy.deepcopy(entity)
        child["entity_id"] = child_id
        child["canonical_label"] = label.strip()
        child["aliases"] = [name.strip() for name in aliases]
        child["alias_evidence"] = [
            item for item in copy.deepcopy(entity.get("alias_evidence", []))
            if normalize(item["alias"]) in {normalize(name) for name in names}
        ]
        children.append(child)
    if sorted(assigned) != sorted(normalize(name) for name in old_names) or len(assigned) != len(set(assigned)):
        raise ValueError("manual split groups must partition every original name")
    result = copy.deepcopy(registry)
    before = {"entity": copy.deepcopy(entity)}
    result["entities"] = [item for item in result["entities"] if item["entity_id"] != entity_id] + children
    after = {"children": copy.deepcopy(children), "retired_entity": copy.deepcopy(entity)}
    return _manual_change(result, "SPLIT", operator, decided_at, before, after, evidence)


def build_index(registry: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    validate_registry(registry)
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for entity in registry["entities"]:
        jurisdiction = normalize(entity.get("jurisdiction") or "")
        for name in [entity["canonical_label"], *entity.get("aliases", [])]:
            index[(entity["kind"], jurisdiction, normalize(name))] = entity
    return index


def no_match(registry: dict[str, Any], kind: str, text: str, jurisdiction: str | None) -> dict[str, Any]:
    return {
        "status": "NO_MATCH",
        "query": text,
        "kind": kind,
        "jurisdiction": jurisdiction,
        **registry_receipt(registry),
    }


def resolve(registry: dict[str, Any], kind: str, text: str, jurisdiction: str | None = None) -> dict[str, Any]:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported kind: {kind}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("query text must be a non-empty string")
    if jurisdiction is not None and (not isinstance(jurisdiction, str) or not jurisdiction.strip()):
        raise ValueError("jurisdiction must be a non-empty string when provided")
    query = normalize(text)
    jurisdiction_norm = normalize(jurisdiction or "")
    index = build_index(registry)

    if jurisdiction is not None:
        entity = index.get((kind, jurisdiction_norm, query))
        if entity:
            return _resolved(entity, registry)
        return no_match(registry, kind, text, jurisdiction)

    matches: dict[str, dict[str, Any]] = {}
    for (entry_kind, _entry_jurisdiction, alias), entity in index.items():
        if entry_kind == kind and alias == query:
            matches[entity["entity_id"]] = entity
    if len(matches) == 1:
        return _resolved(next(iter(matches.values())), registry)
    if len(matches) > 1:
        return {
            "status": "AMBIGUOUS",
            "query": text,
            "kind": kind,
            "candidate_ids": sorted(matches),
            **registry_receipt(registry),
        }
    return no_match(registry, kind, text, jurisdiction)


def _resolved(entity: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "RESOLVED",
        "entity_id": entity["entity_id"],
        "kind": entity["kind"],
        "canonical_label": entity["canonical_label"],
        "jurisdiction": entity.get("jurisdiction"),
        **registry_receipt(registry),
        "match_method": "EXACT_NORMALIZED_ALIAS",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--kind", choices=sorted(ALLOWED_KINDS))
    parser.add_argument("--text")
    parser.add_argument("--jurisdiction")
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def self_check(registry: dict[str, Any]) -> None:
    expected = {
        "臺中市政府警察局": "agency:tc-police",
        "台中市警局": "agency:tc-police",
        "中市警": "agency:tc-police",
    }
    for name, entity_id in expected.items():
        result = resolve(registry, "agency", name, "臺中市")
        assert result["status"] == "RESOLVED" and result["entity_id"] == entity_id
    road = resolve(registry, "location", "台灣大道", "台中市")
    assert road["entity_id"] == "location:tc-taiwan-blvd"
    unknown = resolve(registry, "location", "不存在的地點", "臺中市")
    assert unknown["status"] == "NO_MATCH"
    assert unknown["registry_version"] == registry["registry_version"]
    assert unknown["registry_hash"] == registry_hash(registry)
    print(
        "ENTITY_REGISTRY_SELF_CHECK_OK "
        f"version={registry['registry_version']} hash={registry_hash(registry)[:12]} entities={len(registry['entities'])}"
    )


def main() -> int:
    args = parse_args()
    registry = load_registry(args.registry)
    if args.self_check:
        self_check(registry)
        return 0
    if not args.kind or not args.text:
        raise SystemExit("--kind and --text are required unless --self-check is used")
    print(json.dumps(resolve(registry, args.kind, args.text, args.jurisdiction), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
