#!/usr/bin/env python3
"""驗證 S-028 警政統計、人口分母與 165 公開資料的可溯源垂直切片。"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import time
from collections import Counter
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup


TZ = ZoneInfo("Asia/Taipei")
ROOT = Path(__file__).resolve().parent
USER_AGENT = "TaichungPoliceIntelCanary/1.0 (+public-source-monitor)"
DATASET_URL = "https://data.gov.tw/dataset/{dataset_id}"
CRIME_DATASET_ID = "88147"
POPULATION_DATASET_ID = "103703"
FRAUD_DATASET_ID = "176455"
CRIME_ITEM = "處理刑事案件-分局別-發生數(單位:件)"
POPULATION_ITEM = "各區戶數、人口數按戶別及性別分-人口數(單位:人)"
CRIME_UNITS = {
    "局本部", "第一分局", "第二分局", "第三分局", "第四分局", "第五分局",
    "第六分局", "豐原分局", "霧峰分局", "烏日分局", "清水分局", "大甲分局",
    "太平分局", "東勢分局", "和平分局", "大雅分局",
}
POPULATION_FIELDS = {
    "男_共同生活戶", "女_共同生活戶", "男_共同事業戶",
    "女_共同事業戶", "男_單獨生活戶", "女_單獨生活戶",
}
FRAUD_FIELDS = {"民國年月", "網域", "網站性質", "法律依據", "聲請單位"}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_sha256(value: object) -> str:
    body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(body)


def get(session: requests.Session, url: str, allowed_hosts: set[str], timeout: int = 60) -> requests.Response:
    if urlparse(url).hostname not in allowed_hosts:
        raise RuntimeError(f"非白名單來源：{url}")
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        try:
            response = session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            if urlparse(response.url).hostname not in allowed_hosts:
                raise RuntimeError(f"官方來源導向非白名單網域：{response.url}")
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
        "requested_url": response.request.url,
        "final_url": response.url,
        "fetched_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "http_status": response.status_code,
        "content_type": response.headers.get("Content-Type", ""),
        "content_length": len(response.content),
        "etag": response.headers.get("ETag"),
        "last_modified": response.headers.get("Last-Modified"),
        "raw_sha256": sha256(response.content),
    }


def metadata_value(soup: BeautifulSoup, label: str) -> str:
    node = soup.find(string=lambda value: value and value.strip() == label)
    row = node.find_parent("div", class_="table-row") if node else None
    values = list(row.stripped_strings) if row else []
    if len(values) < 2:
        raise RuntimeError(f"資料集頁缺少詮釋資料：{label}")
    return values[-1]


def resource_id(url: str) -> str:
    parsed = urlparse(url)
    rid = (parse_qs(parsed.query).get("rid") or [""])[0]
    if rid:
        return rid
    match = re.search(r"/resource/([^/]+)/download$", parsed.path)
    if not match:
        raise RuntimeError(f"無法取得 resource ID：{url}")
    return match.group(1)


def fetch_dataset_page(
    session: requests.Session,
    dataset_id: str,
    expected_title: str,
    download_host: str,
) -> dict:
    response = get(session, DATASET_URL.format(dataset_id=dataset_id), {"data.gov.tw"})
    soup = BeautifulSoup(response.content, "html.parser")
    if re.sub(r"\s+", "", expected_title) not in re.sub(r"\s+", "", " ".join(soup.stripped_strings)):
        raise RuntimeError(f"資料集 {dataset_id} 未命中預期標題")
    resources: list[dict] = []
    for anchor in soup.select("a[href]"):
        url = anchor.get("href", "")
        if urlparse(url).hostname != download_host:
            continue
        parent = anchor.find_parent("li") or anchor.parent
        strings = list(parent.stripped_strings)
        if not strings:
            continue
        resources.append({
            "resource_id": resource_id(url),
            "format": strings[0],
            "label": strings[-1],
            "url": url,
        })
    if not resources:
        raise RuntimeError(f"資料集 {dataset_id} 沒有官方資源連結")
    return {
        "dataset_id": dataset_id,
        "title": expected_title,
        "publisher": metadata_value(soup, "提供機關"),
        "update_frequency": metadata_value(soup, "更新頻率"),
        "license": metadata_value(soup, "授權方式"),
        "metadata_updated_at": metadata_value(soup, "詮釋資料更新時間"),
        "page": response_evidence(response),
        "resources": resources,
    }


def latest_dated_resource(resources: list[dict], pattern: str) -> dict:
    dated: list[tuple[str, dict]] = []
    for item in resources:
        match = re.search(pattern, item["label"])
        if match:
            dated.append(("-".join(match.groups()), item))
    if not dated:
        raise RuntimeError("找不到含統計期的資料資源")
    return max(dated, key=lambda pair: pair[0])[1]


def integer(value: object) -> int:
    parsed = int(str(value).replace(",", "").strip())
    if parsed < 0:
        raise ValueError("統計值不得為負數")
    return parsed


def analyze_crime(rows: list[dict]) -> dict:
    required = {"項目", "欄位名稱", "數值", "資料時間日期", "資料週期"}
    if not rows or not required <= rows[0].keys():
        raise RuntimeError("警政統計缺少必要欄位")
    matches: list[dict] = []
    for index, row in enumerate(rows):
        if row["項目"] != CRIME_ITEM or not row["欄位名稱"].endswith("_詐欺"):
            continue
        unit = row["欄位名稱"].rsplit("_", 1)[0]
        matches.append({"row_index": index, "unit": unit, "value": integer(row["數值"])})
    units = {row["unit"] for row in matches}
    if units != CRIME_UNITS or len(matches) != len(CRIME_UNITS):
        raise RuntimeError(f"詐欺發生數單位不完整：預期 {len(CRIME_UNITS)}，取得 {len(matches)}")
    periods = {row["資料時間日期"] for row in rows if row["項目"] == CRIME_ITEM}
    cycles = {row["資料週期"] for row in rows if row["項目"] == CRIME_ITEM}
    if len(periods) != 1 or cycles != {"月"}:
        raise RuntimeError("警政統計期間不一致或不是月資料")
    return {
        "statistical_period": next(iter(periods))[:7],
        "parsed_count": len(rows),
        "matched_count": len(matches),
        "fraud_occurrence_count": sum(row["value"] for row in matches),
        "normalized_rows": sorted(matches, key=lambda row: row["unit"]),
    }


def analyze_population(rows: list[dict]) -> dict:
    required = {"地區", "項目", "欄位名稱", "數值", "資料時間日期", "資料週期"}
    if not rows or not required <= rows[0].keys():
        raise RuntimeError("人口統計缺少必要欄位")
    matches = [row for row in rows if row["項目"] == POPULATION_ITEM]
    regions = {row["地區"] for row in matches}
    if len(regions) != 29 or len(matches) != 29 * len(POPULATION_FIELDS):
        raise RuntimeError(f"人口分母不完整：預期 29 區共 174 列，取得 {len(matches)}")
    for region in regions:
        fields = {row["欄位名稱"] for row in matches if row["地區"] == region}
        if fields != POPULATION_FIELDS:
            raise RuntimeError(f"{region} 人口欄位不完整")
    periods = {row["資料時間日期"] for row in matches}
    cycles = {row["資料週期"] for row in matches}
    if len(periods) != 1 or cycles != {"年"}:
        raise RuntimeError("人口統計期間不一致或不是年資料")
    region_totals = {
        region: sum(integer(row["數值"]) for row in matches if row["地區"] == region)
        for region in sorted(regions)
    }
    return {
        "statistical_period": next(iter(periods))[:4],
        "parsed_count": len(rows),
        "matched_count": len(matches),
        "population": sum(region_totals.values()),
        "region_count": len(regions),
        "region_totals": region_totals,
    }


def roc_month_to_iso(value: str) -> str:
    if not re.fullmatch(r"\d{5}", value or ""):
        raise ValueError(f"無效民國年月：{value!r}")
    year, month = int(value[:3]) + 1911, int(value[3:])
    if not 1 <= month <= 12:
        raise ValueError(f"無效月份：{value!r}")
    return f"{year:04d}-{month:02d}"


def analyze_fraud_csv(content: bytes) -> dict:
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
    if not reader.fieldnames or not FRAUD_FIELDS <= set(reader.fieldnames):
        raise RuntimeError("165 CSV 缺少必要欄位")
    rows = list(reader)
    if not rows:
        raise RuntimeError("165 CSV 沒有資料")
    periods: Counter[str] = Counter()
    latest_categories: Counter[str] = Counter()
    domains: set[str] = set()
    normalized_periods: list[tuple[str, dict]] = []
    for row in rows:
        period = roc_month_to_iso(row["民國年月"])
        domain = row["網域"].strip().lower()
        if not domain:
            raise RuntimeError("165 CSV 含空白網域")
        periods[period] += 1
        domains.add(domain)
        normalized_periods.append((period, row))
    latest_period = max(periods)
    for period, row in normalized_periods:
        if period == latest_period:
            latest_categories[row["網站性質"].strip() or "未分類"] += 1
    top_categories = [
        {"category": category, "count": count}
        for category, count in sorted(latest_categories.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]
    return {
        "parsed_count": len(rows),
        "unique_domain_count": len(domains),
        "duplicate_domain_rows": len(rows) - len(domains),
        "latest_statistical_period": latest_period,
        "latest_period_count": periods[latest_period],
        "latest_top_categories": top_categories,
    }


def rate_per_100k(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        raise ValueError("人口分母必須大於零")
    value = Decimal(numerator) * Decimal(100_000) / Decimal(denominator)
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def fetch_s028_crime(session: requests.Session) -> dict:
    dataset = fetch_dataset_page(
        session, CRIME_DATASET_ID, "10952-01-01-2 臺中市受(處)理刑事案件-分局別",
        "newdatacenter.taichung.gov.tw",
    )
    resource = latest_dated_resource(dataset["resources"], r"\b(20\d{2})-(\d{2})_")
    response = get(session, resource["url"], {"newdatacenter.taichung.gov.tw"}, 120)
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("警政統計資源不是 JSON 陣列")
    return {
        "source_id": "S-028",
        "role": "NUMERATOR",
        "canary_status": "PASS",
        "snapshot_completeness": "FULL_RESOURCE_PARSED",
        "dataset": {key: value for key, value in dataset.items() if key != "resources"},
        "resource": {**resource, **response_evidence(response)},
        "analysis": analyze_crime(rows),
    }


def fetch_population(session: requests.Session) -> dict:
    dataset = fetch_dataset_page(
        session, POPULATION_DATASET_ID, "10122-00-01-2 臺中市各區戶數、人口數按戶別及性別分",
        "newdatacenter.taichung.gov.tw",
    )
    resource = latest_dated_resource(dataset["resources"], r"\b(20\d{2})_")
    response = get(session, resource["url"], {"newdatacenter.taichung.gov.tw"}, 120)
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("人口統計資源不是 JSON 陣列")
    return {
        "source_id": "S-028",
        "role": "DENOMINATOR",
        "canary_status": "PASS",
        "snapshot_completeness": "FULL_RESOURCE_PARSED",
        "dataset": {key: value for key, value in dataset.items() if key != "resources"},
        "resource": {**resource, **response_evidence(response)},
        "analysis": analyze_population(rows),
    }


def fetch_fraud_context(session: requests.Session) -> dict:
    dataset = fetch_dataset_page(
        session, FRAUD_DATASET_ID, "165反詐騙諮詢專線_遭停止解析涉詐網站",
        "opdadm.moi.gov.tw",
    )
    if len(dataset["resources"]) != 1:
        raise RuntimeError(f"165 資料集資源數異常：{len(dataset['resources'])}")
    resource = dataset["resources"][0]
    response = get(session, resource["url"], {"opdadm.moi.gov.tw"}, 120)
    return {
        "source_id": None,
        "candidate_name": "165 全民防騙公開統計候選",
        "role": "CONTEXT_ONLY",
        "canary_status": "PASS",
        "snapshot_completeness": "FULL_RESOURCE_PARSED",
        "dataset": {key: value for key, value in dataset.items() if key != "resources"},
        "resource": {**resource, **response_evidence(response)},
        "analysis": analyze_fraud_csv(response.content),
    }


def build_card(crime: dict, population: dict, fraud: dict) -> dict:
    numerator = crime["analysis"]["fraud_occurrence_count"]
    denominator = population["analysis"]["population"]
    base = {
        "card_id": "trend-taichung-fraud-baseline",
        "claim_type": "STATISTICAL_BASELINE",
        "title": "臺中市詐欺案件公開統計基線",
        "metric_id": "TAICHUNG_FRAUD_OCCURRENCE_PER_100K",
        "value": numerator,
        "unit": "件",
        "statistical_period": crime["analysis"]["statistical_period"],
        "rate_per_100k": rate_per_100k(numerator, denominator),
        "formula": "fraud_occurrence_count / population * 100000",
        "formula_version": "fraud-rate-v1",
        "denominator": denominator,
        "denominator_period": population["analysis"]["statistical_period"],
        "comparison_status": "BASELINE_ONLY",
        "comparability_status": "LIMITED_DENOMINATOR_PERIOD_MISMATCH",
        "context": {
            "description": "165 涉詐網站為全國脈絡，不與臺中案件數直接相加或比較。",
            "latest_period": fraud["analysis"]["latest_statistical_period"],
            "stopped_domain_rows": fraud["analysis"]["latest_period_count"],
            "top_categories": fraud["analysis"]["latest_top_categories"],
        },
        "evidence_locators": [
            {
                "dataset_id": CRIME_DATASET_ID,
                "resource_id": crime["resource"]["resource_id"],
                "locator_type": "JSON_FILTER",
                "locator": f"項目={CRIME_ITEM};欄位名稱=*_詐欺",
                "content_sha256": crime["resource"]["raw_sha256"],
            },
            {
                "dataset_id": POPULATION_DATASET_ID,
                "resource_id": population["resource"]["resource_id"],
                "locator_type": "JSON_FILTER",
                "locator": f"項目={POPULATION_ITEM}",
                "content_sha256": population["resource"]["raw_sha256"],
            },
            {
                "dataset_id": FRAUD_DATASET_ID,
                "resource_id": fraud["resource"]["resource_id"],
                "locator_type": "CSV_COLUMNS",
                "locator": "民國年月,網站性質",
                "content_sha256": fraud["resource"]["raw_sha256"],
            },
        ],
    }
    return {**base, "derived_output_sha256": manifest_sha256(base)}


def stable_manifest(sources: dict, card: dict) -> dict:
    return {
        "crime": {
            "dataset_id": CRIME_DATASET_ID,
            "metadata_updated_at": sources["crime"]["dataset"]["metadata_updated_at"],
            "resource_id": sources["crime"]["resource"]["resource_id"],
            "resource_sha256": sources["crime"]["resource"]["raw_sha256"],
            "analysis": sources["crime"]["analysis"],
        },
        "population": {
            "dataset_id": POPULATION_DATASET_ID,
            "metadata_updated_at": sources["population"]["dataset"]["metadata_updated_at"],
            "resource_id": sources["population"]["resource"]["resource_id"],
            "resource_sha256": sources["population"]["resource"]["raw_sha256"],
            "analysis": sources["population"]["analysis"],
        },
        "fraud_context": {
            "dataset_id": FRAUD_DATASET_ID,
            "metadata_updated_at": sources["fraud_context"]["dataset"]["metadata_updated_at"],
            "resource_id": sources["fraud_context"]["resource"]["resource_id"],
            "resource_sha256": sources["fraud_context"]["resource"]["raw_sha256"],
            "analysis": sources["fraud_context"]["analysis"],
        },
        "card": card,
    }


def self_check() -> None:
    assert resource_id("https://example.test/x?rid=abc") == "abc"
    assert resource_id("https://example.test/dataset/d/resource/r/download") == "r"
    assert roc_month_to_iso("11507") == "2026-07"
    assert rate_per_100k(1670, 2_868_465) == "58.22"
    crime_rows = [
        {"項目": CRIME_ITEM, "欄位名稱": f"{unit}_詐欺", "數值": "1", "資料時間日期": "2026-06-01T00:00:00", "資料週期": "月"}
        for unit in CRIME_UNITS
    ]
    assert analyze_crime(crime_rows)["fraud_occurrence_count"] == len(CRIME_UNITS)
    population_rows = [
        {"地區": f"臺中市測試{index:02d}區", "項目": POPULATION_ITEM, "欄位名稱": field, "數值": "10", "資料時間日期": "2025-01-01T00:00:00", "資料週期": "年"}
        for index in range(29) for field in POPULATION_FIELDS
    ]
    assert analyze_population(population_rows)["population"] == 29 * len(POPULATION_FIELDS) * 10
    fraud_csv = "民國年月,網域,網站性質,法律依據,聲請單位\n11507,a.test,電子商務,法規,機關\n11507,b.test,金融保險,法規,機關\n".encode()
    assert analyze_fraud_csv(fraud_csv)["latest_period_count"] == 2
    assert manifest_sha256({"b": 2, "a": 1}) == manifest_sha256({"a": 1, "b": 2})
    print("SELF_CHECK_OK")


def write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return

    checked_at = datetime.now(TZ)
    output = args.output or ROOT / f"source-live-canary-s028-165-{checked_at:%Y%m%dT%H%M%S}.json"
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-TW,zh;q=0.9"})
    sources: dict[str, dict] = {}
    fetchers = {
        "crime": fetch_s028_crime,
        "population": fetch_population,
        "fraud_context": fetch_fraud_context,
    }
    for name, fetcher in fetchers.items():
        try:
            sources[name] = fetcher(session)
        except Exception as exc:  # 保留來源級失敗，不把失敗改寫成零筆。
            sources[name] = {
                "canary_status": "FAIL",
                "source_health": "FETCH_OR_PARSE_FAILED",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }

    passed = all(source.get("canary_status") == "PASS" for source in sources.values())
    card = build_card(sources["crime"], sources["population"], sources["fraud_context"]) if passed else None
    result = {
        "status": "PASS" if passed else "FAIL",
        "checked_at": checked_at.isoformat(timespec="seconds"),
        "script": Path(__file__).name,
        "script_sha256": sha256(Path(__file__).read_bytes()),
        "sources": sources,
        "trend_card": card,
        "manifest_sha256": manifest_sha256(stable_manifest(sources, card)) if passed and card else None,
    }
    write_result(output, result)
    print(f"STATUS={result['status']}")
    for name, source in sources.items():
        print(f"{name}={source['canary_status']}")
    if card:
        print(
            f"CARD={card['value']}件 RATE_PER_100K={card['rate_per_100k']} "
            f"PERIOD={card['statistical_period']} COMPARISON={card['comparison_status']}"
        )
    print(f"MANIFEST={result['manifest_sha256']}")
    print(f"OUTPUT={output.resolve()}")
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
