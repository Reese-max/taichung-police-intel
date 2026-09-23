"""Validated, read-only query projections for PublicEvent and statistics stores."""
from __future__ import annotations

import base64
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit


MAX_ROWS = 10000
MAX_LIMIT = 100
MAX_BYTES = 32 * 1024 * 1024
EVENT_STORE_SCHEMA_VERSION = 1
STATISTICS_STORE_SCHEMA_VERSION = 1
EVENT_STATUSES = frozenset({"CONFIRMED", "CANDIDATE", "CONFLICT", "SPLIT_REQUIRED", "PARTIAL_LKG"})


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value)).hexdigest()


def _load_json(path: Path) -> Any:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError("domain store input exceeds byte limit")
    return json.loads(raw.decode("utf-8"))


def _text(value: Any, *, name: str, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{name} must be a non-empty bounded string")
    return value.strip()


def _optional_text(value: Any, *, name: str, max_length: int = 256) -> str | None:
    if value is None:
        return None
    return _text(value, name=name, max_length=max_length)


def _instant(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _time_bound(value: Any, *, name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an ISO-8601 date or timestamp")
    try:
        if len(value) == 10:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time(), tzinfo=timezone.utc)
        else:
            parsed = _instant(value, name=name)
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 date or timestamp") from error
    return parsed


def _https(value: Any, *, name: str) -> str:
    text = _text(value, name=name, max_length=4096)
    parsed = urlsplit(text)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name} must be a credential-free HTTPS URL")
    return text


def _string_list(value: Any, *, name: str, max_items: int = 100) -> list[str]:
    if not isinstance(value, list) or len(value) > max_items:
        raise ValueError(f"{name} must be a bounded array")
    result = [_text(item, name=f"{name} item", max_length=256) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{name} must not contain duplicates")
    return result


def _safe_fields(value: Any, *, name: str) -> dict[str, str | int | float | bool | None]:
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError(f"{name} must be a bounded object")
    result = {}
    for key, item in value.items():
        safe_key = _text(key, name=f"{name} key", max_length=256)
        if item is not None and (isinstance(item, (dict, list)) or not isinstance(item, (str, int, float, bool))):
            raise ValueError(f"{name} values must be scalar")
        if isinstance(item, str) and len(item) > 2000:
            raise ValueError(f"{name} string value is too long")
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError(f"{name} contains a non-finite number")
        result[safe_key] = item
    return result


def _validate_event_version(version: Any, event_id: str) -> str:
    if not isinstance(version, dict):
        raise ValueError(f"event version must be an object: {event_id}")
    version_id = _text(version.get("document_version_id") or version.get("version_id"), name="document_version_id", max_length=256)
    for field in ("observed_at", "published_at", "effective_at"):
        if version.get(field) is not None:
            _instant(version[field], name=field)
    _safe_fields(version.get("fields", {}), name="event version fields")
    _string_list(version.get("changed_fields", []), name="event version changed_fields")
    return version_id


def validate_event(event: Any) -> dict[str, Any]:
    if not isinstance(event, dict) or event.get("schema_version") != 1:
        raise ValueError("PublicEvent must use schema_version=1")
    event_id = _text(event.get("public_event_id"), name="public_event_id", max_length=256)
    _text(event.get("canonical_title"), name="canonical_title", max_length=2000)
    status = _text(event.get("fusion_status"), name="fusion_status", max_length=64)
    if status not in EVENT_STATUSES:
        raise ValueError(f"unsupported PublicEvent fusion_status: {event_id}")
    for field in ("event_type", "district_id", "source_state", "event_status", "verification_status"):
        if event.get(field) is not None:
            _text(event[field], name=field, max_length=256)
    for field in ("named_event_id", "tracking_id"):
        if event.get(field) is not None:
            _text(event[field], name=field, max_length=256)
    for field in ("tracked", "changed", "lkg"):
        if field in event and not isinstance(event[field], bool):
            raise ValueError(f"{field} must be boolean: {event_id}")
    for field in ("event_date",):
        if event.get(field) is not None:
            try:
                date.fromisoformat(_text(event[field], name=field, max_length=32))
            except ValueError as error:
                raise ValueError(f"{field} must be an ISO date: {event_id}") from error
    for field in ("start_at", "end_at", "created_at", "updated_at"):
        if event.get(field) is not None:
            _instant(event[field], name=field)
    if event.get("start_at") and event.get("end_at") and _instant(event["end_at"], name="end_at") < _instant(event["start_at"], name="start_at"):
        raise ValueError(f"PublicEvent time range is reversed: {event_id}")
    for field in ("independent_source_ids", "agency_ids", "location_ids", "location_candidates"):
        if event.get(field) is not None:
            _string_list(event[field], name=field)
    if event.get("independent_source_count") is not None:
        count = event["independent_source_count"]
        if type(count) is not int or not 0 <= count <= 100:
            raise ValueError(f"independent_source_count is invalid: {event_id}")
    for field in ("changed_fields", "conflict_fields", "uncertain_fields"):
        if event.get(field) is not None:
            _string_list(event[field], name=field)
    links = event.get("linked_document_versions")
    if not isinstance(links, list) or not links or len(links) > 100:
        raise ValueError(f"PublicEvent document links are invalid: {event_id}")
    seen_versions: set[str] = set()
    for link in links:
        if not isinstance(link, dict):
            raise ValueError(f"PublicEvent document link is invalid: {event_id}")
        version_id = _text(link.get("document_version_id"), name="document_version_id", max_length=256)
        if version_id in seen_versions:
            raise ValueError(f"duplicate PublicEvent document version: {event_id}")
        seen_versions.add(version_id)
        _optional_text(link.get("document_id"), name="document_id", max_length=256)
        _optional_text(link.get("evidence_id"), name="evidence_id", max_length=256)
        _text(link.get("source_id"), name="source_id", max_length=64)
        _https(link.get("official_url"), name="official_url")
        if link.get("evidence_locator") is not None:
            _text(link["evidence_locator"], name="evidence_locator", max_length=4096)
    if event.get("version_history") is not None:
        versions = event["version_history"]
        if not isinstance(versions, list) or len(versions) > 100:
            raise ValueError(f"PublicEvent version_history is invalid: {event_id}")
        seen_history: set[str] = set()
        for version in versions:
            version_id = _validate_event_version(version, event_id)
            if version_id in seen_history:
                raise ValueError(f"duplicate event version: {event_id}")
            seen_history.add(version_id)
    if event.get("affected_handoff_claims") is not None:
        _string_list(event["affected_handoff_claims"], name="affected_handoff_claims")
    return event


def _event_features(events: list[dict[str, Any]]) -> dict[str, bool]:
    return {
        "region": any(event.get("district_id") or event.get("location_ids") or event.get("location_candidates") for event in events),
        "agency": any(event.get("agency_ids") or event.get("independent_source_ids") for event in events),
        "category": any(event.get("event_type") for event in events),
        "time_range": any(event.get("event_date") or event.get("start_at") or event.get("end_at") for event in events),
        "verification_status": True,
        "event_status": any(event.get("event_status") or event.get("source_state") for event in events),
        "tracked": any("tracked" in event or event.get("tracking_id") for event in events),
        "changed_only": any("changed" in event or event.get("changed_fields") or event.get("change_type") for event in events),
    }


def _event_material(store: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": store["schema_version"],
        "store_type": store["store_type"],
        "generated_at": store.get("generated_at"),
        "source_ids": store["source_ids"],
        "features": store["features"],
        "public_events": store["public_events"],
    }


def build_event_store(events: list[dict[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    if not isinstance(events, list) or len(events) > MAX_ROWS:
        raise ValueError("public_events must be a bounded array")
    rows = [validate_event(event) for event in events]
    rows.sort(key=lambda event: event["public_event_id"])
    if len({event["public_event_id"] for event in rows}) != len(rows):
        raise ValueError("duplicate PublicEvent IDs")
    if generated_at is not None:
        _instant(generated_at, name="generated_at")
    source_ids = sorted({
        link["source_id"]
        for event in rows
        for link in event["linked_document_versions"]
    })
    store = {
        "schema_version": EVENT_STORE_SCHEMA_VERSION,
        "store_type": "PUBLIC_EVENT_QUERY",
        "generated_at": generated_at,
        "source_ids": source_ids,
        "features": _event_features(rows),
        "public_events": rows,
    }
    store["store_sha256"] = digest(_event_material(store))
    store["generation_id"] = digest({"store_sha256": store["store_sha256"], "source_ids": source_ids})
    return store


def validate_event_store(store: Any) -> dict[str, Any]:
    if not isinstance(store, dict) or store.get("schema_version") != EVENT_STORE_SCHEMA_VERSION or store.get("store_type") != "PUBLIC_EVENT_QUERY":
        raise ValueError("unsupported PublicEvent query store")
    rows = store.get("public_events")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("public_events must be a bounded array")
    for event in rows:
        validate_event(event)
    expected = build_event_store(rows, generated_at=store.get("generated_at"))
    if store.get("source_ids") != expected["source_ids"] or store.get("features") != expected["features"]:
        raise ValueError("PublicEvent query store metadata mismatch")
    if store.get("store_sha256") != expected["store_sha256"] or store.get("generation_id") != expected["generation_id"]:
        raise ValueError("PublicEvent query store hash mismatch")
    if [event["public_event_id"] for event in rows] != sorted(event["public_event_id"] for event in rows):
        raise ValueError("PublicEvent query store ordering is not deterministic")
    return store


def load_event_store(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if isinstance(payload, dict) and payload.get("store_type") == "PUBLIC_EVENT_QUERY":
        return validate_event_store(payload)
    rows = payload.get("public_events") if isinstance(payload, dict) else None
    return build_event_store(rows, generated_at=payload.get("generated_at") if isinstance(payload, dict) else None)


def _statistic_material(store: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": store["schema_version"],
        "store_type": store["store_type"],
        "generated_at": store.get("generated_at"),
        "source_ids": store["source_ids"],
        "statistics": store["statistics"],
    }


def validate_statistic(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("statistic row must be an object")
    for field in ("statistic_id", "dataset_id", "source_id", "metric", "period", "unit", "geography"):
        _text(row.get(field), name=field, max_length=512)
    value = row.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("statistic value must be a finite number")
    if not isinstance(row.get("provisional"), bool):
        raise ValueError("statistic provisional must be boolean")
    _optional_text(row.get("agency"), name="agency", max_length=256)
    _instant(row.get("updated_at"), name="updated_at")
    _https(row.get("official_url"), name="official_url")
    if row.get("evidence_locator") is not None:
        _text(row["evidence_locator"], name="evidence_locator", max_length=4096)
    if row.get("comparison_period") is not None:
        _text(row["comparison_period"], name="comparison_period", max_length=128)
    _optional_text(row.get("official_note"), name="official_note", max_length=2000)
    return row


def build_statistics_store(rows: list[dict[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("statistics must be a bounded array")
    normalized = [validate_statistic(row) for row in rows]
    normalized.sort(key=lambda row: (row["period"], row["dataset_id"], row["statistic_id"]))
    if len({row["statistic_id"] for row in normalized}) != len(normalized):
        raise ValueError("duplicate statistic IDs")
    if generated_at is not None:
        _instant(generated_at, name="generated_at")
    store = {
        "schema_version": STATISTICS_STORE_SCHEMA_VERSION,
        "store_type": "TYPED_STATISTICS_QUERY",
        "generated_at": generated_at,
        "source_ids": sorted({row["source_id"] for row in normalized}),
        "statistics": normalized,
    }
    store["store_sha256"] = digest(_statistic_material(store))
    store["generation_id"] = digest({"store_sha256": store["store_sha256"], "source_ids": store["source_ids"]})
    return store


def validate_statistics_store(store: Any) -> dict[str, Any]:
    if not isinstance(store, dict) or store.get("schema_version") != STATISTICS_STORE_SCHEMA_VERSION or store.get("store_type") != "TYPED_STATISTICS_QUERY":
        raise ValueError("unsupported statistics query store")
    rows = store.get("statistics")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("statistics must be a bounded array")
    expected = build_statistics_store(rows, generated_at=store.get("generated_at"))
    if store.get("source_ids") != expected["source_ids"]:
        raise ValueError("statistics query store source metadata mismatch")
    if store.get("store_sha256") != expected["store_sha256"] or store.get("generation_id") != expected["generation_id"]:
        raise ValueError("statistics query store hash mismatch")
    if [row["statistic_id"] for row in rows] != [row["statistic_id"] for row in expected["statistics"]]:
        raise ValueError("statistics query store ordering is not deterministic")
    return store


def load_statistics_store(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    if isinstance(payload, dict) and payload.get("store_type") == "TYPED_STATISTICS_QUERY":
        return validate_statistics_store(payload)
    rows = payload.get("statistics") if isinstance(payload, dict) else None
    return build_statistics_store(rows, generated_at=payload.get("generated_at") if isinstance(payload, dict) else None)


def _filters_hash(filters: dict[str, Any]) -> str:
    return digest(filters)


def _page(rows: list[dict[str, Any]], *, generation_id: str, filters: dict[str, Any], limit: int, cursor: str | None, now: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be an integer between 1 and {MAX_LIMIT}")
    offset = 0
    filter_hash = _filters_hash(filters)
    if cursor is not None:
        try:
            token = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
            if token.get("generation") != generation_id or token.get("filters") != filter_hash:
                raise ValueError("cursor binding")
            offset = token["offset"]
            if type(offset) is not int or not 0 <= offset <= MAX_ROWS:
                raise ValueError("cursor offset")
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError("invalid cursor: generation/filter/offset mismatch") from error
    ordered = sorted(rows, key=now)
    if offset > len(ordered):
        raise ValueError("cursor offset exceeds result set")
    selected = ordered[offset:offset + limit]
    next_cursor = None
    if offset + len(selected) < len(ordered):
        next_cursor = base64.urlsafe_b64encode(canonical({
            "generation": generation_id,
            "filters": filter_hash,
            "offset": offset + len(selected),
        })).decode("ascii")
    return {
        "total_matches": len(ordered),
        "result_count": len(selected),
        "offset": offset,
        "truncated": len(ordered) > len(selected),
        "has_more": next_cursor is not None,
        "next_cursor": next_cursor,
        "results": selected,
    }


def _event_start(event: dict[str, Any]) -> datetime:
    if event.get("start_at"):
        return _instant(event["start_at"], name="start_at")
    if event.get("event_date"):
        return _time_bound(event["event_date"], name="event_date")
    if event.get("end_at"):
        return _instant(event["end_at"], name="end_at")
    return datetime.min.replace(tzinfo=timezone.utc)


def _event_end(event: dict[str, Any]) -> datetime:
    if event.get("end_at"):
        return _instant(event["end_at"], name="end_at")
    return _event_start(event)


def _event_matches(event: dict[str, Any], arguments: dict[str, Any]) -> bool:
    if arguments.get("public_event_id") and event["public_event_id"] != arguments["public_event_id"]:
        return False
    if arguments.get("q") and arguments["q"].casefold() not in f"{event['canonical_title']} {event.get('event_type') or ''}".casefold():
        return False
    locations = {event.get("district_id"), *(event.get("location_ids") or []), *(event.get("location_candidates") or [])}
    for location_filter in ("region", "district"):
        if arguments.get(location_filter) and arguments[location_filter] not in locations:
            return False
    if arguments.get("agency") and arguments["agency"] not in set(event.get("agency_ids") or []) | set(event.get("independent_source_ids") or []):
        return False
    if arguments.get("category") and arguments["category"] != event.get("event_type"):
        return False
    if arguments.get("verification_status") and arguments["verification_status"] != (event.get("verification_status") or event.get("fusion_status")):
        return False
    if arguments.get("event_status") and arguments["event_status"] != (event.get("event_status") or event.get("source_state")):
        return False
    if "tracked" in arguments and bool(arguments["tracked"]) != bool(event.get("tracked", event.get("tracking_id"))):
        return False
    if arguments.get("changed_only") and not bool(event.get("changed", event.get("changed_fields") or event.get("change_type"))):
        return False
    lower = _time_bound(arguments["time_from"], name="time_from") if arguments.get("time_from") else None
    upper = _time_bound(arguments["time_to"], name="time_to") if arguments.get("time_to") else None
    if lower and upper and upper < lower:
        raise ValueError("time_to must not precede time_from")
    has_time = bool(event.get("start_at") or event.get("end_at") or event.get("event_date"))
    if (lower or upper) and not has_time:
        return False
    if lower and _event_end(event) < lower:
        return False
    if upper and _event_start(event) > upper:
        return False
    return True


def _project_document(event_id: str, link: dict[str, Any]) -> dict[str, Any]:
    version_id = link["document_version_id"]
    locator = link.get("evidence_locator") or f"{link['official_url']}#document-version={quote(version_id, safe='')}"
    return {
        "document_version_id": version_id,
        "document_id": link.get("document_id"),
        "source_id": link["source_id"],
        "official_url": link["official_url"],
        "evidence_locator": locator,
        "evidence_id": link.get("evidence_id") or f"DOC-{digest([event_id, version_id])[:20].upper()}",
    }


def project_event(event: dict[str, Any]) -> dict[str, Any]:
    validate_event(event)
    allowed = (
        "public_event_id", "event_type", "named_event_id", "canonical_title", "event_date", "start_at", "end_at",
        "district_id", "location_candidates", "location_ids", "agency_ids", "independent_source_ids",
        "independent_source_count", "fusion_status", "verification_status", "event_status", "source_state", "lkg",
        "tracked", "tracking_id", "changed", "changed_fields", "conflict_fields", "uncertain_fields", "created_at", "updated_at",
    )
    result = {key: event.get(key) for key in allowed if key in event}
    result["documents"] = [_project_document(event["public_event_id"], link) for link in event["linked_document_versions"]]
    return result


def _project_version(version: dict[str, Any]) -> dict[str, Any]:
    return {
        key: version[key]
        for key in ("document_version_id", "version_id", "observed_at", "published_at", "effective_at", "fields", "changed_fields")
        if key in version
    }


def query_events(store: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    validate_event_store(store)
    filters = {key: value for key, value in arguments.items() if key not in {"limit", "cursor", "expected_generation"}}
    if arguments.get("expected_generation") is not None and arguments["expected_generation"] != store["generation_id"]:
        raise ValueError("event query generation mismatch")
    matches = [project_event(event) for event in store["public_events"] if _event_matches(event, arguments)]
    page = _page(
        matches,
        generation_id=store["generation_id"],
        filters=filters,
        limit=arguments.get("limit", 20),
        cursor=arguments.get("cursor"),
        now=lambda row: (-_event_start(row).timestamp(), row["public_event_id"]),
    )
    return {"query_generation_id": store["generation_id"], **page}


def get_event(store: dict[str, Any], event_id: str) -> dict[str, Any]:
    validate_event_store(store)
    for event in store["public_events"]:
        if event["public_event_id"] == event_id:
            projected = project_event(event)
            projected["tracking"] = {
                "tracking_id": event.get("tracking_id"),
                "tracked": bool(event.get("tracked", event.get("tracking_id"))),
                "changed": bool(event.get("changed", event.get("changed_fields") or event.get("change_type"))),
            }
            projected["version_count"] = len(event.get("version_history") or [])
            return projected
    raise KeyError(event_id)


def compare_event_versions(store: dict[str, Any], event_id: str, *, before_version: str | None = None, after_version: str | None = None) -> dict[str, Any]:
    validate_event_store(store)
    event = next((row for row in store["public_events"] if row["public_event_id"] == event_id), None)
    if event is None:
        raise KeyError(event_id)
    versions = list(event.get("version_history") or [])
    versions.sort(key=lambda version: (version.get("observed_at") or "", version.get("document_version_id") or version.get("version_id") or ""))
    by_id = {(version.get("document_version_id") or version.get("version_id")): version for version in versions}
    if before_version is not None and before_version not in by_id:
        raise KeyError(before_version)
    if after_version is not None and after_version not in by_id:
        raise KeyError(after_version)
    if before_version is not None and after_version is not None:
        before, after = by_id[before_version], by_id[after_version]
    elif before_version is not None:
        before = by_id[before_version]
        position = versions.index(before)
        after = versions[position + 1] if position + 1 < len(versions) else None
    elif after_version is not None:
        after = by_id[after_version]
        position = versions.index(after)
        before = versions[position - 1] if position > 0 else None
    elif len(versions) >= 2:
        before, after = versions[-2], versions[-1]
    else:
        before = after = None
    if before is None or after is None or before is after:
        return {
            "comparison_status": "NO_COMPARABLE_VERSION_HISTORY",
            "public_event_id": event_id,
            "before": None,
            "after": None,
            "changed_fields": [],
            "materiality": "UNKNOWN",
            "source_document_versions": [link["document_version_id"] for link in event["linked_document_versions"]],
            "affected_handoff_claims": [],
        }
    before_fields = before.get("fields", {})
    after_fields = after.get("fields", {})
    changed = sorted({
        *before.get("changed_fields", []),
        *after.get("changed_fields", []),
        *(key for key in set(before_fields) | set(after_fields) if before_fields.get(key) != after_fields.get(key)),
    })
    return {
        "comparison_status": "COMPARED",
        "public_event_id": event_id,
        "before": _project_version(before),
        "after": _project_version(after),
        "changed_fields": changed,
        "materiality": "MATERIAL" if changed else "NO_CHANGE",
        "source_document_versions": [
            version.get("document_version_id") or version.get("version_id")
            for version in (before, after)
        ],
        "observed_at": {"before": before.get("observed_at"), "after": after.get("observed_at")},
        "published_at": {"before": before.get("published_at"), "after": after.get("published_at")},
        "effective_at": {"before": before.get("effective_at"), "after": after.get("effective_at")},
        "affected_handoff_claims": list(event.get("affected_handoff_claims", [])),
    }


def query_statistics(store: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    validate_statistics_store(store)
    filters = {key: value for key, value in arguments.items() if key not in {"limit", "cursor", "expected_generation"}}
    if arguments.get("expected_generation") is not None and arguments["expected_generation"] != store["generation_id"]:
        raise ValueError("statistics query generation mismatch")
    rows = []
    for row in store["statistics"]:
        if arguments.get("dataset_id") and row["dataset_id"] != arguments["dataset_id"]:
            continue
        if arguments.get("metric") and row["metric"] != arguments["metric"]:
            continue
        if arguments.get("geography") and row["geography"] != arguments["geography"]:
            continue
        if arguments.get("agency") and row.get("agency") != arguments["agency"]:
            continue
        if arguments.get("provisional") is not None and row["provisional"] != arguments["provisional"]:
            continue
        if arguments.get("period_from") and row["period"] < arguments["period_from"]:
            continue
        if arguments.get("period_to") and row["period"] > arguments["period_to"]:
            continue
        rows.append({key: row[key] for key in (
            "statistic_id", "dataset_id", "source_id", "metric", "period", "value", "unit", "geography",
            "agency", "provisional", "updated_at", "official_url", "evidence_locator", "comparison_period", "official_note",
        ) if key in row})
    page = _page(
        rows,
        generation_id=store["generation_id"],
        filters=filters,
        limit=arguments.get("limit", 20),
        cursor=arguments.get("cursor"),
        now=lambda row: (row["period"], row["dataset_id"], row["statistic_id"]),
    )
    return {"query_generation_id": store["generation_id"], **page}
