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
    const host = new URL(baseUrl).hostname.toLowerCase();
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
  if (seek?.kind === "STREAM_TIME" && isTrustedSeekBase(session, seek.base_url)) {
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
      receipt.stale === true
        ? "STALE_NEEDS_REVIEW"
        : receipt.outcome === "CONFIRMED_BY_OFFICIAL_MEDIA" || receipt.outcome === "CONFIRMED_BY_MINUTES"
          ? "OFFICIAL_RECONCILED"
          : "PROVISIONAL_UNRESOLVED",
  }));
}
