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
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.review import load_state as load_review_state, project as project_review_items, reconcile as reconcile_review, schema_drift_candidates, source_health_candidates

DEFAULT_STATUS = ROOT / "apps/web/public/data/source-status.json"
DEFAULT_BRIEF = ROOT / "apps/web/public/data/v2-daily-brief.json"
DEFAULT_SCHEMA_DRIFT = ROOT / "apps/web/public/data/schema-drift.json"
DEFAULT_REVIEW_STATE = ROOT / "state" / "review-inbox.json"
DEFAULT_UPSTREAM_STATE = ROOT / "state" / "discovery-consumer-state.json"
DEFAULT_QUERY_RECEIPT = ROOT / "runtime-evidence" / "query-gateway-receipt.json"
SOURCE_POLICY = ROOT / "scripts/source-policy.py"
STAGE_MODEL_VERSION = "govintel-e2e-stages.v1"
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
COMPLETE_SOURCE_WINDOWS = {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
# Publication-lane failure classes that mean the published artifact disagrees
# with the source snapshot, as opposed to a collection or runtime failure.
MISMATCH_ERROR_CLASSES = {
    "PUBLICATION_GENERATION_MISMATCH",
    "PUBLIC_HTTP_HASH_MISMATCH",
}

# End-to-end chain, ordered from upstream source publication to the served
# query runtime. Every stage receipt is attributed against this registry so an
# operator can name the broken link instead of reporting one red light.
STAGE_MODEL: tuple[dict[str, Any], ...] = (
    {
        "lane": "discovery",
        "stage": "source_contracts",
        "order": 10,
        "description": "上游來源結構契約比對",
        "measures": ["source_contract_status"],
    },
    {
        "lane": "discovery",
        "stage": "upstream_operating_state",
        "order": 20,
        "description": "上游 Dashboard operating state（只反映上游，不覆蓋 GovIntel 自身健康）",
        "measures": ["upstream_operating_state", "upstream_current"],
    },
    {
        "lane": "publication",
        "stage": "collection",
        "order": 30,
        "description": "來源抓取（每個 active source 的結果與 gap）",
        "measures": ["collection_success_ratio", "source_freshness_age_ms", "stale_source_ratio"],
    },
    {
        "lane": "publication",
        "stage": "parsing",
        "order": 40,
        "description": "抓取結果解析為正規化紀錄",
        "measures": ["parsed_item_count"],
    },
    {
        "lane": "publication",
        "stage": "fusion_verification",
        "order": 50,
        "description": "跨來源融合與官方來源驗證",
        "measures": ["verified_item_count"],
    },
    {
        "lane": "publication",
        "stage": "canonical_validation",
        "order": 60,
        "description": "canonical 發布與來源世代一致性驗證",
        "measures": ["publication_mismatch_count"],
    },
    {
        "lane": "publication",
        "stage": "publication_build",
        "order": 70,
        "description": "publication bundle / Pages 產物建置",
        "measures": [],
    },
    {
        "lane": "publication",
        "stage": "deployment",
        "order": 80,
        "description": "protected state 分支推送與 Pages 部署",
        "measures": [],
    },
    {
        "lane": "publication",
        "stage": "public_http_verification",
        "order": 90,
        "description": "公開 HTTP / hash 驗證（部署後可讀回）",
        "measures": ["publish_to_public_visible_ms"],
    },
    {
        "lane": "query",
        "stage": "query_index",
        "order": 100,
        "description": "Query Store 索引建置與世代綁定",
        "measures": ["query_index_lag_ms"],
    },
    {
        "lane": "query",
        "stage": "read_only_mcp",
        "order": 105,
        "description": "唯讀 MCP stdio 生命週期與發布綁定",
        "measures": [],
    },
    {
        "lane": "query",
        "stage": "mcp_web_query",
        "order": 110,
        "description": "Web Chat / MCP 查詢服務",
        "measures": ["query_error_rate"],
    },
)

# First release measures these fields only. No SLA threshold is promised until
# a field can be measured on a real production receipt.
SLO_METRIC_FIELDS: tuple[tuple[str, str], ...] = (
    ("collection_success_ratio", "ratio"),
    ("source_freshness_age_ms", "milliseconds"),
    ("stale_source_ratio", "ratio"),
    ("partial_source_ratio", "ratio"),
    ("publication_mismatch_count", "count"),
    ("source_to_detect_ms", "milliseconds"),
    ("detect_to_verify_ms", "milliseconds"),
    ("verify_to_publish_ms", "milliseconds"),
    ("publish_to_public_visible_ms", "milliseconds"),
    ("query_index_lag_ms", "milliseconds"),
    ("query_error_rate", "ratio"),
)

UPSTREAM_OPERATING_STATES = {"ACTIVE", "DEGRADED", "RESTORING", "PAUSED"}
# A paused or degraded upstream feed is an upstream fact, never a GovIntel
# collection failure; it degrades the discovery lane only.
UPSTREAM_STATE_OUTCOMES = {
    "ACTIVE": "SUCCESS",
    "DEGRADED": "PARTIAL",
    "RESTORING": "PARTIAL",
    "PAUSED": "STALE",
}

# Each known failure class resolves to exactly one stage. Unclassified classes
# fail closed instead of being dumped into a catch-all stage.
FAILURE_STAGE_CLASSIFICATION: dict[str, tuple[str, str, str]] = {
    "COLLECTOR_TRANSPORT_ERROR": ("publication", "collection", "FAILED"),
    "COLLECTION_NOT_RUN": ("publication", "collection", "FAILED"),
    "SOURCE_POLICY_UNAVAILABLE": ("publication", "collection", "FAILED"),
    "COLLECTION_RUN_FAILED": ("publication", "collection", "FAILED"),
    "COLLECTION_RECEIPT_INCOMPLETE": ("publication", "collection", "FAILED"),
    "SOURCE_COVERAGE_OR_COLLECTION_GAP": ("publication", "collection", "FAILED"),
    "SOURCE_FRESHNESS_STALE": ("publication", "collection", "FAILED"),
    "SOURCE_FRESHNESS_UNKNOWN": ("publication", "collection", "FAILED"),
    "PARSER_OUTPUT_PARTIAL": ("publication", "parsing", "PARTIAL"),
    "PARSER_OUTPUT_INVALID": ("publication", "parsing", "FAILED"),
    "NO_PARSING_RECEIPT_IN_CANONICAL_ARTIFACT": ("publication", "parsing", "FAILED"),
    "FUSION_VERIFICATION_FAILED": ("publication", "fusion_verification", "FAILED"),
    "UPSTREAM_NOT_CURRENT": ("publication", "fusion_verification", "FAILED"),
    "NO_FUSION_RECEIPT_IN_CANONICAL_ARTIFACT": ("publication", "fusion_verification", "FAILED"),
    "PUBLICATION_GENERATION_MISMATCH": ("publication", "canonical_validation", "FAILED"),
    "PUBLICATION_NOT_READY": ("publication", "canonical_validation", "FAILED"),
    "PUBLICATION_RECEIPT_INCOMPLETE": ("publication", "canonical_validation", "FAILED"),
    "CANONICAL_VALIDATION_FAILED": ("publication", "canonical_validation", "FAILED"),
    "CANONICAL_VALIDATION_NOT_REPORTED": ("publication", "canonical_validation", "FAILED"),
    "CANONICAL_VALIDATION_INCOMPLETE": ("publication", "canonical_validation", "FAILED"),
    "PUBLICATION_BUILD_FAILED": ("publication", "publication_build", "FAILED"),
    "NO_PUBLICATION_BUILD_RECEIPT_IN_CANONICAL_ARTIFACT": ("publication", "publication_build", "FAILED"),
    "PROTECTED_BRANCH_PUSH_REJECTED": ("publication", "deployment", "FAILED"),
    "PUBLICATION_STATE_PUSH_FAILED": ("publication", "deployment", "FAILED"),
    "DEPLOY_ACTION_FAILED": ("publication", "deployment", "FAILED"),
    "DEPLOYMENT_NOT_RUN": ("publication", "deployment", "FAILED"),
    "NO_DEPLOYMENT_RECEIPT_IN_CANONICAL_ARTIFACT": ("publication", "deployment", "FAILED"),
    "PUBLIC_HTTP_HASH_MISMATCH": ("publication", "public_http_verification", "FAILED"),
    "PUBLIC_HTTP_UNREACHABLE": ("publication", "public_http_verification", "FAILED"),
    "PUBLIC_HTTP_NOT_VERIFIED": ("publication", "public_http_verification", "FAILED"),
    "NO_HTTP_HASH_RECEIPT_IN_CANONICAL_ARTIFACT": ("publication", "public_http_verification", "FAILED"),
    "QUERY_INDEX_BUILD_FAILED": ("query", "query_index", "FAILED"),
    "QUERY_INDEX_GENERATION_MISMATCH": ("query", "query_index", "FAILED"),
    "QUERY_CHECKS_MISSING": ("query", "query_index", "FAILED"),
    "QUERY_CHECK_FAILED": ("query", "query_index", "FAILED"),
    "QUERY_DOWN_INJECTED": ("query", "query_index", "FAILED"),
    "QUERY_INDEX_NOT_YET_WIRED": ("query", "query_index", "FAILED"),
    "MCP_RUNTIME_UNAVAILABLE": ("query", "mcp_web_query", "FAILED"),
    "MCP_RUNTIME_NOT_VERIFIED": ("query", "read_only_mcp", "FAILED"),
    "READ_ONLY_MCP_NOT_YET_WIRED": ("query", "read_only_mcp", "FAILED"),
    "QUERY_RUNTIME_NOT_VERIFIED": ("query", "mcp_web_query", "FAILED"),
    "QUERY_RUNTIME_NOT_YET_WIRED": ("query", "mcp_web_query", "FAILED"),
    "CAPABILITY_NOT_AVAILABLE": ("query", "mcp_web_query", "FAILED"),
    "SOURCE_CONTRACT_DRIFT": ("discovery", "source_contracts", "FAILED"),
    "SOURCE_CONTRACT_UNVERIFIED": ("discovery", "source_contracts", "FAILED"),
    "SOURCE_CONTRACT_STATUS_UNKNOWN": ("discovery", "source_contracts", "FAILED"),
    "NO_SCHEMA_DRIFT_RECEIPT": ("discovery", "source_contracts", "FAILED"),
    "SCHEMA_DRIFT_NOT_VERIFIED": ("discovery", "source_contracts", "FAILED"),
    "UPSTREAM_OPERATING_STATE_DEGRADED": ("discovery", "upstream_operating_state", "FAILED"),
    "UPSTREAM_OPERATING_STATE_RESTORING": ("discovery", "upstream_operating_state", "FAILED"),
    "UPSTREAM_OPERATING_STATE_PAUSED": ("discovery", "upstream_operating_state", "FAILED"),
    "NO_UPSTREAM_OPERATING_STATE_RECEIPT": ("discovery", "upstream_operating_state", "FAILED"),
}

STAGE_LINKAGE_FIELDS = (
    "started_at",
    "ended_at",
    "last_success_at",
    "generation_id",
    "upstream_hash",
    "downstream_hash",
    "item_count",
    "gap_count",
    "error_stage",
    "error_class",
    "receipt_ref",
)



def load_source_policy() -> Any:
    spec = importlib.util.spec_from_file_location("system_health_source_policy", SOURCE_POLICY)
    if spec is None or spec.loader is None:
        raise ValueError("source policy module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_current_policy() -> dict[str, Any]:
    module = load_source_policy()
    return module.load_current_policy()


def policy_binding(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "catalog_hash": policy["catalog_hash"],
        "active_source_ids": list(policy["active_source_ids"]),
    }


def stage_model() -> dict[str, Any]:
    """The versioned end-to-end stage contract shipped with this receipt.

    `required_publication_stages` names the stages whose absence alone forces the
    publication lane to UNKNOWN, so a consumer never has to guess what gates a
    HEALTHY verdict.
    """
    return {
        "version": STAGE_MODEL_VERSION,
        "stage_ids": [row["stage"] for row in STAGE_MODEL],
        "required_publication_stages": sorted(REQUIRED_PUBLICATION_STAGES),
        "stages": [dict(row) for row in STAGE_MODEL],
    }


def stage_model_entry(stage_id: Any) -> dict[str, Any] | None:
    """Registry row for a stage id, or None when it is outside the contract."""
    for row in STAGE_MODEL:
        if row["stage"] == stage_id:
            return dict(row)
    return None


def error_classes_for_stage(stage_id: Any) -> list[str]:
    return sorted(
        error_class
        for error_class, (_, owner, _) in FAILURE_STAGE_CLASSIFICATION.items()
        if owner == stage_id
    )


def failure_stage_receipt(
    error_class: str,
    outcome: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """Attribute one known failure class to exactly one stage, or fail closed."""
    target = FAILURE_STAGE_CLASSIFICATION.get(error_class)
    if target is None:
        raise ValueError(f"unclassified error class: {error_class}")
    lane, stage_id, default_outcome = target
    receipt: dict[str, Any] = {
        "lane": lane,
        "stage": stage_id,
        "outcome": outcome or default_outcome,
        "error_class": error_class,
        "error_stage": stage_id,
    }
    receipt.update(extra)
    return receipt


def upstream_operating_state_stage(receipt: Any = None) -> dict[str, Any]:
    """Reflect the upstream feed operating state without overriding our health."""
    payload = receipt if isinstance(receipt, dict) else {}
    state = payload.get("upstream_operating_state")
    if state not in UPSTREAM_OPERATING_STATES:
        return {
            "lane": "discovery",
            "stage": "upstream_operating_state",
            "outcome": "UNKNOWN",
            "error_class": "NO_UPSTREAM_OPERATING_STATE_RECEIPT",
            "ended_at": None,
            "last_success_at": None,
        }
    outcome = UPSTREAM_STATE_OUTCOMES[state]
    return {
        "lane": "discovery",
        "stage": "upstream_operating_state",
        "outcome": outcome,
        "error_class": None if outcome == "SUCCESS" else f"UPSTREAM_OPERATING_STATE_{state}",
        "generation_id": payload.get("last_seen_generation_id"),
        "upstream_hash": payload.get("last_seen_content_hash"),
        "ended_at": payload.get("last_seen_generated_at"),
        "last_success_at": payload.get("last_seen_generated_at") if outcome == "SUCCESS" else None,
        "upstream_current": payload.get("upstream_current") is True,
        "overrides_govintel_health": False,
    }


def upstream_context(receipt: Any = None) -> dict[str, Any]:
    stage = upstream_operating_state_stage(receipt)
    return {
        "stage": stage["stage"],
        "upstream_operating_state": (
            receipt.get("upstream_operating_state")
            if isinstance(receipt, dict)
            and receipt.get("upstream_operating_state") in UPSTREAM_OPERATING_STATES
            else "UNKNOWN"
        ),
        "upstream_current": stage.get("upstream_current", False),
        "generation_id": stage.get("generation_id"),
        "overrides_govintel_health": False,
        "note": "上游 operating state 只反映上游事實，不覆蓋 GovIntel 自身健康判定。",
    }


def read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_upstream_receipt(path: Path = DEFAULT_UPSTREAM_STATE) -> dict[str, Any] | None:
    """Upstream discovery state is optional; absence stays UNKNOWN, never success."""
    return read_json_object(path)


def load_query_receipt(path: Path = DEFAULT_QUERY_RECEIPT) -> dict[str, Any] | None:
    """Query/MCP runtime receipts are produced by the workflow, not checked in."""
    return read_json_object(path)



def load_schema_drift(path: Path = DEFAULT_SCHEMA_DRIFT) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_review_inbox(
    path: Path = DEFAULT_REVIEW_STATE,
    schema_drift: dict[str, Any] | None = None,
    source_status: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | None:
    try:
        state = load_review_state(path)
        if isinstance(schema_drift, dict) and isinstance(schema_drift.get("generated_at"), str):
            candidates = schema_drift_candidates(schema_drift)
            if candidates:
                state = reconcile_review(state, candidates, observed_at=schema_drift["generated_at"])
        if isinstance(source_status, dict) and isinstance(source_status.get("generated_at"), str):
            candidates = source_health_candidates(source_status)
            if candidates:
                state = reconcile_review(state, candidates, observed_at=source_status["generated_at"])
        return project_review_items(state, public=True)
    except (OSError, ValueError, json.JSONDecodeError):
        return None


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


_OPERATOR_OUTCOME_PRIORITY = {
    "FAILED": 0,
    "PARTIAL": 1,
    "STALE": 2,
    "UNKNOWN": 3,
    "SKIPPED": 4,
    "SUCCESS": 5,
}


def operator_summary(overall: str, stages: list[dict[str, Any]]) -> dict[str, Any]:
    attention = [stage for stage in stages if stage.get("outcome") != "SUCCESS"]
    primary = min(
        attention,
        key=lambda stage: (
            _OPERATOR_OUTCOME_PRIORITY.get(stage.get("outcome"), 99),
            stage.get("lane", ""),
            stage.get("stage", ""),
        ),
        default=None,
    )
    if overall == "HEALTHY":
        message = "目前沒有需要處理的健康告警。"
    elif primary is None:
        message = "處理鏈狀態尚待核對，不能視為成功。"
    elif overall == "DEGRADED" and primary.get("lane") != "publication":
        message = "發布資料鏈仍可用，但下游服務需要處理。"
    elif overall == "BLOCKED":
        message = "處理鏈已阻塞，請先處理主要失敗階段。"
    elif overall == "STALE":
        message = "資料仍可讀取但已過期，請確認來源新鮮度。"
    elif overall == "PARTIAL":
        message = "處理鏈不完整，請先處理主要缺口。"
    else:
        message = "處理鏈狀態尚待核對，不能視為成功。"
    return {
        "status": overall,
        "requires_attention": overall != "HEALTHY",
        "message": message,
        "primary_stage": (
            {
                "lane": primary.get("lane"),
                "stage": primary.get("stage"),
                "outcome": primary.get("outcome"),
                "error_class": primary.get("error_class"),
            }
            if primary is not None else None
        ),
        "attention_count": len(attention),
    }


def latency_ms(start: Any, end: Any) -> int | None:
    left = parse_time(start)
    right = parse_time(end)
    if left is None or right is None or left.tzinfo is None or right.tzinfo is None:
        return None
    delta = int((right - left).total_seconds() * 1000)
    # Two endpoints carrying the same instant are indistinguishable from a
    # reused generation timestamp, so the latency stays UNKNOWN rather than 0.
    return delta if delta > 0 else None


CHAIN_TIMESTAMP_SOURCES = {
    "source_published_at": (),
    "detected_at": ("collection",),
    "verified_at": ("fusion_verification", "canonical_validation"),
    "published_at": ("deployment", "publication_build"),
    "public_visible_at": ("public_http_verification",),
}


def chain_timestamps(stages: list[dict[str, Any]]) -> dict[str, Any]:
    """Derive the latency chain from real stage receipts.

    `source_published_at` has no stage, so it is never synthesised here; a source
    without a published timestamp keeps source→detect latency UNKNOWN.
    """
    ended: dict[str, Any] = {}
    for stage in stages:
        if isinstance(stage, dict) and stage.get("outcome") not in {"UNKNOWN", "SKIPPED", None}:
            ended[str(stage.get("stage"))] = stage.get("ended_at")
    times: dict[str, Any] = {}
    for key, candidates in CHAIN_TIMESTAMP_SOURCES.items():
        times[key] = next(
            (ended[name] for name in candidates if ended.get(name) is not None),
            None,
        )
    return times


def max_stage_time(stages: list[dict[str, Any]]) -> str | None:
    """Latest real observation across stage receipts — deterministic, never now()."""
    candidates: list[tuple[datetime, str]] = []
    for stage in stages:
        for field in ("ended_at", "last_success_at"):
            value = stage.get(field) if isinstance(stage, dict) else None
            parsed = parse_time(value)
            if parsed is not None and parsed.tzinfo is not None:
                candidates.append((parsed, str(value)))
    if not candidates:
        return None
    return max(candidates, key=lambda row: row[0])[1]


def normalize_stage(stage: dict[str, Any]) -> dict[str, Any]:
    """Every published stage carries last-success and generation/hash linkage."""
    normalized = dict(stage)
    for field in STAGE_LINKAGE_FIELDS:
        normalized.setdefault(field, None)
    normalized["error_stage"] = (
        normalized["stage"] if normalized["outcome"] != "SUCCESS" else None
    )
    return normalized


def _metric(unit: str, value: Any, reason: str) -> dict[str, Any]:
    measured = isinstance(value, (int, float)) and not isinstance(value, bool)
    return {
        "value": value if measured else None,
        "unit": unit,
        "measured": measured,
        "reason": None if measured else reason,
    }


def slo_metrics(
    sources: Any,
    latency: dict[str, Any] | None = None,
    query_receipt: Any = None,
    generated_at: Any = None,
    stages: list[dict[str, Any]] | None = None,
    source_policy: Any = None,
) -> dict[str, Any]:
    """Measure the first-release SLO fields; unknown stays unknown."""
    latency = latency if isinstance(latency, dict) else {}
    query = query_receipt if isinstance(query_receipt, dict) else {}
    rows = [row for row in sources if isinstance(row, dict)] if isinstance(sources, list) else []
    total = len(rows)
    complete = [
        index for index, row in enumerate(rows)
        if row.get("source_health") == "PASS"
        and row.get("window_completeness") in COMPLETE_SOURCE_WINDOWS
    ]
    stale = [
        index for index, row in enumerate(rows)
        if normalize_source_freshness(row.get("freshness_status"), source_policy) in STALE_SOURCE_STATES
    ]
    partial = [
        index for index, row in enumerate(rows)
        if row.get("window_completeness") not in COMPLETE_SOURCE_WINDOWS
    ]
    no_sources = "NO_SOURCE_RECEIPTS"

    # Publication mismatch is an artifact disagreement, not a collection gap, so
    # it is counted from the publication-lane receipts the chain produced.
    publication_stages = [
        row for row in (stages or [])
        if isinstance(row, dict) and row.get("lane") == "publication"
    ]
    mismatched = [
        row for row in publication_stages if row.get("error_class") in MISMATCH_ERROR_CLASSES
    ]

    reference = parse_time(generated_at)
    ages: int | None = None
    if reference is None or reference.tzinfo is None or not total:
        ages = None
    else:
        observed = [parse_time(row.get("last_success_at")) for row in rows]
        if all(value is not None and value.tzinfo is not None for value in observed):
            candidate = max(int((reference - value).total_seconds() * 1000) for value in observed)
            ages = candidate if candidate >= 0 else None

    indexed = query.get("indexed_at")
    published = query.get("published_at")
    query_total = query.get("query_total")
    query_errors = query.get("query_errors")
    error_rate = (
        round(query_errors / query_total, 6)
        if isinstance(query_total, int) and isinstance(query_errors, int)
        and not isinstance(query_total, bool) and not isinstance(query_errors, bool)
        and query_total > 0 and 0 <= query_errors <= query_total
        else None
    )

    values = {
        "collection_success_ratio": (len(complete) / total if total else None, no_sources),
        "source_freshness_age_ms": (ages, "NO_RELIABLE_FRESHNESS_TIMESTAMPS"),
        "stale_source_ratio": (len(stale) / total if total else None, no_sources),
        "partial_source_ratio": (len(partial) / total if total else None, no_sources),
        "publication_mismatch_count": (
            len(mismatched) if publication_stages else None,
            "NO_PUBLICATION_STAGE_RECEIPTS",
        ),
        "source_to_detect_ms": (latency.get("source_to_detect_ms"), "NO_RELIABLE_STAGE_TIMESTAMPS"),
        "detect_to_verify_ms": (latency.get("detect_to_verify_ms"), "NO_RELIABLE_STAGE_TIMESTAMPS"),
        "verify_to_publish_ms": (latency.get("verify_to_publish_ms"), "NO_RELIABLE_STAGE_TIMESTAMPS"),
        "publish_to_public_visible_ms": (latency.get("publish_to_visible_ms"), "NO_RELIABLE_STAGE_TIMESTAMPS"),
        "query_index_lag_ms": (latency_ms(published, indexed), "NO_QUERY_INDEX_RECEIPT"),
        "query_error_rate": (error_rate, "NO_QUERY_ERROR_COUNTS"),
    }
    units = dict(SLO_METRIC_FIELDS)
    return {
        "status": "MEASUREMENT_ONLY",
        "thresholds": None,
        "note": "第一版只量測欄位，不承諾未驗證的 SLA 門檻。",
        "metrics": {
            name: _metric(units[name], values.get(name, (None, "NOT_MEASURED"))[0], values.get(name, (None, "NOT_MEASURED"))[1])
            for name, _ in SLO_METRIC_FIELDS
        },
    }


def build_health(
    stages: list[dict[str, Any]],
    timestamps: dict[str, Any] | None = None,
    *,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extra = context if isinstance(context, dict) else {}
    seen: set[tuple[str, str]] = set()
    for stage in stages:
        validate_stage(stage)
        key = (stage["lane"], stage["stage"])
        if key in seen:
            raise ValueError(f"duplicate stage receipt: {key[0]}/{key[1]}")
        seen.add(key)

    grouped = {lane: [stage for stage in stages if stage["lane"] == lane] for lane in sorted(VALID_LANES)}
    lanes = {lane: lane_health(lane, rows) for lane, rows in grouped.items()}
    upstream = upstream_context(extra.get("upstream_receipt"))
    if not any(row["stage"] == upstream["stage"] for row in stages):
        # The upstream operating state is part of every receipt; a caller that
        # already supplied one keeps its own measurement.
        stages = list(stages) + [upstream_operating_state_stage(extra.get("upstream_receipt"))]
        lanes = {lane: lane_health(lane, [row for row in stages if row["lane"] == lane]) for lane in sorted(VALID_LANES)}
    times = chain_timestamps(stages) if timestamps is None else timestamps
    metrics = {
        "source_to_detect_ms": latency_ms(times.get("source_published_at"), times.get("detected_at")),
        "detect_to_verify_ms": latency_ms(times.get("detected_at"), times.get("verified_at")),
        "verify_to_publish_ms": latency_ms(times.get("verified_at"), times.get("published_at")),
        "publish_to_visible_ms": latency_ms(times.get("published_at"), times.get("public_visible_at")),
    }
    normalized_stages = [
        normalize_stage(stage)
        for stage in sorted(stages, key=lambda row: (row["lane"], row["stage"]))
    ]
    overall = overall_health(lanes)
    return {
        "schema_version": 1,
        "stage_model": stage_model(),
        "generated_at": extra.get("generated_at") or max_stage_time(normalized_stages),
        "overall": overall,
        "lanes": lanes,
        "stages": normalized_stages,
        "latency_metrics": metrics,
        "upstream": upstream,
        "slo": slo_metrics(extra.get("sources"), metrics, extra.get("query_receipt"),
                           extra.get("generated_at") or max_stage_time(normalized_stages),
                           normalized_stages, extra.get("source_policy")),
        "operator_summary": operator_summary(overall, normalized_stages),
    }



def _source_coverage(sources: Any, required_source_ids: set[str]) -> tuple[list[dict[str, Any]], bool]:
    if not isinstance(sources, list) or not sources or any(not isinstance(source, dict) for source in sources):
        return [], False
    source_ids = [source.get("source_id") for source in sources]
    if any(not isinstance(source_id, str) or not source_id for source_id in source_ids):
        return sources, False
    exact = len(source_ids) == len(set(source_ids)) == len(required_source_ids) and set(source_ids) == required_source_ids
    return sources, exact


def aggregate_last_success_at(sources: list[dict[str, Any]]) -> str | None:
    values = [source.get("last_success_at") for source in sources]
    parsed = [parse_time(value) for value in values]
    if not sources or any(value is None for value in parsed):
        return None
    return values[parsed.index(min(parsed))]


def normalize_source_freshness(value: Any, source_policy: Any | None = None) -> str:
    if source_policy is not None:
        return source_policy.normalize_freshness(value)
    if value is None:
        return "UNKNOWN"
    normalized = str(value).strip().upper()
    return normalized or "UNKNOWN"


def current_publication_stages(
    status: dict[str, Any],
    brief: dict[str, Any],
    policy: dict[str, Any] | None = None,
    schema_drift: dict[str, Any] | None = None,
    upstream_receipt: dict[str, Any] | None = None,
    query_receipt: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if upstream_receipt is None:
        upstream_receipt = load_upstream_receipt()
    try:
        source_policy = load_source_policy()
        if policy is None:
            policy = source_policy.load_current_policy()
    except (OSError, ValueError, json.JSONDecodeError):
        source_policy = None
        if policy is None:
            policy = None
    required_source_ids = set(policy["active_source_ids"]) if policy else set()
    generated_at = status.get("generated_at")
    run = status.get("latest_collection_run") if isinstance(status.get("latest_collection_run"), dict) else {}
    run_id = run.get("collection_run_id")
    run_status = run.get("status")
    sources, exact_coverage = _source_coverage(status.get("sources"), required_source_ids)
    freshness_states = {
        normalize_source_freshness(source.get("freshness_status"), source_policy)
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
    collection_last_success_at = aggregate_last_success_at(sources)
    if collection_last_success_at is None and collect_outcome == "SUCCESS":
        collection_last_success_at = generated_at

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
    stages.append(upstream_operating_state_stage(upstream_receipt))
    stages.extend([
        {
            "lane": "publication",
            "stage": "collection",
            "outcome": collect_outcome,
            "generation_id": run_id,
            "ended_at": generated_at,
            "last_success_at": collection_last_success_at,
            "item_count": len(sources) if isinstance(status.get("sources"), list) else None,
            "gap_count": sum(1 for row in sources if row.get("source_health") != "PASS"
                             or row.get("window_completeness") not in COMPLETE_SOURCE_WINDOWS)
            if isinstance(status.get("sources"), list) else None,
            "error_class": collect_error,
            "receipt_ref": "apps/web/public/data/source-status.json",
            **binding,
        },
        {
            "lane": "publication",
            "stage": "parsing",
            "outcome": "UNKNOWN",
            "error_class": "NO_PARSING_RECEIPT_IN_CANONICAL_ARTIFACT",
            "receipt_ref": "apps/web/public/data/source-status.json",
            **binding,
        },
        {
            "lane": "publication",
            "stage": "fusion_verification",
            "outcome": "UNKNOWN",
            "error_class": "NO_FUSION_RECEIPT_IN_CANONICAL_ARTIFACT",
            "receipt_ref": "apps/web/public/data/public-event-demo.json",
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
            "receipt_ref": "apps/web/public/data/v2-daily-brief.json",
            **binding,
        },
        {
            "lane": "publication",
            "stage": "publication_build",
            "outcome": "UNKNOWN",
            "error_class": "NO_PUBLICATION_BUILD_RECEIPT_IN_CANONICAL_ARTIFACT",
            "receipt_ref": "apps/web/public/data/intelligence-feed.json",
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
            "generation_id": (query_receipt or {}).get("query_generation_id"),
            "ended_at": (query_receipt or {}).get("verified_at"),
            "error_class": "QUERY_INDEX_NOT_YET_WIRED",
            **binding,
        },
        {
            "lane": "query",
            "stage": "read_only_mcp",
            "outcome": "UNKNOWN",
            "error_class": "READ_ONLY_MCP_NOT_YET_WIRED",
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
    review_items = load_review_inbox(DEFAULT_REVIEW_STATE, schema_drift, status)
    upstream_receipt = load_upstream_receipt()
    query_receipt = load_query_receipt()
    try:
        source_policy = load_source_policy()
    except (OSError, ValueError, json.JSONDecodeError):
        source_policy = None
    result = build_health(
        current_publication_stages(status, brief, policy, schema_drift, upstream_receipt, query_receipt),
        context={
            "sources": status.get("sources"),
            "upstream_receipt": upstream_receipt,
            "query_receipt": query_receipt,
            "source_policy": source_policy,
        },
    )
    result["policy"] = policy_binding(policy) if policy else {"policy_status": "UNKNOWN"}
    result["schema_drift"] = {
        "overall": schema_drift.get("overall") if schema_drift else "UNKNOWN",
        "source_count": len(schema_drift.get("sources", [])) if schema_drift else 0,
    }
    result["review_inbox"] = review_items if review_items is not None else (schema_drift.get("review_inbox", []) if schema_drift else [])
    result["review_inbox_total"] = len(result["review_inbox"])
    return result


def self_check() -> None:
    healthy_publication = [
        {"lane": "publication", "stage": "collection", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "canonical_validation", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "deployment", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "public_http_verification", "outcome": "SUCCESS"},
    ]
    query_failed = [{"lane": "query", "stage": "query_index", "outcome": "FAILED", "error_class": "QUERY_INDEX_BUILD_FAILED"}]
    result = build_health(healthy_publication + query_failed)
    assert result["lanes"]["publication"] == "HEALTHY"
    assert result["lanes"]["query"] == "BLOCKED"
    assert result["overall"] == "DEGRADED"
    assert result["stage_model"]["version"] == STAGE_MODEL_VERSION
    assert all(slo["thresholds"] is None for slo in [result["slo"]])

    incomplete = build_health([{"lane": "publication", "stage": "collection", "outcome": "SUCCESS"}])
    assert incomplete["lanes"]["publication"] == "UNKNOWN"
    assert incomplete["overall"] == "UNKNOWN"

    blocked = build_health([
        {"lane": "publication", "stage": "collection", "outcome": "SUCCESS"},
        {"lane": "publication", "stage": "canonical_validation", "outcome": "SUCCESS"},
        failure_stage_receipt("PROTECTED_BRANCH_PUSH_REJECTED"),
    ])
    assert blocked["overall"] == "BLOCKED"
    assert blocked["operator_summary"]["primary_stage"]["stage"] == "deployment"

    paused = build_health(
        healthy_publication + [
            {"lane": "query", "stage": "query_index", "outcome": "SUCCESS"},
            {"lane": "query", "stage": "mcp_web_query", "outcome": "SUCCESS"},
        ],
        context={"upstream_receipt": {"upstream_operating_state": "PAUSED", "upstream_current": False}},
    )
    assert paused["lanes"]["publication"] == "HEALTHY"
    assert paused["upstream"]["overrides_govintel_health"] is False
    assert paused["overall"] == "DEGRADED"
    print("SYSTEM_HEALTH_SELF_CHECK_OK query_failure_preserves_publication=true publish_failure_blocks=true missing_stage_unknown=true upstream_never_overrides=true")



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    parser.add_argument("--input", type=Path, help="Optional explicit stage-receipt JSON")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def write_if_changed(path: Path, text: str) -> bool:
    try:
        # Text mode normalizes CRLF from a Windows Git checkout to LF.
        if path.read_text(encoding="utf-8") == text:
            return False
    except FileNotFoundError:
        pass
    path.write_text(text, encoding="utf-8")
    return True


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
        write_if_changed(args.output, text)
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
