"""來源價值評分、內容分流與 AI 驗證狀態機。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


WEIGHTS = {
    "mission_fit": 30,
    "actionable_change": 25,
    "local_police_relevance": 20,
    "evidence_strength": 15,
    "novelty_corroboration": 10,
}
SOURCE_WEIGHTS = {
    "useful_yield_rate": 35,
    "traceable_rate": 25,
    "unique_information_rate": 20,
    "successful_window_rate": 20,
}
VERIFICATION_STATUSES = {"AUTO_PASS", "AUTO_RETRY", "AI_DISAGREEMENT", "QUARANTINED"}
PRODUCT_ROLES = {
    "PREP_CORE",
    "TREND_SIGNAL",
    "POLICY_UPSTREAM",
    "ANALYTIC_EVIDENCE",
    "DISCOVERY_ONLY",
    "CONTEXT_ONLY",
}
NEW_INFORMATION_FIELDS = {"new_facts", "new_status", "new_milestone", "new_evidence"}

TRANSITIONS = {
    (None, "VALIDATION_PASSED"): "AUTO_PASS",
    (None, "VALIDATION_DISAGREED"): "AI_DISAGREEMENT",
    (None, "VALIDATION_UNAVAILABLE"): "AUTO_RETRY",
    ("AUTO_PASS", "VALIDATION_PASSED"): "AUTO_PASS",
    ("AUTO_PASS", "VALIDATION_DISAGREED"): "AI_DISAGREEMENT",
    ("AUTO_PASS", "SOURCE_BROKEN"): "QUARANTINED",
    ("AI_DISAGREEMENT", "RETRY_SCHEDULED"): "AUTO_RETRY",
    ("AUTO_RETRY", "VALIDATION_PASSED"): "AUTO_PASS",
    ("AUTO_RETRY", "VALIDATION_DISAGREED"): "QUARANTINED",
    ("AUTO_RETRY", "RETRY_EXHAUSTED"): "QUARANTINED",
    ("QUARANTINED", "RETRY_SCHEDULED"): "AUTO_RETRY",
}


def transition(current: str | None, event: str) -> str:
    """套用唯一允許的驗證狀態轉移。"""
    if current is not None and current not in VERIFICATION_STATUSES:
        raise ValueError(f"未知 verification_status：{current}")
    try:
        return TRANSITIONS[(current, event)]
    except KeyError as exc:
        raise ValueError(f"不允許的狀態轉移：{current} + {event}") from exc


def item_score(components: Mapping[str, int]) -> int:
    """驗證五個構面並回傳 0～100 總分。"""
    if set(components) != set(WEIGHTS):
        raise ValueError(f"score_components 必須剛好包含：{', '.join(WEIGHTS)}")
    for name, maximum in WEIGHTS.items():
        value = components[name]
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
            raise ValueError(f"{name} 必須是 0～{maximum} 的整數")
    return sum(components.values())


def route_item(
    *,
    verification_status: str,
    traceability_gate: str,
    score_components: Mapping[str, int] | None = None,
    product_role: str = "PREP_CORE",
    has_t1_t2_evidence: bool = True,
    has_new_change: bool = True,
    exact_duplicate: bool = False,
    semantic_duplicate: bool = False,
    new_information_fields: Sequence[str] = (),
) -> dict:
    """依硬 Gate、分數上限與去重結果決定正式產品分流。"""
    if verification_status not in VERIFICATION_STATUSES:
        raise ValueError(f"未知 verification_status：{verification_status}")
    if traceability_gate not in {"PASS", "FAIL"}:
        raise ValueError("traceability_gate 只能是 PASS 或 FAIL")
    if product_role not in PRODUCT_ROLES:
        raise ValueError(f"未知 product_role：{product_role}")
    unknown_fields = set(new_information_fields) - NEW_INFORMATION_FIELDS
    if unknown_fields:
        raise ValueError(f"未知 new_information_fields：{sorted(unknown_fields)}")

    if traceability_gate == "FAIL":
        return {
            "verification_status": "QUARANTINED",
            "content_disposition": "QUARANTINED",
            "item_value_score": None,
            "score_reason_codes": [],
            "validation_reason_codes": ["UNTRACEABLE"],
        }
    if verification_status != "AUTO_PASS":
        return {
            "verification_status": verification_status,
            "content_disposition": "QUARANTINED" if verification_status == "QUARANTINED" else "VALIDATION_PENDING",
            "item_value_score": None,
            "score_reason_codes": [],
            "validation_reason_codes": [verification_status],
        }
    if exact_duplicate:
        return {
            "verification_status": "AUTO_PASS",
            "content_disposition": "EXACT_DUPLICATE_SUPPRESSED",
            "item_value_score": None,
            "score_reason_codes": ["EXACT_DUPLICATE"],
            "validation_reason_codes": [],
        }
    if score_components is None:
        raise ValueError("AUTO_PASS 且非精確重複時必須提供 score_components")

    score = item_score(score_components)
    reason_codes: list[str] = []
    if product_role == "CONTEXT_ONLY" and score > 49:
        score = 49
        reason_codes.append("CONTEXT_ONLY_CAP")
    if product_role == "DISCOVERY_ONLY" and not has_t1_t2_evidence and score > 39:
        score = 39
        reason_codes.append("DISCOVERY_ONLY_CAP")
    if product_role == "ANALYTIC_EVIDENCE" and not has_new_change and score > 59:
        score = 59
        reason_codes.append("ROUTINE_ANALYTIC_CAP")

    if semantic_duplicate and not new_information_fields:
        disposition = "SEMANTIC_DUPLICATE_SUPPRESSED"
        reason_codes.append("NO_NEW_INFORMATION")
    elif score < 40:
        disposition = "LOW_VALUE_SUPPRESSED"
    elif score < 70 or not has_new_change:
        disposition = "SEARCH_ONLY"
    else:
        disposition = "HOME_CANDIDATE"
    if semantic_duplicate and new_information_fields:
        reason_codes.append("SEMANTIC_MATCH_WITH_NEW_INFORMATION")

    return {
        "verification_status": "AUTO_PASS",
        "content_disposition": disposition,
        "item_value_score": score,
        "score_reason_codes": reason_codes,
        "validation_reason_codes": [],
    }


def source_score(
    rates: Mapping[str, float] | None,
    *,
    product_role: str,
    consecutive_low_windows: int = 0,
) -> dict:
    """計算 90 日來源分數；資料不足不偽裝成 0 分。"""
    if product_role not in PRODUCT_ROLES:
        raise ValueError(f"未知 product_role：{product_role}")
    if isinstance(consecutive_low_windows, bool) or not isinstance(consecutive_low_windows, int) or consecutive_low_windows < 0:
        raise ValueError("consecutive_low_windows 必須是非負整數")
    if rates is None:
        return {"source_value_score_90d": None, "source_value_status": "NOT_ENOUGH_DATA"}
    if set(rates) != set(SOURCE_WEIGHTS):
        raise ValueError(f"rates 必須剛好包含：{', '.join(SOURCE_WEIGHTS)}")
    for name, value in rates.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"{name} 必須介於 0 與 1")

    score = round(sum(SOURCE_WEIGHTS[name] * rates[name] for name in SOURCE_WEIGHTS), 2)
    if score >= 70:
        status = "ACTIVE"
    elif score < 40 and consecutive_low_windows >= 2 and product_role != "PREP_CORE":
        status = "DEFERRED_BY_VALUE"
    else:
        status = "LOW_FREQUENCY"
    return {"source_value_score_90d": score, "source_value_status": status}
