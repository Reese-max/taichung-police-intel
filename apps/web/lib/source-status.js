const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000;
const STALE_STATUSES = new Set(["STALE", "VERY_STALE"]);

function validIso(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

function validOptionalString(value) {
  return value === undefined || value === null || typeof value === "string";
}

export function validateSourceStatus(data) {
  if (!data || data.schema_version !== 1 || !Array.isArray(data.sources) || data.sources.length === 0
      || !validIso(data.generated_at)) throw new Error("來源狀態快照格式無法驗證");
  const ids = new Set();
  for (const source of data.sources) {
    if (!source || typeof source.source_id !== "string" || !source.source_id.trim() || ids.has(source.source_id)) {
      throw new Error(`來源狀態 source_id 無法驗證：${source?.source_id || "unknown"}`);
    }
    if (typeof source.source_name !== "string" || !source.source_name.trim()
        || !validOptionalString(source.source_url)
        || !validOptionalString(source.source_health)
        || !validOptionalString(source.result)
        || !validOptionalString(source.window_completeness)
        || !validOptionalString(source.freshness_status)
        || !validOptionalString(source.data_as_of)
        || (source.data_as_of !== undefined && source.data_as_of !== null && !validIso(source.data_as_of))
        || (source.intelligence_gaps !== undefined && (!Array.isArray(source.intelligence_gaps)
          || source.intelligence_gaps.some((gap) => typeof gap !== "string")))) {
      throw new Error(`來源狀態資料無法驗證：${source.source_id}`);
    }
    ids.add(source.source_id);
  }
  if (data.next_update_at !== undefined && !validIso(data.next_update_at)) throw new Error("來源狀態下一次更新時間無法驗證");
  if (data.latest_collection_run !== undefined && (!data.latest_collection_run
      || typeof data.latest_collection_run.collection_run_id !== "string"
      || !data.latest_collection_run.collection_run_id.trim())) throw new Error("來源狀態收集世代無法驗證");
  return data;
}

export function isHealthyStaleSource(source) {
  return source?.source_health === "PASS"
    && source?.result === "NO_NEW_ITEM"
    && STALE_STATUSES.has(source?.freshness_status);
}

function iso(value) {
  return value ? new Date(value).toISOString() : null;
}

export function freshnessStatus(dataAsOf, now = new Date()) {
  if (!dataAsOf) return "NO_DATA";
  const ageHours = (new Date(now).getTime() - new Date(dataAsOf).getTime()) / 3_600_000;
  if (ageHours <= 13) return "FRESH";
  if (ageHours <= 24) return "STALE";
  return "VERY_STALE";
}

export function nextUpdateAt(now = new Date()) {
  const current = new Date(now);
  const taipei = new Date(current.getTime() + TAIPEI_OFFSET_MS);
  const parts = [taipei.getUTCFullYear(), taipei.getUTCMonth(), taipei.getUTCDate()];
  for (const [hour, minute] of [[6, 30], [18, 30]]) {
    const candidate = Date.UTC(...parts, hour - 8, minute);
    if (candidate > current.getTime()) return new Date(candidate + TAIPEI_OFFSET_MS).toISOString().replace("Z", "+08:00");
  }
  const candidate = Date.UTC(parts[0], parts[1], parts[2] + 1, -2, 30);
  return new Date(candidate + TAIPEI_OFFSET_MS).toISOString().replace("Z", "+08:00");
}

export function buildSourceStatus(row, now = new Date()) {
  const dataAsOf = iso(row.data_as_of);
  const freshness = freshnessStatus(dataAsOf, now);
  const gaps = [];
  if (!row.source_run_id) gaps.push("NO_COLLECTION_RUN");
  if (["FAILED", "QUARANTINED"].includes(row.source_health)) gaps.push("SOURCE_FAILED");
  if (row.window_completeness === "PARTIAL") gaps.push("WINDOW_PARTIAL");
  if (!row.lkg_source_run_id) gaps.push("NO_LAST_KNOWN_GOOD");
  if (["STALE", "VERY_STALE"].includes(freshness)) gaps.push(`${freshness}_DATA`);
  if (freshness === "NO_DATA") gaps.push("NO_DATA_AS_OF");

  return {
    source_id: row.source_id,
    source_name: row.name,
    source_url: row.source_url || null,
    current_source_run_id: row.source_run_id || null,
    source_health: row.source_health || "NOT_RUN",
    window_completeness: row.window_completeness || "NOT_RUN",
    result: row.result || "NOT_RUN",
    freshness_status: freshness,
    data_as_of: dataAsOf,
    last_checked_at: iso(row.completed_at),
    last_success_at: iso(row.lkg_completed_at),
    next_update_at: nextUpdateAt(now),
    last_known_good: row.lkg_source_run_id ? {
      source_run_id: row.lkg_source_run_id,
      completed_at: iso(row.lkg_completed_at),
      manifest_sha256: row.lkg_manifest_sha256,
    } : null,
    intelligence_gaps: gaps,
  };
}
