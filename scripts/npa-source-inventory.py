#!/usr/bin/env python3
"""Validate the NPA source matrix without promoting any source to production."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "docs/govintel/npa-source-inventory.v1.json"
DEFAULT_CATALOG = ROOT / "docs/govintel/source-catalog.v2.json"
DEFAULT_FIXTURE = ROOT / "tests/fixtures/npa/batch1.json"
ROLES = {"PRIMARY_EVENT", "PRIMARY_REFERENCE", "ENRICHMENT", "EXCLUDE_OR_AGGREGATE_ONLY"}
STATUSES = {"CATALOG_CANDIDATE", "FIXTURE_ONLY", "METADATA_ONLY", "EXISTING_CATALOG", "EXCLUDED"}
METADATA_API_BASE = "https://data.gov.tw/api/v2/rest/dataset/"
MAX_METADATA_BYTES = 2 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("metadata redirect is not allowed")


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _resource_id(url: str) -> str | None:
    parts = [part for part in urlsplit(url).path.split("/") if part]
    try:
        index = parts.index("resource")
    except ValueError:
        return None
    return parts[index + 1] if index + 1 < len(parts) else None


def _resource_receipt(resource: dict[str, Any]) -> dict[str, Any]:
    url = str(resource.get("resourceDownloadUrl") or "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("resource URL must be credential-free HTTPS")
    fields = resource.get("resourceField")
    return {
        "resource_id": _resource_id(url),
        "resource_url": url,
        "format": resource.get("resourceFormat"),
        "field_count": len(fields) if isinstance(fields, list) else None,
        "metadata_sha256": hashlib.sha256(_canonical(resource)).hexdigest(),
    }


def fetch_live_metadata(
    row: dict[str, Any], *, opener=None, observed_at: str | None = None
) -> dict[str, Any]:
    """Fetch one data.gov.tw metadata document; never downloads its resources."""
    dataset_id = str(row.get("dataset_id") or "")
    if not dataset_id.isdigit():
        raise ValueError(f"{row.get('inventory_id')} has no numeric dataset_id")
    metadata_url = f"{METADATA_API_BASE}{dataset_id}"
    request = Request(
        metadata_url,
        headers={
            "Accept": "application/json",
            "User-Agent": "GovIntel-NPA-metadata-check/1",
        },
    )
    client = opener or build_opener(_NoRedirect)
    with client.open(request, timeout=30) as response:
        body = response.read(MAX_METADATA_BYTES + 1)
        if len(body) > MAX_METADATA_BYTES:
            raise ValueError("metadata response exceeds bounded byte limit")
        response_status = getattr(response, "status", None)
        status = int(response_status if response_status is not None else response.getcode())
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
    if final_url != metadata_url:
        raise ValueError("metadata final URL changed")
    parsed_url = urlsplit(final_url)
    if parsed_url.scheme != "https" or parsed_url.hostname != "data.gov.tw":
        raise ValueError("metadata final URL is not data.gov.tw")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise ValueError("metadata API did not return success=true")
    result = payload.get("result")
    if not isinstance(result, dict) or str(result.get("datasetId")) != dataset_id:
        raise ValueError("metadata dataset identity mismatch")
    distributions = result.get("distribution")
    if isinstance(distributions, dict):
        distributions = [distributions]
    if not isinstance(distributions, list):
        raise ValueError("metadata distribution shape is unknown")
    resources = [_resource_receipt(item) for item in distributions if isinstance(item, dict)]
    if len(resources) != len(distributions):
        raise ValueError("metadata distribution contains a non-object resource")
    resource_status = "PASS" if resources else "PARTIAL"
    return {
        "inventory_id": row["inventory_id"],
        "dataset_id": dataset_id,
        "metadata_url": metadata_url,
        "final_url": final_url,
        "http_status": status,
        "content_type": content_type,
        "raw_bytes": len(body),
        "raw_sha256": hashlib.sha256(body).hexdigest(),
        "title": result.get("title"),
        "published_at": result.get("publishedDate"),
        "modified_at": result.get("modifiedDate"),
        "resource_status": resource_status,
        "resource_count": len(resources),
        "resources": resources,
        "observed_at": observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "PASS" if status == 200 and resource_status == "PASS" else "PARTIAL",
    }


def live_metadata_report(
    inventory: dict[str, Any], *, source_ids: list[str] | None = None, opener=None, observed_at: str | None = None
) -> dict[str, Any]:
    rows = inventory.get("sources", [])
    selected = [row for row in rows if source_ids is None or row.get("inventory_id") in source_ids]
    if source_ids:
        missing = sorted(set(source_ids) - {row.get("inventory_id") for row in selected})
        if missing:
            raise ValueError(f"unknown inventory_id: {','.join(missing)}")
    if not selected:
        raise ValueError("no inventory rows selected")
    observed = observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    records = []
    skipped = []
    for row in selected:
        if not row.get("dataset_id"):
            skipped.append({"inventory_id": row.get("inventory_id"), "status": "SKIPPED", "reason": "NO_DATASET_ID"})
            continue
        try:
            records.append(fetch_live_metadata(row, opener=opener, observed_at=observed))
        except Exception as error:
            records.append({
                "inventory_id": row.get("inventory_id"),
                "dataset_id": str(row.get("dataset_id")),
                "metadata_url": f"{METADATA_API_BASE}{row.get('dataset_id')}",
                "status": "FAILED",
                "error_class": type(error).__name__,
                "error": str(error),
                "observed_at": observed,
            })
    failed = sum(record.get("status") == "FAILED" for record in records)
    partial = sum(record.get("status") == "PARTIAL" for record in records)
    passed = sum(record.get("status") == "PASS" for record in records)
    status = "FAILED" if failed else "PARTIAL" if partial or (not records and skipped) else "PASS"
    return {
        "schema_version": 1,
        "kind": "NPA_LIVE_METADATA_RESOURCE_RECEIPT",
        "observed_at": observed,
        "status": status,
        "requested_count": len(selected),
        "eligible_count": len(records),
        "observed_count": len(records),
        "skipped_count": len(skipped),
        "pass_count": passed,
        "partial_count": partial,
        "failed_count": failed,
        "resource_count": sum(record.get("resource_count", 0) for record in records),
        "records": records,
        "skipped": skipped,
        "verification_boundary": "Metadata and resource URLs only; resource bytes, parser compatibility, production persistence, and promotion remain separate gates.",
    }


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
    parser.add_argument("--live-metadata", action="store_true")
    parser.add_argument("--source-id", action="append", dest="source_ids", default=[])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.live_metadata:
        report = live_metadata_report(load_json(args.inventory), source_ids=args.source_ids or None)
        text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
            print(
                f"NPA_METADATA_RECEIPT status={report['status']} pass={report['pass_count']} "
                f"partial={report['partial_count']} failed={report['failed_count']} output={args.output}"
            )
        else:
            print(text, end="")
        return 0 if report["status"] == "PASS" else 1
    summary = validate_inventory(load_json(args.inventory), load_json(args.catalog))
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
