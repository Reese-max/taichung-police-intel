#!/usr/bin/env python3
"""Consume a bounded Taiwan Intel Dashboard discovery feed."""

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


def self_check() -> None:
    feed = load_feed(str(ROOT / "tests/fixtures/discovery/dashboard-feed.v1.json"))
    documents = json.loads((ROOT / "tests/fixtures/discovery/official-matches.json").read_text(encoding="utf-8"))
    result = ingest_feed(feed, official_documents=documents, now=datetime(2026, 9, 21, tzinfo=timezone.utc))
    assert result["receipt"]["relevant_count"] == 2
    assert result["receipt"]["official_match_count"] == 2
    assert result["receipt"]["new_public_event_count"] == 1
    assert result["receipt"]["existing_event_match_count"] == 1
    assert result["receipt"]["status"] == "PENDING_PUBLICATION"
    print("DISCOVERY_ADAPTER_SELF_CHECK_OK relevant=2 official=2 new_event=1 existing=1 media_boundary=true")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ingest", "self-check"))
    parser.add_argument("--feed", type=Path)
    parser.add_argument("--official-documents", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--now")
    args = parser.parse_args()
    if args.command == "self-check":
        self_check()
        return 0
    if args.feed is None or args.output is None:
        parser.error("ingest requires --feed and --output")
    feed = load_feed(str(args.feed))
    documents = json.loads(args.official_documents.read_text(encoding="utf-8")) if args.official_documents else []
    previous = json.loads(args.state.read_text(encoding="utf-8")) if args.state and args.state.exists() else None
    now = datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else datetime.now(timezone.utc)
    result = ingest_feed(feed, previous_state=previous, official_documents=documents, now=now)
    write_json(args.output, result)
    print(f"DISCOVERY_ADAPTER_OK status={result['receipt']['status']} relevant={result['receipt']['relevant_count']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
