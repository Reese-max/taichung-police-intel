#!/usr/bin/env python3
"""Build a bounded located-fact candidate bundle from a saved document."""

from __future__ import annotations

import argparse
import base64
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys
from urllib.request import HTTPRedirectHandler, Request, build_opener

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.located_facts import MAX_DOCUMENT_BYTES, acquire_document, build_bundle, validate_document_url, verify_fact


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("redirect is not allowed for bounded official acquisition")


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
    json_body = (ROOT / "tests/fixtures/located-facts/official-data.json").read_bytes()
    json_document = acquire_document(
        source_id="S-028",
        requested_url="https://data.gov.tw/dataset/88147",
        final_url="https://data.gov.tw/api/v2/rest/dataset/88147",
        body=json_body,
        content_type="application/json",
        fetched_at="2026-09-21T00:00:00+00:00",
    )
    json_bundle = build_bundle(json_document, json_body, [{"subject_id": "crime:A", "predicate": "count", "pointer": "/event/count", "normalizer": "integer"}])
    assert json_bundle["facts"][0]["normalized_value"] == 1234
    assert json_bundle["evidence_catalog"][0]["locator"]["type"] == "JSON_POINTER"
    print("LOCATED_FACTS_SELF_CHECK_OK html=true json=true hash_bound=true locator_verified=true candidate_only=true")


def fetch_live(source_id: str, requested_url: str, fetched_at: str, rights_status: str) -> tuple[dict, bytes]:
    validate_document_url(source_id, requested_url)
    request = Request(requested_url, headers={"User-Agent": "GovIntel-located-facts/1", "Accept": "text/html,application/json"})
    with build_opener(_NoRedirect).open(request, timeout=30) as response:
        body = response.read(MAX_DOCUMENT_BYTES + 1)
        if len(body) > MAX_DOCUMENT_BYTES:
            raise ValueError("document exceeds bounded byte limit")
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "application/octet-stream")
    validate_document_url(source_id, final_url)
    document = acquire_document(
        source_id=source_id,
        requested_url=requested_url,
        final_url=final_url,
        body=body,
        content_type=content_type,
        fetched_at=fetched_at,
        rights_status=rights_status,
    )
    return document, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "live", "self-check"))
    parser.add_argument("--source-id")
    parser.add_argument("--requested-url")
    parser.add_argument("--final-url")
    parser.add_argument("--body", type=Path)
    parser.add_argument("--content-type", default="text/html; charset=utf-8")
    parser.add_argument("--fetched-at")
    parser.add_argument("--rights-status", default="METADATA_LINK_ONLY")
    parser.add_argument("--snapshot-output", type=Path)
    parser.add_argument("--rules", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "self-check":
        self_check()
        return 0
    if args.command == "live":
        required = (args.source_id, args.requested_url, args.fetched_at, args.rules, args.output)
        if any(value is None for value in required):
            parser.error("live requires --source-id --requested-url --fetched-at --rules --output")
        document, body = fetch_live(args.source_id, args.requested_url, args.fetched_at, args.rights_status)
        rules_payload = json.loads(args.rules.read_text(encoding="utf-8"))
        rules = rules_payload["json" if "json" in document["content_type"].lower() else "html"] if isinstance(rules_payload, dict) else rules_payload
        bundle = build_bundle(document, body, rules)
        bundle["receipt"]["acquisition_mode"] = "LIVE_HTTP"
        bundle["receipt"]["snapshot_saved"] = args.snapshot_output is not None
        if args.snapshot_output:
            args.snapshot_output.parent.mkdir(parents=True, exist_ok=True)
            args.snapshot_output.write_bytes(body)
        write_json(args.output, bundle)
        print(f"LOCATED_FACTS_LIVE_OK source={args.source_id} facts={len(bundle['facts'])} raw_sha256={document['raw_bytes_sha256']} output={args.output}")
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
