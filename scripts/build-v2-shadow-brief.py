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

from intel_v2.semantics import ChangeEvent, compare_snapshot, feed_item_to_version, shadow_brief


TZ = ZoneInfo("Asia/Taipei")
GENERATOR_VERSION = 2
DEFAULT_FEED = ROOT / "apps" / "web" / "public" / "data" / "intelligence-feed.json"
DEFAULT_STATUS = ROOT / "apps" / "web" / "public" / "data" / "source-status.json"
DEFAULT_STATE = ROOT / "state" / "v2-shadow-state.json"
DEFAULT_OUTPUT = ROOT / "apps" / "web" / "public" / "data" / "v2-daily-brief.json"

SOURCE_CONTEXT = {
    "S-004": {
        "source_name": "臺中市議會議事日程",
        "why_it_matters": "涉及會期、議程、列席與答詢準備時程。",
        "affected_roles": ["議會聯絡", "局本部幕僚"],
    },
    "S-006": {
        "source_name": "臺中市議會質詢順序表",
        "why_it_matters": "涉及質詢順序、列席主管與資料準備優先順序。",
        "affected_roles": ["議會聯絡", "列席主管", "相關業管單位"],
    },
    "S-007": {
        "source_name": "臺中市議會議事錄",
        "why_it_matters": "可能包含公開答覆、承諾事項與後續追蹤責任。",
        "affected_roles": ["議會聯絡", "原答詢業管單位"],
    },
    "S-009": {
        "source_name": "臺中市議會各項提案",
        "why_it_matters": "涉及議員提案、政策回應、預算或地方警政需求。",
        "affected_roles": ["議會聯絡", "相關業管單位", "涉及分局或大隊"],
    },
    "S-029": {
        "source_name": "臺中市政府議會專案報告",
        "why_it_matters": "涉及市府專案說明、跨機關政策與警察局公開立場。",
        "affected_roles": ["局本部幕僚", "專案報告業管單位", "相關分局或大隊"],
    },
}

CHANGE_ACTIONS = {
    "NEW": "確認業管歸屬與現行處理狀態，判斷是否需納入答詢或政策追蹤。",
    "REVISED": "比對前後版本，更新既有答詢、簡報或追蹤紀錄。",
    "STATUS_CHANGED": "確認最新官方階段與後續責任，更新追蹤狀態。",
    "DEADLINE_CHANGED": "立即核對新日期，調整列席、資料準備與內部期限。",
    "REMOVED": "確認是否為正式撤下、移轉或來源改版，保留最後版本並視需要查核。",
}

CHANGE_PRIORITY = {
    "STATUS_CHANGED": 0,
    "DEADLINE_CHANGED": 1,
    "NEW": 2,
    "REVISED": 3,
    "REMOVED": 4,
}


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


def event_projection(event: ChangeEvent, publication_tier: str) -> dict:
    context = SOURCE_CONTEXT.get(
        event.source_id,
        {
            "source_name": event.source_id,
            "why_it_matters": "涉及公開警政政策、議會或市政資訊的變更。",
            "affected_roles": ["議會聯絡", "相關業管單位"],
        },
    )
    return {
        "event_id": event.event_id,
        "source_id": event.source_id,
        "source_name": context["source_name"],
        "change_type": event.change_type,
        "headline": event.title,
        "what_changed": event.wording,
        "why_it_matters": context["why_it_matters"],
        "affected_roles": context["affected_roles"],
        "recommended_action": CHANGE_ACTIONS.get(
            event.change_type,
            "確認官方內容與業管責任，視需要更新追蹤紀錄。",
        ),
        "deadline": event.occurred_at if event.change_type == "DEADLINE_CHANGED" else None,
        "temporal_basis": event.temporal_basis,
        "date_status": event.date_status,
        "detected_at": event.detected_at,
        "changed_fields": list(event.changed_fields),
        "official_url": event.official_url,
        "verification_status": "DETERMINISTIC_PASS",
        "evidence_status": "OFFICIAL_URL_BOUND",
        "publication_tier": publication_tier,
    }


def enrich_for_police_users(brief: dict, events: list[ChangeEvent]) -> None:
    publishable = [event for event in events if event.publishable]
    publishable.sort(
        key=lambda event: (
            CHANGE_PRIORITY.get(event.change_type, 99),
            event.source_id,
            event.identity,
        )
    )
    priority_items = [event_projection(event, "TOP") for event in publishable[:3]]
    other_changes = [event_projection(event, "OTHER") for event in publishable[3:23]]

    brief["generator_version"] = GENERATOR_VERSION
    brief["audience"] = ["議會聯絡", "局本部幕僚", "業管承辦", "分局主管"]
    brief["priority_items"] = priority_items
    brief["tracking_items"] = []
    brief["other_changes"] = other_changes
    brief["overview"]["priority_count"] = len(priority_items)
    brief["overview"]["tracking_count"] = 0
    brief["overview"]["other_change_count"] = len(other_changes)


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
    enrich_for_police_users(brief, events)
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
        f"priority={brief['overview']['priority_count']} "
        f"complete={str(snapshot_complete).lower()} "
        f"quality_issues={len(brief['quality_issues'])} "
        f"state={args.state} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
