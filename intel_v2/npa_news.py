"""Dataset 7505 candidate-only collector. No activation or public news export.

Untrusted text is parsed transiently and hashed, never placed in the receipt or
state. The feed offers publication dates, not source modification timestamps.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timezone
import hashlib
import io
import json
import re
import time
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

SOURCE_ID = "S-038"
DATASET_URL = "https://data.gov.tw/dataset/7505"
RESOURCE_ID = "0C059012-34C5-40B1-80AD-CC9389827CA3"
RESOURCE_URL = ("https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/"
                "00F7F1C4-2AC0-461C-B060-A6FCD3FF6E45/resource/" + RESOURCE_ID + "/download")
FIELDS = ["serialNo", "stitle", "deptName", "postDate", "content"]
DESCRIPTION = ["序號", "主旨", "發布機關", "張貼日", "內文"]
MAX_BYTES = 8 * 1024 * 1024
MAX_ROWS = 5000
SCHEMA = "npa-news-candidate.v1"


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _observed(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("observed_at must be an ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("observed_at must include a timezone")
    return parsed.astimezone(timezone.utc)


def _publication(value):
    # Official CSV includes local minute timestamps; retain source precision.
    if re.fullmatch(r"\d{4}[-/]\d{2}[-/]\d{2}", value):
        try:
            day = date.fromisoformat(value.replace("/", "-")).isoformat()
            return day, None, "DAY"
        except ValueError:
            pass
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?", value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Taipei"))
            local = parsed.astimezone(ZoneInfo("Asia/Taipei"))
            precision = "SECOND" if re.match(r".*T\d{2}:\d{2}:\d{2}", value) else "MINUTE"
            return local.date().isoformat(), local.isoformat(), precision
        except ValueError:
            pass
    raise ValueError("invalid publication date")


def parse_csv(body: bytes, *, observed_at: str):
    observed = _observed(observed_at)
    if not isinstance(body, bytes) or not body or len(body) > MAX_BYTES:
        raise ValueError("empty or over-budget CSV")
    try:
        text = body.decode("utf-8-sig", errors="strict")
    except UnicodeError:
        raise ValueError("CSV must be valid UTF-8") from None
    if "\x00" in text:
        raise ValueError("NUL byte in CSV")
    rows = []
    try:
        for row in csv.reader(io.StringIO(text, newline=""), strict=True):
            if not any(value.strip() for value in row):
                continue
            rows.append(row)
            if len(rows) > MAX_ROWS + 2:
                raise ValueError("empty or over-budget row count")
    except csv.Error:
        raise ValueError("malformed CSV") from None
    if not rows or len(rows) > MAX_ROWS + 2:
        raise ValueError("empty or over-budget row count")
    description_count = 0
    if [v.strip() for v in rows[0]] == DESCRIPTION:
        rows.pop(0)
        description_count += 1
    if not rows or [v.strip() for v in rows.pop(0)] != FIELDS:
        raise ValueError("CSV schema drift")
    if rows and [v.strip() for v in rows[0]] == DESCRIPTION:
        rows.pop(0)
        description_count += 1
    if description_count > 1 or not rows or len(rows) > MAX_ROWS:
        raise ValueError("invalid description rows or empty data")
    records, seen_payload, duplicate_count = {}, {}, 0
    taichung_count = 0
    for row in rows:
        if len(row) != len(FIELDS):
            raise ValueError("CSV row width mismatch")
        serial, title, department, posted, content = [v.strip() for v in row]
        if not re.fullmatch(r"[0-9]{1,30}", serial):
            raise ValueError("invalid serialNo")
        if not title or not department or not content:
            raise ValueError("required text field is empty")
        published_date, published_at, precision = _publication(posted)
        # Publication day is local Taiwan time, independent of receipt UTC day.
        if date.fromisoformat(published_date) > observed.astimezone(ZoneInfo("Asia/Taipei")).date():
            raise ValueError("publication date is in the future")
        if published_at and datetime.fromisoformat(published_at) > observed:
            raise ValueError("publication timestamp is in the future")
        normalized = [serial, title, department, published_at or published_date, content.replace("\r\n", "\n").replace("\r", "\n")]
        digest = _hash(_canonical(normalized))
        if serial in records:
            if records[serial]["record_sha256"] != digest:
                raise ValueError("conflicting duplicate serialNo")
            duplicate_count += 1
            continue
        # Text/links/images/contacts are deliberately absent even from saved state.
        records[serial] = {"record_sha256": digest, "content_sha256": _hash(normalized[-1].encode()),
                           "publisher_sha256": _hash(department.encode()), "published_date": published_date,
                           "published_at": published_at, "publication_precision": precision, "publication_timezone": "Asia/Taipei",
                           "source_updated_at": None}
        seen_payload.setdefault(records[serial]["content_sha256"], set()).add(serial)
        taichung_count += int("臺中" in department or "台中" in department)
    return records, {"input_rows": len(rows), "unique_records": len(records),
                     "identical_duplicate_rows": duplicate_count, "description_rows": description_count,
                     "shared_content_hash_groups": sum(len(v) > 1 for v in seen_payload.values()),
                     "taichung_publisher_rows": taichung_count,
                     "oldest_publication_date": min(r["published_date"] for r in records.values()),
                     "newest_publication_date": max(r["published_date"] for r in records.values())}


def _previous(previous, observed):
    if not isinstance(previous, dict) or previous.get("schema") != SCHEMA or previous.get("source_id") != SOURCE_ID or previous.get("resource_url") != RESOURCE_URL:
        raise ValueError("previous state identity mismatch")
    if _observed(previous.get("observed_at")) > observed:
        raise ValueError("observation time moved backwards")
    records = previous.get("records")
    if not isinstance(records, dict) or not records or len(records) > MAX_ROWS * 20:
        raise ValueError("invalid previous records")
    for serial, row in records.items():
        if not re.fullmatch(r"[0-9]{1,30}", serial) or not isinstance(row, dict):
            raise ValueError("invalid previous record identity")
        if set(row) != {"record_sha256", "content_sha256", "publisher_sha256", "published_date", "published_at",
                        "publication_precision", "publication_timezone", "source_updated_at", "first_seen_at",
                        "last_seen_at", "change_observed_at"}:
            raise ValueError("unexpected previous record fields")
        if row["source_updated_at"] is not None or row["publication_timezone"] != "Asia/Taipei":
            raise ValueError("invalid previous date semantics")
        day, stamp, precision = _publication(row["published_at"] or row["published_date"])
        if (day != row["published_date"] or row["publication_precision"] not in {"DAY", "MINUTE", "SECOND"}
                or (row["published_at"] is None) != (row["publication_precision"] == "DAY")):
            raise ValueError("invalid previous publication metadata")
        for key in ("last_seen_at", "change_observed_at"):
            if not _observed(row["first_seen_at"]) <= _observed(row[key]) <= _observed(previous["observed_at"]):
                raise ValueError("invalid previous observation sequence")
        for field in ("record_sha256", "content_sha256", "publisher_sha256"):
            if not re.fullmatch(r"[a-f0-9]{64}", str(row.get(field, ""))):
                raise ValueError("invalid previous record hash")
        if _observed(row.get("first_seen_at")) > _observed(previous["observed_at"]):
            raise ValueError("invalid first_seen_at")
    return records


def collect_snapshot(body: bytes, *, observed_at: str, previous=None, transport=None):
    """Pure candidate delta. Failure raises without advancing last-known-good state."""
    observed = _observed(observed_at)
    timestamp = observed.isoformat(timespec="seconds")
    old = {} if previous is None else _previous(previous, observed)
    records, quality = parse_csv(body, observed_at=timestamp)
    state_records = {k: dict(v) for k, v in old.items()}
    deltas = []
    for serial, record in records.items():
        prior = old.get(serial)
        change = ("HISTORICAL_BASELINE" if previous is None else
                  "CANDIDATE_NEW_ID" if prior is None else
                  "CANDIDATE_REVISION" if prior["record_sha256"] != record["record_sha256"] else "UNCHANGED")
        record["first_seen_at"] = prior["first_seen_at"] if prior else timestamp
        record["last_seen_at"] = timestamp
        record["change_observed_at"] = timestamp if change != "UNCHANGED" else prior.get("change_observed_at", timestamp)
        state_records[serial] = record
        deltas.append({"serial_no": serial, "change_type": change, "record_sha256": record["record_sha256"]})
    if len(state_records) > MAX_ROWS * 20:
        raise ValueError("cumulative state record budget exceeded")
    # Preserve missing identities: limited snapshots cannot establish deletion.
    absent = sorted(set(old) - set(records))
    state = {"schema": SCHEMA, "source_id": SOURCE_ID, "resource_url": RESOURCE_URL,
             "observed_at": timestamp, "records": state_records}
    receipt = {"schema": SCHEMA, "source_id": SOURCE_ID, "dataset_id": "7505", "resource_id": RESOURCE_ID,
               "dataset_url": DATASET_URL, "resource_url": RESOURCE_URL, "observed_at": timestamp,
               "raw_bytes": len(body), "raw_sha256": _hash(body), "quality": quality,
               "mode": "HISTORICAL_BASELINE" if previous is None else "CANDIDATE_DELTA",
               "deltas": deltas, "absent_serials": absent, "absence_semantics": "UNKNOWN_NOT_RETRACTION",
               "rights_status": "UNKNOWN", "review_required": True, "activated": False,
               "public_records": [], "full_text_allowed": False, "excerpt_allowed": False,
               "coverage_status": "UNKNOWN", "freshness_status": "UNKNOWN", "cadence_status": "UNKNOWN",
               "serial_identity_stability": "UNKNOWN", "runtime_status": "CANDIDATE_ONLY", "transport": transport or {"mode": "OFFLINE"}}
    return receipt, state


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("resource redirects are not allowed")


def fetch_csv(*, opener=None, clock=time.monotonic):
    """One GET, exact fixed HTTPS resource, no redirect/retry/detail fetch.

    20 second socket timeout and 30 second streaming deadline; an in-progress
    socket operation can extend the deadline by at most its timeout.
    """
    start = clock()
    client = opener or build_opener(_NoRedirect)
    request = Request(RESOURCE_URL, headers={"Accept": "text/csv", "Accept-Encoding": "identity",
                                           "User-Agent": "GovIntel-S038-candidate/1"})
    with client.open(request, timeout=20) as response:
        if response.status != 200 or response.geturl() != RESOURCE_URL:
            raise ValueError("unexpected resource status or final URL")
        content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"text/csv", "application/csv", "application/octet-stream", "text/plain"}:
            raise ValueError("unexpected resource content type")
        if response.headers.get("Content-Encoding", "identity").lower() not in {"", "identity"}:
            raise ValueError("encoded resource response is not allowed")
        length = response.headers.get("Content-Length")
        if length is not None and (not length.isdigit() or int(length) > MAX_BYTES):
            raise ValueError("invalid or excessive Content-Length")
        chunks, total = [], 0
        while True:
            if clock() - start >= 30:
                raise ValueError("resource elapsed-time budget exceeded")
            block = response.read1(min(65536, MAX_BYTES + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > MAX_BYTES:
                raise ValueError("resource byte budget exceeded")
        if clock() - start >= 30:
            raise ValueError("resource elapsed-time budget exceeded")
        body = b"".join(chunks)
        if length is not None and len(body) != int(length):
            raise ValueError("truncated resource response")
    return body, {"mode": "BOUNDED_PUBLIC_FETCH", "requested_url": RESOURCE_URL, "final_url": RESOURCE_URL,
                  "http_status": 200, "content_type": content_type, "elapsed_seconds": round(clock() - start, 3)}
