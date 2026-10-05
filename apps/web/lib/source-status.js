const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000;
const STALE_STATUSES = new Set(["STALE", "VERY_STALE"]);
const SOURCE_HEALTH = new Set(["PASS", "DEGRADED", "FAILED", "QUARANTINED", "NOT_RUN"]);
const SOURCE_RESULT = new Set(["NEW_ITEMS", "NO_NEW_ITEM", "PARTIAL", "FAILED", "NOT_RUN"]);
const WINDOW_COMPLETENESS = new Set(["COMPLETE_WITH_ITEMS", "COMPLETE_ZERO", "PARTIAL", "NOT_RUN"]);
const FRESHNESS = new Set(["FRESH", "STALE", "VERY_STALE", "NO_DATA"]);

function validIso(value) {
  if (typeof value !== "string") return false;
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|([+-])(\d{2}):(\d{2}))$/.exec(value);
  if (!match || Number(match[2]) > 23 || Number(match[3]) > 59 || Number(match[4]) > 59
      || (match[5] && (Number(match[6]) > 23 || Number(match[7]) > 59))) return false;
  const calendar = Date.parse(`${match[1]}T00:00:00Z`);
  return Number.isFinite(calendar) && new Date(calendar).toISOString().slice(0, 10) === match[1]
    && Number.isFinite(Date.parse(value));
}

function validOptionalString(value) {
  return value === undefined || value === null || typeof value === "string";
}

export function lastKnownGoodAge(lastKnownGood, observedNow) {
  const result = { status: "UNKNOWN", age_hours: null, observed_at: null, completed_at: null, reason: null };
  const observed = observedNow instanceof Date ? observedNow.getTime()
    : typeof observedNow === "number" ? observedNow : NaN;
  const observedDate = new Date(observed);
  if (!Number.isSafeInteger(observed) || !Number.isFinite(observedDate.getTime())
      || observedDate.getUTCFullYear() < 1 || observedDate.getUTCFullYear() > 9999) {
    return { ...result, reason: "INVALID_OBSERVED_AT" };
  }
  result.observed_at = observedDate.toISOString();
  if (!lastKnownGood || typeof lastKnownGood !== "object" || Array.isArray(lastKnownGood)) {
    return { ...result, reason: "NO_LAST_KNOWN_GOOD" };
  }
  const completed = lastKnownGood.completed_at;
  if (completed === undefined || completed === null || completed === "") {
    return { ...result, reason: "MISSING_COMPLETED_AT" };
  }
  if (!validIso(completed) || completed.startsWith("0000-")) return { ...result, reason: "INVALID_COMPLETED_AT" };
  const completedDate = new Date(completed);
  if (completedDate.getUTCFullYear() < 1 || completedDate.getUTCFullYear() > 9999) {
    return { ...result, reason: "INVALID_COMPLETED_AT" };
  }
  result.completed_at = completed;
  // Date.parse retains only milliseconds. Python completion clocks can carry
  // six fractional digits, so compare their remaining microseconds as well.
  const fraction = /\.(\d{1,6})(?:Z|[+-]\d{2}:\d{2})$/.exec(completed)?.[1] || "";
  const submillisecond = BigInt(fraction.padEnd(6, "0").slice(3));
  const completedMicroseconds = BigInt(Date.parse(completed)) * 1000n + submillisecond;
  const elapsedMicroseconds = BigInt(observed) * 1000n - completedMicroseconds;
  if (elapsedMicroseconds < 0n) return { ...result, reason: "FUTURE_COMPLETED_AT" };
  const ageHours = Number(elapsedMicroseconds) / 3_600_000_000;
  return { ...result, status: "KNOWN", age_hours: ageHours };
}

export function lastKnownGoodAgeLabel(lastKnownGood, observedNow) {
  const age = lastKnownGoodAge(lastKnownGood, observedNow);
  if (age.status !== "KNOWN") {
    if (age.reason === "FUTURE_COMPLETED_AT") return "未知（快照完成時間晚於本次檢視時間）";
    return "未知（尚無有效的成功快照完成時間或檢視時間）";
  }
  if (age.age_hours > 0 && age.age_hours < 0.1) return "不到 0.1 小時";
  return `${Number(age.age_hours.toFixed(1))} 小時`;
}

export function validateSourceStatus(data, policy) {
  if (!data || data.schema_version !== 1 || !Array.isArray(data.sources) || data.sources.length === 0
      || data.mode !== "COMPETITION_DEMO"
      || !validIso(data.generated_at)) throw new Error("來源狀態快照格式無法驗證");
  const expected = policy?.active_source_ids;
  if (![1, 2].includes(policy?.schema_version) || !Array.isArray(expected) || !expected.length
      || expected.some((id) => typeof id !== "string" || !id.trim() || id !== id.trim())
      || new Set(expected).size !== expected.length) throw new Error("來源狀態來源政策無法驗證");
  if (policy.schema_version === 2 && (!/^[a-f0-9]{64}$/.test(policy.governance_binding?.governance_hash || "")
      || !Array.isArray(policy.active_sources) || JSON.stringify(policy.active_sources.map(row => row.source_id)) !== JSON.stringify(expected))) {
    throw new Error("來源狀態治理政策無法驗證");
  }
  if (policy.schema_version === 2) {
    const binding = {policy_version: policy.policy_version, policy_hash: policy.policy_hash,
      catalog_hash: policy.catalog_hash, active_source_ids: [...expected].sort(),
      governance_hash: policy.governance_binding.governance_hash,
      retention_policy_hash: policy.governance_binding.retention_policy.policy_hash};
    const actual = data.source_policy;
    if (!actual || Object.keys(actual).length !== Object.keys(binding).length
        || Object.entries(binding).some(([key, value]) => JSON.stringify(actual[key]) !== JSON.stringify(value))) {
      throw new Error("來源狀態治理世代無法驗證");
    }
  }
  const ids = new Set();
  for (const source of data.sources) {
    if (!source || typeof source.source_id !== "string" || !source.source_id.trim() || ids.has(source.source_id)) {
      throw new Error(`來源狀態 source_id 無法驗證：${source?.source_id || "unknown"}`);
    }
    if (typeof source.source_name !== "string" || !source.source_name.trim()
        || !validOptionalString(source.source_url)
        || !SOURCE_HEALTH.has(source.source_health)
        || !SOURCE_RESULT.has(source.result)
        || !WINDOW_COMPLETENESS.has(source.window_completeness)
        || !FRESHNESS.has(source.freshness_status)
        || !validOptionalString(source.data_as_of)
        || (source.data_as_of !== undefined && source.data_as_of !== null && !validIso(source.data_as_of))
        || (source.intelligence_gaps !== undefined && (!Array.isArray(source.intelligence_gaps)
          || source.intelligence_gaps.some((gap) => typeof gap !== "string")))) {
      throw new Error(`來源狀態資料無法驗證：${source.source_id}`);
    }
    for (const field of ["last_checked_at", "last_success_at", "next_update_at"]) {
      if (source[field] !== undefined && source[field] !== null && !validIso(source[field])) {
        throw new Error(`來源狀態時間無法驗證：${source.source_id}`);
      }
    }
    ids.add(source.source_id);
  }
  if (ids.size !== expected.length || expected.some((id) => !ids.has(id))) throw new Error("來源狀態未涵蓋完整來源政策");
  if (data.next_update_at !== undefined && !validIso(data.next_update_at)) throw new Error("來源狀態下一次更新時間無法驗證");
  if (!data.latest_collection_run
      || typeof data.latest_collection_run.collection_run_id !== "string"
      || !data.latest_collection_run.collection_run_id.trim()) throw new Error("來源狀態收集世代無法驗證");
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
  const lkgCompleted = row.lkg_completed_at instanceof Date
    ? Number.isFinite(row.lkg_completed_at.getTime()) ? row.lkg_completed_at.toISOString() : null
    : validIso(row.lkg_completed_at) && !row.lkg_completed_at.startsWith("0000-") ? row.lkg_completed_at : null;
  const lastKnownGood = row.lkg_source_run_id ? {
    source_run_id: row.lkg_source_run_id,
    completed_at: lkgCompleted,
    manifest_sha256: row.lkg_manifest_sha256,
  } : null;
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
    last_success_at: iso(lkgCompleted),
    next_update_at: nextUpdateAt(now),
    last_known_good: lastKnownGood,
    last_known_good_age: lastKnownGoodAge(lastKnownGood, now),
    intelligence_gaps: gaps,
  };
}
