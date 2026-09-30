"""Small, offline NPA adapters used by the source-inventory fixtures.

These adapters normalize only the fields needed by the bounded PublicEvent and
reference contracts.  They do not fetch URLs or promote candidates to live data.
"""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime
import hashlib
import json
import re
from typing import Any


NPA_11307_URL = "https://data.gov.tw/dataset/11307"
NPA_172159_URL = "https://data.gov.tw/dataset/172159"
NPA_172159_SCHEMA_VERSION = "npa-172159.v1"
NPA_165_DATASETS = {"38262", "160055", "176455"}
PERSONAL_DATASET_IDS = {"14420", "STOLEN-VEHICLES", "LOST-PROPERTY"}


def _text(row: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return None


def _required_text(row: dict[str, Any], *keys: str) -> str:
    value = _text(row, *keys)
    if not value:
        raise ValueError(f"missing required field: {'/'.join(keys)}")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any, length: int = 16) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:length]


def _timestamp(value: str, field: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"invalid {field}: {value}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return parsed.isoformat(timespec="seconds")


def parse_assembly_records(
    rows: list[dict[str, Any]],
    *,
    observed_at: str,
    source_id: str = "S-036",
    official_url: str = NPA_11307_URL,
) -> list[dict[str, Any]]:
    """Convert dataset 11307 rows into normalized official PublicEvent documents."""
    if not isinstance(rows, list):
        raise ValueError("assembly rows must be an array")
    observed = _timestamp(observed_at, "observed_at")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("assembly row must be an object")
        start = _timestamp(_required_text(row, "event_start_at", "活動開始時間"), "event_start_at")
        end = _timestamp(_required_text(row, "event_end_at", "活動結束時間"), "event_end_at")
        route = _required_text(row, "route_or_venue", "集會處所或遊行路線", "地點")
        authority = _required_text(row, "authority", "主管機關")
        category = _required_text(row, "event_category", "活動類別")
        source_record_id = _text(row, "record_id", "活動編號")
        identity = {
            "start": start,
            "end": end,
            "route_or_venue": route,
            "authority": authority,
        }
        stable_identity = f"assembly:{_digest(identity, 20)}"
        named_event_id = f"assembly:{source_record_id}" if source_record_id else stable_identity
        title = _text(row, "title", "活動名稱") or f"{category}：{route}"
        document_id = f"{source_id}:{source_record_id or stable_identity}"
        result.append(
            {
                "document_id": document_id,
                "document_version_id": f"{document_id}:{_digest(row)}",
                "source_id": source_id,
                "independent_source_id": "NPA-11307",
                "authority": "official",
                "title": title,
                "event_type": "assembly_or_protest",
                "named_event_id": named_event_id,
                "stable_identity": stable_identity,
                "event_start_at": start,
                "event_end_at": end,
                "district_id": _text(row, "district_id", "行政區"),
                "agency_ids": [authority],
                "location_ids": [route],
                "official_url": _text(row, "official_url", "官方網址") or official_url,
                "observed_at": observed,
                "semantic_fields": identity,
                "source_record_id": source_record_id,
            }
        )
    return result


def compare_assembly_versions(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic semantic-change receipt for one assembly identity."""
    fields = ("event_start_at", "event_end_at", "location_ids", "agency_ids")
    changed = [field for field in fields if previous.get(field) != current.get(field)]
    return {
        "change_type": "MATERIAL_CHANGE" if changed else "UNCHANGED",
        "semantic_change": bool(changed),
        "changed_fields": changed,
    }


def parse_important_statistics_rows(
    rows: list[dict[str, Any]], *, observed_at: str, source_url: str = "https://www.npa.gov.tw/"
) -> list[dict[str, Any]]:
    """Keep statistical period, table identity, value and official notes together."""
    if not isinstance(rows, list):
        raise ValueError("statistics rows must be an array")
    observed = _timestamp(observed_at, "observed_at")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("statistics row must be an object")
        period = _required_text(row, "period", "統計期別", "資料期別")
        table_id = _required_text(row, "table_id", "表號")
        value = row.get("value", row.get("數值", row.get("機關別值")))
        if value in (None, ""):
            raise ValueError("statistics row missing value")
        result.append(
            {
                "record_type": "REFERENCE_STATISTIC",
                "source_id": "NPA-IMPORTANT-STATS",
                "table_id": table_id,
                "period": period,
                "published_at": _text(row, "published_at", "發布日"),
                "value": value,
                "official_notes": _text(row, "official_notes", "官方註記", "備註") or "",
                "source_url": source_url,
                "observed_at": observed,
                "realtime_allowed": False,
                "role": "PRIMARY_REFERENCE",
            }
        )
    return result


def parse_fraud_effectiveness_rows(rows: list[dict[str, Any]], *, observed_at: str) -> list[dict[str, Any]]:
    """Normalize verified dataset 172159 CSV rows as period-bound reference data.

    This deliberately accepts the official CSV labels only. Dataset 172159 uses
    ROC years and monthly periods; its metadata update frequency is irregular.
    The observed CSV encodes non-negative integers with optional comma grouping.
    """
    if not isinstance(rows, list):
        raise ValueError("fraud effectiveness rows must be an array")
    observed = _timestamp(observed_at, "observed_at")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("fraud effectiveness row must be an object")
        roc_year = _required_text(row, "年度")
        if not re.fullmatch(r"\d{3}", roc_year) or int(roc_year) == 0:
            raise ValueError("年度 must be a three-digit ROC year")
        month_text = _required_text(row, "月")
        if not re.fullmatch(r"\d{1,2}", month_text):
            raise ValueError("月 must be a numeric month")
        month = int(month_text)
        if not 1 <= month <= 12:
            raise ValueError("月 must be between 1 and 12")

        def metric(field: str) -> int:
            value = row.get(field)
            if isinstance(value, bool):
                raise ValueError(f"{field} must be a non-negative integer")
            if isinstance(value, int):
                parsed = value
            elif isinstance(value, str):
                value = value.strip()
                if not re.fullmatch(r"(?:\d+|[1-9]\d{0,2}(?:,\d{3})+)", value):
                    raise ValueError(f"{field} must be a non-negative integer")
                parsed = int(value.replace(",", ""))
            else:
                raise ValueError(f"{field} must be a non-negative integer")
            if parsed < 0:
                raise ValueError(f"{field} must be a non-negative integer")
            return parsed

        metrics = {
            "groups": metric("查緝不法犯罪集團團數"),
            "people": metric("查緝不法犯罪集團人數"),
            "seized_amount": metric("查扣不法所得金額"),
            "prevented_amount": metric("攔阻金額"),
        }
        source_period = f"{roc_year}-{month:02d}"
        gregorian_year = int(roc_year) + 1911
        result.append(
            {
                "record_type": "FRAUD_EFFECTIVENESS_REFERENCE",
                "source_id": "NPA-172159",
                "dataset_id": "172159",
                "source_url": NPA_172159_URL,
                "source_schema_version": NPA_172159_SCHEMA_VERSION,
                "source_calendar": "ROC",
                "source_period": source_period,
                "period": f"{gregorian_year}-{month:02d}",
                "metrics": metrics,
                "observed_at": observed,
                "realtime_allowed": False,
                "local_case_count_allowed": False,
                "role": "PRIMARY_REFERENCE",
            }
        )
    return result


def parse_165_records(rows: list[dict[str, Any]], *, dataset_id: str) -> list[dict[str, Any]]:
    """Normalize 165 family records without treating them as local case counts."""
    dataset_id = str(dataset_id)
    if dataset_id not in NPA_165_DATASETS:
        raise ValueError(f"unsupported 165 dataset: {dataset_id}")
    result = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("165 row must be an object")
        title = _text(row, "title", "標題") or ""
        content = _text(row, "content", "發布內容") or ""
        domain = _text(row, "domain", "網域")
        domain_key = re.sub(r"^https?://", "", domain.lower()).rstrip("/") if domain else ""
        dedupe_key = f"domain:{domain_key}" if domain_key else f"text:{_digest({'title': title, 'content': content}, 20)}"
        result.append(
            {
                "record_type": "FRAUD_REFERENCE",
                "dataset_id": dataset_id,
                "source_id": f"NPA-{dataset_id}",
                "independent_source_id": "NPA-165-FAMILY",
                "record_id": _text(row, "record_id", "資料編號") or _digest(row, 20),
                "dedupe_key": dedupe_key,
                "domain": domain,
                "title": title,
                "content": content,
                "published_at": _text(row, "published_at", "發布時間"),
                "realtime_allowed": False,
                "local_case_count_allowed": False,
                "related_source_ids": ["NPA-38262", "NPA-160055", "CTX-165"],
                "superseded_by_source_id": "CTX-165" if dataset_id == "160055" else None,
                "role": "PRIMARY_REFERENCE",
            }
        )
    return result


def dedupe_165_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe equivalent domains/text across the three 165 acquisition paths."""
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for record in records:
        key = record.get("dedupe_key")
        if not isinstance(key, str) or not key:
            raise ValueError("165 record missing dedupe_key")
        if key not in grouped:
            grouped[key] = dict(record)
            grouped[key]["dataset_ids"] = [record["dataset_id"]]
            continue
        current = grouped[key]
        current["dataset_ids"] = sorted(set(current["dataset_ids"]) | {record["dataset_id"]})
        current["related_source_ids"] = sorted(set(current["related_source_ids"]) | set(record.get("related_source_ids", [])))
        if current.get("superseded_by_source_id") is None:
            current["superseded_by_source_id"] = record.get("superseded_by_source_id")
    return list(grouped.values())


def assert_public_canonical_allowed(record: dict[str, Any]) -> None:
    """Fail closed for case-level personal records before public projection."""
    dataset_id = str(record.get("dataset_id", "")).upper()
    if record.get("contains_personal_data") or dataset_id in PERSONAL_DATASET_IDS:
        raise ValueError("personal/case-level record is excluded from public canonical feed")
    if record.get("role") == "EXCLUDE_OR_AGGREGATE_ONLY":
        raise ValueError("excluded record cannot enter public canonical feed")
