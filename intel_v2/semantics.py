from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time
from typing import Any, Iterable
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Taipei")
STATUS_FIELD_NAMES = {
    "status",
    "stage",
    "resolution",
    "verification_status",
    "workflow_status",
}
DEADLINE_FIELD_NAMES = {
    "deadline",
    "due_at",
    "effective_at",
    "meeting_at",
    "scheduled_at",
    "session_date",
}
PUBLISHABLE_CHANGE_TYPES = {
    "NEW",
    "REVISED",
    "STATUS_CHANGED",
    "DEADLINE_CHANGED",
    "REMOVED",
}


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _without_fields(value: Any, ignored_fields: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_fields(child, ignored_fields)
            for key, child in sorted(value.items())
            if key not in ignored_fields
        }
    if isinstance(value, list):
        return [_without_fields(child, ignored_fields) for child in value]
    return value


def normalized_payload_sha256(
    payload: dict[str, Any],
    ignored_fields: Iterable[str] = (),
) -> str:
    """Hash semantic content while excluding explicitly volatile fields."""

    return canonical_sha256(_without_fields(payload, set(ignored_fields)))


def _as_datetime(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(TZ)


def _timestamp(value: datetime | str) -> str:
    return _as_datetime(value).isoformat(timespec="seconds")


def ensure_slot_is_due(
    slot: str,
    slot_date: date,
    now: datetime | str,
) -> datetime:
    """Reject manual or scheduled attempts that target a future collection slot."""

    normalized_slot = slot.upper()
    if normalized_slot not in {"MORNING", "EVENING"}:
        raise ValueError("slot must be MORNING or EVENING")
    clock = time(6, 30) if normalized_slot == "MORNING" else time(18, 30)
    scheduled_for = datetime.combine(slot_date, clock, TZ)
    observed = _as_datetime(now)
    if observed < scheduled_for:
        raise ValueError(
            "FUTURE_SLOT: "
            f"{normalized_slot} is not due until {scheduled_for.isoformat(timespec='seconds')}"
        )
    return scheduled_for


def _date_status(published_at: str | None, effective_at: str | None) -> str:
    return "KNOWN" if published_at or effective_at else "UNVERIFIED_DATE"


def _identity(source_id: str, stable_key: str) -> str:
    if not source_id or not stable_key:
        raise ValueError("source_id and stable_key are required")
    return f"{source_id}:{stable_key}"


@dataclass(frozen=True)
class ItemVersion:
    identity: str
    source_id: str
    stable_key: str
    version_no: int
    title: str
    official_url: str
    raw_sha256: str
    normalized_sha256: str
    payload: dict[str, Any]
    published_at: str | None
    effective_at: str | None
    first_seen_at: str
    last_seen_at: str
    date_status: str
    missing_streak: int = 0
    is_current: bool = True

    def to_state(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_state(cls, value: dict[str, Any]) -> "ItemVersion":
        return cls(**value)


@dataclass(frozen=True)
class ChangeEvent:
    event_id: str
    identity: str
    source_id: str
    stable_key: str
    title: str
    official_url: str
    change_type: str
    detected_at: str
    occurred_at: str | None
    date_status: str
    temporal_basis: str
    before_version: int | None
    after_version: int | None
    changed_fields: tuple[str, ...]
    publishable: bool
    wording: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["changed_fields"] = list(self.changed_fields)
        return value


def build_item_version(
    *,
    source_id: str,
    stable_key: str,
    title: str,
    official_url: str,
    payload: dict[str, Any],
    observed_at: datetime | str,
    raw_sha256: str | None = None,
    published_at: str | None = None,
    effective_at: str | None = None,
    ignored_fields: Iterable[str] = (),
) -> ItemVersion:
    if not official_url.startswith("https://"):
        raise ValueError("official_url must use HTTPS")
    observed = _timestamp(observed_at)
    normalized_sha = normalized_payload_sha256(payload, ignored_fields)
    raw_sha = raw_sha256 or canonical_sha256(payload)
    stable_identity = _identity(source_id, stable_key)
    return ItemVersion(
        identity=stable_identity,
        source_id=source_id,
        stable_key=stable_key,
        version_no=1,
        title=title.strip(),
        official_url=official_url,
        raw_sha256=raw_sha,
        normalized_sha256=normalized_sha,
        payload=payload,
        published_at=published_at,
        effective_at=effective_at,
        first_seen_at=observed,
        last_seen_at=observed,
        date_status=_date_status(published_at, effective_at),
    )


def feed_item_to_version(
    item: dict[str, Any],
    observed_at: datetime | str,
) -> ItemVersion:
    """Convert the legacy feed shape into a conservative V2 shadow item.

    Collector timestamps, freshness labels, legacy eligibility and legacy change
    labels are deliberately excluded from the semantic hash. They describe the
    monitoring run, not the official item itself.
    """

    source_id = str(item.get("source_id") or "")
    stable_key = str(item.get("stable_id") or "")
    title = str(item.get("title") or "")
    official_url = str(item.get("official_url") or "")
    payload = {
        "title": title,
        "official_url": official_url,
        "committee": item.get("committee") or "",
        "published_at": item.get("published_at"),
        "effective_at": item.get("effective_at"),
        "next_milestone": item.get("next_milestone"),
    }
    return build_item_version(
        source_id=source_id,
        stable_key=stable_key,
        title=title,
        official_url=official_url,
        payload=payload,
        observed_at=observed_at,
        raw_sha256=item.get("content_sha256") or None,
        published_at=item.get("published_at"),
        effective_at=item.get("effective_at"),
    )


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, child in sorted(value.items()):
            child_prefix = f"{prefix}.{key}" if prefix else key
            flattened.update(_flatten(child, child_prefix))
        return flattened
    if isinstance(value, list):
        flattened = {}
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            flattened.update(_flatten(child, child_prefix))
        return flattened
    return {prefix: value}


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, ...]:
    before_flat = _flatten(before)
    after_flat = _flatten(after)
    keys = set(before_flat) | set(after_flat)
    return tuple(sorted(key for key in keys if before_flat.get(key) != after_flat.get(key)))


def _leaf_names(paths: Iterable[str]) -> set[str]:
    names = set()
    for path in paths:
        leaf = path.rsplit(".", 1)[-1]
        leaf = leaf.split("[", 1)[0]
        names.add(leaf)
    return names


def _classify_revision(changed_fields: tuple[str, ...]) -> str:
    leaf_names = _leaf_names(changed_fields)
    if leaf_names & DEADLINE_FIELD_NAMES:
        return "DEADLINE_CHANGED"
    if leaf_names & STATUS_FIELD_NAMES:
        return "STATUS_CHANGED"
    return "REVISED"


def _event_id(
    identity: str,
    change_type: str,
    detected_at: str,
    after_hash: str | None,
) -> str:
    digest = canonical_sha256(
        {
            "identity": identity,
            "change_type": change_type,
            "detected_at": detected_at,
            "after_hash": after_hash,
        }
    )[:20]
    return f"EVT-{digest.upper()}"


def _wording(
    *,
    title: str,
    change_type: str,
    date_status: str,
    occurred_at: str | None,
    missing_streak: int = 0,
) -> tuple[str, str]:
    if change_type == "NEW":
        if date_status == "KNOWN" and occurred_at:
            return (
                "OFFICIAL_DATE",
                f"本次監測首次偵測到「{title}」；官方資料日期為 {occurred_at[:10]}。",
            )
        return (
            "FIRST_SEEN",
            f"本次監測首次偵測到「{title}」；官方未提供可驗證的發布日期。",
        )
    if change_type in {"REVISED", "STATUS_CHANGED", "DEADLINE_CHANGED"}:
        return (
            "DETECTED_CHANGE",
            f"本次監測偵測到「{title}」內容變更；變更時間以系統偵測時間為準。",
        )
    if change_type == "REMOVED":
        return (
            "DETECTED_CHANGE",
            f"連續 {missing_streak} 次完整監測未再發現「{title}」，標記為已移除。",
        )
    if change_type == "REMOVAL_CANDIDATE":
        return (
            "DETECTED_CHANGE",
            f"本次完整監測未發現「{title}」；尚待下一次完整監測確認是否移除。",
        )
    return ("NONE", "")


def _make_event(
    *,
    current: ItemVersion,
    previous: ItemVersion | None,
    change_type: str,
    detected_at: str,
    changed_fields: tuple[str, ...] = (),
    missing_streak: int = 0,
) -> ChangeEvent:
    occurred_at = current.effective_at or current.published_at
    temporal_basis, wording = _wording(
        title=current.title,
        change_type=change_type,
        date_status=current.date_status,
        occurred_at=occurred_at,
        missing_streak=missing_streak,
    )
    return ChangeEvent(
        event_id=_event_id(
            current.identity,
            change_type,
            detected_at,
            current.normalized_sha256 if current.is_current else None,
        ),
        identity=current.identity,
        source_id=current.source_id,
        stable_key=current.stable_key,
        title=current.title,
        official_url=current.official_url,
        change_type=change_type,
        detected_at=detected_at,
        occurred_at=occurred_at,
        date_status=current.date_status,
        temporal_basis=temporal_basis,
        before_version=previous.version_no if previous else None,
        after_version=current.version_no if current.is_current else None,
        changed_fields=changed_fields,
        publishable=change_type in PUBLISHABLE_CHANGE_TYPES,
        wording=wording,
    )


def compare_snapshot(
    previous_state: dict[str, Any] | None,
    current_items: Iterable[ItemVersion],
    observed_at: datetime | str,
    *,
    snapshot_complete: bool = True,
    removal_confirmations: int = 2,
) -> tuple[dict[str, Any], list[ChangeEvent]]:
    """Compare one complete normalized snapshot against the durable baseline.

    The first snapshot establishes a baseline and emits no NEW events. Later
    snapshots emit item-level events. An item must be absent from at least two
    complete snapshots before a REMOVED event is publishable.
    """

    if removal_confirmations < 2:
        raise ValueError("removal_confirmations must be at least 2")
    observed = _timestamp(observed_at)
    previous_items = {
        identity: ItemVersion.from_state(value)
        for identity, value in (previous_state or {}).get("items", {}).items()
    }
    baseline_exists = bool((previous_state or {}).get("baseline_established_at"))

    current_by_identity: dict[str, ItemVersion] = {}
    for item in current_items:
        if item.identity in current_by_identity:
            raise ValueError(f"duplicate stable identity: {item.identity}")
        current_by_identity[item.identity] = item

    next_items: dict[str, dict[str, Any]] = {}
    events: list[ChangeEvent] = []

    for identity, incoming in sorted(current_by_identity.items()):
        previous = previous_items.get(identity)
        if previous is None:
            current = replace(
                incoming,
                version_no=1,
                first_seen_at=observed,
                last_seen_at=observed,
                missing_streak=0,
                is_current=True,
            )
            if baseline_exists:
                events.append(
                    _make_event(
                        current=current,
                        previous=None,
                        change_type="NEW",
                        detected_at=observed,
                    )
                )
        elif previous.normalized_sha256 == incoming.normalized_sha256:
            # A raw page or wrapper may change while the normalized official
            # item remains identical. That is monitoring evidence, not a new
            # intelligence event.
            current = replace(
                incoming,
                version_no=previous.version_no,
                first_seen_at=previous.first_seen_at,
                last_seen_at=observed,
                missing_streak=0,
                is_current=True,
            )
        else:
            fields = _changed_fields(previous.payload, incoming.payload)
            change_type = _classify_revision(fields)
            current = replace(
                incoming,
                version_no=previous.version_no + 1,
                first_seen_at=previous.first_seen_at,
                last_seen_at=observed,
                missing_streak=0,
                is_current=True,
            )
            events.append(
                _make_event(
                    current=current,
                    previous=previous,
                    change_type=change_type,
                    detected_at=observed,
                    changed_fields=fields,
                )
            )
        next_items[identity] = current.to_state()

    for identity, previous in sorted(previous_items.items()):
        if identity in current_by_identity:
            continue
        if not snapshot_complete:
            next_items[identity] = previous.to_state()
            continue

        missing_streak = previous.missing_streak + 1
        is_removed = missing_streak >= removal_confirmations
        current = replace(
            previous,
            last_seen_at=observed,
            missing_streak=missing_streak,
            is_current=not is_removed,
        )
        if previous.is_current:
            events.append(
                _make_event(
                    current=current,
                    previous=previous,
                    change_type="REMOVED" if is_removed else "REMOVAL_CANDIDATE",
                    detected_at=observed,
                    missing_streak=missing_streak,
                )
            )
        next_items[identity] = current.to_state()

    baseline_established_at = (
        (previous_state or {}).get("baseline_established_at") or observed
    )
    state = {
        "schema_version": 1,
        "mode": "V2_SHADOW",
        "baseline_established_at": baseline_established_at,
        "last_observed_at": observed,
        "items": next_items,
    }
    return state, events


def shadow_brief(
    *,
    feed: dict[str, Any],
    state: dict[str, Any],
    events: Iterable[ChangeEvent],
    generated_at: datetime | str,
) -> dict[str, Any]:
    """Build a conservative, non-production comparison brief.

    It never promotes legacy CONFIRMED items merely because an endpoint was
    reachable. Only events produced by the V2 item-level comparator qualify.
    """

    observed = _timestamp(generated_at)
    feed_items = feed.get("items") if isinstance(feed.get("items"), list) else []
    event_list = list(events)
    publishable = [event for event in event_list if event.publishable]

    legacy_eligible = sum(
        isinstance(item, dict) and item.get("eligibility") == "HOME_CANDIDATE"
        for item in feed_items
    )
    confirmed_as_current = sum(
        isinstance(item, dict)
        and item.get("change_type") == "CONFIRMED"
        and item.get("eligibility") == "HOME_CANDIDATE"
        for item in feed_items
    )
    missing_source_dates = sum(
        isinstance(item, dict) and not item.get("published_at")
        for item in feed_items
    )

    quality_issues: list[dict[str, Any]] = []
    ratio = legacy_eligible / len(feed_items) if feed_items else 0.0
    if len(feed_items) >= 20 and ratio >= 0.80:
        quality_issues.append(
            {
                "code": "HIGH_LEGACY_ELIGIBILITY_RATIO",
                "severity": "HIGH",
                "detail": (
                    f"Legacy feed marks {legacy_eligible}/{len(feed_items)} items as current. "
                    "V2 does not treat archive confirmation as a daily change."
                ),
            }
        )
    if confirmed_as_current:
        quality_issues.append(
            {
                "code": "LEGACY_CONFIRMED_AS_CURRENT",
                "severity": "HIGH",
                "count": confirmed_as_current,
            }
        )
    if missing_source_dates:
        quality_issues.append(
            {
                "code": "MISSING_OFFICIAL_DATE",
                "severity": "INFO",
                "count": missing_source_dates,
                "detail": "These items may use first-seen wording after the baseline is established.",
            }
        )

    change_priority = {
        "STATUS_CHANGED": 0,
        "DEADLINE_CHANGED": 1,
        "NEW": 2,
        "REVISED": 3,
        "REMOVED": 4,
    }
    publishable.sort(
        key=lambda event: (
            change_priority.get(event.change_type, 99),
            event.source_id,
            event.identity,
        )
    )

    priority_items = [
        {
            "event_id": event.event_id,
            "change_type": event.change_type,
            "headline": event.title,
            "what_changed": event.wording,
            "time_basis": event.temporal_basis,
            "date_status": event.date_status,
            "changed_fields": list(event.changed_fields),
            "official_url": event.official_url,
        }
        for event in publishable[:3]
    ]

    return {
        "schema_version": 1,
        "mode": "V2_SHADOW",
        "generated_at": observed,
        "source_collection_run_id": feed.get("collection_run_id"),
        "baseline_established_at": state.get("baseline_established_at"),
        "overview": {
            "current_change_count": len(publishable),
            "priority_count": len(priority_items),
            "tracking_count": 0,
            "archive_total": len(feed_items),
            "legacy_home_candidate_count": legacy_eligible,
        },
        "status_message": (
            "本期未偵測到可確認的新增或修正；歷史資料保留於資料庫。"
            if not publishable
            else f"本期偵測到 {len(publishable)} 件可確認變更。"
        ),
        "priority_items": priority_items,
        "quality_issues": quality_issues,
    }
