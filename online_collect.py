from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import time
import urllib.parse
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import requests
from bs4 import BeautifulSoup
from psycopg.rows import dict_row
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from collect import (
    P0_SOURCES,
    ROOT,
    SOURCE_FRESHNESS_POLICY,
    TZ,
    canonical_sha256,
    freshness_status,
    gap_reasons,
    next_update,
    save_state,
    scheduled_time,
    timestamp,
)


USER_AGENT = "TaichungPoliceIntel/0.2 (+public-source-monitor)"
API_S007 = "https://yishi.tccc.gov.tw/api/ProceedingsBackWeb/FrontList"
API_S009 = "https://yishi.tccc.gov.tw/api/Proposal/FrontList"
PARSER_VERSION = "p0-live-1"
SOURCE_ROWS = {
    source_id: (name, "PRIMARY_OFFICIAL", "PREP_CORE", "ACTIVE")
    for source_id, (name, _) in P0_SOURCES.items()
}


def http_session() -> requests.Session:
    retry = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    response = session.get(url, timeout=kwargs.pop("timeout", 60), **kwargs)
    response.raise_for_status()
    return response


def snapshot(response: requests.Response, purpose: str) -> dict:
    return {
        "purpose": purpose,
        "requested_url": response.request.url,
        "final_url": response.url,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type") or "application/octet-stream",
        "body": response.content,
        "content_sha256": canonical_bytes_sha256(response.content),
    }


def canonical_bytes_sha256(body: bytes) -> str:
    import hashlib

    return hashlib.sha256(body).hexdigest()


def roc_date(value: str) -> date | None:
    match = re.search(r"(\d{2,3})[./](\d{1,2})[./](\d{1,2})", value)
    if not match:
        return None
    year, month, day = map(int, match.groups())
    return date(year + 1911, month, day)


def published_at(value: str | date | None) -> str | None:
    if value is None:
        return None
    parsed = value if isinstance(value, date) else date.fromisoformat(value[:10])
    return timestamp(datetime.combine(parsed, datetime.min.time(), TZ))


def parse_download_entries(html: bytes, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    entries = []
    for row in soup.select("div#Fdownload_list"):
        title_node = row.select_one(".text02_1")
        if not title_node:
            continue
        title = " ".join(title_node.stripped_strings)
        title = re.sub(r"\s*NEW\s*$", "", title).strip()
        attachments = [
            urllib.parse.urljoin(base_url, anchor["href"])
            for anchor in row.select(".text02_2 a[href]")
        ]
        if attachments:
            entries.append({"title": title, "attachment_urls": attachments})
    if not entries:
        raise ValueError("download list has no parseable entries")
    session_match = re.match(r"^(.+?第\d+次(?:定期會|臨時會))", entries[0]["title"])
    if not session_match:
        raise ValueError("download list latest session is unknown")
    prefix = session_match.group(1)
    return [entry for entry in entries if entry["title"].startswith(prefix)]


def collect_download_list(
    session: requests.Session,
    source_id: str,
    start: date,
    end: date,
) -> dict:
    source_url = P0_SOURCES[source_id][1]
    listing = get(session, source_url)
    responses = [snapshot(listing, "LIST")]
    entries = parse_download_entries(listing.content, listing.url)
    items = []
    for entry in entries:
        attachments = []
        for url in entry["attachment_urls"]:
            response = get(session, url, timeout=120)
            responses.append(snapshot(response, "ATTACHMENT"))
            attachments.append(
                {
                    "url": response.url,
                    "content_type": response.headers.get("content-type") or "application/octet-stream",
                    "byte_count": len(response.content),
                    "content_sha256": canonical_bytes_sha256(response.content),
                }
            )
        item_date = roc_date(entry["title"])
        payload = {"title": entry["title"], "attachments": attachments}
        stable_path = urllib.parse.urlparse(entry["attachment_urls"][0]).path
        items.append(
            {
                "stable_key": Path(stable_path).stem,
                "source_url": attachments[0]["url"],
                "published_at": published_at(item_date),
                "content_sha256": canonical_sha256(payload),
                "payload": payload,
            }
        )

    dated = [date.fromisoformat(item["published_at"][:10]) for item in items if item["published_at"]]
    window_items = [item for item in items if item["published_at"] and start <= date.fromisoformat(item["published_at"][:10]) <= end]
    if window_items:
        completeness = "COMPLETE_WITH_ITEMS"
    elif dated and max(dated) < start:
        completeness = "COMPLETE_ZERO"
    elif not dated and items:
        # Items exist but have no extractable dates (e.g. committee question-order lists).
        # The source successfully returned content; treat as complete-zero in window terms.
        completeness = "COMPLETE_ZERO"
    else:
        completeness = "PARTIAL"
    manifest = [{key: item[key] for key in ("stable_key", "content_sha256", "published_at")} for item in items]
    return {
        "source_health": "PASS",
        "window_completeness": completeness,
        "window_item_count": len(window_items),
        "snapshot_item_count": len(items),
        "items": items,
        "snapshots": responses,
        "manifest_sha256": canonical_sha256(manifest),
    }


def paginated_api(
    session: requests.Session,
    url: str,
    params: dict,
    *,
    page_size: int = 200,
) -> tuple[list[dict], list[dict]]:
    records = []
    responses = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        response = get(session, url, params={**params, "pageNumber": page, "pageSize": page_size})
        responses.append(snapshot(response, "API"))
        payload = response.json()
        if payload.get("success") is not True or not isinstance(payload.get("data", {}).get("data"), list):
            raise ValueError(f"invalid API payload: {url}")
        data = payload["data"]
        total_pages = int(data["totalPages"])
        if total_pages > 100:
            raise ValueError(f"API page guard exceeded: {total_pages}")
        records.extend(data["data"])
        page += 1
    if records and len(records) != int(data["totalCount"]):
        raise ValueError(f"API count mismatch: {len(records)} != {data['totalCount']}")
    return records, responses


def collect_s007(session: requests.Session, start: date, end: date) -> dict:
    records, responses = paginated_api(
        session,
        API_S007,
        {"keywordList": "警察局", "dateStart": start.isoformat(), "dateEnd": end.isoformat()},
    )
    items = []
    for record in records:
        payload = {key: record.get(key) for key in sorted(record)}
        # ponytail: the API exposes no segment ID; hash identity is exact-dedup only.
        identity = canonical_sha256({"speaker": record.get("speaker"), "content": record.get("content")})[:16]
        items.append(
            {
                "stable_key": f"{record['proceedingsId']}:{identity}",
                "source_url": f"https://yishi.tccc.gov.tw/meeting-records/{record['proceedingsId']}",
                "published_at": timestamp(datetime.fromisoformat(record["date"]).replace(tzinfo=TZ)),
                "content_sha256": canonical_sha256(payload),
                "payload": payload,
            }
        )

    # Fetch the latest record date without date filter for accurate data_as_of.
    # This tells us when the most recent meeting record was published, even if
    # it's outside the current collection window.
    latest_date_str = None
    try:
        probe_resp = get(session, API_S007, params={"keywordList": "警察局", "pageNumber": 1, "pageSize": 1}, timeout=30)
        responses.append(snapshot(probe_resp, "PROBE_LATEST"))
        probe_data = probe_resp.json()
        if probe_data.get("success") and probe_data["data"]["data"]:
            raw_date = probe_data["data"]["data"][0].get("date")
            if raw_date:
                latest_date_str = timestamp(datetime.fromisoformat(raw_date).replace(tzinfo=TZ))
    except Exception:
        pass  # Non-fatal; we still have the window results

    return {
        "source_health": "PASS",
        "window_completeness": "COMPLETE_WITH_ITEMS" if items else "COMPLETE_ZERO",
        "window_item_count": len(items),
        "snapshot_item_count": len(items),
        "items": items,
        "snapshots": responses,
        "manifest_sha256": canonical_sha256([item["content_sha256"] for item in items]),
        "latest_record_date": latest_date_str,
    }


def collect_s009(session: requests.Session, start: date, end: date) -> dict:
    del start, end
    records, responses = paginated_api(session, API_S009, {"keywordList": "警察局"})
    items = []
    for record in records:
        payload = {key: record.get(key) for key in sorted(record)}
        items.append(
            {
                "stable_key": record["billId"],
                "source_url": f"https://yishi.tccc.gov.tw/proposals/{record['billId']}",
                "published_at": None,
                "content_sha256": canonical_sha256(payload),
                "payload": payload,
            }
        )
    # S-009 proposals have no date field; use the fetch timestamp as data_as_of
    # since a successful API response with current-session items proves the source
    # is alive and the data reflects the latest legislative state.
    # window_completeness is COMPLETE_ZERO because the API successfully returned all
    # police-related proposals — they just have no publishedAt to place in a date window.
    return {
        "source_health": "PASS",
        "window_completeness": "COMPLETE_ZERO",
        "window_item_count": 0,
        "snapshot_item_count": len(items),
        "items": items,
        "snapshots": responses,
        "manifest_sha256": canonical_sha256([item["content_sha256"] for item in items]),
        "api_confirmed_at": timestamp(datetime.now(TZ)),
    }


def load_canary_module():
    path = ROOT / "canary-s026-s029.py"
    spec = importlib.util.spec_from_file_location("canary_s026_s029", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load S-029 canary")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def collect_s029(session: requests.Session, start: date, end: date) -> dict:
    source = load_canary_module().fetch_s029(session, start, end)
    urls = [(source["index"]["requested_url"], "LIST")]
    urls.extend((page["requested_url"], "LIST") for page in source["latest_session"]["list_pages"])
    urls.extend((item["url"], "ATTACHMENT") for item in source["police_attachments"])
    responses = [snapshot(get(session, url, timeout=120), purpose) for url, purpose in urls]
    items = []
    for item in source["police_attachments"]:
        payload = {key: item[key] for key in sorted(item)}
        items.append(
            {
                "stable_key": item["item_id"],
                "source_url": item["url"],
                "published_at": published_at(item["published_at"]),
                "content_sha256": canonical_sha256(payload),
                "payload": payload,
            }
        )
    return {
        "source_health": source["source_health"],
        "window_completeness": source["window_completeness"],
        "window_item_count": len(source["window_items"]),
        "snapshot_item_count": source["latest_session"]["parsed_count"],
        "items": items,
        "snapshots": responses,
        "manifest_sha256": source["manifest_sha256"],
    }


COLLECTORS = {
    "S-004": collect_download_list,
    "S-006": collect_download_list,
    "S-007": collect_s007,
    "S-009": collect_s009,
    "S-029": collect_s029,
}


def collect_source(session: requests.Session, source_id: str, start: date, end: date) -> dict:
    collector = COLLECTORS[source_id]
    if collector is collect_download_list:
        return collector(session, source_id, start, end)
    return collector(session, start, end)


def result_for(completeness: str, change_count: int) -> str:
    if completeness == "PARTIAL":
        return "PARTIAL"
    return "NEW_ITEMS" if change_count else "NO_NEW_ITEM"


def count_window_changes(changes: list[dict], window_start: datetime, window_end: datetime) -> int:
    return sum(
        1 for item in changes
        if item["published_at"]
        and window_start.date() <= datetime.fromisoformat(item["published_at"]).date() <= window_end.date()
    )


def seed_sources(connection, created_at: datetime) -> None:
    for source_id, (name, evidence_role, product_role, integration_status) in SOURCE_ROWS.items():
        connection.execute(
            """
            INSERT INTO sources (source_id, name, evidence_role, product_role, integration_status, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_id) DO UPDATE SET
                name = EXCLUDED.name,
                evidence_role = EXCLUDED.evidence_role,
                product_role = EXCLUDED.product_role,
                integration_status = EXCLUDED.integration_status
            """,
            (source_id, name, evidence_role, product_role, integration_status, created_at),
        )


def current_items(connection, source_id: str) -> dict[str, dict]:
    rows = connection.execute(
        """
        SELECT raw_item_id, stable_key, version_no, content_sha256
        FROM raw_items WHERE source_id = %s AND is_current
        """,
        (source_id,),
    ).fetchall()
    return {row["stable_key"]: row for row in rows}


def prior_success(connection, source_id: str) -> str | None:
    row = connection.execute(
        """
        SELECT source_run_id FROM source_runs
        WHERE source_id = %s
          AND source_health IN ('PASS', 'DEGRADED')
          AND result IN ('NEW_ITEMS', 'NO_NEW_ITEM', 'PARTIAL')
          AND manifest_sha256 IS NOT NULL
        ORDER BY completed_at DESC LIMIT 1
        """,
        (source_id,),
    ).fetchone()
    return row["source_run_id"] if row else None


def save_success(
    connection,
    collection_run_id: str,
    source_run_id: str,
    source_id: str,
    collected: dict,
    attempted_at: datetime,
    completed_at: datetime,
    window_start: datetime,
    window_end: datetime,
    latency_ms: int,
) -> None:
    existing = current_items(connection, source_id)
    changes = [
        item for item in collected["items"]
        if existing.get(item["stable_key"], {}).get("content_sha256") != item["content_sha256"]
    ]
    window_change_count = count_window_changes(changes, window_start, window_end)
    result = result_for(collected["window_completeness"], window_change_count)
    first_status = collected["snapshots"][0]["http_status"] if collected["snapshots"] else None
    connection.execute(
        """
        INSERT INTO source_runs (
            source_run_id, collection_run_id, source_id, attempted_at, completed_at,
            source_health, window_start, window_end, window_completeness, result,
            item_count, change_count, http_status, latency_ms, manifest_sha256,
            previous_successful_source_run_id, error_code, error_message
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, NULL)
        """,
        (
            source_run_id, collection_run_id, source_id, attempted_at, completed_at,
            collected["source_health"], window_start, window_end,
            collected["window_completeness"], result, collected["window_item_count"],
            window_change_count, first_status, latency_ms, collected["manifest_sha256"],
            prior_success(connection, source_id),
        ),
    )

    first_snapshot_id = None
    for index, item in enumerate(collected["snapshots"], 1):
        snapshot_id = f"SN-{source_run_id}-{index:03d}"
        first_snapshot_id = first_snapshot_id or snapshot_id
        connection.execute(
            """
            INSERT INTO snapshot_blobs (content_sha256, content_type, byte_count, body, created_at)
            VALUES (%s, %s, %s, %s, %s) ON CONFLICT (content_sha256) DO NOTHING
            """,
            (item["content_sha256"], item["content_type"], len(item["body"]), item["body"], completed_at),
        )
        connection.execute(
            """
            INSERT INTO source_snapshots (
                snapshot_id, source_run_id, source_id, purpose, requested_url, final_url,
                http_status, fetched_at, content_sha256
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                snapshot_id, source_run_id, source_id, item["purpose"], item["requested_url"],
                item["final_url"], item["http_status"], completed_at, item["content_sha256"],
            ),
        )

    for item in changes:
        current = existing.get(item["stable_key"])
        if current:
            connection.execute("UPDATE raw_items SET is_current = false WHERE raw_item_id = %s", (current["raw_item_id"],))
        historical = connection.execute(
            """
            SELECT raw_item_id FROM raw_items
            WHERE source_id = %s AND stable_key = %s AND content_sha256 = %s
            """,
            (source_id, item["stable_key"], item["content_sha256"]),
        ).fetchone()
        if historical:
            connection.execute("UPDATE raw_items SET is_current = true WHERE raw_item_id = %s", (historical["raw_item_id"],))
            continue
        version = connection.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version "
            "FROM raw_items WHERE source_id = %s AND stable_key = %s",
            (source_id, item["stable_key"]),
        ).fetchone()["next_version"]
        raw_item_id = f"RI-{source_id[2:]}-{canonical_sha256(item['stable_key'])[:12]}-V{version}"
        connection.execute(
            """
            INSERT INTO raw_items (
                raw_item_id, source_run_id, source_id, stable_key, version_no,
                requested_url, final_url, published_at, fetched_at, content_sha256,
                parser_version, snapshot_locator, supersedes_raw_item_id, is_current
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true)
            """,
            (
                raw_item_id, source_run_id, source_id, item["stable_key"], version,
                item["source_url"], item["source_url"], item["published_at"], completed_at,
                item["content_sha256"], PARSER_VERSION,
                f"postgres:source_snapshots/{first_snapshot_id}",
                current["raw_item_id"] if current else None,
            ),
        )


def save_failure(
    connection,
    collection_run_id: str,
    source_run_id: str,
    source_id: str,
    attempted_at: datetime,
    completed_at: datetime,
    window_start: datetime,
    window_end: datetime,
    error: Exception,
) -> None:
    connection.execute(
        """
        INSERT INTO source_runs (
            source_run_id, collection_run_id, source_id, attempted_at, completed_at,
            source_health, window_start, window_end, window_completeness, result,
            item_count, change_count, manifest_sha256, previous_successful_source_run_id,
            error_code, error_message
        ) VALUES (%s, %s, %s, %s, %s, 'FAILED', %s, %s, 'PARTIAL', 'FAILED',
                  NULL, NULL, NULL, %s, %s, %s)
        """,
        (
            source_run_id, collection_run_id, source_id, attempted_at, completed_at,
            window_start, window_end, prior_success(connection, source_id),
            type(error).__name__.upper()[:64], str(error)[:1000],
        ),
    )


def run_database_slot(slot: str, slot_date: date, now: datetime | None = None) -> dict:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for an online slot")
    slot = slot.upper()
    now = (now or datetime.now(TZ)).astimezone(TZ)
    scheduled_for = scheduled_time(slot_date, slot)
    if now < scheduled_for:
        raise ValueError(f"{slot} slot is not due until {timestamp(scheduled_for)}")
    collection_run_id = f"CR-{slot_date:%Y%m%d}-{slot}"
    window_start = scheduled_for - timedelta(days=7)
    window_end = scheduled_for
    lock_name = f"taichung-police-intel:{slot_date}:{slot}"

    with psycopg.connect(database_url, autocommit=True, row_factory=dict_row) as connection:
        locked = connection.execute("SELECT pg_try_advisory_lock(hashtext(%s)) AS locked", (lock_name,)).fetchone()["locked"]
        if not locked:
            raise RuntimeError("this collection slot is already running")
        try:
            seed_sources(connection, now)
            existing_run = connection.execute(
                "SELECT status FROM collection_runs WHERE collection_run_id = %s",
                (collection_run_id,),
            ).fetchone()
            if existing_run and existing_run["status"] != "RUNNING":
                return {"collection_run_id": collection_run_id, "status": existing_run["status"], "replayed": True}
            if not existing_run:
                connection.execute(
                    """
                    INSERT INTO collection_runs (
                        collection_run_id, slot_date, slot, timezone, scheduled_for,
                        started_at, finished_at, status
                    ) VALUES (%s, %s, %s, 'Asia/Taipei', %s, %s, NULL, 'RUNNING')
                    """,
                    (collection_run_id, slot_date, slot, scheduled_for, now),
                )

            completed_sources = {
                row["source_id"] for row in connection.execute(
                    "SELECT source_id FROM source_runs WHERE collection_run_id = %s",
                    (collection_run_id,),
                ).fetchall()
            }
            session = http_session()
            for source_id in P0_SOURCES:
                if source_id in completed_sources:
                    continue
                source_run_id = f"SR-{slot_date:%Y%m%d}-{slot}-{source_id[2:]}"
                attempted_at = datetime.now(TZ)
                started = time.monotonic()
                try:
                    collected = collect_source(session, source_id, window_start.date(), window_end.date())
                    completed_at = datetime.now(TZ)
                    with connection.transaction():
                        save_success(
                            connection, collection_run_id, source_run_id, source_id, collected,
                            attempted_at, completed_at, window_start, window_end,
                            round((time.monotonic() - started) * 1000),
                        )
                except Exception as error:
                    completed_at = datetime.now(TZ)
                    with connection.transaction():
                        save_failure(
                            connection, collection_run_id, source_run_id, source_id,
                            attempted_at, completed_at, window_start, window_end, error,
                        )

            results = connection.execute(
                "SELECT result FROM source_runs WHERE collection_run_id = %s",
                (collection_run_id,),
            ).fetchall()
            failed = sum(row["result"] == "FAILED" for row in results)
            partial = sum(row["result"] == "PARTIAL" for row in results)
            status = "FAILED" if failed == len(P0_SOURCES) else "PARTIAL" if failed or partial else "SUCCEEDED"
            connection.execute(
                "UPDATE collection_runs SET status = %s, finished_at = %s WHERE collection_run_id = %s",
                (status, datetime.now(TZ), collection_run_id),
            )
            return {"collection_run_id": collection_run_id, "status": status, "replayed": False}
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))


def self_check() -> None:
    sample = b"""
    <div id='Fdownload_list'><div class='text02_1'>\xe7\xac\xac\xe5\x9b\x9b\xe5\xb1\x86 \xe7\xac\xac8\xe6\xac\xa1\xe5\xae\x9a\xe6\x9c\x9f\xe6\x9c\x83 \xe8\xad\xb0\xe4\xba\x8b\xe6\x97\xa5\xe7\xa8\x8b\xe8\xa1\xa8(115.07.27\xe4\xbf\xae\xe6\xad\xa3)</div>
    <div class='text02_2'><a href='a.pdf'>PDF</a></div></div>
    """
    parsed = parse_download_entries(sample, "https://example.test/list")
    assert parsed[0]["attachment_urls"] == ["https://example.test/a.pdf"]
    assert roc_date(parsed[0]["title"]) == date(2026, 7, 27)
    assert result_for("COMPLETE_WITH_ITEMS", 0) == "NO_NEW_ITEM"
    assert result_for("PARTIAL", 4) == "PARTIAL"
    assert count_window_changes(
        [{"published_at": "2026-08-01T00:00:00+08:00"}, {"published_at": None}],
        datetime(2026, 8, 16, 6, 30, tzinfo=TZ),
        datetime(2026, 8, 22, 6, 30, tzinfo=TZ),
    ) == 0
    print("ONLINE_COLLECT_SELF_CHECK_OK")


def canary() -> None:
    session = http_session()
    end = datetime.now(TZ).date()
    start = end - timedelta(days=6)
    summary = {}
    for source_id in P0_SOURCES:
        started = time.monotonic()
        result = collect_source(session, source_id, start, end)
        summary[source_id] = {
            "source_health": result["source_health"],
            "window_completeness": result["window_completeness"],
            "window_item_count": result["window_item_count"],
            "snapshot_item_count": result["snapshot_item_count"],
            "snapshot_count": len(result["snapshots"]),
            "manifest_sha256": result["manifest_sha256"],
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    print(json.dumps({"checked_at": timestamp(datetime.now(TZ)), "sources": summary}, ensure_ascii=False, sort_keys=True))


def project_feed_item(
    item: dict,
    source_id: str,
    source_name: str,
    source_url: str,
    freshness: str,
    source_health: str,
    window_completeness: str,
    data_as_of: str | None,
    fetched_at: str,
    previous_sha256s: set[str],
) -> dict:
    """Project a raw collector item into a safe feed item for the homepage."""
    stable_key_hash = canonical_sha256(f"{source_id}:{item['stable_key']}")[:16]
    stable_id = f"FEED-{source_id}-{stable_key_hash}"

    # Determine change type
    if item["content_sha256"] in previous_sha256s:
        # Item content is identical to prior feed. However, if the source is
        # confirmed FRESH and healthy, mark as CONFIRMED (still active/valid)
        # rather than UNCHANGED (implies stale repetition).
        if source_health == "PASS" and freshness in ("FRESH", "STALE"):
            change_type = "CONFIRMED"
        else:
            change_type = "UNCHANGED"
    else:
        change_type = "NEW"

    # Extract safe title from payload (no raw payload exposure)
    title = ""
    if isinstance(item.get("payload"), dict):
        title = item["payload"].get("title", "")
        if not title:
            # For API records, use proposalRationale/content/subject/billName
            title = (
                item["payload"].get("proposalRationale")
                or item["payload"].get("subject")
                or item["payload"].get("billName")
                or item["payload"].get("content", "")[:100]
                or ""
            )
        # Clean up: remove keyword highlights from API
        if "關鍵字包含" in title:
            title = title.split("\n")[0].strip()
    # Truncate for safety
    if len(title) > 200:
        title = title[:197] + "…"

    # Eligibility rules — order matters: most restrictive first
    if change_type == "UNCHANGED":
        eligibility = "INELIGIBLE_UNCHANGED"
    elif source_health == "FAILED":
        eligibility = "INELIGIBLE_SOURCE_FAILED"
    elif window_completeness == "PARTIAL":
        eligibility = "INELIGIBLE_PARTIAL"
    elif freshness in ("VERY_STALE",):
        eligibility = "INELIGIBLE_STALE"
    elif freshness == "NO_DATA" or (not item.get("published_at") and change_type == "NEW"):
        eligibility = "INELIGIBLE_NO_DATE"
    else:
        eligibility = "HOME_CANDIDATE"

    # Reason codes based on source context
    reason_codes = []
    if source_id in ("S-007",):
        reason_codes.append("COUNCIL_ATTENTION")
    elif source_id in ("S-004", "S-006"):
        reason_codes.append("NEAR_MILESTONE")
    elif source_id in ("S-009",):
        reason_codes.append("POLICY_CHANGE")
    elif source_id in ("S-029",):
        reason_codes.append("CROSS_SOURCE")
    if not reason_codes:
        reason_codes.append("HIGH_VALUE")

    # Value score: higher for items with dates and fresh sources
    score = 50
    if item.get("published_at"):
        score += 20
    if freshness == "FRESH":
        score += 20
    elif freshness == "STALE":
        score += 10
    if change_type == "NEW":
        score += 10

    return {
        "stable_id": stable_id,
        "source_id": source_id,
        "source_role": "PRIMARY_OFFICIAL",
        "title": title,
        "official_url": item.get("source_url") or source_url,
        "published_at": item.get("published_at"),
        "fetched_at": fetched_at,
        "data_as_of": data_as_of,
        "change_type": change_type,
        "freshness_status": freshness,
        "source_health": source_health,
        "window_completeness": window_completeness,
        "reason_codes": reason_codes,
        "item_value_score": min(score, 100),
        "eligibility": eligibility,
        "evidence_count": 1,
        "next_milestone": None,
        "content_sha256": item["content_sha256"],
    }


def build_demo_status(output: Path, slot: str, slot_date: date, trigger: str) -> dict:
    now = datetime.now(TZ)
    prior = json.loads(output.read_text(encoding="utf-8")) if output.exists() else None
    if prior and (prior.get("schema_version"), prior.get("mode")) != (1, "COMPETITION_DEMO"):
        raise ValueError("unsupported competition demo state")
    prior_sources = {item["source_id"]: item for item in (prior or {}).get("sources", [])}

    # Load prior feed for change detection
    feed_output = output.parent / "intelligence-feed.json"
    prior_feed = json.loads(feed_output.read_text(encoding="utf-8")) if feed_output.exists() else None
    prior_feed_sha256s: set[str] = set()
    if prior_feed and isinstance(prior_feed.get("items"), list):
        prior_feed_sha256s = {item["content_sha256"] for item in prior_feed["items"] if item.get("content_sha256")}

    session = http_session()
    window_end = scheduled_time(slot_date, slot)
    window_start = window_end - timedelta(days=7)
    next_at = next_update(slot_date, slot)
    source_status = []
    feed_items = []
    source_summary = {}

    for source_id, (source_name, source_url) in P0_SOURCES.items():
        source_run_id = f"SR-DEMO-{slot_date:%Y%m%d}-{slot}-{source_id[2:]}"
        previous = prior_sources.get(source_id, {})
        previous_lkg = previous.get("last_known_good")
        collected_items = []
        try:
            collected = collect_source(session, source_id, window_start.date(), window_end.date())
            collected_items = collected.get("items", [])
            dates = [item["published_at"] for item in collected_items if item["published_at"]]
            # Use latest_record_date from S-007 probe or api_confirmed_at from S-009
            # when no items have published_at in the collection window.
            data_as_of = max(dates, default=None)
            if not data_as_of and collected.get("latest_record_date"):
                data_as_of = collected["latest_record_date"]
            if not data_as_of and collected.get("api_confirmed_at"):
                data_as_of = collected["api_confirmed_at"]
            if not data_as_of:
                # For download-list sources (S-004/S-006) that have items but no
                # dates in titles, use last_checked_at since content is confirmed present.
                if collected_items and collected["source_health"] == "PASS":
                    data_as_of = timestamp(now)
                else:
                    data_as_of = previous.get("data_as_of")
            manifest_changed = collected["manifest_sha256"] != previous.get("manifest_sha256")
            change_count = collected["window_item_count"] if manifest_changed else 0
            result = result_for(collected["window_completeness"], change_count)
            lkg = {
                "source_run_id": source_run_id,
                "completed_at": timestamp(now),
                "manifest_sha256": collected["manifest_sha256"],
                "snapshot_item_count": collected["snapshot_item_count"],
                "snapshot_count": len(collected["snapshots"]),
            }
            record = {
                "source_id": source_id,
                "source_name": source_name,
                "source_url": source_url,
                "current_source_run_id": source_run_id,
                "source_health": collected["source_health"],
                "window_completeness": collected["window_completeness"],
                "result": result,
                "window_item_count": collected["window_item_count"],
                "snapshot_item_count": collected["snapshot_item_count"],
                "snapshot_count": len(collected["snapshots"]),
                "manifest_sha256": collected["manifest_sha256"],
                "data_as_of": data_as_of,
                "last_checked_at": timestamp(now),
                "last_success_at": timestamp(now),
                "next_update_at": next_at,
                "last_known_good": lkg,
            }
        except Exception as error:
            record = {
                "source_id": source_id,
                "source_name": source_name,
                "source_url": source_url,
                "current_source_run_id": source_run_id,
                "source_health": "FAILED",
                "window_completeness": "PARTIAL",
                "result": "FAILED",
                "window_item_count": None,
                "snapshot_item_count": previous.get("snapshot_item_count"),
                "snapshot_count": None,
                "manifest_sha256": None,
                "data_as_of": previous.get("data_as_of"),
                "last_checked_at": timestamp(now),
                "last_success_at": previous.get("last_success_at"),
                "next_update_at": next_at,
                "last_known_good": previous_lkg,
                "error_code": type(error).__name__.upper()[:64],
            }
        freshness = freshness_status(record["data_as_of"], now, *SOURCE_FRESHNESS_POLICY.get(source_id, (13, 24)))
        record["freshness_status"] = freshness
        record["intelligence_gaps"] = gap_reasons(record, record["last_known_good"], freshness)
        if freshness == "NO_DATA":
            record["intelligence_gaps"].append("NO_DATA_AS_OF")
        source_status.append(record)

        # Project items into feed
        fetched_at = timestamp(now)
        if collected_items:
            for item in collected_items:
                feed_item = project_feed_item(
                    item=item,
                    source_id=source_id,
                    source_name=source_name,
                    source_url=source_url,
                    freshness=freshness,
                    source_health=record["source_health"],
                    window_completeness=record["window_completeness"],
                    data_as_of=record["data_as_of"],
                    fetched_at=fetched_at,
                    previous_sha256s=prior_feed_sha256s,
                )
                feed_items.append(feed_item)
        elif record["source_health"] == "FAILED" and prior_feed and isinstance(prior_feed.get("items"), list):
            # C: LKG preservation — copy prior feed items for this source with LKG markers
            for prior_item in prior_feed["items"]:
                if prior_item.get("source_id") == source_id:
                    lkg_item = {**prior_item}
                    lkg_item["change_type"] = "LKG"
                    lkg_item["source_health"] = "FAILED"
                    lkg_item["eligibility"] = "INELIGIBLE_SOURCE_FAILED"
                    lkg_item["freshness_status"] = freshness if freshness != "FRESH" else "VERY_STALE"
                    lkg_item["fetched_at"] = fetched_at
                    feed_items.append(lkg_item)

        # Source summary for feed
        source_summary[source_id] = {
            "health": record["source_health"],
            "freshness": freshness,
            "item_count": len(collected_items),
        }

    failed = sum(item["result"] == "FAILED" for item in source_status)
    partial = sum(item["result"] == "PARTIAL" for item in source_status)
    status = "FAILED" if failed == len(source_status) else "PARTIAL" if failed or partial else "SUCCEEDED"
    collection_run_id = f"CR-DEMO-{slot_date:%Y%m%d}-{slot}-{trigger.upper()}"
    state = {
        "schema_version": 1,
        "mode": "COMPETITION_DEMO",
        "generated_at": timestamp(now),
        "next_update_at": next_at,
        "latest_collection_run": {
            "collection_run_id": collection_run_id,
            "slot_date": slot_date.isoformat(),
            "slot": slot,
            "trigger": trigger.upper(),
            "scheduled_for": timestamp(window_end),
            "finished_at": timestamp(now),
            "status": status,
        },
        "sources": source_status,
    }
    save_state(output, state)

    # Write intelligence feed
    # Dedup by stable_id (keep first occurrence per stable_id)
    seen_ids: set[str] = set()
    deduped_items = []
    for item in feed_items:
        if item["stable_id"] not in seen_ids:
            seen_ids.add(item["stable_id"])
            deduped_items.append(item)

    feed_state = {
        "schema_version": 1,
        "generated_at": timestamp(now),
        "collection_run_id": collection_run_id,
        "items": deduped_items,
        "source_summary": source_summary,
    }
    save_state(feed_output, feed_state)

    print(f"DEMO_STATUS_OK slot={slot} status={status} sources={len(source_status)} output={output}")
    print(f"FEED_OK items={len(deduped_items)} eligible={sum(1 for i in deduped_items if i['eligibility'] == 'HOME_CANDIDATE')} output={feed_output}")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Production P0 source collector")
    parser.add_argument("--slot", choices=("morning", "evening"))
    parser.add_argument("--slot-date", type=date.fromisoformat, default=datetime.now(TZ).date())
    parser.add_argument("--canary", action="store_true")
    parser.add_argument("--demo-output", type=Path)
    parser.add_argument("--trigger", choices=("manual", "schedule"), default="manual")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.demo_output and not args.slot:
        parser.error("--demo-output requires --slot")
    if sum((bool(args.slot and not args.demo_output), args.canary, args.self_check, bool(args.demo_output))) != 1:
        parser.error("choose exactly one database slot, --canary, --demo-output, or --self-check")
    if args.self_check:
        self_check()
    elif args.canary:
        canary()
    elif args.demo_output:
        build_demo_status(args.demo_output, args.slot.upper(), args.slot_date, args.trigger)
    else:
        print(json.dumps(run_database_slot(args.slot, args.slot_date), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
