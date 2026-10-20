// Ask GovIntel — client helpers for the shared conversational Query Gateway.
//
// The browser only sends `{text, context}` to `chat_turn`; intent resolution
// and answer rendering live server-side so Web Chat and MCP stay identical.
// These helpers mirror the server's minimal-context contract for display and
// session storage — transcripts are never persisted here.

import { queryGateway } from "./query-release-client.js";

export const CHAT_CONTEXT_SCHEMA_VERSION = 1;
export const CHAT_CONTEXT_STORAGE_KEY = "govintel:chat-context:v1";

export const CHAT_CONTEXT_KEYS = new Set([
  "selected_event_id",
  "selected_time_window",
  "selected_region",
  "selected_agency",
  "selected_category",
  "last_result_event_ids",
  "last_query_receipt",
]);

export const CHAT_QUICK_ACTIONS = [
  { id: "today-important", label: "今日重要事件", text: "今日重要事件" },
  { id: "recent-changes", label: "最近異動", text: "最近異動" },
  { id: "weekend-events", label: "這週末活動／交通", text: "這週末有哪些活動或交通管制？" },
  { id: "anti-fraud", label: "反詐最新資訊", text: "反詐最新資訊" },
  { id: "by-district", label: "查某地區", text: "查地區" },
  { id: "official-evidence", label: "查官方證據", text: "查官方證據" },
  { id: "statistics", label: "查警政統計", text: "查警政統計" },
  { id: "no-data", label: "為什麼今天沒有資料？", text: "為什麼今天沒有資料？" },
];

export const CHAT_STATUS_LABELS = {
  OK: "完成",
  BOUNDED_NO_MATCH: "涵蓋範圍內無符合項目",
  UNBOUNDED_NO_MATCH: "無法確認有無符合項目",
  CLARIFICATION_NEEDED: "需要更多條件",
  CAPABILITY_NOT_AVAILABLE: "對話查詢能力未提供",
  NOT_FOUND: "找不到指定事件或版本",
};

const MAX_ID_LENGTH = 256;
const MAX_EVENT_ID_LIST = 100;
const MAX_RECEIPT_KEYS = 12;

function boundedText(value, max = MAX_ID_LENGTH) {
  return typeof value === "string" && value.trim() && value.length <= max ? value.trim() : null;
}

export function sanitizeChatContext(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const context = { schema_version: CHAT_CONTEXT_SCHEMA_VERSION };
  for (const key of ["selected_event_id", "selected_region", "selected_agency", "selected_category"]) {
    if (raw[key] !== undefined && raw[key] !== null) {
      const value = boundedText(raw[key]);
      if (value === null) return null;
      context[key] = value;
    }
  }
  if (raw.selected_time_window != null) {
    const window = raw.selected_time_window;
    if (typeof window !== "object" || Array.isArray(window)) return null;
    const timeFrom = boundedText(window.time_from, 64);
    const timeTo = boundedText(window.time_to, 64);
    if (!timeFrom || !timeTo) return null;
    context.selected_time_window = { time_from: timeFrom, time_to: timeTo };
    const zone = boundedText(window.time_zone, 64);
    if (zone) context.selected_time_window.time_zone = zone;
    const label = boundedText(window.label, 64);
    if (label) context.selected_time_window.label = label;
  }
  if (raw.last_result_event_ids != null) {
    if (!Array.isArray(raw.last_result_event_ids) || raw.last_result_event_ids.length > MAX_EVENT_ID_LIST) return null;
    const ids = raw.last_result_event_ids.map((item) => boundedText(item));
    if (ids.some((item) => item === null)) return null;
    context.last_result_event_ids = [...new Set(ids)];
  }
  if (raw.last_query_receipt != null) {
    const receipt = raw.last_query_receipt;
    if (typeof receipt !== "object" || Array.isArray(receipt) || Object.keys(receipt).length > MAX_RECEIPT_KEYS) return null;
    const keep = {};
    for (const key of ["tool_name", "arguments_sha256", "publication_hash", "query_generation_id", "query_id", "issued_at"]) {
      if (receipt[key] == null) continue;
      const text = boundedText(receipt[key]);
      if (text === null) return null;
      keep[key] = text;
    }
    context.last_query_receipt = keep;
  }
  return context;
}

function chatDisplaySafe(chat, payload) {
  const texts = (row, keys) => row && typeof row === "object" && !Array.isArray(row) &&
    keys.every(key => row[key] == null || typeof row[key] === "string");
  const rows = (items, valid) => items == null || (Array.isArray(items) && items.length <= 100 && items.every(valid));
  const gap = row => texts(row, ["source_id", "status", "source_health", "reason"]);
  return texts(payload, ["query_id", "publication_hash", "query_generation_id", "freshness", "verification_summary"]) &&
    typeof chat.status === "string" && Array.isArray(chat.lines) && rows(chat.lines, line => typeof line === "string") &&
    ["verified", "unverified", "conflicts", "stale"].every(kind => rows(chat[kind], row =>
      (kind === "stale" && typeof row === "string") || (texts(row, ["public_event_id", "canonical_title", "event_status"]) && typeof row.public_event_id === "string"))) &&
    rows(chat.statistics, row => texts(row, ["statistic_id", "geography", "metric", "period", "unit", "dataset_id", "source_id", "updated_at"]) &&
      (typeof row.value === "string" || (typeof row.value === "number" && Number.isFinite(row.value))) && typeof row.provisional === "boolean") &&
    rows(chat.sources, row => texts(row, ["source_id", "source_health", "freshness_status", "window_completeness"])) &&
    rows(chat.evidence_links, row => texts(row, ["official_url", "title", "source_id", "evidence_id", "document_version_id", "evidence_locator"])) &&
    rows(chat.notices, row => typeof row === "string" || texts(row, ["code", "message"])) &&
    rows(chat.gaps, gap) && rows(payload.source_gaps, gap);
}

export async function requestChat(endpoint, { text, quickAction, context = { schema_version: 1 } }, releaseContext = null) {
  const sanitized = sanitizeChatContext(context);
  if (!sanitized || (text !== undefined && (typeof text !== "string" || !text.trim() || text.length > 512)) ||
      (quickAction !== undefined && !CHAT_QUICK_ACTIONS.some(action => action.id === quickAction)) ||
      (text === undefined && quickAction === undefined)) {
    throw Object.assign(new Error("對話問題或條件無法驗證，請重新輸入。"), { code: "INVALID_ARGUMENTS" });
  }
  const payload = await queryGateway(endpoint, "chat_turn", {
    ...(text === undefined ? {} : { text: text.trim() }),
    ...(quickAction === undefined ? {} : { quick_action: quickAction }), context: sanitized,
  }, releaseContext);
  const chat = payload.chat;
  const receipts = [payload.receipt, ...(chat?.query_receipt ? [chat.query_receipt] : [])];
  if (payload.schema_version !== 1 || payload.result_type !== "conversation" || !payload.query_id ||
      !payload.publication_hash || !payload.query_generation_id || chat?.schema_version !== 1 ||
      chat.publication_hash !== payload.publication_hash || !sanitizeChatContext(chat.context) ||
      payload.receipt?.tool_name !== "chat_turn" ||
      receipts.some(receipt => receipt?.publication_hash !== payload.publication_hash || receipt?.query_generation_id !== payload.query_generation_id) ||
      !chatDisplaySafe(chat, payload)) {
    throw Object.assign(new Error("對話回應或發布收據無法驗證，未顯示回答。"), { code: "INVALID_RESPONSE" });
  }
  return payload;
}

export function loadChatContext(storage) {
  try {
    const raw = storage?.getItem(CHAT_CONTEXT_STORAGE_KEY);
    if (!raw) return { schema_version: CHAT_CONTEXT_SCHEMA_VERSION };
    return sanitizeChatContext(JSON.parse(raw)) || { schema_version: CHAT_CONTEXT_SCHEMA_VERSION };
  } catch {
    return { schema_version: CHAT_CONTEXT_SCHEMA_VERSION };
  }
}

export function saveChatContext(storage, context) {
  const sanitized = sanitizeChatContext(context);
  if (!sanitized) return false;
  try {
    storage?.setItem(CHAT_CONTEXT_STORAGE_KEY, JSON.stringify(sanitized));
    return true;
  } catch {
    return false;
  }
}

export function clearChatContext(storage) {
  try {
    storage?.removeItem(CHAT_CONTEXT_STORAGE_KEY);
  } catch {
    // Session storage may be unavailable; the in-memory state still clears.
  }
}

const SECTION_LABELS = {
  verified: "已驗證",
  unverified: "待確認（未驗證）",
  conflicts: "來源衝突",
  stale: "資料可能過期",
};

function resolvedRequestText(request) {
  if (!request || typeof request !== "object" || !request.tool) return "";
  const args = request.arguments && typeof request.arguments === "object" ? request.arguments : {};
  const parts = Object.keys(args).sort().map((key) => `${key}=${JSON.stringify(args[key])}`);
  return `${request.tool}${parts.length ? ` · ${parts.join(" · ")}` : ""}`;
}

export function summarizeChat(chat) {
  const source = chat && typeof chat === "object" ? chat : {};
  const sections = [];
  for (const kind of ["verified", "unverified", "conflicts", "stale"]) {
    const items = Array.isArray(source[kind]) ? source[kind] : [];
    if (items.length) sections.push({ kind, label: SECTION_LABELS[kind], items: items.map(item => {
      if (!item || typeof item !== "object") return item;
      const index = (source.context?.last_result_event_ids || []).indexOf(item.public_event_id);
      return { ...item, result_number: index < 0 ? null : index + 1 };
    }) });
  }
  return {
    status: source.status || "OK",
    statusLabel: CHAT_STATUS_LABELS[source.status] || source.status || "未知",
    intent: source.intent || null,
    lines: Array.isArray(source.lines) ? source.lines : [],
    sections,
    statistics: Array.isArray(source.statistics) ? source.statistics : [],
    sources: Array.isArray(source.sources) ? source.sources : [],
    comparison: source.comparison || null,
    evidenceLinks: Array.isArray(source.evidence_links) ? source.evidence_links : [],
    gaps: Array.isArray(source.gaps) ? source.gaps : [],
    freshness: source.freshness || null,
    trust: source.trust || null,
    noMatch: source.no_match || null,
    notices: Array.isArray(source.notices) ? source.notices : [],
    quickActionHints: Array.isArray(source.quick_action_hints) ? source.quick_action_hints : [],
    resolvedText: resolvedRequestText(source.resolved_request),
    publicationHash: source.publication_hash || null,
    queryReceipt: source.query_receipt || null,
    context: sanitizeChatContext(source.context) || { schema_version: CHAT_CONTEXT_SCHEMA_VERSION },
  };
}
