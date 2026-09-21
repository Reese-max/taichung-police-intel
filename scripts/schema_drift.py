#!/usr/bin/env python3
"""Fail-closed source contract checks with last-known-good preservation."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.util
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / "state/schema-drift-state.json"
DEFAULT_OUTPUT = ROOT / "apps/web/public/data/schema-drift.json"
VALID_STATUSES = {
    "NO_DRIFT",
    "ADDITIVE_COMPATIBLE",
    "BREAKING_DRIFT",
    "CONTENT_SHAPE_UNKNOWN",
    "SOURCE_UNAVAILABLE",
}
GOOD_STATUSES = {"NO_DRIFT", "ADDITIVE_COMPATIBLE"}
CONTRACT_VERSION = "1.0"


def _news(source_id: str, name: str, pattern: str, *, published_required: bool = True) -> dict[str, Any]:
    required_fields = ["stable_key", "title", "detail_url"]
    if published_required:
        required_fields.append("published")
    return {
        "source_id": source_id,
        "source_name": name,
        "contract_version": CONTRACT_VERSION,
        "transport": "HTML_LIST_DETAIL",
        "expected_content_types": ["text/html"],
        "parser_version": "online_collect:p0-live-1",
        "required_fields": required_fields,
        "optional_fields": ["detail", "body_sha256", "attachments"] + ([] if published_required else ["published"]),
        "published_required": published_required,
        "id_pattern": pattern,
    }


def _rss_news(source_id: str, name: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_name": name,
        "contract_version": CONTRACT_VERSION,
        "transport": "RSS_LIST_DETAIL",
        "expected_content_types": ["application/xml", "text/xml"],
        "parser_version": "online_collect:p0-live-1",
        "required_fields": ["stable_key", "title", "detail_url", "published"],
        "optional_fields": ["detail", "body_sha256", "attachments"],
        "published_required": True,
    }


def _fire_live(source_id: str, name: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "source_name": name,
        "contract_version": CONTRACT_VERSION,
        "transport": "HTML_LIVE_SNAPSHOT",
        "expected_content_types": ["text/html"],
        "parser_version": "online_collect:p0-live-1",
        "required_fields": [
            "stable_key",
            "category",
            "event_type",
            "district",
            "observed_at",
            "dispatch_unit",
            "status",
            "source_modified_at",
        ],
        "optional_fields": [],
    }


CONTRACTS: dict[str, dict[str, Any]] = {
    "S-001": _news(
        "S-001",
        "臺中市政府警察局警政新聞",
        r"news_view\.jsp[^\"']*dataserno=(\d+)",
    ),
    "S-019": _news(
        "S-019",
        "臺中市政府市政會議紀錄與專案報告",
        r"/(\d+)/post\b",
        published_required=False,
    ),
    "S-032": _news(
        "S-032",
        "臺中市政府交通局最新消息",
        r"index-1\.asp\?Parser=9,4,20,,,,(\d+)",
    ),
    "S-033": _rss_news("S-033", "臺中市政府新聞局最新消息"),
    "S-031": _fire_live("S-031", "臺中市政府消防局即時災情"),
    "S-007": {
        "source_id": "S-007",
        "source_name": "臺中市議會議事資訊系統－議事錄",
        "contract_version": CONTRACT_VERSION,
        "transport": "JSON_API",
        "expected_content_types": ["application/json"],
        "parser_version": "online_collect:p0-live-1",
        "required_paths": ["success", "data", "data.data", "data.totalPages", "data.totalCount"],
        "type_paths": {
            "success": "boolean",
            "data": "object",
            "data.data": "array",
            "data.totalPages": "integer",
            "data.totalCount": "integer",
        },
        "record_required_fields": ["proceedingsId", "date", "speaker", "content"],
        "record_type_fields": {
            "proceedingsId": "string",
            "date": "string",
            "speaker": "string",
            "content": "string",
        },
        "pagination_paths": ["data.totalPages", "data.totalCount"],
    },
    "S-009": {
        "source_id": "S-009",
        "source_name": "臺中市議會議事資訊系統－各項提案",
        "contract_version": CONTRACT_VERSION,
        "transport": "JSON_API",
        "expected_content_types": ["application/json"],
        "parser_version": "online_collect:p0-live-1",
        "required_paths": ["success", "data", "data.data", "data.totalPages", "data.totalCount"],
        "type_paths": {
            "success": "boolean",
            "data": "object",
            "data.data": "array",
            "data.totalPages": "integer",
            "data.totalCount": "integer",
        },
        "record_required_fields": ["billId"],
        "record_type_fields": {"billId": "string"},
        "pagination_paths": ["data.totalPages", "data.totalCount"],
    },
    "S-028": {
        "source_id": "S-028",
        "source_name": "臺中市受（處）理刑事案件－分局別",
        "contract_version": CONTRACT_VERSION,
        "transport": "DATA_GOV_JSON",
        "expected_content_types": ["application/json", "application/octet-stream"],
        "parser_version": "canary-s028-165:1",
        "dataset_id": "88147",
        "required_fields": ["項目", "欄位名稱", "數值", "資料時間日期", "資料週期"],
        "field_types": {field: "string" for field in ["項目", "欄位名稱", "數值", "資料時間日期", "資料週期"]},
        "resource_id_required": True,
    },
    "CTX-POP": {
        "source_id": "CTX-POP",
        "source_name": "臺中市各區戶數、人口數按戶別及性別分",
        "contract_version": CONTRACT_VERSION,
        "transport": "DATA_GOV_JSON",
        "expected_content_types": ["application/json", "application/octet-stream"],
        "parser_version": "canary-s028-165:1",
        "dataset_id": "103703",
        "required_fields": ["地區", "項目", "欄位名稱", "數值", "資料時間日期", "資料週期"],
        "field_types": {field: "string" for field in ["地區", "項目", "欄位名稱", "數值", "資料時間日期", "資料週期"]},
        "resource_id_required": True,
    },
    "CTX-165": {
        "source_id": "CTX-165",
        "source_name": "165反詐騙諮詢專線－遭停止解析涉詐網站",
        "contract_version": CONTRACT_VERSION,
        "transport": "DATA_GOV_CSV",
        "expected_content_types": ["text/csv", "application/csv", "application/octet-stream"],
        "parser_version": "canary-s028-165:1",
        "dataset_id": "176455",
        "required_fields": ["民國年月", "網域", "網站性質", "法律依據", "聲請單位"],
        "resource_id_required": True,
        "expected_column_count": 5,
    },
}


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def get_path(value: Any, path: str) -> tuple[bool, Any]:
    current = value
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _base_result(contract: dict[str, Any], body: bytes, observed_at: str | None, **meta: Any) -> dict[str, Any]:
    return {
        "source_id": contract["source_id"],
        "source_name": contract["source_name"],
        "contract_version": contract["contract_version"],
        "transport": contract["transport"],
        "parser_version": contract["parser_version"],
        "observed_schema_fingerprint": None,
        "sample_sha256": sha256(body),
        "resource_id": meta.get("resource_id"),
        "resource_id_changed": False,
        "status": "CONTENT_SHAPE_UNKNOWN",
        "window_completeness": "PARTIAL",
        "source_health": "DEGRADED",
        "reasons": [],
        "review_required": False,
        "observed_at": observed_at or now_iso(),
        "requested_url": meta.get("requested_url"),
        "final_url": meta.get("final_url"),
    }


def _finish(result: dict[str, Any], signature: Any, status: str, reasons: list[str]) -> dict[str, Any]:
    if status not in VALID_STATUSES:
        raise ValueError(f"invalid drift status: {status}")
    result["observed_schema_fingerprint"] = canonical_hash(signature)
    result["status"] = status
    result["reasons"] = sorted(set(reasons))
    result["review_required"] = status in {"BREAKING_DRIFT", "SOURCE_UNAVAILABLE"} or bool(result.get("resource_id_changed"))
    if status in GOOD_STATUSES:
        result["window_completeness"] = "COMPLETE_WITH_ITEMS"
        result["source_health"] = "PASS"
    elif status == "SOURCE_UNAVAILABLE":
        result["source_health"] = "FAILED"
    return result


def _content_type_ok(content_type: str, expected: list[str]) -> bool:
    actual = (content_type or "").split(";", 1)[0].strip().lower()
    return actual in {item.lower() for item in expected}


def _resource_drift(result: dict[str, Any], previous: dict[str, Any] | None) -> None:
    old = previous.get("resource_id") if previous else None
    current = result.get("resource_id")
    if old and current and old != current:
        result["resource_id_changed"] = True
        result["reasons"].append("RESOURCE_ID_CHANGED")
        if result["status"] == "NO_DRIFT":
            result["status"] = "ADDITIVE_COMPATIBLE"


def observe(
    contract: dict[str, Any],
    body: bytes | str,
    *,
    http_status: int = 200,
    content_type: str = "",
    resource_id: str | None = None,
    observed_at: str | None = None,
    requested_url: str | None = None,
    final_url: str | None = None,
    error_reason: str | None = None,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = body.encode("utf-8") if isinstance(body, str) else body
    result = _base_result(
        contract,
        raw,
        observed_at,
        resource_id=resource_id,
        requested_url=requested_url,
        final_url=final_url,
    )
    if http_status < 200 or http_status >= 300:
        result["reasons"] = [error_reason or f"HTTP_{http_status}"]
        result["status"] = "SOURCE_UNAVAILABLE"
        result["review_required"] = True
        return result
    if not _content_type_ok(content_type, contract["expected_content_types"]):
        result["reasons"] = ["CONTENT_TYPE_MISMATCH"]
        return result

    transport = contract["transport"]
    if contract.get("resource_id_required") and not resource_id:
        return _finish(
            result,
            {"transport": transport, "resource_id": None},
            "BREAKING_DRIFT",
            ["RESOURCE_ID_MISSING"],
        )
    if transport == "HTML_LIST_DETAIL":
        result = _observe_html(contract, raw, result, previous)
    elif transport == "RSS_LIST_DETAIL":
        result = _observe_rss(contract, raw, result, previous)
    elif transport == "HTML_LIVE_SNAPSHOT":
        result = _observe_fire_live(contract, raw, result, previous)
    elif transport == "JSON_API":
        result = _observe_json_api(contract, raw, result, previous)
    elif transport == "DATA_GOV_JSON":
        result = _observe_data_gov_json(contract, raw, result, previous)
    elif transport == "DATA_GOV_CSV":
        result = _observe_data_gov_csv(contract, raw, result, previous)
    else:
        result["reasons"] = ["UNSUPPORTED_TRANSPORT"]
    _resource_drift(result, previous)
    result["review_required"] = result["status"] in {"BREAKING_DRIFT", "SOURCE_UNAVAILABLE"} or bool(result.get("resource_id_changed"))
    return result


def _observe_html(contract: dict[str, Any], body: bytes, result: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from online_collect import parse_news_list

        entries = parse_news_list(body, result["final_url"] or "https://invalid.local/", contract["id_pattern"])
    except Exception as error:
        result["reasons"] = ["HTML_LIST_ID_OR_SELECTOR_FAILED", type(error).__name__.upper()]
        result["status"] = "BREAKING_DRIFT"
        result["observed_schema_fingerprint"] = canonical_hash({"transport": "HTML", "parse": "failed"})
        result["review_required"] = True
        return result
    fields = sorted({key for entry in entries for key in entry})
    date_coverage = sum(entry.get("published") is not None for entry in entries)
    signature = {
        "transport": contract["transport"],
        "entry_fields": fields,
        "date_coverage": f"{date_coverage}/{len(entries)}",
        "id_pattern": contract["id_pattern"],
    }
    reasons: list[str] = []
    status = "NO_DRIFT"
    if set(contract["required_fields"]) - set(fields):
        status = "BREAKING_DRIFT"
        reasons.append("REQUIRED_FIELD_MISSING")
    elif contract.get("published_required", True) and date_coverage != len(entries):
        status = "BREAKING_DRIFT"
        reasons.append("REQUIRED_PUBLISHED_DATE_MISSING")
    return _finish(result, signature, status, reasons)


def _observe_rss(contract: dict[str, Any], body: bytes, result: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from online_collect import parse_news_rss

        entries = parse_news_rss(body, result["final_url"] or "https://invalid.local/")
    except Exception as error:
        result["reasons"] = ["RSS_LIST_SHAPE_FAILED", type(error).__name__.upper()]
        result["status"] = "BREAKING_DRIFT"
        result["observed_schema_fingerprint"] = canonical_hash({"transport": "RSS", "parse": "failed"})
        result["review_required"] = True
        return result
    fields = sorted({key for entry in entries for key in entry})
    signature = {
        "transport": contract["transport"],
        "entry_fields": fields,
        "date_coverage": f"{sum(entry.get('published') is not None for entry in entries)}/{len(entries)}",
    }
    missing = set(contract["required_fields"]) - set(fields)
    return _finish(result, signature, "BREAKING_DRIFT" if missing else "NO_DRIFT", ["REQUIRED_FIELD_MISSING"] if missing else [])


def _observe_fire_live(contract: dict[str, Any], body: bytes, result: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from online_collect import parse_fire_live

        entries = parse_fire_live(body)
    except Exception as error:
        result["reasons"] = ["HTML_LIVE_SHAPE_FAILED", type(error).__name__.upper()]
        result["status"] = "BREAKING_DRIFT"
        result["observed_schema_fingerprint"] = canonical_hash({"transport": "HTML_LIVE", "parse": "failed"})
        result["review_required"] = True
        return result
    fields = sorted({key for entry in entries for key in entry})
    signature = {
        "transport": contract["transport"],
        "entry_fields": fields,
        "entry_count": len(entries),
    }
    missing = sorted(set(contract["required_fields"]) - set(fields))
    status = "BREAKING_DRIFT" if missing else "NO_DRIFT"
    result = _finish(result, signature, status, ["REQUIRED_FIELD_MISSING"] if missing else [])
    if status in GOOD_STATUSES:
        result["window_completeness"] = "PARTIAL"
    return result


def _decode_json(body: bytes) -> Any:
    return json.loads(body.decode("utf-8-sig"))


def _observe_json_api(contract: dict[str, Any], body: bytes, result: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    try:
        payload = _decode_json(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        result["reasons"] = ["INVALID_JSON"]
        return result
    missing = [path for path in contract["required_paths"] if not get_path(payload, path)[0]]
    wrong_types = [
        path for path, expected in contract["type_paths"].items()
        if get_path(payload, path)[0] and type_name(get_path(payload, path)[1]) != expected
    ]
    if missing:
        reasons = ["REQUIRED_PATH_MISSING", *[f"MISSING_{path}" for path in missing]]
        if set(missing) & set(contract.get("pagination_paths", [])):
            reasons.append("PAGINATION_MARKER_MISSING")
        return _finish(result, {"transport": contract["transport"], "missing_paths": missing}, "BREAKING_DRIFT", reasons)
    if wrong_types:
        return _finish(result, {"transport": contract["transport"], "wrong_types": wrong_types}, "BREAKING_DRIFT", ["TYPE_CHANGED", *[f"TYPE_{path}" for path in wrong_types]])
    records = get_path(payload, "data.data")[1]
    if not records:
        return _finish(result, {"transport": contract["transport"], "record_fields": []}, "CONTENT_SHAPE_UNKNOWN", ["NO_RECORD_FOR_SCHEMA_CHECK"])
    if not all(isinstance(record, dict) for record in records):
        return _finish(result, {"transport": contract["transport"], "record_types": [type_name(record) for record in records]}, "BREAKING_DRIFT", ["RECORD_NOT_OBJECT"])
    fields = sorted({key for record in records for key in record})
    missing_fields = sorted(set(contract["record_required_fields"]) - set(fields))
    wrong_record_types = [
        field for field, expected in contract["record_type_fields"].items()
        if any(field in record and type_name(record[field]) != expected for record in records)
    ]
    if missing_fields:
        return _finish(result, {"transport": contract["transport"], "record_fields": fields}, "BREAKING_DRIFT", ["REQUIRED_FIELD_MISSING", *[f"MISSING_{field}" for field in missing_fields]])
    if wrong_record_types:
        return _finish(result, {"transport": contract["transport"], "record_fields": fields}, "BREAKING_DRIFT", ["TYPE_CHANGED", *[f"TYPE_{field}" for field in wrong_record_types]])
    extra = sorted(set(fields) - set(contract["record_required_fields"]))
    status = "ADDITIVE_COMPATIBLE" if extra else "NO_DRIFT"
    reasons = ["ADDITIVE_FIELDS"] if extra else []
    signature = {
        "transport": contract["transport"],
        "record_fields": fields,
        "record_types": {field: type_name(records[0].get(field)) for field in fields},
        "pagination": {path: get_path(payload, path)[1] for path in contract["pagination_paths"]},
    }
    return _finish(result, signature, status, reasons)


def _observe_data_gov_json(contract: dict[str, Any], body: bytes, result: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    try:
        payload = _decode_json(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        result["reasons"] = ["INVALID_JSON"]
        return result
    if not isinstance(payload, list):
        return _finish(result, {"transport": contract["transport"], "top_level": type_name(payload)}, "BREAKING_DRIFT", ["JSON_RESOURCE_NOT_ARRAY"])
    if not payload:
        return _finish(result, {"transport": contract["transport"], "record_fields": []}, "CONTENT_SHAPE_UNKNOWN", ["NO_RECORD_FOR_SCHEMA_CHECK"])
    if not all(isinstance(row, dict) for row in payload):
        return _finish(result, {"transport": contract["transport"]}, "BREAKING_DRIFT", ["RECORD_NOT_OBJECT"])
    fields = sorted({key for row in payload for key in row})
    missing = sorted(set(contract["required_fields"]) - set(fields))
    wrong_types = [
        field for field, expected in contract["field_types"].items()
        if any(field in row and type_name(row[field]) != expected for row in payload)
    ]
    if missing:
        return _finish(result, {"transport": contract["transport"], "record_fields": fields}, "BREAKING_DRIFT", ["REQUIRED_FIELD_MISSING", *[f"MISSING_{field}" for field in missing]])
    if wrong_types:
        return _finish(result, {"transport": contract["transport"], "record_fields": fields}, "BREAKING_DRIFT", ["TYPE_CHANGED", *[f"TYPE_{field}" for field in wrong_types]])
    extra = sorted(set(fields) - set(contract["required_fields"]))
    status = "ADDITIVE_COMPATIBLE" if extra else "NO_DRIFT"
    return _finish(
        result,
        {"transport": contract["transport"], "record_fields": fields, "field_types": contract["field_types"]},
        status,
        ["ADDITIVE_FIELDS"] if extra else [],
    )


def _observe_data_gov_csv(contract: dict[str, Any], body: bytes, result: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    try:
        text = body.decode("utf-8-sig")
        rows = list(csv.reader(io.StringIO(text)))
    except UnicodeDecodeError:
        result["reasons"] = ["UNSUPPORTED_ENCODING"]
        return result
    if not rows or not rows[0]:
        return _finish(result, {"transport": contract["transport"]}, "CONTENT_SHAPE_UNKNOWN", ["CSV_HEADER_MISSING"])
    header = rows[0]
    required = set(contract["required_fields"])
    missing = sorted(required - set(header))
    if missing:
        return _finish(result, {"transport": contract["transport"], "header": header}, "BREAKING_DRIFT", ["REQUIRED_HEADER_MISSING", *[f"MISSING_{field}" for field in missing]])
    if len(header) < contract["expected_column_count"]:
        return _finish(result, {"transport": contract["transport"], "header": header}, "BREAKING_DRIFT", ["COLUMN_COUNT_DECREASED"])
    extra = sorted(set(header) - required)
    if len(rows) == 1:
        return _finish(result, {"transport": contract["transport"], "header": header, "column_count": len(header)}, "CONTENT_SHAPE_UNKNOWN", ["NO_DATA_ROWS"])
    status = "ADDITIVE_COMPATIBLE" if extra or len(header) > contract["expected_column_count"] else "NO_DRIFT"
    return _finish(
        result,
        {"transport": contract["transport"], "header": header, "column_count": len(header), "value_types": {field: "string" for field in header}},
        status,
        ["ADDITIVE_FIELDS"] if status == "ADDITIVE_COMPATIBLE" else [],
    )


def _unknown(contract: dict[str, Any], reason: str, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _base_result(contract, b"", None)
    result["reasons"] = [reason]
    result["review_required"] = False
    if previous:
        result["last_known_good"] = previous.get("last_known_good")
    return result


def empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "contract_version": CONTRACT_VERSION, "updated_at": None, "sources": {}}


def update_state(state: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    state = json.loads(json.dumps(state, ensure_ascii=False))
    state.setdefault("schema_version", 1)
    state.setdefault("contract_version", CONTRACT_VERSION)
    state.setdefault("sources", {})
    source_id = result["source_id"]
    old = state["sources"].get(source_id, {})
    current = old.get("current")
    history = list(old.get("history", []))
    if current:
        history.append({key: current.get(key) for key in ("contract_version", "observed_schema_fingerprint", "sample_sha256", "status", "observed_at", "resource_id")})
    if len(history) > 50:
        history = history[-50:]
    last_known_good = old.get("last_known_good")
    if result["status"] in GOOD_STATUSES:
        last_known_good = {key: result.get(key) for key in ("contract_version", "observed_schema_fingerprint", "sample_sha256", "status", "observed_at", "resource_id")}
    state["sources"][source_id] = {
        "current": result,
        "last_known_good": last_known_good,
        "history": history,
    }
    state["updated_at"] = result["observed_at"]
    return state


def build_receipt(
    observations: list[dict[str, Any]] | None = None,
    *,
    state: dict[str, Any] | None = None,
    contracts: dict[str, dict[str, Any]] = CONTRACTS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = state or empty_state()
    observations_by_id = {item["source_id"]: item for item in (observations or [])}
    receipt_sources = []
    review_inbox = []
    for source_id, contract in contracts.items():
        old = state.get("sources", {}).get(source_id, {})
        previous = old.get("current")
        observation = observations_by_id.get(source_id)
        if observation:
            result = observe(contract, observation.get("body", b""), previous=previous, **{key: value for key, value in observation.items() if key not in {"source_id", "body"}})
        else:
            result = _unknown(contract, "NO_CURRENT_OBSERVATION", previous)
        state = update_state(state, result)
        public_result = {key: value for key, value in result.items() if key != "body"}
        source_state = state["sources"][source_id]
        public_result["last_known_good"] = source_state.get("last_known_good")
        public_result["history_count"] = len(source_state.get("history", []))
        receipt_sources.append(public_result)
        if result.get("review_required"):
            review_inbox.append({
                "source_id": source_id,
                "status": result["status"],
                "reasons": result["reasons"],
                "observed_at": result["observed_at"],
                "state": "OPEN",
            })
    statuses = {item["status"] for item in receipt_sources}
    overall = "BLOCKED" if "BREAKING_DRIFT" in statuses or "SOURCE_UNAVAILABLE" in statuses else "DEGRADED" if "ADDITIVE_COMPATIBLE" in statuses else "UNKNOWN" if statuses - {"NO_DRIFT"} else "HEALTHY"
    receipt = {
        "schema_version": 1,
        "contract_version": CONTRACT_VERSION,
        "generated_at": now_iso(),
        "overall": overall,
        "sources": receipt_sources,
        "review_inbox": review_inbox,
    }
    return receipt, state


def _load_observations(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload.get("observations", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("input must contain an observations list")
    decoded = []
    for row in rows:
        item = dict(row)
        if "body_base64" in item:
            item["body"] = base64.b64decode(item.pop("body_base64"))
        elif "body_text" in item:
            item["body"] = item.pop("body_text")
        else:
            raise ValueError(f"observation missing body: {item.get('source_id')}")
        decoded.append(item)
    return decoded


def _live_session():
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=2,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.headers.update({
        "User-Agent": "TaichungPoliceIntelSchemaDrift/1.0 (+public-source-monitor)",
        "Accept-Language": "zh-TW,zh;q=0.9",
    })
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


def _live_get(session, url: str, *, params: dict[str, Any] | None = None, timeout: int = 60):
    response = session.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    if not response.content:
        raise RuntimeError("HTTP succeeded with an empty body")
    return response


def _load_s028_module():
    path = ROOT / "canary-s028-165.py"
    spec = importlib.util.spec_from_file_location("schema_drift_s028_165", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("S-028/CTX-165 canary is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fetch_live_source(session, source_id: str):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from online_collect import API_S007, API_S009, NEWS_LIST_SOURCES

    if source_id in NEWS_LIST_SOURCES:
        return _live_get(session, NEWS_LIST_SOURCES[source_id]["list_url"]), None
    if source_id == "S-007":
        return _live_get(
            session,
            API_S007,
            params={"keywordList": "警察局", "pageNumber": 1, "pageSize": 200},
        ), None
    if source_id == "S-009":
        return _live_get(
            session,
            API_S009,
            params={"keywordList": "警察局", "pageNumber": 1, "pageSize": 200},
        ), None

    canary = _load_s028_module()
    if source_id == "S-028":
        dataset = canary.fetch_dataset_page(
            session,
            canary.CRIME_DATASET_ID,
            "10952-01-01-2 臺中市受(處)理刑事案件-分局別",
            "newdatacenter.taichung.gov.tw",
        )
        resource = canary.latest_dated_resource(dataset["resources"], r"\b(20\d{2})-(\d{2})_")
        return canary.get(session, resource["url"], {"newdatacenter.taichung.gov.tw"}, 120), resource["resource_id"]
    if source_id == "CTX-POP":
        dataset = canary.fetch_dataset_page(
            session,
            canary.POPULATION_DATASET_ID,
            "10122-00-01-2 臺中市各區戶數、人口數按戶別及性別分",
            "newdatacenter.taichung.gov.tw",
        )
        resource = canary.latest_dated_resource(dataset["resources"], r"\b(20\d{2})_")
        return canary.get(session, resource["url"], {"newdatacenter.taichung.gov.tw"}, 120), resource["resource_id"]
    if source_id == "CTX-165":
        dataset = canary.fetch_dataset_page(
            session,
            canary.FRAUD_DATASET_ID,
            "165反詐騙諮詢專線_遭停止解析涉詐網站",
            "opdadm.moi.gov.tw",
        )
        if len(dataset["resources"]) != 1:
            raise RuntimeError(f"165 dataset resource count is {len(dataset['resources'])}")
        resource = dataset["resources"][0]
        return canary.get(session, resource["url"], {"opdadm.moi.gov.tw"}, 120), resource["resource_id"]
    raise KeyError(f"no live schema source: {source_id}")


def _response_observation(source_id: str, response, resource_id: str | None = None) -> dict[str, Any]:
    request = getattr(response, "request", None)
    return {
        "source_id": source_id,
        "body": response.content,
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
        "resource_id": resource_id,
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_url": getattr(request, "url", None) or response.url,
        "final_url": response.url,
    }


def _failed_observation(source_id: str, error: Exception) -> dict[str, Any]:
    response = getattr(error, "response", None)
    reason = f"LIVE_FETCH_{type(error).__name__.upper()}"
    if response is not None:
        observation = _response_observation(source_id, response)
        observation["error_reason"] = reason
        return observation
    return {
        "source_id": source_id,
        "body": b"",
        "http_status": 503,
        "content_type": "",
        "resource_id": None,
        "observed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_url": None,
        "final_url": None,
        "error_reason": reason,
    }


def live_observations(*, session=None, fetch_source=None) -> list[dict[str, Any]]:
    session = session or _live_session()
    fetch_source = fetch_source or _fetch_live_source
    observations = []
    for source_id in CONTRACTS:
        try:
            response, resource_id = fetch_source(session, source_id)
            observations.append(_response_observation(source_id, response, resource_id))
        except Exception as error:
            observations.append(_failed_observation(source_id, error))
    return observations


def self_check() -> None:
    html_by_source = {
        "S-001": '<li><a href="home.jsp?mcustomize=news_view.jsp&dataserno=1">標題 115-09-10</a></li>',
        "S-019": '<li><a href="/12047/12142/12186/873338/post">標題 115-09-10</a></li>',
        "S-032": '<li><a href="index-1.asp?Parser=9,4,20,,,,21750">標題 115-09-10</a></li>',
    }
    for source_id, html in html_by_source.items():
        result = observe(CONTRACTS[source_id], html, content_type="text/html; charset=utf-8", final_url="https://official.test/list")
        assert result["status"] == "NO_DRIFT", source_id
    rss = '''<?xml version="1.0"?><rss version="2.0"><channel>
    <item iCuItem="3375296"><title>Official item</title><link>https://official.test/3375296/post</link><pubDate>Mon, 21 Sep 2026 02:57:54 GMT</pubDate></item>
    </channel></rss>'''.encode("utf-8")
    rss_result = observe(CONTRACTS["S-033"], rss, content_type="application/xml", final_url="https://official.test/rss")
    assert rss_result["status"] == "NO_DRIFT"
    fire_live = """<div class='update'>最後異動時間：2026-09-21 19:24:23</div>
    <ul class='list rwd-table'><li class='list_head'>標題</li><li>
    <span data-th='受理時間：'>2026/09/21 19:21:44</span>
    <span data-th='案類：'>緊急救護</span><span data-th='案別'>車禍</span>
    <span data-th='發生地點：'>北屯區軍榮二街</span><span data-th='派遣分隊：'>東山分隊</span>
    <span data-th='執行狀況：'></span></li></ul>"""
    fire_result = observe(CONTRACTS["S-031"], fire_live, content_type="text/html", final_url="https://official.test/caselist")
    assert fire_result["status"] == "NO_DRIFT" and fire_result["window_completeness"] == "PARTIAL"
    broken_html = observe(CONTRACTS["S-001"], b"<html><body>200 but changed</body></html>", content_type="text/html")
    assert broken_html["status"] == "BREAKING_DRIFT" and broken_html["window_completeness"] == "PARTIAL"

    api = {"success": True, "data": {"data": [{"proceedingsId": "p1", "date": "2026-09-10T00:00:00", "speaker": "甲", "content": "內容"}], "totalPages": 1, "totalCount": 1}}
    good_api = observe(CONTRACTS["S-007"], json.dumps(api), content_type="application/json")
    assert good_api["status"] == "NO_DRIFT"
    added = dict(api)
    added["data"] = {**api["data"], "data": [{**api["data"]["data"][0], "newField": "safe"}]}
    assert observe(CONTRACTS["S-007"], json.dumps(added), content_type="application/json")["status"] == "ADDITIVE_COMPATIBLE"
    no_page = json.loads(json.dumps(api))
    no_page["data"].pop("totalPages")
    missing_page = observe(CONTRACTS["S-007"], json.dumps(no_page), content_type="application/json")
    assert missing_page["status"] == "BREAKING_DRIFT" and missing_page["window_completeness"] == "PARTIAL"
    assert observe(CONTRACTS["S-007"], json.dumps({"error": "temporarily unavailable"}), content_type="application/json")["status"] == "BREAKING_DRIFT"

    rows = [{"項目": "x", "欄位名稱": "y", "數值": "1", "資料時間日期": "2026-09-01", "資料週期": "月"}]
    json_result = observe(CONTRACTS["S-028"], json.dumps(rows), content_type="application/json", resource_id="r1")
    assert json_result["status"] == "NO_DRIFT"
    assert observe(CONTRACTS["S-028"], json.dumps([{**rows[0], "數值": 1}]), content_type="application/json", resource_id="r1")["status"] == "BREAKING_DRIFT"
    changed_resource = observe(CONTRACTS["S-028"], json.dumps(rows), content_type="application/json", resource_id="r2", previous=json_result)
    assert changed_resource["resource_id_changed"] and changed_resource["status"] == "ADDITIVE_COMPATIBLE"
    csv_body = "民國年月,網域,網站性質,法律依據,聲請單位\n11509,a.test,其他,法規,機關\n"
    assert observe(CONTRACTS["CTX-165"], csv_body, content_type="text/csv", resource_id="r1")["status"] == "NO_DRIFT"
    bad_csv = observe(CONTRACTS["CTX-165"], "民國年月,網域\n11509,a.test\n", content_type="text/csv", resource_id="r1")
    assert bad_csv["status"] == "BREAKING_DRIFT"

    receipt, state = build_receipt([], state=empty_state(), contracts={"S-007": CONTRACTS["S-007"]})
    assert receipt["overall"] == "UNKNOWN"
    good = observe(CONTRACTS["S-007"], json.dumps(api), content_type="application/json")
    state = update_state(empty_state(), good)
    failed = observe(CONTRACTS["S-007"], json.dumps({"error": "x"}), content_type="application/json", previous=good)
    state = update_state(state, failed)
    assert state["sources"]["S-007"]["last_known_good"]["observed_schema_fingerprint"] == good["observed_schema_fingerprint"]
    assert state["sources"]["S-007"]["history"]
    print(f"SCHEMA_DRIFT_SELF_CHECK_OK sources={len(CONTRACTS)} lkg_preserved=true fail_closed=true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    if args.self_check:
        self_check()
        return 0
    if args.live and args.input:
        parser.error("--live and --input are mutually exclusive")
    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else empty_state()
    observations = live_observations() if args.live else _load_observations(args.input) if args.input else []
    receipt, next_state = build_receipt(observations, state=state)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.input:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.state.write_text(json.dumps(next_state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SCHEMA_DRIFT_RECEIPT_OK overall={receipt['overall']} sources={len(receipt['sources'])} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
