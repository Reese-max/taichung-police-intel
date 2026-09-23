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
GOOD_SCHEMA_STATUSES = {"NO_DRIFT", "ADDITIVE_COMPATIBLE"}
GOOD_HEALTH_STATUSES = {"PASS", "DEGRADED"}
GOOD_WINDOW_CLAIMS = {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS", "PARTIAL"}


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
) -> dict[str, Any]:
    if not reports:
        raise ValueError("at least one canary report is required")
    if required_days < 1:
        raise ValueError("required_days must be positive")

    reasons: list[str] = []
    expected_ids: tuple[str, ...] | None = None
    source_observations: dict[str, list[dict[str, Any]]] = {}
    seen_report_times: set[str] = set()
    report_summaries: list[dict[str, Any]] = []

    for path, report in reports:
        observed_at = report.get("observed_at")
        try:
            local_day = observed_date(observed_at)
        except ValueError as exc:
            reasons.append(f"{path}: {exc}")
            local_day = None
        if not isinstance(observed_at, str) or observed_at in seen_report_times:
            reasons.append(f"{path}: duplicate or missing observed_at")
        if isinstance(observed_at, str):
            seen_report_times.add(observed_at)

        rows = report["sources"]
        raw_ids = [row.get("source_id") for row in rows if isinstance(row, dict)]
        ids = tuple(sorted(source_id for source_id in raw_ids if isinstance(source_id, str)))
        if len(ids) != len(raw_ids) or not ids or any(not re.fullmatch(r"S-\d{3,4}", source_id) for source_id in ids):
            reasons.append(f"{path}: invalid source inventory")
        if len(ids) != len(set(ids)):
            reasons.append(f"{path}: duplicate source_id")
        if expected_ids is None:
            expected_ids = ids
        elif ids != expected_ids:
            reasons.append(f"{path}: source inventory changed")

        failed_count = report.get("failed_count")
        if report.get("status") != "OBSERVED" or failed_count != 0:
            reasons.append(f"{path}: report is not an all-source observation")

        for row in rows:
            if not isinstance(row, dict):
                reasons.append(f"{path}: source row is not an object")
                continue
            source_id = row.get("source_id")
            if not isinstance(source_id, str):
                continue
            schema = row.get("schema_contract") or {}
            if not isinstance(schema, dict):
                reasons.append(f"{path}:{source_id}: invalid schema contract")
                schema = {}
            manifest = row.get("manifest_sha256")
            if row.get("integration_status") != "CANDIDATE":
                reasons.append(f"{path}:{source_id}: not a candidate observation")
            if row.get("promotion_eligible") is not False:
                reasons.append(f"{path}:{source_id}: promotion flag is not false")
            if row.get("coverage_independently_verified") is not False:
                reasons.append(f"{path}:{source_id}: coverage verification flag is unsafe")
            if row.get("source_health") not in GOOD_HEALTH_STATUSES:
                reasons.append(f"{path}:{source_id}: source health is not usable")
            if row.get("collector_window_claim") not in GOOD_WINDOW_CLAIMS:
                reasons.append(f"{path}:{source_id}: invalid window claim")
            if not isinstance(manifest, str) or not re.fullmatch(r"[0-9a-f]{64}", manifest):
                reasons.append(f"{path}:{source_id}: invalid manifest")
            if schema.get("status") not in GOOD_SCHEMA_STATUSES or schema.get("review_required"):
                reasons.append(f"{path}:{source_id}: schema requires review")
            source_observations.setdefault(source_id, []).append({
                "observed_at": observed_at,
                "local_date": local_day.isoformat() if local_day else None,
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

    sources: dict[str, dict[str, Any]] = {}
    for source_id in expected_ids or ():
        observations = sorted(
            source_observations.get(source_id, []),
            key=lambda item: item["observed_at"] or "",
        )
        days = sorted({item["local_date"] for item in observations if item["local_date"]})
        if len(days) < required_days:
            reasons.append(f"{source_id}: only {len(days)} observed days; need {required_days}")
        missing_days: list[str] = []
        if days:
            first = date.fromisoformat(days[0])
            last = date.fromisoformat(days[-1])
            expected = {(
                first + timedelta(days=offset)
            ).isoformat() for offset in range((last - first).days + 1)}
            missing_days = sorted(expected - set(days))
            if missing_days:
                reasons.append(f"{source_id}: missing observation days {','.join(missing_days)}")
        sources[source_id] = {
            "observation_count": len(observations),
            "observed_days": days,
            "missing_days": missing_days,
            "first_observed_at": observations[0]["observed_at"] if observations else None,
            "last_observed_at": observations[-1]["observed_at"] if observations else None,
            "window_complete": len(days) >= required_days and not missing_days,
            "promotion_eligible": False,
        }

    return {
        "schema_version": 1,
        "validation_scope": "CANDIDATE_OBSERVATION_WINDOW_NOT_PROMOTION",
        "required_observation_days": required_days,
        "source_ids": list(expected_ids or ()),
        "report_count": len(reports),
        "status": "PASS" if not reasons else "BLOCKED",
        "promotion_eligible": False,
        "reasons": reasons,
        "reports": report_summaries,
        "sources": sources,
    }


def self_check() -> None:
    reports = []
    for offset in range(7):
        day = date(2026, 9, 15) + timedelta(days=offset)
        observed_at = f"{day.isoformat()}T10:00:00+08:00"
        rows = []
        for source_id in ("S-001", "S-031"):
            rows.append({
                "source_id": source_id,
                "integration_status": "CANDIDATE",
                "promotion_eligible": False,
                "coverage_independently_verified": False,
                "source_health": "PASS",
                "collector_window_claim": "PARTIAL" if source_id == "S-031" else "COMPLETE_WITH_ITEMS",
                "manifest_sha256": "a" * 64,
                "schema_contract": {"status": "NO_DRIFT", "review_required": False},
            })
        reports.append((f"fixture-{offset}.json", {
            "observed_at": observed_at,
            "status": "OBSERVED",
            "failed_count": 0,
            "sources": rows,
        }))
    result = validate_reports(reports)
    assert result["status"] == "PASS"
    assert not result["promotion_eligible"]
    print("CANDIDATE_OBSERVATION_WINDOW_SELF_CHECK_OK days=7 promotion=false")


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
