"""Validate public metadata, skip unchanged records, and enforce write guards."""
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

from .store import RUN_WRITE_RESERVE, sql_value, utc_now

# D1 free tier allows 100,000 rows/UTC day; keep 1,000 for run receipts and manual queries.
DAILY_WRITE_LIMIT = 99_000
MAX_BATCH_BYTES = 99_000  # leave space for local accounting SQL; file stays <100KB
# D1 meta.rows_written measured 6.0 rows per inserted record (2026-11-19: TNN 69,918/11,653,
# KHH 16,578/2,763); 16 leaves headroom for updates that rewrite index and FTS entries.
RECORD_WRITE_ESTIMATE = 16
SOURCE_WRITE_ESTIMATE = 2
PUBLIC_FIELDS = set("title case_number event_date jurisdiction authority official_url provider_url license".split())
SYSTEM_FIELDS = set("source_id kind external_id fetched_at rights_class".split())
RECORD_FIELDS = tuple("source_id kind jurisdiction external_id title case_number event_date authority official_url provider_url fetched_at content_sha256 license rights_class".split())


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def normalize(source, record):
    allowed = set(source["public_fields"])
    if not allowed <= PUBLIC_FIELDS or not {"title", "official_url"} <= allowed:
        raise ValueError("invalid source public_fields")
    if not isinstance(record, dict) or set(record) - (allowed | SYSTEM_FIELDS):
        raise ValueError("record contains non-public or loader-owned fields")
    if any(value is not None and not isinstance(value, str) for value in record.values()):
        raise ValueError("record fields must be strings or null")
    result = {key: record.get(key) for key in RECORD_FIELDS}
    for key in ("external_id", "title", "kind"):
        if not result[key] or not result[key].strip():
            raise ValueError(key + " must not be blank")
    if len(result["external_id"].encode("utf-8")) > 512:
        raise ValueError("external_id exceeds 512 bytes")
    if record.get("source_id", source["national_source_id"]) != source["national_source_id"]:
        raise ValueError("source_id mismatch")
    if record.get("rights_class", source["rights_class"]) != source["rights_class"]:
        raise ValueError("rights_class mismatch")
    result["source_id"] = source["national_source_id"]
    result["rights_class"] = source["rights_class"]
    defaults = {"jurisdiction": source.get("scope", {}).get("jurisdiction"),
                "authority": source["official_authority"], "license": source["license"]}
    for key, value in defaults.items():
        if key in allowed and key not in record:
            result[key] = value
    hosts = {urlsplit(origin).hostname for origin in source["official_url_hosts"]}
    for key in ("official_url", "provider_url"):
        value = result[key]
        if key == "provider_url" and value is None:
            continue
        if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("invalid " + key)
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in hosts
                or parsed.username is not None or parsed.password is not None
                or parsed.port not in (None, 443) or "\\" in value):
            raise ValueError("non-official " + key)
    if result["event_date"] is not None:
        value = result["event_date"]
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("event_date must be YYYY-MM-DD")
    result["fetched_at"] = record.get("fetched_at") or utc_now()
    if datetime.fromisoformat(result["fetched_at"].replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("fetched_at must have a timezone")
    # Fetch time is operational: a repeated fetch alone must not rewrite content.
    # Identity/type/rights are also public metadata and must invalidate the hash.
    result["content_sha256"] = digest({key: result[key] for key in sorted(
        allowed | (SYSTEM_FIELDS - {"fetched_at"}))})
    return result


def record_sql(record):
    updates = ",".join(key + "=excluded." + key for key in RECORD_FIELDS
                       if key not in {"source_id", "external_id"})
    return ("INSERT INTO records (" + ",".join(RECORD_FIELDS) + ") VALUES ("
            + ",".join(sql_value(record[key]) for key in RECORD_FIELDS) + ") "
            + "ON CONFLICT(source_id,external_id) DO UPDATE SET " + updates
            + " WHERE records.content_sha256 <> excluded.content_sha256;\n")


def batches(records):
    sql, count = "", 0
    for record in records:
        statement = record_sql(record)
        if len(statement.encode("utf-8")) >= MAX_BATCH_BYTES:
            raise ValueError("single record exceeds SQL batch byte limit")
        if len((sql + statement).encode("utf-8")) >= MAX_BATCH_BYTES:
            yield sql, count
            sql, count = "", 0
        sql += statement
        count += 1
    if sql:
        yield sql, count


def source_sql(source):
    row = {"source_id": source["national_source_id"], "provider": source["provider"],
           "official_authority": source["official_authority"],
           "jurisdiction": source.get("scope", {}).get("jurisdiction"), "runner": source["runner"],
           "rights_class": source["rights_class"], "license": source["license"],
           "robots_status": source["robots"]["status"], "registry_sha256": digest(source), "updated_at": utc_now()}
    return ("INSERT INTO sources (" + ",".join(row) + ") VALUES ("
            + ",".join(sql_value(value) for value in row.values()) + ") "
            + "ON CONFLICT(source_id) DO UPDATE SET "
            + ",".join(key + "=excluded." + key for key in row if key != "source_id")
            + " WHERE sources.registry_sha256 <> excluded.registry_sha256;\n")


def check_guards(store, *, capacity_limit_mb=400, daily_limit=DAILY_WRITE_LIMIT):
    size = store.database_size_bytes()
    written = store.rows_written_today()
    return {"database_size_bytes": size, "capacity_limit_mb": capacity_limit_mb,
            "rows_written_today": written, "daily_write_limit": daily_limit,
            "remaining_rows": max(0, daily_limit - written),
            "status": "CAPACITY_BLOCKED" if size > capacity_limit_mb * 1_000_000 else
                      "BUDGET_STOPPED" if written + RUN_WRITE_RESERVE >= daily_limit else "OK"}


def load(store, source, adapter, *, capacity_limit_mb=400, daily_limit=DAILY_WRITE_LIMIT,
         runner=None, dry_run=False, emit=print):
    runner = runner or os.environ.get("NATIONAL_RUNNER", "github-actions")
    run = dict(run_id=str(uuid4()), source_id=source["national_source_id"], started_at=utc_now(),
               finished_at=None, status="OK", fetched=0, changed=0, rows_written=0,
               cursor_before=None, cursor_after=None, error=None, exit_code=0, warnings=[])
    if source["runner"] != runner:
        return {**run, "finished_at": utc_now(), "status": "NOT_CONFIGURED",
                "reason": "RUNNER_MISMATCH", "runner": runner, "expected_runner": source["runner"]}
    cursor = None if dry_run else store.resume_cursor(run["source_id"])
    run["cursor_before"] = run["cursor_after"] = cursor
    today = utc_now()[:10]
    accounted = 0

    def permitted(estimate):
        current_day = utc_now()[:10]
        if current_day != today:
            # Stop before crossing UTC days so the receipt is not misattributed.
            run["status"] = "BUDGET_STOPPED"
            return False
        guards = check_guards(store, capacity_limit_mb=capacity_limit_mb, daily_limit=daily_limit)
        if guards["status"] == "CAPACITY_BLOCKED":
            run.update(status="CAPACITY_BLOCKED", exit_code=1)
            return False
        if guards["rows_written_today"] + accounted + estimate + RUN_WRITE_RESERVE > daily_limit:
            run["status"] = "BUDGET_STOPPED"
            return False
        return True

    try:
        retention_days = source.get("scope", {}).get("retention_days")
        if retention_days is not None and (type(retention_days) is not int or retention_days < 0):
            raise ValueError("retention_days must be a nonnegative integer")
        if not dry_run and not permitted(0):
            return run
        if hasattr(adapter, "begin_run"):
            adapter.begin_run()
        source_pending = True
        while True:
            records, next_cursor = adapter.fetch(source, cursor)
            run["warnings"] = list(getattr(adapter, "warnings", []))
            run["fetched"] += len(records)
            normalized = [normalize(source, record) for record in records]
            ids = [record["external_id"] for record in normalized]
            if len(ids) != len(set(ids)):
                raise ValueError("duplicate external_id in adapter page")
            hashes = {} if dry_run else store.existing_hashes(run["source_id"], ids)
            changed = [record for record in normalized if hashes.get(record["external_id"]) != record["content_sha256"]]
            # Validate all statement sizes before writing any part of the page.
            page_batches = list(batches(changed))
            if source_pending:
                sql = source_sql(source)
                if dry_run:
                    emit(sql)
                elif store.source_hash(run["source_id"]) != digest(source):
                    if not permitted(SOURCE_WRITE_ESTIMATE):
                        return run
                    accounted += store.execute_batch(sql)
                    run["rows_written"] = accounted
                source_pending = False
            for sql, count in page_batches:
                if dry_run:
                    emit(sql)
                else:
                    if not permitted(count * RECORD_WRITE_ESTIMATE):
                        return run
                    accounted += store.execute_batch(sql)
                    run["rows_written"] = accounted
                run["changed"] += count
            cursor = next_cursor
            run["cursor_after"] = cursor
            if cursor is None:
                break
        if retention_days is not None:
            cutoff = (date.fromisoformat(today) - timedelta(days=retention_days)).isoformat()
            predicate = ("source_id=" + sql_value(run["source_id"])
                         + " AND event_date < " + sql_value(cutoff))
            sql = "DELETE FROM records WHERE " + predicate + ";\n"
            if dry_run:
                emit(sql)
            else:
                count = store.query("SELECT COUNT(*) AS n FROM records WHERE " + predicate)[0]["n"]
                run["deleted"] = 0
                # Bound each estimate so a backlog larger than one day's budget can progress.
                for start in range(0, count, 100):
                    size = min(100, count - start)
                    if not permitted(size * RECORD_WRITE_ESTIMATE):
                        return run
                    sql = ("DELETE FROM records WHERE id IN (SELECT id FROM records WHERE "
                           + predicate + " ORDER BY id LIMIT " + str(size) + ");\n")
                    accounted += store.execute_batch(sql)
                    run["rows_written"] = accounted
                    run["deleted"] += size
    except Exception as error:
        # Keep the current page cursor; committed sub-batches are skipped on replay.
        run.update(status="ERROR", exit_code=1, error=type(error).__name__ + ": " + str(error))
    finally:
        run["warnings"] = list(getattr(adapter, "warnings", []))
        run["finished_at"] = utc_now()
        if not dry_run:
            if accounted == 0 and store.rows_written_today() + RUN_WRITE_RESERVE > daily_limit:
                # A stopped retry must not spend the last rows just to log a stop.
                run["audit_recorded"] = False
            else:
                store.record_run(run)
    return run
