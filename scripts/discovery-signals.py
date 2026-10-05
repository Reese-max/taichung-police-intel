#!/usr/bin/env python3
"""Project a Dashboard discovery ingest into the bounded UI signals payload."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.discovery_adapter import ingest_feed, load_feed
from intel_v2.discovery_projection import project_discovery_signals


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
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


def build(feed_path: Path, documents_path: Path | None, state_path: Path | None, now: datetime | None, pending_limit: int, status: str) -> dict:
    feed = load_feed(str(feed_path))
    # Default the replay clock to the upstream snapshot time so committed
    # artifacts stay deterministic; callers pass --now for live runs.
    effective_now = now or datetime.fromisoformat(feed["generated_at"].replace("Z", "+00:00"))
    documents = json.loads(documents_path.read_text(encoding="utf-8")) if documents_path else []
    previous = json.loads(state_path.read_text(encoding="utf-8")) if state_path and state_path.exists() else None
    result = ingest_feed(feed, previous_state=previous, official_documents=documents, now=effective_now)
    return project_discovery_signals(result, now=effective_now, pending_limit=pending_limit, status=status)


def self_check() -> None:
    payload = build(
        ROOT / "tests/fixtures/discovery/dashboard-feed.v1.json",
        ROOT / "tests/fixtures/discovery/official-matches.json",
        None,
        datetime(2026, 9, 21, tzinfo=timezone.utc),
        5,
        "FIXTURE_ONLY",
    )
    assert payload["schema_version"] == 1
    assert payload["kind"] == "GOVINTEL_DISCOVERY_SIGNALS"
    assert payload["status"] == "FIXTURE_ONLY" and payload["production_verified"] is False
    assert payload["upstream"]["signal_mode"] == "CURRENT"
    assert len(payload["confirmed"]) == 2 and all(row["official_document_versions"] for row in payload["confirmed"])
    assert len(payload["pending"]) == 1
    assert payload["pending"][0]["note"] == "尚未找到官方確認"
    assert payload["counts"]["confirmed_count"] == 2
    assert payload["counts"]["pending_total"] == 1
    assert payload["counts"]["feed_item_count"] == 4
    print("DISCOVERY_SIGNALS_SELF_CHECK_OK confirmed=2 pending=1 mode=CURRENT fixture_only=true")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("generate", "self-check"))
    parser.add_argument("--feed", type=Path)
    parser.add_argument("--official-documents", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--now")
    parser.add_argument("--pending-limit", type=int, default=5)
    parser.add_argument("--status", choices=("FIXTURE_ONLY", "LIVE"), default="FIXTURE_ONLY")
    args = parser.parse_args()
    if args.command == "self-check":
        self_check()
        return 0
    if args.feed is None or args.output is None:
        parser.error("generate requires --feed and --output")
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
    payload = build(args.feed, args.official_documents, args.state, now, args.pending_limit, args.status)
    write_json(args.output, payload)
    counts = payload["counts"]
    print(
        f"DISCOVERY_SIGNALS_OK status={payload['status']} confirmed={counts['confirmed_count']} "
        f"pending={counts['pending_shown']}/{counts['pending_total']} output={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
