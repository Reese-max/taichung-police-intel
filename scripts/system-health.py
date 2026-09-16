#!/usr/bin/env python3
"""End-to-end health receipts for GovIntel.

Health is lane-aware: a query/MCP failure must not rewrite a successful canonical
publication as failed, while publication-path failures remain blocking for claims
about current public visibility.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = ROOT / "apps/web/public/data/source-status.json"
DEFAULT_BRIEF = ROOT / "apps/web/public/data/v2-daily-brief.json"
VALID_OUTCOMES = {"SUCCESS", "FAILED", "PARTIAL", "STALE", "UNKNOWN", "SKIPPED"}
VALID_LANES = {"publication", "query", "discovery"}


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_stage(stage: dict[str, Any]) -> None:
    name = stage.get("stage")
    lane = stage.get("lane")
    outcome = stage.get("outcome")
    if not isinstance(name, str) or not name:
        raise ValueError("stage name missing")
    if lane not in VALID_LANES:
        raise ValueError(f"invalid lane for {name}: {lane}")
    if outcome not in VALID_OUTCOMES:
        raise ValueError(f"invalid outcome for {name}: {outcome}")
    for field in ("started_at", "ended_at", "last_success_at"):
        value = stage.get(field)
        if value is not None and parse_time(value) is None:
            raise ValueError(f"invalid {field} for {name}")
    started = parse_time(stage.get("started_at"))
    ended = parse_time(stage.get("ended_at"))
    if started and ended and ended < started:
        raise ValueError(f"stage {name} ended before it started")


def stage_health(outcome: str) -> str:
    return {
        "SUCCESS": "HEALTHY",
        "FAILED": "BLOCKED",
        "PARTIAL": "PARTIAL",
        "STALE": "STALE",
        "UNKNOWN": "UNKNOWN",
        "SKIPPED": "UNKNOWN",
    }[outcome]


def lane_health(stages: list[dict[str, Any]]) -> str:
    if not stages:
        return "UNKNOWN"
    states = [stage_health(stage["outcome"]) for stage in stages]
    if "BLOCKED" in states:
        return "BLOCKED"
    if "PARTIAL" in states:
        return "PARTIAL"
    if "STALE" in states:
        return "STALE"
    if "UNKNOWN" in states:
        return "UNKNOWN"
    return "HEALTHY"


def overall_health(lanes: dict[str, str]) -> str:
    publication = lanes.get("publication", "UNKNOWN")
    if publication == "BLOCKED":
        return "BLOCKED"
    if publication == "PARTIAL":
        return "PARTIAL"
    if publication == "STALE":
        return "STALE"
    if publication == "UNKNOWN":
        return "UNKNOWN"
    # Canonical publication is healthy.  Optional downstream/discovery lanes may
    # degrade the system experience but never rewrite publication truth.
    if any(value != "HEALTHY" for lane, value in lanes.items() if lane != "publication"):
        return "DEGRADED"
    return "HEALTHY"


def latency_ms(start: Any, end: Any) -> int | None:
    left = parse_time(start)
    right = parse_time(end)
    if left is None or right is None:
        return None
    if left.tzinfo is None or right.tzinfo is None:
        return None
    delta = int((right - left).total_seconds() * 1000)
    return delta if delta >= 0 else None


def build_health(stages: list[dict[str, Any]], timestamps: dict[str, Any] | None = None) -> dict[str, Any]:
    seen: set[tuple[str, str]] = set()
    for stage in stages:
        validate_stage(stage)
        key = (stage["lane"], stage["stage"])
        if key in seen:
            raise ValueError(f"duplicate stage receipt: {key[0]}/{key[1]}")
        seen.add(key)

    grouped = {lane: [stage for stage in stages if stage["lane"] == lane] for lane in sorted(VALID_LANES)}
    lanes = {lane: lane_health(rows) for lane, rows in grouped.items()}
    times = timestamps or {}
    metrics = {
        "source_to_detect_ms": latency_ms(times.get("source_published_at"), times.get("detected_at")),
        "detect_to_verify_ms": latency_ms(times.get("detected_at"), times.get("verified_at")),
        "verify_to_publish_ms": latency_ms(times.get("verified_at"), times.get("published_at")),
        "publish_to_visible_ms": latency_ms(times.get("published_at"), times.get("public_visible_at")),
    }
    return {
        "schema_version": 1,
        "overall": overall_health(lanes),
        "lanes": lanes,
        "stages": sorted(stages, key=lambda row: (row["lane"], row["stage"])),
        "latency_metrics": metrics,
    }


def current_publication_stages(status: dict[str, Any], brief: dict[str, Any]) -> list[dict[str, Any]]:
    generated_at = status.get("generated_at")
    run = status.get("latest_collection_run") if isinstance(status.get("latest_collection_run"), dict) else {}
    run_status = run.get("status")
    sources = status.get("sources") if isinstance(status.get("sources"), list) else []
    has_source_gap = any(
        not isinstance(source, dict)
        or source.get("source_health") != "PASS"
        or source.get("window_completeness") not in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
        for source in sources
    )
    if run_status == "SUCCEEDED" and not has_source_gap:
        collect_outcome = "SUCCESS"
    elif run_status in {"FAILED", "ERROR"}:
        collect_outcome = "FAILED"
    elif sources:
        collect_outcome = "PARTIAL"
    else:
        collect_outcome = "UNKNOWN"

    publication_status = brief.get("publication_status")
    snapshot_complete = brief.get("snapshot_complete") is True
    if publication_status == "READY" and snapshot_complete:
        validate_outcome = "SUCCESS"
    elif publication_status in {"PARTIAL", "BLOCKED"} or brief:
        validate_outcome = "PARTIAL"
    else:
        validate_outcome = "UNKNOWN"

    return [
        {
            "lane": "publication",
            "stage": "collection",
            "outcome": collect_outcome,
            "generation_id": run.get("collection_run_id"),
            "ended_at": generated_at,
            "last_success_at": generated_at if collect_outcome == "SUCCESS" else None,
            "error_class": None if collect_outcome == "SUCCESS" else "SOURCE_OR_COLLECTION_GAP",
        },
        {
            "lane": "publication",
            "stage": "canonical_validation",
            "outcome": validate_outcome,
            "generation_id": brief.get("source_collection_run_id"),
            "ended_at": brief.get("generated_at"),
            "last_success_at": brief.get("generated_at") if validate_outcome == "SUCCESS" else None,
            "error_class": None if validate_outcome == "SUCCESS" else "PUBLICATION_NOT_READY",
        },
        {
            "lane": "publication",
            "stage": "deployment",
            "outcome": "UNKNOWN",
            "error_class": "NO_DEPLOYMENT_RECEIPT_IN_CANONICAL_ARTIFACT",
        },
        {
            "lane": "publication",
            "stage": "public_http_verification",
            "outcome": "UNKNOWN",
            "error_class": "NO_HTTP_HASH_RECEIPT_IN_CANONICAL_ARTIFACT",
        },
        {
            "lane": "query",
            "stage": "query_index",
            "outcome": "UNKNOWN",
            "error_class": "QUERY_INDEX_NOT_YET_WIRED",
        },
        {
            "lane": "query",
            "stage": "mcp_web_query",
            "outcome": "UNKNOWN",
            "error_class": "QUERY_RUNTIME_NOT_YET_WIRED",
        },
    ]


def load_current(status_path: Path = DEFAULT_STATUS, brief_path: Path = DEFAULT_BRIEF) -> dict[str, Any]:
    status = json.loads(status_path.read_text(encoding="utf-8"))
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    return build_health(current_publication_stages(status, brief))


def self_check() -> None:
    healthy_publication = [
        {"lane": "publication", "stage": "collection", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "validation", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "deployment", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "public_http", "outcome": "SUCCESS"},
    ]
    query_failed = [
        {"lane": "query", "stage": "query_index", "outcome": "FAILED", "error_class": "INDEX_BUILD"}
    ]
    result = build_health(healthy_publication + query_failed)
    assert result["lanes"]["publication"] == "HEALTHY"
    assert result["lanes"]["query"] == "BLOCKED"
    assert result["overall"] == "DEGRADED"

    blocked = build_health([
        {"lane": "publication", "stage": "collection", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "deployment", "outcome": "FAILED", "error_class": "PROTECTED_BRANCH"},
    ])
    assert blocked["overall"] == "BLOCKED"
    print("SYSTEM_HEALTH_SELF_CHECK_OK query_failure_preserves_publication=true publish_failure_blocks=true")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    parser.add_argument("--input", type=Path, help="Optional explicit stage-receipt JSON")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        return 0
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        result = build_health(payload.get("stages", []), payload.get("timestamps"))
    else:
        result = load_current(args.status, args.brief)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
