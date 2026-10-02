#!/usr/bin/env python3
"""Small, deterministic schema migration and V2 replay registry."""

from __future__ import annotations

import argparse
import base64
import binascii
import copy
import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
TARGET_SCHEMA_VERSION = 3
HASH = re.compile(r"^[0-9a-f]{64}$")
OBJECT_KEYS = ("PublicEvent", "ChangeEvent", "EvidenceEnvelope")
HASHED_OBJECT_KEYS = ("PublicEvent", "EvidenceEnvelope")
MANUAL_STATE_KEYS = ("manual_state", "watch_state", "handoff_state", "fusion_state", "review_state")
MANUAL_STATE_REAPPLY = {
    "manual_state": None,
    "watch_state": "scripts/handoff-state.py watch",
    "handoff_state": "scripts/handoff-state.py confirm",
    "fusion_state": "scripts/public-event-fusion.py",
    "review_state": "scripts/review-inbox.py decide",
}
# The real field names each owned state uses; a state that matches none of them is
# reported as an unrecognized shape rather than counted as one decision.
MANUAL_STATE_DECISION_FIELDS = {
    "watch_state": ("watch_items",),
    "handoff_state": ("watch_items", "handoffs"),
    "review_state": ("items",),
    "fusion_state": ("manual_history",),
}
MANUAL_STATE_STRATEGY = "PRESERVE_SEPARATELY_NO_AUTOMATIC_MUTATION"
IDENTITY_KEYS = {
    "PublicEvent": "stable_id",
    "ChangeEvent": "event_id",
    "EvidenceEnvelope": "evidence_id",
}
REQUIRED_FIELDS = {
    "PublicEvent": ("stable_id", "source_id", "source_snapshot_ref", "content_sha256", "created_at", "updated_at"),
    "ChangeEvent": ("event_id", "stable_id", "source_id", "source_snapshot_ref", "change_type", "created_at", "updated_at"),
    "EvidenceEnvelope": ("evidence_id", "raw_item_id", "source_snapshot_ref", "content_sha256", "official_url", "locator", "created_at", "updated_at"),
}
MIGRATION_REGISTRY = {
    (1, 2): "bind_source_snapshot_and_timestamps",
    (2, 3): "bind_derivation_versions",
}
# The body-hash semantics axis is versioned separately from the object schema:
# a hash means nothing until the algorithm that produced it is named.
BODY_HASH_VERSION = "canonical-payload-sha256-v1"
# What a pre-registry object actually is: the field existed, nothing named the
# algorithm, and this repository still ships producers that hash raw response
# bytes (online_collect canonical_bytes_sha256) and producers that hash the
# canonical payload (canonical_sha256). Migrating an undeclared object therefore
# records "unversioned", never a guess about which of the two it was.
LEGACY_BODY_HASH_VERSION = "legacy-unversioned-v1"
BODY_HASH_SEMANTICS = {
    BODY_HASH_VERSION: "content_sha256 over the canonical JSON encoding of the retained source payload",
    "raw-bytes-sha256-v1": "content_sha256 over the retained raw response bytes",
    LEGACY_BODY_HASH_VERSION: "content_sha256 produced before body-hash semantics were versioned; the producer may have hashed raw response bytes or the canonical payload, so this is not evidence of either",
}
UNDETERMINED_BODY_HASH_VERSION = "UNDETERMINABLE_NO_RAW_SNAPSHOT"
MISSING_EVIDENCE_REASON = "missing_retained_evidence"
UNRESOLVED = "UNKNOWN_UNDECLARED"
# Receipts written into the migrated artifact are derived metadata about a run,
# never input: re-running a migration on its own output must stay byte-stable.
DERIVED_RECEIPT_KEYS = ("apply_receipt", "body_hash_migration_receipt")
# Version axes a stored publication must be able to explain.
VERSION_AXES = (
    "schema_version",
    "body_hash_version",
    "parser_version",
    "semantics_version",
    "projection_version",
    "derivation_version",
    "model_version",
)
# Facts about this module's own replay path, reported separately from the
# artifact's versions so the two are never confused.
REPLAY_TOOL_VERSIONS = {
    "parser_version": "legacy-feed-adapter-v1",
    "semantics_version": "intel_v2.semantics-v1",
    "projection_version": "v2-shadow-v1",
    "body_hash_version": BODY_HASH_VERSION,
}
QUERY_STORE = ROOT / "scripts" / "query-store.py"
PUBLISHED_DATA_DIR = (ROOT / "apps" / "web" / "public").resolve()
SEMANTICS_MODULE = "govintel_replay_semantics"
QUERY_STORE_MODULE = "govintel_query_store"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256(value: Any) -> str:
    raw = value if isinstance(value, bytes) else canonical_bytes(value)
    return hashlib.sha256(raw).hexdigest()


def parse_timestamp(value: Any) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp is required")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("timestamp must be ISO-8601") from error
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return stamp.isoformat(timespec="seconds")


def _fallback_timestamp(item: dict[str, Any], observed_at: str) -> str:
    for key in ("updated_at", "detected_at", "observed_at", "fetched_at", "published_at"):
        if item.get(key):
            return parse_timestamp(item[key])
    return parse_timestamp(observed_at)


def _stable_identity(kind: str, item: dict[str, Any]) -> str:
    key = IDENTITY_KEYS[kind]
    value = item.get(key)
    if isinstance(value, str) and value.strip():
        return value
    # A legacy event may only have a source/raw identity. The derivation is
    # deterministic and remains auditable in migration_history.
    seed = {field: item.get(field) for field in ("source_id", "stable_id", "raw_item_id", "source_snapshot_ref", "content_sha256", "locator")}
    if not any(value for value in seed.values()):
        raise ValueError(f"{kind} has no stable identity material")
    prefix = {"PublicEvent": "PUB", "ChangeEvent": "EVT", "EvidenceEnvelope": "EVD"}[kind]
    return f"{prefix}-{sha256(seed)[:20].upper()}"


def _snapshot_ref(item: dict[str, Any]) -> str:
    value = item.get("source_snapshot_ref") or item.get("snapshot_ref")
    if isinstance(value, str) and value.strip():
        return value
    raw_item_id = item.get("raw_item_id")
    if isinstance(raw_item_id, str) and raw_item_id.strip():
        return f"raw_items/{raw_item_id}"
    raise ValueError("source_snapshot_ref is required; migration cannot invent source evidence")


def _migrate_step(kind: str, item: dict[str, Any], from_version: int, observed_at: str, bundle: dict[str, Any]) -> dict[str, Any]:
    name = MIGRATION_REGISTRY.get((from_version, from_version + 1))
    if name is None:
        raise ValueError(f"no registry path {from_version}->{from_version + 1}")
    step = MIGRATION_STEPS.get(name)
    if step is None:
        raise ValueError(f"registry step {name} for {from_version}->{from_version + 1} is registered but not implemented")
    value = copy.deepcopy(item)
    step(kind, value, observed_at, bundle)
    value["schema_version"] = from_version + 1
    value[IDENTITY_KEYS[kind]] = _stable_identity(kind, value)
    return value


def _step_bind_source_snapshot_and_timestamps(kind: str, value: dict[str, Any], observed_at: str, bundle: dict[str, Any]) -> None:
    value["source_snapshot_ref"] = _snapshot_ref(value)
    created = _fallback_timestamp(value, observed_at)
    value.setdefault("created_at", created)
    value.setdefault("updated_at", created)


def _step_bind_derivation_versions(kind: str, value: dict[str, Any], observed_at: str, bundle: dict[str, Any]) -> None:
    value.setdefault("derivation_version", bundle.get("semantics_version") or "legacy-v2")
    value.setdefault("parser_version", bundle.get("parser_version") or "legacy-parser-unknown")
    if kind in HASHED_OBJECT_KEYS:
        value.setdefault("body_hash_version", LEGACY_BODY_HASH_VERSION)


MIGRATION_STEPS: dict[str, Callable[[str, dict[str, Any], str, dict[str, Any]], None]] = {
    "bind_source_snapshot_and_timestamps": _step_bind_source_snapshot_and_timestamps,
    "bind_derivation_versions": _step_bind_derivation_versions,
}


def strip_derived_receipts(value: dict[str, Any]) -> dict[str, Any]:
    """Drop receipts a previous run embedded, so a re-run hashes the same data."""
    return {key: item for key, item in value.items() if key not in DERIVED_RECEIPT_KEYS}


def validate_object(kind: str, item: dict[str, Any], expected_version: int = TARGET_SCHEMA_VERSION) -> None:
    if kind not in OBJECT_KEYS or not isinstance(item, dict):
        raise ValueError(f"unsupported {kind} object")
    if item.get("schema_version") != expected_version:
        raise ValueError(f"{kind} schema_version must be {expected_version}")
    for field in REQUIRED_FIELDS[kind]:
        if not isinstance(item.get(field), str) or not item[field].strip():
            raise ValueError(f"{kind} missing {field}")
    for field in ("created_at", "updated_at"):
        parse_timestamp(item[field])
    if kind in HASHED_OBJECT_KEYS:
        if not HASH.fullmatch(item["content_sha256"]):
            raise ValueError(f"{kind} content_sha256 is invalid")
        declared = item.get("body_hash_version")
        if not isinstance(declared, str) or not declared.strip():
            raise ValueError(f"{kind} missing body_hash_version; a content hash is unreadable without its semantics version")
        if declared not in BODY_HASH_SEMANTICS:
            raise ValueError(f"{kind} body_hash_version {declared} is not registered")



def _manual_hashes(bundle: dict[str, Any]) -> dict[str, str | None]:
    return {
        key: sha256(bundle[key]) if key in bundle else None
        for key in MANUAL_STATE_KEYS
    }


def migrate_bundle(bundle: dict[str, Any], *, target_version: int = TARGET_SCHEMA_VERSION) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not isinstance(bundle, dict) or not isinstance(bundle.get("objects"), dict):
        raise ValueError("migration input must contain an objects object")
    source_version = int(bundle.get("schema_version", 1))
    if source_version > target_version:
        raise ValueError(f"input schema_version {source_version} is newer than target {target_version}")
    observed_at = parse_timestamp(bundle.get("observed_at") or "2026-01-01T00:00:00+00:00")
    migrated = strip_derived_receipts(copy.deepcopy(bundle))
    migrated["schema_version"] = target_version
    errors: list[dict[str, Any]] = []
    declared_body_hash = bundle.get("body_hash_version")
    if declared_body_hash is not None and (not isinstance(declared_body_hash, str) or declared_body_hash not in BODY_HASH_SEMANTICS):
        errors.append({"kind": None, "index": None, "error": f"bundle body_hash_version {declared_body_hash!r} is not registered"})
    if source_version < target_version:
        # An object that arrives without a declaration is unversioned, not canonical:
        # record that, so the bundle never claims a semantics it cannot prove.
        migrated["body_hash_version"] = declared_body_hash or LEGACY_BODY_HASH_VERSION
    missing_kinds = [kind for kind in OBJECT_KEYS if kind not in bundle["objects"]]
    unknown_kinds = sorted(set(bundle["objects"]) - set(OBJECT_KEYS))
    errors.extend({"kind": kind, "index": None, "error": "missing object collection"} for kind in missing_kinds)
    errors.extend({"kind": kind, "index": None, "error": "unknown object collection"} for kind in unknown_kinds)
    affected = 0
    object_counts: dict[str, int] = {}
    for kind in OBJECT_KEYS:
        rows = bundle["objects"].get(kind)
        if kind not in bundle["objects"]:
            object_counts[kind] = 0
            continue
        if not isinstance(rows, list):
            errors.append({"kind": kind, "index": None, "error": "objects value must be an array"})
            continue
        output_rows = []
        seen_identities: set[str] = set()
        for index, item in enumerate(rows):
            try:
                current = copy.deepcopy(item)
                version = int(current.get("schema_version", source_version))
                while version < target_version:
                    if (version, version + 1) not in MIGRATION_REGISTRY:
                        raise ValueError(f"no registry path {version}->{version + 1}")
                    current = _migrate_step(kind, current, version, observed_at, bundle)
                    version += 1
                    affected += 1
                validate_object(kind, current, target_version)
                identity = current[IDENTITY_KEYS[kind]]
                if identity in seen_identities:
                    raise ValueError(f"duplicate {kind} identity: {identity}")
                seen_identities.add(identity)
                output_rows.append(current)
            except (TypeError, ValueError, KeyError) as error:
                errors.append({"kind": kind, "index": index, "error": str(error)})
        migrated["objects"][kind] = output_rows
        object_counts[kind] = len(output_rows)

    history = list(bundle.get("migration_history", [])) if isinstance(bundle.get("migration_history", []), list) else []
    history_entry = {
        "from_version": source_version,
        "to_version": target_version,
        "registry": [f"{left}->{right}:{name}" for (left, right), name in MIGRATION_REGISTRY.items() if source_version <= left < target_version],
        "input_sha256": sha256(bundle),
    }
    if source_version < target_version and history_entry not in history:
        history.append(history_entry)
    if history or "migration_history" in bundle:
        migrated["migration_history"] = history
    manual_before = _manual_hashes(bundle)
    manual_after = _manual_hashes(migrated)
    report = {
        "schema_version": 1,
        "report_type": "MIGRATION_DRY_RUN",
        "from_version": source_version,
        "to_version": target_version,
        "migration_path": history_entry["registry"],
        "input_sha256": sha256(bundle),
        "output_sha256": sha256(migrated) if not errors else None,
        "affected_count": affected,
        "error_count": len(errors),
        "errors": errors,
        "object_counts": object_counts,
        "version_binding": {
            "parser_version": bundle.get("parser_version"),
            "semantics_version": bundle.get("semantics_version"),
            "projection_version": bundle.get("projection_version"),
            "body_hash_version": migrated.get("body_hash_version"),
        },
        "manual_state_hashes": {"before": manual_before, "after": manual_after, "preserved": manual_before == manual_after},
        "idempotent_at_target": not errors and source_version == target_version and migrated == bundle,
    }
    return (None if errors else migrated), report


def replay_publication(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("feed"), dict):
        raise ValueError("replay input must contain feed")
    semantics_path = ROOT / "intel_v2" / "semantics.py"
    spec = importlib.util.spec_from_file_location("govintel_replay_semantics", semantics_path)
    if spec is None or spec.loader is None:
        raise ValueError("V2 semantics module unavailable")
    semantics = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = semantics
    spec.loader.exec_module(semantics)
    feed = payload["feed"]
    observed_at = payload.get("observed_at") or feed.get("generated_at")
    if not observed_at:
        raise ValueError("replay requires observed_at or feed.generated_at")
    current = [semantics.feed_item_to_version(item, observed_at) for item in feed.get("items", [])]
    state, events = semantics.compare_snapshot(
        payload.get("previous_state"),
        current,
        observed_at,
        snapshot_complete=payload.get("snapshot_complete", True),
    )
    manual = {key: payload[key] for key in MANUAL_STATE_KEYS if key in payload}
    event_rows = [event.to_dict() for event in events]
    receipt = {
        "schema_version": 1,
        "receipt_type": "DETERMINISTIC_REPLAY",
        "parser_version": payload.get("parser_version", "legacy-feed-adapter-v1"),
        "semantics_version": "intel_v2.semantics-v1",
        "projection_version": payload.get("projection_version", "v2-shadow-v1"),
        "source_collection_run_id": feed.get("collection_run_id"),
        "input_sha256": sha256({"feed": feed, "previous_state": payload.get("previous_state")}),
        "state_sha256": sha256(state),
        "event_ids": [event["event_id"] for event in event_rows],
        "first_seen_event_count": sum(event.get("temporal_basis") == "FIRST_SEEN" for event in event_rows),
        "manual_state_hashes": _manual_hashes(payload),
        "manual_state_strategy": MANUAL_STATE_STRATEGY,
        "manual_state_reapply_plan": manual_state_reapply_plan(manual),
    }
    return {"state": state, "events": event_rows, "replay_receipt": receipt, **manual}


def count_manual_decisions(key: str, value: Any) -> tuple[int, bool]:
    """Count the human decisions inside a preserved manual state blob.

    Returns (count, shape_recognised). Each owned state has its own real field
    names, and a payload that matches none of them is reported as unrecognized
    rather than assumed to hold one decision.
    """
    fields = MANUAL_STATE_DECISION_FIELDS.get(key)
    if fields is None or not isinstance(value, dict):
        return 0, False
    recognised = False
    total = 0
    for field in fields:
        rows = value.get(field)
        if isinstance(rows, (list, dict)):
            recognised = True
            total += len(rows)
    return total, recognised


def manual_state_reapply_plan(manual: dict[str, Any]) -> list[dict[str, Any]]:
    """Human merge/split/watch/handoff decisions are preserved by hash, never re-applied silently.

    Replay is not allowed to re-apply a recorded human decision into a freshly derived
    event: the writer that owns the decision has to confirm it again. The plan is the
    explicit list of what a replay deliberately did NOT apply.
    """
    plan: list[dict[str, Any]] = []
    for key in MANUAL_STATE_KEYS:
        if key not in manual:
            continue
        decisions, recognised = count_manual_decisions(key, manual[key])
        plan.append({
            "state_key": key,
            "strategy": MANUAL_STATE_STRATEGY,
            "sha256": sha256(manual[key]),
            "decision_count": decisions,
            "shape_recognised": recognised,
            "requires_human_confirmation": decisions > 0,
            "reapply_via": MANUAL_STATE_REAPPLY.get(key),
        })
    return plan


def _safe_sha256(value: Any) -> tuple[str | None, str | None]:
    try:
        return sha256(value), None
    except (TypeError, ValueError) as error:
        return None, str(error)


def _value_is_encodable(value: Any) -> bool:
    try:
        canonical_bytes(value)
    except (TypeError, ValueError):
        return False
    return True


def _declared_version_is_usable(axis: str, value: Any) -> bool:
    if axis == "schema_version":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 1
    if axis == "body_hash_version":
        return isinstance(value, str) and value in BODY_HASH_SEMANTICS
    return isinstance(value, str) and bool(value.strip())


def explain_publication_receipt(artifact: Any) -> dict[str, Any]:
    """Explain which schema/body-hash/parser/semantics/projection version produced a stored publication.

    A value is only reported when the artifact declares a usable one, or when every
    preserved object declares the same one. Anything else is reported as unresolved, and
    preserved objects that disagree are never arbitrated: this tool explains receipts, it
    does not complete them. The versions this module's own replay path uses are reported
    separately so they are never mistaken for the artifact's.
    """
    if not isinstance(artifact, dict):
        raise ValueError("publication artifact must be an object")
    binding = artifact.get("version_binding") if isinstance(artifact.get("version_binding"), dict) else {}
    objects = artifact.get("objects") if isinstance(artifact.get("objects"), dict) else {}

    def object_declarations(field: str) -> set[str]:
        found: set[str] = set()
        for rows in objects.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or row.get(field) is None:
                    continue
                try:
                    found.add(canonical_bytes(row[field]).decode("utf-8"))
                except (TypeError, ValueError):
                    continue
        return found

    versions: dict[str, dict[str, Any]] = {}
    for axis in VERSION_AXES:
        value = artifact.get(axis)
        if value is None:
            value = binding.get(axis)
        if value is not None and not _value_is_encodable(value):
            versions[axis] = {"value": UNRESOLVED, "source": "unusable_declared_value"}
            continue
        declared = object_declarations(axis)
        declared_value = json.loads(next(iter(declared))) if len(declared) == 1 else None
        if value is not None:
            if not _declared_version_is_usable(axis, value):
                versions[axis] = {"value": UNRESOLVED, "source": "unusable_declared_value"}
            elif declared and canonical_bytes(value).decode("utf-8") not in declared:
                versions[axis] = {"value": UNRESOLVED, "source": "conflicting_object_declarations"}
            elif len(declared) > 1:
                versions[axis] = {"value": UNRESOLVED, "source": "conflicting_object_declarations"}
            else:
                versions[axis] = {"value": value, "source": "declared"}
            continue
        if declared_value is not None:
            # An object declaration is held to exactly the same bar as a declared one, so
            # the two paths can never disagree about whether a value is usable.
            if _declared_version_is_usable(axis, declared_value):
                versions[axis] = {"value": declared_value, "source": "object_declarations"}
            else:
                versions[axis] = {"value": UNRESOLVED, "source": "unusable_declared_value"}
            continue
        if len(declared) > 1:
            versions[axis] = {"value": UNRESOLVED, "source": "conflicting_object_declarations"}
            continue
        versions[axis] = {"value": UNRESOLVED, "source": "unresolvable"}

    history = artifact.get("migration_history")
    history = history if isinstance(history, list) else []
    path: list[str] = []
    for entry in history:
        if isinstance(entry, dict) and isinstance(entry.get("registry"), list):
            path.extend(str(step) for step in entry["registry"])
    source_sha256, hash_error = _safe_sha256(artifact)
    return {
        "schema_version": 1,
        "receipt_type": "PUBLICATION_VERSION_EXPLANATION",
        "source_receipt_type": artifact.get("receipt_type"),
        "source_receipt_sha256": source_sha256,
        "source_receipt_hash_error": hash_error,        "versions": versions,
        "unresolved": sorted(axis for axis, entry in versions.items() if entry["source"] != "declared" and entry["source"] != "object_declarations"),
        "migration_path": path,
        "migration_path_source": "artifact_migration_history" if path else "absent_never_migrated",
        "migration_history": history,
        "replay_tool_versions": dict(REPLAY_TOOL_VERSIONS),
        "body_hash_semantics": dict(BODY_HASH_SEMANTICS),
    }


def _retained_body_hash(target_body_hash_version: str, snapshot: Any) -> str:
    if target_body_hash_version not in BODY_HASH_SEMANTICS:
        raise ValueError(f"body hash semantics {target_body_hash_version} is not registered")
    if not isinstance(snapshot, dict):
        raise ValueError("retained source snapshot is absent for this source_snapshot_ref")
    if target_body_hash_version == "raw-bytes-sha256-v1":
        if "raw_bytes_base64" not in snapshot:
            raise ValueError("retained source snapshot does not carry raw bytes")
        try:
            raw = base64.b64decode(snapshot["raw_bytes_base64"], validate=True)
        except (binascii.Error, TypeError, ValueError) as error:
            raise ValueError("retained source snapshot raw bytes are not base64") from error
        return sha256(raw)
    if "payload" not in snapshot:
        raise ValueError("retained source snapshot does not carry a canonical payload")
    return sha256(snapshot["payload"])


def rehash_bundle(bundle: dict[str, Any], *, target_body_hash_version: str = BODY_HASH_VERSION) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Re-derive content hashes under a new body-hash semantics version.

    A body-hash semantics bump changes what the stored hash *means*, so it can only be
    re-derived from retained raw/canonical evidence. When retention dropped the bytes the
    migration refuses and reports the hash as undeterminable instead of inventing one.
    A changed hash also means a changed stable identity, so the rename is cascaded into the
    ChangeEvents that reference it and dangling references fail the whole migration.
    """
    if not isinstance(bundle, dict) or not isinstance(bundle.get("objects"), dict):
        raise ValueError("body hash migration input must contain an objects object")
    if target_body_hash_version not in BODY_HASH_SEMANTICS:
        return None, {
            "schema_version": 1,
            "report_type": "BODY_HASH_MIGRATION_DRY_RUN",
            "target_body_hash_version": target_body_hash_version,
            "error_count": 1,
            "errors": [{"kind": None, "index": None, "error": f"body hash semantics {target_body_hash_version} is not registered"}],
            "affected_count": 0,
            "undetermined_body_hash_version": None,
        }
    snapshots = bundle.get("raw_snapshots") if isinstance(bundle.get("raw_snapshots"), dict) else {}
    migrated = strip_derived_receipts(copy.deepcopy(bundle))
    errors: list[dict[str, Any]] = []
    rehashed: list[dict[str, Any]] = []
    seen_from: set[str] = set()
    affected = 0
    undetermined = False
    # Every collection is validated, not just the hashed ones, so a successful rehash
    # means the whole bundle is sound rather than two thirds of it.
    for kind in OBJECT_KEYS:
        rows = bundle["objects"].get(kind)
        if kind not in bundle["objects"]:
            errors.append({"kind": kind, "index": None, "error": "missing object collection"})
            continue
        if not isinstance(rows, list):
            errors.append({"kind": kind, "index": None, "error": "objects value must be an array"})
            continue
        output_rows: list[tuple[int, dict[str, Any]]] = []
        for index, item in enumerate(rows):
            current = copy.deepcopy(item)
            try:
                version = current.get("schema_version", TARGET_SCHEMA_VERSION)
                validate_object(kind, current, int(version))
                if kind not in HASHED_OBJECT_KEYS or current["body_hash_version"] == target_body_hash_version:
                    output_rows.append((index, current))
                    continue
                seen_from.add(current["body_hash_version"])
                content_sha256 = _retained_body_hash(target_body_hash_version, snapshots.get(current["source_snapshot_ref"]))
            except (TypeError, ValueError, KeyError) as error:
                if "retained source snapshot" in str(error):
                    undetermined = True
                    errors.append({"kind": kind, "index": index, "error": str(error), "reason": MISSING_EVIDENCE_REASON})
                else:
                    errors.append({"kind": kind, "index": index, "error": str(error)})
                continue
            before_identity = current[IDENTITY_KEYS[kind]]
            current["content_sha256"] = content_sha256
            current["body_hash_version"] = target_body_hash_version
            # A changed hash means the same ID would describe two different bodies, so
            # the stable identity is re-derived from the new identity material.
            current.pop(IDENTITY_KEYS[kind], None)
            current[IDENTITY_KEYS[kind]] = _stable_identity(kind, current)
            validate_object(kind, current, int(version))
            affected += 1
            rehashed.append({
                "kind": kind,
                "index": index,
                "source_snapshot_ref": current["source_snapshot_ref"],
                "identity_before": before_identity,
                "identity_after": current[IDENTITY_KEYS[kind]],
            })
            output_rows.append((index, current))
        seen_identities: set[str] = set()
        for input_index, row in output_rows:
            identity = row.get(IDENTITY_KEYS[kind]) if isinstance(row, dict) else None
            if not isinstance(identity, str):
                continue
            if identity in seen_identities:
                # Two objects that hashed differently can collapse onto one identity once
                # they are re-derived under the same semantics; that silently merges them.
                # The index is the input row, so remediation points at the right one.
                errors.append({"kind": kind, "index": input_index, "error": f"duplicate {kind} identity: {identity}"})
            seen_identities.add(identity)
        migrated["objects"][kind] = [row for _, row in output_rows]

    renames = {
        row["identity_before"]: row["identity_after"]
        for row in rehashed
        if row["kind"] == "PublicEvent"
    }
    cascaded = 0
    for index, row in enumerate(migrated["objects"].get("ChangeEvent", [])):
        replacement = renames.get(row.get("stable_id"))
        if replacement is None:
            continue
        row["stable_id"] = replacement
        cascaded += 1
    public_ids = {
        row.get(IDENTITY_KEYS["PublicEvent"])
        for row in migrated["objects"].get("PublicEvent", [])
        if isinstance(row, dict)
    }
    for index, row in enumerate(migrated["objects"].get("ChangeEvent", [])):
        if row.get("stable_id") not in public_ids:
            errors.append({"kind": "ChangeEvent", "index": index, "error": f"ChangeEvent stable_id {row.get('stable_id')} does not resolve to a migrated PublicEvent"})
    hashed_present = any(isinstance(bundle["objects"].get(kind), list) and bundle["objects"][kind] for kind in HASHED_OBJECT_KEYS)
    if not errors and hashed_present:
        # After a clean run every hashed object declares the target, so the bundle-level
        # declaration follows them; leaving it behind would leave the bundle contradicting
        # its own objects even when this run had nothing left to rehash.
        migrated["body_hash_version"] = target_body_hash_version

    history = list(bundle.get("body_hash_migration", [])) if isinstance(bundle.get("body_hash_migration", []), list) else []
    entry = {
        "from_body_hash_versions": sorted(seen_from),
        "to_body_hash_version": target_body_hash_version,
        "input_sha256": sha256(bundle),
    }
    if seen_from and entry not in history:
        history.append(entry)
        migrated["body_hash_migration"] = history
    elif seen_from:
        migrated["body_hash_migration"] = history
    report = {
        "schema_version": 1,
        "report_type": "BODY_HASH_MIGRATION_DRY_RUN",
        "target_body_hash_version": target_body_hash_version,
        "from_body_hash_versions": sorted(seen_from),
        "input_sha256": sha256(bundle),
        "output_sha256": sha256(migrated) if not errors else None,
        "affected_count": affected,
        "identity_cascade_count": cascaded,
        "error_count": len(errors),
        "errors": errors,
        "rehashed": rehashed,
        "retained_snapshot_count": len(snapshots),
        "undetermined_body_hash_version": UNDETERMINED_BODY_HASH_VERSION if undetermined else None,
        "idempotent_at_target": not errors and not seen_from and migrated == bundle,
    }
    return (None if errors else migrated), report


def load_query_store_module() -> Any:
    spec = importlib.util.spec_from_file_location(QUERY_STORE_MODULE, QUERY_STORE)
    if spec is None or spec.loader is None:
        raise ValueError("query store module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def rebuild_query_store(feed: Path, status: Path, brief: Path, *, expected_generation: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Rebuild the Query Store from one named canonical generation and prove the binding.

    The store is never a truth store: it is derived from the feed/status/brief triple and
    its generation id covers all three artifact hashes plus the projection and policy
    binding, so a rebuild either reproduces that exact generation or fails closed. Each
    artifact is read exactly once so the version explanation describes the same bytes the
    receipt hashes.
    """
    query_store = load_query_store_module()
    feed_document, feed_hash = query_store.load_json(Path(feed))
    status_document, status_hash = query_store.load_json(Path(status))
    brief_document, brief_hash = query_store.load_json(Path(brief))
    store = query_store.build_store(
        feed_document,
        status_document,
        brief_document,
        {"feed": feed_hash, "status": status_hash, "brief": brief_hash},
    )
    generation_id = store["generation_id"]
    if expected_generation is not None and expected_generation != generation_id:
        raise ValueError(
            f"canonical generation mismatch: rebuilt {generation_id} but {expected_generation} was requested; "
            "the store was not written"
        )
    explanation = explain_publication_receipt(feed_document)
    explanation["source_document_sha256"] = feed_hash
    receipt = {
        "schema_version": 1,
        "receipt_type": "QUERY_STORE_REBUILD",
        "generation_id": generation_id,
        "expected_generation": expected_generation,
        "expected_generation_verified": expected_generation is not None,
        "store_schema_version": store["schema_version"],
        "projection_version": store["projection_version"],
        "store_projection_sha256": store["projection_sha256"],
        "policy_hash": store["policy"]["policy_hash"],
        "counts": store["counts"],
        "artifact_hashes": {
            "feed": feed_hash,
            "status": status_hash,
            "brief": brief_hash,
        },
        "version_explanation": explanation,
    }
    return store, receipt


def rebuild_query_store_to_path(
    feed: Path,
    status: Path,
    brief: Path,
    *,
    output: Path,
    receipt_output: Path | None = None,
    expected_generation: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    store, receipt = rebuild_query_store(feed, status, brief, expected_generation=expected_generation)
    output = Path(output)
    receipt_path = Path(receipt_output) if receipt_output is not None else output.with_name(f"{output.stem}.migration-receipt.json")
    if PUBLISHED_DATA_DIR in receipt_path.resolve().parents:
        # The served data directory is publication surface: a receipt dropped there would
        # publish internal artifact hashes and leave an untracked file in the checkout.
        raise ValueError(f"rebuild receipt must not be written inside {PUBLISHED_DATA_DIR}; pass receipt_output")
    # The store is authoritative and internally self-verifying (query-store's
    # atomic_write_json validates it, and validate_store recomputes the projection hash that
    # covers generation_id), so it is written first: a crash between the two writes can then
    # leave a missing or stale receipt, which is detectable by comparing its generation_id
    # against the store. Writing the receipt first would instead leave a receipt claiming a
    # generation that exists nowhere on disk.
    load_query_store_module().atomic_write_json(output, store)
    atomic_write(receipt_path, receipt)
    return store, receipt


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def self_check() -> None:
    base = {
        "schema_version": 1,
        "observed_at": "2026-09-21T00:00:00+00:00",
        "parser_version": "parser-v1",
        "semantics_version": "semantics-v1",
        "watch_state": {"schema_version": 1, "watch_items": {"WATCH-1": {"status": "WATCHING"}}},
        "handoff_state": {"schema_version": 1, "handoffs": [{"handoff_id": "H-1"}]},
        "objects": {
            "PublicEvent": [{"stable_id": "PUB-1", "source_id": "S-007", "raw_item_id": "RI-1", "content_sha256": "a" * 64, "title": "事件"}],
            "ChangeEvent": [{"stable_id": "PUB-1", "source_id": "S-007", "raw_item_id": "RI-1", "change_type": "NEW", "detected_at": "2026-09-21T00:00:00+00:00"}],
            "EvidenceEnvelope": [{"evidence_id": "EVD-1", "raw_item_id": "RI-1", "content_sha256": "b" * 64, "official_url": "https://official.test/1", "locator": "body"}],
        },
    }
    migrated, report = migrate_bundle(base)
    assert migrated and report["error_count"] == 0 and report["affected_count"] == 6
    assert report["manual_state_hashes"]["preserved"] is True
    again, second = migrate_bundle(migrated)
    assert again == migrated and second["idempotent_at_target"] is True
    bad = copy.deepcopy(base)
    bad["objects"]["PublicEvent"][0].pop("raw_item_id")
    failed, failed_report = migrate_bundle(bad)
    assert failed is None and failed_report["error_count"] == 1
    feed = {
        "schema_version": 1,
        "collection_run_id": "CR-1",
        "generated_at": "2026-09-21T00:00:00+00:00",
        "items": [{"stable_id": "S-007:1", "source_id": "S-007", "title": "首次", "official_url": "https://official.test/1", "content_sha256": "c" * 64, "published_at": None}],
    }
    replay = replay_publication({"feed": feed, "previous_state": {"schema_version": 1, "mode": "V2_SHADOW", "baseline_established_at": feed["generated_at"], "items": {}}})
    assert replay["events"][0]["temporal_basis"] == "FIRST_SEEN"
    assert replay["replay_receipt"]["first_seen_event_count"] == 1
    assert set(MIGRATION_STEPS) == set(MIGRATION_REGISTRY.values())
    assert migrated["objects"]["PublicEvent"][0]["body_hash_version"] == LEGACY_BODY_HASH_VERSION
    explanation = explain_publication_receipt(migrated)
    assert explanation["unresolved"] == ["model_version", "projection_version"]
    assert explanation["versions"]["parser_version"]["value"] == "parser-v1"
    assert explanation["versions"]["body_hash_version"] == {"value": LEGACY_BODY_HASH_VERSION, "source": "declared"}
    legacy = explain_publication_receipt({"receipt_type": "PUBLICATION_PROJECTION", "schema_version": 1})
    assert legacy["unresolved"] == ["body_hash_version", "derivation_version", "model_version", "parser_version", "projection_version", "semantics_version"]
    assert legacy["migration_path"] == [] and legacy["migration_path_source"] == "absent_never_migrated"
    assert legacy["replay_tool_versions"]["semantics_version"] == "intel_v2.semantics-v1"
    rehashed_input = copy.deepcopy(migrated)
    rehashed_input["raw_snapshots"] = {"raw_items/RI-1": {"payload": {"title": "事件"}}}
    for kind in HASHED_OBJECT_KEYS:
        rehashed_input["objects"][kind][0]["body_hash_version"] = "raw-bytes-sha256-v1"
    rehashed, rehash_report = rehash_bundle(rehashed_input)
    assert rehashed and rehash_report["error_count"] == 0 and rehash_report["affected_count"] == 2
    assert rehashed["objects"]["PublicEvent"][0]["content_sha256"] == sha256({"title": "事件"})
    assert rehash_report["identity_cascade_count"] == 1
    assert rehashed["objects"]["ChangeEvent"][0]["stable_id"] == rehashed["objects"]["PublicEvent"][0]["stable_id"]
    noop, noop_report = rehash_bundle(rehashed)
    assert noop == rehashed and noop_report["idempotent_at_target"] is True
    undeterminable = copy.deepcopy(rehashed_input)
    undeterminable["raw_snapshots"] = {}
    refused, refused_report = rehash_bundle(undeterminable)
    assert refused is None
    assert len([error for error in refused_report["errors"] if error.get("reason") == MISSING_EVIDENCE_REASON]) == 2
    assert refused_report["undetermined_body_hash_version"] == UNDETERMINED_BODY_HASH_VERSION
    assert all(error.get("reason") == MISSING_EVIDENCE_REASON for error in refused_report["errors"] if "kind" in error and error["kind"] in HASHED_OBJECT_KEYS)
    plan = manual_state_reapply_plan({"handoff_state": {"watch_items": {"W-1": {}}, "handoffs": [{"handoff_id": "H-1"}, {"handoff_id": "H-2"}]}})
    assert plan[0]["decision_count"] == 3 and plan[0]["shape_recognised"] is True
    print("MIGRATION_REPLAY_SELF_CHECK_OK registry=1->2->3 lkg_safe=true first_seen_preserved=true body_hash_versioned=true manual_decisions_preserved=true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-check")
    for name in ("dry-run", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
    replay = sub.add_parser("replay")
    replay.add_argument("--input", type=Path, required=True)
    replay.add_argument("--output", type=Path, required=True)
    rehash = sub.add_parser("rehash")
    rehash.add_argument("--input", type=Path, required=True)
    rehash.add_argument("--output", type=Path, required=True)
    rehash.add_argument("--body-hash-version", default=BODY_HASH_VERSION)
    explain = sub.add_parser("explain-receipt")
    explain.add_argument("--input", type=Path, required=True)
    explain.add_argument("--output", type=Path, required=True)
    rebuild = sub.add_parser("rebuild-query-store")
    rebuild.add_argument("--feed", type=Path, required=True)
    rebuild.add_argument("--status", type=Path, required=True)
    rebuild.add_argument("--brief", type=Path, required=True)
    rebuild.add_argument("--output", type=Path, required=True)
    rebuild.add_argument("--receipt-output", type=Path, default=None)
    rebuild.add_argument("--expect-generation", default=None)
    args = parser.parse_args(argv)
    if args.command == "self-check":
        self_check()
        return 0
    if args.command == "rebuild-query-store":
        store, _ = rebuild_query_store_to_path(args.feed, args.status, args.brief, output=args.output, receipt_output=args.receipt_output, expected_generation=args.expect_generation)
        print(f"QUERY_STORE_REBUILD_OK generation={store['generation_id'][:12]} items={store['counts']['publication_items']} output={args.output}")
        return 0
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if args.command in {"dry-run", "apply"}:
        migrated, report = migrate_bundle(payload)
        if args.command == "dry-run":
            atomic_write(args.output, report)
            status = "OK" if report["error_count"] == 0 else "FAILED"
            print(f"MIGRATION_DRY_RUN_{status} errors={report['error_count']} affected={report['affected_count']} output={args.output}")
            return 0 if report["error_count"] == 0 else 1
        if migrated is None:
            raise ValueError(f"migration refused with {report['error_count']} errors; existing output was not changed")
        # The audit receipt travels inside the same atomic write as the migrated data, so a
        # crash can never leave migrated objects whose producing versions are unknown.
        applied = {**migrated, "apply_receipt": report}
        atomic_write(args.output, applied)
        report_path = args.output.with_name(f"{args.output.stem}.migration-receipt.json")
        atomic_write(report_path, report)
        print(f"MIGRATION_{args.command.upper().replace('-', '_')}_OK errors={report['error_count']} affected={report['affected_count']} output={args.output}")
        return 0
    if args.command == "rehash":
        rehashed, report = rehash_bundle(payload, target_body_hash_version=args.body_hash_version)
        if report["error_count"] or rehashed is None:
            # A refused body-hash migration never touches the data path; the report goes
            # beside it so the reason stays auditable and the previous data stays intact.
            atomic_write(args.output.with_name(f"{args.output.stem}.body-hash-report.json"), report)
            print(f"BODY_HASH_MIGRATION_FAILED errors={report['error_count']} undetermined={report['undetermined_body_hash_version']} output={args.output} (unchanged)")
            return 1
        atomic_write(args.output, {**rehashed, "body_hash_migration_receipt": report})
        print(f"BODY_HASH_MIGRATION_OK errors=0 affected={report['affected_count']} body_hash_version={report['target_body_hash_version']} output={args.output}")
        return 0
    if args.command == "explain-receipt":
        explanation = explain_publication_receipt(payload)
        atomic_write(args.output, explanation)
        print(f"PUBLICATION_EXPLANATION_OK unresolved={','.join(explanation['unresolved']) or 'none'} output={args.output}")
        return 0
    replayed = replay_publication(payload)
    atomic_write(args.output, replayed)
    print(f"REPLAY_OK events={len(replayed['events'])} state_sha256={replayed['replay_receipt']['state_sha256'][:12]} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
