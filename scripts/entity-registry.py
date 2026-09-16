#!/usr/bin/env python3
"""Deterministic resolver for GovIntel non-person entities.

The resolver only confirms exact canonical/alias matches after normalization.  Fuzzy
matching can be surfaced separately as a candidate, but never mutates canonical IDs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import unicodedata
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY = ROOT / "config/entity-registry.v1.json"
ALLOWED_KINDS = {"agency", "location", "named_event"}


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


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_registry(data)
    return data


def validate_registry(registry: dict[str, Any]) -> None:
    if registry.get("schema_version") != 1:
        raise ValueError("unsupported entity registry schema_version")
    if not isinstance(registry.get("registry_version"), int) or registry["registry_version"] < 1:
        raise ValueError("registry_version must be a positive integer")
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
        names = [label, *aliases]
        for name in names:
            key = (kind, normalize(jurisdiction), normalize(name))
            previous = alias_keys.get(key)
            if previous and previous != entity_id:
                raise ValueError(f"alias collision: {name!r} maps to {previous} and {entity_id}")
            alias_keys[key] = entity_id


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
