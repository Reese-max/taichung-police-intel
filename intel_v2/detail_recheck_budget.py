"""Request budget, bounded backoff and pending-retry state for detail rechecks.

A 429 or 5xx from one official host must never turn into a site-wide re-crawl,
and a source that asked for a longer cool-down must be honoured.  This module
owns only the admission decision and the queryable pending-retry ledger; the
transport and the classification stay in :mod:`intel_v2.detail_recheck` and
:mod:`intel_v2.detail_recheck_http`.

The attempt ledger is the per-target ``attempts`` log.  Per-source and per-host
caps aggregate that log over the candidate batch the caller passes in, so the
cap is exact for a run and conservative (never optimistic by more than one
window) across runs.
"""
from __future__ import annotations

import copy
from datetime import timedelta
from typing import Any

from intel_v2.detail_recheck import _stamp, _timestamp


MAX_RETRY_AFTER_SECONDS = 86400
DEFAULT_RECHECK_BUDGET_POLICY = {
    "per_source_limit": 3,
    "per_host_limit": 5,
    "window_seconds": 3600,
    "backoff_seconds": 900,
    "backoff_factor": 2,
    "max_backoff_seconds": 21600,
    "backoff_deadline_hours": 24,
}
POLICY_KEYS = tuple(DEFAULT_RECHECK_BUDGET_POLICY)
BACKING_OFF_STATUSES = {"DEFERRED"}
SUCCESS_STATUSES = {
    "BASELINE",
    "UNCHANGED",
    "MATERIAL_CHANGE",
    "ATTACHMENT_CHANGED",
    "PRESENTATION_ONLY",
    "NOT_MODIFIED",
}
ALLOWED_REASON = "ALLOWED"
DEFER_REASONS = (
    "RETRY_DEADLINE_EXCEEDED",
    "BACKOFF_ACTIVE",
    "SOURCE_BUDGET_EXHAUSTED",
    "HOST_BUDGET_EXHAUSTED",
    "RUN_LIMIT_REACHED",
)
OUTCOME_REASONS = ("BACKED_OFF", "COMPLETED", "INCOMPLETE", "NOT_DUE")
REFUSAL_REASONS = ("INVALID_BUDGET_STATE",)
KNOWN_REASONS = (ALLOWED_REASON, *DEFER_REASONS, *OUTCOME_REASONS, *REFUSAL_REASONS)


def empty_budget_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "attempts": [],
        "failure_streak": 0,
        "deferred_until": None,
        "first_deferred_at": None,
        "deadline_at": None,
        "retry_deadline_exceeded": False,
        "acknowledged_at": None,
        "last_decision": None,
        "last_decided_at": None,
    }


def _policy(policy: dict[str, Any] | None) -> dict[str, Any]:
    resolved = dict(DEFAULT_RECHECK_BUDGET_POLICY)
    if policy is None:
        return resolved
    if not isinstance(policy, dict):
        raise ValueError("recheck budget policy must be an object")
    unknown = sorted(set(policy) - set(POLICY_KEYS))
    if unknown:
        raise ValueError(f"unsupported recheck budget policy keys: {unknown}")
    for key in POLICY_KEYS:
        if key in policy:
            value = policy[key]
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{key} must be a positive integer")
            resolved[key] = value
    return resolved


def recheck_budget_policy(policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a validated policy copy, raising before any state is touched."""
    return _policy(policy)


def _attempts(value: Any, window_seconds: int, now: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ValueError("budget_state attempts must be a list of timestamps")
    floor = now - timedelta(seconds=window_seconds)
    kept = set()
    for item in value:
        stamp = _timestamp(item)
        if floor < stamp <= now:
            kept.add(_stamp(stamp))
    return sorted(kept)


def normalize_budget_state(value: Any, *, now: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a validated copy of a stored budget ledger."""
    resolved = _policy(policy)
    current = _timestamp(now)
    if value is None:
        return empty_budget_state()
    if not isinstance(value, dict):
        raise ValueError("budget_state must be an object")
    state = empty_budget_state()
    state["attempts"] = _attempts(value.get("attempts"), resolved["window_seconds"], current)
    streak = value.get("failure_streak", 0)
    if not isinstance(streak, int) or isinstance(streak, bool) or streak < 0:
        raise ValueError("failure_streak must be a non-negative integer")
    state["failure_streak"] = streak
    for key in ("deferred_until", "first_deferred_at", "deadline_at", "acknowledged_at", "last_decided_at"):
        stamp = value.get(key)
        if stamp is not None:
            state[key] = _stamp(_timestamp(stamp))
    if value.get("last_decision") is not None:
        decision = str(value["last_decision"])
        if decision not in KNOWN_REASONS:
            raise ValueError("last_decision is not a known recheck budget decision")
        state["last_decision"] = decision
    state["retry_deadline_exceeded"] = _deadline_exceeded(state, current)
    return state


def _deadline_exceeded(state: dict[str, Any], now: Any) -> bool:
    deadline = state.get("deadline_at")
    if not deadline:
        return False
    return now >= _timestamp(deadline)


def _target(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("recheck budget target must be an object")
    for key in ("source_id", "stable_key", "host"):
        field = value.get(key)
        if not isinstance(field, str) or not field.strip():
            raise ValueError(f"{key} is required for a recheck budget target")
    return value


def plan_recheck_budget(
    targets: list[dict[str, Any]],
    *,
    now: str,
    policy: dict[str, Any] | None = None,
    run_limit: int | None = None,
) -> list[dict[str, Any]]:
    """Decide which due detail targets may be requested in this run.

    Each granted request consumes a per-source, per-host and (when given) a
    per-run slot for the rest of the batch, so a run can never exceed the
    configured caps even when the database hands back more due rows than the
    caps allow.  A target whose stored ledger cannot be decoded is refused as
    ``INVALID_BUDGET_STATE`` instead of raising, so one poisoned row cannot
    abort a whole collection run.
    """
    resolved = _policy(policy)
    current = _timestamp(now)
    if not isinstance(targets, list):
        raise ValueError("recheck budget targets must be a list")
    if run_limit is not None and (not isinstance(run_limit, int) or isinstance(run_limit, bool) or run_limit < 0):
        raise ValueError("run_limit must be a non-negative integer")
    states = [_target(item) for item in targets]
    normalized: list[dict[str, Any] | None] = []
    invalid: list[dict[str, Any]] = []
    for item in states:
        try:
            normalized.append(normalize_budget_state(item.get("budget_state"), now=now, policy=resolved))
        except (TypeError, ValueError):
            normalized.append(None)
            invalid.append(item)
    source_totals: dict[str, int] = {}
    host_totals: dict[str, int] = {}
    for state, item in zip(normalized, states):
        if state is None:
            continue
        source_totals[item["source_id"]] = source_totals.get(item["source_id"], 0) + len(state["attempts"])
        host_totals[item["host"]] = host_totals.get(item["host"], 0) + len(state["attempts"])

    decisions = []
    granted_sources: dict[str, int] = {}
    granted_hosts: dict[str, int] = {}
    granted_total = 0
    for state, item in zip(normalized, states):
        source = item["source_id"]
        host = item["host"]
        if state is None:
            # The stored ledger could not be decoded, so the target is refused
            # for one window and the row is rewritten with a clean, listable and
            # acknowledgeable refusal state instead of staying poisoned.
            reason = "INVALID_BUDGET_STATE"
            due_at = _stamp(current + timedelta(seconds=resolved["window_seconds"]))
            decision_state = {
                "schema_version": 1,
                "attempts": [],
                "failure_streak": 0,
                "deferred_until": due_at,
                "first_deferred_at": _stamp(current),
                "deadline_at": _stamp(current + timedelta(hours=resolved["backoff_deadline_hours"])),
                "retry_deadline_exceeded": False,
                "acknowledged_at": None,
                "last_decision": reason,
                "last_decided_at": _stamp(current),
            }
            source_used = source_totals.get(source, 0)
            host_used = host_totals.get(host, 0)
            decisions.append(
                {
                    "source_id": source,
                    "stable_key": item["stable_key"],
                    "host": host,
                    "decision": "DEFER",
                    "reason": reason,
                    "due_at": due_at,
                    "source_attempts_in_window": source_used,
                    "host_attempts_in_window": host_used,
                    "failure_streak": 0,
                    "retry_deadline_exceeded": False,
                    "budget_state": decision_state,
                }
            )
            continue
        source_used = source_totals[source] + granted_sources.get(source, 0)
        host_used = host_totals[host] + granted_hosts.get(host, 0)
        if _deadline_exceeded(state, current):
            reason = "RETRY_DEADLINE_EXCEEDED"
        elif state["deferred_until"] and current < _timestamp(state["deferred_until"]):
            reason = "BACKOFF_ACTIVE"
        elif source_used >= resolved["per_source_limit"]:
            reason = "SOURCE_BUDGET_EXHAUSTED"
        elif host_used >= resolved["per_host_limit"]:
            reason = "HOST_BUDGET_EXHAUSTED"
        elif run_limit is not None and granted_total >= run_limit:
            reason = "RUN_LIMIT_REACHED"
        else:
            reason = ALLOWED_REASON
        if reason == ALLOWED_REASON:
            granted_sources[source] = granted_sources.get(source, 0) + 1
            granted_hosts[host] = granted_hosts.get(host, 0) + 1
            granted_total += 1
            due_at = None
        elif reason == "BACKOFF_ACTIVE":
            due_at = state["deferred_until"]
        elif reason == "RETRY_DEADLINE_EXCEEDED":
            due_at = _stamp(current + timedelta(hours=resolved["backoff_deadline_hours"]))
        elif reason == "RUN_LIMIT_REACHED":
            due_at = None
        else:
            due_at = _stamp(current + timedelta(seconds=resolved["window_seconds"]))
        decision_state = dict(state)
        decision_state["retry_deadline_exceeded"] = _deadline_exceeded(state, current)
        decision_state["last_decision"] = reason
        decision_state["last_decided_at"] = _stamp(current)
        decisions.append(
            {
                "source_id": source,
                "stable_key": item["stable_key"],
                "host": host,
                "decision": "ALLOW" if reason == ALLOWED_REASON else "DEFER",
                "reason": reason,
                "due_at": due_at,
                "source_attempts_in_window": source_used,
                "host_attempts_in_window": host_used,
                "failure_streak": state["failure_streak"],
                "retry_deadline_exceeded": decision_state["retry_deadline_exceeded"],
                "budget_state": decision_state,
            }
        )
    return decisions


def record_recheck_budget(
    state: Any,
    classification: dict[str, Any],
    *,
    now: str,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the next ledger entry for one finished or refused recheck."""
    resolved = _policy(policy)
    current = _timestamp(now)
    if not isinstance(classification, dict):
        raise ValueError("classification must be an object")
    status = classification.get("status")
    if not isinstance(status, str) or not status:
        raise ValueError("classification status is required")
    budget_state = normalize_budget_state(state, now=now, policy=resolved)
    budget_state["attempts"] = _attempts(
        [*budget_state["attempts"], _stamp(current)], resolved["window_seconds"], current
    )
    if status in BACKING_OFF_STATUSES:
        outcome = "BACKED_OFF"
        retry_after = classification.get("retry_after_seconds")
        if retry_after is not None and (not isinstance(retry_after, int) or isinstance(retry_after, bool) or retry_after < 0):
            raise ValueError("retry_after_seconds must be a non-negative integer")
        budget_state["failure_streak"] += 1
        backoff = min(
            resolved["max_backoff_seconds"],
            resolved["backoff_seconds"] * resolved["backoff_factor"] ** (budget_state["failure_streak"] - 1),
        )
        if retry_after is not None:
            backoff = max(backoff, min(retry_after, MAX_RETRY_AFTER_SECONDS))
        budget_state["deferred_until"] = _stamp(current + timedelta(seconds=backoff))
        if budget_state["first_deferred_at"] is None:
            budget_state["first_deferred_at"] = _stamp(current)
        budget_state["deadline_at"] = _stamp(
            _timestamp(budget_state["first_deferred_at"]) + timedelta(hours=resolved["backoff_deadline_hours"])
        )
    else:
        outcome = "COMPLETED" if status in SUCCESS_STATUSES else "INCOMPLETE"
        if status in SUCCESS_STATUSES:
            budget_state["failure_streak"] = 0
            budget_state["deferred_until"] = None
            budget_state["first_deferred_at"] = None
            budget_state["deadline_at"] = None
        # Any other non-success status (an unavailable document, a transport
        # error) consumes an attempt but must not erase an outstanding
        # backoff streak, or an alternating 429/404 source could defer its
        # retry deadline forever.
    budget_state["retry_deadline_exceeded"] = _deadline_exceeded(budget_state, current)
    budget_state["last_decision"] = outcome
    budget_state["last_decided_at"] = _stamp(current)
    return budget_state


def acknowledge_recheck_budget(state: Any, *, now: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Clear a blocked retry ledger only on an explicit human decision."""
    resolved = _policy(policy)
    current = _timestamp(now)
    budget_state = normalize_budget_state(state, now=now, policy=resolved)
    budget_state.update(
        {
            "failure_streak": 0,
            "deferred_until": None,
            "first_deferred_at": None,
            "deadline_at": None,
            "retry_deadline_exceeded": False,
            "acknowledged_at": _stamp(current),
            "last_decided_at": _stamp(current),
        }
    )
    return budget_state


def pending_retry_rows(states: list[dict[str, Any]], *, now: str) -> list[dict[str, Any]]:
    """Return the queryable pending-retry view for stored recheck ledgers."""
    current = _timestamp(now)
    if not isinstance(states, list):
        raise ValueError("pending retry states must be a list")
    rows = []
    for item in states:
        target = _target(item)
        state = normalize_budget_state(target.get("budget_state"), now=now)
        deferred_until = state["deferred_until"]
        if not deferred_until and not state["retry_deadline_exceeded"]:
            continue
        deferred = _timestamp(deferred_until) if deferred_until else None
        deadline = _timestamp(state["deadline_at"]) if state["deadline_at"] else None
        rows.append(
            {
                "source_id": target["source_id"],
                "stable_key": target["stable_key"],
                "host": target["host"],
                "deferred_until": deferred_until,
                "deadline_at": state["deadline_at"],
                "failure_streak": state["failure_streak"],
                "retry_deadline_exceeded": state["retry_deadline_exceeded"],
                "seconds_until_retry": max(int((deferred - current).total_seconds()), 0) if deferred else 0,
                "seconds_until_deadline": max(int((deadline - current).total_seconds()), 0) if deadline else None,
                "attempts_in_window": len(state["attempts"]),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            not row["retry_deadline_exceeded"],
            row["deferred_until"] or "",
            row["source_id"],
            row["stable_key"],
        ),
    )


def budget_state_for_storage(state: Any, *, now: str, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the canonical JSONB payload for ``detail_recheck_state.budget_state``."""
    return copy.deepcopy(normalize_budget_state(state, now=now, policy=policy))
