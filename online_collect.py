from __future__ import annotations

import argparse
import csv
from email.utils import parsedate_to_datetime
import importlib.util
import io
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader
from psycopg.rows import dict_row
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from collect import (
    P0_SOURCES,
    ROOT,
    SOURCE_FRESHNESS_POLICY,
    SOURCE_POLICY_BINDING,
    TZ,
    canonical_sha256,
    catalog_rows,
    freshness_status,
    gap_reasons,
    load_source_catalog,
    next_update,
    resolve_collection_date,
    save_state,
    scheduled_time,
    timestamp,
)
from intel_v2.detail_recheck import classify_observation, plan_recheck
from intel_v2.detail_recheck_budget import (
    budget_state_for_storage,
    plan_recheck_budget,
    recheck_budget_policy,
    record_recheck_budget,
)
from intel_v2.detail_recheck_http import recheck_detail
from intel_v2.lkg_age import last_known_good_age
from intel_v2.located_facts import validate_document_url


USER_AGENT = "TaichungPoliceIntel/0.2 (+public-source-monitor)"
DETAIL_RECHECK_INTERVAL_HOURS = 24
DETAIL_RECHECK_MAX_PER_RUN = 1
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 3
API_S007 = "https://yishi.tccc.gov.tw/api/ProceedingsBackWeb/FrontList"
API_S009 = "https://yishi.tccc.gov.tw/api/Proposal/FrontList"
PARSER_VERSION = "p0-live-1"
MAX_NEWS_LIST_PAGES = 4
MAX_NEWS_LIST_PAGES_HARD_LIMIT = 40
CANARY_MAX_DETAILS = 5
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
        # Return the final response after the bounded retry budget. Otherwise
        # urllib3 hides an upstream 503 inside a retry-exhaustion exception.
        raise_on_status=False,
    )
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"})
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def get(
    session: requests.Session,
    url: str,
    *,
    source_id: str | None = None,
    **kwargs,
) -> requests.Response:
    """Fetch one catalog-bound URL without following an unapproved redirect."""
    requested_url = url
    timeout = kwargs.pop("timeout", 60)
    for _ in range(MAX_REDIRECTS + 1):
        if source_id:
            validate_document_url(source_id, url)
        response = session.get(
            url,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        )
        final_url = str(getattr(response, "url", url) or url)
        if source_id:
            validate_document_url(source_id, final_url)
        if response.status_code in REDIRECT_STATUSES:
            location = response.headers.get("location")
            response.close()
            if not location:
                raise ValueError("redirect response has no location")
            url = urllib.parse.urljoin(url, location)
            continue
        response.raise_for_status()
        response._govintel_requested_url = requested_url
        return response
    raise ValueError("redirect budget exhausted")


def source_failure_code(error: Exception) -> str:
    """Preserve a received HTTP status separately from connection failures."""
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool) and 100 <= status <= 599:
        return f"HTTP_{status}"
    return type(error).__name__.upper()[:64]


def snapshot(response: requests.Response, purpose: str) -> dict:
    return {
        "purpose": purpose,
        "requested_url": getattr(response, "_govintel_requested_url", response.request.url),
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
    # Parse Gregorian dates first; otherwise the ROC matcher can start at the
    # second digit of a four-digit year (for example, 2026 -> 026).
    gregorian = re.search(
        r"(?<!\d)(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?(?!\d)",
        value,
    )
    if gregorian:
        year, month, day = map(int, gregorian.groups())
        if not 1912 <= year <= 2200:
            return None
        try:
            return date(year, month, day)
        except ValueError:
            return None

    roc = re.search(
        r"(?<!\d)(\d{2,3})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?(?!\d)",
        value,
    )
    if not roc:
        return None
    year, month, day = map(int, roc.groups())
    if not 1 <= year <= 289:
        return None
    try:
        return date(year + 1911, month, day)
    except ValueError:
        return None


def published_at(value: str | date | None) -> str | None:
    if value is None:
        return None
    parsed = value if isinstance(value, date) else date.fromisoformat(value[:10])
    return timestamp(datetime.combine(parsed, datetime.min.time(), TZ))


def official_record_timestamp(value: str) -> str:
    """Interpret a naive council clock in Taipei; preserve an explicit offset."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return timestamp(parsed.astimezone(TZ))


def attachment_revision_evidence(body: bytes, url: str) -> dict | None:
    """Read an explicitly labelled revision date from an already fetched PDF.

    A meeting date, PDF metadata date, filename, and HTTP timestamp are not
    publication evidence. A malformed, oversized or unlabelled document stays
    undated; successful attachment retrieval remains an availability fact.
    """
    if not body.startswith(b"%PDF-") or len(body) > 8 * 1024 * 1024:
        return None
    try:
        reader = PdfReader(io.BytesIO(body), strict=True)
        if reader.is_encrypted or not reader.pages:
            return None
        text = reader.pages[0].extract_text(extraction_mode="layout") or ""
    except Exception:
        return None
    if len(text) > 64 * 1024:
        return None
    # Match a date with its explicit revision label on the same line. In
    # particular, dates in the question-order grid cannot establish this date.
    date_pattern = r"(?<!\d)(?:\d{4}|\d{2,3})[^\S\n]*[-/.年][^\S\n]*\d{1,2}[^\S\n]*[-/.月][^\S\n]*\d{1,2}[^\S\n]*日?(?!\d)"
    patterns = (
        rf"({date_pattern})[^\S\n]*(?:第[^\S\n]*\d+[^\S\n]*次[^\S\n]*)?(?:修正|修訂|更新)",
        rf"(?:修正|修訂|更新)(?:日期|時間)?[^\S\n]*[:：]?[^\S\n]*({date_pattern})",
    )
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            revision_date = roc_date(match.group(1))
            if revision_date:
                matches.append((revision_date, match.group(0)))
    if not matches:
        return None
    revision_date, excerpt = max(matches, key=lambda value: value[0])
    return {
        "date_basis": "OFFICIAL_DOCUMENT_REVISION_DATE",
        "document_revision_at": published_at(revision_date),
        "official_url": url,
        "content_sha256": canonical_bytes_sha256(body),
        "page_number": 1,
        "excerpt": excerpt,
    }


def public_date_evidence(evidence: dict) -> dict:
    """Expose date provenance metadata; keep document excerpts in raw evidence."""
    return {
        key: evidence[key]
        for key in ("date_basis", "document_revision_at", "official_url", "content_sha256", "page_number")
        if key in evidence
    }


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
    listing = get(session, source_url, source_id=source_id)
    responses = [snapshot(listing, "LIST")]
    entries = parse_download_entries(listing.content, listing.url)
    items = []
    for entry in entries:
        attachments = []
        revision_evidence = []
        for url in entry["attachment_urls"]:
            response = get(session, url, source_id=source_id, timeout=120)
            responses.append(snapshot(response, "ATTACHMENT"))
            attachments.append(
                {
                    "url": response.url,
                    "content_type": response.headers.get("content-type") or "application/octet-stream",
                    "byte_count": len(response.content),
                    "content_sha256": canonical_bytes_sha256(response.content),
                }
            )
            if source_id == "S-006":
                evidence = attachment_revision_evidence(response.content, response.url)
                if evidence:
                    revision_evidence.append(evidence)
        item_date = roc_date(entry["title"])
        payload = {"title": entry["title"], "attachments": attachments}
        stable_path = urllib.parse.urlparse(entry["attachment_urls"][0]).path
        item = {
            "stable_key": Path(stable_path).stem,
            "source_url": attachments[0]["url"],
            "published_at": published_at(item_date),
            "content_sha256": canonical_sha256(payload),
            "payload": payload,
        }
        if revision_evidence:
            evidence = max(revision_evidence, key=lambda value: value["document_revision_at"])
            item.update(
                document_revision_at=evidence["document_revision_at"],
                date_basis=evidence["date_basis"],
                date_evidence=evidence,
            )
        items.append(item)

    # A version's explicit revision date supports version-window coverage and
    # data_as_of; it never becomes the document's first publication time.
    item_times = [item.get("document_revision_at") or item["published_at"] for item in items]
    dated = [date.fromisoformat(value[:10]) for value in item_times if value]
    window_items = [
        item for item, value in zip(items, item_times)
        if value and start <= date.fromisoformat(value[:10]) <= end
    ]
    if len(dated) != len(items):
        # An undated attachment cannot be placed outside the requested window.
        # Successful retrieval is evidence of availability, not a zero count.
        completeness = "PARTIAL"
    elif window_items:
        completeness = "COMPLETE_WITH_ITEMS"
    elif dated and max(dated) < start:
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
    source_id: str | None = None,
    page_size: int = 200,
) -> tuple[list[dict], list[dict]]:
    records = []
    responses = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        response = get(
            session,
            url,
            source_id=source_id,
            params={**params, "pageNumber": page, "pageSize": page_size},
        )
        responses.append(snapshot(response, "API"))
        payload = response.json()
        if payload.get("success") is not True or not isinstance(payload.get("data", {}).get("data"), list):
            raise ValueError(f"invalid API payload: {url}")
        data = payload["data"]
        total_pages = data["totalPages"]
        total_count = data["totalCount"]
        if type(total_pages) is not int or not 0 <= total_pages <= 100:
            raise ValueError(f"API page guard exceeded: {total_pages}")
        if type(total_count) is not int or total_count < 0:
            raise ValueError(f"invalid API totalCount: {total_count}")
        records.extend(data["data"])
        page += 1
    if len(records) != total_count:
        raise ValueError(f"API count mismatch: {len(records)} != {total_count}")
    return records, responses


def collect_s007(session: requests.Session, start: date, end: date) -> dict:
    records, responses = paginated_api(
        session,
        API_S007,
        {"keywordList": "警察局", "dateStart": start.isoformat(), "dateEnd": end.isoformat()},
        source_id="S-007",
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
                "published_at": official_record_timestamp(record["date"]),
                "date_basis": "OFFICIAL_API_RECORD_DATE",
                "content_sha256": canonical_sha256(payload),
                "payload": payload,
            }
        )

    # A bounded unfiltered page supplies the latest *observed* official record
    # date even when the window is empty. Do not assume its first row is sorted
    # newest-first, or turn this sample into a complete-inventory claim.
    latest_date_str = None
    try:
        probe_resp = get(
            session,
            API_S007,
            source_id="S-007",
            params={"keywordList": "警察局", "pageNumber": 1, "pageSize": 20},
            timeout=30,
        )
        responses.append(snapshot(probe_resp, "PROBE_LATEST"))
        probe_data = probe_resp.json()
        if probe_data.get("success") is True and isinstance(probe_data.get("data", {}).get("data"), list):
            observed_dates = [
                official_record_timestamp(record["date"])
                for record in probe_data["data"]["data"] if record.get("date")
            ]
            latest_date_str = max(observed_dates, default=None)
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
        "latest_record_date_scope": "OBSERVED_API_PAGE" if latest_date_str else None,
    }


def collect_s009(session: requests.Session, start: date, end: date) -> dict:
    del start, end
    records, responses = paginated_api(
        session, API_S009, {"keywordList": "警察局"}, source_id="S-009"
    )
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
    # The official proposal schema has no verifiable publication date. A complete
    # API inventory establishes availability, but cannot place nonempty proposals
    # inside or outside this date window. Observation time belongs only in
    # last_checked_at; it must not become a source publication date.
    return {
        "source_health": "PASS",
        "window_completeness": "PARTIAL" if items else "COMPLETE_ZERO",
        "window_item_count": 0,
        "snapshot_item_count": len(items),
        "items": items,
        "snapshots": responses,
        "manifest_sha256": canonical_sha256([item["content_sha256"] for item in items]),
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
    # Preserve the bytes already validated by the canary. Fetching each URL a
    # second time both doubles the official traffic and can make the stored
    # snapshot disagree with the attachment hash in this observation.
    responses = []
    source = load_canary_module().fetch_s029(
        session, start, end,
        on_response=lambda response, purpose: responses.append(snapshot(response, purpose)),
    )
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


# Candidate official news lists use a list-first rule: only a new or changed
# list row fetches its detail page. They stay outside P0 publication until the
# catalog promotion gates have fresh live evidence.
NEWS_LIST_SOURCES = {
    "S-001": {
        "name": "臺中市政府警察局警政新聞",
        "pages_candidate": True,
        "list_url": "https://www.police.taichung.gov.tw/ch/home.jsp?id=1&parentpath=0&mcustomize=news_list.jsp",
        "fallback_list_url": "https://www.police.taichung.gov.tw/ch/home.jsp?id=1",
        "id_pattern": r"news_view\.jsp[^\"']*dataserno=(\d+)",
    },
    "S-019": {
        "name": "臺中市政府市政會議紀錄與專案報告",
        "pages_candidate": True,
        "list_url": "https://www.rdec.taichung.gov.tw/12047/12142/12186",
        "id_pattern": r"/(\d+)/post\b",
    },
    "S-032": {
        "name": "臺中市政府交通局最新消息",
        "pages_candidate": True,
        "list_url": "https://www.traffic.taichung.gov.tw/news/index.asp?Parser=9,4,20",
        "id_pattern": r"index-1\.asp\?Parser=9,4,20,,,,(\d+)",
    },
    "S-033": {
        "name": "臺中市政府市政新聞",
        "list_url": "https://www.taichung.gov.tw/10179/564770/rss?nodeId=9962",
        "format": "rss",
    },
    "S-031": {
        "name": "臺中市政府消防局即時災情",
        "list_url": "https://www.fire.taichung.gov.tw/caselist/index.asp?Parser=99,8,226",
        "format": "fire_live",
    },
}


def get_news_listing(session: requests.Session, source_id: str) -> requests.Response:
    config = NEWS_LIST_SOURCES[source_id]
    try:
        return get(session, config["list_url"], source_id=source_id)
    except (ConnectionError, requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.HTTPError) as error:
        # The bounded candidate transport returns a real HTTP error instead of
        # an adapter retry exception. Only transient failures may use the
        # catalog-bound alternate list; access denials must fail closed.
        if isinstance(error, requests.exceptions.SSLError):
            raise
        if isinstance(error, requests.exceptions.HTTPError):
            status = error.response.status_code if error.response is not None else None
            if status not in (429, 500, 502, 503, 504):
                raise
        fallback_url = config.get("fallback_list_url")
        if not fallback_url:
            raise
        return get(session, fallback_url, source_id=source_id)


def parse_news_list(html: bytes, base_url: str, id_pattern: str, *, strict_same_page_ids: bool = False) -> list[dict]:
    """Extract rows; opt-in strict receipts reject conflicting IDs before dedup."""
    if type(strict_same_page_ids) is not bool:
        raise ValueError("strict_same_page_ids must be boolean")
    soup = BeautifulSoup(html, "html.parser")
    pattern = re.compile(id_pattern)
    seen: dict[str, dict] = {}
    for anchor in soup.find_all("a", href=True):
        # Site navigation can contain article-shaped URLs (including the same
        # ID as a dated list row). It is not evidence of a list item/date.
        if anchor.find_parent(["nav", "header", "footer", "aside"]):
            continue
        match = pattern.search(anchor["href"])
        if not match:
            continue
        stable_key = match.group(1)
        row = anchor.find_parent(["li", "tr", "dd", "div"]) or anchor.parent or anchor
        row_text = " ".join(row.stripped_strings)
        candidate = {
            "stable_key": stable_key,
            "title": re.sub(r"\s+", " ", anchor.get_text(strip=True)),
            "detail_url": urllib.parse.urljoin(base_url, anchor["href"]),
            "published": roc_date(row_text),
        }
        if stable_key not in seen:
            seen[stable_key] = candidate
        elif strict_same_page_ids and candidate != seen[stable_key]:
            raise ValueError("conflicting stable row within one list page")
    entries = list(seen.values())
    if not entries:
        raise ValueError("news list has no parseable entries")
    return entries


def _list_page_identity(url: str) -> tuple[tuple, int] | None:
    """Require an explicit, unique positive page and identical list parameters."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.fragment:
        return None
    try:
        port = parts.port or 443
    except ValueError:
        return None
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    pages = [(key, value) for key, value in query if key.lower() == "page"]
    if len(pages) != 1 or not re.fullmatch(r"[1-9]\d{0,5}", pages[0][1]):
        return None
    if len({key.lower() for key, _ in query}) != len(query):
        return None
    parameters = tuple(sorted((key, value) for key, value in query if key.lower() != "page"))
    identity = (parts.scheme, parts.hostname.lower(), port, parts.path, pages[0][0], parameters)
    return identity, int(pages[0][1])


def _traffic_list_page_identity(url: str) -> tuple[tuple, int] | None:
    """S032's exact list Parser grammar; article Parsers are not list cursors."""
    parts = urllib.parse.urlsplit(url)
    configured = urllib.parse.urlsplit(NEWS_LIST_SOURCES["S-032"]["list_url"])
    try:
        safe_origin = (parts.scheme, parts.hostname, parts.port or 443, parts.path)
    except ValueError:
        return None
    if safe_origin != ("https", configured.hostname, 443, configured.path) or parts.username or parts.password or parts.fragment:
        return None
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if len(query) != 1 or query[0][0] != "Parser":
        return None
    fields = query[0][1].split(",")
    if fields == ["9", "4", "20"]:
        number = 1
    elif (len(fields) == 11 and fields[:3] == ["9", "4", "20"]
          and all(value == "" for value in fields[3:10])
          and re.fullmatch(r"[1-9]\d{0,5}", fields[10])):
        number = int(fields[10])
    else:
        return None
    return safe_origin + ("Parser=9,4,20",), number


def _traffic_news_list_next(soup: BeautifulSoup, base_url: str) -> tuple[str | None, bool]:
    """Resolve every S032 pager control before trusting a clamped last-page Next."""
    current = _traffic_list_page_identity(base_url)
    assert current is not None
    next_targets = []
    last_numbers = set()
    controls = []
    for control in soup.find_all(["a", "link", "button"]):
        labels = [" ".join(control.stripped_strings).strip().lower(),
                  str(control.get("title") or "").strip().lower(),
                  str(control.get("aria-label") or "").strip().lower()]
        rel = {str(value).lower() for value in control.get("rel", [])}
        classes = {str(value).lower() for value in control.get("class", [])}
        is_next = ("next" in rel or any("next" in value for value in classes)
            or any(any(token in value for token in ("下一頁", "下一页", "下頁", "下页"))
                   or re.search(r"\bnext(?:\s+page)?\b", value) for value in labels if value))
        is_last = "last" in rel or any(any(token in value for token in ("最後一頁", "最后一页", "末頁", "末页"))
            or re.search(r"\blast(?:\s+page)?\b", value) for value in labels if value)
        is_first = "first" in rel or any("第一頁" in value or "第一页" in value or re.search(r"\bfirst(?:\s+page)?\b", value)
            for value in labels if value)
        is_previous = bool(rel & {"prev", "previous"}) or any("上一頁" in value or "上一页" in value or re.search(r"\bprev(?:ious)?(?:\s+page)?\b", value)
            for value in labels if value)
        numbered = [value for value in labels if re.fullmatch(r"(?:第\s*)?\d{1,4}\s*(?:頁|页)?", value)]
        if not (is_next or is_last or is_first or is_previous or numbered):
            continue
        href = str(control.get("href") or "").strip()
        target_url = urllib.parse.urljoin(base_url, href) if href else None
        target = _traffic_list_page_identity(target_url) if target_url else None
        if target is None or target[0] != current[0]:
            return None, True
        if any(int(re.search(r"\d+", label).group()) != target[1] for label in numbered):
            return None, True
        if is_first and target[1] != 1 or is_previous and target[1] != max(1, current[1] - 1):
            return None, True
        controls.append(target[1])
        if is_last:
            last_numbers.add(target[1])
        if is_next:
            next_targets.append((target_url, target[1]))
    if len(last_numbers) > 1:
        return None, True
    last = next(iter(last_numbers), None)
    if last is not None and (last < current[1] or any(number > last for number in controls)):
        return None, True
    if next_targets:
        numbers = {number for _, number in next_targets}
        if numbers == {current[1]} and last == current[1]:
            return None, False
        if numbers == {current[1] + 1}:
            return next_targets[0][0], True
        return None, True
    if last == current[1]:
        return None, False
    return None, True


def next_news_list_page(html: bytes, base_url: str, source_id: str) -> tuple[str | None, bool]:
    """Return a safe next-page URL and whether pagination needs accounting for."""
    soup = BeautifulSoup(html, "html.parser")
    traffic_page = _traffic_list_page_identity(base_url) if source_id == "S-032" else None
    # Preserve the generic explicit-Page parser for historical/alternate list
    # contracts. The comma contract is activated by an explicit comma cursor
    # or at least one same-list Parser control on the entry page.
    has_parser_control = traffic_page and any(
        _traffic_list_page_identity(urllib.parse.urljoin(base_url, str(control.get("href") or ""))) is not None
        for control in soup.find_all(["a", "link", "button"], href=True)
    )
    explicit_comma_cursor = traffic_page is not None and urllib.parse.parse_qsl(urllib.parse.urlsplit(base_url).query)[0][1] != "9,4,20"
    if traffic_page is not None and (explicit_comma_cursor or has_parser_control):
        return _traffic_news_list_next(soup, base_url)
    current_page = _list_page_identity(base_url)
    # An explicitly paginated URL needs terminal evidence even if controls
    # disappeared. Absence of Next is not proof that the final page was served.
    pagination_hint = current_page is not None
    last_page_seen = False
    terminal_controls_consistent = current_page is not None
    for control in soup.find_all(["a", "link", "button"]):
        label = " ".join(control.stripped_strings).strip().lower()
        title = str(control.get("title") or "").strip().lower()
        aria_label = str(control.get("aria-label") or "").strip().lower()
        classes = {str(value).lower() for value in control.get("class", [])}
        rel = {str(value).lower() for value in control.get("rel", [])}
        href = str(control.get("href") or "").strip()
        labels = (label, title, aria_label)
        # S019's friendly utility bar says 回上一頁 but invokes browser history;
        # it is not the list's 上一頁 control. Exclude only the observed exact
        # utility, outside any actual pagination context; unknown JS stays unsafe.
        in_pager = any(
            set(parent.get("class", [])) & {"page", "pagination", "pager", "paginator"}
            for parent in control.parents if getattr(parent, "attrs", None) is not None
        )
        if (
            any(value == "回上一頁" for value in labels)
            and re.fullmatch(r"javascript:\s*history\.back\(\)\s*;?", href, re.I)
            and control.find_parent(["section", "div"], class_="function")
            and not in_pager
        ):
            continue
        is_next = (
            any(any(token in value for token in ("下一頁", "下一页", "下頁", "下页")) for value in labels if value)
            or any(re.search(r"\bnext(?:\s+page)?\b", value) for value in labels if value)
            or "next" in rel
            or any("next" in value for value in classes)
        )
        numbered = any(
            re.fullmatch(r"(?:第\s*)?\d{1,4}\s*(?:頁|页)?", value)
            for value in labels
            if value
        )
        is_last = any(
            any(token in value for token in ("最後一頁", "最后一页", "末頁", "末页"))
            or re.search(r"\blast(?:\s+page)?\b", value)
            for value in labels if value
        ) or "last" in rel
        is_boundary_control = is_last or numbered or any(
            any(token in value for token in ("第一頁", "第一页", "上一頁", "上一页"))
            or re.search(r"\b(?:first|prev(?:ious)?)(?:\s+page)?\b", value)
            for value in labels if value
        )
        if is_boundary_control:
            pagination_hint = True
            target = _list_page_identity(urllib.parse.urljoin(base_url, href)) if href else None
            if current_page is None or target is None or target[0] != current_page[0] or target[1] > current_page[1]:
                terminal_controls_consistent = False
            if numbered and target is not None:
                numbered_labels = [value for value in labels if re.fullmatch(r"(?:第\s*)?\d{1,4}\s*(?:頁|页)?", value)]
                if any(int(re.search(r"\d+", value).group()) != target[1] for value in numbered_labels):
                    terminal_controls_consistent = False
            if is_last:
                last_page_seen = True
                if target is None or current_page is None or target[1] != current_page[1]:
                    terminal_controls_consistent = False
        if not is_next:
            if numbered and (
                re.search(r"[?&](?:page|intpage)=\d+", href, re.I)
                or re.search(r"javascript:\s*list\(", href, re.I)
            ):
                pagination_hint = True
            continue
        pagination_hint = True
        if not href or href.startswith("#"):
            return None, True
        if href.lower().startswith("javascript:"):
            match = re.fullmatch(r"javascript:\s*list\((\d{1,4}),\s*(\d{1,4})\)\s*;?", href, re.I)
            if source_id != "S-001" or not match or int(match.group(1)) < 1:
                return None, True
            parts = urllib.parse.urlsplit(base_url)
            query = [
                (key, value)
                for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
                if key.lower() not in {"page", "intpage"}
            ]
            query.extend((("page", match.group(1)), ("intpage", match.group(2))))
            return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query))), True
        return urllib.parse.urljoin(base_url, href), True
    if last_page_seen and terminal_controls_consistent:
        return None, False
    return None, pagination_hint or traffic_page is not None


def parse_news_rss(xml: bytes, base_url: str) -> list[dict]:
    """Extract the official RSS list without treating a malformed item as zero."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError("news RSS is not valid XML") from error
    entries = []
    for item in root.findall("./channel/item"):
        stable_key = (item.get("iCuItem") or "").strip()
        title = " ".join((item.findtext("title") or "").split())
        detail_url = urllib.parse.urljoin(base_url, (item.findtext("link") or "").strip())
        raw_date = (item.findtext("pubDate") or "").strip()
        if not stable_key or not title or not detail_url or not raw_date:
            raise ValueError("news RSS item is missing stable ID, title, link, or pubDate")
        try:
            published = parsedate_to_datetime(raw_date)
        except (TypeError, ValueError) as error:
            raise ValueError(f"news RSS item has invalid pubDate: {raw_date!r}") from error
        if published.tzinfo is None:
            published = published.replace(tzinfo=TZ)
        entries.append({
            "stable_key": stable_key,
            "title": title,
            "detail_url": detail_url,
            "published": published.astimezone(TZ).date(),
        })
    if not entries:
        raise ValueError("news RSS has no parseable entries")
    return entries


def _fire_datetime(value: str, label: str) -> datetime:
    raw = " ".join(value.split())
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=TZ)
        except ValueError:
            pass
    raise ValueError(f"fire live {label} has invalid timestamp: {raw!r}")


def parse_fire_live(html: bytes) -> list[dict]:
    """Parse only coarse, transient incident metadata from the official table."""
    soup = BeautifulSoup(html, "html.parser")
    update = soup.select_one(".update")
    update_text = " ".join(update.stripped_strings) if update else ""
    update_match = re.search(
        r"最後異動時間\s*[：:]?\s*(\d{4}[-/]\d{2}[-/]\d{2}\s+\d{2}:\d{2}:\d{2})",
        update_text,
    )
    if not update_match:
        raise ValueError("fire live page has no last-update marker")
    source_modified_at = _fire_datetime(update_match.group(1), "last-update marker")
    table = soup.select_one("ul.list.rwd-table")
    if not table:
        raise ValueError("fire live page has no incident table")
    rows = [row for row in table.find_all("li", recursive=False) if "list_head" not in row.get("class", [])]
    if not rows:
        raise ValueError("fire live page has no incident rows")

    required = {"受理時間", "案類", "案別", "發生地點", "派遣分隊", "執行狀況"}
    entries = []
    for row in rows:
        values = {}
        for span in row.find_all("span", attrs={"data-th": True}, recursive=False):
            label = re.sub(r"[：:]$", "", span["data-th"].strip())
            direct_text = " ".join("".join(span.find_all(string=True, recursive=False)).split())
            values[label] = direct_text
        missing = required - set(values)
        if missing:
            raise ValueError(f"fire live row is missing fields: {sorted(missing)}")
        received_at = _fire_datetime(values["受理時間"], "reception")
        category = values["案類"].strip()
        event_type = values["案別"].strip()
        location = values["發生地點"].strip()
        dispatch_unit = values["派遣分隊"].strip()
        if not category or not event_type or not location or not dispatch_unit:
            raise ValueError("fire live row has an empty required value")
        districts = re.findall(r"[\u4e00-\u9fff]{1,3}區", location)
        if not districts:
            raise ValueError("fire live row has no district")
        identity = {
            "received_at": received_at.isoformat(),
            "category": category,
            "event_type": event_type,
            "location": location,
            "dispatch_unit": dispatch_unit,
        }
        entries.append({
            "stable_key": f"S-031:{canonical_sha256(identity)[:24]}",
            "category": category,
            "event_type": event_type,
            "district": districts[-1],
            "observed_at": received_at.isoformat(),
            "dispatch_unit": dispatch_unit,
            "status": values["執行狀況"].strip() or "UNKNOWN",
            "source_modified_at": source_modified_at.isoformat(),
        })
    return entries


def collect_fire_live(session: requests.Session, start: date, end: date) -> dict:
    """Collect one transient snapshot; it cannot prove a historical date window."""
    del start, end
    config = NEWS_LIST_SOURCES["S-031"]
    listing = get(session, config["list_url"], source_id="S-031")
    entries = parse_fire_live(listing.content)
    items = []
    for entry in entries:
        payload = {key: value for key, value in entry.items() if key != "stable_key"}
        items.append({
            "stable_key": entry["stable_key"],
            "source_url": listing.url,
            "published_at": None,
            "content_sha256": canonical_sha256(payload),
            "payload": payload,
        })
    manifest = [
        {key: item[key] for key in ("stable_key", "content_sha256", "published_at")}
        for item in items
    ]
    return {
        "source_health": "PASS",
        "window_completeness": "PARTIAL",
        "window_item_count": None,
        "snapshot_item_count": len(items),
        "items": items,
        "snapshots": [snapshot(listing, "LIST")],
        "manifest_sha256": canonical_sha256(manifest),
    }


def collect_news_list(
    session: requests.Session,
    source_id: str,
    start: date,
    end: date,
    existing: dict[str, dict] | None = None,
    max_details: int | None = None,
    *,
    max_list_pages: int | None = None,
) -> dict:
    """Collect a bounded list; an old row never proves an unseen page complete."""
    list_budget = MAX_NEWS_LIST_PAGES if max_list_pages is None else max_list_pages
    if type(list_budget) is not int or not 1 <= list_budget <= MAX_NEWS_LIST_PAGES_HARD_LIMIT:
        raise ValueError("max_list_pages must be an integer between 1 and 40")
    config = NEWS_LIST_SOURCES[source_id]
    responses = []
    all_pages_seen = True
    is_rss = config.get("format") == "rss"
    page_limit = 1 if is_rss else list_budget
    stop_reason = "RSS_SNAPSHOT" if is_rss else "TERMINAL_PAGE"
    if is_rss:
        listing = get_news_listing(session, source_id)
        responses.append(snapshot(listing, "LIST"))
        entries = parse_news_rss(listing.content, listing.url)
        observed_entries = entries
    else:
        entries = []
        observed_entries = []
        seen_keys = set()
        seen_pages = set()
        page_url = config["list_url"]
        for _ in range(list_budget):
            if page_url in seen_pages:
                stop_reason = "PAGE_LOOP"
                all_pages_seen = False
                break
            seen_pages.add(page_url)
            listing = (
                get_news_listing(session, source_id)
                if page_url == config["list_url"]
                else get(session, page_url, source_id=source_id)
            )
            responses.append(snapshot(listing, "LIST"))
            page_entries = parse_news_list(listing.content, listing.url, config["id_pattern"])
            observed_entries.extend(page_entries)
            for entry in page_entries:
                if entry["stable_key"] not in seen_keys:
                    entries.append(entry)
                    seen_keys.add(entry["stable_key"])
            next_url, has_next = next_news_list_page(listing.content, listing.url, source_id)
            if not has_next:
                break
            if not next_url or next_url in seen_pages:
                stop_reason = "PAGE_LOOP" if next_url in seen_pages else "UNRESOLVED_PAGINATION"
                all_pages_seen = False
                break
            page_url = next_url
        else:
            stop_reason = "PAGE_LIMIT"
            all_pages_seen = False
    existing = existing or {}
    details_fetched = 0
    items = []
    for entry in entries:
        list_payload = {
            "title": entry["title"],
            "detail_url": entry["detail_url"],
            "published_at": published_at(entry["published"]),
        }
        list_sha = canonical_sha256(list_payload)
        if existing.get(entry["stable_key"], {}).get("content_sha256") == list_sha:
            payload = {**list_payload, "detail": "unchanged-skipped"}
        elif max_details is not None and details_fetched >= max_details:
            payload = {**list_payload, "detail": "skipped-detail-cap"}
        else:
            details_fetched += 1
            detail = get(session, entry["detail_url"], source_id=source_id, timeout=120)
            responses.append(snapshot(detail, "DETAIL"))
            payload = {
                **list_payload,
                "detail": "fetched",
                "body_sha256": canonical_bytes_sha256(detail.content),
                "attachments": [
                    urllib.parse.urljoin(detail.url, anchor["href"])
                    for anchor in BeautifulSoup(detail.content, "html.parser").find_all("a", href=True)
                    if re.search(r"\.(pdf|docx?|xlsx?|odt|zip)(\?|$)", anchor["href"], re.I)
                ],
            }
        items.append(
            {
                "stable_key": entry["stable_key"],
                "source_url": entry["detail_url"],
                "published_at": list_payload["published_at"],
                "content_sha256": list_sha,
                "payload": payload,
            }
        )

    dated = [entry["published"] for entry in observed_entries if entry["published"]]
    window_items = [
        item for item in items
        if item["published_at"] and start <= date.fromisoformat(item["published_at"][:10]) <= end
    ]
    reverse_chronological = all(left >= right for left, right in zip(dated, dated[1:]))
    complete_list = all_pages_seen and len(dated) == len(observed_entries) and reverse_chronological
    reaches_before_window = bool(dated) and min(dated) < start
    if complete_list and window_items and reaches_before_window:
        completeness = "COMPLETE_WITH_ITEMS"
    elif complete_list and dated and max(dated) < start:
        completeness = "COMPLETE_ZERO"
    else:
        completeness = "PARTIAL"
    manifest = [{key: item[key] for key in ("stable_key", "content_sha256", "published_at")} for item in items]
    return {
        "source_health": "PASS",
        "window_completeness": completeness,
        "pagination": {
            "strategy": "rss" if is_rss else "next-link",
            "pages_fetched": sum(response["purpose"] == "LIST" for response in responses),
            "page_limit": page_limit,
            "complete": all_pages_seen,
        },
        "list_traversal": {
            "stop_reason": stop_reason,
            "requested_start": start.isoformat(),
            "requested_end": end.isoformat(),
            "reverse_chronological_observed": reverse_chronological,
            "all_observed_dates_known": len(dated) == len(observed_entries),
            "oldest_observed_date": min(dated).isoformat() if dated else None,
            "newest_observed_date": max(dated).isoformat() if dated else None,
            "whole_history_completeness": "UNKNOWN",
            "detail_pages_complete": all(item["payload"].get("detail") == "fetched" for item in items),
            "attachments_downloaded": False,
        },
        "window_item_count": len(window_items),
        "snapshot_item_count": len(items),
        "items": items,
        "snapshots": responses,
        "manifest_sha256": canonical_sha256(manifest),
    }


COLLECTORS = {
    "S-004": collect_download_list,
    "S-006": collect_download_list,
    "S-007": collect_s007,
    "S-009": collect_s009,
    "S-029": collect_s029,
    "S-001": collect_news_list,
    "S-019": collect_news_list,
    "S-032": collect_news_list,
    "S-033": collect_news_list,
    "S-031": collect_fire_live,
}


def collect_source(
    session: requests.Session,
    source_id: str,
    start: date,
    end: date,
    existing: dict[str, dict] | None = None,
    *,
    max_details: int | None = None,
    max_list_pages: int | None = None,
) -> dict:
    collector = COLLECTORS[source_id]
    if max_list_pages is not None and collector is not collect_news_list:
        raise ValueError(f"max_list_pages is only valid for list-news sources: {source_id}")
    if collector is collect_download_list:
        return collector(session, source_id, start, end)
    if collector is collect_news_list:
        return collector(session, source_id, start, end, existing, max_details=max_details, max_list_pages=max_list_pages)
    if collector is collect_fire_live:
        return collector(session, start, end)
    if max_details is not None:
        raise ValueError(f"max_details is only valid for list-news sources: {source_id}")
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


def _detail_previous(row: dict | None) -> dict | None:
    if not row or not row.get("document_version_id"):
        return None
    previous = {
        "document_version_id": row["document_version_id"],
        "body_sha256": row["body_sha256"],
        "normalized_text_sha256": row["normalized_text_sha256"],
        "attachments": row.get("attachments") or [],
    }
    for field in ("last_checked_at", "expires_at", "etag", "last_modified"):
        value = row.get(field)
        if value is not None:
            previous[field] = timestamp(value) if isinstance(value, datetime) else value
    return previous


def detail_next_check(
    classification: dict,
    observed_at: datetime,
    *,
    interval_hours: float = DETAIL_RECHECK_INTERVAL_HOURS,
) -> datetime:
    if not isinstance(interval_hours, (int, float)) or interval_hours <= 0:
        raise ValueError("detail recheck interval must be positive")
    if observed_at.tzinfo is None:
        raise ValueError("detail recheck observed_at requires timezone")
    retry = classification.get("retry_after_seconds")
    if classification.get("status") == "DEFERRED" and isinstance(retry, int):
        return observed_at + timedelta(seconds=min(max(retry, 0), 86400))
    return observed_at + timedelta(hours=interval_hours)


def register_detail_recheck(
    connection,
    source_id: str,
    stable_key: str,
    requested_url: str,
    next_check_at: datetime,
) -> None:
    if not isinstance(stable_key, str) or not stable_key.strip():
        raise ValueError("detail recheck stable_key is required")
    if next_check_at.tzinfo is None:
        raise ValueError("detail recheck next_check_at requires timezone")
    validate_document_url(source_id, requested_url)
    connection.execute(
        """
        INSERT INTO detail_recheck_state (source_id, stable_key, requested_url, next_check_at)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (source_id, stable_key) DO UPDATE SET
            requested_url = EXCLUDED.requested_url,
            next_check_at = LEAST(detail_recheck_state.next_check_at, EXCLUDED.next_check_at),
            updated_at = now()
        """,
        (source_id, stable_key, requested_url, next_check_at),
    )


def _seed_detail_rechecks(connection, source_id: str, items: list[dict], completed_at: datetime) -> int:
    seeded = 0
    next_check_at = completed_at + timedelta(hours=DETAIL_RECHECK_INTERVAL_HOURS)
    for item in items:
        payload = item.get("payload") if isinstance(item, dict) else None
        if not isinstance(payload, dict) or payload.get("detail") != "fetched":
            continue
        register_detail_recheck(
            connection,
            source_id,
            str(item["stable_key"]),
            str(item["source_url"]),
            next_check_at,
        )
        seeded += 1
    return seeded


def _detail_unavailable(requested_url: str, previous: dict | None, observed_at: datetime, error: Exception) -> dict:
    observation = {"status_code": 0, "available": False}
    return {
        "requested_url": requested_url,
        "final_url": None,
        "http_status": None,
        "redirect_count": 0,
        "request_headers": {},
        "plan": None,
        "observation": observation,
        "classification": classify_observation(previous, observation, observed_at=timestamp(observed_at)),
        "transport_error": {
            "type": type(error).__name__,
            "message": str(error).strip()[:256],
        },
    }


def _detail_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _save_detail_snapshot(
    connection,
    source_run_id: str,
    source_id: str,
    stable_key: str,
    result: dict,
    fetched_at: datetime,
) -> str | None:
    body = result.get("response_body")
    observation = result.get("observation") or {}
    if not isinstance(body, (bytes, bytearray)) or not observation.get("body_sha256"):
        return None
    snapshot_id = f"DR-{source_run_id}-{canonical_sha256(f'detail:{stable_key}')[:16]}"
    connection.execute(
        """
        INSERT INTO snapshot_blobs (content_sha256, content_type, byte_count, body, created_at)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (content_sha256) DO NOTHING
        """,
        (
            observation["body_sha256"],
            observation.get("content_type") or "application/octet-stream",
            len(body),
            bytes(body),
            fetched_at,
        ),
    )
    connection.execute(
        """
        INSERT INTO source_snapshots (
            snapshot_id, source_run_id, source_id, purpose, requested_url, final_url,
            http_status, fetched_at, content_sha256
        ) VALUES (%s, %s, %s, 'DETAIL', %s, %s, %s, %s, %s)
        ON CONFLICT (snapshot_id) DO NOTHING
        """,
        (
            snapshot_id,
            source_run_id,
            source_id,
            result["requested_url"],
            result.get("final_url") or result["requested_url"],
            result["http_status"],
            fetched_at,
            observation["body_sha256"],
        ),
    )
    return snapshot_id


def _detail_budget_host(row: dict) -> str:
    """Return the approved host used for the per-host request cap."""
    try:
        source = validate_document_url(row["source_id"], row["requested_url"])
        host = urllib.parse.urlsplit(source["entrypoint"]).hostname
    except (ValueError, TypeError, OSError):
        host = None
    if not host:
        try:
            host = urllib.parse.urlsplit(str(row["requested_url"])).hostname
        except (ValueError, TypeError, OSError):
            host = None
    return str(host or f"unresolved:{row['source_id']}")


def _defer_detail_recheck(
    connection,
    row: dict,
    *,
    due_at: str | None,
    budget_state: dict,
    observed_at: datetime,
) -> None:
    connection.execute(
        """
        UPDATE detail_recheck_state
        SET next_check_at = GREATEST(next_check_at, COALESCE(%s, next_check_at)),
            budget_state = %s::jsonb,
            updated_at = %s
        WHERE source_id = %s AND stable_key = %s
        """,
        (
            datetime.fromisoformat(due_at) if due_at else None,
            _detail_json(budget_state),
            observed_at,
            row["source_id"],
            row["stable_key"],
        ),
    )


def _detail_recheck_outcome(row: dict, reason: str, budget_state: dict, decision: dict) -> dict:
    """Return a refused-target outcome that carries no document evidence."""
    return {
        "source_id": row["source_id"],
        "stable_key": row["stable_key"],
        "requested_url": row["requested_url"],
        "status": "SKIPPED",
        "reason": reason,
        "review_required": False,
        "classification": None,
        "snapshot_id": None,
        "budget": {
            "reason": reason,
            "due_at": decision["due_at"],
            "state": budget_state,
        },
    }


def run_detail_rechecks(
    connection,
    session: requests.Session,
    source_run_ids: dict[str, str],
    observed_at: datetime,
    *,
    limit: int = DETAIL_RECHECK_MAX_PER_RUN,
    interval_hours: float = DETAIL_RECHECK_INTERVAL_HOURS,
    budget_policy: dict | None = None,
) -> list[dict]:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("detail recheck limit must be non-negative")
    if not source_run_ids or limit == 0:
        return []
    # Validate the policy before opening the row-lock transaction so a bad
    # operator input can never abort a collection run mid-flight.
    resolved_policy = recheck_budget_policy(budget_policy)
    source_ids = sorted(source_run_ids)
    placeholders = ", ".join(["%s"] * len(source_ids))
    # The candidate batch is the unit of accounting: it must be wider than the
    # request limit, otherwise the per-source and per-host caps could never
    # arbitrate between due targets.
    candidate_limit = max(
        limit,
        resolved_policy["per_source_limit"],
        resolved_policy["per_host_limit"],
    )
    # ponytail: one row lock spans one bounded request; split claim/worker
    # phases only if recheck throughput becomes a measured bottleneck.
    with connection.transaction():
        # Serialize rolling-budget admission and ledger updates across runs.
        # Row locks alone would allow two disjoint target batches on one host.
        connection.execute("SELECT pg_advisory_xact_lock(hashtext('govintel-detail-budget-v1'))")
        historical_rows = connection.execute(
            "SELECT source_id, stable_key, requested_url, budget_state FROM detail_recheck_state"
        ).fetchall()
        rows = connection.execute(
            f"""
            SELECT source_id, stable_key, requested_url, last_checked_at, next_check_at,
                   etag, last_modified, document_version_id, body_sha256,
                   normalized_text_sha256, attachments, budget_state
            FROM detail_recheck_state
            WHERE next_check_at <= %s AND source_id IN ({placeholders})
            ORDER BY next_check_at, source_id, stable_key
            LIMIT %s
            FOR UPDATE SKIP LOCKED
            """,
            (observed_at, *source_ids, candidate_limit),
        ).fetchall()
        observed = timestamp(observed_at)
        # Due-ness is decided once, with the same TTL planner the transport
        # uses, so a target that is not due never consumes a budget slot.
        due_keys = {
            (row["source_id"], row["stable_key"])
            for row in rows
            if plan_recheck(
                _detail_previous(row),
                observed,
                interval_hours=interval_hours,
            )["status"]
            == "DUE"
        }
        decisions = {
            (item["source_id"], item["stable_key"]): item
            for item in plan_recheck_budget(
                [
                    {
                        "source_id": row["source_id"],
                        "stable_key": row["stable_key"],
                        "host": _detail_budget_host(row),
                        "budget_state": row.get("budget_state"),
                    }
                    for row in rows
                    if (row["source_id"], row["stable_key"]) in due_keys
                ],
                now=observed,
                policy=resolved_policy,
                run_limit=limit,
                historical_targets=[{
                    "source_id": row["source_id"], "stable_key": row["stable_key"],
                    "host": _detail_budget_host(row), "budget_state": row.get("budget_state"),
                } for row in historical_rows],
            )
        }
        outcomes = []
        for row in rows:
            key = (row["source_id"], row["stable_key"])
            if key not in due_keys:
                checked = row.get("last_checked_at")
                base = checked if isinstance(checked, datetime) else observed_at
                next_due = timestamp(max(base + timedelta(hours=interval_hours), observed_at))
                state = budget_state_for_storage(
                    row.get("budget_state"), now=observed, policy=resolved_policy
                )
                state["last_decision"] = "NOT_DUE"
                _defer_detail_recheck(
                    connection,
                    row,
                    due_at=next_due,
                    budget_state=state,
                    observed_at=observed_at,
                )
                outcomes.append(
                    _detail_recheck_outcome(row, "NOT_DUE", state, {"due_at": next_due})
                )
                continue
            decision = decisions[key]
            if decision["decision"] != "ALLOW":
                _defer_detail_recheck(
                    connection,
                    row,
                    due_at=decision["due_at"],
                    budget_state=decision["budget_state"],
                    observed_at=observed_at,
                )
                outcomes.append(
                    _detail_recheck_outcome(
                        row, decision["reason"], decision["budget_state"], decision
                    )
                )
                continue
            previous = _detail_previous(row)
            try:
                source = validate_document_url(row["source_id"], row["requested_url"])
                host = urllib.parse.urlsplit(source["entrypoint"]).hostname
                if not host:
                    raise ValueError("approved source entrypoint has no hostname")
                result = recheck_detail(
                    session,
                    row["requested_url"],
                    previous,
                    observed_at=timestamp(observed_at),
                    allowed_hosts={host},
                    interval_hours=interval_hours,
                    include_body=True,
                )
            except (ValueError, TypeError, OSError, requests.RequestException) as error:
                result = _detail_unavailable(row["requested_url"], previous, observed_at, error)

            classification = result.get("classification")
            if not isinstance(classification, dict):
                # Defence in depth: the target was planned as due but the
                # transport refused to classify it, so no document evidence
                # exists.  Never record that as a completed check.
                checked = row.get("last_checked_at")
                base = checked if isinstance(checked, datetime) else observed_at
                next_due = timestamp(max(base + timedelta(hours=interval_hours), observed_at))
                state = budget_state_for_storage(
                    row.get("budget_state"), now=observed, policy=resolved_policy
                )
                state["last_decision"] = "NOT_DUE"
                _defer_detail_recheck(
                    connection,
                    row,
                    due_at=next_due,
                    budget_state=state,
                    observed_at=observed_at,
                )
                outcomes.append(
                    _detail_recheck_outcome(row, "NOT_DUE", state, {"due_at": next_due})
                )
                continue
            snapshot_id = _save_detail_snapshot(
                connection,
                source_run_ids[row["source_id"]],
                row["source_id"],
                row["stable_key"],
                result,
                observed_at,
            )
            after = classification.get("after") or {}
            current = {
                "document_version_id": after.get("document_version_id") or row.get("document_version_id"),
                "body_sha256": after.get("body_sha256") or row.get("body_sha256"),
                "normalized_text_sha256": after.get("normalized_text_sha256") or row.get("normalized_text_sha256"),
                "attachments": after.get("attachments") if "attachments" in after else (row.get("attachments") or []),
                "etag": after.get("etag") if "etag" in after else row.get("etag"),
                "last_modified": after.get("last_modified") if "last_modified" in after else row.get("last_modified"),
            }
            checked_at = datetime.fromisoformat(classification["last_checked_at"])
            next_check_at = detail_next_check(classification, checked_at, interval_hours=interval_hours)
            budget_state = record_recheck_budget(
                row.get("budget_state"),
                classification,
                now=observed,
                policy=resolved_policy,
            )
            if budget_state["deferred_until"]:
                next_check_at = max(next_check_at, datetime.fromisoformat(budget_state["deferred_until"]))
            public_result = {key: value for key, value in result.items() if key != "response_body"}
            connection.execute(
                """
                UPDATE detail_recheck_state
                SET last_checked_at = %s,
                    next_check_at = %s,
                    etag = %s,
                    last_modified = %s,
                    document_version_id = %s,
                    body_sha256 = %s,
                    normalized_text_sha256 = %s,
                    attachments = %s::jsonb,
                    status = %s,
                    review_required = %s,
                    preserve_last_known_good = %s,
                    event_cancelled = %s,
                    changed_fields = %s::jsonb,
                    last_result = %s::jsonb,
                    last_snapshot_id = COALESCE(%s, last_snapshot_id),
                    updated_at = %s,
                    budget_state = %s::jsonb
                WHERE source_id = %s AND stable_key = %s
                """,
                (
                    checked_at,
                    next_check_at,
                    current["etag"],
                    current["last_modified"],
                    current["document_version_id"],
                    current["body_sha256"],
                    current["normalized_text_sha256"],
                    _detail_json(current["attachments"]),
                    classification["status"],
                    classification["review_required"],
                    classification["preserve_last_known_good"],
                    classification["event_cancelled"],
                    _detail_json(classification.get("changed_fields", [])),
                    _detail_json(public_result),
                    snapshot_id,
                    observed_at,
                    _detail_json(budget_state),
                    row["source_id"],
                    row["stable_key"],
                ),
            )
            outcomes.append(
                {
                    "source_id": row["source_id"],
                    "stable_key": row["stable_key"],
                    "requested_url": row["requested_url"],
                    "status": classification["status"],
                    "reason": None,
                    "review_required": classification["review_required"],
                    "classification": public_result["classification"],
                    "snapshot_id": snapshot_id,
                    "budget": {
                        "reason": None,
                        "due_at": timestamp(next_check_at),
                        "state": budget_state,
                    },
                }
            )
    return outcomes


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

    # Only detail pages explicitly fetched by a list-first collector become
    # recheck targets; unchanged rows are never expanded into a site-wide crawl.
    _seed_detail_rechecks(connection, source_id, collected["items"], completed_at)


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
            source_failure_code(error), str(error)[:1000],
        ),
    )


def run_database_slot(slot: str, slot_date: date, now: datetime | None = None) -> dict:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is required for an online slot")
    slot = slot.upper()
    observed_now = now or datetime.now(TZ)
    slot_date = resolve_collection_date(slot, "manual", slot_date, now=observed_now)
    now = observed_now.astimezone(TZ)
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
                    # List-first collectors diff against durable current items so
                    # unchanged list rows never refetch their detail pages.
                    existing = current_items(connection, source_id)
                    collected = collect_source(
                        session, source_id, window_start.date(), window_end.date(), existing
                    )
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

            source_run_ids = {
                row["source_id"]: row["source_run_id"]
                for row in connection.execute(
                    "SELECT source_id, source_run_id FROM source_runs WHERE collection_run_id = %s",
                    (collection_run_id,),
                ).fetchall()
            }
            detail_rechecks = run_detail_rechecks(
                connection,
                session,
                source_run_ids,
                datetime.now(TZ),
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
            return {
                "collection_run_id": collection_run_id,
                "status": status,
                "replayed": False,
                "detail_rechecks": detail_rechecks,
            }
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtext(%s))", (lock_name,))


def self_check() -> None:
    missing_collectors = sorted(set(P0_SOURCES) - set(COLLECTORS))
    assert not missing_collectors, f"active source policy has no collector: {missing_collectors}"
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
        options = {"max_details": CANARY_MAX_DETAILS} if COLLECTORS.get(source_id) is collect_news_list else {}
        result = collect_source(session, source_id, start, end, {}, **options)
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
    source_role: str,
    integration_status: str,
    freshness: str,
    source_health: str,
    window_completeness: str,
    data_as_of: str | None,
    fetched_at: str,
    previous_sha256s: set[str],
    *,
    baseline_only: bool = False,
) -> dict:
    """Project a raw collector item into a safe feed item for the homepage."""
    stable_key_hash = canonical_sha256(f"{source_id}:{item['stable_key']}")[:16]
    stable_id = f"FEED-{source_id}-{stable_key_hash}"

    # Determine change type
    if baseline_only:
        change_type = "CONFIRMED"
    elif item["content_sha256"] in previous_sha256s:
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
    if baseline_only:
        eligibility = "INELIGIBLE_BASELINE"
    elif change_type == "UNCHANGED":
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

    if integration_status != "PRODUCTION_ACTIVE":
        eligibility = "INELIGIBLE_CANDIDATE_SOURCE_FAILED" if source_health == "FAILED" else "INELIGIBLE_CANDIDATE"

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
    if baseline_only:
        reason_codes.append("FIRST_OBSERVATION_BASELINE")
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

    # Extract committee from payload (S-009 items)
    committee = ""
    if isinstance(item.get("payload"), dict):
        committee = item["payload"].get("committee", "") or ""

    projected = {
        "stable_id": stable_id,
        "source_id": source_id,
        "source_name": source_name,
        "source_role": "PRIMARY_OFFICIAL" if integration_status == "PRODUCTION_ACTIVE" else "CANDIDATE_UNVERIFIED",
        "catalog_role": source_role,
        "integration_status": integration_status,
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
        "committee": committee,
    }
    for key in ("document_revision_at", "date_basis"):
        if key in item:
            projected[key] = item[key]
    if isinstance(item.get("date_evidence"), dict):
        projected["date_evidence"] = public_date_evidence(item["date_evidence"])
    return projected


def generate_intelligence_summary(
    feed_items: list[dict],
    collection_run_id: str,
    now: datetime,
    source_status: list[dict],
) -> dict:
    """Generate a deterministic intelligence summary from the feed items."""
    total_items = len(feed_items)
    eligible_items = sum(1 for i in feed_items if i.get("eligibility") == "HOME_CANDIDATE")

    # Source breakdown
    source_groups: dict[str, list[dict]] = {}
    for item in feed_items:
        source_groups.setdefault(item["source_id"], []).append(item)

    source_names = {s["source_id"]: s["source_name"] for s in source_status}
    source_freshness = {s["source_id"]: s["freshness_status"] for s in source_status}

    # Topic keywords for extraction
    topic_keywords = {
        "科技執法與道路安全": ["科技執法", "測速", "超速", "闖紅燈", "道路安全", "交通事故"],
        "分局整建與設施": ["整建", "分局", "派出所", "廳舍", "設施"],
        "毒品與毒駕防制": ["毒品", "毒駕", "反毒", "藥駕"],
        "交通管理與規劃": ["交通", "停車", "號誌", "路口", "道路"],
        "治安巡邏與防竊": ["巡邏", "防竊", "竊盜", "治安", "監視器"],
        "移工管理與查緝": ["移工", "外勞", "失聯", "查緝"],
        "婦幼安全與保護": ["婦幼", "性騷擾", "家暴", "保護令"],
        "詐騙防制": ["詐騙", "詐欺", "反詐"],
        "警力配置與人事": ["警力", "人事", "員額", "編制", "調任"],
    }

    def extract_top_topics(items: list[dict], limit: int = 3) -> list[str]:
        """Extract top topics from item titles using keyword matching."""
        topic_counts: Counter = Counter()
        for item in items:
            title = item.get("title", "")
            for topic, keywords in topic_keywords.items():
                if any(kw in title for kw in keywords):
                    topic_counts[topic] += 1
        return [t for t, _ in topic_counts.most_common(limit)]

    source_breakdown = []
    for sid in sorted(source_groups.keys()):
        items = source_groups[sid]
        source_breakdown.append({
            "source_id": sid,
            "source_name": source_names.get(sid, sid),
            "item_count": len(items),
            "freshness": source_freshness.get(sid, "UNKNOWN"),
            "top_topics": extract_top_topics(items),
        })

    # Key topics across all items
    key_topics = []
    for topic, keywords in topic_keywords.items():
        matching_items = [
            item for item in feed_items
            if any(kw in item.get("title", "") for kw in keywords)
        ]
        if matching_items:
            sources = sorted(set(item["source_id"] for item in matching_items))
            sample_titles = [item["title"] for item in matching_items[:3] if item.get("title")]
            key_topics.append({
                "topic": topic,
                "item_count": len(matching_items),
                "sources": sources,
                "sample_titles": sample_titles,
            })
    key_topics.sort(key=lambda x: x["item_count"], reverse=True)

    # Committee breakdown from S-009 items (extract from titles if committee field unavailable)
    committee_counts: Counter = Counter()
    for item in feed_items:
        committee = item.get("committee")
        if committee:
            committee_counts[committee] += 1
    committee_breakdown = [
        {"committee": c, "count": n}
        for c, n in committee_counts.most_common()
    ]

    # Template-based daily brief (Chinese)
    top_sources_str = "、".join(
        f"{s['source_name']}（{s['item_count']} 筆）" for s in source_breakdown[:3]
    )
    top_topic_names = [t["topic"] for t in key_topics[:3]]
    topics_str = "、".join(top_topic_names) if top_topic_names else "一般議會事務"
    daily_brief = (
        f"本期共監測 {total_items} 筆警政相關議會資訊，"
        f"符合首頁展示條件 {eligible_items} 筆。"
        f"主要來源：{top_sources_str}。"
        f"關鍵議題包括：{topics_str}。"
    )

    # Template-based daily brief (English)
    top_sources_en = ", ".join(
        f"{s['source_id']} ({s['item_count']} items)" for s in source_breakdown[:3]
    )
    topics_en = ", ".join(top_topic_names) if top_topic_names else "general council affairs"
    daily_brief_en = (
        f"Monitoring {total_items} police-related council items, "
        f"{eligible_items} eligible for homepage display. "
        f"Primary sources: {top_sources_en}. "
        f"Key topics: {topics_en}."
    )

    return {
        "schema_version": 1,
        "generated_at": timestamp(now),
        "collection_run_id": collection_run_id,
        "total_items": total_items,
        "eligible_items": eligible_items,
        "source_breakdown": source_breakdown,
        "key_topics": key_topics,
        "committee_breakdown": committee_breakdown,
        "daily_brief": daily_brief,
        "daily_brief_en": daily_brief_en,
    }


def generate_feed_csv(feed_items: list[dict], raw_items_by_source: dict[str, list[dict]], output_path: Path) -> None:
    """Generate a CSV export of all feed items with full content data."""
    fieldnames = [
        "stable_id", "source_id", "source_name", "title",
        "official_url", "published_at", "freshness_status",
        "change_type", "committee", "bill_type", "sponsor",
        "proposal_rationale", "resolution", "session",
        "content_sha256",
    ]

    # Map source_id to source_name
    source_names = {sid: name for sid, (name, _) in P0_SOURCES.items()}

    # Build a lookup from stable_key to raw payload
    raw_lookup: dict[str, dict] = {}
    for source_id, items in raw_items_by_source.items():
        for item in items:
            key = f"{source_id}:{item['stable_key']}"
            raw_lookup[key] = item.get("payload", {})

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for item in feed_items:
        source_id = item.get("source_id", "")
        # Recover the stable_key from stable_id: "FEED-S-009-xxxx" -> lookup
        stable_id = item.get("stable_id", "")
        # Try to find matching raw payload by content_sha256
        payload = {}
        for sid, raw_items in raw_items_by_source.items():
            if sid != source_id:
                continue
            for raw in raw_items:
                if canonical_sha256(f"{sid}:{raw['stable_key']}")[:16] in stable_id:
                    payload = raw.get("payload", {})
                    break
            if payload:
                break

        # Extract S-009 specific fields
        proposal_rationale = ""
        if isinstance(payload, dict):
            rationale = payload.get("proposalRationale") or payload.get("proposalRationaleSort") or ""
            # Clean HTML tags
            if "<" in rationale:
                rationale = re.sub(r"<[^>]+>", "", rationale)
            # Remove keyword highlight suffix
            if "關鍵字包含" in rationale:
                rationale = rationale.split("關鍵字包含")[0].strip()
            proposal_rationale = rationale.strip()

        row = {
            "stable_id": stable_id,
            "source_id": source_id,
            "source_name": source_names.get(source_id, ""),
            "title": item.get("title", ""),
            "official_url": item.get("official_url", ""),
            "published_at": item.get("published_at", ""),
            "freshness_status": item.get("freshness_status", ""),
            "change_type": item.get("change_type", ""),
            "committee": item.get("committee", "") or (payload.get("committee", "") if isinstance(payload, dict) else ""),
            "bill_type": payload.get("billType", "") if isinstance(payload, dict) else "",
            "sponsor": payload.get("sponsor", "") if isinstance(payload, dict) else "",
            "proposal_rationale": proposal_rationale,
            "resolution": (payload.get("resolution", "") if isinstance(payload, dict) else ""),
            "session": payload.get("session", "") if isinstance(payload, dict) else "",
            "content_sha256": item.get("content_sha256", ""),
        }
        writer.writerow(row)

    output_path.write_text(buf.getvalue(), encoding="utf-8-sig")


def build_demo_status(output: Path, slot: str, slot_date: date, trigger: str) -> dict:
    now = datetime.now(TZ)
    # Refuse a future completed window before reading state or making requests.
    slot_date = resolve_collection_date(slot, trigger, slot_date, now=now)
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
    catalog = catalog_rows()
    window_end = scheduled_time(slot_date, slot)
    window_start = window_end - timedelta(days=7)
    next_at = next_update(slot_date, slot)
    source_status = []
    feed_items = []
    source_summary = {}
    raw_items_by_source: dict[str, list[dict]] = {}

    for source_id, (source_name, source_url) in P0_SOURCES.items():
        source_run_id = f"SR-DEMO-{slot_date:%Y%m%d}-{slot}-{source_id[2:]}"
        previous = prior_sources.get(source_id, {})
        previous_lkg = previous.get("last_known_good")
        baseline_only = previous_lkg is None and not any(
            item.get("source_id") == source_id for item in (prior_feed or {}).get("items", [])
        )
        retained_data_as_of = previous.get("data_as_of")
        retained_date_metadata = {
            key: previous[key]
            for key in ("data_as_of_basis", "data_as_of_evidence", "data_as_of_scope")
            if key in previous
        }
        if source_id == "S-009" or (
            source_id == "S-006" and previous.get("data_as_of_basis") not in {
                "OFFICIAL_DOCUMENT_REVISION_DATE", "OFFICIAL_LIST_TITLE_DATE",
            }
        ):
            # A legacy fetch clock is not rescued by a failed or empty fetch.
            retained_data_as_of = None
            retained_date_metadata = {}
        date_metadata = dict(retained_date_metadata)
        collected_items = []
        try:
            collected = collect_source(session, source_id, window_start.date(), window_end.date())
            source_checked_at = datetime.now(TZ)
            collected_items = collected.get("items", [])
            raw_items_by_source[source_id] = collected_items
            dated_items = [
                (item.get("document_revision_at") or item.get("published_at"), item)
                for item in collected_items
                if item.get("document_revision_at") or item.get("published_at")
            ]
            latest_item_time, latest_item = max(dated_items, key=lambda value: value[0], default=(None, None))
            # An official S-007 record date may establish data time even when it
            # falls outside this collection window. Local API observation time
            # never establishes an official publication date.
            data_as_of = latest_item_time
            date_metadata = {}
            if latest_item and source_id == "S-006":
                date_metadata["data_as_of_basis"] = latest_item.get("date_basis", "OFFICIAL_LIST_TITLE_DATE")
                date_metadata["data_as_of_scope"] = "LATEST_EVIDENCED_DOCUMENT_VERSION"
                if latest_item.get("date_evidence"):
                    date_metadata["data_as_of_evidence"] = latest_item["date_evidence"]
            if latest_item and source_id == "S-007":
                date_metadata = {"data_as_of_basis": "OFFICIAL_API_RECORD_DATE", "data_as_of_scope": "COLLECTION_WINDOW"}
            if collected.get("latest_record_date") and (
                not data_as_of or collected["latest_record_date"] > data_as_of
            ):
                data_as_of = collected["latest_record_date"]
                date_metadata = {
                    "data_as_of_basis": "OFFICIAL_API_RECORD_DATE",
                    "data_as_of_scope": collected.get("latest_record_date_scope", "OBSERVED_API_PAGE"),
                }
            if not data_as_of:
                # Undated successful content does not establish an official
                # publication time. Keep last_checked_at as the observation
                # clock, and do not carry a prior fabricated date forward.
                # S-009 has no official date evidence, including for an empty
                # inventory; discard legacy values made from the fetch clock.
                if not collected_items and source_id != "S-009":
                    data_as_of = retained_data_as_of
                    date_metadata = dict(retained_date_metadata)
            manifest_changed = collected["manifest_sha256"] != previous.get("manifest_sha256")
            change_count = collected["window_item_count"] if manifest_changed and not baseline_only else 0
            result = result_for(collected["window_completeness"], change_count)
            lkg = {
                "source_run_id": source_run_id,
                "completed_at": timestamp(source_checked_at),
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
                "last_checked_at": timestamp(source_checked_at),
                "last_success_at": timestamp(source_checked_at),
                "next_update_at": next_at,
                "last_known_good": lkg,
            }
        except Exception as error:
            source_checked_at = datetime.now(TZ)
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
                # The undated S-009 schema cannot validate a legacy observation
                # clock even when today's fetch fails. Preserve LKG content,
                # while removing that unsupported publication-time claim.
                "data_as_of": retained_data_as_of,
                "last_checked_at": timestamp(source_checked_at),
                "last_success_at": previous.get("last_success_at"),
                "next_update_at": next_at,
                "last_known_good": previous_lkg,
                "error_code": source_failure_code(error),
            }
        if isinstance(date_metadata.get("data_as_of_evidence"), dict):
            date_metadata["data_as_of_evidence"] = public_date_evidence(date_metadata["data_as_of_evidence"])
        record.update(date_metadata)
        catalog_row = catalog.get(source_id, {})
        record["source_role"] = catalog_row.get("role")
        record["integration_status"] = catalog_row.get("status")
        if source_checked_at < now:
            raise ValueError("collection clock moved backwards")
        freshness = freshness_status(record["data_as_of"], source_checked_at, *SOURCE_FRESHNESS_POLICY.get(source_id, (13, 24)))
        record["freshness_status"] = freshness
        record["last_known_good_age"] = last_known_good_age(record["last_known_good"], source_checked_at)
        record["intelligence_gaps"] = gap_reasons(record, record["last_known_good"], freshness)
        if freshness == "NO_DATA":
            record["intelligence_gaps"].append("NO_DATA_AS_OF")
        source_status.append(record)

        # Project items into feed
        fetched_at = timestamp(source_checked_at)
        if collected_items:
            for item in collected_items:
                feed_item = project_feed_item(
                    item=item,
                    source_id=source_id,
                    source_name=source_name,
                    source_url=source_url,
                    source_role=record["source_role"],
                    integration_status=record["integration_status"],
                    freshness=freshness,
                    source_health=record["source_health"],
                    window_completeness=record["window_completeness"],
                    data_as_of=record["data_as_of"],
                    fetched_at=fetched_at,
                    previous_sha256s=prior_feed_sha256s,
                    baseline_only=baseline_only,
                )
                feed_items.append(feed_item)
        elif record["source_health"] == "FAILED" and prior_feed and isinstance(prior_feed.get("items"), list):
            # C: LKG preservation — copy prior feed items for this source with LKG markers
            for prior_item in prior_feed["items"]:
                if prior_item.get("source_id") == source_id:
                    lkg_item = {**prior_item}
                    lkg_item["change_type"] = "LKG"
                    lkg_item["source_health"] = "FAILED"
                    # Legacy publications did not separate official trust from
                    # the catalog's purpose. Rebind both to the current active
                    # source without changing the retained content or clocks.
                    lkg_item["source_role"] = "PRIMARY_OFFICIAL"
                    lkg_item["catalog_role"] = record["source_role"]
                    lkg_item["integration_status"] = record["integration_status"]
                    lkg_item["eligibility"] = "INELIGIBLE_SOURCE_FAILED"
                    lkg_item["freshness_status"] = freshness if freshness != "FRESH" else "VERY_STALE"
                    lkg_item["data_as_of"] = record["data_as_of"]
                    # A failed poll did not fetch these retained bytes.
                    lkg_item["fetched_at"] = prior_item.get("fetched_at")
                    if isinstance(lkg_item.get("date_evidence"), dict):
                        lkg_item["date_evidence"] = public_date_evidence(lkg_item["date_evidence"])
                    feed_items.append(lkg_item)

        # Source summary for feed
        source_summary[source_id] = {
            "health": record["source_health"],
            "freshness": freshness,
            "item_count": len(collected_items),
        }

    candidate_sources = []
    for source_id in load_source_catalog().get("promotion_plan", []):
        row = catalog.get(source_id)
        if not row or row["status"] == "PRODUCTION_ACTIVE" or source_id not in NEWS_LIST_SOURCES:
            continue
        entry = {
            "source_id": source_id,
            "source_name": row["name"],
            "source_url": row["entrypoint"],
            "source_role": row["role"],
            "integration_status": row["status"],
            "cadence_class": row.get("cadence_class"),
            "promotion_eligible": False,
        }
        for field in ("public_usage_notice", "retention_class"):
            if row.get(field):
                entry[field] = row[field]
        candidate_sources.append(entry)
    from scripts.candidate_lane import collect_pages_candidate_lane

    candidate_status, candidate_items, candidate_summary = collect_pages_candidate_lane(
        globals(),
        window_start.date(),
        window_end.date(),
        prior,
        prior_feed,
        now,
        next_at,
        clock=lambda: datetime.now(TZ),
    )

    finished_at = datetime.now(TZ)
    if finished_at < now or any(
        datetime.fromisoformat(row["last_checked_at"]) > finished_at
        for row in source_status + candidate_status
    ):
        raise ValueError("collection clock moved backwards")

    failed = sum(item["result"] == "FAILED" for item in source_status)
    partial = sum(item["result"] == "PARTIAL" for item in source_status)
    status = "FAILED" if failed == len(source_status) else "PARTIAL" if failed or partial else "SUCCEEDED"
    collection_run_id = f"CR-DEMO-{slot_date:%Y%m%d}-{slot}-{trigger.upper()}"
    state = {
        "schema_version": 1,
        "source_policy": SOURCE_POLICY_BINDING,
        "mode": "COMPETITION_DEMO",
        "generated_at": timestamp(finished_at),
        "next_update_at": next_at,
        "candidate_catalog": candidate_sources,
        "latest_collection_run": {
            "source_policy": SOURCE_POLICY_BINDING,
            "collection_run_id": collection_run_id,
            "slot_date": slot_date.isoformat(),
            "slot": slot,
            "trigger": trigger.upper(),
            "scheduled_for": timestamp(window_end),
            "started_at": timestamp(now),
            "finished_at": timestamp(finished_at),
            "status": status,
        },
        "sources": source_status,
        "candidate_sources": candidate_status,
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
        "source_policy": SOURCE_POLICY_BINDING,
        "schema_version": 1,
        "generated_at": timestamp(finished_at),
        "collection_run_id": collection_run_id,
        "items": deduped_items,
        "source_summary": source_summary,
        "candidate_items": candidate_items,
        "candidate_source_summary": candidate_summary,
    }
    save_state(feed_output, feed_state)

    print(f"DEMO_STATUS_OK slot={slot} status={status} sources={len(source_status)} output={output}")
    print(f"FEED_OK items={len(deduped_items)} eligible={sum(1 for i in deduped_items if i['eligibility'] == 'HOME_CANDIDATE')} output={feed_output}")

    # Generate intelligence summary
    summary_output = output.parent / "intelligence-summary.json"
    summary_data = generate_intelligence_summary(deduped_items, collection_run_id, finished_at, source_status)
    summary_data["source_policy"] = SOURCE_POLICY_BINDING
    save_state(summary_output, summary_data)
    print(f"SUMMARY_OK topics={len(summary_data['key_topics'])} output={summary_output}")

    # Generate CSV export
    csv_output = output.parent / "feed-export.csv"
    generate_feed_csv(deduped_items, raw_items_by_source, csv_output)
    print(f"CSV_OK items={len(deduped_items)} output={csv_output}")

    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Production P0 source collector")
    parser.add_argument("--slot", choices=("morning", "evening"))
    parser.add_argument("--slot-date", type=date.fromisoformat)
    parser.add_argument("--canary", action="store_true")
    parser.add_argument("--demo-output", type=Path)
    parser.add_argument("--trigger", choices=("manual", "schedule"), default="manual")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.demo_output and not args.slot:
        parser.error("--demo-output requires --slot")
    if sum((bool(args.slot and not args.demo_output), args.canary, args.self_check, bool(args.demo_output))) != 1:
        parser.error("choose exactly one database slot, --canary, --demo-output, or --self-check")
    if args.slot:
        try:
            args.slot_date = resolve_collection_date(
                args.slot, args.trigger, args.slot_date, now=datetime.now(TZ),
            )
        except ValueError as error:
            parser.error(str(error))
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
