from __future__ import annotations

from typing import Any


def _matches_filters(entry: dict[str, Any], filters: dict[str, Any]) -> bool:
    fields = _extract_filterable_fields(entry)
    for key, value in filters.items():
        if value is None:
            continue
        if key == "q":
            if not _matches_query(entry, str(value)):
                return False
            continue
        field_value = fields.get(key)
        if isinstance(field_value, list):
            if str(value) not in [str(v) for v in field_value]:
                return False
        elif str(field_value) != str(value):
            return False
    return True


def _matches_query(entry: dict[str, Any], query: str) -> bool:
    query_lower = query.lower()
    for key in ("title", "canonical_id", "source_id", "agency", "category"):
        value = str(entry.get(key, ""))
        if query_lower in value.lower():
            return True
    return False


def _extract_filterable_fields(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "canonical_id": entry.get("canonical_id", ""),
        "source_id": entry.get("source_id", ""),
        "title": entry.get("title", ""),
        "region": entry.get("region", ""),
        "agency": entry.get("agency", ""),
        "category": entry.get("category", ""),
        "status": entry.get("status", ""),
        "trust_tier": entry.get("trust_tier", ""),
        "evidence_ids": entry.get("evidence_ids", []),
        "detected_at": entry.get("detected_at", ""),
    }
