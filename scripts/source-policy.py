#!/usr/bin/env python3
"""Compile GovIntel's source catalog into one deterministic source/query policy.

The catalog remains the only hand-maintained source inventory. This module does not
contain a parallel source-id list and never promotes/retire sources by itself. Changes
from a previous policy require explicit promotion/retirement receipts.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "docs/govintel/source-catalog.v2.json"
SCHEMA_VERSION = 1
VALID_STATUSES = {"PRODUCTION_ACTIVE", "AUDITED_EXISTING", "VERIFIED_CANDIDATE", "REFERENCE_ONLY"}
VALID_ROLES = {"PRIMARY_EVENT", "PRIMARY_REFERENCE", "ENRICHMENT", "DISCOVERY_ONLY"}
COMPLETE = {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
FRESHNESS_RECENT = {"FRESH", "RECENT"}
FRESHNESS_STALE = {"STALE", "VERY_STALE"}
FRESHNESS_UNKNOWN = "UNKNOWN"


def normalize_freshness(value: Any) -> str:
    """Normalize source freshness values before policy or health projection."""
    if value is None:
        return FRESHNESS_UNKNOWN
    normalized = str(value).strip().upper()
    return normalized or FRESHNESS_UNKNOWN


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def load_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_catalog(data)
    return data


def validate_catalog(catalog: dict[str, Any]) -> None:
    if not isinstance(catalog, dict) or catalog.get("schema_version") != 2:
        raise ValueError("unsupported source catalog schema")
    sources = catalog.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("catalog sources must be a nonempty array")
    seen: set[str] = set()
    for row in sources:
        if not isinstance(row, dict):
            raise ValueError("source row must be an object")
        source_id = row.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("source_id missing")
        if source_id in seen:
            raise ValueError(f"duplicate source_id: {source_id}")
        seen.add(source_id)
        if row.get("status") not in VALID_STATUSES:
            raise ValueError(f"unsupported status for {source_id}")
        if row.get("role") not in VALID_ROLES:
            raise ValueError(f"unsupported role for {source_id}")
        for field in ("name", "authority", "entrypoint"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                raise ValueError(f"{source_id} missing {field}")
        parsed = urlsplit(row["entrypoint"])
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"{source_id} entrypoint must be a credential-free HTTPS URL")


def _text(row: dict[str, Any]) -> str:
    return " ".join(str(row.get(key) or "") for key in ("source_id", "name", "authority", "role"))


def _all_active(_: dict[str, Any]) -> bool:
    return True


def _council(row: dict[str, Any]) -> bool:
    return "議會" in _text(row) or "質詢" in _text(row)


def _traffic_event(row: dict[str, Any]) -> bool:
    return row.get("role") == "PRIMARY_EVENT" and any(term in _text(row) for term in ("交通", "道路", "公車"))


def _fraud_reference(row: dict[str, Any]) -> bool:
    return row.get("role") in {"PRIMARY_REFERENCE", "PRIMARY_EVENT"} and any(term in _text(row) for term in ("反詐", "詐騙", "涉詐"))


CAPABILITIES: tuple[tuple[str, str, str, Callable[[dict[str, Any]], bool]], ...] = (
    ("publication_metadata", "目前核准來源的 publication metadata", "只代表 policy 中已啟用來源，不代表世界完整性。", _all_active),
    ("source_health", "目前核准來源的來源健康狀態", "健康來源不等於該問題領域具完整覆蓋。", _all_active),
    ("council_affairs", "議會／質詢相關公開資料", "只涵蓋 policy 中符合議會語意且已啟用的來源。", _council),
    ("traffic_events", "交通事件公開資料", "僅涵蓋已啟用且明確屬交通事件的來源；不代表所有臨時交通事件。", _traffic_event),
    ("fraud_reference", "反詐公開參考資料", "僅作已啟用官方資料範圍內的反詐參考，不推論本地案件量。", _fraud_reference),
)


def _project_source(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["source_id"],
        "name": row["name"],
        "authority": row["authority"],
        "role": row["role"],
        "status": row["status"],
        "entrypoint": row["entrypoint"],
        "cadence_class": row.get("cadence_class"),
    }


def _receipts_by_source(receipts: list[dict[str, Any]] | None, kind: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for receipt in receipts or []:
        if not isinstance(receipt, dict):
            raise ValueError(f"{kind} receipt must be an object")
        source_id = receipt.get("source_id")
        receipt_id = receipt.get("receipt_id")
        reason = receipt.get("reason")
        if not all(isinstance(v, str) and v.strip() for v in (source_id, receipt_id, reason)):
            raise ValueError(f"{kind} receipt requires source_id/receipt_id/reason")
        if source_id in result:
            raise ValueError(f"duplicate {kind} receipt for {source_id}")
        result[source_id] = {"source_id": source_id, "receipt_id": receipt_id, "reason": reason}
    return result


def compile_policy(
    catalog: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    promotions: list[dict[str, Any]] | None = None,
    retirements: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    validate_catalog(catalog)
    active_rows = [row for row in catalog["sources"] if row["status"] == "PRODUCTION_ACTIVE"]
    active_rows.sort(key=lambda row: row["source_id"])
    active_ids = [row["source_id"] for row in active_rows]
    if not active_rows:
        raise ValueError("policy cannot have zero active sources")

    promotion_receipts = _receipts_by_source(promotions, "promotion")
    retirement_receipts = _receipts_by_source(retirements, "retirement")
    version = 1
    transition = {"promoted": [], "retired": []}
    if previous is not None:
        validate_policy(previous)
        old_ids = set(previous["active_source_ids"])
        new_ids = set(active_ids)
        added = sorted(new_ids - old_ids)
        removed = sorted(old_ids - new_ids)
        if set(promotion_receipts) != set(added):
            raise ValueError("active-source additions require exact promotion receipts")
        if set(retirement_receipts) != set(removed):
            raise ValueError("active-source removals require exact retirement receipts")
        transition = {
            "promoted": [promotion_receipts[source_id] for source_id in added],
            "retired": [retirement_receipts[source_id] for source_id in removed],
        }
        version = previous["policy_version"] + (1 if added or removed else 0)
    elif promotion_receipts or retirement_receipts:
        raise ValueError("transition receipts require a previous policy")

    capability_rows = []
    for capability_id, label, limitation, selector in CAPABILITIES:
        required = sorted(row["source_id"] for row in active_rows if selector(row))
        optional = sorted(
            row["source_id"] for row in catalog["sources"]
            if row["status"] != "PRODUCTION_ACTIVE" and selector(row)
        )
        capability_rows.append({
            "capability_id": capability_id,
            "label": label,
            "supported": bool(required),
            "required_sources": required,
            "candidate_or_optional_sources": optional,
            "coverage_limitation": limitation,
        })

    core = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": version,
        "catalog_schema_version": catalog["schema_version"],
        "catalog_updated_at": catalog.get("updated_at"),
        "catalog_hash": digest(catalog),
        "active_source_ids": active_ids,
        "active_sources": [_project_source(row) for row in active_rows],
        "capabilities": capability_rows,
        "transition": transition,
        "rules": {
            "zero_result_scope": "BOUNDED_TO_REQUIRED_SOURCES_ONLY",
            "candidate_sources_are_verified": False,
            "runtime_may_drop_failed_required_source": False,
        },
    }
    return {**core, "policy_hash": digest(core)}


def validate_policy(policy: dict[str, Any]) -> None:
    if not isinstance(policy, dict) or policy.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported policy schema")
    if type(policy.get("policy_version")) is not int or policy["policy_version"] < 1:
        raise ValueError("invalid policy_version")
    active = policy.get("active_source_ids")
    if (
        not isinstance(active, list)
        or not active
        or not all(isinstance(source_id, str) and source_id for source_id in active)
        or active != sorted(active)
        or len(active) != len(set(active))
    ):
        raise ValueError("invalid active_source_ids")
    supplied = policy.get("policy_hash")
    actual = digest({key: value for key, value in policy.items() if key != "policy_hash"})
    if supplied != actual:
        raise ValueError("policy hash mismatch")
    active_sources = policy.get("active_sources")
    if (
        not isinstance(active_sources, list)
        or not all(isinstance(row, dict) for row in active_sources)
        or [row.get("source_id") for row in active_sources] != active
    ):
        raise ValueError("active_sources do not match active_source_ids")
    for row in active_sources:
        if (
            not isinstance(row, dict)
            or row.get("status") != "PRODUCTION_ACTIVE"
            or row.get("role") not in VALID_ROLES
            or not isinstance(row.get("entrypoint"), str)
            or urlsplit(row["entrypoint"]).scheme != "https"
        ):
            raise ValueError("invalid active source projection")
    capabilities = policy.get("capabilities")
    expected_capability_ids = [row[0] for row in CAPABILITIES]
    if (
        not isinstance(capabilities, list)
        or not all(isinstance(row, dict) for row in capabilities)
        or [row.get("capability_id") for row in capabilities] != expected_capability_ids
    ):
        raise ValueError("invalid policy capabilities")
    for capability in capabilities:
        required = capability.get("required_sources")
        optional = capability.get("candidate_or_optional_sources")
        if (
            not isinstance(required, list)
            or not all(isinstance(source_id, str) and source_id for source_id in required)
            or required != sorted(required)
            or len(required) != len(set(required))
            or not set(required) <= set(active)
            or not isinstance(optional, list)
            or not all(isinstance(source_id, str) and source_id for source_id in optional)
            or optional != sorted(optional)
            or len(optional) != len(set(optional))
            or set(required) & set(optional)
            or capability.get("supported") is not (bool(required))
        ):
            raise ValueError("invalid policy capability source binding")
def assess_query(policy: dict[str, Any], capability_id: str, source_states: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    validate_policy(policy)
    capability = next((row for row in policy["capabilities"] if row["capability_id"] == capability_id), None)
    if capability is None or not capability["supported"]:
        return {
            "status": "CAPABILITY_NOT_AVAILABLE",
            "policy_version": policy["policy_version"],
            "policy_hash": policy["policy_hash"],
            "capability_id": capability_id,
            "required_sources": [] if capability is None else capability["required_sources"],
            "missing_required_sources": [],
            "can_state_bounded_no_match": False,
            "coverage_limitations": ["目前 source policy 沒有足以支援此能力的已啟用來源。"],
        }
    required = capability["required_sources"]
    if source_states is None:
        return {
            "status": "POLICY_ONLY",
            "policy_version": policy["policy_version"],
            "policy_hash": policy["policy_hash"],
            "capability_id": capability_id,
            "required_sources": required,
            "missing_required_sources": required,
            "can_state_bounded_no_match": False,
            "coverage_limitations": [capability["coverage_limitation"], "尚未提供本輪來源執行狀態。"],
        }
    missing = [source_id for source_id in required if source_id not in source_states]
    bad = []
    stale = []
    for source_id in required:
        state = source_states.get(source_id)
        if state is None:
            continue
        if state.get("source_health") != "PASS" or state.get("window_completeness") not in COMPLETE:
            bad.append(source_id)
        freshness = normalize_freshness(state.get("freshness", state.get("freshness_status")))
        if freshness in FRESHNESS_STALE:
            stale.append(source_id)
        elif freshness not in FRESHNESS_RECENT:
            bad.append(source_id)
    if missing or bad:
        status = "PARTIAL"
    elif stale:
        status = "STALE"
    else:
        status = "COVERED_BOUNDED_SCOPE"
    return {
        "status": status,
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "capability_id": capability_id,
        "required_sources": required,
        "missing_required_sources": sorted(set(missing + bad)),
        "stale_required_sources": stale,
        "can_state_bounded_no_match": status == "COVERED_BOUNDED_SCOPE",
        "coverage_limitations": [capability["coverage_limitation"]],
    }


def self_check() -> None:
    catalog = load_catalog()
    policy = compile_policy(catalog)
    assert policy["active_source_ids"] == sorted(policy["active_source_ids"])
    assert assess_query(policy, "traffic_events")["status"] == "CAPABILITY_NOT_AVAILABLE"
    states = {source_id: {"source_health": "PASS", "window_completeness": "COMPLETE_WITH_ITEMS", "freshness": "RECENT"} for source_id in policy["active_source_ids"]}
    result = assess_query(policy, "publication_metadata", states)
    assert result["status"] == "COVERED_BOUNDED_SCOPE"
    assert result["can_state_bounded_no_match"] is True
    print(f"SOURCE_POLICY_SELF_CHECK_OK version={policy['policy_version']} active={len(policy['active_source_ids'])} hash={policy['policy_hash'][:12]}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        return 0
    policy = compile_policy(load_catalog(args.catalog))
    text = json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
