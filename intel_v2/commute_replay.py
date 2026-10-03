"""Deterministic replay of the v6 commute-road reopen session.

Fingerprint: ``govintel/v6/commute-reopen-replay-evaluation/v1``

The module replays one versioned scenario: a commuter saves a tracked road
condition, the station is closed, publications arrive, and the station reopens
at controlled clock points. For every reopen point it reports which updates the
station may prompt, together with the reason every other update was withheld.

Reuse, not rewrite: content identity and semantic hashing come from
:mod:`intel_v2.semantics`; this module adds only the reopen/unread bookkeeping
and the v6 scenario contract. Metric algorithms stay in
``scripts/evaluate-govintel.py`` so there is exactly one scoring implementation.

Every value in the bundled scenario is synthetic. It does not describe any real
road, traffic condition or road safety.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields as dataclass_fields, replace
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable

from intel_v2.semantics import canonical_sha256, normalized_payload_sha256

FINGERPRINT = "govintel/v6/commute-reopen-replay-evaluation/v1"
SCENARIO_SCHEMA_VERSION = 1
REPLAY_RECEIPT_VERSION = 1
POLICY_VERSION = "v6-commute-reopen-policy-1"
PARSER_VERSION = "v6-commute-reopen-parser-1"
SCENARIO_PATH = "eval/gold/v6/commute-reopen-v1/session.json"
CASE_PATH = "eval/gold/v6/commute-reopen-v1/cases.jsonl"
ANNOTATION_PATH = "eval/gold/v6/commute-reopen-v1/annotations.json"
COST_PATH = "eval/gold/v6/commute-reopen-v1/cost-ledger.json"

# This replay is a rules-and-hash replay. It issues no model request, so the
# receipt must never imply one.
SEMANTIC_MODEL_VERSION: str | None = None
SEMANTIC_PROMPT_HASH: str | None = None

# Prompted: the station may show this update to the commuter.
PROMPTED = "PROMPTED"
# Withheld reasons.
ALREADY_READ = "ALREADY_READ"
FORMAT_ONLY = "FORMAT_ONLY"
REPOST_MIRROR = "REPOST_MIRROR"
DUPLICATE_FETCH = "DUPLICATE_FETCH"
POST_CANCEL = "POST_CANCEL"
NOT_YET_ACQUIRED = "NOT_YET_ACQUIRED"
NO_ACTIVE_CONDITION = "NO_ACTIVE_CONDITION"
OUTSIDE_TRACKED_WINDOW = "OUTSIDE_TRACKED_WINDOW"
DATE_UNVERIFIED = "DATE_UNVERIFIED"
SUPPRESSED_REASONS = (
    ALREADY_READ,
    FORMAT_ONLY,
    REPOST_MIRROR,
    DUPLICATE_FETCH,
    POST_CANCEL,
    NOT_YET_ACQUIRED,
    NO_ACTIVE_CONDITION,
    OUTSIDE_TRACKED_WINDOW,
    DATE_UNVERIFIED,
)

# Leaf payload fields that carry the road condition itself.
MATERIAL_FIELDS = frozenset(
    {
        "road",
        "district",
        "status",
        "effective_from",
        "effective_to",
        "start",
        "end",
        "text",
    }
)
# Top-level payload paths that only describe presentation or collection mechanics.
FORMATTING_PATHS = frozenset(
    {
        "layout",
        "punctuation_style",
        "whitespace",
        "raw_html",
        "fetched_at",
        "rendered_at",
        "page_shell",
    }
)
VOLATILE_FIELDS = ("fetched_at", "rendered_at", "raw_html")
# The dedupe identity of a document excludes presentation, so a layout-only
# republication stays the same content and a later re-fetch of the original body
# is still recognised as a duplicate fetch.
CONTENT_IDENTITY_IGNORED_FIELDS = tuple(sorted(set(VOLATILE_FIELDS) | FORMATTING_PATHS))

# Reopen station states, kept distinct on purpose: no new items, source failure,
# a stale tracked end and thin coverage are four different observations.
NEW_UPDATES = "NEW_UPDATES"
NO_NEW_ITEMS = "NO_NEW_ITEMS"
SOURCE_GAP = "SOURCE_GAP"
STALE_TRACKED_END = "STALE_TRACKED_END"
OBSERVATION_STATUSES = (NEW_UPDATES, NO_NEW_ITEMS, SOURCE_GAP, STALE_TRACKED_END)

# Collection gap kinds.
GAP_CONNECTION_FAILURE = "CONNECTION_FAILURE"
GAP_PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
GAP_SOURCE_ITEM_MISSING = "SOURCE_ITEM_MISSING"
GAP_SCHEDULED_END_PASSED = "SCHEDULED_END_PASSED"
GAP_KINDS = (
    GAP_CONNECTION_FAILURE,
    GAP_PARTIAL_COVERAGE,
    GAP_SOURCE_ITEM_MISSING,
    GAP_SCHEDULED_END_PASSED,
)


class ScenarioError(ValueError):
    """Raised when a scenario violates the v6 replay contract."""


@dataclass(frozen=True)
class ReplayPolicy:
    """Every switch here is a live matching or dedupe decision of the harness.

    ``semantic_matching`` enables the v6 condition semantics: road/district
    identity plus effective-interval intersection with the tracked daily band.
    Turning it off yields the ``C_RULES_ONLY`` ablation, which keeps field
    equality and drops all time reasoning.
    """

    semantic_matching: bool = True
    mirror_is_independent_evidence: bool = False
    track_read_state: bool = True
    dedupe_repeat_fetches: bool = True
    # ``content`` dedupes on the semantic body of a document. ``document`` allows
    # only one substantive alert per official document, which silently swallows a
    # later substantive correction of the same document.
    dedupe_scope: str = "content"
    formatting_is_substantive: bool = False
    auto_lift_on_tracked_end: bool = False
    policy_version: str = POLICY_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "semantic_matching": self.semantic_matching,
            "mirror_is_independent_evidence": self.mirror_is_independent_evidence,
            "track_read_state": self.track_read_state,
            "dedupe_repeat_fetches": self.dedupe_repeat_fetches,
            "dedupe_scope": self.dedupe_scope,
            "formatting_is_substantive": self.formatting_is_substantive,
            "auto_lift_on_tracked_end": self.auto_lift_on_tracked_end,
        }


DEFAULT_POLICY = ReplayPolicy()


def _as_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ScenarioError(f"timestamp must include a timezone: {value}")
    return parsed


def _clock(value: str) -> time:
    hour, minute = (int(part) for part in value.split(":"))
    return time(hour, minute)


def _leaf_name(path: str) -> str:
    return path.rsplit(".", 1)[-1].split("[", 1)[0]


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
            child_prefix = f"{prefix}[{index}]" if prefix else str(index)
            flattened.update(_flatten(child, child_prefix))
        return flattened
    return {prefix: value}


def changed_paths(before: Any, after: Any) -> tuple[str, ...]:
    """Return the sorted flattened payload paths whose value differs."""

    before_flat = _flatten(before)
    after_flat = _flatten(after)
    return tuple(
        sorted(path for path in set(before_flat) | set(after_flat) if before_flat.get(path) != after_flat.get(path))
    )


def is_formatting_only(paths: Iterable[str]) -> bool:
    """True when a revision touched presentation or collection mechanics only.

    An empty revision is not formatting-only: nothing changed, so there is no
    substantive update to report in the first place.
    """

    changed = list(paths)
    if not changed:
        return False
    if any(_leaf_name(path) in MATERIAL_FIELDS for path in changed):
        return False
    return all(path.split(".", 1)[0] in FORMATTING_PATHS for path in changed)


def content_identity_sha256(payload: dict[str, Any]) -> str:
    """Dedupe identity of one official body, ignoring presentation fields."""

    return normalized_payload_sha256(payload, CONTENT_IDENTITY_IGNORED_FIELDS)


def scenario_hash(scenario: dict[str, Any]) -> str:
    """Hash the scenario body so a receipt proves which input was replayed."""

    return canonical_sha256(scenario)


def load_scenario(path: str | Path = SCENARIO_PATH) -> dict[str, Any]:
    scenario = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_scenario(scenario)
    return scenario


DEDUPE_SCOPES = ("content", "document")


def policy_from_mapping(values: dict[str, Any] | None = None) -> ReplayPolicy:
    if not values:
        return DEFAULT_POLICY
    known = {field.name for field in dataclass_fields(ReplayPolicy)}
    unknown = set(values) - known
    if unknown:
        raise ScenarioError(f"unknown replay policy fields: {sorted(unknown)}")
    policy = replace(DEFAULT_POLICY, **values)
    if policy.dedupe_scope not in DEDUPE_SCOPES:
        raise ScenarioError(f"unsupported dedupe_scope: {policy.dedupe_scope!r}")
    return policy


def validate_scenario(scenario: dict[str, Any]) -> None:
    if not isinstance(scenario, dict):
        raise ScenarioError("scenario must be an object")
    if scenario.get("schema_version") != SCENARIO_SCHEMA_VERSION:
        raise ScenarioError("unsupported scenario schema_version")
    if scenario.get("fingerprint") != FINGERPRINT:
        raise ScenarioError("scenario fingerprint mismatch")
    if scenario.get("synthetic") is not True:
        raise ScenarioError("scenario must be marked synthetic")
    for key in ("scenario_id", "data_cutoff", "conditions", "updates", "collections", "reopen_points"):
        if key not in scenario:
            raise ScenarioError(f"scenario missing {key}")
    _as_datetime(scenario["data_cutoff"])

    condition_ids: set[str] = set()
    for condition in scenario["conditions"]:
        condition_id = condition.get("condition_id")
        if not isinstance(condition_id, str) or not condition_id:
            raise ScenarioError("condition_id missing")
        if condition_id in condition_ids:
            raise ScenarioError(f"duplicate condition_id: {condition_id}")
        condition_ids.add(condition_id)
        if condition.get("kind") not in {"ROAD_CONDITION", "DISTRICT_CONDITION"}:
            raise ScenarioError(f"unsupported condition kind: {condition.get('kind')!r}")
        band = condition.get("daily")
        if not isinstance(band, dict) or "start" not in band or "end" not in band:
            raise ScenarioError(f"condition daily band missing: {condition_id}")
        _clock(band["start"])
        _clock(band["end"])
        if not condition.get("tracked_from"):
            raise ScenarioError(f"condition tracked_from missing: {condition_id}")
        _as_datetime(condition["tracked_from"])
        _as_datetime(condition["saved_at"])
        if condition.get("cancelled_at"):
            _as_datetime(condition["cancelled_at"])

    update_ids: set[str] = set()
    for update in scenario["updates"]:
        update_id = update.get("update_id")
        if not isinstance(update_id, str) or not update_id:
            raise ScenarioError("update_id missing")
        if update_id in update_ids:
            raise ScenarioError(f"duplicate update_id: {update_id}")
        for key in ("document_id", "source_id", "acquired_at", "content_sha256", "payload"):
            if key not in update:
                raise ScenarioError(f"update missing {key}: {update_id}")
        _as_datetime(update["acquired_at"])
        upstream = update.get("upstream_update_id")
        if upstream and upstream not in update_ids:
            raise ScenarioError(f"repost must reference an earlier update: {update_id}")
        if update["content_sha256"] != normalized_payload_sha256(update["payload"], VOLATILE_FIELDS):
            raise ScenarioError(f"content_sha256 does not match payload: {update_id}")
        update_ids.add(update_id)

    slot_ids: set[str] = set()
    for collection in scenario["collections"]:
        for key in ("slot_id", "observed_at", "snapshot_complete"):
            if key not in collection:
                raise ScenarioError(f"collection missing {key}")
        if collection["slot_id"] in slot_ids:
            raise ScenarioError(f"duplicate slot_id: {collection['slot_id']}")
        slot_ids.add(collection["slot_id"])
        _as_datetime(collection["observed_at"])
        for update_id in collection.get("visible_update_ids", []):
            if update_id not in update_ids:
                raise ScenarioError(f"collection references unknown update: {update_id}")

    reopen_ids: set[str] = set()
    previous: datetime | None = None
    for reopen in scenario["reopen_points"]:
        reopen_id = reopen.get("reopen_id")
        if not isinstance(reopen_id, str) or not reopen_id:
            raise ScenarioError("reopen_id missing")
        if reopen_id in reopen_ids:
            raise ScenarioError(f"duplicate reopen_id: {reopen_id}")
        reopen_ids.add(reopen_id)
        if reopen.get("observation_status") is not None and reopen["observation_status"] not in OBSERVATION_STATUSES:
            raise ScenarioError(f"unsupported observation_status: {reopen['observation_status']!r}")
        opened = _as_datetime(reopen["reopened_at"])
        if previous is not None and opened < previous:
            raise ScenarioError("reopen_points must be ordered")
        previous = opened


def _condition_active(condition: dict[str, Any], moment: datetime) -> bool:
    if moment < _as_datetime(condition["saved_at"]):
        return False
    cancelled_at = condition.get("cancelled_at")
    return not cancelled_at or moment < _as_datetime(cancelled_at)


NOT_SAVED = "NOT_SAVED"
ACTIVE = "ACTIVE"
LIFTED_BY_OFFICIAL_TEXT = "LIFTED_BY_OFFICIAL_TEXT"
CANCELLED = "CANCELLED"
CONDITION_STATUSES = (NOT_SAVED, ACTIVE, LIFTED_BY_OFFICIAL_TEXT, CANCELLED)


def _condition_status(
    condition: dict[str, Any],
    moment: datetime,
    lifted_by_update_id: dict[str, str],
) -> str:
    """A condition only leaves ACTIVE by explicit text or by the user's cancel."""

    if moment < _as_datetime(condition["saved_at"]):
        return NOT_SAVED
    cancelled_at = condition.get("cancelled_at")
    if cancelled_at and moment >= _as_datetime(cancelled_at):
        return CANCELLED
    return LIFTED_BY_OFFICIAL_TEXT if condition["condition_id"] in lifted_by_update_id else ACTIVE


def _update_interval(update: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    payload = update["payload"]
    start = payload.get("effective_from")
    end = payload.get("effective_to")
    return (_as_datetime(start) if start else None, _as_datetime(end) if end else None)


def intersects_tracked_band(
    update: dict[str, Any],
    condition: dict[str, Any],
    tracked_end: str | None = None,
) -> bool | None:
    """Does the update fall inside the tracked date range *and* daily band?

    Returns ``None`` when the official date cannot be verified. An unverifiable
    date is an unknown, never a licence to assume the update is irrelevant.
    """

    start, end = _update_interval(update)
    if start is None:
        return None
    band_start = _clock(condition["daily"]["start"])
    band_end = _clock(condition["daily"]["end"])
    tracked_from = condition.get("tracked_from")
    tracked_from_at = _as_datetime(tracked_from) if tracked_from else None
    tracked_end_at = _as_datetime(tracked_end) if tracked_end else None
    day = start.date()
    last_day = (end or start).date()
    while day <= last_day:
        window_start = datetime.combine(day, band_start, start.tzinfo)
        window_end = datetime.combine(day, band_end, start.tzinfo)
        if start <= window_end and (end is None or window_start <= end):
            in_range = True
            if tracked_from_at is not None and window_end < tracked_from_at:
                in_range = False
            if tracked_end_at is not None and window_start > tracked_end_at:
                in_range = False
            if in_range:
                return True
        day += timedelta(days=1)
    return False


def _identity_matches(update: dict[str, Any], condition: dict[str, Any]) -> bool:
    payload = update["payload"]
    if condition["kind"] == "ROAD_CONDITION":
        return payload.get("road") == condition.get("road")
    return payload.get("district") == condition.get("district")


def match_conditions(
    update: dict[str, Any],
    conditions: Iterable[dict[str, Any]],
    moment: datetime,
    policy: ReplayPolicy,
    tracked_end_by_condition: dict[str, str | None] | None = None,
) -> tuple[tuple[str, ...], str]:
    """Return the active conditions an update belongs to, plus a rejection reason."""

    active = [condition for condition in conditions if _condition_active(condition, moment)]
    matched = sorted(
        condition["condition_id"]
        for condition in active
        if _identity_matches(update, condition)
    )
    if not matched:
        cancelled = sorted(
            condition["condition_id"]
            for condition in conditions
            if not _condition_active(condition, moment)
            and condition.get("cancelled_at")
            and _identity_matches(update, condition)
        )
        return (), POST_CANCEL if cancelled else NO_ACTIVE_CONDITION
    if not policy.semantic_matching:
        # Ablation: identity equality only, no interval reasoning at all.
        return tuple(matched), PROMPTED
    ends = tracked_end_by_condition or {}
    verdicts = [
        intersects_tracked_band(update, condition, ends.get(condition["condition_id"]))
        for condition in active
        if condition["condition_id"] in matched
    ]
    if any(verdict is True for verdict in verdicts):
        return tuple(matched), PROMPTED
    if any(verdict is None for verdict in verdicts):
        return (), DATE_UNVERIFIED
    return (), OUTSIDE_TRACKED_WINDOW


def _classify_substance(
    updates: list[dict[str, Any]],
    policy: ReplayPolicy,
) -> dict[str, tuple[bool, str]]:
    """Decide, once, whether each acquired update is a new substantive update."""

    substance: dict[str, tuple[bool, str]] = {}
    seen_raw: dict[str, set[str]] = {}
    seen_identity: dict[str, set[str]] = {}
    substantive_documents: set[str] = set()
    for update in updates:
        update_id = update["update_id"]
        if update.get("upstream_update_id") and not policy.mirror_is_independent_evidence:
            substance[update_id] = (False, REPOST_MIRROR)
            continue
        document_id = update["document_id"]
        raw_hash = update["content_sha256"]
        identity_hash = content_identity_sha256(update["payload"])
        document_raw = seen_raw.setdefault(document_id, set())
        document_identity = seen_identity.setdefault(document_id, set())
        already_substantive = document_id in substantive_documents
        if policy.dedupe_scope == "document" and already_substantive:
            substance[update_id] = (False, DUPLICATE_FETCH)
        elif raw_hash in document_raw:
            substance[update_id] = (
                (True, PROMPTED) if not policy.dedupe_repeat_fetches else (False, DUPLICATE_FETCH)
            )
        elif identity_hash in document_identity:
            substance[update_id] = (
                (True, PROMPTED) if policy.formatting_is_substantive else (False, FORMAT_ONLY)
            )
        else:
            substance[update_id] = (True, PROMPTED)
            substantive_documents.add(document_id)
        document_raw.add(raw_hash)
        document_identity.add(identity_hash)
    return substance


def gap_flags(
    scenario: dict[str, Any],
    moment: datetime,
    conditions: list[dict[str, Any]],
    tracked_end_by_condition: dict[str, str | None],
    policy: ReplayPolicy,
) -> list[dict[str, Any]]:
    """Report collection gaps. A gap is never treated as a lift."""

    flags: list[dict[str, Any]] = []
    latest_by_source: dict[str, dict[str, Any]] = {}
    for collection in scenario["collections"]:
        if _as_datetime(collection["observed_at"]) > moment:
            continue
        source_id = collection.get("source_id") or collection["slot_id"]
        known = latest_by_source.get(source_id)
        if known is None or _as_datetime(collection["observed_at"]) >= _as_datetime(known["observed_at"]):
            latest_by_source[source_id] = collection
    for collection in latest_by_source.values():
        complete = bool(collection.get("snapshot_complete", True))
        for source_id in collection.get("unreachable_source_ids", []):
            flags.append(
                {
                    "kind": GAP_CONNECTION_FAILURE,
                    "source_id": source_id,
                    "slot_id": collection["slot_id"],
                    "observed_at": collection["observed_at"],
                    "auto_lifted": False,
                }
            )
        for source_id in collection.get("partial_source_ids", []):
            flags.append(
                {
                    "kind": GAP_PARTIAL_COVERAGE,
                    "source_id": source_id,
                    "slot_id": collection["slot_id"],
                    "observed_at": collection["observed_at"],
                    "auto_lifted": False,
                }
            )
        if not complete:
            continue
        visible = set(collection.get("visible_update_ids", []))
        for update in scenario["updates"]:
            if update.get("upstream_update_id"):
                continue
            if collection.get("source_id") and update["source_id"] != collection["source_id"]:
                continue
            if update["update_id"] in visible:
                continue
            if _as_datetime(update["acquired_at"]) > _as_datetime(collection["observed_at"]):
                continue
            flags.append(
                {
                    "kind": GAP_SOURCE_ITEM_MISSING,
                    "source_id": update["source_id"],
                    "slot_id": collection["slot_id"],
                    "observed_at": collection["observed_at"],
                    "document_id": update["document_id"],
                    "auto_lifted": False,
                }
            )
    for condition in conditions:
        if not _condition_active(condition, moment):
            continue
        tracked_end = tracked_end_by_condition.get(condition["condition_id"])
        if not tracked_end or policy.auto_lift_on_tracked_end:
            continue
        if _as_datetime(tracked_end) <= moment:
            flags.append(
                {
                    "kind": GAP_SCHEDULED_END_PASSED,
                    "condition_id": condition["condition_id"],
                    "observed_at": tracked_end,
                    "auto_lifted": False,
                }
            )
    unique: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for flag in flags:
        key = (
            flag["kind"],
            flag.get("source_id"),
            flag.get("condition_id"),
            flag.get("document_id"),
            flag["observed_at"],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(flag)
    return unique


def observation_status(gap_flags: list[dict[str, Any]], prompted: list[str]) -> str:
    if prompted:
        return NEW_UPDATES
    kinds = {flag["kind"] for flag in gap_flags}
    if kinds & {GAP_CONNECTION_FAILURE, GAP_PARTIAL_COVERAGE, GAP_SOURCE_ITEM_MISSING}:
        return SOURCE_GAP
    if GAP_SCHEDULED_END_PASSED in kinds:
        return STALE_TRACKED_END
    return NO_NEW_ITEMS


def replay(scenario: dict[str, Any], policy: ReplayPolicy = DEFAULT_POLICY) -> dict[str, Any]:
    """Replay every reopen point and return the machine-readable receipt."""

    validate_scenario(scenario)
    cutoff = _as_datetime(scenario["data_cutoff"])
    conditions = list(scenario["conditions"])
    updates = sorted(
        scenario["updates"],
        key=lambda item: (_as_datetime(item["acquired_at"]), item["update_id"]),
    )
    substance = _classify_substance(updates, policy)
    acquired = [
        update["update_id"]
        for update in updates
        if _as_datetime(update["acquired_at"]) <= cutoff
    ]

    read_update_ids: set[str] = set()
    tracked_end_by_condition: dict[str, str | None] = {
        condition["condition_id"]: condition.get("tracked_end") for condition in conditions
    }
    lifted_by_update_id: dict[str, str] = {}
    reopen_points: list[dict[str, Any]] = []
    counters = {
        "prompted_updates": 0,
        "multi_condition_hit": 0,
        "prompted_condition_pairs": 0,
        "source_acquisition_gap": 0,
    }
    multi_condition_updates: set[str] = set()
    updates_by_id = {update["update_id"]: update for update in updates}
    condition_kind = {condition["condition_id"]: condition["kind"] for condition in conditions}

    for reopen in scenario["reopen_points"]:
        moment = _as_datetime(reopen["reopened_at"])
        gaps = gap_flags(scenario, moment, conditions, tracked_end_by_condition, policy)
        dispositions: list[dict[str, Any]] = []
        prompted: list[str] = []
        for update in updates:
            update_id = update["update_id"]
            acquired_at = _as_datetime(update["acquired_at"])
            if acquired_at > moment or acquired_at > cutoff:
                dispositions.append({"update_id": update_id, "decision": NOT_YET_ACQUIRED, "conditions": []})
                continue
            is_substantive, reason = substance[update_id]
            if not is_substantive:
                dispositions.append({"update_id": update_id, "decision": reason, "conditions": []})
                continue
            matched, match_reason = match_conditions(
                update, conditions, moment, policy, tracked_end_by_condition
            )
            if not matched:
                dispositions.append({"update_id": update_id, "decision": match_reason, "conditions": []})
                continue
            if update_id in read_update_ids:
                dispositions.append(
                    {"update_id": update_id, "decision": ALREADY_READ, "conditions": list(matched)}
                )
                if policy.track_read_state:
                    continue
                # The arm deliberately lost read tracking. The prompt is still an
                # already-read repeat, so it must be counted as one.
                prompted.append(update_id)
                continue
            dispositions.append({"update_id": update_id, "decision": PROMPTED, "conditions": list(matched)})
            prompted.append(update_id)

        prompted_sorted = sorted(prompted)
        point_multi = sorted(
            disposition["update_id"]
            for disposition in dispositions
            if disposition["decision"] == PROMPTED and len(disposition["conditions"]) > 1
        )
        multi_condition_updates.update(point_multi)
        counters["prompted_updates"] += len(prompted_sorted)
        counters["multi_condition_hit"] += len(point_multi)
        counters["prompted_condition_pairs"] += sum(
            len(disposition["conditions"])
            for disposition in dispositions
            if disposition["update_id"] in set(prompted_sorted)
        )
        counters["source_acquisition_gap"] += len(gaps)

        # A deferral moves the tracked end forward; an explicit lift is recorded
        # against the condition. Neither ever happens because a date arrived.
        for disposition in dispositions:
            if disposition["decision"] != PROMPTED:
                continue
            payload = updates_by_id[disposition["update_id"]]["payload"]
            effective_to = payload.get("effective_to")
            if payload.get("status") == "LIFTED":
                for condition_id in disposition["conditions"]:
                    kind = condition_kind[condition_id]
                    if kind == "ROAD_CONDITION":
                        lifted_by_update_id.setdefault(condition_id, disposition["update_id"])
            if not effective_to:
                continue
            for condition_id in disposition["conditions"]:
                current = tracked_end_by_condition.get(condition_id)
                if not current or _as_datetime(effective_to) > _as_datetime(current):
                    tracked_end_by_condition[condition_id] = effective_to

        condition_status = {
            condition["condition_id"]: _condition_status(condition, moment, lifted_by_update_id)
            for condition in conditions
        }
        # The gap set stays as the commuter saw it at this reopen: a deferral read
        # in the same session must not erase the stale tracked end it replaced.
        status = reopen.get("observation_status") or observation_status(gaps, prompted_sorted)

        declared_read = reopen.get("read_update_ids")
        if declared_read is None:
            read_update_ids.update(prompted_sorted)
        else:
            for update_id in declared_read:
                if update_id not in acquired:
                    raise ScenarioError(
                        f"reopen {reopen['reopen_id']} marks an unacquired update read: {update_id}"
                    )
                read_update_ids.add(update_id)

        reopen_points.append(
            {
                "reopen_id": reopen["reopen_id"],
                "reopened_at": reopen["reopened_at"],
                "observation_status": status,
                "prompted_update_ids": prompted_sorted,
                "acquired_update_ids": sorted(
                    update["update_id"]
                    for update in updates
                    if _as_datetime(update["acquired_at"]) <= min(moment, cutoff)
                ),
                "multi_condition_update_ids": point_multi,
                "condition_status": condition_status,
                "gap_flags": gaps,
                "dispositions": dispositions,
                "public_original_available": True,
            }
        )

    return {
        "schema_version": REPLAY_RECEIPT_VERSION,
        "fingerprint": FINGERPRINT,
        "scenario_id": scenario["scenario_id"],
        "synthetic": True,
        "notice": "合成資料：測試路 A 至 B／測試路 C 至 D，不代表任何真實路況或道路安全。",
        "data_hash": scenario_hash(scenario),
        "data_cutoff": scenario["data_cutoff"],
        "clock": {
            "reopen_count": len(reopen_points),
            "last_reopened_at": reopen_points[-1]["reopened_at"] if reopen_points else None,
        },
        "versions": {
            "policy_version": policy.policy_version,
            "parser_version": PARSER_VERSION,
            "model_version": SEMANTIC_MODEL_VERSION,
            "prompt_hash": SEMANTIC_PROMPT_HASH,
        },
        "policy": policy.to_dict(),
        "publication_receipt": {
            "acquired_update_ids": acquired,
            "acquired_count": len(acquired),
            "collection_slots": [item["slot_id"] for item in scenario["collections"]],
            "content_identity": [
                {
                    "update_id": update["update_id"],
                    "document_id": update["document_id"],
                    "raw_sha256": update["content_sha256"],
                    "content_identity_sha256": content_identity_sha256(update["payload"]),
                }
                for update in updates
            ],
        },
        "reopen_points": reopen_points,
        "counters": counters,
        "multi_condition_update_ids": sorted(multi_condition_updates),
        "condition_summary": {
            "tracked_end_by_condition": dict(sorted(tracked_end_by_condition.items())),
            "lifted_by_update_id": dict(sorted(lifted_by_update_id.items())),
        },
        "cost_scope": {
            "queries_completed": len(reopen_points),
            "effective_updates": sum(1 for value in substance.values() if value[0]),
            "tracked_conditions": len(conditions),
            "model_requests": 0,
            "human_hours": None,
            "status": "REPLAY_ONLY",
        },
    }


def prediction_rows(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    """Project a receipt into evaluator prediction rows."""

    rows: list[dict[str, Any]] = []
    for point in receipt["reopen_points"]:
        dispositions = {item["update_id"]: item["decision"] for item in point["dispositions"]}
        rows.append(
            {
                "case_id": point["reopen_id"],
                "prediction": {
                    "update_ids": list(point["prompted_update_ids"]),
                    "observation_status": point["observation_status"],
                    "gap_kinds": sorted({flag["kind"] for flag in point["gap_flags"]}),
                    "condition_status": dict(sorted(point["condition_status"].items())),
                    "decision_by_update_id": dispositions,
                    "multi_condition_update_ids": list(point["multi_condition_update_ids"]),
                },
            }
        )
    return rows