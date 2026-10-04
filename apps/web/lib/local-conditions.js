export const CONDITION_STORAGE_KEY = "govintel.v2.conditions.v1";

const ALLOWED_FILTER_KEYS = new Set(["source_id", "keywords", "district", "category", "road", "topic", "agency", "time_from", "time_to", "time_semantics"]);
const MATERIAL_TYPES = new Set(["NEW", "REVISED", "STATUS_CHANGED", "DEADLINE_CHANGED", "CANCELLED", "RELEASED", "OFFICIAL_CANCELLED", "OFFICIAL_RELEASED"]);
const RESERVED_IDS = new Set(["__proto__", "prototype", "constructor"]);

function namespaceFor(value) { return value?.namespace || "published"; }
function signature(filters) {
  return JSON.stringify(Object.fromEntries(Object.entries(filters).sort(([a], [b]) => a.localeCompare(b)).map(([key, value]) => [key, Array.isArray(value) ? [...value].map((v) => key === "keywords" ? v.toLocaleLowerCase("zh-Hant") : v).sort() : value])));
}
function eventIdFor(item) { return item?.public_event_id || item?.event_id || item?.identity || item?.stable_id || ""; }
function versionFor(item) {
  const version = item?.material_version ?? item?.source_version ?? item?.version_no ?? item?.version;
  return Number.isInteger(version) && version > 0 ? version : 0;
}
function scopedKey(namespace, key) { return namespace === "published" ? key : `${namespace}::${key}`; }
function keyFor(item) {
  const id = eventIdFor(item);
  if (!id) return null;
  return item.material_change_id ? `${id}#change:${item.material_change_id}` : readKey(id, versionFor(item));
}
function materialItem(item) {
  const kind = String(item.materiality || item.change_class || item.comparison?.materiality || "").toUpperCase();
  if (["FORMAT_ONLY", "FORMATTING_ONLY", "PRESENTATION_ONLY", "UNCHANGED", "NON_MATERIAL"].includes(kind) || item.is_material === false) return false;
  return Boolean(item.material_change_id || (versionFor(item) && MATERIAL_TYPES.has(String(item.change_type || "").toUpperCase())));
}
function timeOf(value) { const parsed = new Date(value).getTime(); return Number.isFinite(parsed) ? parsed : null; }


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
    } else {
      if (typeof raw !== "string" || !raw.trim()) throw new Error(`${key} 必須是非空字串`);
      normalized[key] = raw.trim();
    }
  }
  if (normalized.time_semantics && normalized.time_semantics !== "event_overlap") throw new Error("僅支援事件時段重疊查詢");
  for (const key of ["time_from", "time_to"]) if (normalized[key] && timeOf(normalized[key]) === null) throw new Error(`${key} 時間格式無效`);
  if (normalized.time_from && normalized.time_to && timeOf(normalized.time_from) > timeOf(normalized.time_to)) throw new Error("開始時間不能晚於結束時間");
  return normalized;
}

function itemMatches(item, condition) {
  const filters = condition.filters || {};
  if (filters.source_id && !filters.source_id.includes(item.source_id)) return false;
  if (filters.keywords && filters.keywords.length > 0) {
    const haystack = [item.headline, item.title, item.what_changed, item.summary, ...(item.road_names || []), ...(item.road_segments || []).map((s) => typeof s === "string" ? s : s.road_name)].filter(Boolean).join(" ").toLocaleLowerCase("zh-Hant");
    if (!filters.keywords.some((kw) => haystack.includes(kw.toLocaleLowerCase("zh-Hant")))) return false;
  }
  if (filters.district) {
    const locations = [item.district_id, item.district, ...(item.district_ids || []), ...(item.location_ids || []), ...(item.location_candidates || [])];
    if (!locations.includes(filters.district)) return false;
  }
  if (filters.category) {
    if (item.change_type !== filters.category && item.event_type !== filters.category) return false;
  }
  for (const [filter, fields] of [["road", [item.road, ...(item.road_names || []), ...(item.road_segments || []).map((s) => typeof s === "string" ? s : s.road_name)]], ["topic", [item.topic, ...(item.topic_ids || []), ...(item.topics || [])]], ["agency", [item.agency_id, ...(item.agency_ids || [])]]]) {
    if (filters[filter] && !fields.some((value) => typeof value === "string" && value.toLocaleLowerCase("zh-Hant").includes(filters[filter].toLocaleLowerCase("zh-Hant")))) return false;
  }
  if (filters.time_from || filters.time_to) {
    const start = timeOf(item.event_start_at || item.event_time_start || item.effective_from || item.time_start || item.start_at);
    const end = timeOf(item.event_end_at || item.event_time_end || item.effective_to || item.time_end || item.end_at);
    if (start === null || end === null) return false;
    if (filters.time_from && end < timeOf(filters.time_from)) return false;
    if (filters.time_to && start > timeOf(filters.time_to)) return false;
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
      || !conditionId || RESERVED_IDS.has(conditionId)
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
    if (condition.namespace !== undefined && !["published", "demo:commute"].includes(condition.namespace)) throw new Error("未知追蹤資料範圍");
    if (condition.condition_version !== undefined && (!Number.isInteger(condition.condition_version) || condition.condition_version < 1)) throw new Error("條件版本無效");
    if (condition.baseline_keys !== undefined && (!Array.isArray(condition.baseline_keys) || condition.baseline_keys.some((key) => typeof key !== "string"))) throw new Error("初始清單格式無效");
    if (condition.baseline_at) stamp(condition.baseline_at);
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
      || !Number.isInteger(entry.version) || entry.version < 0
      || !Array.isArray(entry.condition_ids)
      || typeof entry.read_at !== "string"
    ) {
      throw new Error(`本機已讀紀錄無法驗證：${key}`);
    }
    if (key !== scopedKey(namespaceFor(entry), (entry.material_change_id ? `${entry.event_id}#change:${entry.material_change_id}` : readKey(entry.event_id, entry.version)))) throw new Error(`本機已讀索引無法驗證：${key}`);
    stringList(entry.condition_ids, "condition_ids");
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
  const namespace = namespaceFor(condition);
  if (!["published", "demo:commute"].includes(namespace)) throw new Error("未知追蹤資料範圍");
  const equivalent = Object.values(result.conditions).find((row) => row.enabled && namespaceFor(row) === namespace && signature(row.filters) === signature(filters));
  if (equivalent) return result;
  const conditionId = condition.condition_id || `cond-${globalThis.crypto?.randomUUID?.() || `${Date.now().toString(36)}-${Object.keys(result.conditions).length}`}`;
  if (typeof conditionId !== "string" || !conditionId || RESERVED_IDS.has(conditionId)) throw new Error("條件 ID 無效");
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
    namespace,
    condition_version: (existing?.condition_version || 0) + 1,
    baseline_generation: null,
    baseline_keys: [],
    baseline_at: null,
    baseline_initialized: false,
  };
  result.last_updated_at = at;
  return result;
}

export function updateLocalCondition(state, conditionId, updates, updatedAt = new Date()) {
  const result = copy(validateLocalConditions(state));
  const condition = result.conditions[conditionId];
  if (!condition) throw new Error(`找不到條件：${conditionId}`);
  const at = stamp(updatedAt);
  const changedFilters = updates.filters && signature(normalizeFilters(updates.filters)) !== signature(condition.filters);
  const reenabled = updates.enabled === true && !condition.enabled;
  if (updates.filters) condition.filters = normalizeFilters(updates.filters);
  if (typeof updates.enabled === "boolean") condition.enabled = updates.enabled;
  if (changedFilters || reenabled) {
    condition.condition_version = (condition.condition_version || 1) + 1;
    condition.baseline_generation = null;
    condition.baseline_keys = [];
    condition.baseline_at = null;
    condition.baseline_initialized = false;
  }
  condition.updated_at = at;
  result.last_updated_at = at;
  return result;
}

export function cancelLocalCondition(state, conditionId, updatedAt = new Date()) {
  return updateLocalCondition(state, conditionId, { enabled: false }, updatedAt);
}

export function matchPublishedItems(state, items, options = {}) {
  const valid = validateLocalConditions(state);
  const namespace = typeof options === "object" ? options.namespace || "published" : "published";
  const activeConditions = Object.values(valid.conditions).filter((c) => c.enabled && namespaceFor(c) === namespace);
  const seen = new Map();
  for (const raw of Array.isArray(items) ? items : []) {
    const item = normalizeConditionItem(raw);
    if (!item || !keyFor(item)) continue;
    const matched = activeConditions.filter((condition) => itemMatches(item, condition));
    if (!matched.length) continue;
    const key = keyFor(item);
    let entry = seen.get(key);
    if (!entry) {
      const material = materialItem(item);
      const initial = matched.every((condition) => condition.baseline_keys?.includes(key));
      const published = timeOf(item.official_published_at || item.published_at);
      const ended = timeOf(item.end_at || item.effective_to || item.event_end_at);
      const historical = !material || (item.change_type === "NEW" && matched.every((condition) => condition.baseline_at && (published === null || published < timeOf(condition.baseline_at) || (ended !== null && ended < timeOf(condition.baseline_at)))));
      entry = { ...item, event_id: eventIdFor(item), source_version: versionFor(item), change_key: scopedKey(namespace, key), namespace, hit_condition_ids: [], read: false, update_kind: initial ? "INITIAL" : historical ? "HISTORICAL" : "UPDATE" };
      seen.set(key, entry);
    }
    entry.hit_condition_ids = [...new Set([...entry.hit_condition_ids, ...matched.map((condition) => condition.condition_id)])];
  }
  for (const entry of seen.values()) {
    const readEntry = valid.read_entries[entry.change_key];
    entry.read = Boolean(readEntry && entry.hit_condition_ids.every((id) => readEntry.condition_ids.includes(id)));
  }
  return [...seen.values()];
}

export function markLocalRead(state, conditionId, eventId, version, readAt = new Date(), materialChangeId = null, namespace = "published") {
  const result = copy(validateLocalConditions(state));
  const at = stamp(readAt);
  if (typeof eventId !== "string" || !eventId || !Number.isInteger(version) || version < 0 || typeof conditionId !== "string" || !conditionId) throw new Error("已讀標記缺少可驗證的事件版本");
  const key = scopedKey(namespace, materialChangeId ? `${eventId}#change:${materialChangeId}` : readKey(eventId, version));
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
      ...(materialChangeId ? { material_change_id: materialChangeId } : {}),
      ...(namespace !== "published" ? { namespace } : {}),
    };
  }
  result.last_updated_at = at;
  return result;
}

export function getUnreadUpdates(state, items, options = {}) {
  const matches = matchPublishedItems(state, items, options);
  return matches.filter((m) => !m.read && m.update_kind === "UPDATE");
}

export function projectLocalConditions(state, items, options = {}) {
  return matchPublishedItems(state, items, options);
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


// Only IDs and query conditions enter localStorage; public source documents remain shared.
export function initializeConditionBaselines(state, items, publication = {}, namespace = "published") {
  const result = copy(validateLocalConditions(state));
  if (publication.generation_mixed) throw new Error("資料世代不一致，未更新追蹤初始清單");
  for (const condition of Object.values(result.conditions)) {
    if (!condition.enabled || namespaceFor(condition) !== namespace || condition.baseline_initialized) continue;
    condition.baseline_keys = [...new Set((Array.isArray(items) ? items : []).map(normalizeConditionItem).filter((item) => item && itemMatches(item, condition)).map(keyFor).filter(Boolean))];
    condition.baseline_generation = publication.source_collection_run_id || publication.collection_run_id || publication.generation || null;
    condition.baseline_at = publication.generated_at ? stamp(publication.generated_at) : condition.updated_at;
    condition.baseline_initialized = true;
    condition.baseline_complete = publication.snapshot_complete === true;
  }
  if (publication.snapshot_complete === true) result.last_successful_check = { generation: publication.source_collection_run_id || publication.collection_run_id || publication.generation || null, checked_at: stamp(publication.generated_at || new Date()) };
  return result;
}

export function saveLocalConditionRequest(request, items = [], publication = {}, storage = null) {
  const state = loadLocalConditions(storage);
  const added = addLocalCondition(state, request);
  const initialized = initializeConditionBaselines(added, items, publication, namespaceFor(request));
  return saveLocalConditions(initialized, storage);
}

export function markDisplayedUpdatesRead(state, displayed, readAt = new Date()) {
  let next = state;
  for (const item of displayed) {
    if (!item.change_key || item.change_key !== scopedKey(namespaceFor(item), keyFor(item))) throw new Error("顯示項目的版本不一致，未標記已讀");
    for (const conditionId of item.hit_condition_ids || []) next = markLocalRead(next, conditionId, eventIdFor(item), versionFor(item), readAt, item.material_change_id || null, namespaceFor(item));
  }
  return next;
}

export function clearLocalConditions(storage = null) {
  const target = storage || (typeof window !== "undefined" ? window.localStorage : null);
  if (!target) throw new Error("瀏覽器不提供本機儲存");
  target.removeItem(CONDITION_STORAGE_KEY);
  return emptyLocalConditions();
}

export function collectConditionItems(publication, archive) {
  const rows = Array.isArray(archive?.items) ? [...archive.items] : [];
  for (const view of [publication, ...(publication?.profile_views || [])]) {
    for (const key of ["priority_items", "tracking_items", "other_changes"]) if (Array.isArray(view?.[key])) rows.push(...view[key]);
  }
  const seen = new Map();
  for (const item of rows) if (keyFor(item)) seen.set(keyFor(item), item);
  return [...seen.values()];
}

export function conditionDatasetStatus(publication, archive, sourceStatus) {
  const runs = [publication?.source_collection_run_id || publication?.collection_run_id, archive?.collection_run_id, sourceStatus?.latest_collection_run?.collection_run_id || sourceStatus?.collection_run_id];
  const present = runs.every(Boolean) && [publication, archive, sourceStatus].every(Boolean);
  const mixed = present && (new Set(runs).size !== 1 || new Set([publication.generated_at, archive.generated_at, sourceStatus.generated_at]).size !== 1);
  const sources = Array.isArray(sourceStatus?.sources) ? sourceStatus.sources : [];
  const gaps = sources.filter((row) => row.source_health !== "PASS" || row.freshness_status !== "FRESH" || (row.intelligence_gaps || []).length > 0);
  const complete = present && !mixed && publication.snapshot_complete === true && sources.length > 0 && gaps.length === 0 && !archive.truncated && !archive.has_more && (sourceStatus.publication_status || sourceStatus.status || "") !== "PARTIAL";
  return { present, mixed, complete, gaps, reason: mixed ? "資料世代不一致，已保留本機紀錄；停止比對。" : !present ? "公開資料或來源狀態未完整取得，保留已保存條件。" : complete ? "目前發布快照已載入；未收錄事件仍可能存在。" : "資料涵蓋仍有缺口或陳舊來源，僅比對已收錄快照；未讀清單不能代表完整現況。" };
}


export function normalizeConditionItem(item) {
  if (!item || typeof item !== "object") return null;
  const comparison = item.comparison;
  let changeType = item.change_type;
  if (!changeType && item.public_event_id) {
    if (comparison?.materiality === "MATERIAL") {
      const status = comparison.after?.fields?.status || item.event_status;
      changeType = ["LIFTED", "RELEASED"].includes(status) ? "RELEASED" : status === "CANCELLED" ? "CANCELLED" : (comparison.changed_fields || []).includes("effective_to") ? "DEADLINE_CHANGED" : "REVISED";
    } else changeType = versionFor(item) === 1 ? "NEW" : "UNKNOWN";
  }
  return {
    ...item,
    event_id: eventIdFor(item),
    headline: item.headline || item.title || item.canonical_title,
    source_id: item.source_id || item.independent_source_ids?.[0],
    source_version: versionFor(item),
    change_type: changeType,
    official_url: item.official_url || item.documents?.at(-1)?.official_url,
    daily_schedule: item.daily_schedule || item.daily,
    effective_from: item.effective_from || item.start_at,
    effective_to: item.effective_to || item.end_at,
  };
}
