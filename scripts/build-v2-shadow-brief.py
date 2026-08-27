from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from intel_v2.semantics import compare_snapshot, feed_item_to_version, shadow_brief


ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Asia/Taipei")
DEFAULT_FEED = ROOT / "apps" / "web" / "public" / "data" / "intelligence-feed.json"
DEFAULT_STATE = ROOT / ".runtime" / "v2-shadow-state.json"
DEFAULT_OUTPUT = ROOT / ".runtime" / "v2-shadow-brief.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a non-production V2 brief from the current legacy feed."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--observed-at")
    parser.add_argument("--reset-baseline", action="store_true")
    parser.add_argument("--snapshot-partial", action="store_true")
    args = parser.parse_args()

    feed = load_json(args.input)
    if feed.get("schema_version") != 1 or not isinstance(feed.get("items"), list):
        raise ValueError("legacy feed must use schema_version=1 and contain an items array")

    observed_at = args.observed_at or feed.get("generated_at") or datetime.now(TZ).isoformat()
    previous_state = None
    if args.state.exists() and not args.reset_baseline:
        previous_state = load_json(args.state)
        if previous_state.get("schema_version") != 1 or previous_state.get("mode") != "V2_SHADOW":
            raise ValueError("unsupported V2 shadow state")

    current_items = [feed_item_to_version(item, observed_at) for item in feed["items"]]
    state, events = compare_snapshot(
        previous_state,
        current_items,
        observed_at,
        snapshot_complete=not args.snapshot_partial,
    )
    brief = shadow_brief(
        feed=feed,
        state=state,
        events=events,
        generated_at=observed_at,
    )

    save_json(args.state, state)
    save_json(args.output, brief)

    print(
        "V2_SHADOW_OK "
        f"baseline={brief['baseline_established_at']} "
        f"archive={brief['overview']['archive_total']} "
        f"changes={brief['overview']['current_change_count']} "
        f"quality_issues={len(brief['quality_issues'])} "
        f"output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
