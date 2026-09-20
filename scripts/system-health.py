#!/usr/bin/env python3
"""End-to-end health receipts for GovIntel.

Health is lane-aware: a query/MCP failure must not rewrite a successful canonical
publication as failed, while publication-path failures remain blocking for claims
about current public visibility.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS = ROOT / "apps/web/public/data/source-status.json"
DEFAULT_BRIEF = ROOT / "apps/web/public/data/v2-daily-brief.json"
DEFAULT_SCHEMA_DRIFT = ROOT / "apps/web/public/data/schema-drift.json"
SOURCE_POLICY = ROOT / "scripts/source-policy.py"
VALID_OUTCOMES = {"SUCCESS", "FAILED", "PARTIAL", "STALE", "UNKNOWN", "SKIPPED"}
VALID_LANES = {"publication", "query", "discovery"}
REQUIRED_PUBLICATION_STAGES = {
    "collection",
    "canonical_validation",
    "deployment",
    "public_http_verification",
}
FRESH_SOURCE_STATES = {"FRESH", "RECENT"}
STALE_SOURCE_STATES = {"STALE", "VERY_STALE"}


def load_current_policy() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("system_health_source_policy", SOURCE_POLICY)
    if spec is None or spec.loader is None:
        raise ValueError("source policy module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.compile_policy(module.load_catalog())


def policy_binding(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "catalog_hash": policy["catalog_hash"],
        "active_source_ids": list(policy["active_source_ids"]),
    }


def load_schema_drift(path: Path = DEFAULT_SCHEMA_DRIFT) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def schema_contract_stage(receipt: dict[str, Any] | None) -> dict[str, Any]:
    if not receipt or not isinstance(receipt.get("sources"), list) or not receipt["sources"]:
        return {
            "lane": "discovery",
            "stage": "source_contracts",
            "outcome": "UNKNOWN",
            "error_class": "NO_SCHEMA_DRIFT_RECEIPT",
        }
    statuses = {str(source.get("status", "UNKNOWN")) for source in receipt["sources"] if isinstance(source, dict)}
    if "BREAKING_DRIFT" in statuses or "SOURCE_UNAVAILABLE" in statuses:
        outcome, error = "FAILED", "SOURCE_CONTRACT_DRIFT"
    elif "CONTENT_SHAPE_UNKNOWN" in statuses:
        outcome, error = "UNKNOWN", "SOURCE_CONTRACT_UNVERIFIED"
    elif statuses <= {"NO_DRIFT", "ADDITIVE_COMPATIBLE"}:
        outcome, error = "SUCCESS", None
    else:
        outcome, error = "UNKNOWN", "SOURCE_CONTRACT_STATUS_UNKNOWN"
    return {
        "lane": "discovery",
        "stage": "source_contracts",
        "outcome": outcome,
        "error_class": error,
        "contract_overall": receipt.get("overall"),
        "review_inbox_count": len(receipt.get("review_inbox", [])) if isinstance(receipt.get("review_inbox"), list) else 0,
        "ended_at": receipt.get("generated_at"),
        "last_success_at": receipt.get("generated_at") if outcome == "SUCCESS" else None,
    }


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


def lane_health(lane: str, stages: list[dict[str, Any]]) -> str:
    if not stages:
        return "UNKNOWN"
    states = [stage_health(stage["outcome"]) for stage in stages]
    if lane == "publication":
        names = {stage["stage"] for stage in stages}
        if REQUIRED_PUBLICATION_STAGES - names:
            states.append("UNKNOWN")
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
    if any(value != "HEALTHY" for lane, value in lanes.items() if lane != "publication"):
        return "DEGRADED"
    return "HEALTHY"


def latency_ms(start: Any, end: Any) -> int | None:
    left = parse_time(start)
    right = parse_time(end)
    if left is None or right is None or left.tzinfo is None or right.tzinfo is None:
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
    lanes = {lane: lane_health(lane, rows) for lane, rows in grouped.items()}
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


def _source_coverage(sources: Any, required_source_ids: set[str]) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(sources, list) or not sources or any(not isinstance(source, dict) for source in sources):
        return [], False
    source_ids = [source.get("source_id") for source in sources]
    if any(not isinstance(source_id, str) or not source_id for source_id in source_ids):
        return sources, False
    exact = len(source_ids) == len(set(source_ids)) == len(required_source_ids) and set(source_ids) == required_source_ids
    return sources, exact


def current_publication_stages(
    status: dict[str, Any],
    brief: dict[str, Any],
    policy: dict[str, Any] | None = None,
    schema_drift: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if policy is None:
        try:
            policy = load_current_policy()
        except (OSError, ValueError, json.JSONDecodeError):
            policy = None
    required_source_ids = set(policy["active_source_ids"]) if policy else set()
    generated_at = status.get("generated_at")
    run = status.get("latest_collection_run") if isinstance(status.get("latest_collection_run"), dict) else {}
    run_id = run.get("collection_run_id")
    run_status = run.get("status")
    sources, exact_coverage = _source_coverage(status.get("sources"), required_source_ids)
    freshness_states = {
        str(source.get("freshness_status", "UNKNOWN")).upper()
        for source in sources
    }
    has_stale_source = bool(freshness_states & STALE_SOURCE_STATES)
    has_unknown_freshness = any(
        state not in FRESH_SOURCE_STATES | STALE_SOURCE_STATES
        for state in freshness_states
    )
    has_source_gap = (not exact_coverage) or any(
        source.get("source_health") != "PASS"
        or source.get("window_completeness") not in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
        for source in sources
    )
    if policy is None:
        collect_outcome = "UNKNOWN"
        collect_error = "SOURCE_POLICY_UNAVAILABLE"
    elif run_status == "SUCCEEDED" and exact_coverage and not has_source_gap and not has_unknown_freshness and not has_stale_source:
        collect_outcome = "SUCCESS"
        collect_error = None
    elif run_status == "SUCCEEDED" and exact_coverage and has_stale_source and not has_unknown_freshness:
        collect_outcome = "STALE"
        collect_error = "SOURCE_FRESHNESS_STALE"
    elif run_status == "SUCCEEDED" and exact_coverage and has_unknown_freshness:
        collect_outcome = "UNKNOWN"
        collect_error = "SOURCE_FRESHNESS_UNKNOWN"
    elif run_status in {"FAILED", "ERROR"}:
        collect_outcome = "FAILED"
        collect_error = "COLLECTION_RUN_FAILED"
    elif run_status == "SUCCEEDED" or sources:
        collect_outcome = "PARTIAL"
        collect_error = "SOURCE_COVERAGE_OR_COLLECTION_GAP"
    else:
        collect_outcome = "UNKNOWN"
        collect_error = "COLLECTION_RECEIPT_INCOMPLETE"

    publication_status = brief.get("publication_status")
    snapshot_complete = brief.get("snapshot_complete") is True
    same_run = bool(run_id) and brief.get("source_collection_run_id") == run_id
    same_generation = brief.get("source_status_generated_at") == status.get("generated_at")
    if publication_status == "READY" and snapshot_complete and same_run and same_generation:
        validate_outcome = "SUCCESS"
        validate_error = None
    elif brief and (not same_run or not same_generation):
        validate_outcome = "PARTIAL"
        validate_error = "PUBLICATION_GENERATION_MISMATCH"
    elif publication_status in {"PARTIAL", "BLOCKED"} or brief:
        validate_outcome = "PARTIAL"
        validate_error = "PUBLICATION_NOT_READY"
    else:
        validate_outcome = "UNKNOWN"
        validate_error = "PUBLICATION_RECEIPT_INCOMPLETE"

    binding = policy_binding(policy) if policy else {"policy_status": "UNKNOWN"}
    stages = [schema_contract_stage(schema_drift or load_schema_drift())]
    stages.extend([
        {
            "lane": "publication",
            "stage": "collection",
            "outcome": collect_outcome,
            "generation_id": run_id,
            "ended_at": generated_at,
            "last_success_at": generated_at if collect_outcome == "SUCCESS" else None,
            "error_class": collect_error,
            **binding,
        },
        {
            "lane": "publication",
            "stage": "canonical_validation",
            "outcome": validate_outcome,
            "generation_id": brief.get("source_collection_run_id"),
            "ended_at": brief.get("generated_at"),
            "last_success_at": brief.get("generated_at") if validate_outcome == "SUCCESS" else None,
            "error_class": validate_error,
            **binding,
        },
        {
            "lane": "publication",
            "stage": "deployment",
            "outcome": "UNKNOWN",
            "error_class": "NO_DEPLOYMENT_RECEIPT_IN_CANONICAL_ARTIFACT",
            **binding,
        },
        {
            "lane": "publication",
            "stage": "public_http_verification",
            "outcome": "UNKNOWN",
            "error_class": "NO_HTTP_HASH_RECEIPT_IN_CANONICAL_ARTIFACT",
            **binding,
        },
        {
            "lane": "query",
            "stage": "query_index",
            "outcome": "UNKNOWN",
            "error_class": "QUERY_INDEX_NOT_YET_WIRED",
            **binding,
        },
        {
            "lane": "query",
            "stage": "mcp_web_query",
            "outcome": "UNKNOWN",
            "error_class": "QUERY_RUNTIME_NOT_YET_WIRED",
            **binding,
        },
    ])
    return stages


def load_current(status_path: Path = DEFAULT_STATUS, brief_path: Path = DEFAULT_BRIEF) -> dict[str, Any]:
    status = json.loads(status_path.read_text(encoding="utf-8"))
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    try:
        policy = load_current_policy()
    except (OSError, ValueError, json.JSONDecodeError):
        policy = None
    schema_drift = load_schema_drift()
    result = build_health(current_publication_stages(status, brief, policy, schema_drift))
    result["policy"] = policy_binding(policy) if policy else {"policy_status": "UNKNOWN"}
    result["schema_drift"] = {
        "overall": schema_drift.get("overall") if schema_drift else "UNKNOWN",
        "source_count": len(schema_drift.get("sources", [])) if schema_drift else 0,
    }
    result["review_inbox"] = schema_drift.get("review_inbox", []) if schema_drift else []
    return result


def self_check() -> None:
    healthy_publication = [
        {"lane": "publication", "stage": "collection", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "canonical_validation", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "deployment", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "public_http_verification", "outcome": "SUCCESS"},
    ]
    query_failed = [{"lane": "query", "stage": "query_index", "outcome": "FAILED", "error_class": "INDEX_BUILD"}]
    result = build_health(healthy_publication + query_failed)
    assert result["lanes"]["publication"] == "HEALTHY"
    assert result["lanes"]["query"] == "BLOCKED"
    assert result["overall"] == "DEGRADED"

    incomplete = build_health([{"lane": "publication", "stage": "collection", "outcome": "SUCCESS"}])
    assert incomplete["lanes"]["publication"] == "UNKNOWN"
    assert incomplete["overall"] == "UNKNOWN"

    blocked = build_health([
        {"lane": "publication", "stage": "collection", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "canonical_validation", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "deployment", "outcome": "FAILED", "error_class": "PROTECTED_BRANCH"},
    ])
    assert blocked["overall"] == "BLOCKED"
    print("SYSTEM_HEALTH_SELF_CHECK_OK query_failure_preserves_publication=true publish_failure_blocks=true missing_stage_unknown=true")


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
