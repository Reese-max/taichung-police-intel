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
import importlib.util
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Taipei")
FUSION_STATUSES = {"CONFIRMED", "CANDIDATE", "CONFLICT", "SPLIT_REQUIRED"}
OCCURRENCE_RELATION_TYPES = {"RESCHEDULES"}
OCCURRENCE_REVIEW_STATUSES = {"PENDING_REVIEW", "CONFIRMED", "REJECTED"}
ENTITY_REGISTRY_PATH = Path(__file__).with_name("entity-registry.py")
_entity_registry_module = None


def _load_entity_registry_module():
    global _entity_registry_module
    if _entity_registry_module is None:
        spec = importlib.util.spec_from_file_location("govintel_entity_registry", ENTITY_REGISTRY_PATH)
        if spec is None or spec.loader is None:
            raise RuntimeError("entity registry module is unavailable")
        _entity_registry_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_entity_registry_module)
    return _entity_registry_module


def load_entity_registry(path: Path | None = None) -> dict[str, Any]:
    return _load_entity_registry_module().load_registry(path or _load_entity_registry_module().DEFAULT_REGISTRY)


def bind_document_entities(document: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    module = _load_entity_registry_module()
    module.validate_registry(registry)
    result = copy.deepcopy(document)
    for kind, label_field, id_field in (
        ("agency", "agency_labels", "agency_ids"),
        ("location", "location_labels", "location_ids"),
    ):
        labels = result.pop(label_field, None)
        existing = result.get(id_field, [])
        if not isinstance(existing, list) or any(not isinstance(item, str) or not item for item in existing):
            raise ValueError(f"{id_field} must be a string array")
        known_ids = {
            entity["entity_id"] for entity in registry["entities"] if entity["kind"] == kind
        }
        if any(item not in known_ids for item in existing):
            raise ValueError(f"unknown {kind} entity ID")
        if labels is None:
            continue
        if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels):
            raise ValueError(f"{label_field} must be a string array")
        jurisdiction = result.get(f"{kind}_jurisdiction") or result.get("jurisdiction")
        if not isinstance(jurisdiction, str) or not jurisdiction.strip():
            raise ValueError(f"{label_field} requires an explicit jurisdiction")
        resolved = []
        for label in labels:
            match = module.resolve(registry, kind, label, jurisdiction)
            if match.get("status") != "RESOLVED":
                raise ValueError(f"unresolved {kind} label: {label}")
            resolved.append(match["entity_id"])
        result[id_field] = sorted(set(existing + resolved))
    result["entity_registry"] = module.registry_receipt(registry)
    return result


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


def _valid_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def _validate_occurrence_relations(document: dict[str, Any]) -> None:
    relations = document.get("occurrence_relations", [])
    if not isinstance(relations, list):
        raise ValueError("occurrence_relations must be an array")
    for relation in relations:
        if not isinstance(relation, dict):
            raise ValueError("occurrence relation must be an object")
        if relation.get("relation") not in OCCURRENCE_RELATION_TYPES:
            raise ValueError("unsupported occurrence relation")
        for field in ("target_document_id", "target_document_version_id", "evidence_locator", "reason"):
            if not isinstance(relation.get(field), str) or not relation[field].strip():
                raise ValueError(f"occurrence relation missing {field}")
        if relation["target_document_version_id"] == document["document_version_id"]:
            raise ValueError("occurrence relation cannot target itself")
        review = relation.get("review_decision")
        if not isinstance(review, dict) or review.get("status") not in OCCURRENCE_REVIEW_STATUSES:
            raise ValueError("occurrence relation review_decision is invalid")
        if review["status"] != "CONFIRMED":
            continue
        if review.get("reviewer_type") != "HUMAN":
            raise ValueError("confirmed occurrence relation requires HUMAN review")
        if not isinstance(review.get("operator"), str) or not review["operator"].strip():
            raise ValueError("confirmed occurrence relation requires operator")
        if _stamp(review.get("decided_at")) is None:
            raise ValueError("confirmed occurrence relation requires decided_at")
        if not isinstance(review.get("registry_version"), int) or isinstance(review["registry_version"], bool) or review["registry_version"] < 1:
            raise ValueError("confirmed occurrence relation requires registry_version")
        if not _valid_sha256(review.get("registry_hash")):
            raise ValueError("confirmed occurrence relation requires registry_hash")


def _relation_projection(document: dict[str, Any], relation: dict[str, Any]) -> dict[str, Any]:
    return {
        "relation": relation["relation"],
        "source_document_id": document["document_id"],
        "source_document_version_id": document["document_version_id"],
        "target_document_id": relation["target_document_id"],
        "target_document_version_id": relation["target_document_version_id"],
        "official_url": _https(document["official_url"]),
        "evidence_locator": relation["evidence_locator"],
        "reason": relation["reason"],
        "review_decision": copy.deepcopy(relation["review_decision"]),
    }


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
    if "occurrence_id" in document and (not isinstance(document["occurrence_id"], str) or not document["occurrence_id"].strip()):
        raise ValueError("occurrence_id must be a nonempty string")
    _validate_occurrence_relations(document)


def cluster_key(document: dict[str, Any]) -> tuple[str, str, str] | None:
    named_event_id = document.get("named_event_id") or document.get("cross_reference_id")
    occurrence_id = document.get("occurrence_id")
    if occurrence_id is not None:
        if not isinstance(named_event_id, str) or not named_event_id:
            return None
        return document["event_type"], named_event_id, f"occurrence:{occurrence_id}"
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


def build_public_event(
    key: tuple[str, str, str] | None,
    documents: list[dict[str, Any]],
    *,
    entity_registry_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    occurrence_relations = [
        _relation_projection(document, relation)
        for document in ordered
        for relation in document.get("occurrence_relations", [])
    ]
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
        "event_date": _iso_date(ordered[0].get("event_start_at")),
        "start_at": _consistent_raw_value(ordered, "event_start_at") if not time_conflicts else None,
        "end_at": _consistent_raw_value(ordered, "event_end_at") if not time_conflicts else None,
        "district_id": district,
        "location_candidates": locations,
        "location_ids": locations,
        "agency_ids": agencies,
        "independent_source_ids": independent_sources,
        "independent_source_count": len(independent_sources),
        "linked_document_versions": _linked_documents(ordered),
        "occurrence_relations": occurrence_relations,
        "link_reasons": (
            ["stable_occurrence_id"] if key and key[2].startswith("occurrence:")
            else ["stable_named_event_id", "event_date"] if key
            else ["insufficient_identity_for_auto_merge"]
        ),
        "fusion_status": fusion_status,
        "conflict_fields": sorted(conflict_fields),
        "uncertain_fields": sorted(uncertain_fields),
        "created_at": _metadata_time(ordered, ("created_at", "observed_at")),
        "updated_at": _metadata_time(ordered, ("updated_at", "observed_at")),
        "manual_history": [],
        "background": [],
        "source_state": "CURRENT",
        "lkg": False,
        **({"entity_registry": copy.deepcopy(entity_registry_receipt)} if entity_registry_receipt is not None else {}),
    }


def fuse_documents(
    documents: list[dict[str, Any]],
    *,
    entity_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(documents, list):
        raise ValueError("documents must be an array")
    registry_receipt = None
    if entity_registry is not None:
        registry_receipt = _load_entity_registry_module().registry_receipt(entity_registry)
        documents = [bind_document_entities(document, entity_registry) for document in documents]
    elif any(isinstance(document, dict) and "entity_registry" in document for document in documents):
        raise ValueError("entity_registry requires explicit registry binding")
    keyed: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    candidates: list[dict[str, Any]] = []
    for document in documents:
        validate_document(document)
        key = cluster_key(document)
        if key is None:
            candidates.append(build_public_event(None, [document], entity_registry_receipt=registry_receipt))
        else:
            keyed[key].append(document)
    events = [build_public_event(key, docs, entity_registry_receipt=registry_receipt) for key, docs in keyed.items()]
    events.extend(candidates)
    return sorted(events, key=lambda event: event["public_event_id"])


def _unique_dicts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {
        json.dumps(item, ensure_ascii=False, sort_keys=True): item
        for item in items
    }
    return list(unique.values())


def _overlay_event_evidence(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for field in ("linked_document_versions", "occurrence_relations"):
        result[field] = _unique_dicts([*result.get(field, []), *extra.get(field, [])])
    for field in ("agency_ids", "location_candidates", "location_ids", "independent_source_ids"):
        result[field] = sorted(set(result.get(field, [])) | set(extra.get(field, [])))
    result["independent_source_count"] = len(result["independent_source_ids"])
    return result


def _apply_official_reschedule(
    target: dict[str, Any], replacement: dict[str, Any], relation: dict[str, Any]
) -> dict[str, Any]:
    if target.get("fusion_status") != "CONFIRMED":
        raise ValueError("official reschedule target must be CONFIRMED")
    if target.get("event_type") != replacement.get("event_type"):
        raise ValueError("official reschedule event_type mismatch")
    relation_id = _stable_id(
        "OR",
        "|".join(
            [
                relation["source_document_version_id"],
                relation["target_document_version_id"],
                relation["relation"],
                relation["evidence_locator"],
            ]
        ),
    )
    result = _overlay_event_evidence(target, replacement)
    result["public_event_id"] = target["public_event_id"]
    result["canonical_title"] = replacement.get("canonical_title") or target.get("canonical_title")
    result["event_date"] = replacement.get("event_date") or target.get("event_date")
    result["start_at"] = replacement.get("start_at") or target.get("start_at")
    result["end_at"] = replacement.get("end_at") or target.get("end_at")
    result["updated_at"] = replacement.get("updated_at") or target.get("updated_at")
    result["conflict_fields"] = sorted(set(target.get("conflict_fields", [])) | set(replacement.get("conflict_fields", [])))
    result["uncertain_fields"] = sorted(set(target.get("uncertain_fields", [])) | set(replacement.get("uncertain_fields", [])))
    result["fusion_status"] = "CONFLICT" if replacement.get("fusion_status") == "CONFLICT" else "CONFIRMED"
    result["link_reasons"] = list(dict.fromkeys([*target.get("link_reasons", []), *replacement.get("link_reasons", []), "official_reschedule"]))
    result["manual_history"] = _unique_dicts([*target.get("manual_history", []), *replacement.get("manual_history", [])])
    result["background"] = _unique_dicts([*target.get("background", []), *replacement.get("background", [])])
    result["source_state"] = "CURRENT"
    result["lkg"] = False

    canonical_name = target.get("named_event_id") or replacement.get("named_event_id")
    result["named_event_id"] = canonical_name
    names = {
        name
        for name in [target.get("named_event_id"), replacement.get("named_event_id"), *target.get("named_event_aliases", []), *replacement.get("named_event_aliases", [])]
        if isinstance(name, str) and name
    }
    result["named_event_aliases"] = sorted(names - {canonical_name}) if canonical_name else sorted(names)

    history = [*target.get("occurrence_history", []), *replacement.get("occurrence_history", [])]
    history_record = {
        "relation_id": relation_id,
        **copy.deepcopy(relation),
        "from_public_event_id": replacement["public_event_id"],
        "to_public_event_id": target["public_event_id"],
        "previous_event_date": target.get("event_date"),
        "new_event_date": replacement.get("event_date"),
    }
    if not any(item.get("relation_id") == relation_id for item in history):
        history.append(history_record)
    result["occurrence_history"] = _unique_dicts(history)

    redirects = [*target.get("public_event_redirects", []), *replacement.get("public_event_redirects", [])]
    if replacement["public_event_id"] != target["public_event_id"]:
        redirects.append(
            {
                "from_public_event_id": replacement["public_event_id"],
                "to_public_event_id": target["public_event_id"],
                "reason": "OFFICIAL_RESCHEDULE",
                "relation_id": relation_id,
            }
        )
    result["public_event_redirects"] = _unique_dicts(redirects)
    if replacement.get("entity_registry") is not None:
        result["entity_registry"] = copy.deepcopy(replacement["entity_registry"])
    return result


def reconcile_public_events(
    previous_events: list[dict[str, Any]],
    documents: list[dict[str, Any]],
    *,
    snapshot_complete: bool,
    entity_registry: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Keep prior events when a partial source cannot prove removal.

    ponytail: carry-forward is a bounded linear merge of event IDs; replace with
    persisted source-level reconciliation if event volume or source granularity grows.
    """
    if not isinstance(previous_events, list):
        raise ValueError("previous_events must be an array")
    current = fuse_documents(documents, entity_registry=entity_registry)
    by_id = {event["public_event_id"]: event for event in current}
    current_by_document_version = {
        link["document_version_id"]: event
        for event in current
        for link in event.get("linked_document_versions", [])
    }
    previous_by_document_version: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for previous in previous_events:
        if not isinstance(previous, dict) or not previous.get("public_event_id"):
            continue
        for link in previous.get("linked_document_versions", []):
            document_version_id = link.get("document_version_id") if isinstance(link, dict) else None
            if document_version_id:
                previous_by_document_version[document_version_id][previous["public_event_id"]] = previous

    grouped_relations: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    registry_receipt = _load_entity_registry_module().registry_receipt(entity_registry) if entity_registry is not None else None
    for document in documents:
        for raw_relation in document.get("occurrence_relations", []):
            if raw_relation["review_decision"]["status"] != "CONFIRMED":
                continue
            if registry_receipt is None:
                raise ValueError("confirmed occurrence relation requires explicit entity registry")
            review = raw_relation["review_decision"]
            if {"registry_version": review["registry_version"], "registry_hash": review["registry_hash"]} != registry_receipt:
                raise ValueError("occurrence relation registry binding mismatch")
            current_event = current_by_document_version.get(document["document_version_id"])
            if current_event is None:
                raise ValueError("occurrence relation source document is not fused")
            grouped_relations[current_event["public_event_id"]].append(
                (_relation_projection(document, raw_relation), current_event)
            )

    for current_id, relations in grouped_relations.items():
        target_versions = {relation["target_document_version_id"] for relation, _ in relations}
        if len(target_versions) != 1:
            raise ValueError("one current event cannot reschedule multiple target occurrences")
        target_version = next(iter(target_versions))
        target_candidates = list(previous_by_document_version.get(target_version, {}).values())
        if len(target_candidates) != 1:
            raise ValueError("occurrence relation target must resolve to one previous event")
        previous_target = target_candidates[0]
        target_document_ids = {
            link.get("document_id")
            for link in previous_target.get("linked_document_versions", [])
            if link.get("document_version_id") == target_version
        }
        if any(relation["target_document_id"] not in target_document_ids for relation, _ in relations):
            raise ValueError("occurrence relation target document mismatch")
        target_id = previous_target["public_event_id"]
        target = _overlay_event_evidence(previous_target, by_id[target_id]) if target_id in by_id and target_id != current_id else copy.deepcopy(previous_target)
        for relation, replacement in relations:
            target = _apply_official_reschedule(target, replacement, relation)
        if current_id != target_id:
            by_id.pop(current_id, None)
        by_id[target_id] = target

    if snapshot_complete:
        return sorted(by_id.values(), key=lambda event: event["public_event_id"])

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
    registry = load_entity_registry()
    documents = [
        _demo_document("S-CITY", "agency:tc-news", "大型活動公告"),
        _demo_document("S-POLICE", "agency:tc-police", "大型活動交通疏導"),
        _demo_document("S-TRAFFIC", "agency:tc-traffic", "大型活動交通資訊"),
    ]
    events = fuse_documents(documents, entity_registry=registry)
    assert len(events) == 1 and events[0]["fusion_status"] == "CONFIRMED"
    assert events[0]["entity_registry"]["registry_hash"] == _load_entity_registry_module().registry_hash(registry)
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
    revised = fuse_documents(
        [*documents[:2], _demo_document("S-TRAFFIC", "agency:tc-traffic", "大型活動交通資訊", version="v2", start="16:00")],
        entity_registry=registry,
    )[0]
    assert revised["public_event_id"] == event["public_event_id"]
    assert revised["fusion_status"] == "CONFLICT"
    candidate = confirm_candidate(
        fuse_documents([documents[0]], entity_registry=registry)[0],
        operator="demo",
        decided_at="2026-09-21T10:00:00+08:00",
    )
    assert candidate["fusion_status"] == "CONFIRMED"
    assert event["background"][0]["role"] == "BACKGROUND_ONLY"
    print(f"PUBLIC_EVENT_FUSION_SELF_CHECK_OK event={event['public_event_id']} sources=3 revision=conflict")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--entity-registry", type=Path)
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
    registry = load_entity_registry(args.entity_registry) if args.entity_registry else None
    output = {"schema_version": 1, "public_events": fuse_documents(documents, entity_registry=registry)}
    text = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
