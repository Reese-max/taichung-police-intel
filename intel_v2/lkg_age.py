"""Elapsed age of a saved acquisition snapshot, not official-data freshness."""
from __future__ import annotations

import re
from datetime import datetime, timezone

_ISO_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](\d{2}):(\d{2}))$"
)


def last_known_good_age(last_known_good: object, observed_now: datetime) -> dict:
    """Use only the LKG completion clock and the supplied observation clock.

    Unknown or future completion times never become zero-age evidence. Other
    fetch, check, publication and official-record clocks are not substitutes.
    """
    result = {"status": "UNKNOWN", "age_hours": None, "observed_at": None,
              "completed_at": None, "reason": None}
    if not isinstance(observed_now, datetime):
        result["reason"] = "INVALID_OBSERVED_AT"
        return result
    try:
        if observed_now.tzinfo is None or observed_now.utcoffset() is None:
            result["reason"] = "INVALID_OBSERVED_AT"
            return result
        observed = observed_now.astimezone(timezone.utc)
    except (ValueError, OverflowError, TypeError):
        result["reason"] = "INVALID_OBSERVED_AT"
        return result
    result["observed_at"] = observed.isoformat()
    if not isinstance(last_known_good, dict):
        result["reason"] = "NO_LAST_KNOWN_GOOD"
        return result
    value = last_known_good.get("completed_at")
    if value is None or value == "":
        result["reason"] = "MISSING_COMPLETED_AT"
        return result
    matched = _ISO_TIME.fullmatch(value) if isinstance(value, str) else None
    if (not matched or (matched[1] is not None
                        and (int(matched[1]) > 23 or int(matched[2]) > 59))):
        result["reason"] = "INVALID_COMPLETED_AT"
        return result
    try:
        completed = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, OverflowError):
        result["reason"] = "INVALID_COMPLETED_AT"
        return result
    result["completed_at"] = value
    age_hours = (observed - completed).total_seconds() / 3600
    if age_hours < 0:
        result["reason"] = "FUTURE_COMPLETED_AT"
        return result
    result.update(status="KNOWN", age_hours=age_hours)
    return result
