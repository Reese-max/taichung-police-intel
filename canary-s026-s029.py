#!/usr/bin/env python3
"""執行 S-026 法規草案與 S-029 議會專案報告的唯讀 live canary。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


TZ = ZoneInfo("Asia/Taipei")
USER_AGENT = "TaichungPoliceIntelCanary/1.0 (+public-source-monitor)"
ROOT = Path(__file__).resolve().parent
S026_LEGACY_URL = "https://lawsearch.taichung.gov.tw/GLRSout/DraftForum.aspx"
S026_CURRENT_URL = "https://law.taichung.gov.tw/DraftForum.aspx"
S026_HISTORY_URL = f"{S026_CURRENT_URL}?Type=H"
S026_POLICE_PROBE_URL = "https://law.taichung.gov.tw/DraftOpinion.aspx?id=111637&Type=H"
S029_INDEX_URL = "https://www.rdec.taichung.gov.tw/12047/12142/12145"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_sha256(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(body)


def roc_dot_to_iso(value: str) -> str:
    match = re.search(r"(\d{2,3})\.(\d{1,2})\.(\d{1,2})", value or "")
    if not match:
        return ""
    year, month, day = map(int, match.groups())
    return f"{year + 1911:04d}-{month:02d}-{day:02d}"


def in_window(value: str, start: date, end: date) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    return start <= parsed <= end


def get(session: requests.Session, url: str, timeout: int = 60) -> requests.Response:
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            if not response.content:
                raise requests.RequestException("HTTP 成功但內容為空")
            return response
        except requests.RequestException as exc:
            last_error = exc
            status = exc.response.status_code if exc.response is not None else None
            if attempt == 2 or (status is not None and status != 429 and status < 500):
                raise
            time.sleep(attempt + 1)
    raise RuntimeError(f"取得失敗：{url}：{last_error}")


def response_evidence(response: requests.Response) -> dict:
    return {
        "requested_url": (
            response.history[0].request.url
            if response.history
            else response.request.url
        ),
        "final_url": response.url,
        "http_status": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "content_length": len(response.content),
        "raw_sha256": sha256(response.content),
    }


def download_pdfs(session: requests.Session, anchors, base_url: str) -> list[dict]:
    attachments: list[dict] = []
    seen: set[str] = set()
    for anchor in anchors:
        url = urljoin(base_url, anchor.get("href", ""))
        if not url or url in seen:
            continue
        seen.add(url)
        response = get(session, url, 120)
        if not response.content.startswith(b"%PDF-"):
            raise RuntimeError(f"附件不是 PDF：{url}")
        attachments.append({
            "title": " ".join(anchor.stripped_strings),
            "url": response.url,
            "http_status": response.status_code,
            "content_length": len(response.content),
            "sha256": sha256(response.content),
        })
    return attachments


def parse_law_items(soup: BeautifulSoup, base_url: str) -> list[dict]:
    items: dict[str, dict] = {}
    for anchor in soup.select('a[href*="DraftOpinion.aspx"]'):
        url = urljoin(base_url, anchor.get("href", ""))
        item_id = (parse_qs(urlparse(url).query).get("id") or [""])[0]
        if not item_id:
            continue
        row = anchor.find_parent("tr")
        row_text = " ".join((row or anchor.parent).stripped_strings)
        date_match = re.search(r"\b(\d{2,3}\.\d{1,2}\.\d{1,2})\b", row_text)
        deadline_match = re.search(r"預告終止日\s*(\d{2,3}\.\d{1,2}\.\d{1,2})", row_text)
        items[item_id] = {
            "item_id": item_id,
            "title": " ".join(anchor.stripped_strings),
            "published_at": roc_dot_to_iso(date_match.group(1)) if date_match else "",
            "deadline_at": roc_dot_to_iso(deadline_match.group(1)) if deadline_match else "",
            "url": url,
        }
    return list(items.values())


def fetch_s026(session: requests.Session, start: date, end: date) -> dict:
    legacy = get(session, S026_LEGACY_URL)
    if urlparse(legacy.url).netloc != "law.taichung.gov.tw":
        raise RuntimeError(f"舊入口未導向目前官方網域：{legacy.url}")

    current = get(session, S026_CURRENT_URL)
    current_soup = BeautifulSoup(current.content, "html.parser")
    current_text = " ".join(current_soup.stripped_strings)
    active_items = parse_law_items(current_soup, current.url)
    explicit_zero = "尚無預告中法規草案" in current_text
    if not active_items and not explicit_zero:
        raise RuntimeError("最新草案頁沒有項目，也沒有正式零筆說明")

    history = get(session, S026_HISTORY_URL)
    history_soup = BeautifulSoup(history.content, "html.parser")
    history_text = " ".join(history_soup.stripped_strings)
    history_items = parse_law_items(history_soup, history.url)
    count_match = re.search(r"共\s*([\d,]+)\s*筆.*?頁次：\s*(\d+)\s*/\s*(\d+)", history_text)
    if not count_match:
        raise RuntimeError("歷史草案頁缺少總筆數或頁次")
    declared_count = int(count_match.group(1).replace(",", ""))
    current_page = int(count_match.group(2))
    page_count = int(count_match.group(3))
    expected_page_items = min(10, declared_count)
    if current_page != 1 or len(history_items) != expected_page_items:
        raise RuntimeError(f"歷史草案首頁筆數不一致：預期 {expected_page_items}，取得 {len(history_items)}")
    history_dates = [item["published_at"] for item in history_items]
    if any(not value for value in history_dates) or history_dates != sorted(history_dates, reverse=True):
        raise RuntimeError("歷史草案首頁日期缺漏或不是新到舊")
    if page_count > 1 and date.fromisoformat(history_dates[-1]) > start:
        raise RuntimeError("歷史首頁尚未覆蓋完整 7 日窗口")

    probe = get(session, S026_POLICE_PROBE_URL)
    probe_soup = BeautifulSoup(probe.content, "html.parser")
    probe_text = " ".join(probe_soup.stripped_strings)
    if "臺中市交通義勇警察協勤派遣與管理辦法" not in probe_text:
        raise RuntimeError("警政草案探針未命中預期正式標題")
    probe_anchors = [
        anchor for anchor in probe_soup.select('a[href*="Download.ashx"]')
        if "id=111637" in anchor.get("href", "")
    ]
    probe_attachments = download_pdfs(session, probe_anchors, probe.url)
    if len(probe_attachments) != 2:
        raise RuntimeError(f"警政草案探針附件數不一致：預期 2，取得 {len(probe_attachments)}")

    window_items = [item for item in active_items + history_items if in_window(item["published_at"], start, end)]
    stable_manifest = {
        "active_items": active_items,
        "history_page_1_items": history_items,
        "history_declared_count": declared_count,
        "history_page_count": page_count,
        "police_probe_attachments": probe_attachments,
    }
    return {
        "source_id": "S-026",
        "source_name": "臺中市政府主管法規共用系統",
        "canary_status": "PASS",
        "source_health": "PASS",
        "window_completeness": "COMPLETE_WITH_ITEMS" if window_items else "COMPLETE_ZERO",
        "legacy_redirect": response_evidence(legacy),
        "current_drafts": {
            **response_evidence(current),
            "item_count": len(active_items),
            "explicit_zero": explicit_zero,
        },
        "history_page_1": {
            **response_evidence(history),
            "declared_count": declared_count,
            "page_count": page_count,
            "parsed_count": len(history_items),
            "latest_published_at": history_dates[0],
            "oldest_published_at": history_dates[-1],
        },
        "window_items": window_items,
        "police_probe": {
            **response_evidence(probe),
            "item_id": "111637",
            "title": "預告修正「臺中市交通義勇警察協勤派遣與管理辦法」草案",
            "attachments": probe_attachments,
        },
        "manifest_sha256": manifest_sha256(stable_manifest),
    }


def parse_session_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    sessions: list[dict] = []
    seen: set[str] = set()
    pattern = re.compile(r"^臺中市議會第\d+屆第\d+次(?:定期會|臨時會)$")
    for anchor in soup.select('a[href*="Lpsimplelist"]'):
        title = (anchor.get("title") or " ".join(anchor.stripped_strings)).strip()
        if not pattern.fullmatch(title):
            continue
        url = urljoin(base_url, anchor.get("href", ""))
        if url in seen:
            continue
        seen.add(url)
        sessions.append({"title": title, "url": url})
    return sessions


def parse_rdec_items(soup: BeautifulSoup, base_url: str) -> list[dict]:
    items: dict[str, dict] = {}
    for anchor in soup.select('a[href*="/media/"]'):
        url = urljoin(base_url, anchor.get("href", ""))
        if ".pdf" not in url.lower():
            continue
        row = anchor.find_parent("li") or anchor.find_parent("tr") or anchor.parent
        row_text = " ".join(row.stripped_strings)
        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", row_text)
        raw_title = anchor.get("title") or " ".join(anchor.stripped_strings)
        title = raw_title.split("(另開新視窗)", 1)[0].strip()
        item_match = re.match(r"(\d{7,9}-\d+)", title)
        items[url] = {
            "item_id": item_match.group(1) if item_match else sha256(url.encode("utf-8"))[:16],
            "title": title,
            "published_at": date_match.group(1) if date_match else "",
            "url": url,
        }
    return list(items.values())


def fetch_s029(session: requests.Session, start: date, end: date) -> dict:
    index = get(session, S029_INDEX_URL)
    index_soup = BeautifulSoup(index.content, "html.parser")
    sessions = parse_session_links(index_soup, index.url)
    if not sessions:
        raise RuntimeError("議會專案報告入口找不到會期清單")
    latest = sessions[0]

    first_url = f"{latest['url']}?Page=1&PageSize=30&type="
    first = get(session, first_url)
    first_soup = BeautifulSoup(first.content, "html.parser")
    first_text = " ".join(first_soup.stripped_strings)
    count_match = re.search(r"共\s*([\d,]+)\s*筆資料.*?第\s*(\d+)\s*/\s*(\d+)\s*頁", first_text)
    if not count_match:
        raise RuntimeError("最新會期清單缺少總筆數或頁次")
    declared_count = int(count_match.group(1).replace(",", ""))
    current_page = int(count_match.group(2))
    page_count = int(count_match.group(3))
    if current_page != 1 or page_count > 20:
        raise RuntimeError(f"最新會期頁次異常：{current_page}/{page_count}")

    pages = [first]
    for page in range(2, page_count + 1):
        pages.append(get(session, f"{latest['url']}?Page={page}&PageSize=30&type="))
    items_by_url: dict[str, dict] = {}
    for response in pages:
        for item in parse_rdec_items(BeautifulSoup(response.content, "html.parser"), response.url):
            items_by_url[item["url"]] = item
    items = list(items_by_url.values())
    if len(items) != declared_count:
        raise RuntimeError(f"最新會期筆數不一致：宣告 {declared_count}，取得 {len(items)}")
    if any(not item["published_at"] for item in items):
        raise RuntimeError("議會專案報告缺少發布日期")

    police_items = [item for item in items if "警察" in item["title"] or "警政" in item["title"]]
    if not police_items:
        raise RuntimeError("最新會期沒有可驗證的警政專案報告")
    police_attachments = []
    for item in police_items:
        response = get(session, item["url"], 120)
        if not response.content.startswith(b"%PDF-"):
            raise RuntimeError(f"警政專案附件不是 PDF：{item['url']}")
        police_attachments.append({
            **item,
            "http_status": response.status_code,
            "content_length": len(response.content),
            "sha256": sha256(response.content),
        })

    window_items = [item for item in items if in_window(item["published_at"], start, end)]
    stable_manifest = {
        "latest_session": latest,
        "declared_count": declared_count,
        "items": items,
        "police_attachments": police_attachments,
    }
    return {
        "source_id": "S-029",
        "source_name": "臺中市政府議會專案報告",
        "canary_status": "PASS",
        "source_health": "PASS",
        "window_completeness": "COMPLETE_WITH_ITEMS" if window_items else "COMPLETE_ZERO",
        "index": {
            **response_evidence(index),
            "session_count": len(sessions),
        },
        "latest_session": {
            **latest,
            "declared_count": declared_count,
            "page_count": page_count,
            "parsed_count": len(items),
            "list_pages": [response_evidence(page) for page in pages],
        },
        "window_items": window_items,
        "police_item_count": len(police_items),
        "police_attachments": police_attachments,
        "manifest_sha256": manifest_sha256(stable_manifest),
    }


def self_check() -> None:
    assert roc_dot_to_iso("115.07.27") == "2026-07-27"
    assert roc_dot_to_iso("日期缺失") == ""
    assert in_window("2026-08-10", date(2026, 8, 8), date(2026, 8, 14))
    assert not in_window("2026-08-01", date(2026, 8, 8), date(2026, 8, 14))
    assert manifest_sha256({"b": 2, "a": 1}) == manifest_sha256({"a": 1, "b": 2})
    print("SELF_CHECK_OK")


def main() -> None:
    today = datetime.now(TZ).date()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-end", type=date.fromisoformat, default=today)
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if args.window_days < 1:
        raise ValueError("window-days 必須至少為 1")

    window_start = args.window_end - timedelta(days=args.window_days - 1)
    output = args.output or ROOT / f"source-live-canary-s026-s029-{args.window_end.isoformat()}.json"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"})
    result = {
        "status": "PASS",
        "checked_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "window_start": window_start.isoformat(),
        "window_end": args.window_end.isoformat(),
        "script": Path(__file__).name,
        "script_sha256": sha256(Path(__file__).read_bytes()),
        "sources": {},
    }
    for source_id, fetcher in (("S-026", fetch_s026), ("S-029", fetch_s029)):
        try:
            result["sources"][source_id] = fetcher(session, window_start, args.window_end)
        except Exception as exc:  # 保留失敗證據，不把失敗改寫成零筆。
            result["status"] = "FAIL"
            result["sources"][source_id] = {
                "source_id": source_id,
                "canary_status": "FAIL",
                "source_health": "FETCH_FAILED",
                "window_completeness": "UNKNOWN",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"STATUS={result['status']}")
    for source_id, source in result["sources"].items():
        print(
            f"{source_id}={source['canary_status']} "
            f"HEALTH={source['source_health']} WINDOW={source['window_completeness']}"
        )
    print(f"OUTPUT={output.resolve()}")
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
