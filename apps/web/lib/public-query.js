// The live path uses the existing Gateway. The saved path only filters bounded,
// Query Domain projections; it never acquires sources or generates an AI answer.
export function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function sha256Json(value) {
  if (!globalThis.crypto?.subtle) throw new Error("無法驗證資料 hash，已停止使用保存快照");
  const bytes = new TextEncoder().encode(canonicalJson(value));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export async function validateReplay(bundle) {
  if (bundle?.schema_version !== 1 || bundle.mode !== "SYNTHETIC_REPLAY" || bundle.namespace !== "demo:commute"
      || !bundle.snapshots || Object.keys(bundle.snapshots).length > 30) {
    throw new Error("保存快照格式不相容，已停止使用");
  }
  const { bundle_sha256, ...material } = bundle;
  if (!/^[a-f0-9]{64}$/.test(bundle_sha256) || await sha256Json(material) !== bundle_sha256) {
    throw new Error("保存快照 hash 混版或內容遭變更，已拒絕使用");
  }
  for (const snapshot of Object.values(bundle.snapshots)) {
    if (!Array.isArray(snapshot.events) || snapshot.events.length > 10000 || !snapshot.generation_id
        || new Set(snapshot.events.map((event) => event.public_event_id)).size !== snapshot.events.length) {
      throw new Error("保存快照事件結構無法驗證");
    }
  }
  return bundle;
}

export function safeHttpsUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      && !url.hostname.endsWith(".invalid") ? url.href : null;
  } catch { return null; }
}

export function normalizeQueryFilters(draft) {
  const filters = {};
  for (const key of ["q", "district", "category", "road", "agency"]) {
    const value = String(draft[key] || "").trim();
    if (value.length > 512) throw new Error("查詢條件超過 512 字元上限");
    if (value) filters[key] = value;
  }
  for (const key of ["time_from", "time_to"]) {
    const value = draft[key];
    if (!value) continue;
    // datetime-local has no timezone; the visible form explicitly uses Taipei.
    if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) throw new Error("查詢日期格式無法驗證");
    const timestamp = `${value}+08:00`;
    if (Number.isNaN(Date.parse(timestamp))) throw new Error("查詢日期格式無法驗證");
    filters[key] = timestamp;
  }
  if (filters.time_from && filters.time_to && Date.parse(filters.time_to) < Date.parse(filters.time_from)) {
    throw new Error("結束時間不能早於起始時間");
  }
  return filters;
}

function matches(event, filters) {
  if (filters.q && !`${event.canonical_title} ${event.event_type || ""}`.toLowerCase().includes(filters.q.toLowerCase())) return false;
  if (filters.road && !event.road?.includes(filters.road)) return false;
  if (filters.district && ![event.district_id, ...(event.location_ids || []), ...(event.location_candidates || [])].includes(filters.district)) return false;
  if (filters.category && event.event_type !== filters.category) return false;
  if (filters.agency && !(event.agency_ids || []).includes(filters.agency)) return false;
  const start = event.start_at ? Date.parse(event.start_at) : null;
  const end = event.end_at ? Date.parse(event.end_at) : null;
  if ((filters.time_from || filters.time_to) && start === null && end === null) return false;
  if (filters.time_from && end !== null && end < Date.parse(filters.time_from)) return false;
  if (filters.time_to && start !== null && start > Date.parse(filters.time_to)) return false;
  return true;
}

export async function queryReplay(bundle, filters, { snapshot: snapshotId = bundle.default_snapshot, cursor = null, limit = 20 } = {}) {
  const snapshot = bundle.snapshots[snapshotId];
  if (!snapshot) throw new Error("保存快照不存在");
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error("查詢筆數上限無效");
  const queryId = await sha256Json({ bundle: bundle.bundle_sha256, snapshot: snapshotId, filters });
  let offset = 0;
  if (cursor) {
    try {
      const token = JSON.parse(atob(cursor));
      if (token.query !== queryId || !Number.isInteger(token.offset) || token.offset < 0) throw new Error();
      offset = token.offset;
    } catch { throw new Error("分頁條件或世代已改變，請重新查詢"); }
  }
  const rows = snapshot.events.filter((event) => matches(event, filters))
    .sort((a, b) => (Date.parse(b.start_at) || 0) - (Date.parse(a.start_at) || 0) || a.public_event_id.localeCompare(b.public_event_id));
  if (offset > rows.length) throw new Error("分頁位置超出結果");
  const events = rows.slice(offset, offset + limit);
  const hasMore = offset + events.length < rows.length;
  const nextCursor = hasMore ? btoa(JSON.stringify({ query: queryId, offset: offset + events.length })) : null;
  return {
    mode: bundle.mode, namespace: bundle.namespace, events, results: events,
    submitted_filters: { ...filters }, query_id: queryId, snapshot_id: snapshotId,
    query_generation_id: snapshot.generation_id, publication_hash: bundle.bundle_sha256,
    data_as_of: snapshot.as_of, queried_at: new Date().toISOString(),
    total_matches: rows.length, result_count: events.length, offset,
    truncated: hasMore, has_more: hasMore, next_cursor: nextCursor,
    freshness: "SAVED_SNAPSHOT", status: snapshot.status, source_gaps: snapshot.source_gaps,
    // This is a finite saved scenario; even complete fixture coverage cannot establish reality.
    answerable_no_match: false,
  };
}

export async function requestGateway(endpoint, tool, args, fetchImpl = fetch) {
  if (!endpoint) throw Object.assign(new Error("未設定正式查詢服務"), { code: "CAPABILITY_NOT_AVAILABLE" });
  const response = await fetchImpl(endpoint, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool, arguments: args }), signal: AbortSignal.timeout(12000),
  });
  const payload = await response.json();
  if (!response.ok || payload.error) throw Object.assign(new Error(payload.error?.message || `HTTP ${response.status}`), {
    code: payload.error?.code || "FAILED",
  });
  return payload;
}

export function trackingFilters(filters) {
  const { q, ...other } = filters;
  return { ...other, ...(q ? { keywords: [q] } : {}), ...((filters.time_from || filters.time_to) ? { time_semantics: "event_overlap" } : {}) };
}

export function replayTrackingItems(snapshot) {
  return snapshot.events.map((event) => ({
    ...event, event_id: event.public_event_id, identity: event.public_event_id,
    headline: event.canonical_title, what_changed: event.canonical_title,
    source_id: event.independent_source_ids[0], source_version: event.source_version,
    source_sha256: event.content_sha256, effective_from: event.start_at, effective_to: event.end_at,
    published_at: event.published_at, acquired_at: event.acquired_at,
    road: event.road, daily: event.daily, namespace: "demo:commute",
  }));
}
