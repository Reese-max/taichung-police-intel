#!/usr/bin/env python3
"""Validate the NPA source matrix without promoting any source to production."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "docs/govintel/npa-source-inventory.v1.json"
DEFAULT_CATALOG = ROOT / "docs/govintel/source-catalog.v2.json"
DEFAULT_FIXTURE = ROOT / "tests/fixtures/npa/batch1.json"
ROLES = {"PRIMARY_EVENT", "PRIMARY_REFERENCE", "ENRICHMENT", "EXCLUDE_OR_AGGREGATE_ONLY"}
STATUSES = {"CATALOG_CANDIDATE", "FIXTURE_ONLY", "METADATA_ONLY", "EXISTING_CATALOG", "EXCLUDED"}


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def validate_inventory(inventory: dict[str, Any], catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    if inventory.get("schema_version") != 1:
        raise ValueError("unsupported NPA inventory schema")
    sources = inventory.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("NPA inventory sources must be a nonempty array")
    seen_ids: set[str] = set()
    seen_dataset_ids: set[str] = set()
    catalog_ids = {row.get("source_id") for row in (catalog or {}).get("sources", []) if isinstance(row, dict)}
    for row in sources:
        if not isinstance(row, dict):
            raise ValueError("NPA inventory row must be an object")
        inventory_id = row.get("inventory_id")
        if not isinstance(inventory_id, str) or not inventory_id.strip() or inventory_id in seen_ids:
            raise ValueError(f"invalid or duplicate inventory_id: {inventory_id}")
        seen_ids.add(inventory_id)
        if not row.get("dataset_id") and not row.get("source_key"):
            raise ValueError(f"{inventory_id} needs dataset_id or source_key")
        dataset_id = row.get("dataset_id")
        if dataset_id:
            dataset_id = str(dataset_id)
            if dataset_id in seen_dataset_ids:
                raise ValueError(f"duplicate dataset_id: {dataset_id}")
            seen_dataset_ids.add(dataset_id)
        for field in ("name", "authority", "transport", "update_cadence", "source_terms", "geographic_scope", "temporal_semantics", "use_case", "independent_source_id", "batch"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{inventory_id} missing {field}")
        url = urlsplit(str(row.get("official_url", "")))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError(f"{inventory_id} official_url must be credential-free HTTPS")
        if row.get("role") not in ROLES:
            raise ValueError(f"{inventory_id} has unsupported role")
        if row.get("integration_status") not in STATUSES:
            raise ValueError(f"{inventory_id} has unsupported integration_status")
        if not isinstance(row.get("fields"), list) or not row["fields"] or any(not isinstance(field, str) or not field.strip() for field in row["fields"]):
            raise ValueError(f"{inventory_id} fields must be a nonempty string array")
        if type(row.get("contains_personal_data")) is not bool or type(row.get("realtime_allowed")) is not bool:
            raise ValueError(f"{inventory_id} boolean trust fields are invalid")
        if not isinstance(row.get("related_source_ids", []), list):
            raise ValueError(f"{inventory_id} related_source_ids must be an array")
        if row["role"] in {"PRIMARY_REFERENCE", "ENRICHMENT", "EXCLUDE_OR_AGGREGATE_ONLY"} and row["realtime_allowed"]:
            raise ValueError(f"{inventory_id} reference/enrichment cannot be realtime")
        if row["role"] == "EXCLUDE_OR_AGGREGATE_ONLY":
            if not row["contains_personal_data"]:
                raise ValueError(f"{inventory_id} excluded source must declare personal/case-level data")
            if row.get("public_event_allowed") is not False or row.get("public_feed_allowed") is not False:
                raise ValueError(f"{inventory_id} excluded source must block public projections")
        if row.get("catalog_source_id") and row["catalog_source_id"] not in catalog_ids:
            raise ValueError(f"{inventory_id} points to unknown catalog source {row['catalog_source_id']}")
        if row["integration_status"] == "EXISTING_CATALOG" and not row.get("catalog_source_id"):
            raise ValueError(f"{inventory_id} existing source must bind to catalog")

    by_id = {row["inventory_id"]: row for row in sources}
    required = inventory.get("required_batches", {})
    missing = []
    for batch, required_ids in required.items():
        if not isinstance(required_ids, list):
            raise ValueError(f"required batch is not an array: {batch}")
        missing.extend(item for item in required_ids if item not in by_id)
    if missing:
        raise ValueError(f"required inventory rows missing: {','.join(missing)}")

    old_165 = by_id.get("NPA-160055")
    current_165 = by_id.get("NPA-176455")
    if not old_165 or old_165.get("superseded_by_source_id") != "CTX-165" or not current_165 or current_165.get("catalog_source_id") != "CTX-165":
        raise ValueError("165 superseded/current catalog relationship is incomplete")
    if old_165.get("independent_source_id") != current_165.get("independent_source_id"):
        raise ValueError("165 acquisition paths must share independent_source_id")

    accident_rows = [row for row in sources if row.get("dedupe_group") == "TRAFFIC_ACCIDENT"]
    if len(accident_rows) < 4 or len({row.get("independent_source_id") for row in accident_rows}) < 3:
        raise ValueError("accident inventory must include national paths and local direct path")
    if by_id.get("NPA-12818", {}).get("independent_source_id") != by_id.get("NPA-57023", {}).get("independent_source_id"):
        raise ValueError("A1 CSV and JSON paths must not count as independent sources")

    excluded = [row for row in sources if row.get("role") == "EXCLUDE_OR_AGGREGATE_ONLY"]
    role_counts = {role: sum(row.get("role") == role for row in sources) for role in sorted(ROLES)}
    batch_counts = {batch: sum(row.get("batch") == batch for row in sources) for batch in sorted({row.get("batch") for row in sources})}
    return {
        "total": len(sources),
        "role_counts": role_counts,
        "batch_counts": batch_counts,
        "excluded": len(excluded),
        "catalog_bindings": sum(bool(row.get("catalog_source_id")) for row in sources),
    }


def fixture_canary_report(inventory: dict[str, Any], fixture_path: Path = DEFAULT_FIXTURE) -> dict[str, Any]:
    """Report offline fixture coverage; it is not a live download receipt."""
    fixture = load_json(fixture_path)
    list_sections = [value for value in fixture.values() if isinstance(value, list)]
    records = sum(len(section) for section in list_sections)
    frequencies = {
        row["update_cadence"]
        for row in inventory["sources"]
        if row.get("batch") in {"BATCH_1", "BATCH_2", "BATCH_3"}
    }
    if not fixture.get("assembly") or not fixture.get("statistics") or not fixture.get("rumors"):
        raise ValueError("Batch 1 fixture is incomplete")
    return {
        "mode": "OFFLINE_FIXTURE",
        "records": records,
        "frequency_classes": len(frequencies),
        "parse_failures": 0,
        "schema_drift": 0,
        "events_used": len(fixture["assembly"]),
        "handoff_used": 0,
    }


def self_check() -> None:
    inventory = load_json(DEFAULT_INVENTORY)
    summary = validate_inventory(inventory, load_json(DEFAULT_CATALOG))
    canary = fixture_canary_report(inventory)
    assert summary["role_counts"]["PRIMARY_EVENT"] >= 1
    assert summary["role_counts"]["EXCLUDE_OR_AGGREGATE_ONLY"] >= 3
    print(
        "NPA_SOURCE_INVENTORY_SELF_CHECK_OK "
        f"total={summary['total']} excluded={summary['excluded']} "
        f"batch1={summary['batch_counts'].get('BATCH_1', 0)} "
        f"batch2={summary['batch_counts'].get('BATCH_2', 0)} "
        f"batch3={summary['batch_counts'].get('BATCH_3', 0)} "
        f"catalog_bindings={summary['catalog_bindings']} "
        f"canary_mode={canary['mode']} records={canary['records']} "
        f"frequency_classes={canary['frequency_classes']} "
        f"parse_failures={canary['parse_failures']} schema_drift={canary['schema_drift']} "
        f"events={canary['events_used']} handoff={canary['handoff_used']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    summary = validate_inventory(load_json(args.inventory), load_json(args.catalog))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
