#!/usr/bin/env python3
"""Verify a continuous candidate observation window without promoting a source."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import re
from typing import Any
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "govintel" / "source-catalog.v2.json"
GOOD_SCHEMA_STATUSES = {"NO_DRIFT", "ADDITIVE_COMPATIBLE"}
GOOD_HEALTH_STATUSES = {"PASS", "DEGRADED"}
GOOD_WINDOW_CLAIMS = {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS", "PARTIAL"}
# Transient public-safety feeds must always carry their usage disclaimer and
# retention class so a promotion window can never drop the guardrail.
USAGE_NOTICE_REQUIRED = {"S-031"}


def promotion_source_ids() -> tuple[str, ...]:
    """Return the catalog promotion plan; every listed source needs a window."""
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    known = {
        row.get("source_id")
        for row in catalog.get("sources", [])
        if isinstance(row, dict)
    }
    plan = catalog.get("promotion_plan")
    if not isinstance(plan, list) or not plan or any(not isinstance(item, str) or not item for item in plan):
        raise ValueError("catalog promotion_plan must be a nonempty array of source IDs")
    unknown = sorted(set(plan) - known)
    if unknown:
        raise ValueError(f"catalog promotion_plan references unknown sources: {unknown}")
    return tuple(plan)


def load_report(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("sources"), list):
        raise ValueError(f"{path}: invalid canary report")
    return value


def observed_date(value: str) -> date:
    if not isinstance(value, str):
        raise ValueError("observed_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid observed_at: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include timezone")
    return parsed.astimezone(TZ).date()


def validate_reports(
    reports: list[tuple[str, dict[str, Any]]],
    *,
    required_days: int = 7,
    required_source_ids: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    if not reports:
        raise ValueError("at least one canary report is required")
    if required_days < 1:
        raise ValueError("required_days must be positive")
    required = (
        promotion_source_ids() if required_source_ids is None else tuple(required_source_ids)
    )

    reasons: list[str] = []
    source_observations: dict[str, list[dict[str, Any]]] = {}
    day_sources: dict[str, list[str]] = {}
    seen_source_days: set[tuple[str, str]] = set()
    duplicate_sources: set[str] = set()
    report_summaries: list[dict[str, Any]] = []

    for path, report in reports:
        observed_at = report.get("observed_at")
        try:
            local_day = observed_date(observed_at)
        except ValueError as exc:
            reasons.append(f"{path}: {exc}")
            local_day = None
        rows = report["sources"]
        raw_ids = [row.get("source_id") for row in rows if isinstance(row, dict)]
        ids = tuple(sorted(source_id for source_id in raw_ids if isinstance(source_id, str)))
        if len(ids) != len(raw_ids) or not ids or any(not re.fullmatch(r"S-\d{3,4}", source_id) for source_id in ids):
            reasons.append(f"{path}: invalid source inventory")
        if len(ids) != len(set(ids)):
            reasons.append(f"{path}: duplicate source_id")
        if local_day:
            day_sources.setdefault(local_day.isoformat(), []).extend(ids)

        failed_count = report.get("failed_count")
        report_valid = report.get("status") == "OBSERVED" and failed_count == 0
        if not report_valid:
            reasons.append(f"{path}: report is not a successful observation")

        for row in rows:
            if not isinstance(row, dict):
                reasons.append(f"{path}: source row is not an object")
                continue
            source_id = row.get("source_id")
            if not isinstance(source_id, str) or not re.fullmatch(r"S-\d{3,4}", source_id):
                continue
            schema = row.get("schema_contract") or {}
            row_reasons: list[str] = []
            if not isinstance(schema, dict):
                row_reasons.append("invalid schema contract")
                schema = {}
            manifest = row.get("manifest_sha256")
            if row.get("integration_status") != "CANDIDATE":
                row_reasons.append("not a candidate observation")
            if row.get("promotion_eligible") is not False:
                row_reasons.append("promotion flag is not false")
            if row.get("coverage_independently_verified") is not False:
                row_reasons.append("coverage verification flag is unsafe")
            if row.get("source_health") not in GOOD_HEALTH_STATUSES:
                row_reasons.append("source health is not usable")
            if row.get("collector_window_claim") not in GOOD_WINDOW_CLAIMS:
                row_reasons.append("invalid window claim")
            if not isinstance(manifest, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest):
                row_reasons.append("invalid manifest")
            if source_id in USAGE_NOTICE_REQUIRED:
                if not isinstance(row.get("public_usage_notice"), str) or not row["public_usage_notice"].strip():
                    row_reasons.append("missing required public usage notice")
                if not isinstance(row.get("retention_class"), str) or not row["retention_class"].strip():
                    row_reasons.append("missing required retention class")
            if schema.get("status") not in GOOD_SCHEMA_STATUSES or schema.get("review_required"):
                row_reasons.append("schema requires review")
            reasons.extend(f"{path}:{source_id}: {reason}" for reason in row_reasons)
            day_key = (local_day.isoformat(), source_id) if local_day else None
            if day_key and day_key in seen_source_days:
                reasons.append(f"{path}:{source_id}: duplicate observation day")
                duplicate_sources.add(source_id)
            if day_key:
                seen_source_days.add(day_key)
            source_observations.setdefault(source_id, []).append({
                "observed_at": observed_at,
                "local_date": local_day.isoformat() if local_day else None,
                "valid": report_valid and not row_reasons and local_day is not None,
                "source_health": row.get("source_health"),
                "window_completeness": row.get("collector_window_claim"),
                "schema_status": schema.get("status"),
                "manifest_sha256": manifest,
            })
        report_summaries.append({
            "path": path,
            "observed_at": observed_at,
            "local_date": local_day.isoformat() if local_day else None,
            "source_count": len(rows),
            "failed_count": failed_count,
        })

    expected_ids: tuple[str, ...] | None = None
    for day, ids in sorted(day_sources.items()):
        daily_ids = tuple(sorted(set(ids)))
        if expected_ids is None:
            expected_ids = daily_ids
        elif daily_ids != expected_ids:
            reasons.append(f"{day}: source inventory changed")

    # A promotion candidate that never appears in the receipts has zero valid
    # days — the window is incomplete and must block rather than pass silently.
    for source_id in required:
        source_observations.setdefault(source_id, [])

    sources: dict[str, dict[str, Any]] = {}
    for source_id in sorted(source_observations):
        observations = sorted(
            source_observations.get(source_id, []),
            key=lambda item: item["observed_at"] or "",
        )
        days = sorted({item["local_date"] for item in observations if item["local_date"]})
        valid_days = sorted({item["local_date"] for item in observations if item["valid"]})
        if len(valid_days) < required_days:
            reasons.append(f"{source_id}: only {len(valid_days)} valid observed days; need {required_days}")
        missing_days: list[str] = []
        if valid_days:
            first = date.fromisoformat(valid_days[0])
            last = date.fromisoformat(valid_days[-1])
            expected = {(
                first + timedelta(days=offset)
            ).isoformat() for offset in range((last - first).days + 1)}
            missing_days = sorted(expected - set(valid_days))
            if missing_days:
                reasons.append(f"{source_id}: missing observation days {','.join(missing_days)}")
        sources[source_id] = {
            "observation_count": len(observations),
            "observed_days": days,
            "valid_observed_days": valid_days,
            "missing_days": missing_days,
            "first_observed_at": observations[0]["observed_at"] if observations else None,
            "last_observed_at": observations[-1]["observed_at"] if observations else None,
            "window_complete": len(valid_days) >= required_days and not missing_days and source_id not in duplicate_sources,
            "promotion_eligible": False,
        }

    return {
        "schema_version": 1,
        "validation_scope": "CANDIDATE_OBSERVATION_WINDOW_NOT_PROMOTION",
        "required_observation_days": required_days,
        "source_ids": sorted(source_observations),
        "report_count": len(reports),
        "status": "PASS" if not reasons else "BLOCKED",
        "promotion_eligible": False,
        "reasons": reasons,
        "reports": report_summaries,
        "sources": sources,
    }


def self_check() -> None:
    required = promotion_source_ids()
    reports = []
    for offset in range(7):
        day = date(2026, 9, 15) + timedelta(days=offset)
        observed_at = f"{day.isoformat()}T10:00:00+08:00"
        rows = []
        for source_id in required:
            row = {
                "source_id": source_id,
                "integration_status": "CANDIDATE",
                "promotion_eligible": False,
                "coverage_independently_verified": False,
                "source_health": "PASS",
                "collector_window_claim": "PARTIAL" if source_id == "S-031" else "COMPLETE_WITH_ITEMS",
                "manifest_sha256": "a" * 64,
                "schema_contract": {"status": "NO_DRIFT", "review_required": False},
            }
            if source_id in USAGE_NOTICE_REQUIRED:
                row["public_usage_notice"] = "僅供公共態勢感知，不作派遣或勤務指揮依據。"
                row["retention_class"] = "OFFICIAL_TRANSIENT_METADATA"
            rows.append(row)
        reports.append((f"fixture-{offset}.json", {
            "observed_at": observed_at,
            "status": "OBSERVED",
            "failed_count": 0,
            "sources": rows,
        }))
    result = validate_reports(reports)
    assert result["status"] == "PASS"
    assert not result["promotion_eligible"]
    assert all(
        result["sources"][source_id]["window_complete"] for source_id in required
    )
    print(
        "CANDIDATE_OBSERVATION_WINDOW_SELF_CHECK_OK "
        f"days=7 sources={len(required)} promotion=false"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--required-days", type=int, default=7)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    if not args.input:
        parser.error("at least one --input is required unless --self-check is used")
    result = validate_reports([(str(path), load_report(path)) for path in args.input], required_days=args.required_days)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
