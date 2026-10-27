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

// The assisted route's own fields: how the question was parsed and the answer
// step's outcome. Only gate-checked text may appear in an answer.
export const CHAT_ASSIST_REASON_LABELS = {
  DISABLED: "站方未啟用 AI 解析", KEY_MISSING: "AI 尚未完成啟用設定", BILLING_REVIEW_REQUIRED: "AI 尚未完成啟用設定",
  ADMISSION_UNCONFIGURED: "AI 尚未完成啟用設定", ADMISSION_DENIED: "未取得 AI 使用授權或額度", OUTPUT_REJECTED: "AI 輸出未通過核對",
  PROVIDER_UNAVAILABLE: "AI 服務暫時無法使用", INPUT_RESTRICTED: "問題含可能的個人資料", RIGHTS_BLOCKED: "來源權利尚未核准，不產生 AI 回答",
  TOOL_HAS_NO_ANSWER_STEP: "此類查詢不產生 AI 回答", QUERY_NOT_EXECUTED: "查詢條件無法套用，未產生回答", NO_MATCH: "沒有符合的已收錄公告",
  NO_APPROVED_SOURCES: "沒有可供核對的目前官方來源", NO_RELEVANT_SELECTION: "AI 未找到相關公告",
};
const reasonCode = value => value === null || (typeof value === "string" && /^[A-Z][A-Z0-9_]{0,63}$/.test(value));
const day = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value);
const GATE_VERSION = "answer-evidence-gate/3";
const RENDERER_VERSION = "controlled-answer-renderer/1";

async function sha256Hex(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map(byte => byte.toString(16).padStart(2, "0")).join("");
}

// Fallback reasons under which the question never reached the provider.
export const CHAT_ASSIST_NOT_SENT = new Set(["DISABLED", "KEY_MISSING", "BILLING_REVIEW_REQUIRED", "ADMISSION_UNCONFIGURED", "ADMISSION_DENIED"]);

// A ready answer must be the Worker's controlled rendering of a passing gate's
// supported title claims for this publication: claim i is the title of selected
// ID i, cites that evidence, and its line is rebuilt here from the proposition.
function readyAnswerSafe(answer, payload) {
  const receipt = answer.answer_evidence_receipt;
  const ids = answer.selected_evidence_ids;
  if (answer.reason_code !== null || answer.answer.length === 0 || !receipt || typeof receipt !== "object" ||
      receipt.gate_status !== "PASS" || receipt.validator_version !== GATE_VERSION || receipt.renderer_version !== RENDERER_VERSION ||
      receipt.publication_hash !== payload.publication_hash || !/^[a-f0-9]{64}$/.test(receipt.answer_sha256 || "") ||
      !Array.isArray(receipt.evidence_ids) || !Array.isArray(receipt.claims) || receipt.claims.length !== answer.answer.length ||
      !Array.isArray(ids) || ids.length !== answer.answer.length || new Set(ids).size !== ids.length) return false;
  return ids.every((id, index) => {
    const claim = receipt.claims[index];
    const proposition = claim?.propositions?.[0];
    return typeof id === "string" && id.length <= 256 && id.startsWith("PUB-") && receipt.evidence_ids.includes(id) &&
      claim.claim_type === "STATUS" && claim.support_status === "SUPPORTED" && claim.propositions.length === 1 &&
      proposition.subject === `publication:${id.slice(4)}:title` && typeof proposition.value === "string" && proposition.value.length <= 1024 &&
      Array.isArray(claim.supporting_evidence) && claim.supporting_evidence.some(row => row?.evidence_id === id) &&
      answer.answer[index] === `官方來源已核對：${proposition.subject}=${proposition.value}。`;
  });
}

function assistSafe(chat, payload) {
  const parser = chat.parser;
  const answer = chat.assisted_answer;
  if (!parser || typeof parser !== "object" || Array.isArray(parser) || typeof parser.version !== "string" || parser.version.length > 64) return false;
  const range = parser.time_window;
  if (parser.kind === "llm") {
    if (range !== null && (!range || typeof range !== "object" || !day(range.time_from) || !day(range.time_to) ||
        range.time_zone !== "Asia/Taipei" || typeof range.label !== "string" || range.label.length > 64)) return false;
  } else if (parser.kind !== "rules" || !reasonCode(parser.fallback_reason)) return false;
  if (!answer || typeof answer !== "object" || Array.isArray(answer) || !reasonCode(answer.reason_code) ||
      !Array.isArray(answer.answer) || answer.answer.length > 32 || answer.answer.some(line => typeof line !== "string" || line.length > 2000)) return false;
  if (answer.status === "ANSWER_READY") return readyAnswerSafe(answer, payload);
  return ["NO_ANSWER", "NOT_ATTEMPTED"].includes(answer.status) && answer.answer.length === 0;
}

// assist: send the question to the Web-only assisted route. It needs the
// public-data attestation, always binds the release and allows more time.
export async function requestChat(endpoint, { text, quickAction, context = { schema_version: 1 }, assist = false, publicDataOnly = false }, releaseContext = null) {
  const sanitized = sanitizeChatContext(context);
  if (!sanitized || (text !== undefined && (typeof text !== "string" || !text.trim() || text.length > 512)) ||
      (quickAction !== undefined && !CHAT_QUICK_ACTIONS.some(action => action.id === quickAction)) ||
      (text === undefined && quickAction === undefined) || (assist && (text === undefined || quickAction !== undefined))) {
    throw Object.assign(new Error("對話問題或條件無法驗證，請重新輸入。"), { code: "INVALID_ARGUMENTS" });
  }
  if (assist && publicDataOnly !== true) {
    throw Object.assign(new Error("請先確認問題只含公開、不含個資的資訊。"), { code: "PUBLIC_DATA_CONFIRMATION_REQUIRED" });
  }
  const payload = await queryGateway(endpoint, "chat_turn", {
    ...(text === undefined ? {} : { text: text.trim() }),
    ...(quickAction === undefined ? {} : { quick_action: quickAction }), context: sanitized,
  }, releaseContext, assist ? { route: "chat", timeoutMs: 45000, bindRelease: true, body: { public_data_only: true } } : undefined);
  const chat = payload.chat;
  const receipts = [payload.receipt, ...(chat?.query_receipt ? [chat.query_receipt] : [])];
  if (payload.schema_version !== 1 || payload.result_type !== "conversation" || !payload.query_id ||
      !payload.publication_hash || !payload.query_generation_id || chat?.schema_version !== 1 ||
      chat.publication_hash !== payload.publication_hash || !sanitizeChatContext(chat.context) ||
      payload.receipt?.tool_name !== "chat_turn" ||
      receipts.some(receipt => receipt?.publication_hash !== payload.publication_hash || receipt?.query_generation_id !== payload.query_generation_id) ||
      !chatDisplaySafe(chat, payload) || (assist && !assistSafe(chat, payload)) ||
      // A deterministic reply never carries the assisted fields; they would render as checked.
      (!assist && (chat.parser !== undefined || chat.assisted_answer !== undefined)) ||
      (assist && chat.assisted_answer.status === "ANSWER_READY" &&
        await sha256Hex(JSON.stringify(chat.assisted_answer.answer)) !== chat.assisted_answer.answer_evidence_receipt.answer_sha256)) {
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
    parser: source.parser || null,
    assistedAnswer: source.assisted_answer || null,
    context: sanitizeChatContext(source.context) || { schema_version: CHAT_CONTEXT_SCHEMA_VERSION },
  };
}
