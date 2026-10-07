#!/usr/bin/env python3
"""Validate a local CSV against a supplied evidence plan. Never fetches."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,64}$")
NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

SOURCE_ALIASES = {
    "S-035": "S-035",
    "S035": "S-035",
    "D1": "S-035",
    "S-037": "S-037",
    "S037": "S-037",
    "D2": "S-037",
    "CTX-POP": "CTX-POP",
    "S-028": "S-028",
    "S028": "S-028",
    "S-034": "S-034",
    "S034": "S-034",
    "S-036": "S-036",
    "S036": "S-036",
    "CTX-165": "CTX-165",
}
SUPPORTED_SOURCE_IDS = frozenset(SOURCE_ALIASES)
CANONICAL_SOURCE_IDS = frozenset(SOURCE_ALIASES.values())

SUPPORTED_EPSG = {
    4326: {"units": "degree", "axis_orders": frozenset({"lon-lat", "lat-lon"})},
    3825: {"units": "metre", "axis_orders": frozenset({"east-north", "north-east"})},
    3826: {"units": "metre", "axis_orders": frozenset({"east-north", "north-east"})},
    3828: {"units": "metre", "axis_orders": frozenset({"east-north", "north-east"})},
}

PLAN_KEYS = frozenset({
    "schema_version",
    "source_id",
    "fixture_kind",
    "declared_sha256",
    "schema",
    "id_fields",
    "expected_row_count",
    "period_evidence",
    "numeric_fields",
    "coordinate_fields",
    "crs",
    "metadata",
    "acquisition",
    "max_bytes",
    "coordinate_validation_requested",
})
IGNORED_PLAN_KEYS = frozenset({
    "notes",
    "completeness",
    "promotion",
    "rights_decision",
    "production_active",
    "completeness_claim",
    "description",
    "comment",
    "comments",
    "narrative",
    "crs_notes",
})
ACQUISITION_STATUSES = frozenset({"BLOCKED", "LOCAL_BYTES", "NOT_ATTEMPTED"})
POINT_NAMES = frozenset({"POINT_X", "POINT_Y"})


def sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_sha256(value: Any) -> str:
    return sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    )


def canonicalize_source_id(source_id: str) -> str:
    if not isinstance(source_id, str) or source_id not in SOURCE_ALIASES:
        allowed = ", ".join(sorted(SUPPORTED_SOURCE_IDS))
        raise ValueError(f"source_id must be one of: {allowed}")
    return SOURCE_ALIASES[source_id]


def require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def require_int(value: Any, name: str, *, allow_null: bool = False, minimum: int | None = None) -> int | None:
    if value is None:
        if allow_null:
            return None
        raise ValueError(f"{name} must be an integer")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def require_sha256(value: Any, name: str, *, allow_null: bool = False) -> str | None:
    if value is None:
        if allow_null:
            return None
        raise ValueError(f"{name} must be a SHA256 hex digest")
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a SHA256 hex digest")
    return value


def require_string_list(value: Any, name: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item != "" and item.strip() == item for item in value):
        raise ValueError(f"{name} must be a list of nonempty strings without surrounding whitespace")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    if len(set(value)) != len(value):
        raise ValueError(f"{name} must not contain duplicates")
    return value


def looks_like_non_csv_payload(body: bytes) -> bool:
    head = body.lstrip().lower()
    if head.startswith((b"[", b"{", b"PK\x03\x04")):
        return True
    if head.startswith((b"<!doctype", b"<html", b"<head", b"<body")):
        return True
    return head.startswith(b"<") and b"<html" in head[:1024]


def load_plan(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        raw = handle.read(DEFAULT_MAX_BYTES + 1)
    if len(raw) > DEFAULT_MAX_BYTES:
        raise ValueError("plan exceeds bounded byte limit")
    try:
        plan = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("plan must be UTF-8 JSON") from exc
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    return plan


def _period_evidence(plan: dict[str, Any], *, required: bool) -> dict[str, str] | None:
    value = plan.get("period_evidence")
    if value is None:
        if required:
            raise ValueError("period_evidence is required when original bytes are supplied")
        return None
    if not isinstance(value, dict):
        raise ValueError("period_evidence must be an object")
    extra = set(value) - {"field", "value"}
    if extra:
        raise ValueError(f"period_evidence has unsupported keys: {sorted(extra)}")
    field = value.get("field")
    period = value.get("value")
    if not isinstance(field, str) or not field or field.strip() != field:
        raise ValueError("period_evidence.field must be a nonempty string")
    if not isinstance(period, str) or not period or period.strip() != period:
        raise ValueError("period_evidence.value must be a nonempty string")
    return {"field": field, "value": period}


def _crs_declaration(crs: Any) -> dict[str, Any]:
    if crs is None:
        return {"declared": False, "epsg": None, "units": None, "axis_order": None, "reasons": []}
    if not isinstance(crs, dict):
        raise ValueError("crs must be an object")
    extra = set(crs) - {"epsg", "units", "axis_order", "authority", "status", "notes"}
    if extra:
        raise ValueError(f"crs has unsupported keys: {sorted(extra)}")
    epsg = crs.get("epsg")
    units = crs.get("units")
    axis_order = crs.get("axis_order")
    authority = crs.get("authority")
    if epsg is None and units is None and axis_order is None and authority is None:
        return {"declared": False, "epsg": None, "units": None, "axis_order": None, "reasons": []}
    reasons: list[str] = []
    valid = (
        isinstance(epsg, int)
        and not isinstance(epsg, bool)
        and epsg in SUPPORTED_EPSG
        and units == SUPPORTED_EPSG[epsg]["units"]
        and axis_order in SUPPORTED_EPSG[epsg]["axis_orders"]
        and authority == "AUTHORITATIVE_METADATA"
    )
    if not valid:
        reasons.append("CRS_DECLARATION_INCOMPLETE")
        return {"declared": False, "epsg": None, "units": None, "axis_order": None, "reasons": reasons}
    return {
        "declared": True,
        "epsg": epsg,
        "units": units,
        "axis_order": axis_order,
        "reasons": reasons,
    }


def _metadata(plan: dict[str, Any]) -> dict[str, Any]:
    value = plan.get("metadata")
    if value is None:
        return {"http_status": None, "sha256": None, "status": "ABSENT"}
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    extra = set(value) - {"http_status", "sha256", "notes", "title"}
    if extra:
        raise ValueError(f"metadata has unsupported keys: {sorted(extra)}")
    status_code = require_int(value.get("http_status"), "metadata.http_status", allow_null=True, minimum=100)
    digest = require_sha256(value.get("sha256"), "metadata.sha256", allow_null=True)
    if status_code == 200:
        status = "SUCCESS"
    elif status_code is None:
        status = "ABSENT"
    else:
        status = "FAILED"
    return {"http_status": status_code, "sha256": digest, "status": status}


def _acquisition(plan: dict[str, Any]) -> dict[str, Any]:
    value = plan.get("acquisition")
    if value is None:
        return {"status": None, "http_status": None, "reason": None}
    if not isinstance(value, dict):
        raise ValueError("acquisition must be an object")
    extra = set(value) - {"status", "http_status", "reason"}
    if extra:
        raise ValueError(f"acquisition has unsupported keys: {sorted(extra)}")
    status = value.get("status")
    if status is not None and status not in ACQUISITION_STATUSES:
        raise ValueError("acquisition.status must be BLOCKED, LOCAL_BYTES, or NOT_ATTEMPTED")
    http_status = require_int(value.get("http_status"), "acquisition.http_status", allow_null=True, minimum=100)
    reason = value.get("reason")
    if reason is not None and (not isinstance(reason, str) or not REASON_CODE_RE.fullmatch(reason)):
        raise ValueError("acquisition.reason must be a machine reason code")
    return {"status": status, "http_status": http_status, "reason": reason}


def _parse_csv(body: str, schema: list[str]) -> tuple[list[dict[str, str]] | None, bool, list[str]]:
    reader = csv.DictReader(io.StringIO(body), strict=True)
    if reader.fieldnames is None or list(reader.fieldnames) != schema:
        return None, False, ["SCHEMA_MISMATCH"]
    rows: list[dict[str, str]] = []
    for row in reader:
        if set(row) != set(schema) or any(value is None for value in row.values()):
            return None, False, ["SCHEMA_MISMATCH"]
        rows.append({field: row[field] for field in schema})
    return rows, True, []


def _id_failures(rows: list[dict[str, str]], id_fields: list[str]) -> list[str]:
    seen: set[tuple[str, ...]] = set()
    failures: list[str] = []
    for row in rows:
        values = tuple(row[field] for field in id_fields)
        if any(value.strip() == "" for value in values):
            failures.append("MISSING_REQUIRED_ID")
            continue
        if values in seen:
            failures.append("DUPLICATE_REQUIRED_ID")
        seen.add(values)
    return list(dict.fromkeys(failures))


def _period_ok(rows: list[dict[str, str]], evidence: dict[str, str]) -> bool:
    field = evidence["field"]
    expected = evidence["value"]
    return bool(rows) and all(row.get(field) == expected for row in rows)


def _numeric_failures(rows: list[dict[str, str]], fields: list[str]) -> list[str]:
    for row in rows:
        for field in fields:
            raw = row[field].strip()
            if not NUMBER_RE.fullmatch(raw):
                return ["NON_NUMERIC_FIELD"]
            try:
                value = Decimal(raw)
            except InvalidOperation:
                return ["NON_NUMERIC_FIELD"]
            if not value.is_finite():
                return ["NON_NUMERIC_FIELD"]
    return []


def _coordinate_numeric_status(
    rows: list[dict[str, str]],
    fields: list[str],
    *,
    requested: bool,
    crs: dict[str, Any],
) -> str:
    if not requested or not crs["declared"] or len(fields) != 2:
        return "NOT_RUN"
    x_field, y_field = fields
    axis = crs["axis_order"]
    for row in rows:
        try:
            x = float(row[x_field].strip())
            y = float(row[y_field].strip())
        except ValueError:
            return "FAIL"
        if not math.isfinite(x) or not math.isfinite(y):
            return "FAIL"
        if crs["epsg"] == 4326:
            lon, lat = (x, y) if axis == "lon-lat" else (y, x)
            if not -180.0 <= lon <= 180.0 or not -90.0 <= lat <= 90.0:
                return "FAIL"
    return "PASS"


def validate(*, plan: dict[str, Any], resource_bytes: bytes | None) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    extra = set(plan) - PLAN_KEYS - IGNORED_PLAN_KEYS
    if extra:
        raise ValueError(f"plan has unsupported keys: {sorted(extra)}")
    if type(plan.get("schema_version")) is not int or plan.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("schema_version must be 1")
    source_input = plan["source_id"] if "source_id" in plan else None
    source_id = canonicalize_source_id(source_input if isinstance(source_input, str) else "")
    fixture_kind = plan.get("fixture_kind")
    if fixture_kind is not None and fixture_kind != "FICTIONAL":
        raise ValueError("fixture_kind must be FICTIONAL when present")
    schema = require_string_list(plan.get("schema"), "schema")
    id_fields = require_string_list(plan.get("id_fields"), "id_fields")
    missing_id_fields = [field for field in id_fields if field not in schema]
    if missing_id_fields:
        raise ValueError(f"id_fields not in schema: {missing_id_fields}")
    expected_row_count = require_int(plan.get("expected_row_count"), "expected_row_count", allow_null=True, minimum=0)
    numeric_fields = require_string_list(plan.get("numeric_fields"), "numeric_fields", allow_empty=True) if "numeric_fields" in plan else []
    missing_numeric = [field for field in numeric_fields if field not in schema]
    if missing_numeric:
        raise ValueError(f"numeric_fields not in schema: {missing_numeric}")
    if "coordinate_fields" in plan:
        coordinate_fields = require_string_list(plan.get("coordinate_fields"), "coordinate_fields", allow_empty=True)
        missing_coordinates = [field for field in coordinate_fields if field not in schema]
        if missing_coordinates:
            raise ValueError(f"coordinate_fields not in schema: {missing_coordinates}")
    else:
        coordinate_fields = [field for field in schema if field in POINT_NAMES]
    max_bytes = require_int(plan.get("max_bytes"), "max_bytes", allow_null=True, minimum=1)
    if max_bytes is None:
        max_bytes = DEFAULT_MAX_BYTES
    if max_bytes > DEFAULT_MAX_BYTES:
        raise ValueError("max_bytes cannot raise the validator bound")
    coordinate_validation_requested = False
    if "coordinate_validation_requested" in plan:
        coordinate_validation_requested = require_bool(
            plan.get("coordinate_validation_requested"), "coordinate_validation_requested"
        )
    declared_sha256 = require_sha256(plan.get("declared_sha256"), "declared_sha256", allow_null=True)
    period_evidence = _period_evidence(plan, required=resource_bytes is not None)
    if period_evidence is not None and period_evidence["field"] not in schema:
        raise ValueError("period_evidence.field must be present in schema")
    metadata = _metadata(plan)
    acquisition_plan = _acquisition(plan)
    crs_info = _crs_declaration(plan.get("crs"))
    plan_sha256 = canonical_sha256(plan)

    failures: list[str] = []
    reasons: list[str] = list(crs_info["reasons"])
    point_names_present = any(field in POINT_NAMES for field in schema)
    if point_names_present and not crs_info["declared"]:
        reasons.append("CRS_FIELD_NAMES_ARE_NOT_EVIDENCE")

    actual_bytes_sha256 = None
    original_bytes_acquired = False
    schema_ok = False
    period_ok = False
    resource_rows: int | None = None
    rows: list[dict[str, str]] | None = None
    encoding_ok = False

    if resource_bytes is None:
        if expected_row_count == 0:
            failures.append("ABSENT_RESOURCE_MUST_NOT_CLAIM_ZERO_ROWS")
        if expected_row_count is not None:
            failures.append("ROW_COUNT_MISMATCH")
        if declared_sha256 is not None:
            failures.append("DECLARED_SHA256_WITHOUT_RESOURCE")
        if acquisition_plan["status"] == "LOCAL_BYTES":
            failures.append("PLAN_CLAIMS_LOCAL_BYTES_WITHOUT_RESOURCE")
            acquisition = "BLOCKED"
        elif acquisition_plan["status"] in {"BLOCKED", "NOT_ATTEMPTED"}:
            acquisition = acquisition_plan["status"]
        else:
            acquisition = "NOT_ATTEMPTED"
        failures.append("RESOURCE_ABSENT")
        if metadata["status"] == "SUCCESS":
            reasons.append("METADATA_IS_NOT_ORIGINAL_BYTES")
    else:
        actual_bytes_sha256 = sha256_bytes(resource_bytes)
        if acquisition_plan["status"] == "BLOCKED":
            failures.append("PLAN_BLOCKED_BUT_RESOURCE_SUPPLIED")
        if acquisition_plan["status"] == "NOT_ATTEMPTED":
            failures.append("PLAN_NOT_ATTEMPTED_BUT_RESOURCE_SUPPLIED")
        acquisition = "LOCAL_BYTES"
        if acquisition_plan["http_status"] not in (None, 200):
            failures.append("ORIGINAL_ACQUISITION_HTTP_FAILED")
        if declared_sha256 is None:
            failures.append("DECLARED_SHA256_MISSING")
        elif declared_sha256 != actual_bytes_sha256:
            failures.append("DECLARED_SHA256_MISMATCH")
        too_large = len(resource_bytes) > max_bytes
        non_csv = looks_like_non_csv_payload(resource_bytes)
        if too_large:
            failures.append("PAYLOAD_TOO_LARGE")
        elif non_csv:
            failures.append("NON_CSV_HTML_OR_ERROR_PAYLOAD")
        else:
            try:
                decoded = resource_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                failures.append("INVALID_ENCODING")
            else:
                encoding_ok = True
                try:
                    rows, schema_ok, schema_failures = _parse_csv(decoded, schema)
                except csv.Error:
                    rows, schema_ok, schema_failures = None, False, ["INVALID_CSV"]
                failures.extend(schema_failures)
                if rows is not None and schema_ok:
                    resource_rows = len(rows)
                    if not rows:
                        failures.append("EMPTY_RESOURCE")
                    if expected_row_count is None:
                        failures.append("ROW_COUNT_MISMATCH")
                    elif resource_rows != expected_row_count:
                        failures.append("ROW_COUNT_MISMATCH")
                    failures.extend(_id_failures(rows, id_fields))
                    if period_evidence is None:
                        failures.append("PERIOD_EVIDENCE_MISSING")
                    elif _period_ok(rows, period_evidence):
                        period_ok = True
                    else:
                        failures.append("PERIOD_MISMATCH")
                    failures.extend(_numeric_failures(rows, numeric_fields))
        original_bytes_acquired = (
            declared_sha256 is not None
            and declared_sha256 == actual_bytes_sha256
            and not too_large
            and not non_csv
            and encoding_ok
            and "ORIGINAL_ACQUISITION_HTTP_FAILED" not in failures
            and acquisition_plan["status"] not in {"BLOCKED", "NOT_ATTEMPTED"}
        )

    coordinate_numeric_validation = "NOT_RUN"
    if rows is not None and schema_ok:
        coordinate_numeric_validation = _coordinate_numeric_status(
            rows, coordinate_fields, requested=coordinate_validation_requested, crs=crs_info
        )
        if coordinate_numeric_validation == "FAIL":
            failures.append("COORDINATE_NUMERIC_VALIDATION_FAILED")
        if coordinate_validation_requested and coordinate_fields:
            failures.append("CRS_AUTHENTICITY_NOT_VERIFIED")
    failures = list(dict.fromkeys(failures))
    completeness = (
        original_bytes_acquired
        and schema_ok
        and period_ok
        and resource_rows is not None
        and not failures
    )

    # The supplied plan literal is a candidate declaration, not evidence of
    # authoritative origin. Actual metadata bytes/declaration authenticity is
    # not verified by this offline plan checker. Never expose it as official CRS.
    crs_label = "UNKNOWN" if coordinate_fields else "NOT_APPLICABLE"
    crs_epsg = crs_units = crs_axis_order = None
    coordinate_validation = "NOT_RUN"

    reasons = list(dict.fromkeys(reasons + failures))
    bytes_match_declared = (
        declared_sha256 is not None
        and actual_bytes_sha256 is not None
        and declared_sha256 == actual_bytes_sha256
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source_id": source_id,
        "source_input": source_input,
        "fixture_kind": fixture_kind,
        "plan_sha256": plan_sha256,
        "declared_sha256": declared_sha256,
        "actual_bytes_sha256": actual_bytes_sha256,
        "bytes_match_declared": bytes_match_declared,
        "original_bytes_acquired": original_bytes_acquired,
        "metadata_http_status": metadata["http_status"],
        "metadata_sha256": metadata["sha256"],
        "metadata_status": metadata["status"],
        "acquisition": acquisition,
        "acquisition_http_status": acquisition_plan["http_status"],
        "resource_rows": resource_rows,
        "schema_ok": schema_ok,
        "period_ok": period_ok,
        "validation_scope": "LOCAL_SUPPLIED_PLAN_CONTRACT_ONLY",
        "business_scope_completeness": "NOT_VERIFIED",
        "completeness": completeness,
        "completeness_failures": failures,
        "coordinate_reference_system": crs_label,
        "crs_epsg": crs_epsg,
        "crs_units": crs_units,
        "crs_axis_order": crs_axis_order,
        "coordinate_validation": coordinate_validation,
        "crs_authenticity": "NOT_VERIFIED",
        "coordinate_numeric_validation": coordinate_numeric_validation,
        "crs_candidate_from_supplied_plan": crs_info if crs_info["declared"] else None,
        "promotion": False,
        "rights_decision": "PENDING",
        "production_active": False,
        "reasons": reasons,
    }


def validate_files(plan_path: Path, resource_path: Path | None = None) -> dict[str, Any]:
    plan = load_plan(plan_path)
    resource_bytes = None
    if resource_path is not None:
        with resource_path.open("rb") as handle:
            resource_bytes = handle.read(DEFAULT_MAX_BYTES + 1)
    return validate(plan=plan, resource_bytes=resource_bytes)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a local CSV resource against a supplied evidence plan. Never fetches."
    )
    parser.add_argument("--plan", required=True, type=Path, help="Path to the evidence plan JSON")
    parser.add_argument("--resource", required=False, type=Path, default=None, help="Optional local CSV path")
    parser.add_argument("--out", required=False, type=Path, default=None, help="Optional JSON receipt path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.plan.is_file():
        print(f"plan not found: {args.plan}", file=sys.stderr)
        return 2
    if args.resource is not None and not args.resource.is_file():
        print(f"resource not found: {args.resource}", file=sys.stderr)
        return 2
    try:
        receipt = validate_files(args.plan, args.resource)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    text = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if receipt["completeness"] else 2


if __name__ == "__main__":
    sys.exit(main())
