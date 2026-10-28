"""Check one relay capture before the relay host publishes it (stdlib only).

The relay host keeps no checkout of this repository, so this mirrors the
checks scripts/schema_drift.py:load_relay makes, with a much shorter age:
a capture is published right after it is taken or not at all.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "govintel-source-relay/v1"
URLS = {
    "S-001": {
        "https://www.police.taichung.gov.tw/ch/home.jsp?id=1&parentpath=0&mcustomize=news_list.jsp",
        "https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
    },
}
MAX_AGE_SECONDS = 15 * 60
MAX_FUTURE_SECONDS = 300
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = MAX_BODY_BYTES * 4 // 3 + 64 * 1024
KEYS = {
    "schema", "source_id", "requested_url", "final_url", "http_status", "content_type",
    "observed_at", "body_base64", "body_sha256", "captured_by",
}


def problem(payload: object, *, now: datetime | None = None) -> str | None:
    """Why this capture must not be published, or None."""
    now = now or datetime.now(timezone.utc)
    if not isinstance(payload, dict) or set(payload) != KEYS:
        return "fields"
    if not all(isinstance(payload[key], str) for key in KEYS - {"http_status"}):
        return "types"
    if payload["schema"] != SCHEMA or payload["source_id"] not in URLS:
        return "schema"
    if type(payload["http_status"]) is not int or payload["http_status"] != 200:
        return "http_status"
    urls = URLS[payload["source_id"]]
    if payload["requested_url"] not in urls or payload["final_url"] not in urls:
        return "url"
    if len(payload["captured_by"]) > 64 or len(payload["content_type"]) > 256:
        return "metadata"
    try:
        observed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
    except ValueError:
        return "observed_at"
    if observed.tzinfo is None:
        return "observed_at"
    age = (now - observed).total_seconds()
    if age > MAX_AGE_SECONDS or age < -MAX_FUTURE_SECONDS:
        return "age"
    try:
        body = base64.b64decode(payload["body_base64"], validate=True)
    except (binascii.Error, ValueError):
        return "body"
    if not body or len(body) > MAX_BODY_BYTES:
        return "body"
    if hashlib.sha256(body).hexdigest() != payload["body_sha256"]:
        return "hash"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: check_relay.py CAPTURE.json", file=sys.stderr)
        return 2
    try:
        path = Path(argv[0])
        if path.stat().st_size > MAX_FILE_BYTES:
            print("RELAY_CHECK_FAILED reason=oversized", file=sys.stderr)
            return 1
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"RELAY_CHECK_FAILED reason=unreadable:{type(error).__name__}", file=sys.stderr)
        return 1
    reason = problem(payload)
    if reason is not None:
        print(f"RELAY_CHECK_FAILED reason={reason}", file=sys.stderr)
        return 1
    print(f"RELAY_CHECK_OK source={payload['source_id']} observed_at={payload['observed_at']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
