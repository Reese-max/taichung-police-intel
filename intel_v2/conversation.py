"""Deterministic multi-turn conversational layer over the shared Query Gateway.

Issue #29: Web Chat and MCP share the same typed read-only query primitives.
This module resolves a bounded Chinese/English natural-language turn into one
versioned ``QueryRequest`` (``tool`` + ``arguments``) and renders the returned
``EvidenceEnvelope`` into a structured chat answer.  It is deliberately NOT a
free-text LLM: intent, entity, time and geography resolution are deterministic,
the context is the minimal server-validated state from the issue spec, and
FAILED/PARTIAL/STALE/CONFLICT states are carried into the answer, never hidden.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

CONTEXT_SCHEMA_VERSION = 1
QUERY_REQUEST_SCHEMA_VERSION = 1
CHAT_SCHEMA_VERSION = 1
PARSER_VERSION = "govintel-conversation/1"

MAX_TEXT_LENGTH = 512
MAX_ID_LENGTH = 256
MAX_EVENT_ID_LIST = 100
MAX_SECTION_ITEMS = 20
MAX_LINES = 40
DEFAULT_TIME_ZONE = "Asia/Taipei"
CITY_SCOPE_ENTITY = "location:taichung-city"

# Multi-turn context keeps only the minimum state named in the issue spec
# (event / geography / time / agency / category narrowing + last receipt).
CONTEXT_KEYS = frozenset({
    "selected_event_id",
    "selected_time_window",
    "selected_region",
    "selected_agency",
    "selected_category",
    "last_result_event_ids",
    "last_query_receipt",
})

QUICK_ACTIONS = [
    {"id": "today-important", "label": "今日重要事件", "text": "今日重要事件"},
    {"id": "recent-changes", "label": "最近異動", "text": "最近異動"},
    {"id": "weekend-events", "label": "這週末活動／交通", "text": "這週末有哪些活動或交通管制？"},
    {"id": "anti-fraud", "label": "反詐最新資訊", "text": "反詐最新資訊"},
    {"id": "by-district", "label": "查某地區", "text": "查地區"},
    {"id": "official-evidence", "label": "查官方證據", "text": "查官方證據"},
    {"id": "statistics", "label": "查警政統計", "text": "查警政統計"},
    {"id": "no-data", "label": "為什麼今天沒有資料？", "text": "為什麼今天沒有資料？"},
]

_TIER_LABELS = {
    "VERIFIED": "已驗證",
    "DISCOVERY_UNVERIFIED": "待確認（未驗證）",
    "CONFLICT": "來源衝突",
    "STALE": "資料可能過期",
}
_FRESHNESS_LABELS = {
    "RECENT": "資料為近期快照",
    "STALE": "公開快照已過期，不能宣稱目前最新",
    "PARTIAL": "監測或發布範圍不完整",
    "UNKNOWN": "資料時效或完整性無法核對",
    "SOURCE_NOT_AVAILABLE": "指定來源不在核准快照",
}

_EVENT_WORD_RE = re.compile(r"(事件|活動|管制|施工|公告|封路|改道|集會|遊行|查詢|查|找|搜|有哪些|有什麼|什麼|有沒有)")
_ORDINAL_RE = re.compile(r"第([0-9０-９]+|[一二三四五六七八九十]+)(件|個|筆|則|項|張)")
_EVENT_ID_RE = re.compile(r"\b[A-Z]{1,8}-[A-Z0-9][A-Z0-9_-]{1,}\b")
_ASK_DISTRICT_RE = re.compile(r"^(查|找|查詢)?[一這那某]?[個些]?(地區|行政區|區域|鄉鎮)[的呢啊?？]*$")
_RESET_RE = re.compile(r"(全部|不限|重新|所有地區|清除|忘掉|忘記)")
_NARROW_RE = re.compile(r"(只看|只要|限|限定|縮小|改查|改成)")
_COMPARE_RE = re.compile(
    r"(跟|與|和|比|相較|對比).*(昨天|之前|先前|前|上一?版|昨天)"
    r"|(改|變|異動|修訂|更新).{0,6}(什麼|哪些|了嗎|嗎|呢|多少|了沒)"
    r"|(為什麼|為何).{0,10}(變|改)"
    r"|比較"
    r"|跟昨天比"
    r"|有(沒有|什么|什麼)?改"
)
_RECENT_CHANGE_RE = re.compile(r"^(最近|最新|這幾天|近期)(的)?(異動|變更|更新|改變)|有什麼(異動|變更)")
_HEALTH_RE = re.compile(
    r"(為什麼|為何).{0,8}(沒有|無|找不到|查不到)"
    r"|沒有.{0,4}(資料|數據|更新|新訊息|新消息)"
    r"|(資料|數據).{0,4}(為什麼|沒有|消失|斷|失敗)"
    r"|為什麼沒有|為何沒有"
)
_BRIEF_RE = re.compile(r"(今日|今天|目前|最新|現在).{0,4}(重要|簡報|概況|重點|總覽)|今日重要|每日簡報|目前概況|最新簡報")
_EVIDENCE_RE = re.compile(r"(證據|原文|佐證|出處|官方文件|原始文件|原始資料|來源文件|官方資料|證明)")
_DETAIL_RE = re.compile(r"(詳情|細節|詳細|更多|內容|狀態|管制|交通|時間|地點).{0,8}(嗎|呢|如何|什麼|多少|在哪)|詳情|說明一下|告訴我")
_STATS_HINT_RE = re.compile(r"(統計|趨勢|數據|件數|數字|資料庫)")
_STATS_METRIC_WORDS = ("詐欺", "詐騙", "反詐", "酒駕", "交通事故", "車禍", "事故", "集會", "遊行", "犯罪", "刑案", "竊盜")
_STATS_PERIOD_WORDS = re.compile(r"(最近|趨勢|每月|個月|今年|去年|上期|同期|比較)")
_ANTI_FRAUD_INFO_RE = re.compile(r"(反詐|詐騙|詐欺).{0,6}(資訊|宣導|消息|新聞|最新)")
_TRACKED_RE = re.compile(r"(追蹤|已追蹤|我的追蹤)")
_REGION_RE = re.compile(r"([一-鿿]{1,4})(區|里|鄉|鎮)")
_GEO_HINT_RE = re.compile(r"(臺中|台中|台灣|臺灣|全國|全台|全臺)")
_AGENCY_HINT_RE = re.compile(r"(警察局|警局|警方|交通局|消防局|研考會|新聞局|議會|分局)")

_CATEGORY_ALIASES = {
    "traffic_control": ("交通管制", "交通", "管制", "施工", "封路", "改道"),
    "large_event": ("大型活動", "集會", "遊行", "演唱會", "路跑", "晚會"),
    "police_announcement": ("警政", "公告", "宣導"),
}
_METRIC_TOKENS = {
    "fraud": ("詐欺", "詐騙", "反詐"),
    "dui": ("酒駕",),
    "accident": ("交通事故", "車禍", "事故"),
    "assembly": ("集會", "遊行"),
    "crime": ("犯罪", "刑案", "竊盜"),
}
_CJK_NUMERALS = {"零": 0, "一": 1, "二": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).strip()
    return normalized.replace("台", "臺")


def quick_action(action_id: str) -> dict[str, str] | None:
    for action in QUICK_ACTIONS:
        if action["id"] == action_id:
            return action
    return None


def quick_actions() -> list[dict[str, str]]:
    return [dict(action) for action in QUICK_ACTIONS]


def _bounded_text(value: Any, name: str, max_length: int = MAX_ID_LENGTH) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"context {name} must be a bounded non-empty string")
    return value.strip()


def validate_context(raw: Any) -> dict[str, Any]:
    """Keep only the minimal multi-turn keys; unknown keys are dropped.

    The context is client-held state echoed back each turn, so it is fully
    validated: malformed shapes are rejected, unknown keys never reach the
    planner, and no key can inject tools or arguments.
    """
    if raw is None:
        return {"schema_version": CONTEXT_SCHEMA_VERSION}
    if not isinstance(raw, dict):
        raise ValueError("context must be an object")
    context: dict[str, Any] = {"schema_version": CONTEXT_SCHEMA_VERSION}
    for key in ("selected_event_id", "selected_region", "selected_agency", "selected_category"):
        if raw.get(key) is not None:
            context[key] = _bounded_text(raw[key], key)
    window = raw.get("selected_time_window")
    if window is not None:
        if not isinstance(window, dict):
            raise ValueError("context selected_time_window must be an object")
        bounded = {}
        for name in ("time_from", "time_to", "time_zone", "label"):
            if window.get(name) is not None:
                bounded[name] = _bounded_text(window[name], f"selected_time_window.{name}", 64)
        if "time_from" not in bounded or "time_to" not in bounded:
            raise ValueError("context selected_time_window requires time_from and time_to")
        context["selected_time_window"] = bounded
    ids = raw.get("last_result_event_ids")
    if ids is not None:
        if not isinstance(ids, list) or len(ids) > MAX_EVENT_ID_LIST:
            raise ValueError("context last_result_event_ids must be a bounded array")
        context["last_result_event_ids"] = list(dict.fromkeys(
            _bounded_text(item, "last_result_event_ids") for item in ids
        ))
    receipt = raw.get("last_query_receipt")
    if receipt is not None:
        if not isinstance(receipt, dict):
            raise ValueError("context last_query_receipt must be an object")
        keep = {}
        for name in ("tool_name", "arguments_sha256", "publication_hash", "query_generation_id", "query_id", "issued_at"):
            if receipt.get(name) is not None:
                keep[name] = _bounded_text(receipt[name], f"last_query_receipt.{name}", 256)
        context["last_query_receipt"] = keep
    return context


def _numeral(token: str) -> int | None:
    token = token.translate(_FULLWIDTH_DIGITS)
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 60 else None
    if token in _CJK_NUMERALS:
        return _CJK_NUMERALS[token]
    if token.startswith("十") and len(token) == 2 and token[1] in _CJK_NUMERALS:
        return 10 + _CJK_NUMERALS[token[1]]
    return None


def _local_today(now: datetime, time_zone: str) -> date:
    try:
        zone = ZoneInfo(time_zone)
    except Exception:
        zone = ZoneInfo(DEFAULT_TIME_ZONE)
    return now.astimezone(zone).date()


def _window(time_from: date, time_to: date, label: str) -> dict[str, str]:
    return {
        "time_from": time_from.isoformat(),
        "time_to": time_to.isoformat(),
        "time_zone": DEFAULT_TIME_ZONE,
        "label": label,
    }


def parse_time_window(text: str, today: date) -> dict[str, str] | None:
    """Resolve relative Chinese time expressions to explicit Asia/Taipei dates."""
    match = re.search(r"最近([0-9０-９]+|[一二三四五六七八九十兩]+)天", text)
    if match:
        count = _numeral(match.group(1))
        if count:
            return _window(today - timedelta(days=count - 1), today, f"最近{count}天")
    if re.search(r"下[一個]?(週|周|星期)(末|日)", text) or re.search(r"下[一個]?週末|下[一個]?周末", text):
        days_ahead = (7 - today.weekday()) % 7 or 7
        saturday = today + timedelta(days=days_ahead + 5)
        return _window(saturday, saturday + timedelta(days=1), "下週末")
    if re.search(r"[這本](個)?(週|周|星期)(末|日)|週末|周末", text):
        sunday = today + timedelta(days=(6 - today.weekday()) % 7)
        return _window(sunday - timedelta(days=1), sunday, "這週末")
    if re.search(r"下[一個]?(週|周|星期)", text):
        monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
        return _window(monday, monday + timedelta(days=6), "下週")
    if re.search(r"[這本](個)?(週|周|星期)|這星期|本星期", text):
        monday = today - timedelta(days=today.weekday())
        return _window(monday, monday + timedelta(days=6), "這週")
    if re.search(r"最近[一1](個)?(週|星期)", text):
        return _window(today - timedelta(days=6), today, "最近一週")
    for pattern, delta, label in (
        (r"今天|今日|今早", 0, "今天"),
        (r"昨天|昨日", -1, "昨天"),
        (r"前天", -2, "前天"),
        (r"明天|明日", 1, "明天"),
        (r"後天", 2, "後天"),
    ):
        if re.search(pattern, text):
            day = today + timedelta(days=delta)
            return _window(day, day, label)
    dates: list[date] = []
    for match in re.finditer(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text):
        try:
            dates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            return None
    for match in re.finditer(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text):
        try:
            dates.append(date(today.year, int(match.group(1)), int(match.group(2))))
        except ValueError:
            return None
    if dates:
        ordered = sorted(set(dates))
        return _window(ordered[0], ordered[-1], "指定日期")
    return None


def _month_shift(year: int, month: int, offset: int) -> str:
    total = year * 12 + (month - 1) + offset
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def parse_stat_period(text: str, today: date) -> dict[str, str] | None:
    current = f"{today.year:04d}-{today.month:02d}"
    match = re.search(r"最近([0-9０-９]+|[一二三四五六七八九十兩]+)(個)?月", text)
    if match:
        count = _numeral(match.group(1))
        if count:
            return {"period_from": _month_shift(today.year, today.month, -(count - 1)), "period_to": current}
    if re.search(r"上(個)?月|上月", text):
        return {"period_from": _month_shift(today.year, today.month, -1), "period_to": _month_shift(today.year, today.month, -1)}
    if re.search(r"今年|本年", text):
        return {"period_from": f"{today.year:04d}-01", "period_to": current}
    if re.search(r"去年|上年", text):
        return {"period_from": f"{today.year - 1:04d}-01", "period_to": f"{today.year - 1:04d}-12"}
    match = re.search(r"(\d{4})年(\d{1,2})月|(\d{4})-(\d{1,2})", text)
    if match:
        year = int(match.group(1) or match.group(3))
        month = int(match.group(2) or match.group(4))
        if 1 <= month <= 12:
            return {"period_from": f"{year:04d}-{month:02d}", "period_to": f"{year:04d}-{month:02d}"}
    return None


def _scan_entities(text: str, entities: list[dict[str, Any]] | None) -> list[tuple[int, int, dict[str, Any]]]:
    """Find canonical/alias mentions, longest name first, left to right."""
    hits: list[tuple[int, int, dict[str, Any]]] = []
    for entity in entities or []:
        names = [entity.get("canonical_label"), *(entity.get("aliases") or [])]
        best: tuple[int, int] | None = None
        for name in names:
            if not name:
                continue
            normalized = normalize_text(str(name))
            index = text.find(normalized)
            if index < 0:
                continue
            candidate = (index, len(normalized))
            if best is None or candidate[0] < best[0] or (candidate[0] == best[0] and candidate[1] > best[1]):
                best = candidate
        if best is not None:
            hits.append((best[0], best[1], entity))
    hits.sort(key=lambda item: (item[0], -item[1]))
    return hits


def _store_location_tokens(event_store: dict[str, Any] | None) -> set[str]:
    tokens: set[str] = set()
    for event in (event_store or {}).get("public_events", []):
        tokens.add(event.get("district_id"))
        tokens.update(event.get("location_ids") or [])
        tokens.update(event.get("location_candidates") or [])
    return {token for token in tokens if isinstance(token, str) and token}


def resolve_district(entity: dict[str, Any], event_store: dict[str, Any] | None) -> str:
    """Map a resolved location entity to the token the event store actually uses."""
    entity_id = entity["entity_id"]
    label = normalize_text(entity.get("canonical_label") or "")
    tokens = _store_location_tokens(event_store)
    if entity_id in tokens:
        return entity_id
    candidates = sorted(token for token in tokens if label and (label in token or normalize_text(token).startswith(label)))
    if len(candidates) == 1:
        return candidates[0]
    return entity_id


def _raw_district_token(text: str, event_store: dict[str, Any] | None) -> str | None:
    for match in _REGION_RE.finditer(text):
        label = f"{match.group(1)}{match.group(2)}"
        tokens = _store_location_tokens(event_store)
        candidates = sorted(token for token in tokens if label in normalize_text(token))
        if len(candidates) == 1:
            return candidates[0]
        return label
    return None


def resolve_category(text: str, event_store: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    """Map category keywords to canonical event_type values present in the store."""
    tokens = {token for aliases in _CATEGORY_ALIASES.items() for token in aliases[1] if token in text}
    if not tokens:
        return None, []
    store_types = sorted({event.get("event_type") for event in (event_store or {}).get("public_events", []) if event.get("event_type")})
    resolved: set[str] = set()
    for token in tokens:
        canonical = next((name for name, aliases in _CATEGORY_ALIASES.items() if token in aliases), None)
        if canonical in store_types:
            resolved.add(canonical)
            continue
        resolved.update(store_type for store_type in store_types if token in store_type)
    if len(resolved) == 1:
        return next(iter(resolved)), []
    if len(resolved) > 1:
        return None, sorted(resolved)
    return None, []


def resolve_metric(text: str, statistics_store: dict[str, Any] | None) -> tuple[str | None, list[str]]:
    tokens = {token for words in _METRIC_TOKENS.values() for token in words if token in text}
    rows = (statistics_store or {}).get("statistics", [])
    metrics = sorted({row["metric"] for row in rows if isinstance(row.get("metric"), str)})
    datasets = {row["dataset_id"]: row["metric"] for row in rows if isinstance(row.get("dataset_id"), str)}
    resolved: set[str] = set()
    for token in tokens:
        english = next((key for key, words in _METRIC_TOKENS.items() if token in words), token)
        resolved.update(
            metric for metric in metrics
            if english.casefold() in metric.casefold() or token.casefold() in metric.casefold()
        )
        resolved.update(
            metric for dataset_id, metric in datasets.items()
            if english.casefold() in dataset_id.casefold() or token.casefold() in dataset_id.casefold()
        )
    if len(resolved) == 1:
        return next(iter(resolved)), []
    if len(resolved) > 1:
        return None, sorted(resolved)
    return None, []


def resolve_geography(text: str, statistics_store: dict[str, Any] | None) -> str | None:
    if not _GEO_HINT_RE.search(text):
        return None
    geographies = sorted({row["geography"] for row in (statistics_store or {}).get("statistics", []) if isinstance(row.get("geography"), str)})
    normalized = normalize_text(text)
    for geography in geographies:
        if normalize_text(geography) in normalized:
            return geography
    if "臺中" in normalized:
        local = [geo for geo in geographies if "臺中" in normalize_text(geo)]
        if "臺中市" in local:
            return "臺中市"
        if len(local) == 1:
            return local[0]
        return "臺中市"
    for name in ("臺灣", "全國"):
        if name in normalized and name in geographies:
            return name
    return None


def _request(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": QUERY_REQUEST_SCHEMA_VERSION, "tool": tool, "arguments": arguments}


def _plan(intent: str, request: dict[str, Any] | None, **extra: Any) -> dict[str, Any]:
    plan = {"intent": intent, "request": request, "notices": [], "clarification": None}
    plan.update(extra)
    return plan


def plan_turn(
    *,
    text: str,
    context: dict[str, Any],
    now: datetime,
    location_entities: list[dict[str, Any]] | None = None,
    agency_entities: list[dict[str, Any]] | None = None,
    event_store: dict[str, Any] | None = None,
    statistics_store: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one user turn into a typed, read-only QueryRequest.

    Multi-turn narrowing reuses the stored context filters; an explicit reset
    marker clears them.  Anything the parser cannot ground deterministically
    becomes a clarification instead of a guessed query.
    """
    raw = normalize_text(text)
    if not raw:
        raise ValueError("text must be a bounded non-empty string")
    if len(raw) > MAX_TEXT_LENGTH:
        raise ValueError("text must be a bounded non-empty string")
    today = _local_today(now, DEFAULT_TIME_ZONE)
    ctx = dict(context)
    if _RESET_RE.search(raw):
        ctx = {}
    narrowed = bool(_NARROW_RE.search(raw))
    notices: list[dict[str, str]] = []
    if location_entities is not None and not location_entities:
        notices.append({"code": "ENTITY_INDEX_LIMITED", "message": "實體登錄不可用，地區以原始文字比對。"})

    city_scope = False
    district_arg: str | None = None
    for _index, _length, entity in _scan_entities(raw, location_entities):
        if entity["entity_id"] == CITY_SCOPE_ENTITY:
            city_scope = True
            continue
        district_arg = resolve_district(entity, event_store)
        break
    if district_arg is None:
        district_arg = _raw_district_token(raw, event_store)

    agency_arg: str | None = None
    agency_hits = _scan_entities(raw, agency_entities)
    if agency_hits:
        agency_arg = agency_hits[0][2]["entity_id"]
    elif _AGENCY_HINT_RE.search(raw):
        notices.append({"code": "AGENCY_UNRESOLVED", "message": "機關名稱未能比對到登錄實體，未套用機關條件。"})

    window = parse_time_window(raw, today)
    category, ambiguous_categories = resolve_category(raw, event_store)
    if ambiguous_categories:
        notices.append({"code": "CATEGORY_AMBIGUOUS", "message": f"類型關鍵字對應多種事件類型（{'、'.join(ambiguous_categories)}），未限定類型。"})
    event_ids_in_text = [match for match in _EVENT_ID_RE.findall(raw)]

    # Clarification-only quick paths.
    if _ASK_DISTRICT_RE.fullmatch(raw):
        districts = [
            entity["canonical_label"]
            for entity in (location_entities or [])
            if entity["entity_id"] != CITY_SCOPE_ENTITY
        ]
        return _plan("clarify", None, clarification={
            "question": "想查詢哪個地區？",
            "options": districts[:29],
        }, notices=notices)

    # "為什麼今天沒有資料" — source health must distinguish complete/failed/stale.
    if _HEALTH_RE.search(raw):
        return _plan("get_source_health", _request("get_source_health", {}), notices=notices)
    if _BRIEF_RE.search(raw):
        return _plan("get_current_brief", _request("get_current_brief", {}), notices=notices)

    # Version comparison requires a selected event identity — never guessed.
    compare_marker = _COMPARE_RE.search(raw) or re.search(r"(有沒有|有無).{0,4}(改|變|異動)", raw)
    if compare_marker and not _RECENT_CHANGE_RE.search(raw):
        event_id = event_ids_in_text[0] if event_ids_in_text else ctx.get("selected_event_id")
        if not event_id:
            return _plan("clarify", None, clarification={"question": "請先指出要比較版本的事件（先搜尋並選取一件）。", "options": []}, notices=notices)
        return _plan("compare_event_versions", _request("compare_event_versions", {"event_id": event_id}), notices=notices)

    ordinal_match = _ORDINAL_RE.search(raw)
    if ordinal_match:
        position = _numeral(ordinal_match.group(1))
        ids = ctx.get("last_result_event_ids") or []
        if position is None or position < 1 or position > len(ids):
            return _plan("clarify", None, clarification={"question": "請先查詢事件列表，再指出第幾件。", "options": []}, notices=notices)
        return _plan("get_event", _request("get_event", {"event_id": ids[position - 1]}), notices=notices)

    if _EVIDENCE_RE.search(raw):
        event_id = event_ids_in_text[0] if event_ids_in_text else ctx.get("selected_event_id")
        if event_id:
            return _plan("event_evidence", _request("get_event", {"event_id": event_id}), notices=notices)
        return _plan("search_evidence", _request("search_evidence", {}), notices=notices)

    if _ANTI_FRAUD_INFO_RE.search(raw):
        return _plan("search_evidence", _request("search_evidence", {"q": "詐"}), notices=notices)

    metric_hint = any(word in raw for word in _STATS_METRIC_WORDS)
    if _STATS_HINT_RE.search(raw) or (metric_hint and _STATS_PERIOD_WORDS.search(raw)):
        arguments: dict[str, Any] = {}
        metric, ambiguous_metrics = resolve_metric(raw, statistics_store)
        if metric:
            arguments["metric"] = metric
        if ambiguous_metrics:
            notices.append({"code": "METRIC_AMBIGUOUS", "message": f"統計關鍵字對應多種指標（{'、'.join(ambiguous_metrics)}），已回傳全部符合指標。"})
        geography = resolve_geography(raw, statistics_store)
        if geography:
            arguments["geography"] = geography
        period = parse_stat_period(raw, today)
        if period:
            arguments.update(period)
        return _plan("query_statistics", _request("query_statistics", arguments), notices=notices)

    explicit_event_id = event_ids_in_text[0] if event_ids_in_text else None
    if explicit_event_id and _DETAIL_RE.search(raw):
        return _plan("get_event", _request("get_event", {"event_id": explicit_event_id}), notices=notices)
    if _DETAIL_RE.search(raw) and ctx.get("selected_event_id") and not (
        district_arg or window or category or narrowed or _EVENT_WORD_RE.search(raw)
    ):
        return _plan("get_event", _request("get_event", {"event_id": ctx["selected_event_id"]}), notices=notices)

    wants_events = bool(
        district_arg or category or agency_arg or narrowed or explicit_event_id
        or city_scope or _EVENT_WORD_RE.search(raw)
        or _RECENT_CHANGE_RE.search(raw) or _TRACKED_RE.search(raw)
    )
    if wants_events:
        arguments = {}
        # Context filters only merge on an explicit narrowing marker; a fresh
        # query with new slots starts clean so filters never drift silently.
        effective_window = window or (ctx.get("selected_time_window") if narrowed else None)
        if effective_window:
            arguments["time_from"] = effective_window["time_from"]
            arguments["time_to"] = effective_window["time_to"]
            arguments["time_zone"] = effective_window.get("time_zone") or DEFAULT_TIME_ZONE
        if district_arg:
            arguments["district"] = district_arg
        elif narrowed and ctx.get("selected_region"):
            arguments["district"] = ctx["selected_region"]
        if agency_arg:
            arguments["agency"] = agency_arg
        elif narrowed and ctx.get("selected_agency"):
            arguments["agency"] = ctx["selected_agency"]
        if category:
            arguments["category"] = category
        elif narrowed and ctx.get("selected_category"):
            arguments["category"] = ctx["selected_category"]
        if _RECENT_CHANGE_RE.search(raw) or re.search(r"異動|變更", raw):
            arguments["changed_only"] = True
        if _TRACKED_RE.search(raw):
            arguments["tracked"] = True
        if explicit_event_id:
            arguments["public_event_id"] = explicit_event_id
        if not arguments and not narrowed:
            # No structured slot resolved: fall back to a bounded title keyword
            # query instead of pretending a full-coverage structured search.
            arguments["q"] = raw
            notices.append({"code": "TEXT_KEYWORD_SCOPE", "message": "以標題關鍵字比對，並非完整結構化條件。"})
        return _plan(
            "search_events", _request("search_events", arguments),
            notices=notices, slots={"window_label": (window or effective_window or {}).get("label")},
        )

    return _plan("clarify", None, clarification={
        "question": "這個問題無法對應到可查的公開資料條件。可以試試：",
        "options": [action["label"] for action in QUICK_ACTIONS],
    }, notices=notices)


def _notice(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    documents = event.get("documents") or []
    return {
        "public_event_id": event.get("public_event_id"),
        "canonical_title": event.get("canonical_title"),
        "event_type": event.get("event_type"),
        "district_id": event.get("district_id"),
        "start_at": event.get("start_at"),
        "end_at": event.get("end_at"),
        "event_status": event.get("event_status") or event.get("source_state"),
        "fusion_status": event.get("fusion_status"),
        "trust_tier": event.get("trust_tier"),
        "changed": bool(event.get("changed")),
        "tracked": bool(event.get("tracked") or event.get("tracking")),
        "document_count": len(documents),
        "official_url": documents[0].get("official_url") if documents else None,
    }


def _event_links(event: dict[str, Any]) -> list[dict[str, Any]]:
    links = []
    for document in event.get("documents") or []:
        url = document.get("official_url")
        links.append({
            "event_id": event.get("public_event_id"),
            "title": event.get("canonical_title"),
            "source_id": document.get("source_id"),
            "official_url": url if isinstance(url, str) and url.startswith("https://") else None,
            "evidence_id": document.get("evidence_id"),
            "document_version_id": document.get("document_version_id"),
            "evidence_locator": document.get("evidence_locator"),
        })
    return links


def _publication_links(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": result.get("public_event_id") or result.get("canonical_id"),
        "title": result.get("title") or result.get("canonical_title"),
        "source_id": result.get("source_id"),
        "official_url": result.get("official_url") if str(result.get("official_url") or "").startswith("https://") else None,
        "evidence_id": result.get("evidence_id") or result.get("canonical_ref", {}).get("evidence_id"),
        "document_version_id": result.get("document_version_id") or result.get("canonical_ref", {}).get("document_version_id"),
        "evidence_locator": result.get("evidence_locator") or result.get("canonical_ref", {}).get("evidence_locator"),
    }


def _bounded(items: list[Any]) -> list[Any]:
    return items[:MAX_SECTION_ITEMS]


def _receipt_of(envelope: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(envelope, dict):
        return None
    receipt = envelope.get("receipt")
    return receipt if isinstance(receipt, dict) else None


def _update_context(
    context: dict[str, Any],
    plan: dict[str, Any],
    envelope: dict[str, Any] | None,
) -> dict[str, Any]:
    out = dict(context)
    out["schema_version"] = CONTEXT_SCHEMA_VERSION
    request = plan.get("request") or {}
    tool = request.get("tool")
    arguments = request.get("arguments") or {}
    if envelope is None:
        if plan.get("intent") != "clarify" and plan.get("request"):
            # A failed selected-event lookup clears the stale selection instead
            # of letting later turns silently reuse it.
            if plan["request"]["tool"] in {"get_event", "compare_event_versions"}:
                out.pop("selected_event_id", None)
        return {key: value for key, value in out.items() if value is not None}
    if tool == "search_events":
        if arguments.get("time_from") or arguments.get("time_to"):
            out["selected_time_window"] = {
                "time_from": arguments.get("time_from"),
                "time_to": arguments.get("time_to"),
                "time_zone": arguments.get("time_zone") or DEFAULT_TIME_ZONE,
                "label": (plan.get("slots") or {}).get("window_label") or arguments.get("time_from"),
            }
        for arg_name, ctx_name in (("district", "selected_region"), ("agency", "selected_agency"), ("category", "selected_category")):
            if arguments.get(arg_name):
                out[ctx_name] = arguments[arg_name]
        ids = envelope.get("event_ids") or []
        out["last_result_event_ids"] = list(ids)[:MAX_EVENT_ID_LIST]
        if len(ids) == 1:
            out["selected_event_id"] = ids[0]
        else:
            out.pop("selected_event_id", None)
    if tool in {"get_event", "compare_event_versions"} and arguments.get("event_id"):
        out["selected_event_id"] = arguments["event_id"]
    receipt = _receipt_of(envelope)
    if receipt is not None:
        out["last_query_receipt"] = {
            "tool_name": receipt.get("tool_name"),
            "arguments_sha256": receipt.get("arguments_sha256"),
            "publication_hash": receipt.get("publication_hash"),
            "query_generation_id": receipt.get("query_generation_id"),
            "query_id": receipt.get("query_id") or envelope.get("query_id"),
            "issued_at": receipt.get("issued_at"),
        }
    return {key: value for key, value in out.items() if value is not None}


def _no_match(envelope: dict[str, Any]) -> dict[str, str]:
    coverage = envelope.get("query_coverage") or {}
    if envelope.get("answerable_no_match") or coverage.get("can_state_bounded_no_match"):
        return {
            "status": "BOUNDED_NO_MATCH",
            "statement": "查詢涵蓋的官方來源本次完整，範圍內未發現符合條件的項目；此結論只適用於該涵蓋範圍。",
        }
    return {
        "status": "UNBOUNDED_NO_MATCH",
        "statement": "來源涵蓋不完整或時效無法核對，無法確認是否存在符合條件的項目；請查看來源缺口。",
    }


def render_answer(
    *,
    plan: dict[str, Any],
    envelope: dict[str, Any] | None,
    error: dict[str, str] | None,
    context: dict[str, Any],
    fallback_publication_hash: str | None = None,
) -> dict[str, Any]:
    """Render the shared EvidenceEnvelope into a deterministic chat answer.

    The renderer never paraphrases canonical fields into stronger claims:
    trust tiers stay in separate sections, gaps stay visible, and a zero
    result only becomes "no events" when the coverage scope allows it.
    """
    lines: list[str] = []
    notices = list(plan.get("notices") or [])
    request = plan.get("request")
    chat: dict[str, Any] = {
        "schema_version": CHAT_SCHEMA_VERSION,
        "parser_version": PARSER_VERSION,
        "intent": plan["intent"],
        "status": "OK",
        "resolved_request": request,
        "lines": lines,
        "verified": [],
        "unverified": [],
        "conflicts": [],
        "stale": [],
        "statistics": [],
        "sources": [],
        "evidence_links": [],
        "event_ids": [],
        "gaps": [],
        "freshness": None,
        "trust": None,
        "no_match": None,
        "notices": notices,
        "quick_action_hints": [],
        "publication_hash": (envelope or {}).get("publication_hash") or fallback_publication_hash,
        "query_receipt": _receipt_of(envelope),
        "context": {"schema_version": CONTEXT_SCHEMA_VERSION},
    }
    if plan["intent"] == "clarify":
        clarification = plan.get("clarification") or {}
        chat["status"] = "CLARIFICATION_NEEDED"
        chat["resolved_request"] = None
        if clarification.get("question"):
            lines.append(str(clarification["question"]))
        for option in (clarification.get("options") or [])[:30]:
            lines.append(f"• {option}")
        chat["quick_action_hints"] = [action["id"] for action in QUICK_ACTIONS]
        chat["context"] = _update_context(context, plan, None)
        return chat
    if error is not None:
        code = error.get("code") or "FAILED"
        chat["status"] = "NOT_FOUND" if code in {"EVENT_NOT_FOUND", "EVENT_VERSION_NOT_FOUND"} else code
        notices.append(_notice(code, error.get("message") or "query failed"))
        if code == "CAPABILITY_NOT_AVAILABLE":
            lines.append("這項查詢能力目前不可用；不會以推測或其他資料來源代答。")
        elif chat["status"] == "NOT_FOUND":
            lines.append("找不到指定的事件或版本；先前的選取可能已不適用。")
        else:
            lines.append(f"查詢未完成（{code}）：{error.get('message')}")
        chat["context"] = _update_context(context, plan, None)
        return chat
    if envelope is None:
        chat["status"] = "CLARIFICATION_NEEDED"
        chat["resolved_request"] = None
        chat["context"] = _update_context(context, plan, None)
        return chat

    chat["freshness"] = envelope.get("freshness")
    chat["gaps"] = list(envelope.get("source_gaps") or [])
    chat["event_ids"] = list(envelope.get("event_ids") or [])
    trust = {
        key: envelope[key]
        for key in ("trust_tier_counts", "discovery_unverified_count", "conflict_count", "stale_count")
        if key in envelope
    }
    chat["trust"] = trust or None
    freshness_label = _FRESHNESS_LABELS.get(str(chat["freshness"] or "").upper())
    if freshness_label:
        lines.append(f"{freshness_label}。")
    for gap in chat["gaps"][:10]:
        source = gap.get("source_id") or "整體"
        lines.append(f"涵蓋限制：{source} · {gap.get('reason')}")
    tool = (request or {}).get("tool")
    result_type = envelope.get("result_type")

    if tool == "search_events":
        events = envelope.get("events") or []
        for event in events:
            summary = _event_summary(event)
            tier = event.get("trust_tier")
            if tier == "VERIFIED":
                chat["verified"].append(summary)
            elif tier == "CONFLICT":
                chat["conflicts"].append(summary)
            elif tier == "STALE":
                chat["stale"].append(summary)
            else:
                chat["unverified"].append(summary)
        for section in ("verified", "unverified", "conflicts", "stale"):
            chat[section] = _bounded(chat[section])
        counts = envelope.get("trust_tier_counts") or {}
        lines.append(
            "符合條件：已驗證 {v} 件、待確認 {u} 件、來源衝突 {c} 件、可能過期 {s} 件（共 {t} 筆）。".format(
                v=counts.get("VERIFIED", 0), u=counts.get("DISCOVERY_UNVERIFIED", 0),
                c=counts.get("CONFLICT", 0), s=counts.get("STALE", 0), t=envelope.get("total_matches", len(events)),
            )
        )
        if envelope.get("truncated") or envelope.get("has_more"):
            lines.append(f"結果已截斷：本頁 {envelope.get('result_count', len(events))} 筆，仍有更多。")
        if not events:
            chat["no_match"] = _no_match(envelope)
            chat["status"] = chat["no_match"]["status"]
            lines.append(chat["no_match"]["statement"])
        for event in events[:MAX_SECTION_ITEMS]:
            links = _event_links(event)
            chat["evidence_links"].extend(links)
        chat["evidence_links"] = _bounded(chat["evidence_links"])
    elif tool == "get_event":
        event = envelope.get("event") or {}
        summary = _event_summary(event)
        tier = event.get("trust_tier")
        bucket = {"VERIFIED": "verified", "CONFLICT": "conflicts", "STALE": "stale"}.get(tier, "unverified")
        chat[bucket].append(summary)
        label = _TIER_LABELS.get(tier, tier or "未知")
        lines.append(f"事件：{event.get('canonical_title') or event.get('public_event_id')}（{label}）")
        facts = [
            f"類型：{event.get('event_type') or '未提供'}",
            f"地區：{event.get('district_id') or '未提供'}",
            f"狀態：{summary['event_status'] or '未提供'}",
        ]
        if event.get("start_at") or event.get("end_at"):
            facts.append(f"時間：{event.get('start_at') or '未提供'} 至 {event.get('end_at') or '未提供'}")
        facts.append(f"版本文件：{len(event.get('documents') or [])} 份")
        if event.get("changed") or event.get("changed_fields"):
            facts.append("有已記錄的異動")
        lines.append("；".join(facts) + "。")
        chat["evidence_links"] = _bounded(_event_links(event))
        if plan["intent"] == "event_evidence":
            lines.append("官方證據定位如下；每筆都保留原始文件版本與定位。")
    elif tool == "compare_event_versions":
        comparison = envelope.get("comparison") or {}
        chat["comparison"] = comparison
        if comparison.get("comparison_status") != "COMPARED":
            lines.append("此事件沒有可比較的兩個版本紀錄。")
        else:
            before = comparison.get("before") or {}
            after = comparison.get("after") or {}
            lines.append(f"版本比較：{before.get('document_version_id') or before.get('version_id')} → {after.get('document_version_id') or after.get('version_id')}（{(comparison.get('materiality') or 'UNKNOWN')}）")
            for field in comparison.get("changed_fields") or []:
                old = (before.get("fields") or {}).get(field)
                new = (after.get("fields") or {}).get(field)
                lines.append(f"欄位 {field}：{old if old is not None else '未提供'} → {new if new is not None else '未提供'}")
            observed = comparison.get("observed_at") or {}
            lines.append(f"觀測時間：{observed.get('before') or '未提供'} → {observed.get('after') or '未提供'}")
            if comparison.get("affected_handoff_claims"):
                lines.append(f"受影響的交接項目：{len(comparison['affected_handoff_claims'])} 筆")
        tier = comparison.get("trust_tier")
        if tier and tier != "VERIFIED":
            notices.append(_notice("TRUST_TIER", f"此事件狀態為 {_TIER_LABELS.get(tier, tier)}。"))
        event = envelope.get("event") or {"public_event_id": comparison.get("public_event_id"), "documents": []}
        chat["evidence_links"] = _bounded(_event_links(event))
    elif tool == "query_statistics":
        rows = envelope.get("statistics") or []
        chat["statistics"] = _bounded(rows)
        periods = sorted({row["period"] for row in rows})
        units = sorted({row["unit"] for row in rows})
        geographies = sorted({row["geography"] for row in rows})
        lines.append(
            "統計 {n} 筆（期間：{p}；單位：{u}；地理範圍：{g}）。".format(
                n=len(rows),
                p="～".join([periods[0], periods[-1]]) if len(periods) > 1 else (periods[0] if periods else "未提供"),
                u="、".join(units) or "未提供",
                g="、".join(geographies) or "未提供",
            )
        )
        for row in rows[:MAX_SECTION_ITEMS]:
            flag = "暫計" if row.get("provisional") else "定案"
            lines.append(f"• {row['geography']} {row['metric']} {row['period']}：{row['value']}{row['unit']}（{flag}，{row['dataset_id']}，更新 {row['updated_at']}）")
            url = row.get("official_url")
            if isinstance(url, str) and url.startswith("https://"):
                chat["evidence_links"].append({
                    "event_id": None,
                    "title": f"{row['geography']} {row['metric']} {row['period']}",
                    "source_id": row.get("source_id"),
                    "official_url": url,
                    "evidence_id": row.get("statistic_id"),
                    "document_version_id": row.get("dataset_id"),
                    "evidence_locator": row.get("evidence_locator"),
                })
        if not rows:
            chat["no_match"] = _no_match(envelope)
            chat["status"] = chat["no_match"]["status"]
            lines.append(chat["no_match"]["statement"])
        chat["evidence_links"] = _bounded(chat["evidence_links"])
    elif tool == "search_evidence":
        results = envelope.get("results") or []
        lines.append(f"官方文件索引符合 {envelope.get('total_matches', len(results))} 筆；僅涵蓋已發布中介資料。")
        for result in results[:MAX_SECTION_ITEMS]:
            chat["evidence_links"].append(_publication_links(result))
        if not results:
            chat["no_match"] = _no_match(envelope)
            chat["status"] = chat["no_match"]["status"]
            lines.append(chat["no_match"]["statement"])
        chat["evidence_links"] = _bounded(chat["evidence_links"])
    elif tool == "get_source_health":
        sources = envelope.get("sources") or []
        chat["sources"] = _bounded(sources)
        abnormal = [
            source for source in sources
            if source.get("source_health") != "PASS"
            or source.get("window_completeness") not in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
            or str(source.get("freshness_status") or "").upper() not in {"FRESH", "RECENT"}
        ]
        lines.append(f"來源狀態共 {len(sources)} 個，其中 {len(abnormal)} 個需要留意。")
        for source in sources[:MAX_SECTION_ITEMS]:
            lines.append(
                "• {sid}：健康 {health}／涵蓋 {comp}／時效 {fresh}（最後核對 {checked}）".format(
                    sid=source.get("source_id"),
                    health=source.get("source_health") or "未知",
                    comp=source.get("window_completeness") or "未知",
                    fresh=source.get("freshness_status") or "未知",
                    checked=source.get("last_checked_at") or "未提供",
                )
            )
    elif tool == "get_current_brief":
        brief = envelope.get("brief") or {}
        overview = brief.get("overview") or brief.get("status_message") or "目前簡報沒有總覽文字。"
        lines.append(str(overview))
        counts = {
            "重點項目": len(brief.get("priority_items") or []),
            "追蹤項目": len(brief.get("tracking_items") or []),
            "其他異動": len(brief.get("other_changes") or []),
        }
        lines.append("簡報內容：" + "、".join(f"{name} {count} 筆" for name, count in counts.items()) + "。")
        if brief.get("publication_status") and brief["publication_status"] != "READY":
            notices.append(_notice("PUBLICATION_STATUS", f"發布狀態為 {brief['publication_status']}。"))
    else:
        lines.append("已執行對應的唯讀查詢。")
    if result_type:
        chat["result_type"] = result_type
    chat["lines"] = lines[:MAX_LINES]
    chat["context"] = _update_context(context, plan, envelope)
    return chat
