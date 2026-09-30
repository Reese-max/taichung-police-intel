// apps/web/lib/live-session.js
// LiveMeetingSession: explicit, bounded, opt-in live-meeting intelligence
// sessions over allowlisted official public streams, plus post-event
// ReconciliationReceipt mapping provisional ASR candidates back to official
// VOD/minutes/agenda locators.
//
// Trust contract: every live segment is PROVISIONAL / LIVE_ASR_PROVISIONAL
// derived navigation text. It can never enter the formal AUTO_PASS publication
// path; only reconciliation entries confirmed against official post-event
// sources produce formal evidence. See .kiro/steering/evidence-and-safety.md.

import { createHash } from "node:crypto";

import { formatTime } from "./evidence.js";

// ── Schema / enums ────────────────────────────────────────────────────────────
export const LIVE_SESSION_SCHEMA_VERSION = 1;

export const SESSION_STATUSES = Object.freeze([
  "PREPARING",
  "LIVE",
  "DEGRADED",
  "ENDED",
  "RECONCILING",
  "RECONCILED",
  "FAILED",
]);

export const GAP_REASONS = Object.freeze([
  "STREAM_DISCONNECT",
  "ASR_OUTAGE",
  "PROVIDER_TIMEOUT",
  "RATE_LIMIT",
  "BUDGET_STOP",
  "MAX_DURATION_REACHED",
  "SESSION_CRASH",
]);

export const REVISION_KINDS = Object.freeze(["INTERIM", "FINAL"]);

export const RECONCILIATION_STATUSES = Object.freeze([
  "CONFIRMED_BY_OFFICIAL_MEDIA",
  "CONFIRMED_BY_MINUTES",
  "SUPERSEDED_TRANSCRIPT",
  "UNRESOLVED",
  "SOURCE_NOT_YET_AVAILABLE",
  "DROPPED_FALSE_POSITIVE",
]);

export const POST_EVENT_SOURCE_KINDS = Object.freeze(["VOD", "MINUTES", "AGENDA"]);

export const MATCH_VERDICTS = Object.freeze(["CONFIRMED", "SUPERSEDED", "FALSE_POSITIVE"]);

export const BOOKMARK_REASONS = Object.freeze([
  "MANUAL_WATCH",
  "PROFILE_MATCH",
  "AGENDA_TERM",
  "FOLLOW_UP",
]);

// Live ASR output is derived navigation text only. It is deliberately a
// distinct label from GROQ_ASR (which describes post-hoc canary transcripts of
// already-published official media).
export const LIVE_PROVISIONAL_LABEL = "LIVE_ASR_PROVISIONAL";

export const SESSION_BADGES = Object.freeze(["LIVE", "AI_TRANSCRIPT", "UNCHECKED"]);

// Hard ceiling so no session can run unattended past one long meeting day.
export const MAX_SESSION_DURATION_SECONDS = 6 * 3600;

// Allowlisted official public live sources. Only declared official sources may
// ingest; stream hosts are matched exactly or as subdomains.
export const OFFICIAL_LIVE_SOURCES = Object.freeze({
  "S-010": Object.freeze({
    name: "Taichung City Council official video/live",
    allowed_hosts: Object.freeze(["tccc.gov.tw", "streamak0128.akamaized.net"]),
  }),
});

const TRANSITIONS = Object.freeze({
  PREPARING: Object.freeze(["LIVE", "FAILED"]),
  LIVE: Object.freeze(["DEGRADED", "ENDED", "FAILED"]),
  DEGRADED: Object.freeze(["LIVE", "ENDED", "FAILED"]),
  ENDED: Object.freeze(["RECONCILING"]),
  RECONCILING: Object.freeze(["RECONCILED", "FAILED"]),
  RECONCILED: Object.freeze(["RECONCILING"]),
  FAILED: Object.freeze([]),
});

// Fields the caller may never supply — the session layer owns them.
const RESERVED_SEGMENT_FIELDS = new Set([
  "verification_status",
  "evidence_state",
  "content_label",
  "evidence_type",
  "evidence_id",
]);
const SPEAKER_IDENTITY_FIELDS = new Set([
  "speaker_name",
  "speaker_identity",
  "verified_speaker",
  "official_name",
]);

// ── Hashing / time helpers ────────────────────────────────────────────────────
function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

export function canonicalSha256(value) {
  return createHash("sha256").update(stableJson(value), "utf8").digest("hex");
}

function isIso(value) {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function relativeSeconds(session, isoAt) {
  return (Date.parse(isoAt) - Date.parse(session.started_at)) / 1000;
}

function isoAt(session, seconds) {
  return new Date(Date.parse(session.started_at) + seconds * 1000).toISOString();
}

function fail(reason, extra = {}) {
  return { ok: false, reason, ...extra };
}

function hostAllowed(hostname, allowedHosts) {
  return allowedHosts.some((host) => hostname === host || hostname.endsWith(`.${host}`));
}

function transition(session, next) {
  if (!TRANSITIONS[session.status]?.includes(next)) {
    return fail("INVALID_STATUS_TRANSITION", { from: session.status, to: next });
  }
  session.status = next;
  return { ok: true };
}

function sessionProjection(session) {
  return {
    schema_version: session.schema_version,
    session_id: session.session_id,
    status: session.status,
    started_at: session.started_at,
    ended_at: session.ended_at,
    end_reason: session.end_reason,
    max_duration_seconds: session.max_duration_seconds,
    supersedes_session_id: session.supersedes_session_id,
    segments: session.segments.map((segment) => ({
      segment_id: segment.segment_id,
      sequence: segment.sequence,
      t_start_seconds: segment.t_start_seconds,
      t_end_seconds: segment.t_end_seconds,
      content_sha256: segment.content_sha256,
      revisions: segment.revisions.map((revision) => ({
        revision_no: revision.revision_no,
        kind: revision.kind,
        content_sha256: revision.content_sha256,
      })),
    })),
    gap_intervals: session.gap_intervals.map((gap) => ({
      gap_id: gap.gap_id,
      reason: gap.reason,
      started_at: gap.started_at,
      ended_at: gap.ended_at,
      open: gap.open,
    })),
    bookmarks: session.bookmarks.map((bookmark) => ({
      bookmark_id: bookmark.bookmark_id,
      segment_ids: bookmark.segment_ids,
      time_range: bookmark.time_range,
      segment_hashes: bookmark.segment_hashes,
      reason_code: bookmark.reason_code,
      profile_id: bookmark.profile_id,
      note: bookmark.note,
    })),
    receipts: session.receipts.map((receipt) => ({
      receipt_id: receipt.receipt_id,
      receipt_no: receipt.receipt_no,
      outcome: receipt.outcome,
      entries: receipt.entries.map((entry) => ({
        entry_id: entry.entry_id,
        bookmark_id: entry.bookmark_id,
        status: entry.status,
        provisional_locator: entry.provisional_locator,
        before_transcript_sha256: entry.before_transcript_sha256,
        after_transcript_sha256: entry.after_transcript_sha256,
        official_locator: entry.official_locator,
        official_url: entry.official_url,
        source_kind: entry.source_kind,
      })),
    })),
  };
}

function touch(session) {
  session.revision_no += 1;
  session.content_sha256 = canonicalSha256(sessionProjection(session));
}

// ── Session creation (explicit opt-in only) ───────────────────────────────────
/**
 * Creates a bounded live-meeting session. All gates must pass before any
 * provider transport exists; a blocked start returns transport "NONE".
 * @param {object} config
 * @returns {{ok: boolean, reason?: string, transport?: string, session?: object|null}}
 */
export function startLiveSession(config) {
  if (config?.opt_in !== true) return fail("OPT_IN_REQUIRED", { transport: "NONE", session: null });
  if (!isIso(config.requested_at)) return fail("MISSING_REQUESTED_AT", { transport: "NONE", session: null });
  if (typeof config.requested_by !== "string" || !config.requested_by.trim()) {
    return fail("MISSING_REQUESTED_BY", { transport: "NONE", session: null });
  }

  const source = OFFICIAL_LIVE_SOURCES[config.source_id];
  if (!source) return fail("SOURCE_NOT_ALLOWLISTED", { transport: "NONE", session: null });

  let stream;
  try {
    stream = new URL(config.stream_url || "");
  } catch {
    return fail("STREAM_URL_NOT_HTTPS", { transport: "NONE", session: null });
  }
  if (stream.protocol !== "https:") {
    return fail("STREAM_URL_NOT_HTTPS", { transport: "NONE", session: null });
  }
  if (!hostAllowed(stream.hostname, source.allowed_hosts)) {
    return fail("STREAM_HOST_NOT_ALLOWLISTED", { transport: "NONE", session: null });
  }
  if (typeof config.official_page_url !== "string" || !config.official_page_url.startsWith("https://")) {
    return fail("OFFICIAL_PAGE_URL_NOT_HTTPS", { transport: "NONE", session: null });
  }

  if (config.capabilities?.asr_key_configured !== true) {
    return fail("ASR_NOT_CONFIGURED", { transport: "NONE", session: null });
  }

  const maxDuration = Number(config.budget?.max_duration_seconds);
  if (!Number.isInteger(maxDuration) || maxDuration <= 0) {
    return fail("MAX_DURATION_REQUIRED", { transport: "NONE", session: null });
  }
  if (maxDuration > MAX_SESSION_DURATION_SECONDS) {
    return fail("MAX_DURATION_EXCEEDS_CEILING", { transport: "NONE", session: null });
  }
  const maxProvider = config.budget?.max_provider_seconds;
  if (maxProvider !== undefined && maxProvider !== null) {
    if (!Number.isInteger(Number(maxProvider)) || Number(maxProvider) <= 0) {
      return fail("INVALID_PROVIDER_BUDGET", { transport: "NONE", session: null });
    }
  }

  const asr = config.asr || {};
  if (typeof asr.provider !== "string" || !asr.provider.trim()) {
    return fail("MISSING_ASR_PROVIDER", { transport: "NONE", session: null });
  }
  if (typeof asr.model !== "string" || !asr.model.trim()) {
    return fail("MISSING_ASR_MODEL", { transport: "NONE", session: null });
  }
  if (!isIso(config.started_at)) return fail("INVALID_STARTED_AT", { transport: "NONE", session: null });

  const seek = config.seek || { kind: "NONE" };
  if (seek.kind === "STREAM_TIME") {
    if (typeof seek.base_url !== "string" || !seek.base_url.startsWith("https://")) {
      return fail("INVALID_SEEK_BASE_URL", { transport: "NONE", session: null });
    }
    if (!Number.isFinite(Number(seek.offset_seconds)) || Number(seek.offset_seconds) < 0) {
      return fail("INVALID_SEEK_OFFSET", { transport: "NONE", session: null });
    }
  } else if (seek.kind !== "NONE") {
    return fail("INVALID_SEEK_KIND", { transport: "NONE", session: null });
  }

  const meeting = config.meeting || null;
  if (meeting?.agenda_url !== undefined && meeting?.agenda_url !== null) {
    if (typeof meeting.agenda_url !== "string" || !meeting.agenda_url.startsWith("https://")) {
      return fail("AGENDA_URL_NOT_HTTPS", { transport: "NONE", session: null });
    }
  }

  const sessionId =
    config.session_id ??
    `LMS-${canonicalSha256({
      source_id: config.source_id,
      stream_url: config.stream_url,
      started_at: config.started_at,
    })
      .slice(0, 12)
      .toUpperCase()}`;
  if (typeof sessionId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9-]{3,63}$/.test(sessionId)) {
    return fail("INVALID_SESSION_ID", { transport: "NONE", session: null });
  }

  const session = {
    schema_version: LIVE_SESSION_SCHEMA_VERSION,
    session_id: sessionId,
    status: "PREPARING",
    transport: "PROVIDER_STREAM",
    opt_in: {
      requested_by: config.requested_by,
      requested_at: config.requested_at,
    },
    official_source: {
      source_id: config.source_id,
      stream_url: config.stream_url,
      official_page_url: config.official_page_url,
    },
    meeting,
    asr: {
      provider: asr.provider,
      model: asr.model,
      model_version: asr.model_version || null,
      language: asr.language || null,
    },
    seek: {
      kind: seek.kind,
      base_url: seek.kind === "STREAM_TIME" ? seek.base_url : null,
      offset_seconds: seek.kind === "STREAM_TIME" ? Number(seek.offset_seconds) : 0,
    },
    started_at: config.started_at,
    ingest_started_at: null,
    ended_at: null,
    end_reason: null,
    max_duration_seconds: maxDuration,
    budget: {
      max_duration_seconds: maxDuration,
      max_provider_seconds: maxProvider === undefined || maxProvider === null ? null : Number(maxProvider),
      price_per_hour_usd:
        config.budget?.price_per_hour_usd === undefined || config.budget?.price_per_hour_usd === null
          ? null
          : Number(config.budget.price_per_hour_usd),
    },
    cost: { provider_call_count: 0, provider_seconds_used: 0, estimated_cost_usd: null },
    gap_intervals: [],
    segments: [],
    bookmarks: [],
    receipts: [],
    revision_no: 0,
    supersedes_session_id: config.supersedes_session_id ?? null,
    content_sha256: null,
  };
  session.content_sha256 = canonicalSha256(sessionProjection(session));
  return { ok: true, transport: "PROVIDER_STREAM", session };
}

// ── Ingest lifecycle ──────────────────────────────────────────────────────────
export function beginIngest(session, { at } = {}) {
  if (session.status !== "PREPARING") return fail("SESSION_NOT_PREPARING");
  if (!isIso(at) || Date.parse(at) < Date.parse(session.started_at)) return fail("INVALID_INGEST_START");
  const moved = transition(session, "LIVE");
  if (!moved.ok) return moved;
  session.ingest_started_at = at;
  touch(session);
  return { ok: true };
}

/**
 * Appends a provisional transcript segment. Segments are always
 * PROVISIONAL/LIVE_ASR_PROVISIONAL regardless of provider confidence.
 * @param {object} session
 * @param {object} input
 * @returns {{ok: boolean, reason?: string, segment?: object}}
 */
export function appendSegment(session, input = {}) {
  if (session.status !== "LIVE" && session.status !== "DEGRADED") {
    return fail("SESSION_NOT_INGESTING");
  }
  for (const field of Object.keys(input)) {
    if (RESERVED_SEGMENT_FIELDS.has(field)) return fail("RESERVED_FIELD", { field });
    if (SPEAKER_IDENTITY_FIELDS.has(field)) return fail("UNVERIFIED_SPEAKER_IDENTITY", { field });
  }
  if (session.gap_intervals.some((gap) => gap.open)) return fail("GAP_OPEN");

  const tStart = Number(input.t_start_seconds);
  const tEnd = Number(input.t_end_seconds);
  if (!Number.isFinite(tStart) || !Number.isFinite(tEnd) || tStart < 0 || tEnd <= tStart) {
    return fail("INVALID_TIME_RANGE");
  }
  const last = session.segments.at(-1);
  if (last && tStart < last.t_end_seconds) return fail("TIME_OVERLAP");
  if (tEnd > session.max_duration_seconds) return fail("MAX_DURATION_EXCEEDED");
  const inGap = session.gap_intervals.some(
    (gap) => !gap.open && tStart < gap.t_end_seconds && tEnd > gap.t_start_seconds,
  );
  if (inGap) return fail("TIME_IN_GAP");

  if (typeof input.text !== "string" || !input.text.trim()) return fail("MISSING_TEXT");
  if (!REVISION_KINDS.includes(input.kind)) return fail("INVALID_REVISION_KIND");
  if (!isIso(input.received_at)) return fail("INVALID_RECEIVED_AT");
  const confidence = input.provider_confidence;
  if (confidence !== undefined && confidence !== null) {
    if (!Number.isFinite(Number(confidence)) || Number(confidence) < 0 || Number(confidence) > 1) {
      return fail("INVALID_CONFIDENCE");
    }
  }

  const duration = tEnd - tStart;
  if (
    session.budget.max_provider_seconds !== null &&
    session.cost.provider_seconds_used + duration > session.budget.max_provider_seconds
  ) {
    openGap(session, {
      at: input.received_at,
      reason: "BUDGET_STOP",
      detail: "Provider seconds budget exhausted; transport blocked.",
    });
    return fail("BUDGET_EXCEEDED");
  }

  const sequence = session.segments.length + 1;
  const segmentId = `${session.session_id}:SEG-${String(sequence).padStart(4, "0")}`;
  const revision = {
    revision_no: 1,
    kind: input.kind,
    text: input.text,
    speaker_label: input.speaker_label ?? null,
    provider_confidence:
      confidence === undefined || confidence === null
        ? null
        : { value: Number(confidence), kind: "PROVIDER_REPORTED" },
    received_at: input.received_at,
    finalized_at: input.finalized_at ?? (input.kind === "FINAL" ? input.received_at : null),
    content_sha256: canonicalSha256({ text: input.text }),
  };
  const segment = {
    segment_id: segmentId,
    session_id: session.session_id,
    sequence,
    t_start_seconds: tStart,
    t_end_seconds: tEnd,
    text: input.text,
    current_revision: 1,
    revisions: [revision],
    language: input.language ?? session.asr.language,
    asr: { ...session.asr },
    provider_confidence: revision.provider_confidence,
    speaker_label: revision.speaker_label,
    received_at: input.received_at,
    finalized_at: revision.finalized_at,
    is_partial: input.is_partial === true,
    evidence_state: "PROVISIONAL",
    content_label: LIVE_PROVISIONAL_LABEL,
    verification_status: "PROVISIONAL",
    content_sha256: canonicalSha256({
      t_start_seconds: tStart,
      t_end_seconds: tEnd,
      text: input.text,
    }),
  };
  session.segments.push(segment);
  session.cost.provider_call_count += 1;
  session.cost.provider_seconds_used += duration;
  session.cost.estimated_cost_usd =
    session.budget.price_per_hour_usd === null
      ? null
      : Math.round((session.cost.provider_seconds_used / 3600) * session.budget.price_per_hour_usd * 1e6) / 1e6;
  touch(session);
  return { ok: true, segment };
}

/**
 * Records an interim→final (or further interim) provider correction. Revisions
 * are append-only: the UI must never silently replace earlier text.
 */
export function reviseSegment(session, segmentId, input = {}) {
  if (session.status !== "LIVE" && session.status !== "DEGRADED") return fail("SESSION_NOT_INGESTING");
  const segment = session.segments.find((item) => item.segment_id === segmentId);
  if (!segment) return fail("UNKNOWN_SEGMENT");
  for (const field of Object.keys(input)) {
    if (RESERVED_SEGMENT_FIELDS.has(field)) return fail("RESERVED_FIELD", { field });
    if (SPEAKER_IDENTITY_FIELDS.has(field)) return fail("UNVERIFIED_SPEAKER_IDENTITY", { field });
  }
  if (typeof input.text !== "string" || !input.text.trim()) return fail("MISSING_TEXT");
  if (!REVISION_KINDS.includes(input.kind)) return fail("INVALID_REVISION_KIND");
  if (!isIso(input.received_at)) return fail("INVALID_RECEIVED_AT");
  const confidence = input.provider_confidence;
  if (confidence !== undefined && confidence !== null) {
    if (!Number.isFinite(Number(confidence)) || Number(confidence) < 0 || Number(confidence) > 1) {
      return fail("INVALID_CONFIDENCE");
    }
  }

  const revision = {
    revision_no: segment.revisions.length + 1,
    kind: input.kind,
    text: input.text,
    speaker_label: input.speaker_label ?? segment.speaker_label,
    provider_confidence:
      confidence === undefined
        ? segment.provider_confidence
        : confidence === null
          ? null
          : { value: Number(confidence), kind: "PROVIDER_REPORTED" },
    received_at: input.received_at,
    finalized_at: input.finalized_at ?? (input.kind === "FINAL" ? input.received_at : null),
    content_sha256: canonicalSha256({ text: input.text }),
  };
  segment.revisions.push(revision);
  segment.current_revision = revision.revision_no;
  segment.text = input.text;
  segment.speaker_label = revision.speaker_label;
  segment.provider_confidence = revision.provider_confidence;
  segment.finalized_at = revision.finalized_at;
  segment.content_sha256 = canonicalSha256({
    t_start_seconds: segment.t_start_seconds,
    t_end_seconds: segment.t_end_seconds,
    text: input.text,
  });
  touch(session);
  return { ok: true, segment };
}

// ── Gap handling ──────────────────────────────────────────────────────────────
/**
 * Opens a visible coverage gap (stream disconnect, ASR outage, provider
 * timeout, rate limit, budget stop). A gap is a recorded dead interval —
 * never presented as "no discussion happened".
 */
export function openGap(session, { at, reason, detail } = {}) {
  if (session.status !== "LIVE" && session.status !== "DEGRADED") return fail("SESSION_NOT_INGESTING");
  if (!GAP_REASONS.includes(reason)) return fail("INVALID_GAP_REASON");
  if (!isIso(at) || Date.parse(at) < Date.parse(session.started_at)) return fail("INVALID_GAP_START");
  if (session.gap_intervals.some((gap) => gap.open)) return fail("GAP_ALREADY_OPEN");
  const tStart = relativeSeconds(session, at);
  const insideClosed = session.gap_intervals.some(
    (gap) => !gap.open && tStart >= gap.t_start_seconds && tStart < gap.t_end_seconds,
  );
  if (insideClosed) return fail("GAP_OVERLAP");
  const gap = {
    gap_id: `${session.session_id}:GAP-${String(session.gap_intervals.length + 1).padStart(2, "0")}`,
    reason,
    started_at: at,
    ended_at: null,
    t_start_seconds: tStart,
    t_end_seconds: null,
    open: true,
    detail: detail ?? null,
  };
  session.gap_intervals.push(gap);
  if (session.status === "LIVE") transition(session, "DEGRADED");
  touch(session);
  return { ok: true, gap };
}

export function closeGap(session, gapId, { at } = {}) {
  const gap = session.gap_intervals.find((item) => item.gap_id === gapId);
  if (!gap) return fail("UNKNOWN_GAP");
  if (!gap.open) return fail("GAP_NOT_OPEN");
  if (!isIso(at) || Date.parse(at) <= Date.parse(gap.started_at)) return fail("INVALID_GAP_RANGE");
  const tEnd = relativeSeconds(session, at);
  const overlaps = session.gap_intervals.some(
    (other) => !other.open && tEnd > other.t_start_seconds && gap.t_start_seconds < other.t_end_seconds,
  );
  if (overlaps) return fail("GAP_OVERLAP");
  gap.ended_at = at;
  gap.t_end_seconds = tEnd;
  gap.open = false;
  if (session.status === "DEGRADED" && !session.gap_intervals.some((item) => item.open)) {
    transition(session, "LIVE");
  }
  touch(session);
  return { ok: true, gap };
}

// ── Timeline / search / bookmarks / highlighting ──────────────────────────────
/**
 * Returns display-ready ordered timeline entries. Segments carry the
 * provisional badges; gaps are explicit entries, never blank ranges.
 */
export function buildTimeline(session) {
  const entries = [];
  for (const segment of session.segments) {
    entries.push({
      kind: "SEGMENT",
      segment_id: segment.segment_id,
      sequence: segment.sequence,
      t_start_seconds: segment.t_start_seconds,
      t_end_seconds: segment.t_end_seconds,
      text: segment.text,
      evidence_state: segment.evidence_state,
      content_label: segment.content_label,
      badges: [...SESSION_BADGES],
      display_empty: false,
    });
  }
  for (const gap of session.gap_intervals) {
    entries.push({
      kind: "GAP",
      gap_id: gap.gap_id,
      gap_reason: gap.reason,
      t_start_seconds: gap.t_start_seconds,
      t_end_seconds: gap.t_end_seconds,
      started_at: gap.started_at,
      ended_at: gap.ended_at,
      open: gap.open,
      badges: ["GAP"],
      display_empty: false,
    });
  }
  entries.sort((a, b) => a.t_start_seconds - b.t_start_seconds);
  return entries;
}

/**
 * Resolves a provisional segment to a seek locator. When the session lacks
 * reliable stream seek semantics the locator degrades to plain time text —
 * never a fabricated deep link.
 */
export function resolveSegmentLocator(session, segment) {
  const offset = session.seek?.offset_seconds ?? 0;
  const absolute = offset + segment.t_start_seconds;
  if (session.seek?.kind === "STREAM_TIME" && session.seek.base_url?.startsWith("https://")) {
    return {
      kind: "STREAM_TIME",
      url: session.seek.base_url,
      time_seconds: absolute,
      display: formatTime(absolute),
      reliable_seek: true,
    };
  }
  return {
    kind: "TIME_TEXT",
    url: null,
    time_seconds: absolute,
    display: formatTime(absolute),
    reliable_seek: false,
  };
}

export function searchProvisional(session, query) {
  if (typeof query !== "string" || !query.trim()) return fail("EMPTY_QUERY");
  const needle = query.trim().toLowerCase();
  const matches = session.segments
    .filter((segment) => segment.text.toLowerCase().includes(needle))
    .map((segment) => ({
      segment_id: segment.segment_id,
      sequence: segment.sequence,
      t_start_seconds: segment.t_start_seconds,
      t_end_seconds: segment.t_end_seconds,
      text: segment.text,
      evidence_state: segment.evidence_state,
      content_label: segment.content_label,
      locator: resolveSegmentLocator(session, segment),
    }));
  return { ok: true, query: query.trim(), matches };
}

/**
 * Creates a WATCH bookmark over one or more segments. The bookmark stores the
 * segment identities, the provisional time range, and the content hashes at
 * mark time so later provider revisions never silently retarget it.
 */
export function createBookmark(session, { segment_ids, reason_code, note, profile_id, at } = {}) {
  if (!Array.isArray(segment_ids) || !segment_ids.length) return fail("NO_SEGMENTS");
  if (!BOOKMARK_REASONS.includes(reason_code)) return fail("INVALID_REASON_CODE");
  if (!isIso(at)) return fail("INVALID_BOOKMARK_TIME");
  const found = segment_ids.map((id) => session.segments.find((segment) => segment.segment_id === id));
  if (found.some((segment) => !segment)) return fail("UNKNOWN_SEGMENT");
  const tStart = Math.min(...found.map((segment) => segment.t_start_seconds));
  const tEnd = Math.max(...found.map((segment) => segment.t_end_seconds));
  const segmentHashes = Object.fromEntries(found.map((segment) => [segment.segment_id, segment.content_sha256]));
  const bookmarkId = `BMK-${canonicalSha256({
    session_id: session.session_id,
    segment_ids: [...segment_ids],
    reason_code,
    note: note ?? null,
    profile_id: profile_id ?? null,
    at,
  })
    .slice(0, 12)
    .toUpperCase()}`;
  const bookmark = {
    bookmark_id: bookmarkId,
    kind: "WATCH",
    session_id: session.session_id,
    segment_ids: [...segment_ids],
    time_range: { t_start_seconds: tStart, t_end_seconds: tEnd },
    segment_hashes: segmentHashes,
    session_revision: session.revision_no,
    reason_code,
    profile_id: profile_id ?? null,
    note: note ?? null,
    created_at: at,
  };
  session.bookmarks.push(bookmark);
  touch(session);
  return { ok: true, bookmark };
}

/**
 * Deterministic topic/profile highlighting. Produces navigation priority only:
 * it never modifies transcript text and never emits operational advice.
 */
export function highlightSegments(session, profile = {}) {
  if (typeof profile.profile_id !== "string" || !profile.profile_id.trim()) return fail("INVALID_PROFILE");
  if (!Array.isArray(profile.terms) || !profile.terms.length) return fail("INVALID_PROFILE");
  const terms = profile.terms.filter((term) => typeof term === "string" && term.trim());
  if (!terms.length) return fail("INVALID_PROFILE");
  const matches = [];
  for (const segment of session.segments) {
    const haystack = segment.text.toLowerCase();
    const matchedTerms = terms.filter((term) => haystack.includes(term.trim().toLowerCase()));
    if (matchedTerms.length) {
      matches.push({
        segment_id: segment.segment_id,
        matched_terms: matchedTerms,
        priority: matchedTerms.length,
        t_start_seconds: segment.t_start_seconds,
      });
    }
  }
  matches.sort((a, b) => b.priority - a.priority || a.t_start_seconds - b.t_start_seconds);
  return { ok: true, kind: "NAVIGATION_PRIORITY", profile_id: profile.profile_id, matches };
}

// ── Bounds, end, crash ────────────────────────────────────────────────────────
/**
 * Enforces the hard duration bound. Past max_duration the session ends, any
 * open gap is closed at the bound, and transport is cut.
 */
export function checkSessionBounds(session, { at } = {}) {
  if (session.status !== "LIVE" && session.status !== "DEGRADED") return { ok: true, bounded: false };
  if (!isIso(at)) return fail("INVALID_BOUND_TIME");
  const elapsed = (Date.parse(at) - Date.parse(session.started_at)) / 1000;
  if (elapsed < session.max_duration_seconds) return { ok: true, bounded: false };
  const boundIso = isoAt(session, session.max_duration_seconds);
  for (const gap of session.gap_intervals) {
    // A gap that outlasts the bound stays open: coverage after its start is
    // unknown, not empty. Only close when the bound is strictly later.
    if (gap.open && boundIso > gap.started_at) {
      gap.ended_at = boundIso;
      gap.t_end_seconds = relativeSeconds(session, boundIso);
      gap.open = false;
    }
  }
  const moved = transition(session, "ENDED");
  if (!moved.ok) return moved;
  session.ended_at = boundIso;
  session.end_reason = "MAX_DURATION";
  session.transport = "NONE";
  touch(session);
  return { ok: true, bounded: true };
}

export function endLiveSession(session, { at, reason } = {}) {
  if (session.status !== "LIVE" && session.status !== "DEGRADED") return fail("SESSION_NOT_ACTIVE");
  if (!isIso(at) || Date.parse(at) < Date.parse(session.started_at)) return fail("INVALID_END_TIME");
  for (const gap of session.gap_intervals) {
    // A gap opened at or after the end time stays open: coverage is unknown.
    if (gap.open && at > gap.started_at) {
      gap.ended_at = at;
      gap.t_end_seconds = relativeSeconds(session, at);
      gap.open = false;
    }
  }
  const moved = transition(session, "ENDED");
  if (!moved.ok) return moved;
  session.ended_at = at;
  session.end_reason = reason ?? "OPERATOR_STOP";
  session.transport = "NONE";
  touch(session);
  return { ok: true };
}

/**
 * Marks a session FAILED (e.g. crash). An open SESSION_CRASH gap preserves the
 * unknown coverage interval; the session keeps all collected segments and may
 * be superseded by a fresh startLiveSession via supersedes_session_id.
 */
export function failSession(session, { at, reason } = {}) {
  if (!["PREPARING", "LIVE", "DEGRADED", "RECONCILING"].includes(session.status)) {
    return fail("SESSION_NOT_FAILABLE");
  }
  if (!isIso(at) || Date.parse(at) < Date.parse(session.started_at)) return fail("INVALID_FAIL_TIME");
  if (!session.gap_intervals.some((gap) => gap.open)) {
    const tStart = Math.max(0, relativeSeconds(session, at));
    session.gap_intervals.push({
      gap_id: `${session.session_id}:GAP-${String(session.gap_intervals.length + 1).padStart(2, "0")}`,
      reason: GAP_REASONS.includes(reason) ? reason : "SESSION_CRASH",
      started_at: at,
      ended_at: null,
      t_start_seconds: tStart,
      t_end_seconds: null,
      open: true,
      detail: "Session ended unexpectedly; coverage after this point is unknown.",
    });
  }
  const moved = transition(session, "FAILED");
  if (!moved.ok) return moved;
  session.ended_at = at;
  session.end_reason = reason ?? "SESSION_CRASH";
  session.transport = "NONE";
  touch(session);
  return { ok: true };
}

// ── Post-event reconciliation ─────────────────────────────────────────────────
export function reopenReconciliation(session) {
  if (session.status === "RECONCILING") return { ok: true };
  if (session.status !== "RECONCILED") return fail("SESSION_NOT_RECONCILABLE");
  const moved = transition(session, "RECONCILING");
  if (!moved.ok) return moved;
  touch(session);
  return { ok: true };
}

function provisionalText(session, segmentIds) {
  return session.segments
    .filter((segment) => segmentIds.includes(segment.segment_id))
    .map((segment) => segment.text)
    .join("\n");
}

/**
 * Reconciles a session's WATCH bookmarks against post-event official sources.
 * Every candidate produces an immutable receipt entry; the receipt records
 * before/after hashes, locators, verification status, and tool versions.
 * With no post-event sources all candidates stay SOURCE_NOT_YET_AVAILABLE.
 */
export function reconcileSession(session, input = {}) {
  if (session.status !== "ENDED" && session.status !== "RECONCILING") {
    return fail("SESSION_NOT_ENDED");
  }
  const toolVersions = input.tool_versions || {};
  if (typeof toolVersions.reconciler_version !== "string" || !toolVersions.reconciler_version.trim()) {
    return fail("MISSING_TOOL_VERSIONS");
  }
  if (typeof toolVersions.parser_version !== "string" || !toolVersions.parser_version.trim()) {
    return fail("MISSING_TOOL_VERSIONS");
  }
  if (!isIso(input.reconciled_at)) return fail("INVALID_RECONCILED_AT");

  const sources = input.post_event_sources ?? [];
  if (!Array.isArray(sources)) return fail("INVALID_POST_EVENT_SOURCES");
  for (const source of sources) {
    if (!POST_EVENT_SOURCE_KINDS.includes(source?.source_kind)) return fail("INVALID_POST_EVENT_SOURCE");
    if (typeof source.url !== "string" || !source.url.startsWith("https://")) {
      return fail("INVALID_POST_EVENT_SOURCE");
    }
    if (typeof source.content_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(source.content_sha256)) {
      return fail("INVALID_POST_EVENT_SOURCE");
    }
  }

  const matches = input.matches ?? [];
  if (!Array.isArray(matches)) return fail("INVALID_MATCHES");
  const bookmarkIds = new Set(session.bookmarks.map((bookmark) => bookmark.bookmark_id));
  for (const match of matches) {
    if (!bookmarkIds.has(match?.bookmark_id)) return fail("UNKNOWN_BOOKMARK");
    if (!MATCH_VERDICTS.includes(match.verdict)) return fail("INVALID_MATCH_VERDICT");
    const sourceIndex = Number(match.source_index);
    if (!Number.isInteger(sourceIndex) || sourceIndex < 0 || sourceIndex >= sources.length) {
      return fail("INVALID_MATCH_SOURCE");
    }
    // An agenda schedules what was planned; it cannot confirm or supersede
    // what was actually said. Verdicts must cite VOD or MINUTES sources.
    if (sources[sourceIndex].source_kind === "AGENDA") return fail("AGENDA_NOT_VERDICT_EVIDENCE");
    if (match.verdict !== "FALSE_POSITIVE") {
      if (typeof match.official_locator !== "string" || !match.official_locator.trim()) {
        return fail("MISSING_OFFICIAL_LOCATOR");
      }
    }
    if (match.verdict === "CONFIRMED" || match.verdict === "SUPERSEDED") {
      // The matcher must hash the official text it checked; reusing the
      // provisional hash would fabricate an official confirmation.
      if (typeof match.official_text_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(match.official_text_sha256)) {
        return fail("MISSING_OFFICIAL_TEXT_HASH");
      }
    }
  }

  const receiptNo = session.receipts.length + 1;
  const receiptId = `RCPT-${session.session_id}-${String(receiptNo).padStart(2, "0")}`;
  const entries = session.bookmarks.map((bookmark, index) => {
    const match = matches.find((item) => item.bookmark_id === bookmark.bookmark_id);
    const beforeHash = canonicalSha256({ text: provisionalText(session, bookmark.segment_ids) });
    const base = {
      entry_id: `${receiptId}-E${String(index + 1).padStart(2, "0")}`,
      bookmark_id: bookmark.bookmark_id,
      segment_ids: [...bookmark.segment_ids],
      provisional_locator: {
        session_id: session.session_id,
        t_start_seconds: bookmark.time_range.t_start_seconds,
        t_end_seconds: bookmark.time_range.t_end_seconds,
      },
      before_transcript_sha256: beforeHash,
      after_transcript_sha256: null,
      official_locator: null,
      official_url: null,
      source_kind: null,
      note: match?.note ?? null,
      reconciled_at: input.reconciled_at,
    };
    if (!sources.length) return { ...base, status: "SOURCE_NOT_YET_AVAILABLE" };
    if (!match) return { ...base, status: "UNRESOLVED" };
    const source = sources[Number(match.source_index)];
    const confirmedStatus =
      source.source_kind === "VOD" ? "CONFIRMED_BY_OFFICIAL_MEDIA" : "CONFIRMED_BY_MINUTES";
    if (match.verdict === "CONFIRMED") {
      return {
        ...base,
        status: confirmedStatus,
        after_transcript_sha256: match.official_text_sha256,
        official_locator: match.official_locator,
        official_url: source.url,
        source_kind: source.source_kind,
      };
    }
    if (match.verdict === "SUPERSEDED") {
      return {
        ...base,
        status: "SUPERSEDED_TRANSCRIPT",
        after_transcript_sha256: match.official_text_sha256,
        official_locator: match.official_locator,
        official_url: source.url,
        source_kind: source.source_kind,
      };
    }
    return { ...base, status: "DROPPED_FALSE_POSITIVE", source_kind: source.source_kind };
  });

  const outcome = entries.some((entry) => entry.status === "SOURCE_NOT_YET_AVAILABLE")
    ? "PENDING_SOURCES"
    : "COMPLETE";
  const receipt = {
    receipt_id: receiptId,
    receipt_no: receiptNo,
    schema_version: LIVE_SESSION_SCHEMA_VERSION,
    session_id: session.session_id,
    source_id: session.official_source.source_id,
    created_at: input.reconciled_at,
    reconciled_at: input.reconciled_at,
    tool_versions: {
      reconciler_version: toolVersions.reconciler_version,
      parser_version: toolVersions.parser_version,
      asr_provider: session.asr.provider,
      asr_model: session.asr.model,
      asr_model_version: session.asr.model_version,
    },
    post_event_sources: sources.map((source) => ({ ...source })),
    entries,
    outcome,
    session_content_sha256: session.content_sha256,
    supersedes_receipt_id: session.receipts.at(-1)?.receipt_id ?? null,
    stale: false,
    stale_reason: null,
    stale_marked_at: null,
  };
  session.receipts.push(receipt);
  if (session.status === "ENDED") {
    const moved = transition(session, "RECONCILING");
    if (!moved.ok) return moved;
  }
  if (outcome === "COMPLETE") {
    const moved = transition(session, "RECONCILED");
    if (!moved.ok) return moved;
  }
  touch(session);
  return { ok: true, receipt };
}

/**
 * Marks a receipt stale when a recorded post-event source has been revised.
 * Receipt entries are never rewritten; staleness is metadata on the receipt.
 */
export function markReceiptStale(receipt, { source_url, observed_sha256, marked_at } = {}) {
  const source = receipt.post_event_sources.find((item) => item.url === source_url);
  if (!source) return receipt;
  if (receipt.stale) return receipt;
  if (observed_sha256 !== source.content_sha256) {
    receipt.stale = true;
    receipt.stale_reason = "SOURCE_REVISED";
    receipt.stale_marked_at = marked_at ?? null;
  }
  return receipt;
}

/**
 * Projects CONFIRMED_* receipt entries into formal evidence records shaped for
 * the existing council evidence contract. SUPERSEDED_TRANSCRIPT is excluded:
 * the provisional claim was wrong, and the official wording enters through its
 * own collection path rather than through the live layer. A stale receipt
 * emits nothing — its official source was revised, so it must be
 * re-reconciled before any downstream publication.
 */
export function toFormalEvidence(receipt) {
  if (receipt.stale) return [];
  const evidence = [];
  for (const entry of receipt.entries) {
    let evidenceType = null;
    if (entry.status === "CONFIRMED_BY_OFFICIAL_MEDIA") evidenceType = "ORAL_OFFICIAL";
    if (entry.status === "CONFIRMED_BY_MINUTES") evidenceType = "WRITTEN_OFFICIAL";
    if (!evidenceType) continue;
    evidence.push({
      evidence_id: `EV-LIVE-${entry.bookmark_id}`,
      evidence_type: evidenceType,
      source_id: receipt.source_id,
      official_url: entry.official_url,
      locator: entry.official_locator,
      content_label: evidenceType,
      verification_status: "AUTO_PASS",
      verified_via: "POST_EVENT_RECONCILIATION",
      receipt_id: receipt.receipt_id,
      bookmark_id: entry.bookmark_id,
      provisional_sha256: entry.before_transcript_sha256,
      official_sha256: entry.after_transcript_sha256,
    });
  }
  return evidence;
}
