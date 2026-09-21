#!/usr/bin/env python3
"""Conservative deterministic PublicEvent fusion for normalized official documents.

This module never promotes fuzzy similarity or media-only observations.  It keeps
document identities and versions as evidence, while the public-event identity is
an independent, reviewable projection.
"""

from __future__ import annotations

import argparse
import copy
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Taipei")
FUSION_STATUSES = {"CONFIRMED", "CANDIDATE", "CONFLICT", "SPLIT_REQUIRED"}


def _https(value: Any) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ValueError("official/source URLs must use nonempty HTTPS URLs")
    return value


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid ISO timestamp: {value}") from error
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(TZ)


def _iso_date(value: Any) -> str | None:
    parsed = _parse_timestamp(value)
    return parsed.date().isoformat() if parsed else None


def _time_key(value: Any) -> str | None:
    parsed = _parse_timestamp(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def _stable_id(prefix: str, payload: str) -> str:
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def _stamp(value: Any) -> str | None:
    parsed = _parse_timestamp(value)
    return parsed.isoformat(timespec="seconds") if parsed else None


def _manual_record(action: str, operator: str, decided_at: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if action not in {"CONFIRM", "MERGE", "SPLIT"}:
        raise ValueError(f"unsupported manual action: {action}")
    if not isinstance(operator, str) or not operator.strip():
        raise ValueError("operator is required")
    stamp = _stamp(decided_at)
    if stamp is None:
        raise ValueError("decided_at must include a timezone")
    return {"action": action, "operator": operator.strip(), "at": stamp, "payload": copy.deepcopy(payload)}


def validate_document(document: dict[str, Any]) -> None:
    required = ("document_id", "document_version_id", "source_id", "title", "event_type")
    if not isinstance(document, dict):
        raise ValueError("document must be an object")
    for field in required:
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise ValueError(f"document missing {field}")
    if document.get("authority") != "official":
        raise ValueError("only affirmatively official documents can enter canonical PublicEvent fusion")
    _https(document.get("official_url"))
    for field in ("agency_ids", "location_ids"):
        value = document.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"{field} must be a string array")
    for field in ("event_start_at", "event_end_at", "observed_at", "created_at", "updated_at"):
        if field in document and document[field] is not None:
            _parse_timestamp(document[field])


def cluster_key(document: dict[str, Any]) -> tuple[str, str, str] | None:
    named_event_id = document.get("named_event_id") or document.get("cross_reference_id")
    day = _iso_date(document.get("event_start_at"))
    if not isinstance(named_event_id, str) or not named_event_id or day is None:
        return None
    return document["event_type"], named_event_id, day


def _field_values(documents: list[dict[str, Any]], field: str) -> set[str]:
    return {value for value in (doc.get(field) for doc in documents) if isinstance(value, str) and value}


def _scopes(documents: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for document in documents:
        groups[str(document.get("fact_scope") or "event")].append(document)
    return groups


def _time_conflicts(documents: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    conflicts: list[str] = []
    uncertain: list[str] = []
    for field in ("event_start_at", "event_end_at"):
        for scope, scoped_documents in _scopes(documents).items():
            values = [doc.get(field) for doc in scoped_documents if doc.get(field) not in (None, "")]
            keys = {_time_key(value) for value in values}
            if any(key is None for key in keys) or len(values) != len(scoped_documents):
                uncertain.append(field if scope == "event" else f"{scope}.{field}")
            elif len(keys) > 1:
                conflicts.append(field if scope == "event" else f"{scope}.{field}")
    return conflicts, uncertain


def _location_state(documents: list[dict[str, Any]]) -> tuple[bool, bool]:
    conflict = False
    missing = False
    all_districts = _field_values(documents, "district_id")
    for scoped_documents in _scopes(documents).values():
        districts = _field_values(scoped_documents, "district_id")
        conflict = conflict or len(districts) > 1
        missing = missing or any(not doc.get("district_id") for doc in scoped_documents)
    return conflict, missing or len(all_districts) > 1


def _consistent_raw_value(documents: list[dict[str, Any]], field: str) -> str | None:
    values = [doc.get(field) for doc in documents if doc.get(field) not in (None, "")]
    if not values:
        return None
    if field in {"event_start_at", "event_end_at"}:
        if len({_time_key(value) for value in values}) > 1:
            return None
    return sorted(values)[0]


def _metadata_time(documents: list[dict[str, Any]], fields: tuple[str, ...]) -> str | None:
    values = [_stamp(doc.get(field)) for doc in documents for field in fields if doc.get(field)]
    values = [value for value in values if value]
    return min(values) if values else None


def _linked_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(documents, key=lambda doc: (doc["source_id"], doc["document_id"], doc["document_version_id"]))
    return [
        {
            "document_id": doc["document_id"],
            "document_version_id": doc["document_version_id"],
            "source_id": doc["source_id"],
            "independent_source_id": doc.get("independent_source_id") or doc["source_id"],
            "official_url": _https(doc["official_url"]),
            "evidence_locator": doc.get("evidence_locator"),
        }
        for doc in ordered
    ]


def build_public_event(key: tuple[str, str, str] | None, documents: list[dict[str, Any]]) -> dict[str, Any]:
    if not documents:
        raise ValueError("cannot build PublicEvent without documents")
    for document in documents:
        validate_document(document)

    ordered = sorted(documents, key=lambda doc: (doc["source_id"], doc["document_id"], doc["document_version_id"]))
    id_material = "candidate|" + ordered[0]["document_id"] if key is None else "|".join(key)
    public_event_id = _stable_id("PE", id_material)
    independent_sources = sorted({doc.get("independent_source_id") or doc["source_id"] for doc in ordered})
    time_conflicts, time_uncertain = _time_conflicts(ordered)
    location_conflict, location_uncertain = _location_state(ordered)
    status_conflict = any(len(_field_values(scoped, "status")) > 1 for scoped in _scopes(ordered).values())
    conflict_fields = [*time_conflicts, *( ["district_id"] if location_conflict else []), *( ["status"] if status_conflict else [])]
    uncertain_fields = [*time_uncertain, *( ["district_id"] if location_uncertain else [])]

    if conflict_fields:
        fusion_status = "CONFLICT"
    elif key is not None and len(independent_sources) >= 2 and not uncertain_fields:
        fusion_status = "CONFIRMED"
    else:
        fusion_status = "CANDIDATE"

    named_event_id = key[1] if key else ordered[0].get("named_event_id")
    canonical_title = min((doc["title"] for doc in ordered), key=lambda value: (len(value), value))
    agencies = sorted({agency for doc in ordered for agency in doc.get("agency_ids", [])})
    locations = sorted({location for doc in ordered for location in doc.get("location_ids", [])})
    all_districts = _field_values(ordered, "district_id")
    district = next(iter(all_districts), None) if len(all_districts) == 1 and not location_conflict else None
    return {
        "schema_version": 1,
        "public_event_id": public_event_id,
        "event_type": ordered[0]["event_type"],
        "named_event_id": named_event_id,
        "canonical_title": canonical_title,
        "event_date": key[2] if key else _iso_date(ordered[0].get("event_start_at")),
        "start_at": _consistent_raw_value(ordered, "event_start_at") if not time_conflicts else None,
        "end_at": _consistent_raw_value(ordered, "event_end_at") if not time_conflicts else None,
        "district_id": district,
        "location_candidates": locations,
        "location_ids": locations,
        "agency_ids": agencies,
        "independent_source_ids": independent_sources,
        "independent_source_count": len(independent_sources),
        "linked_document_versions": _linked_documents(ordered),
        "link_reasons": ["stable_named_event_id", "event_date"] if key else ["insufficient_identity_for_auto_merge"],
        "fusion_status": fusion_status,
        "conflict_fields": sorted(conflict_fields),
        "uncertain_fields": sorted(uncertain_fields),
        "created_at": _metadata_time(ordered, ("created_at", "observed_at")),
        "updated_at": _metadata_time(ordered, ("updated_at", "observed_at")),
        "manual_history": [],
        "background": [],
        "source_state": "CURRENT",
        "lkg": False,
    }


def fuse_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(documents, list):
        raise ValueError("documents must be an array")
    keyed: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    candidates: list[dict[str, Any]] = []
    for document in documents:
        validate_document(document)
        key = cluster_key(document)
        if key is None:
            candidates.append(build_public_event(None, [document]))
        else:
            keyed[key].append(document)
    events = [build_public_event(key, docs) for key, docs in keyed.items()]
    events.extend(candidates)
    return sorted(events, key=lambda event: event["public_event_id"])


def reconcile_public_events(
    previous_events: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    *,
    snapshot_complete: bool,
) -> list[dict[str, Any]]:
    """Keep prior events when a partial source cannot prove removal.

    ponytail: carry-forward is a bounded linear merge of event IDs; replace with
    persisted source-level reconciliation if event volume or source granularity grows.
    """
    if not isinstance(previous_events, list):
        raise ValueError("previous_events must be an array")
    current = fuse_documents(documents)
    if snapshot_complete:
        return current

    by_id = {event["public_event_id"]: event for event in current}
    for previous in previous_events:
        event_id = previous.get("public_event_id") if isinstance(previous, dict) else None
        if not event_id:
            continue
        event = by_id.get(event_id)
        if event is None:
            carry = copy.deepcopy(previous)
            carry["source_state"] = "PARTIAL_LKG"
            carry["lkg"] = True
            by_id[event_id] = carry
            continue
        old_links = {json.dumps(link, ensure_ascii=False, sort_keys=True): link for link in previous.get("linked_document_versions", [])}
        for link in event.get("linked_document_versions", []):
            old_links[json.dumps(link, ensure_ascii=False, sort_keys=True)] = link
        event["linked_document_versions"] = sorted(
            old_links.values(), key=lambda link: (link.get("source_id", ""), link.get("document_version_id", ""))
        )
        event["source_state"] = "PARTIAL_LKG"
        event["lkg"] = True
        event["previous_fusion_status"] = previous.get("fusion_status")
        if previous.get("fusion_status") == "CONFIRMED" and event.get("fusion_status") != "CONFLICT":
            event["fusion_status"] = "CONFIRMED"
    return sorted(by_id.values(), key=lambda event: event["public_event_id"])


def confirm_candidate(event: dict[str, Any], *, operator: str, decided_at: Any) -> dict[str, Any]:
    if event.get("fusion_status") != "CANDIDATE":
        raise ValueError("only CANDIDATE events can be manually confirmed")
    result = copy.deepcopy(event)
    result["fusion_status"] = "CONFIRMED"
    result.setdefault("manual_history", []).append(
        _manual_record("CONFIRM", operator, decided_at, {"public_event_id": event.get("public_event_id")})
    )
    result.setdefault("link_reasons", []).append("manual_confirmed")
    return result


def merge_public_events(events: list[dict[str, Any]], *, operator: str, decided_at: Any) -> dict[str, Any]:
    if not isinstance(events, list) or len(events) < 2:
        raise ValueError("manual merge requires at least two events")
    ids = [event.get("public_event_id") for event in events]
    if any(not isinstance(event, dict) or not event.get("public_event_id") for event in events) or len(ids) != len(set(ids)):
        raise ValueError("manual merge requires unique PublicEvent IDs")
    ordered = sorted((copy.deepcopy(event) for event in events), key=lambda event: event["public_event_id"])
    target = ordered[0]
    links = {
        json.dumps(link, ensure_ascii=False, sort_keys=True): link
        for event in ordered
        for link in event.get("linked_document_versions", [])
    }
    target["linked_document_versions"] = sorted(
        links.values(), key=lambda link: (link.get("source_id", ""), link.get("document_version_id", ""))
    )
    target["agency_ids"] = sorted({agency for event in ordered for agency in event.get("agency_ids", [])})
    target["location_candidates"] = sorted({location for event in ordered for location in event.get("location_candidates", [])})
    target["location_ids"] = target["location_candidates"]
    target["independent_source_ids"] = sorted({source for event in ordered for source in event.get("independent_source_ids", [])})
    target["independent_source_count"] = len(target["independent_source_ids"])
    target["fusion_status"] = "CONFLICT" if any(event.get("fusion_status") == "CONFLICT" for event in ordered) else "CANDIDATE"
    target["merged_from_public_event_ids"] = [event["public_event_id"] for event in ordered]
    target.setdefault("manual_history", []).append(
        _manual_record("MERGE", operator, decided_at, {"merged_event_ids": target["merged_from_public_event_ids"]})
    )
    return {
        "public_event": target,
        "retired_public_event_ids": target["merged_from_public_event_ids"][1:],
        "retired_events": ordered[1:],
    }


def split_public_event(
    event: dict[str, Any], groups: list[list[str]], *, operator: str, decided_at: Any
) -> dict[str, Any]:
    links = event.get("linked_document_versions")
    if not isinstance(links, list) or len(groups) < 2:
        raise ValueError("manual split requires linked documents and two groups")
    all_ids = [link.get("document_version_id") for link in links]
    requested = [document_version_id for group in groups for document_version_id in group]
    if sorted(requested) != sorted(all_ids) or len(requested) != len(set(requested)):
        raise ValueError("manual split groups must partition all linked document versions")
    record = _manual_record("SPLIT", operator, decided_at, {"public_event_id": event.get("public_event_id"), "groups": groups})
    original = copy.deepcopy(event)
    original["fusion_status"] = "SPLIT_REQUIRED"
    original.setdefault("manual_history", []).append(record)
    children = []
    link_by_id = {link["document_version_id"]: link for link in links}
    child_ids = []
    for index, group in enumerate(groups, start=1):
        child = copy.deepcopy(event)
        child["public_event_id"] = _stable_id("PE", f"split|{event['public_event_id']}|{index}|{'|'.join(sorted(group))}")
        child["linked_document_versions"] = [link_by_id[item] for item in group]
        child["independent_source_ids"] = sorted({link.get("independent_source_id") or link.get("source_id") for link in child["linked_document_versions"]})
        child["independent_source_count"] = len(child["independent_source_ids"])
        child["fusion_status"] = "CANDIDATE"
        child["manual_history"] = [record]
        child["split_from_public_event_id"] = event["public_event_id"]
        children.append(child)
        child_ids.append(child["public_event_id"])
    original["split_into_public_event_ids"] = child_ids
    return {"original": original, "events": children}


def attach_background(event: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    if event.get("fusion_status") != "CONFIRMED":
        raise ValueError("background enrichment requires a CONFIRMED public event")
    event_district = event.get("district_id")
    if not isinstance(event_district, str) or not event_district:
        raise ValueError("background enrichment requires a confirmed nonempty event district")
    if record.get("district_id") != event_district:
        raise ValueError("background geography must exactly match the confirmed event district")
    for field in ("dataset_id", "period", "value", "unit", "source_url"):
        if record.get(field) in (None, ""):
            raise ValueError(f"background record missing {field}")
    source_url = _https(record["source_url"])
    enriched = copy.deepcopy(event)
    enriched.setdefault("background", []).append(
        {
            "role": "BACKGROUND_ONLY",
            "dataset_id": record["dataset_id"],
            "period": record["period"],
            "value": record["value"],
            "unit": record["unit"],
            "district_id": record["district_id"],
            "source_url": source_url,
            "acquisition_path": record.get("acquisition_path", "DIRECT_OFFICIAL"),
        }
    )
    return enriched


def _demo_document(source: str, agency: str, title: str, *, version: str = "v1", day: str = "2026-09-20", start: str = "18:00") -> dict[str, Any]:
    return {
        "document_id": f"doc-{source}",
        "document_version_id": f"doc-{source}:{version}",
        "source_id": source,
        "independent_source_id": source,
        "authority": "official",
        "title": title,
        "event_type": "large_event",
        "named_event_id": "event:demo-2026",
        "event_start_at": f"{day}T{start}:00+08:00",
        "event_end_at": f"{day}T22:00:00+08:00",
        "district_id": "location:tc-xitun",
        "agency_ids": [agency],
        "location_ids": ["location:tc-xitun"],
        "official_url": f"https://example.gov/{source}",
        "observed_at": "2026-09-21T09:00:00+08:00",
    }


def self_check() -> None:
    documents = [
        _demo_document("S-CITY", "agency:tc-news", "大型活動公告"),
        _demo_document("S-POLICE", "agency:tc-police", "大型活動交通疏導"),
        _demo_document("S-TRAFFIC", "agency:tc-traffic", "大型活動交通資訊"),
    ]
    events = fuse_documents(documents)
    assert len(events) == 1 and events[0]["fusion_status"] == "CONFIRMED"
    event = attach_background(
        events[0],
        {
            "dataset_id": "CTX-POP",
            "period": "2026-08",
            "value": 230000,
            "unit": "persons",
            "district_id": "location:tc-xitun",
            "source_url": "https://data.gov.tw/dataset/example",
        },
    )
    revised = fuse_documents([*documents[:2], _demo_document("S-TRAFFIC", "agency:tc-traffic", "大型活動交通資訊", version="v2", start="16:00")])[0]
    assert revised["public_event_id"] == event["public_event_id"]
    assert revised["fusion_status"] == "CONFLICT"
    candidate = confirm_candidate(fuse_documents([documents[0]])[0], operator="demo", decided_at="2026-09-21T10:00:00+08:00")
    assert candidate["fusion_status"] == "CONFIRMED"
    assert event["background"][0]["role"] == "BACKGROUND_ONLY"
    print(f"PUBLIC_EVENT_FUSION_SELF_CHECK_OK event={event['public_event_id']} sources=3 revision=conflict")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        return 0
    if not args.input:
        raise SystemExit("--input is required unless --self-check is used")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    documents = payload.get("documents") if isinstance(payload, dict) else None
    if not isinstance(documents, list):
        raise SystemExit("input must be an object with a documents array")
    output = {"schema_version": 1, "public_events": fuse_documents(documents)}
    text = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
