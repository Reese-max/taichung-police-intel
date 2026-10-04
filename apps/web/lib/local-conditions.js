export const CONDITION_STORAGE_KEY = "govintel.v2.conditions.v1";

const ALLOWED_FILTER_KEYS = new Set(["source_id", "keywords", "district", "category"]);

function copy(value) {
  return JSON.parse(JSON.stringify(value));
}

function stamp(value) {
  const date = value instanceof Date ? value : new Date(value ?? Date.now());
  if (Number.isNaN(date.getTime())) throw new Error("時間格式無法驗證");
  return date.toISOString();
}

function stringList(value, name) {
  if (!Array.isArray(value)) throw new Error(`${name} 必須是陣列`);
  const result = [];
  for (const item of value) {
    if (typeof item !== "string" || !item.trim()) throw new Error(`${name} 必須是非空字串陣列`);
    result.push(item.trim());
  }
  if (new Set(result).size !== result.length) throw new Error(`${name} 不能包含重複項`);
  return result;
}

function normalizeFilters(filters) {
  if (!filters || typeof filters !== "object" || Array.isArray(filters)) {
    throw new Error("條件格式無效：filters 必須是物件");
  }
  const normalized = {};
  for (const [key, raw] of Object.entries(filters)) {
    if (!ALLOWED_FILTER_KEYS.has(key)) throw new Error(`條件格式無效：不支援的過濾欄位 ${key}`);
    if (key === "source_id" || key === "keywords") {
      normalized[key] = stringList(raw, key);
    } else if (key === "district" || key === "category") {
      if (typeof raw !== "string" || !raw.trim()) throw new Error(`${key} 必須是非空字串`);
      normalized[key] = raw.trim();
    }
  }
  return normalized;
}

function itemMatches(item, condition) {
  const filters = condition.filters || {};
  if (filters.source_id && !filters.source_id.includes(item.source_id)) return false;
  if (filters.keywords && filters.keywords.length > 0) {
    const haystack = [item.headline, item.what_changed].filter(Boolean).join(" ").toLocaleLowerCase("zh-Hant");
    if (!filters.keywords.some((kw) => haystack.includes(kw.toLocaleLowerCase("zh-Hant")))) return false;
  }
  if (filters.district) {
    const locations = [item.district_id, ...(item.location_ids || []), ...(item.location_candidates || [])];
    if (!locations.includes(filters.district)) return false;
  }
  if (filters.category) {
    if (item.change_type !== filters.category && item.event_type !== filters.category) return false;
  }
  return true;
}

function readKey(eventId, version) {
  return `${eventId}#v${Number(version) || 0}`;
}

export function emptyLocalConditions() {
  return {
    schema_version: 1,
    mode: "LOCAL_CONDITION_TRACKING",
    conditions: {},
    read_entries: {},
    last_updated_at: null,
  };
}

export function validateLocalConditions(value) {
  if (!value || value.schema_version !== 1 || value.mode !== "LOCAL_CONDITION_TRACKING") {
    throw new Error("本機條件格式不相容，已停止載入");
  }
  if (!value.conditions || typeof value.conditions !== "object" || Array.isArray(value.conditions)) {
    throw new Error("本機條件結構不完整，已停止載入");
  }
  if (!value.read_entries || typeof value.read_entries !== "object" || Array.isArray(value.read_entries)) {
    throw new Error("本機已讀結構不完整，已停止載入");
  }
  for (const [conditionId, condition] of Object.entries(value.conditions)) {
    if (
      !condition
      || condition.condition_id !== conditionId
      || typeof condition.enabled !== "boolean"
      || !condition.filters
      || typeof condition.filters !== "object"
      || Array.isArray(condition.filters)
      || typeof condition.created_at !== "string"
      || typeof condition.updated_at !== "string"
    ) {
      throw new Error(`本機條件無法驗證：${conditionId}`);
    }
    normalizeFilters(condition.filters);
    stamp(condition.created_at);
    stamp(condition.updated_at);
    if (condition.baseline_generation !== null && typeof condition.baseline_generation !== "string") {
      throw new Error(`本機條件 generation 無效：${conditionId}`);
    }
  }
  for (const [key, entry] of Object.entries(value.read_entries)) {
    if (
      !entry
      || typeof entry.event_id !== "string"
      || typeof entry.version !== "number"
      || !Array.isArray(entry.condition_ids)
      || typeof entry.read_at !== "string"
    ) {
      throw new Error(`本機已讀紀錄無法驗證：${key}`);
    }
    stamp(entry.read_at);
  }
  if (value.last_updated_at !== null) stamp(value.last_updated_at);
  return value;
}

export function loadLocalConditions(storage = null) {
  const target = storage || (typeof window !== "undefined" ? window.localStorage : null);
  if (!target) return emptyLocalConditions();
  const raw = target.getItem(CONDITION_STORAGE_KEY);
  if (!raw) return emptyLocalConditions();
  return validateLocalConditions(JSON.parse(raw));
}

export function saveLocalConditions(state, storage = null) {
  const target = storage || (typeof window !== "undefined" ? window.localStorage : null);
  const valid = validateLocalConditions(state);
  if (!target) throw new Error("瀏覽器不提供本機儲存，無法保存條件");
  target.setItem(CONDITION_STORAGE_KEY, JSON.stringify(valid));
  return valid;
}

export function addLocalCondition(state, condition, createdAt = new Date()) {
  const result = copy(validateLocalConditions(state));
  const filters = normalizeFilters(condition.filters || {});
  const at = stamp(createdAt);
  const conditionId = condition.condition_id || `cond-${Date.now().toString(36)}`;
  const existing = result.conditions[conditionId];
  if (existing && existing.enabled) {
    const existingKeys = JSON.stringify(Object.keys(existing.filters).sort());
    const newKeys = JSON.stringify(Object.keys(filters).sort());
    if (existingKeys === newKeys && JSON.stringify(existing.filters) === JSON.stringify(filters)) {
      return result;
    }
  }
  result.conditions[conditionId] = {
    condition_id: conditionId,
    enabled: true,
    filters,
    created_at: existing?.created_at || at,
    updated_at: at,
    baseline_generation: existing?.baseline_generation ?? null,
  };
  result.last_updated_at = at;
  return result;
}

export function updateLocalCondition(state, conditionId, updates, updatedAt = new Date()) {
  const result = copy(validateLocalConditions(state));
  const condition = result.conditions[conditionId];
  if (!condition) throw new Error(`找不到條件：${conditionId}`);
  const at = stamp(updatedAt);
  if (updates.filters) condition.filters = normalizeFilters(updates.filters);
  if (typeof updates.enabled === "boolean") condition.enabled = updates.enabled;
  condition.updated_at = at;
  result.last_updated_at = at;
  return result;
}

export function cancelLocalCondition(state, conditionId, updatedAt = new Date()) {
  return updateLocalCondition(state, conditionId, { enabled: false }, updatedAt);
}

export function matchPublishedItems(state, items, generation = null) {
  const valid = validateLocalConditions(state);
  const activeConditions = Object.values(valid.conditions).filter((c) => c.enabled);
  if (!activeConditions.length) return [];
  const seen = new Map();
  for (const item of items) {
    const matched = [];
    for (const condition of activeConditions) {
      if (itemMatches(item, condition)) matched.push(condition.condition_id);
    }
    if (!matched.length) continue;
    const key = `${item.event_id || item.identity || ""}|${item.source_version || 0}`;
    if (!seen.has(key)) {
      seen.set(key, { ...item, hit_condition_ids: matched, read: false });
    } else {
      const existing = seen.get(key);
      existing.hit_condition_ids = [...new Set([...existing.hit_condition_ids, ...matched])];
    }
  }
  for (const [, entry] of seen) {
    const key = readKey(entry.event_id || entry.identity, entry.source_version);
    const readEntry = valid.read_entries[key];
    entry.read = Boolean(readEntry && readEntry.condition_ids.every((id) => entry.hit_condition_ids.includes(id)));
  }
  return [...seen.values()];
}

export function markLocalRead(state, conditionId, eventId, version, readAt = new Date()) {
  const result = copy(validateLocalConditions(state));
  const at = stamp(readAt);
  const key = readKey(eventId, version);
  const existing = result.read_entries[key];
  if (existing) {
    if (!existing.condition_ids.includes(conditionId)) {
      existing.condition_ids.push(conditionId);
    }
  } else {
    result.read_entries[key] = {
      event_id: String(eventId),
      version: Number(version),
      condition_ids: [conditionId],
      read_at: at,
    };
  }
  result.last_updated_at = at;
  return result;
}

export function getUnreadUpdates(state, items) {
  const matches = matchPublishedItems(state, items);
  return matches.filter((m) => !m.read);
}

export function projectLocalConditions(state, items) {
  return matchPublishedItems(state, items);
}

export function exportLocalConditions(state, format = "json") {
  const valid = validateLocalConditions(state);
  if (format === "markdown") {
    const lines = [
      "# GovIntel AI 本機條件追蹤紀錄",
      "",
      "> 僅保存於本機瀏覽器；不會上傳到任何伺服器。",
      "",
    ];
    for (const condition of Object.values(valid.conditions)) {
      lines.push(`## ${condition.condition_id}`, `- 狀態：\`${condition.enabled ? "啟用" : "停用"}\``, `- 建立時間：\`${condition.created_at}\``, `- 更新時間：\`${condition.updated_at}\``, "```json", JSON.stringify(condition.filters, null, 2), "```", "");
    }
    return { content: lines.join("\n"), filename: "govintel-local-conditions.md", mime: "text/markdown;charset=utf-8" };
  }
  return { content: JSON.stringify(valid, null, 2), filename: "govintel-local-conditions.json", mime: "application/json" };
}
