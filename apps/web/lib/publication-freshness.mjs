/** Snapshot-age policy, not an upstream-data freshness or live-service guarantee. */
export const MAX_SNAPSHOT_AGE_MS = 16 * 60 * 60 * 1000;
export const REQUIRED_PUBLICATION_SOURCE_IDS = Object.freeze(["S-004", "S-006", "S-007", "S-009", "S-029"]);

function time(value) {
  if (typeof value !== "string" || !/(Z|[+-]\d{2}:\d{2})$/i.test(value)) return NaN;
  return Date.parse(value);
}

function hasExactRequiredCoverage(sources) {
  if (!Array.isArray(sources) || sources.length !== REQUIRED_PUBLICATION_SOURCE_IDS.length) return false;
  if (sources.some(s => !s || typeof s !== "object" || typeof s.source_id !== "string" || !s.source_id)) return false;
  const actual = new Set(sources.map(s => s.source_id));
  if (actual.size !== sources.length) return false;
  return REQUIRED_PUBLICATION_SOURCE_IDS.every(sourceId => actual.has(sourceId));
}

export function assessPublication(publication, sourceStatus, nowMs, maxAgeMs = MAX_SNAPSHOT_AGE_MS) {
  const result = (state, reason, ageMs = null) => ({ state, reason, ageMs, canReassure: state === "RECENT" });
  if (!Number.isFinite(nowMs) || !Number.isFinite(maxAgeMs) || maxAgeMs <= 0) {
    return result("UNKNOWN", "無法確認時鐘或時效設定。");
  }
  if (!publication || !sourceStatus) return result("UNKNOWN", "來源狀態尚未取得，不能判定目前是否有新消息。");
  const run = publication.source_collection_run_id;
  if (!run || run !== sourceStatus.latest_collection_run?.collection_run_id ||
      publication.source_status_generated_at !== sourceStatus.generated_at) {
    return result("UNKNOWN", "摘要與來源狀態版本不一致，請重新載入並核對。");
  }
  const sources = sourceStatus.sources;
  if (!hasExactRequiredCoverage(sources)) return result("UNKNOWN", "缺少必要來源覆蓋資料。");
  const timestamps = [publication.generated_at, sourceStatus.generated_at, ...sources.map(s => s.last_checked_at)].map(time);
  if (timestamps.some(t => !Number.isFinite(t) || t > nowMs)) {
    return result("UNKNOWN", "資料時間缺失、無時區或晚於裝置時間，無法判定時效。");
  }
  const ageMs = nowMs - Math.min(...timestamps);
  if (ageMs > maxAgeMs) return result("STALE", "這份快照已超過核對期限，不能推論現在沒有新消息。", ageMs);
  if (publication.publication_status !== "READY" || publication.snapshot_complete !== true ||
      sourceStatus.latest_collection_run?.status !== "SUCCEEDED" ||
      sources.some(s => s.source_health !== "PASS" ||
        !["COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"].includes(s.window_completeness) ||
        !["NEW_ITEMS", "NO_NEW_ITEM"].includes(s.result))) {
    return result("PARTIAL", "這次監測有缺口或未完整完成；保留已取得資料，但不能排除其他異動。", ageMs);
  }
  return result("RECENT", "僅代表此快照的監測範圍；不保證各官方資料即時，亦非成功部署憑據。", ageMs);
}
