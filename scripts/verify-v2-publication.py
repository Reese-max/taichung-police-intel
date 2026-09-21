from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.handoff import ACTIVE_WATCH_STATUSES, validate_state as validate_handoff_state

DEFAULT_FEED = ROOT / "apps" / "web" / "public" / "data" / "intelligence-feed.json"
DEFAULT_STATUS = ROOT / "apps" / "web" / "public" / "data" / "source-status.json"
DEFAULT_STATE = ROOT / "state" / "v2-shadow-state.json"
DEFAULT_HANDOFF_STATE = ROOT / "state" / "v2-handoff-state.json"
DEFAULT_BRIEF = ROOT / "apps" / "web" / "public" / "data" / "v2-daily-brief.json"
PUBLISHABLE_CHANGE_TYPES = {
    "NEW",
    "REVISED",
    "STATUS_CHANGED",
    "DEADLINE_CHANGED",
    "REMOVED",
}
TEMPORAL_BASES = {"OFFICIAL_DATE", "FIRST_SEEN", "DETECTED_CHANGE"}
CANONICAL_EVENT_FIELDS = (
    "event_id",
    "identity",
    "watch_id",
    "stable_key",
    "source_id",
    "source_name",
    "change_type",
    "headline",
    "what_changed",
    "why_it_matters",
    "affected_roles",
    "recommended_action",
    "deadline",
    "temporal_basis",
    "date_status",
    "detected_at",
    "changed_fields",
    "source_version",
    "source_sha256",
    "source_document_version",
    "official_url",
    "verification_status",
    "evidence_status",
)


def load_json(path: Path) -> dict:
    if not path.exists():
        raise ValueError(f"missing V2 publication file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def fail(message: str) -> None:
    raise ValueError(f"V2_PUBLICATION_INVALID: {message}")


def validate_profile_metadata(profile: dict, *, label: str = "profile") -> None:
    if not isinstance(profile, dict):
        fail(f"{label} must be an object")
    if not isinstance(profile.get("profile_id"), str) or not profile["profile_id"]:
        fail(f"{label} is missing profile_id")
    if not isinstance(profile.get("profile_version"), int) or profile["profile_version"] < 1:
        fail(f"{label} has an invalid profile_version")
    if not isinstance(profile.get("profile_hash"), str) or len(profile["profile_hash"]) != 64:
        fail(f"{label} has an invalid profile_hash")
    if not isinstance(profile.get("ranking_policy_version"), str) or not profile["ranking_policy_version"]:
        fail(f"{label} is missing ranking_policy_version")
    if not isinstance(profile.get("label"), str) or not profile["label"].strip():
        fail(f"{label} is missing label")


def validate_profile_relevance(item: dict, profile: dict, *, label: str) -> None:
    relevance = item.get("profile_relevance")
    if not isinstance(relevance, dict):
        fail(f"{label} is missing profile_relevance")
    for key in ("profile_id", "profile_version", "profile_hash", "ranking_policy_version"):
        if relevance.get(key) != profile[key]:
            fail(f"{label} profile metadata mismatch: {key}")
    if not isinstance(relevance.get("reason_codes"), list) or not relevance["reason_codes"]:
        fail(f"{label} must expose deterministic reason_codes")


def validate_action_item(
    item: dict,
    seen_event_ids: set[str],
    expected_tier: str,
    expected_profile: dict | None = None,
) -> None:
    event_id = item.get("event_id")
    if not event_id or event_id in seen_event_ids:
        fail("published event IDs must be present and unique")
    seen_event_ids.add(event_id)
    if item.get("change_type") not in PUBLISHABLE_CHANGE_TYPES:
        fail(f"non-publishable change type in daily intelligence: {item.get('change_type')!r}")
    if not str(item.get("official_url") or "").startswith("https://"):
        fail(f"published item lacks an HTTPS official URL: {event_id}")
    if not item.get("source_id") or not item.get("source_name"):
        fail(f"published item lacks source identity: {event_id}")
    for field in ("headline", "what_changed", "why_it_matters", "recommended_action"):
        if not str(item.get(field) or "").strip():
            fail(f"published item lacks {field}: {event_id}")
    affected_roles = item.get("affected_roles")
    if not isinstance(affected_roles, list) or not affected_roles:
        fail(f"published item lacks affected_roles: {event_id}")
    if item.get("temporal_basis") not in TEMPORAL_BASES:
        fail(f"published item has an invalid temporal basis: {event_id}")
    if item.get("verification_status") != "DETERMINISTIC_PASS":
        fail(f"published item was not deterministically verified: {event_id}")
    if item.get("evidence_status") != "OFFICIAL_URL_BOUND":
        fail(f"published item lacks official evidence binding: {event_id}")
    if item.get("publication_tier") != expected_tier:
        fail(f"published item tier mismatch: {event_id}")
    if expected_profile:
        validate_profile_relevance(item, expected_profile, label=f"published item {event_id}")


def validate_profile_view_consistency(profile_views: list[dict]) -> None:
    """Profile views may reorder/select events, but cannot rewrite event truth."""
    canonical_by_event: dict[str, str] = {}
    for view in profile_views:
        for key in ("priority_items", "other_changes"):
            for item in view[key]:
                event_id = item["event_id"]
                canonical = json.dumps(
                    {field: item.get(field) for field in CANONICAL_EVENT_FIELDS},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                previous = canonical_by_event.get(event_id)
                if previous is not None and previous != canonical:
                    fail(f"canonical event differs across profile views: {event_id}")
                canonical_by_event[event_id] = canonical


def verify(
    *,
    feed_path: Path,
    status_path: Path,
    state_path: Path,
    handoff_state_path: Path,
    brief_path: Path,
) -> dict:
    feed = load_json(feed_path)
    status = load_json(status_path)
    state = load_json(state_path)
    handoff_state = load_json(handoff_state_path)
    brief = load_json(brief_path)

    if feed.get("schema_version") != 1 or not isinstance(feed.get("items"), list):
        fail("legacy feed contract is invalid")
    if state.get("schema_version") != 1 or state.get("mode") != "V2_SHADOW":
        fail("state must use schema_version=1 and mode=V2_SHADOW")
    try:
        validate_handoff_state(handoff_state)
    except ValueError as error:
        fail(str(error))
    if not state.get("baseline_established_at"):
        fail("state is missing baseline_established_at")
    if not isinstance(state.get("items"), dict):
        fail("state items must be an object keyed by stable identity")
    if brief.get("schema_version") != 1 or brief.get("mode") != "V2_SHADOW":
        fail("brief must use schema_version=1 and mode=V2_SHADOW")
    if brief.get("generator_version") != 2:
        fail("brief must use police-user generator_version=2")
    if not isinstance(brief.get("audience"), list) or not brief["audience"]:
        fail("brief must declare its police-user audience")
    profile = brief.get("profile")
    validate_profile_metadata(profile)
    profile_views = brief.get("profile_views")
    if not isinstance(profile_views, list) or not profile_views:
        fail("brief must include at least one profile view")
    view_by_id = {}
    for view in profile_views:
        if not isinstance(view, dict):
            fail("profile view must be an object")
        view_profile = view.get("profile")
        validate_profile_metadata(view_profile, label="profile view")
        profile_id = view_profile["profile_id"]
        if profile_id in view_by_id:
            fail(f"duplicate profile view: {profile_id}")
        view_by_id[profile_id] = view
        view_priority = view.get("priority_items")
        view_tracking = view.get("tracking_items")
        view_other = view.get("other_changes")
        if not isinstance(view_priority, list) or not isinstance(view_tracking, list) or not isinstance(view_other, list):
            fail(f"profile view arrays are invalid: {profile_id}")
        if len(view_priority) > 3 or len(view_tracking) > 5:
            fail(f"profile view caps are invalid: {profile_id}")
        seen_view_events: set[str] = set()
        for item in view_priority:
            validate_action_item(item, seen_view_events, "TOP", view_profile)
        for item in view_other:
            validate_action_item(item, seen_view_events, "OTHER", view_profile)
        for item in view_tracking:
            validate_profile_relevance(item, view_profile, label=f"profile tracking item {profile_id}")
    validate_profile_view_consistency(profile_views)
    if profile["profile_id"] not in view_by_id:
        fail("selected profile is missing from profile_views")
    selected_view = view_by_id[profile["profile_id"]]
    if brief.get("priority_items") != selected_view["priority_items"]:
        fail("brief priority_items do not match selected profile view")
    if brief.get("tracking_items") != selected_view["tracking_items"]:
        fail("brief tracking_items do not match selected profile view")
    if brief.get("other_changes") != selected_view["other_changes"]:
        fail("brief other_changes do not match selected profile view")

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
    tracking_items = brief.get("tracking_items")
    other_changes = brief.get("other_changes")
    if not isinstance(priority_items, list) or not isinstance(tracking_items, list) or not isinstance(other_changes, list):
        fail("priority_items, tracking_items and other_changes must be arrays")
    if len(priority_items) > 3:
        fail("priority_items exceeds the police-user Top 3 limit")
    if len(tracking_items) > 5:
        fail("tracking_items exceeds the five-item limit")
    if overview.get("priority_count") != len(priority_items):
        fail("priority_count does not match priority_items")
    if overview.get("tracking_count") != len(tracking_items):
        fail("tracking_count does not match tracking_items")
    active_watch_ids = {
        watch_id
        for watch_id, watch in handoff_state["watch_items"].items()
        if watch.get("status") in ACTIVE_WATCH_STATUSES
    }
    tracking_watch_ids = {item.get("watch_id") for item in tracking_items}
    if None in tracking_watch_ids or not tracking_watch_ids <= active_watch_ids:
        fail("tracking_items must project active persistent watch items")
    if overview.get("tracking_total") != len(active_watch_ids):
        fail("tracking_total does not match persistent active watch items")
    if overview.get("other_change_count") != len(other_changes):
        fail("other_change_count does not match other_changes")
    if len(priority_items) + len(other_changes) > current_change_count:
        fail("displayed current changes cannot exceed current_change_count")

    seen_event_ids: set[str] = set()
    for item in priority_items:
        validate_action_item(item, seen_event_ids, "TOP", profile)
    for item in other_changes:
        validate_action_item(item, seen_event_ids, "OTHER", profile)
    for item in tracking_items:
        validate_profile_relevance(item, profile, label="tracking item")

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
        "tracking": len(tracking_items),
        "tracking_total": len(active_watch_ids),
        "other": len(other_changes),
        "publication_status": brief["publication_status"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one persistent V2 shadow publication.")
    parser.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--handoff-state", type=Path, default=DEFAULT_HANDOFF_STATE)
    parser.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    args = parser.parse_args()

    result = verify(
        feed_path=args.feed,
        status_path=args.status,
        state_path=args.state,
        handoff_state_path=args.handoff_state,
        brief_path=args.brief,
    )
    print(
        "V2_PUBLICATION_OK "
        f"run={result['run_id']} archive={result['archive_total']} "
        f"state={result['state_total']} changes={result['changes']} "
        f"priority={result['priority']} tracking={result['tracking_total']} other={result['other']} "
        f"status={result['publication_status']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
