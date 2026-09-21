from __future__ import annotations

import copy
import math
import re
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlparse

from intel_v2.semantics import canonical_sha256


SCHEMA_VERSION = 1
MAX_DURATION_SECONDS = 4 * 60 * 60
SESSION_STATUSES = {
    "PREPARING",
    "LIVE",
    "DEGRADED",
    "ENDED",
    "RECONCILING",
    "RECONCILED",
    "FAILED",
}
RECONCILIATION_OUTCOMES = {
    "CONFIRMED_BY_OFFICIAL_MEDIA",
    "CONFIRMED_BY_MINUTES",
    "SUPERSEDED_TRANSCRIPT",
    "UNRESOLVED",
    "SOURCE_NOT_YET_AVAILABLE",
    "DROPPED_FALSE_POSITIVE",
}
TERMINAL_RECONCILIATION_OUTCOMES = {
    "CONFIRMED_BY_OFFICIAL_MEDIA",
    "CONFIRMED_BY_MINUTES",
    "SUPERSEDED_TRANSCRIPT",
    "DROPPED_FALSE_POSITIVE",
}
ALLOWED_SOURCE_HOSTS = {
    "S-004": {"www.tccc.gov.tw", "tccc.gov.tw"},
    "S-006": {"www.tccc.gov.tw", "tccc.gov.tw"},
    "S-007": {"yishi.tccc.gov.tw"},
    "S-010": {"www.tccc.gov.tw", "tccc.gov.tw", "vod.tccc.gov.tw", "streamak0128.akamaized.net"},
    "S-011": {"www.tccc.gov.tw", "tccc.gov.tw", "vod.tccc.gov.tw", "streamak0128.akamaized.net"},
}
SPEAKER_LABEL = re.compile(r"^(?:SPEAKER_[0-9]+|UNKNOWN|UNVERIFIED)$")
REASON_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,48}$")
PUBLIC_IDENTIFIER = re.compile(r"(?:[\w.+-]+@[\w.-]+|09\d{8}|\b\d{10,}\b)")


def _stamp(value: str | datetime) -> str:
    if not isinstance(value, (str, datetime)):
        raise ValueError("timestamp must be an ISO string or datetime")
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.isoformat(timespec="seconds")


def _text(value: object, field: str, *, max_length: int = 20_000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    value = value.strip()
    if len(value) > max_length:
        raise ValueError(f"{field} exceeds its size limit")
    return value


def _official_url(source_id: str, value: object) -> str:
    url = _text(value, "official URL", max_length=2_000)
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("official URL must be HTTPS without credentials")
    if parsed.hostname.lower() not in ALLOWED_SOURCE_HOSTS.get(source_id, set()):
        raise ValueError(f"URL host is not allowlisted for {source_id}")
    return url


def _number(value: object, field: str, *, minimum: float = 0.0) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < minimum
    ):
        raise ValueError(f"{field} must be a number >= {minimum}")
    return round(float(value), 3)


def _copy(state: dict[str, Any]) -> dict[str, Any]:
    validate_session(state)
    return copy.deepcopy(state)


def _full_hash(state: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in state.items()
        if key not in {"content_hash", "stream_content_hash"}
    }
    return canonical_sha256(payload)


def _stream_hash(state: dict[str, Any]) -> str:
    return canonical_sha256({
        "segments": state["segments"],
        "gap_intervals": state["gap_intervals"],
    })


def _touch(state: dict[str, Any], updated_at: str | datetime) -> dict[str, Any]:
    state["updated_at"] = _stamp(updated_at)
    state["content_hash"] = _full_hash(state)
    state["stream_content_hash"] = _stream_hash(state)
    return state


def _bump(state: dict[str, Any]) -> None:
    state["session_revision"] += 1


def _check_interval(start: object, end: object, maximum: float) -> tuple[float, float]:
    first = _number(start, "start_seconds")
    last = _number(end, "end_seconds")
    if last <= first or last > maximum:
        raise ValueError("time range must be increasing and within max_duration")
    return first, last


def _check_speaker(value: object) -> str | None:
    if value is None:
        return None
    speaker = _text(value, "speaker_label", max_length=40).upper()
    if not SPEAKER_LABEL.fullmatch(speaker):
        raise ValueError("speaker_label must remain an unverified speaker token")
    return speaker


def _gap_id(state: dict[str, Any], start: float, end: float, reason: str) -> str:
    return "GAP-" + canonical_sha256([state["session_id"], start, end, reason]).upper()[:20]


def _add_gap(state: dict[str, Any], start: float, end: float, reason: str, at: str) -> None:
    for gap in state["gap_intervals"]:
        if not (end <= gap["start_seconds"] or start >= gap["end_seconds"]):
            raise ValueError("gap intervals cannot overlap")
    for segment in state["segments"]:
        if not (end <= segment["start_seconds"] or start >= segment["end_seconds"]):
            raise ValueError("gap cannot overlap a transcript segment")
    state["gap_intervals"].append(
        {
            "gap_id": _gap_id(state, start, end, reason),
            "start_seconds": start,
            "end_seconds": end,
            "reason": _text(reason, "gap reason", max_length=80),
            "detected_at": at,
        }
    )
    state["gap_intervals"].sort(key=lambda row: (row["start_seconds"], row["end_seconds"], row["gap_id"]))


def start_session(
    *,
    source_id: str,
    official_source_url: str,
    stream_url: str,
    started_at: str | datetime,
    asr_contract: dict[str, Any],
    meeting_id: str | None = None,
    agenda_id: str | None = None,
    max_duration_seconds: int = 1_800,
    transport_enabled: bool = False,
    provider_authorized: bool = False,
    budget_seconds: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    if source_id not in ALLOWED_SOURCE_HOSTS:
        raise ValueError(f"live meeting source is not allowlisted: {source_id}")
    official_source_url = _official_url(source_id, official_source_url)
    stream_url = _official_url(source_id, stream_url)
    if isinstance(max_duration_seconds, bool) or not isinstance(max_duration_seconds, int):
        raise ValueError("max_duration_seconds must be an integer")
    if not 1 <= max_duration_seconds <= MAX_DURATION_SECONDS:
        raise ValueError("max_duration_seconds is outside the bounded range")
    if not isinstance(asr_contract, dict):
        raise ValueError("asr_contract must be an object")
    contract = {
        "provider": _text(asr_contract.get("provider"), "asr provider", max_length=80),
        "model": _text(asr_contract.get("model"), "asr model", max_length=120),
        "version": _text(asr_contract.get("version"), "asr version", max_length=80),
        "language": _text(asr_contract.get("language", "zh"), "asr language", max_length=20),
    }
    started = _stamp(started_at)
    if meeting_id is not None:
        meeting_id = _text(meeting_id, "meeting_id", max_length=160)
    if agenda_id is not None:
        agenda_id = _text(agenda_id, "agenda_id", max_length=160)
    if transport_enabled:
        if not provider_authorized:
            status = "PREPARING"
            transport_status = "BLOCKED_NO_AUTH"
        else:
            if budget_seconds is None or not isinstance(budget_seconds, int) or not 1 <= budget_seconds <= max_duration_seconds:
                raise ValueError("authorized transport requires a bounded budget_seconds")
            status = "LIVE"
            transport_status = "READY"
    else:
        if budget_seconds is not None:
            raise ValueError("budget_seconds requires explicit transport_enabled")
        status = "PREPARING"
        transport_status = "NOT_STARTED"
    derived_id = session_id or "LM-" + canonical_sha256(
        [source_id, official_source_url, stream_url, started]
    ).upper()[:20]
    state = {
        "schema_version": SCHEMA_VERSION,
        "session_id": _text(derived_id, "session_id", max_length=120),
        "source_id": source_id,
        "official_source_url": official_source_url,
        "stream_url": stream_url,
        "meeting_id": meeting_id,
        "agenda_id": agenda_id,
        "started_at": started,
        "ended_at": None,
        "max_duration_seconds": max_duration_seconds,
        "budget_seconds": budget_seconds,
        "transport_status": transport_status,
        "asr_contract": contract,
        "status": status,
        "termination_reason": None,
        "session_revision": 1,
        "segments": [],
        "gap_intervals": [],
        "bookmarks": [],
        "reconciliation_receipts": [],
        "current_reconciliations": {},
        "created_at": started,
        "updated_at": started,
    }
    return _touch(state, started)


def activate_session(
    state: dict[str, Any],
    *,
    activated_at: str | datetime,
    provider_authorized: bool,
    budget_seconds: int,
) -> dict[str, Any]:
    result = _copy(state)
    if result["status"] != "PREPARING":
        raise ValueError("only PREPARING sessions can be activated")
    if not provider_authorized:
        raise ValueError("transport blocked until provider authorization is explicit")
    if not isinstance(budget_seconds, int) or not 1 <= budget_seconds <= result["max_duration_seconds"]:
        raise ValueError("budget_seconds is outside the session bound")
    result["budget_seconds"] = budget_seconds
    result["transport_status"] = "READY"
    result["status"] = "LIVE"
    _bump(result)
    return _touch(result, activated_at)


def append_segment(
    state: dict[str, Any],
    *,
    sequence: int,
    start_seconds: float,
    end_seconds: float,
    text: str,
    received_at: str | datetime,
    finalized: bool = False,
    partial: bool = False,
    confidence: float | None = None,
    speaker_label: str | None = None,
) -> dict[str, Any]:
    result = _copy(state)
    if result["status"] not in {"LIVE", "DEGRADED"}:
        raise ValueError("segments can only be appended while LIVE or DEGRADED")
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
        raise ValueError("sequence must be a non-negative integer")
    start, end = _check_interval(start_seconds, end_seconds, result["max_duration_seconds"])
    text = _text(text, "transcript text")
    if confidence is not None and not 0 <= _number(confidence, "confidence") <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if not isinstance(finalized, bool) or not isinstance(partial, bool):
        raise ValueError("finalized and partial must be boolean")
    speaker = _check_speaker(speaker_label)
    segment_id = f"SEG-{result['session_id']}-{sequence:06d}"
    segments = result["segments"]
    current = next((item for item in segments if item["sequence"] == sequence), None)
    if current is None:
        for item in segments:
            if not (end <= item["start_seconds"] or start >= item["end_seconds"]):
                raise ValueError("segment time ranges cannot overlap")
        revision = 1
        history: list[dict[str, Any]] = []
    else:
        if current["start_seconds"] != start or current["end_seconds"] != end:
            raise ValueError("segment revisions must keep the same time range")
        revision = current["revision"] + 1
        history = list(current["revision_history"])
        history.append({
            key: current[key]
            for key in (
                "revision", "text", "content_sha256", "received_at", "finalized", "partial",
                "confidence", "speaker_label",
            )
        })
        segments.remove(current)
    content_sha256 = canonical_sha256({
        "text": text,
        "start_seconds": start,
        "end_seconds": end,
        "asr_contract": result["asr_contract"],
        "finalized": finalized,
        "partial": partial,
    })
    segments.append(
        {
            "segment_id": segment_id,
            "sequence": sequence,
            "start_seconds": start,
            "end_seconds": end,
            "text": text,
            "content_sha256": content_sha256,
            "confidence": None if confidence is None else round(float(confidence), 3),
            "speaker_label": speaker,
            "language": result["asr_contract"]["language"],
            "asr_contract": dict(result["asr_contract"]),
            "received_at": _stamp(received_at),
            "finalized": finalized,
            "partial": partial,
            "revision": revision,
            "revision_history": history,
        }
    )
    segments.sort(key=lambda item: item["sequence"])
    _bump(result)
    return _touch(result, received_at)


def record_gap(
    state: dict[str, Any],
    *,
    start_seconds: float,
    end_seconds: float,
    reason: str,
    detected_at: str | datetime,
) -> dict[str, Any]:
    result = _copy(state)
    if result["status"] not in {"LIVE", "DEGRADED"}:
        raise ValueError("gaps can only be recorded while LIVE or DEGRADED")
    start, end = _check_interval(start_seconds, end_seconds, result["max_duration_seconds"])
    _add_gap(result, start, end, reason, _stamp(detected_at))
    result["status"] = "DEGRADED"
    result["transport_status"] = "DEGRADED"
    _bump(result)
    return _touch(result, detected_at)


def resume_session(state: dict[str, Any], *, resumed_at: str | datetime) -> dict[str, Any]:
    result = _copy(state)
    if result["status"] not in {"DEGRADED", "FAILED"}:
        raise ValueError("only DEGRADED or FAILED sessions can be resumed")
    result["status"] = "LIVE"
    result["transport_status"] = "READY"
    result["termination_reason"] = None
    _bump(result)
    return _touch(result, resumed_at)


def stop_session(
    state: dict[str, Any],
    *,
    ended_at: str | datetime,
    reason: str = "USER_STOP",
) -> dict[str, Any]:
    result = _copy(state)
    if result["status"] not in {"PREPARING", "LIVE", "DEGRADED"}:
        raise ValueError("only active sessions can be stopped")
    ended = _stamp(ended_at)
    if datetime.fromisoformat(ended) < datetime.fromisoformat(result["started_at"]):
        raise ValueError("ended_at cannot precede started_at")
    result["ended_at"] = ended
    result["status"] = "ENDED"
    result["transport_status"] = "STOPPED"
    result["termination_reason"] = _text(reason, "termination reason", max_length=80)
    _bump(result)
    return _touch(result, ended)


def budget_stop(state: dict[str, Any], *, stopped_at: str | datetime) -> dict[str, Any]:
    result = _copy(state)
    if result["status"] not in {"LIVE", "DEGRADED"}:
        raise ValueError("only active sessions can hit a budget stop")
    last_end = max(
        [item["end_seconds"] for item in result["segments"]]
        + [gap["end_seconds"] for gap in result["gap_intervals"]]
        + [0.0]
    )
    if last_end < result["max_duration_seconds"]:
        _add_gap(result, last_end, result["max_duration_seconds"], "BUDGET_STOP", _stamp(stopped_at))
    result["status"] = "DEGRADED"
    result["transport_status"] = "BUDGET_STOP"
    result["termination_reason"] = "MAX_DURATION_OR_BUDGET"
    _bump(result)
    return _touch(result, stopped_at)


def add_bookmark(
    state: dict[str, Any],
    *,
    segment_ids: Iterable[str],
    reason_code: str,
    bookmarked_at: str | datetime,
    note: str | None = None,
    profile_relevance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _copy(state)
    selected = sorted(set(segment_ids))
    known = {item["segment_id"]: item for item in result["segments"]}
    if not selected or any(segment_id not in known for segment_id in selected):
        raise ValueError("bookmark must reference known segment IDs")
    if not isinstance(reason_code, str) or not REASON_CODE.fullmatch(reason_code):
        raise ValueError("reason_code must be an uppercase token")
    if note is not None:
        note = _text(note, "bookmark note", max_length=500)
        if PUBLIC_IDENTIFIER.search(note):
            raise ValueError("bookmark note cannot contain obvious personal identifiers")
    if profile_relevance is not None:
        if not isinstance(profile_relevance, dict) or set(profile_relevance) - {
            "profile_id", "profile_version", "profile_hash", "ranking_policy_version", "reason_codes"
        }:
            raise ValueError("profile_relevance contains unsupported fields")
    bookmark_id = "BM-" + canonical_sha256(
        [result["session_id"], selected, reason_code]
    ).upper()[:20]
    if any(item["bookmark_id"] == bookmark_id for item in result["bookmarks"]):
        return result
    first = min(known[segment_id]["start_seconds"] for segment_id in selected)
    last = max(known[segment_id]["end_seconds"] for segment_id in selected)
    result["bookmarks"].append(
        {
            "bookmark_id": bookmark_id,
            "segment_ids": selected,
            "start_seconds": first,
            "end_seconds": last,
            "reason_code": reason_code,
            "note": note,
            "profile_relevance": profile_relevance,
            "stream_content_hash": result["stream_content_hash"],
            "session_revision": result["session_revision"],
            "bookmarked_at": _stamp(bookmarked_at),
        }
    )
    _bump(result)
    return _touch(result, bookmarked_at)


def reconcile_session(
    state: dict[str, Any],
    candidates: Iterable[dict[str, Any]],
    *,
    reconciled_at: str | datetime,
) -> dict[str, Any]:
    result = _copy(state)
    if result["status"] not in {"ENDED", "RECONCILING", "RECONCILED"}:
        raise ValueError("session must be stopped before reconciliation")
    bookmarks = {item["bookmark_id"]: item for item in result["bookmarks"]}
    segments = {item["segment_id"]: item for item in result["segments"]}
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("reconciliation candidate must be an object")
        candidate_id = candidate.get("candidate_id") or candidate.get("bookmark_id")
        if candidate_id in bookmarks:
            segment_ids = bookmarks[candidate_id]["segment_ids"]
        else:
            segment_ids = candidate.get("segment_ids")
            if not isinstance(segment_ids, list) or not segment_ids:
                raise ValueError("reconciliation candidate needs a bookmark or segment_ids")
        if any(segment_id not in segments for segment_id in segment_ids):
            raise ValueError("reconciliation candidate references an unknown segment")
        candidate_id = _text(candidate_id or segment_ids[0], "candidate_id", max_length=160)
        outcome = candidate.get("outcome")
        if outcome not in RECONCILIATION_OUTCOMES:
            raise ValueError(f"unsupported reconciliation outcome: {outcome}")
        official = None
        if outcome in {"CONFIRMED_BY_OFFICIAL_MEDIA", "CONFIRMED_BY_MINUTES"}:
            official_source_id = candidate.get("official_source_id") or result["source_id"]
            official = {
                "source_id": official_source_id,
                "official_url": _official_url(official_source_id, candidate.get("official_url")),
                "locator": _text(candidate.get("official_locator"), "official_locator", max_length=300),
                "document_version": _text(candidate.get("official_document_version"), "official_document_version", max_length=160),
                "text_sha256": _text(candidate.get("official_text_sha256"), "official_text_sha256", max_length=64),
            }
            if not re.fullmatch(r"[0-9a-fA-F]{64}", official["text_sha256"]):
                raise ValueError("official_text_sha256 must be a SHA-256 hex digest")
        provisional_hash = canonical_sha256([
            {"segment_id": segment_id, "revision": segments[segment_id]["revision"], "content_sha256": segments[segment_id]["content_sha256"]}
            for segment_id in segment_ids
        ])
        material = {
            "session_id": result["session_id"],
            "candidate_id": candidate_id,
            "segment_ids": segment_ids,
            "outcome": outcome,
            "provisional_text_sha256": provisional_hash,
            "official": official,
            "reconciled_at": _stamp(reconciled_at),
        }
        receipt_id = "RC-" + canonical_sha256(material).upper()[:20]
        if any(receipt["receipt_id"] == receipt_id for receipt in result["reconciliation_receipts"]):
            continue
        result["reconciliation_receipts"].append(
            {
                "receipt_id": receipt_id,
                "session_id": result["session_id"],
                "candidate_id": candidate_id,
                "segment_ids": list(segment_ids),
                "outcome": outcome,
                "provisional_text_sha256": provisional_hash,
                "official": official,
                "asr_contract": dict(result["asr_contract"]),
                "reconciled_at": _stamp(reconciled_at),
                "session_revision": result["session_revision"],
            }
        )
        result["current_reconciliations"][candidate_id] = receipt_id
    current = {
        candidate_id: next(
            receipt for receipt in result["reconciliation_receipts"]
            if receipt["receipt_id"] == receipt_id
        )
        for candidate_id, receipt_id in result["current_reconciliations"].items()
    }
    expected = set(bookmarks)
    if not expected or expected <= set(current) and all(
        receipt["outcome"] in TERMINAL_RECONCILIATION_OUTCOMES for receipt in current.values()
    ):
        result["status"] = "RECONCILED"
    else:
        result["status"] = "RECONCILING"
    _bump(result)
    return _touch(result, reconciled_at)


def formal_candidates(state: dict[str, Any]) -> list[dict[str, Any]]:
    validate_session(state)
    receipts = {receipt["receipt_id"]: receipt for receipt in state["reconciliation_receipts"]}
    result = []
    for candidate_id, receipt_id in state["current_reconciliations"].items():
        receipt = receipts[receipt_id]
        if receipt["outcome"] not in {"CONFIRMED_BY_OFFICIAL_MEDIA", "CONFIRMED_BY_MINUTES"}:
            continue
        result.append(
            {
                "candidate_id": candidate_id,
                "segment_ids": receipt["segment_ids"],
                "evidence_type": "ORAL_OFFICIAL" if receipt["outcome"] == "CONFIRMED_BY_OFFICIAL_MEDIA" else "WRITTEN_OFFICIAL",
                "official": receipt["official"],
                "provisional_text_sha256": receipt["provisional_text_sha256"],
                "verification_status": "OFFICIAL_RECONCILED",
                "provisional": False,
                "receipt_id": receipt["receipt_id"],
            }
        )
    return result


def validate_session(state: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(state, dict):
        raise ValueError("live meeting session must be an object")
    if state.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported live meeting schema_version")
    if state.get("status") not in SESSION_STATUSES:
        raise ValueError("invalid live meeting status")
    if state.get("source_id") not in ALLOWED_SOURCE_HOSTS:
        raise ValueError("live meeting source is not allowlisted")
    if not isinstance(state.get("session_id"), str) or not state["session_id"].strip():
        raise ValueError("session_id is required")
    if isinstance(state.get("max_duration_seconds"), bool) or not isinstance(state.get("max_duration_seconds"), int):
        raise ValueError("max_duration_seconds must be an integer")
    if not 1 <= state["max_duration_seconds"] <= MAX_DURATION_SECONDS:
        raise ValueError("max_duration_seconds is outside the bounded range")
    if state.get("budget_seconds") is not None and (
        isinstance(state["budget_seconds"], bool)
        or not isinstance(state["budget_seconds"], int)
        or not 1 <= state["budget_seconds"] <= state["max_duration_seconds"]
    ):
        raise ValueError("budget_seconds is outside the session bound")
    for field in ("started_at", "created_at", "updated_at"):
        _stamp(state.get(field))
    if state.get("ended_at") is not None:
        _stamp(state["ended_at"])
    contract = state.get("asr_contract")
    if not isinstance(contract, dict) or any(
        not isinstance(contract.get(field), str) or not contract[field].strip()
        for field in ("provider", "model", "version", "language")
    ):
        raise ValueError("asr_contract is incomplete")
    _official_url(state["source_id"], state.get("official_source_url"))
    _official_url(state["source_id"], state.get("stream_url"))
    if not isinstance(state.get("segments"), list) or not isinstance(state.get("gap_intervals"), list):
        raise ValueError("segments and gap_intervals must be arrays")
    if not isinstance(state.get("bookmarks"), list) or not isinstance(state.get("reconciliation_receipts"), list):
        raise ValueError("bookmarks and reconciliation_receipts must be arrays")
    if not isinstance(state.get("current_reconciliations"), dict):
        raise ValueError("current_reconciliations must be an object")
    if not isinstance(state.get("session_revision"), int) or state["session_revision"] < 1:
        raise ValueError("session_revision must be positive")
    seen_sequences: set[int] = set()
    previous_end = -1.0
    segment_ids: set[str] = set()
    for segment in state["segments"]:
        if segment.get("sequence") in seen_sequences or segment.get("segment_id") in segment_ids:
            raise ValueError("segment sequence and ID must be unique")
        seen_sequences.add(segment["sequence"])
        segment_ids.add(segment["segment_id"])
        start, end = _check_interval(segment.get("start_seconds"), segment.get("end_seconds"), state["max_duration_seconds"])
        if start < previous_end:
            raise ValueError("segments must not overlap")
        previous_end = end
        expected_hash = canonical_sha256({
            "text": segment["text"],
            "start_seconds": start,
            "end_seconds": end,
            "asr_contract": segment["asr_contract"],
            "finalized": segment["finalized"],
            "partial": segment["partial"],
        })
        if segment.get("content_sha256") != expected_hash:
            raise ValueError("segment content hash mismatch")
        _check_speaker(segment.get("speaker_label"))
    previous_end = -1.0
    for gap in state["gap_intervals"]:
        start, end = _check_interval(gap.get("start_seconds"), gap.get("end_seconds"), state["max_duration_seconds"])
        if start < previous_end:
            raise ValueError("gap intervals must not overlap")
        previous_end = end
    for bookmark in state["bookmarks"]:
        if not bookmark.get("segment_ids") or any(item not in segment_ids for item in bookmark["segment_ids"]):
            raise ValueError("bookmark references an unknown segment")
    receipt_ids = {receipt.get("receipt_id") for receipt in state["reconciliation_receipts"]}
    if any(receipt_id not in receipt_ids for receipt_id in state["current_reconciliations"].values()):
        raise ValueError("current reconciliation points to an unknown receipt")
    if state.get("content_hash") != _full_hash(state):
        raise ValueError("session content hash mismatch")
    if state.get("stream_content_hash") != _stream_hash(state):
        raise ValueError("stream content hash mismatch")
    return state
