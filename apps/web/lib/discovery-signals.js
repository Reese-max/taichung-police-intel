const VALID_OPERATING_STATES = new Set(["ACTIVE", "PAUSED", "RESTORING", "DEGRADED"]);
const VALID_SIGNAL_MODES = new Set(["CURRENT", "HISTORICAL_REPLAY_ONLY", "CANARY_ONLY", "GAP_VISIBLE"]);
const VALID_PENDING_STATUSES = new Set(["DISCOVERY_UNVERIFIED", "OFFICIAL_CANDIDATE", "NO_OFFICIAL_MATCH", "CONFLICT"]);
const VALID_AUTHORITIES = new Set(["media", "official"]);
const VALID_PAYLOAD_STATUSES = new Set(["FIXTURE_ONLY", "LIVE"]);
const DISCOVERY_ID = /^gd-[0-9a-f]{16}$/;
const COUNT_KEYS = [
  "feed_item_count",
  "relevant_count",
  "confirmed_count",
  "pending_total",
  "pending_shown",
  "expired_count",
  "conflict_count",
  "no_official_match_count",
  "new_public_event_count",
  "existing_event_match_count",
  "canonical_change_count",
];

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isHttpsUrl(value) {
  return typeof value === "string" && value.startsWith("https://");
}

function isValidConfirmedRow(row) {
  return isObject(row)
    && typeof row.candidate_id === "string" && DISCOVERY_ID.test(row.candidate_id)
    && typeof row.headline === "string" && row.headline.length > 0
    && row.verification_status === "VERIFIED_OFFICIAL"
    && VALID_AUTHORITIES.has(row.authority)
    && Array.isArray(row.official_document_versions)
    && row.official_document_versions.length > 0
    && row.official_document_versions.every((version) => typeof version === "string" && version.length > 0)
    && Array.isArray(row.official_urls) && row.official_urls.every(isHttpsUrl)
    && Array.isArray(row.public_event_ids)
    && row.canonical_write === false;
}

function isValidPendingRow(row) {
  return isObject(row)
    && typeof row.candidate_id === "string" && DISCOVERY_ID.test(row.candidate_id)
    && typeof row.headline === "string" && row.headline.length > 0
    && VALID_PENDING_STATUSES.has(row.verification_status)
    && VALID_AUTHORITIES.has(row.authority)
    && typeof row.note === "string" && row.note.length > 0
    && typeof row.discovered_at === "string"
    && row.canonical_write === false;
}

export function isValidDiscoverySignals(data) {
  if (!isObject(data) || data.schema_version !== 1 || data.kind !== "GOVINTEL_DISCOVERY_SIGNALS") return false;
  if (!VALID_PAYLOAD_STATUSES.has(data.status)) return false;
  if (typeof data.production_verified !== "boolean") return false;
  if (data.status === "FIXTURE_ONLY" && data.production_verified !== false) return false;
  if (typeof data.generated_at !== "string") return false;
  const upstream = data.upstream;
  if (!isObject(upstream)
    || typeof upstream.feed_id !== "string"
    || typeof upstream.generation_id !== "string"
    || typeof upstream.generated_at !== "string"
    || !VALID_OPERATING_STATES.has(upstream.operating_state)
    || typeof upstream.current !== "boolean"
    || !VALID_SIGNAL_MODES.has(upstream.signal_mode)) return false;
  // A non-current upstream may only ever render as replay/canary/gap — never as a live signal.
  if (data.status === "LIVE" && upstream.current !== true) return false;
  if (!Array.isArray(data.confirmed) || !Array.isArray(data.pending)) return false;
  if (!isObject(data.limits) || !Number.isInteger(data.limits.pending_max) || data.limits.pending_max < 1) return false;
  if (data.pending.length > data.limits.pending_max) return false;
  if (!data.confirmed.every(isValidConfirmedRow)) return false;
  if (!data.pending.every(isValidPendingRow)) return false;
  const counts = data.counts;
  if (!isObject(counts) || typeof counts.truncated !== "boolean") return false;
  for (const key of COUNT_KEYS) {
    if (!Number.isInteger(counts[key]) || counts[key] < 0) return false;
  }
  if (counts.confirmed_count !== data.confirmed.length) return false;
  if (counts.pending_shown !== data.pending.length || counts.pending_total < counts.pending_shown) return false;
  return true;
}
