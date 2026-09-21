"""Deterministic, local-first Review Inbox state and audit operations."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


REVIEW_REASONS = frozenset(
    {
        "NEEDS_REVIEW",
        "CONFLICT",
        "DISCOVERY_UNVERIFIED",
        "MERGE_CANDIDATE",
        "SPLIT_REQUIRED",
        "STALE_SOURCE",
        "PARTIAL_SOURCE",
    }
)
STATUSES = frozenset({"OPEN", "CLAIMED", "RESOLVED", "DISMISSED"})
DECISIONS = frozenset({"CONFIRM", "MERGE", "SPLIT", "DISMISS", "KEEP_WATCHING", "RESOLVE", "CORRECT_MAPPING"})
PRIORITIES = {
    "CONFLICT": 100,
    "SPLIT_REQUIRED": 100,
    "NEEDS_REVIEW": 90,
    "PARTIAL_SOURCE": 80,
    "STALE_SOURCE": 70,
    "DISCOVERY_UNVERIFIED": 60,
    "MERGE_CANDIDATE": 50,
}
ACTIVE_STATUSES = frozenset({"OPEN", "CLAIMED"})
TERMINAL_STATUSES = frozenset({"RESOLVED", "DISMISSED"})
PUBLIC_ENTITY_ID_KEYS = frozenset({"source_id", "event_id", "document_id", "candidate_id", "public_event_id", "entity_id"})


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def timestamp(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed.isoformat(timespec="seconds")


def empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "mode": "REVIEW_INBOX", "items": {}, "last_updated_at": None}


def review_id_for(fingerprint: str) -> str:
    if not isinstance(fingerprint, str) or not fingerprint.strip():
        raise ValueError("review fingerprint is required")
    return f"REVIEW-{sha256(fingerprint.strip().encode('utf-8')).upper()[:20]}"


def _audit(item: dict[str, Any], action: str, at: str, payload: dict[str, Any]) -> dict[str, Any]:
    sequence = len(item.get("audit", [])) + 1
    material = {"review_id": item["review_id"], "sequence": sequence, "action": action, "at": at, "payload": payload}
    return {"audit_id": f"AUDIT-{sha256(material).upper()[:20]}", **material}


def _validate_audit(review_id: str, sequence: int, audit: Any) -> None:
    if not isinstance(audit, dict) or audit.get("review_id") != review_id:
        raise ValueError(f"invalid audit receipt: {review_id}")
    if audit.get("sequence") != sequence or not isinstance(audit.get("action"), str) or not audit["action"]:
        raise ValueError(f"invalid audit sequence: {review_id}")
    timestamp(audit.get("at"))
    if not isinstance(audit.get("payload"), dict):
        raise ValueError(f"invalid audit payload: {review_id}")
    material = {"review_id": review_id, "sequence": sequence, "action": audit["action"], "at": audit["at"], "payload": audit["payload"]}
    if audit.get("audit_id") != f"AUDIT-{sha256(material).upper()[:20]}":
        raise ValueError(f"audit receipt hash mismatch: {review_id}")


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("review state must be an object")
    if state.get("schema_version") != 1 or state.get("mode") != "REVIEW_INBOX":
        raise ValueError("review state must use schema_version=1 and mode=REVIEW_INBOX")
    items = state.get("items")
    if not isinstance(items, dict):
        raise ValueError("review items must be an object")
    for review_id, item in items.items():
        if not isinstance(item, dict) or item.get("review_id") != review_id:
            raise ValueError("review IDs must be stable object keys")
        if item.get("reason") not in REVIEW_REASONS or item.get("status") not in STATUSES:
            raise ValueError(f"invalid review item: {review_id}")
        if item.get("fingerprint") != str(item.get("fingerprint") or ""):
            raise ValueError(f"review fingerprint is required: {review_id}")
        timestamp(item.get("created_at"))
        timestamp(item.get("updated_at"))
        if not isinstance(item.get("entity_ids"), dict) or not isinstance(item.get("evidence"), dict):
            raise ValueError(f"review item requires entity_ids and evidence: {review_id}")
        if item.get("evidence_sha256") != sha256(item["evidence"]):
            raise ValueError(f"evidence hash mismatch: {review_id}")
        history = item.get("evidence_history")
        if not isinstance(history, list):
            raise ValueError(f"review evidence history is required: {review_id}")
        for entry in history:
            if not isinstance(entry, dict) or not isinstance(entry.get("evidence"), dict):
                raise ValueError(f"invalid evidence history: {review_id}")
            timestamp(entry.get("observed_at"))
            if entry.get("evidence_sha256") != sha256(entry["evidence"]):
                raise ValueError(f"evidence history hash mismatch: {review_id}")
        if not isinstance(item.get("audit"), list) or not item["audit"]:
            raise ValueError(f"review item requires audit history: {review_id}")
        for sequence, audit in enumerate(item["audit"], start=1):
            _validate_audit(review_id, sequence, audit)
        decision = item.get("decision")
        if decision is not None:
            if not isinstance(decision, dict) or decision.get("decision") not in DECISIONS:
                raise ValueError(f"invalid review decision: {review_id}")
            if not isinstance(decision.get("reviewer_ref"), str) or not decision["reviewer_ref"].strip():
                raise ValueError(f"review decision reviewer is required: {review_id}")
            timestamp(decision.get("decided_at"))
            if not isinstance(decision.get("evidence"), dict) or decision.get("evidence_sha256") != sha256(decision["evidence"]):
                raise ValueError(f"review decision evidence hash mismatch: {review_id}")
            version_receipt = decision.get("version_receipt")
            if version_receipt is not None and (
                not isinstance(version_receipt, dict)
                or version_receipt.get("source_version") != item.get("source_version")
                or version_receipt.get("evidence_sha256") != item["evidence_sha256"]
            ):
                raise ValueError(f"review decision version receipt mismatch: {review_id}")
    if state.get("last_updated_at") is not None:
        timestamp(state["last_updated_at"])
    return state


def copy_state(state: dict[str, Any] | None) -> dict[str, Any]:
    return validate_state(copy.deepcopy(state) if state is not None else empty_state())


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    return copy_state(json.loads(path.read_text(encoding="utf-8")))


def _candidate(candidate: dict[str, Any], observed_at: str) -> dict[str, Any]:
    if not isinstance(candidate, dict) or candidate.get("reason") not in REVIEW_REASONS:
        raise ValueError("review candidate has an unsupported reason")
    reason = candidate["reason"]
    entity_ids = candidate.get("entity_ids") if isinstance(candidate.get("entity_ids"), dict) else {}
    fingerprint = str(candidate.get("fingerprint") or sha256({"reason": reason, "entity_ids": entity_ids}))
    evidence = copy.deepcopy(candidate.get("evidence") if isinstance(candidate.get("evidence"), dict) else {})
    return {
        "reason": reason,
        "fingerprint": fingerprint,
        "entity_ids": copy.deepcopy(entity_ids),
        "evidence": evidence,
        "observed_at": timestamp(str(candidate.get("observed_at") or observed_at)),
        "source_version": candidate.get("source_version"),
        "priority_reason": str(candidate.get("priority_reason") or f"{reason}_REQUIRES_HUMAN_REVIEW"),
    }


def upsert(state: dict[str, Any] | None, candidate: dict[str, Any], *, observed_at: str) -> tuple[dict[str, Any], dict[str, Any], bool]:
    result = copy_state(state)
    stamp = timestamp(observed_at)
    value = _candidate(candidate, stamp)
    review_id = review_id_for(value["fingerprint"])
    evidence_hash = sha256(value["evidence"])
    existing = result["items"].get(review_id)
    if existing is None:
        item = {
            "review_id": review_id,
            "fingerprint": value["fingerprint"],
            "reason": value["reason"],
            "status": "OPEN",
            "priority": PRIORITIES[value["reason"]],
            "priority_reason": value["priority_reason"],
            "entity_ids": value["entity_ids"],
            "evidence": value["evidence"],
            "evidence_sha256": evidence_hash,
            "source_version": value["source_version"],
            "created_at": stamp,
            "updated_at": stamp,
            "assignment": {"state": "UNASSIGNED", "assignee_ref": None, "claimed_at": None},
            "decision": None,
            "evidence_history": [],
            "audit": [],
        }
        item["audit"].append(_audit(item, "CREATED", stamp, {"reason": item["reason"], "evidence_sha256": evidence_hash}))
        result["items"][review_id] = item
        result["last_updated_at"] = stamp
        return result, item, True

    changed = existing.get("evidence_sha256") != evidence_hash or existing.get("source_version") != value["source_version"]
    if changed:
        old = {"evidence": existing.get("evidence"), "evidence_sha256": existing.get("evidence_sha256"), "source_version": existing.get("source_version")}
        existing["evidence_history"].append({"observed_at": stamp, **old})
        existing["evidence"] = value["evidence"]
        existing["evidence_sha256"] = evidence_hash
        existing["source_version"] = value["source_version"]
        existing["entity_ids"] = value["entity_ids"] or existing["entity_ids"]
        existing["updated_at"] = stamp
        action = "REOPENED" if existing["status"] in TERMINAL_STATUSES else "UPDATED"
        if action == "REOPENED":
            existing["status"] = "OPEN"
            existing["assignment"] = {"state": "UNASSIGNED", "assignee_ref": None, "claimed_at": None}
            existing["decision"] = None
        existing["audit"].append(_audit(existing, action, stamp, {"evidence_sha256": evidence_hash, "source_version": value["source_version"]}))
        result["last_updated_at"] = stamp
    return result, existing, changed


def reconcile(state: dict[str, Any] | None, candidates: Iterable[dict[str, Any]], *, observed_at: str) -> dict[str, Any]:
    result = copy_state(state)
    for candidate in candidates:
        result, _, _ = upsert(result, candidate, observed_at=observed_at)
    return result


def claim(state: dict[str, Any] | None, review_id: str, *, assignee_ref: str, claimed_at: str) -> dict[str, Any]:
    result = copy_state(state)
    item = result["items"].get(review_id)
    if item is None:
        raise ValueError(f"unknown review ID: {review_id}")
    if not assignee_ref.strip():
        raise ValueError("assignee_ref is required")
    stamp = timestamp(claimed_at)
    if item["status"] == "CLAIMED" and item["assignment"].get("assignee_ref") == assignee_ref:
        return result
    if item["status"] not in ACTIVE_STATUSES:
        raise ValueError(f"review is not active: {review_id}")
    item["status"] = "CLAIMED"
    item["assignment"] = {"state": "CLAIMED", "assignee_ref": assignee_ref, "claimed_at": stamp}
    item["updated_at"] = stamp
    item["audit"].append(_audit(item, "CLAIMED", stamp, {"assignee_ref": assignee_ref}))
    result["last_updated_at"] = stamp
    return result


def decide(state: dict[str, Any] | None, review_id: str, decision: str, *, reviewer_ref: str, decided_at: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    result = copy_state(state)
    item = result["items"].get(review_id)
    decision = decision.upper().replace("-", "_")
    if item is None:
        raise ValueError(f"unknown review ID: {review_id}")
    if decision not in DECISIONS:
        raise ValueError(f"unsupported review decision: {decision}")
    if not reviewer_ref.strip():
        raise ValueError("reviewer_ref is required")
    if item["status"] not in ACTIVE_STATUSES:
        raise ValueError(f"review is not active: {review_id}")
    stamp = timestamp(decided_at)
    decision_evidence = copy.deepcopy(evidence or {})
    outcome = "DISMISSED" if decision == "DISMISS" else "OPEN" if decision == "KEEP_WATCHING" else "RESOLVED"
    item["status"] = outcome
    item["decision"] = {
        "decision": decision,
        "reviewer_ref": reviewer_ref,
        "decided_at": stamp,
        "evidence": decision_evidence,
        "evidence_sha256": sha256(decision_evidence),
        "version_receipt": {
            "source_version": item["source_version"],
            "evidence_sha256": item["evidence_sha256"],
        },
    }
    item["assignment"] = {"state": "UNASSIGNED", "assignee_ref": None, "claimed_at": None}
    item["updated_at"] = stamp
    item["audit"].append(_audit(item, "DECISION", stamp, {
        "decision": decision,
        "reviewer_ref": reviewer_ref,
        "evidence_sha256": sha256(decision_evidence),
        "version_receipt": item["decision"]["version_receipt"],
    }))
    result["last_updated_at"] = stamp
    return result


def project(state: dict[str, Any] | None, *, include_closed: bool = False, public: bool = False) -> list[dict[str, Any]]:
    candidate = copy_state(state)
    rows = [copy.deepcopy(item) for item in candidate["items"].values() if include_closed or item["status"] in ACTIVE_STATUSES]
    rows.sort(key=lambda item: (-item["priority"], item["updated_at"], item["review_id"]))
    if public:
        return [
            {
                key: item[key]
                for key in ("review_id", "reason", "status", "priority", "source_version", "evidence_sha256", "created_at", "updated_at")
            }
            | {
                "priority_reason": f"{item['reason']}_REQUIRES_HUMAN_REVIEW",
                "entity_ids": {key: value for key, value in item["entity_ids"].items() if key in PUBLIC_ENTITY_ID_KEYS and isinstance(value, str) and value.strip()},
            }
            for item in rows
        ]
    return rows


def schema_drift_candidates(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    rows = receipt.get("review_inbox", []) if isinstance(receipt, dict) else []
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("source_id"):
            continue
        status = row.get("status")
        reason = "PARTIAL_SOURCE" if status == "SOURCE_UNAVAILABLE" else "NEEDS_REVIEW"
        candidates.append(
            {
                "fingerprint": f"schema-drift:{row['source_id']}",
                "reason": reason,
                "entity_ids": {"source_id": row["source_id"]},
                "observed_at": row.get("observed_at") or receipt.get("generated_at"),
                "evidence": {"before": row.get("last_known_good"), "after": {"status": status, "reasons": row.get("reasons", []), "observed_at": row.get("observed_at")}},
                "priority_reason": "官方來源契約／可用性需要人工覆核",
            }
        )
    return candidates


def detail_recheck_candidates(outcomes: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert material detail changes into local Review Inbox candidates."""
    if not isinstance(outcomes, Iterable) or isinstance(outcomes, (str, bytes, dict)):
        raise ValueError("detail rechecks must be an array")
    candidates = []
    for row in outcomes:
        if not isinstance(row, dict):
            raise ValueError("detail recheck outcome must be an object")
        classification = row.get("classification")
        if not isinstance(classification, dict):
            raise ValueError("detail recheck classification is required")
        if not classification.get("review_required"):
            continue
        source_id = row.get("source_id")
        stable_key = row.get("stable_key")
        after = classification.get("after")
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(stable_key, str) or not stable_key.strip():
            raise ValueError("detail recheck identity is required")
        if not isinstance(after, dict) or not isinstance(after.get("document_version_id"), str):
            raise ValueError("detail recheck after version is required")
        identity = f"{source_id}:{stable_key}"
        document_version = after["document_version_id"]
        candidates.append(
            {
                "fingerprint": f"detail-recheck:{identity}:{document_version}",
                "reason": "NEEDS_REVIEW",
                "entity_ids": {"source_id": source_id, "document_id": document_version},
                "source_version": document_version,
                "evidence": {
                    "status": classification.get("status"),
                    "changed_fields": list(classification.get("changed_fields") or []),
                    "before": classification.get("before"),
                    "after": after,
                },
                "priority_reason": "官方正文或附件版本變更，需要重新核對交班與引用",
            }
        )
    return candidates


def discovery_candidates(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Project unverified discovery outcomes without promoting them."""
    rows = result.get("candidates", []) if isinstance(result, dict) else []
    if not isinstance(rows, list):
        raise ValueError("discovery candidates must be an array")
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("candidate_id"), str):
            continue
        status = row.get("verification_status")
        if status == "CONFLICT":
            reason = "CONFLICT"
        elif status in {"DISCOVERY_UNVERIFIED", "NO_OFFICIAL_MATCH", "EXPIRED"}:
            reason = "DISCOVERY_UNVERIFIED"
        else:
            continue
        generation = row.get("upstream_generation_id")
        candidates.append(
            {
                "fingerprint": f"discovery:{row['candidate_id']}",
                "reason": reason,
                "entity_ids": {"candidate_id": row["candidate_id"]},
                "source_version": generation,
                "observed_at": row.get("observed_at"),
                "evidence": {
                    "verification_status": status,
                    "verification_reason": row.get("verification_reason"),
                    "official_document_versions": row.get("official_document_versions", []),
                    "source_url": row.get("source_url"),
                    "expires_at": row.get("expires_at"),
                },
                "priority_reason": "媒體／外部發現訊號尚未完成官方來源核對" if reason == "DISCOVERY_UNVERIFIED" else "官方來源對同一發現訊號的結果需要人工覆核",
            }
        )
    return candidates


def fusion_candidates(result: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Project PublicEvent conflict and identity decisions into review."""
    rows = result.get("public_events", []) if isinstance(result, dict) else result
    if not isinstance(rows, list):
        raise ValueError("public events must be an array")
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("public_event_id"), str):
            continue
        status = row.get("fusion_status")
        reason = {
            "CONFLICT": "CONFLICT",
            "SPLIT_REQUIRED": "SPLIT_REQUIRED",
            "CANDIDATE": "MERGE_CANDIDATE",
        }.get(status)
        if reason is None:
            continue
        candidates.append(
            {
                "fingerprint": f"fusion:{row['public_event_id']}",
                "reason": reason,
                "entity_ids": {"public_event_id": row["public_event_id"]},
                "source_version": row.get("updated_at"),
                "observed_at": row.get("updated_at") or row.get("created_at"),
                "evidence": {
                    "fusion_status": status,
                    "conflict_fields": row.get("conflict_fields", []),
                    "uncertain_fields": row.get("uncertain_fields", []),
                    "linked_document_versions": row.get("linked_document_versions", []),
                    "merged_from_public_event_ids": row.get("merged_from_public_event_ids", []),
                    "split_into_public_event_ids": row.get("split_into_public_event_ids", []),
                },
                "priority_reason": {
                    "CONFLICT": "官方事件欄位互相矛盾，需要人工判定保留版本",
                    "SPLIT_REQUIRED": "既有 PublicEvent 疑似誤合併，需要人工拆分",
                    "MERGE_CANDIDATE": "事件身份仍不足以自動合併，需要人工確認關聯",
                }[reason],
            }
        )
    return candidates


def source_health_candidates(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Project stale or incomplete source rows without closing prior review."""
    rows = receipt.get("sources", []) if isinstance(receipt, dict) else []
    if not isinstance(rows, list):
        raise ValueError("source status rows must be an array")
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("source_id"), str) or not row["source_id"].strip():
            continue
        freshness = str(row.get("freshness_status") or "UNKNOWN").upper()
        health = str(row.get("source_health") or "UNKNOWN").upper()
        completeness = str(row.get("window_completeness") or "UNKNOWN").upper()
        if freshness in {"STALE", "VERY_STALE"} and health == "PASS" and completeness in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}:
            reason = "STALE_SOURCE"
        elif health != "PASS" or completeness not in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"} or freshness not in {"FRESH", "RECENT"}:
            reason = "PARTIAL_SOURCE"
        else:
            continue
        source_id = row["source_id"]
        candidates.append(
            {
                "fingerprint": f"source-health:{source_id}",
                "reason": reason,
                "entity_ids": {"source_id": source_id},
                "source_version": row.get("current_source_run_id") or row.get("manifest_sha256"),
                "observed_at": row.get("last_checked_at") or receipt.get("generated_at"),
                "evidence": {
                    "before": row.get("last_known_good"),
                    "after": {
                        "source_health": row.get("source_health"),
                        "window_completeness": row.get("window_completeness"),
                        "freshness_status": row.get("freshness_status"),
                        "intelligence_gaps": row.get("intelligence_gaps", []),
                        "last_checked_at": row.get("last_checked_at"),
                    },
                },
                "priority_reason": "來源抓取失敗或窗口不完整，需要人工確認資料缺口" if reason == "PARTIAL_SOURCE" else "官方來源資料已過期，需要人工確認時效限制",
            }
        )
    return candidates


def runtime_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect bounded candidates from the supported runtime result envelopes."""
    if not isinstance(payload, dict):
        raise ValueError("review input must be an object")
    candidates = []
    if isinstance(payload.get("schema_drift"), dict):
        candidates.extend(schema_drift_candidates(payload["schema_drift"]))
    if "detail_rechecks" in payload:
        candidates.extend(detail_recheck_candidates(payload["detail_rechecks"]))
    if "candidates" in payload and any(
        isinstance(row, dict) and "verification_status" in row for row in (payload.get("candidates") or [])
    ):
        candidates.extend(discovery_candidates(payload))
    if "public_events" in payload:
        candidates.extend(fusion_candidates(payload))
    if "sources" in payload and any(
        isinstance(row, dict) and ("source_health" in row or "freshness_status" in row)
        for row in (payload.get("sources") or [])
    ):
        candidates.extend(source_health_candidates(payload))
    if candidates:
        return candidates
    rows = payload.get("items", payload.get("candidates", []))
    if not isinstance(rows, list):
        raise ValueError("input must contain items or candidates array")
    return rows
