#!/usr/bin/env python3
"""Build a bounded located-fact candidate bundle from a saved document."""

from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.located_facts import acquire_document, build_bundle, verify_fact


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
    body = "<article><p>2026-09-21 活動開始 18:00，道路管制 16:00。</p></article>".encode("utf-8")
    document = acquire_document(
        source_id="S-001",
        requested_url="https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
        final_url="https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
        body=body,
        content_type="text/html; charset=utf-8",
        fetched_at="2026-09-21T00:00:00+00:00",
    )
    bundle = build_bundle(document, body, [{"subject_id": "event:A", "predicate": "event_start_at", "needle": "18:00", "date_needle": "2026-09-21"}])
    assert bundle["facts"][0]["valid_time"] == "2026-09-21"
    assert verify_fact(document, body, bundle["facts"][0])["status"] == "PASS"
    print("LOCATED_FACTS_SELF_CHECK_OK html=true hash_bound=true locator_verified=true candidate_only=true")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "self-check"))
    parser.add_argument("--source-id")
    parser.add_argument("--requested-url")
    parser.add_argument("--final-url")
    parser.add_argument("--body", type=Path)
    parser.add_argument("--content-type", default="text/html; charset=utf-8")
    parser.add_argument("--fetched-at")
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "self-check":
        self_check()
        return 0
    required = (args.source_id, args.requested_url, args.final_url, args.body, args.fetched_at, args.rules, args.output)
    if any(value is None for value in required):
        parser.error("build requires --source-id --requested-url --final-url --body --fetched-at --rules --output")
    body = args.body.read_bytes()
    rules = json.loads(args.rules.read_text(encoding="utf-8"))
    document = acquire_document(
        source_id=args.source_id,
        requested_url=args.requested_url,
        final_url=args.final_url,
        body=body,
        content_type=args.content_type,
        fetched_at=args.fetched_at,
    )
    bundle = build_bundle(document, body, rules)
    write_json(args.output, bundle)
    print(f"LOCATED_FACTS_OK source={args.source_id} facts={len(bundle['facts'])} review={bundle['receipt']['needs_review_count']} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
