#!/usr/bin/env python3
"""Local-first watch and versioned handoff state for the V2 publication."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.handoff import (
    add_watch,
    confirm_handoff,
    find_handoff,
    handoff_markdown,
    load_state,
    set_watch_status,
)


TZ = ZoneInfo("Asia/Taipei")
DEFAULT_HANDOFF_STATE = ROOT / "state" / "v2-handoff-state.json"
DEFAULT_SOURCE_STATE = ROOT / "state" / "v2-shadow-state.json"
DEFAULT_FEED = ROOT / "apps" / "web" / "public" / "data" / "intelligence-feed.json"
DEFAULT_BRIEF = ROOT / "apps" / "web" / "public" / "data" / "v2-daily-brief.json"


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent,
                                     prefix=f".{path.name}.", suffix=".tmp") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def source_items(path: Path) -> dict:
    value = load_json(path)
    if value.get("schema_version") != 1 or not isinstance(value.get("items"), dict):
        raise ValueError("source state must be a V2 shadow state")
    return value["items"]


def publication(feed_path: Path, brief_path: Path) -> dict:
    feed = load_json(feed_path)
    brief = load_json(brief_path)
    raw_brief = brief_path.read_bytes()
    return {
        "collection_run_id": feed.get("collection_run_id"),
        "brief_sha256": hashlib.sha256(raw_brief).hexdigest(),
        "source_status_generated_at": brief.get("source_status_generated_at"),
    }


def command_watch(args: argparse.Namespace) -> int:
    state = load_state(args.handoff_state)
    items = source_items(args.source_state)
    item = items.get(args.identity)
    if item is None:
        raise ValueError(f"unknown source identity: {args.identity}")
    state, watch, created = add_watch(
        state,
        item,
        created_at=args.at or now(),
        reason_code=args.reason_code,
    )
    save_json(args.handoff_state, state)
    print(f"WATCH_{'CREATED' if created else 'EXISTS'} watch_id={watch['watch_id']} identity={watch['identity']}")
    return 0


def command_confirm(args: argparse.Namespace) -> int:
    state = load_state(args.handoff_state)
    current_items = source_items(args.source_state)
    brief = load_json(args.brief)
    updated, handoff = confirm_handoff(
        state,
        current_items=current_items,
        publication=publication(args.feed, args.brief),
        generated_at=args.generated_at or brief.get("generated_at") or now(),
        confirmed_at=args.confirmed_at or now(),
        watch_ids=args.watch_id,
    )
    save_json(args.handoff_state, updated)
    print(f"HANDOFF_CONFIRMED brief_id={handoff['brief_id']} version={handoff['brief_version']} items={len(handoff['items'])}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = load_state(args.handoff_state)
    active = [watch for watch in state["watch_items"].values() if watch["status"] in {"WATCHING", "NEEDS_REVIEW"}]
    if args.json:
        print(json.dumps({"watch_items": active, "handoffs": state["handoffs"]}, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"HANDOFF_STATUS active={len(active)} handoffs={len(state['handoffs'])}")
        for watch in sorted(active, key=lambda value: value["watch_id"]):
            print(f"{watch['watch_id']} status={watch['status']} identity={watch['identity']}")
    return 0


def command_set_status(args: argparse.Namespace) -> int:
    state = load_state(args.handoff_state)
    updated = set_watch_status(
        state,
        args.watch_id,
        args.status,
        updated_at=args.at or now(),
    )
    save_json(args.handoff_state, updated)
    print(f"WATCH_STATUS_UPDATED watch_id={args.watch_id} status={args.status}")
    return 0


def command_export(args: argparse.Namespace) -> int:
    state = load_state(args.handoff_state)
    handoff = find_handoff(state, args.brief_id)
    if args.format == "json":
        output = json.dumps(handoff, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    else:
        output = handoff_markdown(handoff)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"HANDOFF_EXPORTED brief_id={handoff['brief_id']} output={args.output}")
    else:
        print(output, end="")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Manage local-first GovIntel watch and handoff state.")
    result.add_argument("--handoff-state", type=Path, default=DEFAULT_HANDOFF_STATE)
    result.add_argument("--source-state", type=Path, default=DEFAULT_SOURCE_STATE)
    result.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    result.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    commands = result.add_subparsers(dest="command", required=True)

    watch = commands.add_parser("watch")
    watch.add_argument("--identity", required=True)
    watch.add_argument("--reason-code", default="MANUAL")
    watch.add_argument("--at")
    watch.set_defaults(handler=command_watch)

    confirm = commands.add_parser("confirm")
    confirm.add_argument("--watch-id", action="append")
    confirm.add_argument("--generated-at")
    confirm.add_argument("--confirmed-at")
    confirm.set_defaults(handler=command_confirm)

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=command_status)

    for name, value in (("resolve", "RESOLVED"), ("dismiss", "DISMISSED")):
        command = commands.add_parser(name)
        command.add_argument("--watch-id", required=True)
        command.add_argument("--at")
        command.set_defaults(handler=command_set_status, status=value)

    export = commands.add_parser("export")
    export.add_argument("--brief-id")
    export.add_argument("--format", choices=("markdown", "json"), default="markdown")
    export.add_argument("--output", type=Path)
    export.set_defaults(handler=command_export)
    return result


def main() -> int:
    args = parser().parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
