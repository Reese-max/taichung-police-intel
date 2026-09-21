from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


WATCH_STATUSES = {"WATCHING", "NEEDS_REVIEW", "RESOLVED", "DISMISSED"}
ACTIVE_WATCH_STATUSES = {"WATCHING", "NEEDS_REVIEW"}
HANDOFF_STATES = {"CONFIRMED"}
TZ = ZoneInfo("Asia/Taipei")


def empty_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "V2_HANDOFF",
        "watch_items": {},
        "handoffs": [],
        "last_updated_at": None,
    }


def _timestamp(value: datetime | str) -> str:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(TZ).isoformat(timespec="seconds")


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if not isinstance(value, dict):
        raise ValueError("handoff values must be objects")
    return value


def _sha(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def watch_id_for(identity: str) -> str:
    if not identity or ":" not in identity:
        raise ValueError("watch identity must be a canonical source:stable identity")
    return f"WATCH-{_sha(identity).upper()[:20]}"


def claim_id_for(identity: str, source_version: int) -> str:
    if not identity or ":" not in identity or not isinstance(source_version, int) or source_version < 1:
        raise ValueError("claim requires a canonical identity and positive source version")
    return f"CLAIM-{_sha([identity, source_version]).upper()[:20]}"


def _affected_claims(state: dict[str, Any], identity: str, source_version: int) -> list[dict[str, Any]]:
    rows = []
    for handoff in state["handoffs"]:
        for entry in handoff["items"]:
            if entry.get("identity") != identity or entry.get("source_version") != source_version:
                continue
            evidence = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
            rows.append(
                {
                    "brief_id": handoff["brief_id"],
                    "brief_version": handoff["brief_version"],
                    "claim_id": entry.get("claim_id") or claim_id_for(identity, source_version),
                    "source_version": source_version,
                    "source_document_version": entry.get("source_document_version"),
                    "evidence_locator": evidence.get("locator"),
                }
            )
    return sorted(rows, key=lambda row: (row["brief_version"], row["claim_id"]))


def validate_state(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("schema_version") != 1 or state.get("mode") != "V2_HANDOFF":
        raise ValueError("handoff state must use schema_version=1 and mode=V2_HANDOFF")
    watch_items = state.get("watch_items")
    if not isinstance(watch_items, dict):
        raise ValueError("handoff watch_items must be an object")
    for watch_id, watch in watch_items.items():
        if not isinstance(watch, dict) or watch.get("watch_id") != watch_id:
            raise ValueError("handoff watch IDs must be stable object keys")
        if not watch.get("identity") or watch.get("status") not in WATCH_STATUSES:
            raise ValueError(f"invalid handoff watch item: {watch_id}")
        if not watch.get("official_url", "").startswith("https://"):
            raise ValueError(f"handoff watch lacks HTTPS evidence URL: {watch_id}")
        if not isinstance(watch.get("invalidations", []), list):
            raise ValueError(f"handoff invalidations must be an array: {watch_id}")
    handoffs = state.get("handoffs")
    if not isinstance(handoffs, list):
        raise ValueError("handoff history must be an array")
    versions = []
    for handoff in handoffs:
        if not isinstance(handoff, dict) or handoff.get("confirmation_state") not in HANDOFF_STATES:
            raise ValueError("invalid confirmed handoff record")
        if not isinstance(handoff.get("items"), list) or not handoff.get("brief_id"):
            raise ValueError("handoff record requires brief_id and items")
        versions.append(handoff.get("brief_version"))
    if any(not isinstance(version, int) for version in versions):
        raise ValueError("handoff brief versions must be integers")
    if len(versions) != len(set(versions)):
        raise ValueError("handoff brief versions must be unique")
    return state


def copy_state(state: dict[str, Any] | None) -> dict[str, Any]:
    candidate = copy.deepcopy(state) if state is not None else empty_state()
    return validate_state(candidate)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_state()
    return copy_state(json.loads(path.read_text(encoding="utf-8")))


def add_watch(
    state: dict[str, Any] | None,
    item: dict[str, Any],
    *,
    created_at: datetime | str,
    reason_code: str = "MANUAL",
) -> tuple[dict[str, Any], dict[str, Any], bool]:
    result = copy_state(state)
    identity = str(item.get("identity") or "")
    official_url = str(item.get("official_url") or "")
    if not identity or not official_url.startswith("https://"):
        raise ValueError("watch requires canonical identity and HTTPS official_url")
    version = item.get("version_no")
    if not isinstance(version, int) or version < 1:
        raise ValueError("watch requires a positive source version")
    watch_id = watch_id_for(identity)
    existing = result["watch_items"].get(watch_id)
    if existing is not None:
        return result, existing, False
    stamp = _timestamp(created_at)
    watch = {
        "watch_id": watch_id,
        "identity": identity,
        "source_id": str(item.get("source_id") or ""),
        "stable_key": str(item.get("stable_key") or ""),
        "title": str(item.get("title") or "未命名官方資料"),
        "official_url": official_url,
        "created_at": stamp,
        "updated_at": stamp,
        "status": "WATCHING",
        "reason_code": reason_code.strip() or "MANUAL",
        "tracked_version": version,
        "tracked_sha256": str(item.get("normalized_sha256") or ""),
        "last_reviewed_version": None,
        "last_reviewed_sha256": None,
        "last_handoff_id": None,
        "invalidations": [],
    }
    result["watch_items"][watch_id] = watch
    result["last_updated_at"] = stamp
    return result, watch, True


def _event_dict(event: Any) -> dict[str, Any]:
    return _as_dict(event)


def sync_with_publication(
    state: dict[str, Any] | None,
    *,
    previous_items: dict[str, Any] | None,
    current_items: dict[str, Any],
    events: Iterable[Any],
    observed_at: datetime | str,
    collection_run_id: str | None,
) -> dict[str, Any]:
    result = copy_state(state)
    stamp = _timestamp(observed_at)
    previous_items = previous_items or {}
    event_by_identity: dict[str, dict[str, Any]] = {}
    for raw_event in events:
        event = _event_dict(raw_event)
        if not event.get("identity") or not event.get("publishable"):
            continue
        event_by_identity[event["identity"]] = event

    for watch in result["watch_items"].values():
        identity = watch["identity"]
        current = _as_dict(current_items[identity]) if identity in current_items else None
        event = event_by_identity.get(identity)
        previous_url = watch.get("official_url")
        current_version = current.get("version_no") if current else None
        tracked_version = watch.get("tracked_version") or 0
        after_version = event.get("after_version") if event else current_version
        changed = isinstance(after_version, int) and after_version > tracked_version
        if current and current.get("official_url", "").startswith("https://"):
            watch["title"] = current.get("title") or watch["title"]
            watch["official_url"] = current["official_url"]
            watch["source_id"] = current.get("source_id") or watch["source_id"]
            watch["stable_key"] = current.get("stable_key") or watch["stable_key"]
        if changed:
            after_hash = str((current or {}).get("normalized_sha256") or "")
            invalidation_id = "INV-" + _sha(
                [watch["watch_id"], event.get("event_id") if event else None, after_version, after_hash]
            ).upper()[:20]
            known = {entry.get("invalidation_id") for entry in watch["invalidations"]}
            if invalidation_id not in known:
                before = previous_items.get(identity) or {}
                watch["invalidations"].append(
                    {
                        "invalidation_id": invalidation_id,
                        "event_id": event.get("event_id") if event else None,
                        "reason": event.get("change_type") if event else "SOURCE_VERSION_CHANGED",
                        "detected_at": event.get("detected_at") if event else stamp,
                        "changed_fields": list(event.get("changed_fields") or []) if event else [],
                        "before": {
                            "version": tracked_version,
                            "normalized_sha256": watch.get("tracked_sha256")
                            or before.get("normalized_sha256"),
                            "official_url": before.get("official_url") or previous_url,
                        },
                        "after": {
                            "version": after_version,
                            "normalized_sha256": after_hash,
                            "official_url": (current or {}).get("official_url") or watch["official_url"],
                        },
                        "affected_claims": _affected_claims(result, identity, tracked_version),
                    }
                )
            watch["status"] = "NEEDS_REVIEW"
            watch["updated_at"] = stamp
    result["last_publication"] = {
        "collection_run_id": collection_run_id,
        "observed_at": stamp,
    }
    result["last_updated_at"] = stamp
    return result


def _detail_version(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    keys = (
        "document_version_id",
        "body_sha256",
        "normalized_text_sha256",
        "attachments",
        "published_at",
        "source_modified_at",
        "effective_at",
        "expires_at",
        "observed_at",
        "last_checked_at",
    )
    return {key: value[key] for key in keys if key in value}


def _detail_claims(
    state: dict[str, Any],
    identity: str,
    before: dict[str, Any],
) -> list[dict[str, Any]]:
    document_version = before.get("document_version_id")
    normalized_hash = before.get("normalized_text_sha256")
    rows = []
    for handoff in state["handoffs"]:
        for entry in handoff["items"]:
            if entry.get("identity") != identity:
                continue
            if document_version and entry.get("source_document_version") == document_version:
                matched = True
            elif normalized_hash and entry.get("source_sha256") == normalized_hash:
                matched = True
            else:
                matched = False
            if not matched:
                continue
            evidence = entry.get("evidence") if isinstance(entry.get("evidence"), dict) else {}
            rows.append(
                {
                    "brief_id": handoff["brief_id"],
                    "brief_version": handoff["brief_version"],
                    "claim_id": entry.get("claim_id") or claim_id_for(identity, entry["source_version"]),
                    "source_version": entry.get("source_version"),
                    "source_document_version": entry.get("source_document_version"),
                    "evidence_locator": evidence.get("locator"),
                }
            )
    return sorted(rows, key=lambda row: (row["brief_version"], row["claim_id"]))


def sync_with_detail_rechecks(
    state: dict[str, Any] | None,
    outcomes: Iterable[Any],
    *,
    observed_at: datetime | str,
) -> dict[str, Any]:
    """Invalidate only tracked handoff claims affected by a detail recheck."""
    result = copy_state(state)
    stamp = _timestamp(observed_at)
    if not isinstance(outcomes, Iterable) or isinstance(outcomes, (str, bytes, dict)):
        raise ValueError("detail rechecks must be an array")
    for raw_outcome in outcomes:
        outcome = _as_dict(raw_outcome)
        classification = outcome.get("classification")
        if not isinstance(classification, dict):
            raise ValueError("detail recheck classification is required")
        if not classification.get("review_required"):
            continue
        source_id = outcome.get("source_id")
        stable_key = outcome.get("stable_key")
        before = classification.get("before")
        after = classification.get("after")
        if not isinstance(source_id, str) or not source_id.strip() or not isinstance(stable_key, str) or not stable_key.strip():
            raise ValueError("detail recheck identity is required")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("detail recheck versions are required")
        identity = f"{source_id}:{stable_key}"
        watch = next((item for item in result["watch_items"].values() if item.get("identity") == identity), None)
        if watch is None:
            continue
        after_version = after.get("document_version_id")
        if not isinstance(after_version, str) or not after_version.strip():
            raise ValueError("detail recheck after document version is required")
        invalidation_id = "INV-" + _sha([watch["watch_id"], "DETAIL_RECHECK", after_version]).upper()[:20]
        known = {entry.get("invalidation_id") for entry in watch["invalidations"]}
        if invalidation_id not in known:
            watch["invalidations"].append(
                {
                    "invalidation_id": invalidation_id,
                    "event_id": outcome.get("event_id"),
                    "reason": classification.get("status") or "DETAIL_RECHECK",
                    "detected_at": classification.get("observed_at") or stamp,
                    "changed_fields": list(classification.get("changed_fields") or []),
                    "before": _detail_version(before),
                    "after": _detail_version(after),
                    "affected_claims": _detail_claims(result, identity, before),
                }
            )
        watch["status"] = "NEEDS_REVIEW"
        watch["updated_at"] = stamp
    result["last_updated_at"] = stamp
    return result


def tracking_projection(
    state: dict[str, Any] | None,
    *,
    current_items: dict[str, Any],
    events: Iterable[Any],
    source_status: dict[str, Any] | None,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], int]:
    candidate = copy_state(state)
    event_by_identity = {
        event["identity"]: event
        for event in (_event_dict(value) for value in events)
        if event.get("identity") and event.get("publishable")
    }
    source_by_id = {
        source.get("source_id"): source
        for source in (source_status or {}).get("sources", [])
        if isinstance(source, dict) and source.get("source_id")
    }
    rows = []
    for watch in candidate["watch_items"].values():
        if watch.get("status") not in ACTIVE_WATCH_STATUSES:
            continue
        identity = watch["identity"]
        current = _as_dict(current_items[identity]) if identity in current_items else {}
        event = event_by_identity.get(identity)
        invalidation = watch.get("invalidations", [])[-1] if watch.get("invalidations") else None
        source = source_by_id.get(watch.get("source_id"), {})
        needs_review = watch.get("status") == "NEEDS_REVIEW"
        rows.append(
            {
                "tracking_id": f"TRACK-{watch['watch_id']}",
                "watch_id": watch["watch_id"],
                "identity": identity,
                "source_id": watch.get("source_id"),
                "source_name": watch.get("source_id"),
                "headline": current.get("title") or watch.get("title"),
                "what_changed": (
                    f"官方來源版本由 v{invalidation['before']['version']} 更新至 "
                    f"v{invalidation['after']['version']}，需重新核對。"
                    if needs_review and invalidation
                    else "此項保留於跨日追蹤，尚未標記為已處理。"
                ),
                "why_it_matters": "此項由承辦人加入跨日交班追蹤，來源後續修正不會靜默覆蓋。",
                "recommended_action": (
                    "核對新舊官方版本，再確認是否建立下一版交班摘要。"
                    if needs_review
                    else "確認目前官方內容與業管狀態，必要時建立交班版本。"
                ),
                "deadline": None,
                "temporal_basis": "DETECTED_CHANGE" if event or invalidation else "FIRST_SEEN",
                "date_status": current.get("date_status") or "UNVERIFIED_DATE",
                "detected_at": (event or {}).get("detected_at") or watch.get("updated_at"),
                "changed_fields": list((event or {}).get("changed_fields") or []),
                "official_url": current.get("official_url") or watch.get("official_url"),
                "verification_status": "DETERMINISTIC_PASS",
                "evidence_status": "OFFICIAL_URL_BOUND",
                "publication_tier": "TRACKING",
                "watch_status": watch.get("status"),
                "reason_code": watch.get("reason_code"),
                "source_version": current.get("version_no") or watch.get("tracked_version"),
                "last_reviewed_version": watch.get("last_reviewed_version"),
                "last_handoff_id": watch.get("last_handoff_id"),
                "source_health": source.get("source_health", "UNKNOWN"),
                "source_freshness": source.get("freshness_status", "UNKNOWN"),
                "source_gaps": list(source.get("intelligence_gaps") or []),
                "invalidation": invalidation,
            }
        )
    rows.sort(key=lambda row: (0 if row["watch_status"] == "NEEDS_REVIEW" else 1, row["watch_id"]))
    return rows[: max(0, limit)], len(rows)


def confirm_handoff(
    state: dict[str, Any] | None,
    *,
    current_items: dict[str, Any],
    publication: dict[str, Any],
    generated_at: datetime | str,
    confirmed_at: datetime | str,
    watch_ids: Iterable[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = copy_state(state)
    selected = list(watch_ids or [])
    if not selected:
        selected = sorted(
            watch_id
            for watch_id, watch in result["watch_items"].items()
            if watch.get("status") in ACTIVE_WATCH_STATUSES
        )
    if not selected or len(selected) != len(set(selected)):
        raise ValueError("confirm requires one or more unique active watch IDs")
    entries = []
    for watch_id in selected:
        watch = result["watch_items"].get(watch_id)
        if watch is None or watch.get("status") not in ACTIVE_WATCH_STATUSES:
            raise ValueError(f"watch is not active: {watch_id}")
        current = _as_dict(current_items.get(watch["identity"], {}))
        if not isinstance(current.get("version_no"), int) or not current.get("official_url", "").startswith("https://"):
            raise ValueError(f"current source version is unavailable: {watch_id}")
        entries.append(
            {
                "watch_id": watch_id,
                "identity": watch["identity"],
                "title": current.get("title") or watch["title"],
                "source_version": current["version_no"],
                "source_sha256": current.get("normalized_sha256"),
                "claim_id": claim_id_for(watch["identity"], current["version_no"]),
                "source_document_version": current.get("document_version_id") or current.get("normalized_sha256"),
                "evidence": {
                    "official_url": current["official_url"],
                    "locator": f"{watch['identity']}#v{current['version_no']}",
                },
            }
        )
    generated = _timestamp(generated_at)
    confirmed = _timestamp(confirmed_at)
    brief_version = max((handoff["brief_version"] for handoff in result["handoffs"]), default=0) + 1
    material = {"brief_version": brief_version, "generated_at": generated, "items": entries}
    brief_id = f"HANDOFF-{_sha(material).upper()[:20]}"
    allowed_publication = {
        key: publication[key]
        for key in ("collection_run_id", "brief_sha256", "source_status_generated_at", "source_policy_hash")
        if publication.get(key) is not None
    }
    handoff = {
        "brief_id": brief_id,
        "brief_version": brief_version,
        "generated_at": generated,
        "confirmed_at": confirmed,
        "confirmation_state": "CONFIRMED",
        "publication": allowed_publication,
        "items": entries,
    }
    result["handoffs"].append(handoff)
    for entry in entries:
        watch = result["watch_items"][entry["watch_id"]]
        watch["status"] = "WATCHING"
        watch["updated_at"] = confirmed
        watch["tracked_version"] = entry["source_version"]
        watch["tracked_sha256"] = entry["source_sha256"]
        watch["last_reviewed_version"] = entry["source_version"]
        watch["last_reviewed_sha256"] = entry["source_sha256"]
        watch["last_handoff_id"] = brief_id
        for invalidation in watch["invalidations"]:
            invalidation.setdefault("resolved_in_handoff_id", brief_id)
    result["last_updated_at"] = confirmed
    return result, handoff


def set_watch_status(
    state: dict[str, Any] | None,
    watch_id: str,
    status: str,
    *,
    updated_at: datetime | str,
) -> dict[str, Any]:
    if status not in {"RESOLVED", "DISMISSED"}:
        raise ValueError("manual status must be RESOLVED or DISMISSED")
    result = copy_state(state)
    watch = result["watch_items"].get(watch_id)
    if watch is None:
        raise ValueError(f"unknown watch ID: {watch_id}")
    stamp = _timestamp(updated_at)
    watch["status"] = status
    watch["updated_at"] = stamp
    result["last_updated_at"] = stamp
    return result


def find_handoff(state: dict[str, Any], brief_id: str | None = None) -> dict[str, Any]:
    validate_state(state)
    if not state["handoffs"]:
        raise ValueError("no confirmed handoff exists")
    if brief_id is None:
        return state["handoffs"][-1]
    for handoff in state["handoffs"]:
        if handoff.get("brief_id") == brief_id:
            return handoff
    raise ValueError(f"unknown handoff ID: {brief_id}")


def handoff_markdown(handoff: dict[str, Any]) -> str:
    lines = [
        f"# GovIntel AI 交班摘要 v{handoff['brief_version']}",
        "",
        f"- brief_id: `{handoff['brief_id']}`",
        f"- generated_at: `{handoff['generated_at']}`",
        f"- confirmed_at: `{handoff['confirmed_at']}`",
        f"- collection_run_id: `{handoff.get('publication', {}).get('collection_run_id', 'UNKNOWN')}`",
        "",
        "## 追蹤項目",
        "",
    ]
    for item in handoff["items"]:
        evidence = item["evidence"]
        lines.extend(
            [
                f"### {item['title']}",
                f"- watch_id: `{item['watch_id']}`",
                f"- claim_id: `{item.get('claim_id') or claim_id_for(item['identity'], item['source_version'])}`",
                f"- source version: `v{item['source_version']}`",
                f"- source document version: `{item.get('source_document_version') or 'UNKNOWN'}`",
                f"- evidence: [{evidence['official_url']}]({evidence['official_url']})",
                f"- locator: `{evidence['locator']}`",
                "",
            ]
        )
    return "\n".join(lines)
