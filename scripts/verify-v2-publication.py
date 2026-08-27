from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = ROOT / "apps" / "web" / "public" / "data" / "intelligence-feed.json"
DEFAULT_STATUS = ROOT / "apps" / "web" / "public" / "data" / "source-status.json"
DEFAULT_STATE = ROOT / "state" / "v2-shadow-state.json"
DEFAULT_BRIEF = ROOT / "apps" / "web" / "public" / "data" / "v2-daily-brief.json"
PUBLISHABLE_CHANGE_TYPES = {
    "NEW",
    "REVISED",
    "STATUS_CHANGED",
    "DEADLINE_CHANGED",
    "REMOVED",
}


def load_json(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"missing V2 publication file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise ValueError(f"V2_PUBLICATION_INVALID: {message}")


def verify(*, feed_path: Path, status_path: Path, state_path: Path, brief_path: Path) -> dict:
    feed = load_json(feed_path)
    status = load_json(status_path)
    state = load_json(state_path)
    brief = load_json(brief_path)

    if feed.get("schema_version") != 1 or not isinstance(feed.get("items"), list):
        fail("legacy feed contract is invalid")
    if state.get("schema_version") != 1 or state.get("mode") != "V2_SHADOW":
        fail("state must use schema_version=1 and mode=V2_SHADOW")
    if not state.get("baseline_established_at"):
        fail("state is missing baseline_established_at")
    if not isinstance(state.get("items"), dict):
        fail("state items must be an object keyed by stable identity")
    if brief.get("schema_version") != 1 or brief.get("mode") != "V2_SHADOW":
        fail("brief must use schema_version=1 and mode=V2_SHADOW")

    run_id = feed.get("collection_run_id")
    status_run_id = (status.get("latest_collection_run") or {}).get("collection_run_id")
    if run_id != status_run_id:
        fail(f"feed/status collection mismatch: {run_id!r} != {status_run_id!r}")
    if brief.get("source_collection_run_id") != run_id:
        fail("brief does not reference the current feed collection run")
    if brief.get("generated_at") != feed.get("generated_at"):
        fail("brief/feed generated_at mismatch")
    if brief.get("source_status_generated_at") != status.get("generated_at"):
        fail("brief/source-status generated_at mismatch")

    overview = brief.get("overview")
    if not isinstance(overview, dict):
        fail("brief overview must be an object")
    if overview.get("archive_total") != len(feed["items"]):
        fail("archive_total must equal the number of feed items")
    current_change_count = overview.get("current_change_count")
    if not isinstance(current_change_count, int) or current_change_count < 0:
        fail("current_change_count must be a non-negative integer")

    priority_items = brief.get("priority_items")
    if not isinstance(priority_items, list):
        fail("priority_items must be an array")
    if len(priority_items) > 3:
        fail("priority_items exceeds the police-user Top 3 limit")
    if overview.get("priority_count") != len(priority_items):
        fail("priority_count does not match priority_items")
    if len(priority_items) > current_change_count:
        fail("priority items cannot exceed current changes")

    seen_event_ids: set[str] = set()
    for item in priority_items:
        event_id = item.get("event_id")
        if not event_id or event_id in seen_event_ids:
            fail("priority event IDs must be present and unique")
        seen_event_ids.add(event_id)
        if item.get("change_type") not in PUBLISHABLE_CHANGE_TYPES:
            fail(f"non-publishable change type in Top 3: {item.get('change_type')!r}")
        if not str(item.get("official_url") or "").startswith("https://"):
            fail(f"priority item lacks an HTTPS official URL: {event_id}")
        if not item.get("what_changed"):
            fail(f"priority item lacks what_changed wording: {event_id}")
        if item.get("time_basis") not in {"OFFICIAL_DATE", "FIRST_SEEN", "DETECTED_CHANGE"}:
            fail(f"priority item has an invalid time basis: {event_id}")

    state_identities = set(state["items"])
    if len(state_identities) != len(state["items"]):
        fail("state contains duplicate stable identities")
    if len(state["items"]) < len(feed["items"]):
        fail("persistent state cannot contain fewer identities than the current archive")

    source_health = brief.get("source_health")
    if not isinstance(source_health, dict):
        fail("brief source_health projection is missing")
    sources = status.get("sources") or []
    if source_health.get("pass_count") != sum(
        source.get("source_health") == "PASS" for source in sources
    ):
        fail("source health pass count mismatch")
    if brief.get("publication_status") not in {"READY", "PARTIAL"}:
        fail("publication_status must be READY or PARTIAL")
    if bool(brief.get("snapshot_complete")) != (brief.get("publication_status") == "READY"):
        fail("snapshot_complete/publication_status mismatch")

    return {
        "run_id": run_id,
        "archive_total": len(feed["items"]),
        "state_total": len(state["items"]),
        "changes": current_change_count,
        "priority": len(priority_items),
        "publication_status": brief["publication_status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one persistent V2 shadow publication.")
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    args = parser.parse_args()

    result = verify(
        feed_path=args.feed,
        status_path=args.status,
        state_path=args.state,
        brief_path=args.brief,
    )
    print(
        "V2_PUBLICATION_OK "
        f"run={result['run_id']} archive={result['archive_total']} "
        f"state={result['state_total']} changes={result['changes']} "
        f"priority={result['priority']} status={result['publication_status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
