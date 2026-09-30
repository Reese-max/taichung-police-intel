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
    assert result["receipt"]["official_provenance_candidate_count"] == 2
    assert result["receipt"]["new_public_event_count"] == 0
    assert result["receipt"]["existing_event_match_count"] == 0
    assert result["receipt"]["canonical_change_count"] == 0
    media = next(row for row in result["candidates"] if row["authority"] == "media")
    assert media["verification_status"] == "DISCOVERY_UNVERIFIED"
    assert media["official_match_status"] == "VERIFIED_OFFICIAL"
    print("DISCOVERY_ADAPTER_SELF_CHECK_OK relevant=2 official_provenance_candidates=2 canonical_changes=0 media_unverified=true")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("ingest", "self-check"))
    parser.add_argument("--feed", type=Path)
    parser.add_argument("--official-documents", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--now")
    parser.add_argument("--ttl-hours", type=int, default=72)
    parser.add_argument("--max-candidates", type=int, default=50)
    parser.add_argument("--max-retained-candidates", type=int, default=500)
    parser.add_argument("--historical-replay", action="store_true",
                        help="Allow historical verification for a PAUSED snapshot only")
    parser.add_argument("--canary-mode", action="store_true",
                        help="Evaluate matches without promotion for a RESTORING snapshot only")
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
    result = ingest_feed(
        feed,
        previous_state=previous,
        official_documents=documents,
        now=now,
        ttl_hours=args.ttl_hours,
        max_candidates=args.max_candidates,
        max_retained_candidates=args.max_retained_candidates,
        historical_replay=args.historical_replay,
        canary_mode=args.canary_mode,
    )
    write_json(args.output, result)
    print(f"DISCOVERY_ADAPTER_OK status={result['receipt']['status']} relevant={result['receipt']['relevant_count']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
