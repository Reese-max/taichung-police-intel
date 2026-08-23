const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000;

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
