/**
 * View projection for a bounded live-meeting session (schema_version 1 emitted
 * by intel_v2/live_meeting.py). This layer is presentation-only: it keeps every
 * provisional segment visibly labelled, renders gap intervals instead of empty
 * ranges, and degrades locators to plain time text unless the session carries a
 * verified STREAM_TIME seek contract. It never mints formal evidence.
 */

export const PROVISIONAL_BADGES = Object.freeze(["LIVE", "AI_TRANSCRIPT", "UNCHECKED"]);
export const PROVISIONAL_EVIDENCE_STATE = "LIVE_ASR_PROVISIONAL";
const GAP_BADGE = "GAP";

// Mirrors intel_v2/live_meeting.py ALLOWED_SOURCE_HOSTS: a STREAM_TIME seek
// base URL is only trusted when its host is allowlisted for the session's
// source_id. Python remains the authoritative gate; this is the display-side
// defense-in-depth so a malformed session JSON can never mint a deep link.
const OFFICIAL_SEEK_HOSTS = Object.freeze({
  "S-004": ["www.tccc.gov.tw", "tccc.gov.tw"],
  "S-006": ["www.tccc.gov.tw", "tccc.gov.tw"],
  "S-007": ["yishi.tccc.gov.tw"],
  "S-010": ["www.tccc.gov.tw", "tccc.gov.tw", "vod.tccc.gov.tw", "streamak0128.akamaized.net"],
  "S-011": ["www.tccc.gov.tw", "tccc.gov.tw", "vod.tccc.gov.tw", "streamak0128.akamaized.net"],
});

function isTrustedSeekBase(session, baseUrl) {
  if (typeof baseUrl !== "string" || !baseUrl.startsWith("https://")) {
    return false;
  }
  try {
    const parsed = new URL(baseUrl);
    if (parsed.username || parsed.password || (parsed.port && parsed.port !== "443")) return false;
    const host = parsed.hostname.toLowerCase();
    const allowed = OFFICIAL_SEEK_HOSTS[session?.source_id] || [];
    return allowed.includes(host);
  } catch {
    return false;
  }
}

export function formatStreamClock(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const pad = (value) => String(value).padStart(2, "0");
  return `stream+${pad(hours)}:${pad(minutes)}:${pad(secs)}`;
}

function findSegment(session, segmentId) {
  const segments = Array.isArray(session?.segments) ? session.segments : [];
  const segment = segments.find((item) => item.segment_id === segmentId);
  if (!segment) {
    throw new Error(`unknown segment: ${segmentId}`);
  }
  return segment;
}

/**
 * Resolve a provisional segment to a stream-time locator. Only a session-level
 * `seek` contract with kind STREAM_TIME and an allowlisted HTTPS base URL makes
 * the locator actionable; anything else degrades to time text and never
 * fabricates a deep link.
 */
export function projectLocator(session, segmentId) {
  const segment = typeof segmentId === "object" && segmentId !== null
    ? segmentId
    : findSegment(session, segmentId);
  const seek = session?.seek;
  if (seek?.kind === "STREAM_TIME" && isTrustedSeekBase(session, seek.base_url)
      && Number.isFinite(seek.offset_seconds) && seek.offset_seconds >= 0) {
    const absolute = segment.start_seconds + (Number(seek.offset_seconds) || 0);
    return {
      kind: "STREAM_TIME",
      url: seek.base_url,
      time_seconds: absolute,
      display: formatStreamClock(absolute),
      reliable_seek: true,
      provisional: true,
      evidence_state: PROVISIONAL_EVIDENCE_STATE,
    };
  }
  return {
    kind: "TIME_TEXT",
    url: null,
    time_seconds: segment.start_seconds,
    display: formatStreamClock(segment.start_seconds),
    reliable_seek: false,
    provisional: true,
    evidence_state: PROVISIONAL_EVIDENCE_STATE,
  };
}

/**
 * Merge segments and gap intervals into one sorted timeline. Every entry is
 * visible (display_empty stays false) and every entry is provisional.
 */
export function projectTimeline(session) {
  const entries = [];
  for (const segment of session?.segments || []) {
    entries.push({
      kind: "SEGMENT",
      segment_id: segment.segment_id,
      sequence: segment.sequence,
      start_seconds: segment.start_seconds,
      end_seconds: segment.end_seconds,
      text: segment.text,
      finalized: segment.finalized === true,
      partial: segment.partial === true,
      revision: segment.revision,
      speaker_label: segment.speaker_label ?? null,
      profile_relevance: segment.profile_relevance ?? null,
      revision_history: segment.revision_history ?? [],
      badges: [...PROVISIONAL_BADGES],
      provisional: true,
      evidence_state: PROVISIONAL_EVIDENCE_STATE,
      display_empty: false,
      locator: projectLocator(session, segment),
    });
  }
  for (const gap of session?.gap_intervals || []) {
    entries.push({
      kind: "GAP",
      gap_id: gap.gap_id,
      reason: gap.reason,
      start_seconds: gap.start_seconds,
      end_seconds: gap.end_seconds,
      detected_at: gap.detected_at,
      badges: [GAP_BADGE],
      provisional: true,
      evidence_state: PROVISIONAL_EVIDENCE_STATE,
      display_empty: false,
    });
  }
  entries.sort((a, b) => a.start_seconds - b.start_seconds || a.end_seconds - b.end_seconds || a.kind.localeCompare(b.kind));
  return entries;
}

/**
 * Bounded case-insensitive search over provisional transcript text. Matches
 * are navigation hits only — each stays provisional with its own locator.
 */
export function projectSearchResults(session, query, { limit = 50 } = {}) {
  if (typeof query !== "string" || !query.trim()) {
    throw new Error("query must be a non-empty string");
  }
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) {
    throw new Error("limit must be an integer within 1..200");
  }
  const needle = query.trim().toLowerCase();
  const matched = (session?.segments || []).filter((segment) =>
    String(segment.text || "").toLowerCase().includes(needle));
  const matches = matched.slice(0, limit).map((segment) => ({
    segment_id: segment.segment_id,
    sequence: segment.sequence,
    start_seconds: segment.start_seconds,
    end_seconds: segment.end_seconds,
    text: segment.text,
    revision: segment.revision,
    finalized: segment.finalized === true,
    partial: segment.partial === true,
    provisional: true,
    evidence_state: PROVISIONAL_EVIDENCE_STATE,
    locator: projectLocator(session, segment),
  }));
  return {
    query: query.trim(),
    provisional: true,
    evidence_state: PROVISIONAL_EVIDENCE_STATE,
    match_count: matches.length,
    truncated: matched.length > limit,
    matches,
  };
}

/**
 * Receipt summary rows for review UI: every reconciliation receipt stays
 * visible with its stale/superseded markers; only the current receipt for a
 * candidate is marked current.
 */
export function projectReceiptSummary(session) {
  const currentIds = new Set(Object.values(session?.current_reconciliations || {}));
  return (session?.reconciliation_receipts || []).map((receipt) => ({
    receipt_id: receipt.receipt_id,
    candidate_id: receipt.candidate_id,
    segment_ids: receipt.segment_ids,
    outcome: receipt.outcome,
    provisional_text_sha256: receipt.provisional_text_sha256,
    official: receipt.official ?? null,
    reconciled_at: receipt.reconciled_at,
    stale: receipt.stale === true,
    stale_reason: receipt.stale_reason ?? null,
    superseded_by: receipt.superseded_by ?? null,
    current: currentIds.has(receipt.receipt_id),
    verification_status:
      session?.provider_usage?.mode === "FIXTURE_ONLY"
        ? "FIXTURE_ONLY"
        : receipt.stale === true
        ? "STALE_NEEDS_REVIEW"
        : receipt.outcome === "CONFIRMED_BY_OFFICIAL_MEDIA" || receipt.outcome === "CONFIRMED_BY_MINUTES"
          ? "OFFICIAL_RECONCILED"
          : "PROVISIONAL_UNRESOLVED",
  }));
}

/** Local files are navigation input, never a formal publication writer. */
export function validateLiveViewSession(session) {
  if (session?.schema_version !== 1 || typeof session.session_id !== "string"
      || !Number.isInteger(session.session_revision) || session.session_revision < 1
      || !Number.isInteger(session.max_duration_seconds) || session.max_duration_seconds < 1 || session.max_duration_seconds > 14400
      || (session.budget_seconds != null && (!Number.isInteger(session.budget_seconds)
        || session.budget_seconds < 1 || session.budget_seconds > session.max_duration_seconds))
      || !["PREPARING", "LIVE", "DEGRADED", "ENDED", "RECONCILING", "RECONCILED", "FAILED"].includes(session.status)
      || !OFFICIAL_SEEK_HOSTS[session.source_id]
      || !isTrustedSeekBase(session, session.official_source_url)
      || !isTrustedSeekBase(session, session.stream_url)) {
    throw new Error("Invalid session schema or official source identity");
  }
  if (!Array.isArray(session.segments) || !Array.isArray(session.gap_intervals)
      || !Array.isArray(session.reconciliation_receipts) || !Array.isArray(session.bookmarks)
      || typeof session.asr_contract?.provider !== "string"
      || typeof session.asr_contract?.model !== "string") throw new Error("Invalid session arrays or ASR contract");
  const ids = new Set();
  for (const segment of session.segments) {
    if (typeof segment.segment_id !== "string" || ids.has(segment.segment_id)
        || typeof segment.text !== "string" || segment.text.length > 20000
        || !Number.isInteger(segment.revision) || !Array.isArray(segment.revision_history)
        || segment.revision_history.some((prior) => !prior || typeof prior.text !== "string" || !Number.isInteger(prior.revision)
          || (prior.speaker_label != null && typeof prior.speaker_label !== "string"))) throw new Error("Invalid segment identity or revision");
    const profile = segment.profile_relevance;
    if (profile != null && (!Array.isArray(profile.matched_terms) || profile.matched_terms.some((term) => typeof term !== "string"))) throw new Error("Invalid profile metadata");
    ids.add(segment.segment_id);
  }
  if (session.gap_intervals.some((gap) => !gap || typeof gap.reason !== "string" || typeof gap.gap_id !== "string")) throw new Error("Invalid gap metadata");
  if (session.reconciliation_receipts.some((receipt) => !receipt || typeof receipt.receipt_id !== "string"
      || typeof receipt.outcome !== "string" || (receipt.official != null && typeof receipt.official.locator !== "string"))) throw new Error("Invalid receipt metadata");
  const usage = session.provider_usage;
  if (usage != null && (!Array.isArray(usage.calls) || !Number.isFinite(usage.reserved_seconds)
      || !["FIXTURE_ONLY", "GROQ_RECORDED_AUDIO"].includes(usage.mode))) throw new Error("Invalid provider usage");
  const ranges = [...session.segments, ...session.gap_intervals].sort((a, b) => a.start_seconds - b.start_seconds);
  let previous = 0;
  for (const row of ranges) {
    if (!Number.isFinite(row.start_seconds) || !Number.isFinite(row.end_seconds) || row.start_seconds < 0
        || row.end_seconds <= row.start_seconds || row.end_seconds > session.max_duration_seconds) throw new Error("Invalid timeline range");
    if (row.start_seconds < previous) throw new Error("Timeline overlap");
    previous = row.end_seconds;
  }
  return session;
}

export function createLiveWatch(session, segmentId) {
  validateLiveViewSession(session);
  const segment = findSegment(session, segmentId);
  return {
    session_id: session.session_id, segment_ids: [segment.segment_id],
    start_seconds: segment.start_seconds, end_seconds: segment.end_seconds,
    status: "WATCH", reason_code: "USER_WATCH", provisional: true,
    text: segment.text, revision: segment.revision, content_sha256: segment.content_sha256,
    stream_content_hash: session.stream_content_hash, session_revision: session.session_revision,
    profile_relevance: segment.profile_relevance ?? null,
  };
}
