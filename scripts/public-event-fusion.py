#!/usr/bin/env python3
"""Conservative deterministic public-event fusion for GovIntel.

Inputs are already-normalized public documents.  This module intentionally does not
perform fuzzy/embedding promotion.  It only auto-groups documents with a stable
named_event_id (or explicit cross-reference) and compatible date semantics.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any


def _https(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("https://"):
        raise ValueError("official/source URLs must use HTTPS")
    return value


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid ISO timestamp: {value}") from exc


def _stable_id(prefix: str, payload: str) -> str:
    return f"{prefix}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def validate_document(document: dict[str, Any]) -> None:
    required = ("document_id", "document_version_id", "source_id", "title", "event_type")
    for field in required:
        if not isinstance(document.get(field), str) or not document[field].strip():
            raise ValueError(f"document missing {field}")
    if document.get("authority") not in (None, "official"):
        raise ValueError("only official documents can enter canonical PublicEvent fusion")
    _https(document.get("official_url"))
    for field in ("agency_ids", "location_ids"):
        value = document.get(field, [])
        if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
            raise ValueError(f"{field} must be a string array")


def cluster_key(document: dict[str, Any]) -> tuple[str, str, str] | None:
    named_event_id = document.get("named_event_id") or document.get("cross_reference_id")
    day = _iso_date(document.get("event_start_at"))
    if not isinstance(named_event_id, str) or not named_event_id or day is None:
        return None
    return document["event_type"], named_event_id, day


def _field_values(documents: list[dict[str, Any]], field: str) -> set[str]:
    return {value for value in (doc.get(field) for doc in documents) if isinstance(value, str) and value}


def _location_conflict(documents: list[dict[str, Any]]) -> bool:
    districts = _field_values(documents, "district_id")
    return len(districts) > 1


def _time_conflicts(documents: list[dict[str, Any]]) -> list[str]:
    conflicts = []
    for field in ("event_start_at", "event_end_at"):
        values = _field_values(documents, field)
        if len(values) > 1:
            conflicts.append(field)
    return conflicts


def build_public_event(key: tuple[str, str, str] | None, documents: list[dict[str, Any]]) -> dict[str, Any]:
    if not documents:
        raise ValueError("cannot build PublicEvent without documents")
    for document in documents:
        validate_document(document)

    ordered = sorted(documents, key=lambda doc: (doc["source_id"], doc["document_id"], doc["document_version_id"]))
    if key is None:
        id_material = "candidate|" + ordered[0]["document_id"]
    else:
        id_material = "|".join(key)
    public_event_id = _stable_id("PE", id_material)

    independent_sources = sorted({doc.get("independent_source_id") or doc["source_id"] for doc in ordered})
    time_conflicts = _time_conflicts(ordered)
    location_conflict = _location_conflict(ordered)
    conflict_fields = [*time_conflicts, *( ["district_id"] if location_conflict else [])]

    if conflict_fields:
        fusion_status = "CONFLICT"
    elif key is not None and len(independent_sources) >= 2:
        fusion_status = "CONFIRMED"
    else:
        fusion_status = "CANDIDATE"

    named_event_id = key[1] if key else ordered[0].get("named_event_id")
    canonical_title = min((doc["title"] for doc in ordered), key=lambda value: (len(value), value))
    agencies = sorted({agency for doc in ordered for agency in doc.get("agency_ids", [])})
    locations = sorted({location for doc in ordered for location in doc.get("location_ids", [])})

    return {
        "schema_version": 1,
        "public_event_id": public_event_id,
        "event_type": ordered[0]["event_type"],
        "named_event_id": named_event_id,
        "canonical_title": canonical_title,
        "event_date": key[2] if key else _iso_date(ordered[0].get("event_start_at")),
        "start_at": next(iter(_field_values(ordered, "event_start_at")), None) if not time_conflicts else None,
        "end_at": next(iter(_field_values(ordered, "event_end_at")), None) if not time_conflicts else None,
        "district_id": next(iter(_field_values(ordered, "district_id")), None) if not location_conflict else None,
        "agency_ids": agencies,
        "location_ids": locations,
        "independent_source_ids": independent_sources,
        "independent_source_count": len(independent_sources),
        "linked_document_versions": [
            {
                "document_id": doc["document_id"],
                "document_version_id": doc["document_version_id"],
                "source_id": doc["source_id"],
                "independent_source_id": doc.get("independent_source_id") or doc["source_id"],
                "official_url": doc.get("official_url"),
            }
            for doc in ordered
        ],
        "link_reasons": ["stable_named_event_id", "event_date"] if key else ["insufficient_identity_for_auto_merge"],
        "fusion_status": fusion_status,
        "conflict_fields": sorted(conflict_fields),
        "background": [],
    }


def fuse_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def attach_background(event: dict[str, Any], record: dict[str, Any]) -> dict[str, Any]:
    if record.get("district_id") != event.get("district_id"):
        raise ValueError("background geography must exactly match the confirmed event district")
    for field in ("dataset_id", "period", "value", "unit"):
        if record.get(field) in (None, ""):
            raise ValueError(f"background record missing {field}")
    source_url = _https(record.get("source_url"))
    enriched = json.loads(json.dumps(event))
    enriched.setdefault("background", []).append(
        {
            "role": "BACKGROUND_ONLY",
            "dataset_id": record["dataset_id"],
            "period": record["period"],
            "value": record["value"],
            "unit": record["unit"],
            "district_id": record["district_id"],
            "source_url": source_url,
        }
    )
    return enriched


def self_check() -> None:
    docs = [
        {
            "document_id": f"doc-{source}",
            "document_version_id": f"doc-{source}:v1",
            "source_id": source,
            "independent_source_id": source,
            "authority": "official",
            "title": title,
            "event_type": "large_event",
            "named_event_id": "event:demo-2026",
            "event_start_at": "2026-09-20T18:00:00+08:00",
            "event_end_at": "2026-09-20T22:00:00+08:00",
            "district_id": "location:tc-xitun",
            "agency_ids": [agency],
            "location_ids": ["location:tc-xitun"],
            "official_url": f"https://example.gov/{source}",
        }
        for source, agency, title in (
            ("S-CITY", "agency:tc-news", "大型活動公告"),
            ("S-POLICE", "agency:tc-police", "大型活動交通疏導"),
            ("S-TRAFFIC", "agency:tc-traffic", "大型活動交通資訊"),
        )
    ]
    events = fuse_documents(docs)
    assert len(events) == 1
    event = events[0]
    assert event["fusion_status"] == "CONFIRMED"
    assert event["independent_source_count"] == 3
    enriched = attach_background(
        event,
        {
            "dataset_id": "CTX-POP",
            "period": "2026-08",
            "value": 230000,
            "unit": "persons",
            "district_id": "location:tc-xitun",
            "source_url": "https://data.gov.tw/dataset/example",
        },
    )
    assert enriched["background"][0]["role"] == "BACKGROUND_ONLY"
    print(f"PUBLIC_EVENT_FUSION_SELF_CHECK_OK event={event['public_event_id']} sources=3")


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
