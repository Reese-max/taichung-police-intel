export const REVIEW_STORAGE_KEY = "govintel.review-inbox.local.v1";

const LOCAL_STATUSES = new Set(["OPEN", "KEEP_WATCHING", "RESOLVED", "DISMISSED"]);
const DECISIONS = new Set(["KEEP_WATCHING", "RESOLVED", "DISMISSED"]);

function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function stamp(value) {
  const date = value instanceof Date ? value : new Date(value ?? Date.now());
  if (Number.isNaN(date.getTime())) throw new Error("時間格式無法驗證");
  return date.toISOString();
}

function sourceVersion(item) {
  const value = item?.source_version;
  return value === undefined || value === null || value === "" ? "UNKNOWN" : String(value);
}

function publicItem(item) {
  if (!item || typeof item.review_id !== "string" || !item.review_id.trim()) return null;
  if (typeof item.evidence_sha256 !== "string" || !/^[0-9a-f]{64}$/i.test(item.evidence_sha256)) return null;
  return {
    review_id: item.review_id,
    reason: item.reason || "NEEDS_REVIEW",
    canonical_status: item.status || "OPEN",
    source_version: sourceVersion(item),
    evidence_sha256: item.evidence_sha256.toLowerCase(),
    entity_ids: item.entity_ids && typeof item.entity_ids === "object" ? copy(item.entity_ids) : {},
  };
}

function bindingId(item) {
  return encodeURIComponent(`${item.review_id}|${item.source_version}|${item.evidence_sha256}`);
}

function historyEntry(action, at, item, extra = {}) {
  return {
    action,
    at,
    binding_id: bindingId(item),
    source_version: item.source_version,
    evidence_sha256: item.evidence_sha256,
    ...extra,
  };
}

export function emptyLocalReview() {
  return {
    schema_version: 1,
    mode: "LOCAL_REVIEW_OVERLAY",
    items: {},
    last_updated_at: null,
  };
}

export function validateLocalReview(value) {
  if (!value || value.schema_version !== 1 || value.mode !== "LOCAL_REVIEW_OVERLAY") {
    throw new Error("本機覆核資料格式不相容，已停止載入");
  }
  if (!value.items || Array.isArray(value.items)) throw new Error("本機覆核資料結構不完整，已停止載入");
  for (const [reviewId, item] of Object.entries(value.items)) {
    if (
      !item
      || item.review_id !== reviewId
      || typeof item.reason !== "string"
      || !LOCAL_STATUSES.has(item.local_status)
      || typeof item.source_version !== "string"
      || typeof item.evidence_sha256 !== "string"
      || !/^[0-9a-f]{64}$/.test(item.evidence_sha256)
      || !item.entity_ids
      || typeof item.entity_ids !== "object"
      || item.binding_id !== bindingId(item)
      || !Array.isArray(item.history)
      || !item.history.length
      || typeof item.updated_at !== "string"
    ) {
      throw new Error(`本機覆核資料無法驗證：${reviewId}`);
    }
    stamp(item.updated_at);
    for (const entry of item.history) {
      if (
        !entry
        || typeof entry.action !== "string"
        || !entry.action
        || typeof entry.binding_id !== "string"
        || typeof entry.source_version !== "string"
        || typeof entry.evidence_sha256 !== "string"
        || !/^[0-9a-f]{64}$/.test(entry.evidence_sha256)
      ) {
        throw new Error(`本機覆核歷史無法驗證：${reviewId}`);
      }
      stamp(entry.at);
      if (entry.binding_id !== bindingId({
        review_id: reviewId,
        source_version: entry.source_version,
        evidence_sha256: entry.evidence_sha256,
      })) {
        throw new Error(`本機覆核歷史 binding 無法驗證：${reviewId}`);
      }
    }
    if (item.decision !== null && (
      !item.decision
      || !DECISIONS.has(item.decision.decision)
      || typeof item.decision.decided_at !== "string"
      || item.decision.binding_id !== item.binding_id
    )) {
      throw new Error(`本機覆核決策無法驗證：${reviewId}`);
    }
    if (item.decision) stamp(item.decision.decided_at);
  }
  if (value.last_updated_at !== null) stamp(value.last_updated_at);
  return value;
}

export function loadLocalReview(storage = null) {
  const target = storage || (typeof window !== "undefined" ? window.localStorage : null);
  if (!target) return emptyLocalReview();
  const raw = target.getItem(REVIEW_STORAGE_KEY);
  if (!raw) return emptyLocalReview();
  return validateLocalReview(JSON.parse(raw));
}

export function saveLocalReview(state, storage = null) {
  const target = storage || (typeof window !== "undefined" ? window.localStorage : null);
  const valid = validateLocalReview(state);
  if (!target) throw new Error("瀏覽器不提供本機儲存，無法保存覆核草稿");
  target.setItem(REVIEW_STORAGE_KEY, JSON.stringify(valid));
  return valid;
}

export function syncLocalReview(state, items, observedAt = new Date()) {
  const result = copy(validateLocalReview(state));
  const at = stamp(observedAt);
  for (const raw of Array.isArray(items) ? items : []) {
    const current = publicItem(raw);
    if (!current) continue;
    const existing = result.items[current.review_id];
    if (!existing) {
      result.items[current.review_id] = {
        ...current,
        binding_id: bindingId(current),
        local_status: "OPEN",
        decision: null,
        history: [historyEntry("OBSERVED", at, current)],
        updated_at: at,
      };
      continue;
    }
    const changed = existing.binding_id !== bindingId(current);
    if (changed) {
      existing.history.push(historyEntry("REOPENED", at, current, { previous_binding_id: existing.binding_id }));
      existing.local_status = "OPEN";
      existing.decision = null;
      existing.source_version = current.source_version;
      existing.evidence_sha256 = current.evidence_sha256;
      existing.binding_id = bindingId(current);
      existing.updated_at = at;
    }
    existing.reason = current.reason;
    existing.canonical_status = current.canonical_status;
    existing.entity_ids = current.entity_ids;
  }
  result.last_updated_at = at;
  return result;
}

export function setLocalReviewDecision(state, reviewId, decision, decidedAt = new Date()) {
  const result = copy(validateLocalReview(state));
  const item = result.items[reviewId];
  const normalized = String(decision || "").toUpperCase().replace(/-/g, "_");
  if (!item) throw new Error(`找不到本機覆核項目：${reviewId}`);
  if (normalized === "OPEN") {
    item.local_status = "OPEN";
    item.decision = null;
  } else if (DECISIONS.has(normalized)) {
    item.local_status = normalized;
    item.decision = {
      decision: normalized,
      decided_at: stamp(decidedAt),
      binding_id: item.binding_id,
    };
  } else {
    throw new Error(`不支援的本機覆核決策：${decision}`);
  }
  const at = stamp(decidedAt);
  item.history.push(historyEntry(normalized === "OPEN" ? "REOPENED" : "LOCAL_DECISION", at, item, {
    decision: normalized,
  }));
  item.updated_at = at;
  result.last_updated_at = at;
  return result;
}

export function projectLocalReview(state, items) {
  const valid = state ? validateLocalReview(state) : emptyLocalReview();
  return (Array.isArray(items) ? items : []).map((item) => {
    const local = valid.items[item?.review_id];
    if (!local) return { ...item, local_status: "CANONICAL_ONLY" };
    return {
      ...item,
      local_status: local.local_status,
      local_decision: local.decision,
      local_binding_id: local.binding_id,
    };
  });
}

function reviewMarkdown(state) {
  const valid = validateLocalReview(state);
  const lines = [
    "# GovIntel AI 本機 Review Inbox 覆核紀錄",
    "",
    "> 僅保存於本機瀏覽器；不會修改 canonical state、原始文件或公開發布物。",
    "",
  ];
  for (const item of Object.values(valid.items)) {
    lines.push(
      `## ${item.review_id}`,
      `- reason: \`${item.reason}\``,
      `- local_status: \`${item.local_status}\``,
      `- source_version: \`${item.source_version}\``,
      `- evidence_sha256: \`${item.evidence_sha256}\``,
      `- binding_id: \`${item.binding_id}\``,
      `- history_entries: ${item.history.length}`,
      "",
    );
  }
  return lines.join("\n");
}

export function exportLocalReview(state, format = "json") {
  const valid = validateLocalReview(state);
  if (format === "markdown") {
    return {
      content: reviewMarkdown(valid),
      filename: "govintel-review-inbox-local.md",
      mime: "text/markdown;charset=utf-8",
    };
  }
  return {
    content: JSON.stringify(valid, null, 2),
    filename: "govintel-review-inbox-local.json",
    mime: "application/json",
  };
}
