from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo("Asia/Taipei")
STATUS_FIXTURE = "source-seven-day-status-2026-08-14.csv"
S029_FIXTURE = "source-live-canary-s026-s029-2026-08-14.json"
P0_SOURCES = {
    "S-004": (
        "臺中市議會議事日程",
        "https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=49",
    ),
    "S-006": (
        "臺中市議會質詢順序表",
        "https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=50",
    ),
    "S-007": (
        "臺中市議會議事資訊系統－議事錄",
        "https://yishi.tccc.gov.tw/api/ProceedingsBackWeb/FrontList",
    ),
    "S-009": (
        "臺中市議會議事資訊系統－各項提案",
        "https://yishi.tccc.gov.tw/api/Proposal/FrontList",
    ),
    "S-029": (
        "臺中市政府議會專案報告",
        "https://www.rdec.taichung.gov.tw/12047/12142/12145",
    ),
}


def canonical_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def fixture_window(start: str, end: str) -> tuple[str, str]:
    start_at = datetime.combine(date.fromisoformat(start), time.min, TZ)
    end_at = datetime.combine(date.fromisoformat(end) + timedelta(days=1), time.min, TZ)
    return timestamp(start_at), timestamp(end_at)


def load_fixtures(root: Path = ROOT) -> dict[str, dict]:
    with (root / STATUS_FIXTURE).open(encoding="utf-8-sig", newline="") as handle:
        rows = {row["source_id"]: row for row in csv.DictReader(handle)}

    fixtures: dict[str, dict] = {}
    for source_id in ("S-004", "S-006", "S-007", "S-009"):
        row = rows[source_id]
        name, source_url = P0_SOURCES[source_id]
        completeness = row["window_completeness"]
        if completeness == "UNVERIFIED_DATE":
            completeness = "PARTIAL"
        manifest = {
            "source_id": source_id,
            "source_url": source_url,
            "capture_time": row["audit_cutoff"],
            "source_health": row["source_health"],
            "window_completeness": completeness,
            "window_start": row["window_start"],
            "window_end": row["window_end"],
            "window_item_count": int(row["item_count"]),
            "evidence_note": row["evidence_note"],
        }
        window_start, window_end = fixture_window(row["window_start"], row["window_end"])
        fixtures[source_id] = {
            **manifest,
            "source_name": name,
            "window_start": window_start,
            "window_end": window_end,
            "manifest_sha256": canonical_sha256(manifest),
            "snapshot_ref": f"{STATUS_FIXTURE}#{source_id}",
            "snapshot_item_count": int(row["item_count"]),
            "tracked_items": [],
        }

    canary = json.loads((root / S029_FIXTURE).read_text(encoding="utf-8"))
    source = canary["sources"]["S-029"]
    name, source_url = P0_SOURCES["S-029"]
    window_start, window_end = fixture_window(canary["window_start"], canary["window_end"])
    fixtures["S-029"] = {
        "source_id": "S-029",
        "source_name": name,
        "source_url": source_url,
        "capture_time": canary["checked_at"],
        "source_health": source["source_health"],
        "window_completeness": source["window_completeness"],
        "window_start": window_start,
        "window_end": window_end,
        "window_item_count": len(source["window_items"]),
        "manifest_sha256": source["manifest_sha256"],
        "snapshot_ref": S029_FIXTURE,
        "snapshot_item_count": source["latest_session"]["parsed_count"],
        "tracked_items": [
            {
                "stable_key": item["item_id"],
                "title": item["title"],
                "source_url": item["url"],
                "published_at": item["published_at"],
                "content_sha256": item["sha256"],
            }
            for item in source["police_attachments"]
        ],
    }
    return fixtures


def result_for(fixture: dict) -> str:
    completeness = fixture["window_completeness"]
    item_count = fixture["window_item_count"]
    if completeness == "PARTIAL":
        return "PARTIAL"
    if completeness == "COMPLETE_ZERO" and item_count == 0:
        return "NO_NEW_ITEM"
    if completeness == "COMPLETE_WITH_ITEMS" and item_count > 0:
        return "NEW_ITEMS"
    raise ValueError(f"inconsistent fixture window: {fixture['source_id']}")


def load_state(path: Path) -> dict:
    if not path.exists():
        return {
            "schema_version": 1,
            "mode": "FIXTURE",
            "collection_runs": {},
            "source_runs": {},
            "current_items": {},
            "last_known_good": {},
            "source_status": {},
        }
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("schema_version") != 1 or state.get("mode") != "FIXTURE":
        raise ValueError("unsupported or non-fixture state file")
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    ) as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = Path(handle.name)
    os.replace(temporary, path)


def scheduled_time(slot_date: date, slot: str) -> datetime:
    clock = time(6, 30) if slot == "MORNING" else time(18, 30)
    return datetime.combine(slot_date, clock, TZ)


def next_update(slot_date: date, slot: str) -> str:
    if slot == "MORNING":
        return timestamp(datetime.combine(slot_date, time(18, 30), TZ))
    return timestamp(datetime.combine(slot_date + timedelta(days=1), time(6, 30), TZ))


def freshness_status(data_as_of: str | None, now: datetime) -> str:
    if not data_as_of:
        return "NO_DATA"
    observed = datetime.fromisoformat(data_as_of)
    age_hours = (now - observed.astimezone(TZ)).total_seconds() / 3600
    if age_hours <= 13:
        return "FRESH"
    if age_hours <= 24:
        return "STALE"
    return "VERY_STALE"


def gap_reasons(source_run: dict, lkg: dict | None, freshness: str) -> list[str]:
    reasons = []
    if source_run["source_health"] in {"FAILED", "QUARANTINED"}:
        reasons.append("SOURCE_FAILED")
    if source_run["window_completeness"] == "PARTIAL":
        reasons.append("WINDOW_PARTIAL")
    if not lkg:
        reasons.append("NO_LAST_KNOWN_GOOD")
    if freshness in {"STALE", "VERY_STALE"}:
        reasons.append(f"{freshness}_DATA")
    return reasons


def keep_fixture_items(state: dict, source_id: str, fixture: dict, observed_at: str) -> None:
    items = state["current_items"]
    for item in fixture["tracked_items"]:
        key = f"{source_id}:{item['stable_key']}"
        current = items.get(key)
        if current and current["content_sha256"] != item["content_sha256"]:
            raise ValueError(f"fixed fixture changed for {key}")
    for item in fixture["tracked_items"]:
        key = f"{source_id}:{item['stable_key']}"
        items.setdefault(
            key,
            {
                **item,
                "source_id": source_id,
                "version_no": 1,
                "first_observed_at": observed_at,
            },
        )


def run_slot(
    slot: str,
    slot_date: date,
    state_path: Path,
    *,
    now: datetime | None = None,
    root: Path = ROOT,
    broken_source: str | None = None,
) -> tuple[dict, bool, str]:
    slot = slot.upper()
    if slot not in {"MORNING", "EVENING"}:
        raise ValueError("slot must be morning or evening")
    if broken_source and broken_source not in P0_SOURCES:
        raise ValueError("broken_source must be a P0 source")

    state = load_state(state_path)
    collection_run_id = f"CR-{slot_date:%Y%m%d}-{slot}"
    if collection_run_id in state["collection_runs"]:
        return state, True, collection_run_id

    now = (now or datetime.now(TZ)).astimezone(TZ)
    scheduled_for = scheduled_time(slot_date, slot)
    if now < scheduled_for:
        raise ValueError(f"{slot} slot is not due until {timestamp(scheduled_for)}")

    fixtures = load_fixtures(root)
    observed_at = timestamp(now)
    run_records = []
    for source_id in P0_SOURCES:
        fixture = fixtures[source_id]
        source_run_id = f"SR-{slot_date:%Y%m%d}-{slot}-{source_id[2:]}"
        prior_lkg = state["last_known_good"].get(source_id)
        common = {
            "record_type": "source_run",
            "source_run_id": source_run_id,
            "collection_run_id": collection_run_id,
            "source_id": source_id,
            "attempted_at": observed_at,
            "completed_at": observed_at,
            "window_start": fixture["window_start"],
            "window_end": fixture["window_end"],
            "previous_successful_source_run_id": (
                prior_lkg["source_run_id"] if prior_lkg else None
            ),
        }
        try:
            if source_id == broken_source:
                raise RuntimeError("deliberate fixture failure")
            result = result_for(fixture)
            keep_fixture_items(state, source_id, fixture, observed_at)
            source_run = {
                **common,
                "source_health": fixture["source_health"],
                "window_completeness": fixture["window_completeness"],
                "result": result,
                "item_count": fixture["window_item_count"],
                "change_count": 0,
                "manifest_sha256": fixture["manifest_sha256"],
                "error_code": None,
                "error_message": None,
            }
            if source_run["result"] in {"NEW_ITEMS", "NO_NEW_ITEM", "PARTIAL"}:
                state["last_known_good"][source_id] = {
                    "source_run_id": source_run_id,
                    "source_url": fixture["source_url"],
                    "data_as_of": fixture["capture_time"],
                    "last_success_at": observed_at,
                    "manifest_sha256": fixture["manifest_sha256"],
                    "snapshot_ref": fixture["snapshot_ref"],
                    "snapshot_item_count": fixture["snapshot_item_count"],
                }
        except Exception as error:
            source_run = {
                **common,
                "source_health": "FAILED",
                "window_completeness": "PARTIAL",
                "result": "FAILED",
                "item_count": None,
                "change_count": None,
                "manifest_sha256": None,
                "error_code": "FIXTURE_COLLECTION_FAILED",
                "error_message": str(error),
            }

        state["source_runs"][source_run_id] = source_run
        lkg = state["last_known_good"].get(source_id)
        data_as_of = lkg["data_as_of"] if lkg else fixture["capture_time"]
        freshness = freshness_status(data_as_of, now)
        state["source_status"][source_id] = {
            "source_id": source_id,
            "source_name": fixture["source_name"],
            "source_url": fixture["source_url"],
            "current_source_run_id": source_run_id,
            "current_source_health": source_run["source_health"],
            "window_completeness": source_run["window_completeness"],
            "result": source_run["result"],
            "freshness_status": freshness,
            "data_as_of": data_as_of,
            "last_checked_at": observed_at,
            "next_update_at": next_update(slot_date, slot),
            "last_known_good": lkg,
            "intelligence_gaps": gap_reasons(source_run, lkg, freshness),
        }
        run_records.append(source_run)

    failed = sum(record["result"] == "FAILED" for record in run_records)
    partial = sum(record["result"] == "PARTIAL" for record in run_records)
    status = "FAILED" if failed == len(run_records) else "PARTIAL" if failed or partial else "SUCCEEDED"
    state["collection_runs"][collection_run_id] = {
        "record_type": "collection_run",
        "collection_run_id": collection_run_id,
        "slot_date": slot_date.isoformat(),
        "slot": slot,
        "timezone": "Asia/Taipei",
        "scheduled_for": timestamp(scheduled_for),
        "started_at": observed_at,
        "finished_at": observed_at,
        "status": status,
    }
    save_state(state_path, state)
    return state, False, collection_run_id


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one idempotent D2 collection slot.")
    parser.add_argument("--slot", required=True, choices=("morning", "evening"))
    parser.add_argument("--slot-date", type=date.fromisoformat, default=datetime.now(TZ).date())
    parser.add_argument("--state-file", type=Path, default=ROOT / ".runtime" / "fixture-state.json")
    parser.add_argument(
        "--fixtures",
        action="store_true",
        required=True,
        help="Required safety flag: this runner uses checked-in fixtures, not live collectors.",
    )
    args = parser.parse_args()
    state, replayed, run_id = run_slot(args.slot, args.slot_date, args.state_file)
    collection_run = state["collection_runs"][run_id]
    print(
        json.dumps(
            {
                "mode": state["mode"],
                "replayed": replayed,
                "collection_run_id": run_id,
                "status": collection_run["status"],
                "source_results": {
                    source_id: state["source_status"][source_id]["result"]
                    for source_id in P0_SOURCES
                },
                "state_file": str(args.state_file.resolve()),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
