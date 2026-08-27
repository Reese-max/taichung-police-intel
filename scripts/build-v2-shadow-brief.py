from __future__ import annotations

import argparse
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

from intel_v2.semantics import compare_snapshot, feed_item_to_version, shadow_brief


TZ = ZoneInfo("Asia/Taipei")
DEFAULT_FEED = ROOT / "apps" / "web" / "public" / "data" / "intelligence-feed.json"
DEFAULT_STATUS = ROOT / "apps" / "web" / "public" / "data" / "source-status.json"
DEFAULT_STATE = ROOT / "state" / "v2-shadow-state.json"
DEFAULT_OUTPUT = ROOT / "apps" / "web" / "public" / "data" / "v2-daily-brief.json"


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


def collection_is_complete(source_status: dict | None) -> bool:
    """Return true only when every source produced a complete, non-failed observation.

    The V1 collector preserves last-known-good items for failed sources. V2 still
    treats a failed or partial collection as incomplete so it cannot advance the
    two-observation removal confirmation counter.
    """

    if not source_status:
        return True
    sources = source_status.get("sources")
    if not isinstance(sources, list) or not sources:
        return False
    return all(
        source.get("source_health") in {"PASS", "DEGRADED"}
        and source.get("window_completeness") != "PARTIAL"
        and source.get("result") != "FAILED"
        for source in sources
    )


def source_health_projection(source_status: dict | None) -> dict:
    if not source_status:
        return {
            "status": "UNKNOWN",
            "pass_count": 0,
            "stale_count": 0,
            "failed_count": 0,
            "gap_count": 0,
        }
    sources = source_status.get("sources") or []
    failed_count = sum(source.get("source_health") == "FAILED" for source in sources)
    stale_count = sum(
        source.get("freshness_status") in {"STALE", "VERY_STALE"}
        for source in sources
    )
    gap_count = sum(len(source.get("intelligence_gaps") or []) for source in sources)
    run_status = (source_status.get("latest_collection_run") or {}).get("status") or "UNKNOWN"
    return {
        "status": run_status,
        "pass_count": sum(source.get("source_health") == "PASS" for source in sources),
        "stale_count": stale_count,
        "failed_count": failed_count,
        "gap_count": gap_count,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a persistent V2 shadow brief from the current official-source feed."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--source-status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--observed-at")
    parser.add_argument("--reset-baseline", action="store_true")
    parser.add_argument("--snapshot-partial", action="store_true")
    args = parser.parse_args()

    feed = load_json(args.input)
    if feed.get("schema_version") != 1 or not isinstance(feed.get("items"), list):
        raise ValueError("legacy feed must use schema_version=1 and contain an items array")

    source_status = load_json(args.source_status) if args.source_status.exists() else None
    if source_status:
        status_run_id = (source_status.get("latest_collection_run") or {}).get("collection_run_id")
        if status_run_id != feed.get("collection_run_id"):
            raise ValueError(
                "source status and feed must belong to the same collection run: "
                f"{status_run_id!r} != {feed.get('collection_run_id')!r}"
            )

    observed_at = args.observed_at or feed.get("generated_at") or datetime.now(TZ).isoformat()
    previous_state = None
    if args.state.exists() and not args.reset_baseline:
        previous_state = load_json(args.state)
        if previous_state.get("schema_version") != 1 or previous_state.get("mode") != "V2_SHADOW":
            raise ValueError("unsupported V2 shadow state")

    current_items = [feed_item_to_version(item, observed_at) for item in feed["items"]]
    snapshot_complete = collection_is_complete(source_status) and not args.snapshot_partial
    state, events = compare_snapshot(
        previous_state,
        current_items,
        observed_at,
        snapshot_complete=snapshot_complete,
    )
    brief = shadow_brief(
        feed=feed,
        state=state,
        events=events,
        generated_at=observed_at,
    )
    brief["publication_status"] = "READY" if snapshot_complete else "PARTIAL"
    brief["snapshot_complete"] = snapshot_complete
    brief["source_health"] = source_health_projection(source_status)
    brief["source_status_generated_at"] = source_status.get("generated_at") if source_status else None

    save_json(args.state, state)
    save_json(args.output, brief)

    print(
        "V2_SHADOW_OK "
        f"baseline={brief['baseline_established_at']} "
        f"archive={brief['overview']['archive_total']} "
        f"changes={brief['overview']['current_change_count']} "
        f"complete={str(snapshot_complete).lower()} "
        f"quality_issues={len(brief['quality_issues'])} "
        f"state={args.state} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
