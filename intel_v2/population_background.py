"""Validate fixed-period SEGIS population CSV bytes before background use."""
from __future__ import annotations

import csv
import io
import re


FIELDS = ("COUNTY_ID", "COUNTY", "TOWN_ID", "TOWN", "H_CNT", "P_CNT", "M_CNT", "F_CNT", "INFO_TIME")
LABELS = dict(zip(FIELDS, ("縣市代碼", "縣市名稱", "鄉鎮市區代碼", "鄉鎮市區名稱", "戶數", "人口數", "男性人口數", "女性人口數", "資料時間")))
COUNT_FIELDS = ("H_CNT", "P_CNT", "M_CNT", "F_CNT")
MAX_BYTES = 2 * 1024 * 1024


def parse_population_csv(body: bytes, *, expected_period: str) -> list[dict]:
    """Fail closed on period drift, missing counts, duplicate IDs or bad schema.

    The official file has an English header and a Chinese description row.
    Only the exact description row may be skipped; an unparseable data row is
    never silently dropped or replaced with zero.
    """
    if not re.fullmatch(r"\d{3}Y(?:0[1-9]|1[0-2])M", expected_period):
        raise ValueError("expected_period must be an explicit ROC year/month")
    if len(body) > MAX_BYTES:
        raise ValueError("population CSV exceeds bounded byte limit")
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    if reader.fieldnames is None or len(reader.fieldnames) != len(FIELDS) or set(reader.fieldnames) != set(FIELDS):
        raise ValueError("population CSV field contract changed")
    rows = []
    seen = set()
    for position, row in enumerate(reader):
        if position == 0 and row == LABELS:
            continue
        if set(row) != set(FIELDS) or any(value is None or not value.strip() for value in row.values()):
            raise ValueError(f"population CSV row {reader.line_num} has missing fields")
        if row["INFO_TIME"] != expected_period:
            raise ValueError(f"population CSV period mismatch: expected {expected_period}")
        if not re.fullmatch(r"\d{5}", row["COUNTY_ID"]) or not re.fullmatch(r"\d{8}", row["TOWN_ID"]):
            raise ValueError("population administrative code shape changed")
        if not row["TOWN_ID"].startswith(row["COUNTY_ID"]):
            raise ValueError("population town/county code mismatch")
        identity = (row["INFO_TIME"], row["TOWN_ID"])
        if identity in seen:
            raise ValueError("duplicate population town/period identity")
        seen.add(identity)
        parsed = dict(row)
        for field in COUNT_FIELDS:
            if not re.fullmatch(r"\d+", row[field]):
                raise ValueError(f"population {field} must be a nonnegative integer")
            parsed[field] = int(row[field])
            if parsed[field] > 2**53 - 1:
                raise ValueError("population count cannot be represented exactly by the web client")
        if parsed["P_CNT"] != parsed["M_CNT"] + parsed["F_CNT"]:
            raise ValueError("population male/female counts do not sum to total")
        rows.append(parsed)
    if not rows:
        raise ValueError("population CSV has no data rows")
    return rows
