#!/usr/bin/env python3
"""Small, deterministic schema migration and V2 replay registry."""

from __future__ import annotations

import argparse
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
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGET_SCHEMA_VERSION = 3
HASH = re.compile(r"^[0-9a-f]{64}$")
OBJECT_KEYS = ("PublicEvent", "ChangeEvent", "EvidenceEnvelope")
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
    value = copy.deepcopy(item)
    if from_version == 1:
        value["source_snapshot_ref"] = _snapshot_ref(value)
        created = _fallback_timestamp(value, observed_at)
        value.setdefault("created_at", created)
        value.setdefault("updated_at", created)
        value["schema_version"] = 2
    elif from_version == 2:
        value.setdefault("derivation_version", bundle.get("semantics_version") or "legacy-v2")
        value.setdefault("parser_version", bundle.get("parser_version") or "legacy-parser-unknown")
        value["schema_version"] = 3
    else:
        raise ValueError(f"no migration registered for {from_version}")
    value[IDENTITY_KEYS[kind]] = _stable_identity(kind, value)
    return value


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
    if kind in {"PublicEvent", "EvidenceEnvelope"} and not HASH.fullmatch(item["content_sha256"]):
        raise ValueError(f"{kind} content_sha256 is invalid")


def _manual_hashes(bundle: dict[str, Any]) -> dict[str, str | None]:
    return {
        key: sha256(bundle[key]) if key in bundle else None
        for key in ("manual_state", "watch_state", "handoff_state")
    }


def migrate_bundle(bundle: dict[str, Any], *, target_version: int = TARGET_SCHEMA_VERSION) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    if not isinstance(bundle, dict) or not isinstance(bundle.get("objects"), dict):
        raise ValueError("migration input must contain an objects object")
    source_version = int(bundle.get("schema_version", 1))
    if source_version > target_version:
        raise ValueError(f"input schema_version {source_version} is newer than target {target_version}")
    observed_at = parse_timestamp(bundle.get("observed_at") or "2026-01-01T00:00:00+00:00")
    migrated = copy.deepcopy(bundle)
    migrated["schema_version"] = target_version
    errors: list[dict[str, Any]] = []
    affected = 0
    object_counts: dict[str, int] = {}
    for kind in OBJECT_KEYS:
        rows = bundle["objects"].get(kind, [])
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
    manual = {key: payload[key] for key in ("watch_state", "handoff_state") if key in payload}
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
        "manual_state_hashes": {key: sha256(value) for key, value in manual.items()},
    }
    return {"state": state, "events": event_rows, "replay_receipt": receipt, **manual}


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
    print("MIGRATION_REPLAY_SELF_CHECK_OK registry=1->2->3 lkg_safe=true first_seen_preserved=true")


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
    args = parser.parse_args(argv)
    if args.command == "self-check":
        self_check()
        return 0
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    if args.command in {"dry-run", "apply"}:
        migrated, report = migrate_bundle(payload)
        if args.command == "dry-run":
            atomic_write(args.output, report)
        elif migrated is None:
            raise ValueError(f"migration refused with {report['error_count']} errors; existing output was not changed")
        else:
            atomic_write(args.output, migrated)
            report_path = args.output.with_name(f"{args.output.stem}.migration-receipt.json")
            atomic_write(report_path, report)
        print(f"MIGRATION_{args.command.upper().replace('-', '_')}_OK errors={report['error_count']} affected={report['affected_count']} output={args.output}")
        return 0
    replayed = replay_publication(payload)
    atomic_write(args.output, replayed)
    print(f"REPLAY_OK events={len(replayed['events'])} state_sha256={replayed['replay_receipt']['state_sha256'][:12]} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
