#!/usr/bin/env python3
"""Manage an explicit, bounded public live-meeting session without auto-starting ASR."""

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

from intel_v2.live_meeting import (
    activate_session,
    add_bookmark,
    append_segment,
    budget_stop,
    formal_candidates,
    reconcile_session,
    record_gap,
    resume_session,
    start_session,
    stop_session,
    validate_session,
)


DEFAULT_STATE = ROOT / "state" / "live-meeting-session.json"
TZ = ZoneInfo("Asia/Taipei")


def now() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    return validate_session(value)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(validate_session(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temporary, path)


def command_start(args: argparse.Namespace) -> int:
    state = start_session(
        source_id=args.source_id,
        official_source_url=args.official_source_url,
        stream_url=args.stream_url,
        started_at=args.at or now(),
        meeting_id=args.meeting_id,
        agenda_id=args.agenda_id,
        max_duration_seconds=args.max_duration,
        asr_contract={"provider": args.provider, "model": args.model, "version": args.asr_version, "language": args.language},
        transport_enabled=args.enable_transport,
        provider_authorized=args.provider_authorized,
        budget_seconds=args.budget_seconds,
        session_id=args.session_id,
    )
    write(args.state, state)
    print(f"LIVE_MEETING_STARTED session_id={state['session_id']} status={state['status']} transport={state['transport_status']}")
    return 0


def command_activate(args: argparse.Namespace) -> int:
    state = activate_session(read(args.state), activated_at=args.at or now(), provider_authorized=args.provider_authorized, budget_seconds=args.budget_seconds)
    write(args.state, state)
    print(f"LIVE_MEETING_ACTIVATED session_id={state['session_id']} status={state['status']}")
    return 0


def command_append(args: argparse.Namespace) -> int:
    state = append_segment(read(args.state), sequence=args.sequence, start_seconds=args.start, end_seconds=args.end, text=args.text, received_at=args.at or now(), finalized=args.finalized, partial=args.partial, confidence=args.confidence, speaker_label=args.speaker_label)
    write(args.state, state)
    revision = next(item["revision"] for item in state["segments"] if item["sequence"] == args.sequence)
    print(f"LIVE_SEGMENT_APPENDED session_id={state['session_id']} sequence={args.sequence} revision={revision}")
    return 0


def command_gap(args: argparse.Namespace) -> int:
    state = record_gap(read(args.state), start_seconds=args.start, end_seconds=args.end, reason=args.reason, detected_at=args.at or now())
    write(args.state, state)
    print(f"LIVE_GAP_RECORDED session_id={state['session_id']} status={state['status']}")
    return 0


def command_resume(args: argparse.Namespace) -> int:
    state = resume_session(read(args.state), resumed_at=args.at or now())
    write(args.state, state)
    print(f"LIVE_MEETING_RESUMED session_id={state['session_id']} status={state['status']}")
    return 0


def command_stop(args: argparse.Namespace) -> int:
    state = stop_session(read(args.state), ended_at=args.at or now(), reason=args.reason)
    write(args.state, state)
    print(f"LIVE_MEETING_STOPPED session_id={state['session_id']} status={state['status']}")
    return 0


def command_budget_stop(args: argparse.Namespace) -> int:
    state = budget_stop(read(args.state), stopped_at=args.at or now())
    write(args.state, state)
    print(f"LIVE_MEETING_BUDGET_STOP session_id={state['session_id']} status={state['status']}")
    return 0


def command_bookmark(args: argparse.Namespace) -> int:
    state = add_bookmark(read(args.state), segment_ids=args.segment_id, reason_code=args.reason_code, bookmarked_at=args.at or now(), note=args.note)
    write(args.state, state)
    print(f"LIVE_BOOKMARK_SAVED session_id={state['session_id']} bookmarks={len(state['bookmarks'])}")
    return 0


def command_reconcile(args: argparse.Namespace) -> int:
    candidates = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(candidates, list):
        raise ValueError("reconciliation input must be an array")
    state = reconcile_session(read(args.state), candidates, reconciled_at=args.at or now())
    write(args.state, state)
    print(f"LIVE_RECONCILIATION_OK session_id={state['session_id']} status={state['status']} receipts={len(state['reconciliation_receipts'])}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = read(args.state)
    if args.json:
        print(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"LIVE_MEETING_STATUS session_id={state['session_id']} status={state['status']} segments={len(state['segments'])} gaps={len(state['gap_intervals'])} bookmarks={len(state['bookmarks'])} receipts={len(state['reconciliation_receipts'])}")
    return 0


def command_formal(args: argparse.Namespace) -> int:
    print(json.dumps(formal_candidates(read(args.state)), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def self_check() -> None:
    state = start_session(
        source_id="S-010",
        official_source_url="https://www.tccc.gov.tw/",
        stream_url="https://vod.tccc.gov.tw/live/self-check.m3u8",
        started_at="2026-09-21T10:00:00+08:00",
        max_duration_seconds=60,
        asr_contract={"provider": "fixture", "model": "provisional", "version": "v1", "language": "zh"},
        transport_enabled=True,
        provider_authorized=True,
        budget_seconds=60,
        session_id="LM-SELF-CHECK",
    )
    state = append_segment(
        state,
        sequence=0,
        start_seconds=0,
        end_seconds=5,
        text="交通議題暫定文字",
        received_at="2026-09-21T10:00:01+08:00",
        partial=True,
    )
    state = append_segment(
        state,
        sequence=0,
        start_seconds=0,
        end_seconds=5,
        text="交通議題正式暫定文字",
        received_at="2026-09-21T10:00:02+08:00",
        finalized=True,
    )
    state = record_gap(
        state,
        start_seconds=5,
        end_seconds=8,
        reason="ASR_DISCONNECT",
        detected_at="2026-09-21T10:00:03+08:00",
    )
    state = resume_session(state, resumed_at="2026-09-21T10:00:04+08:00")
    state = add_bookmark(
        state,
        segment_ids=[state["segments"][0]["segment_id"]],
        reason_code="REVIEW",
        bookmarked_at="2026-09-21T10:00:05+08:00",
    )
    state = stop_session(state, ended_at="2026-09-21T10:00:06+08:00")
    state = reconcile_session(
        state,
        [{
            "candidate_id": state["bookmarks"][0]["bookmark_id"],
            "outcome": "CONFIRMED_BY_OFFICIAL_MEDIA",
            "official_source_id": "S-010",
            "official_url": "https://vod.tccc.gov.tw/wb_news02.asp?url=self-check",
            "official_locator": "timestamp:0-5",
            "official_document_version": "SELF-CHECK-V1",
            "official_text_sha256": "a" * 64,
        }],
        reconciled_at="2026-09-21T10:00:07+08:00",
    )
    assert state["status"] == "RECONCILED"
    assert len(state["segments"][0]["revision_history"]) == 1
    assert len(state["gap_intervals"]) == 1
    assert formal_candidates(state)[0]["provisional"] is False
    print("LIVE_MEETING_SELF_CHECK_OK revision=true gap=true bookmark=true official_reconciliation=true")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = result.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start")
    start.add_argument("--source-id", required=True)
    start.add_argument("--official-source-url", required=True)
    start.add_argument("--stream-url", required=True)
    start.add_argument("--provider", default="fixture")
    start.add_argument("--model", default="provisional")
    start.add_argument("--asr-version", default="v1")
    start.add_argument("--language", default="zh")
    start.add_argument("--meeting-id")
    start.add_argument("--agenda-id")
    start.add_argument("--session-id")
    start.add_argument("--max-duration", type=int, default=1800)
    start.add_argument("--budget-seconds", type=int)
    start.add_argument("--enable-transport", action="store_true")
    start.add_argument("--provider-authorized", action="store_true")
    start.add_argument("--at")
    start.set_defaults(handler=command_start)

    activate = commands.add_parser("activate")
    activate.add_argument("--budget-seconds", type=int, required=True)
    activate.add_argument("--provider-authorized", action="store_true")
    activate.add_argument("--at")
    activate.set_defaults(handler=command_activate)

    append = commands.add_parser("append")
    append.add_argument("--sequence", type=int, required=True)
    append.add_argument("--start", type=float, required=True)
    append.add_argument("--end", type=float, required=True)
    append.add_argument("--text", required=True)
    append.add_argument("--confidence", type=float)
    append.add_argument("--speaker-label")
    append.add_argument("--finalized", action="store_true")
    append.add_argument("--partial", action="store_true")
    append.add_argument("--at")
    append.set_defaults(handler=command_append)

    gap = commands.add_parser("gap")
    gap.add_argument("--start", type=float, required=True)
    gap.add_argument("--end", type=float, required=True)
    gap.add_argument("--reason", required=True)
    gap.add_argument("--at")
    gap.set_defaults(handler=command_gap)

    resume = commands.add_parser("resume")
    resume.add_argument("--at")
    resume.set_defaults(handler=command_resume)

    stop = commands.add_parser("stop")
    stop.add_argument("--reason", default="USER_STOP")
    stop.add_argument("--at")
    stop.set_defaults(handler=command_stop)

    budget = commands.add_parser("budget-stop")
    budget.add_argument("--at")
    budget.set_defaults(handler=command_budget_stop)

    bookmark = commands.add_parser("bookmark")
    bookmark.add_argument("--segment-id", action="append", required=True)
    bookmark.add_argument("--reason-code", required=True)
    bookmark.add_argument("--note")
    bookmark.add_argument("--at")
    bookmark.set_defaults(handler=command_bookmark)

    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--input", type=Path, required=True)
    reconcile.add_argument("--at")
    reconcile.set_defaults(handler=command_reconcile)

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=command_status)
    commands.add_parser("formal-candidates").set_defaults(handler=command_formal)
    commands.add_parser("self-check").set_defaults(handler=lambda _: self_check() or 0)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.handler(arguments))
