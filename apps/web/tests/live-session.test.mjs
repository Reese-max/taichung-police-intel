import assert from "node:assert/strict";
import test from "node:test";

import {
  BOOKMARK_REASONS,
  GAP_REASONS,
  LIVE_PROVISIONAL_LABEL,
  LIVE_SESSION_SCHEMA_VERSION,
  MAX_SESSION_DURATION_SECONDS,
  OFFICIAL_LIVE_SOURCES,
  RECONCILIATION_STATUSES,
  SESSION_STATUSES,
  appendSegment,
  beginIngest,
  buildTimeline,
  checkSessionBounds,
  closeGap,
  createBookmark,
  endLiveSession,
  failSession,
  highlightSegments,
  markReceiptStale,
  openGap,
  reconcileSession,
  reopenReconciliation,
  resolveSegmentLocator,
  reviseSegment,
  searchProvisional,
  startLiveSession,
  toFormalEvidence,
} from "../lib/live-session.js";
import { LIVE_SESSION_FIXTURES, runLiveFixture } from "../lib/live-session-fixtures.js";
import { validateCouncilEvidence } from "../lib/council-prep.js";
import { checkEligibility } from "../lib/homepage-eligibility.js";

const BASE_CONFIG = Object.freeze({
  opt_in: true,
  requested_by: "council-liaison@example.test",
  requested_at: "2026-09-17T09:55:00+08:00",
  source_id: "S-010",
  stream_url: "https://streamak0128.akamaized.net/live/tccc/playlist.m3u8",
  official_page_url: "https://www.tccc.gov.tw/live",
  meeting: { meeting_id: "TCCC-4-8-REG", title: "第4屆第8次定期會業務質詢" },
  asr: { provider: "Groq", model: "whisper-large-v3", model_version: "2026-08", language: "zh" },
  capabilities: { asr_key_configured: true },
  budget: { max_duration_seconds: 7200 },
  seek: {
    kind: "STREAM_TIME",
    base_url: "https://streamak0128.akamaized.net/live/tccc/playlist.m3u8",
    offset_seconds: 0,
  },
  started_at: "2026-09-17T10:00:00+08:00",
});

function makeSession(overrides = {}) {
  const config = {
    ...BASE_CONFIG,
    ...overrides,
    budget: { ...BASE_CONFIG.budget, ...(overrides.budget || {}) },
    capabilities: { ...BASE_CONFIG.capabilities, ...(overrides.capabilities || {}) },
    seek: { ...BASE_CONFIG.seek, ...(overrides.seek || {}) },
  };
  const result = startLiveSession(config);
  assert.equal(result.ok, true, result.reason);
  return result.session;
}

function makeLiveSession(overrides = {}) {
  const session = makeSession(overrides);
  const begin = beginIngest(session, { at: "2026-09-17T10:00:05+08:00" });
  assert.equal(begin.ok, true, begin.reason);
  return session;
}

function addSegment(session, overrides = {}) {
  const result = appendSegment(session, {
    t_start_seconds: 0,
    t_end_seconds: 30,
    text: "交通罰鍰與警政預算",
    kind: "FINAL",
    provider_confidence: 0.82,
    speaker_label: "SPEAKER_0",
    language: "zh",
    received_at: "2026-09-17T10:00:35+08:00",
    ...overrides,
  });
  assert.equal(result.ok, true, result.reason);
  return result.segment;
}

// ── A1: Versioned LiveMeetingSession schema ───────────────────────────────────

test("session enums and schema version are frozen and complete", () => {
  assert.equal(LIVE_SESSION_SCHEMA_VERSION, 1);
  for (const status of ["PREPARING", "LIVE", "DEGRADED", "ENDED", "RECONCILING", "RECONCILED", "FAILED"]) {
    assert.ok(SESSION_STATUSES.includes(status), `missing status ${status}`);
  }
  for (const reason of ["STREAM_DISCONNECT", "ASR_OUTAGE", "PROVIDER_TIMEOUT", "RATE_LIMIT", "BUDGET_STOP", "MAX_DURATION_REACHED", "SESSION_CRASH"]) {
    assert.ok(GAP_REASONS.includes(reason), `missing gap reason ${reason}`);
  }
  for (const status of ["CONFIRMED_BY_OFFICIAL_MEDIA", "CONFIRMED_BY_MINUTES", "SUPERSEDED_TRANSCRIPT", "UNRESOLVED", "SOURCE_NOT_YET_AVAILABLE", "DROPPED_FALSE_POSITIVE"]) {
    assert.ok(RECONCILIATION_STATUSES.includes(status), `missing reconciliation status ${status}`);
  }
  assert.ok(Object.isFrozen(SESSION_STATUSES));
  assert.ok(Object.isFrozen(RECONCILIATION_STATUSES));
});

test("started session carries versioned schema, ASR contract, bounds, and hashes", () => {
  const session = makeSession();
  assert.equal(session.schema_version, LIVE_SESSION_SCHEMA_VERSION);
  assert.equal(session.status, "PREPARING");
  assert.equal(session.transport, "PROVIDER_STREAM");
  assert.equal(session.official_source.source_id, "S-010");
  assert.equal(session.official_source.stream_url, BASE_CONFIG.stream_url);
  assert.equal(session.asr.provider, "Groq");
  assert.equal(session.asr.model, "whisper-large-v3");
  assert.equal(session.max_duration_seconds, 7200);
  assert.deepEqual(session.gap_intervals, []);
  assert.ok(session.started_at);
  assert.equal(session.ended_at, null);
  assert.match(session.content_sha256, /^[0-9a-f]{64}$/);
  assert.match(session.session_id, /^LMS-[0-9A-F]{12}$/);
  assert.equal(session.cost.provider_call_count, 0);
});

// ── A2: Explicit opt-in gate; transport=0 when not configured ─────────────────

test("session start requires explicit opt-in", () => {
  const result = startLiveSession({ ...BASE_CONFIG, opt_in: false });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "OPT_IN_REQUIRED");
  assert.equal(result.transport, "NONE");
  assert.equal(result.session, null);
});

test("missing opt_in flag is rejected (absence is not consent)", () => {
  const { opt_in, ...rest } = BASE_CONFIG;
  const result = startLiveSession(rest);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "OPT_IN_REQUIRED");
});

test("non-allowlisted source is rejected", () => {
  const result = startLiveSession({ ...BASE_CONFIG, source_id: "S-999" });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "SOURCE_NOT_ALLOWLISTED");
});

test("non-allowlisted stream host is rejected", () => {
  const result = startLiveSession({
    ...BASE_CONFIG,
    stream_url: "https://attacker.example.com/stream.m3u8",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "STREAM_HOST_NOT_ALLOWLISTED");
});

test("non-https stream url is rejected", () => {
  const result = startLiveSession({ ...BASE_CONFIG, stream_url: "http://streamak0128.akamaized.net/x" });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "STREAM_URL_NOT_HTTPS");
});

test("unconfigured ASR key blocks transport entirely", () => {
  const result = startLiveSession({
    ...BASE_CONFIG,
    capabilities: { asr_key_configured: false },
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "ASR_NOT_CONFIGURED");
  assert.equal(result.transport, "NONE");
  assert.equal(result.session, null);
});

test("missing max_duration_seconds is rejected (no unbounded sessions)", () => {
  const config = { ...BASE_CONFIG, budget: {} };
  const result = startLiveSession(config);
  assert.equal(result.ok, false);
  assert.equal(result.reason, "MAX_DURATION_REQUIRED");
});

test("max_duration above the hard ceiling is rejected", () => {
  const result = startLiveSession({
    ...BASE_CONFIG,
    budget: { max_duration_seconds: MAX_SESSION_DURATION_SECONDS + 1 },
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "MAX_DURATION_EXCEEDS_CEILING");
});

// ── A3: Provisional segments can never be formal facts ────────────────────────

test("provisional segment carries provisional labels and never AUTO_PASS", () => {
  const session = makeLiveSession();
  const segment = addSegment(session);
  assert.equal(segment.evidence_state, "PROVISIONAL");
  assert.equal(segment.content_label, LIVE_PROVISIONAL_LABEL);
  assert.equal(segment.verification_status, "PROVISIONAL");
  assert.notEqual(segment.content_label, "GROQ_ASR");
});

test("provisional segment fails the formal homepage eligibility gate", () => {
  const session = makeLiveSession();
  const segment = addSegment(session);
  const forged = {
    ...segment,
    stable_id: segment.segment_id,
    content_disposition: "HOME_CANDIDATE",
    evidence_ids: ["EV-1"],
    official_url: "https://www.tccc.gov.tw/live",
    reason_codes: ["HIGH_VALUE"],
  };
  const eligibility = checkEligibility(forged);
  assert.equal(eligibility.eligible, false);
  assert.equal(eligibility.reason, "NOT_AUTO_PASS");
});

test("caller cannot forge formal fields onto a live segment", () => {
  const session = makeLiveSession();
  for (const field of ["verification_status", "evidence_state", "content_label"]) {
    const result = appendSegment(session, {
      t_start_seconds: 0,
      t_end_seconds: 10,
      text: "x",
      kind: "FINAL",
      received_at: "2026-09-17T10:00:10+08:00",
      [field]: "AUTO_PASS",
    });
    assert.equal(result.ok, false);
    assert.equal(result.reason, "RESERVED_FIELD");
  }
});

test("speaker diarization may not attach real official names", () => {
  const session = makeLiveSession();
  const result = appendSegment(session, {
    t_start_seconds: 0,
    t_end_seconds: 10,
    text: "質詢內容",
    kind: "FINAL",
    speaker_name: "王議員",
    received_at: "2026-09-17T10:00:10+08:00",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "UNVERIFIED_SPEAKER_IDENTITY");
});

// ── A4: Stable identity, time range, hash, provider contract, revisions ────────

test("segments receive stable session-local ids and monotonic sequences", () => {
  const session = makeLiveSession();
  const first = addSegment(session);
  const second = addSegment(session, {
    t_start_seconds: 30,
    t_end_seconds: 60,
    received_at: "2026-09-17T10:01:05+08:00",
  });
  assert.equal(first.sequence, 1);
  assert.equal(second.sequence, 2);
  assert.ok(first.segment_id.startsWith(`${session.session_id}:SEG-`));
  assert.notEqual(first.segment_id, second.segment_id);
  assert.match(first.content_sha256, /^[0-9a-f]{64}$/);
  assert.equal(first.asr.model, "whisper-large-v3");
  assert.equal(first.provider_confidence.kind, "PROVIDER_REPORTED");
});

test("overlapping segment time ranges are rejected (no silent stitching)", () => {
  const session = makeLiveSession();
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 30 });
  const result = appendSegment(session, {
    t_start_seconds: 29,
    t_end_seconds: 60,
    text: "overlap",
    kind: "FINAL",
    received_at: "2026-09-17T10:01:00+08:00",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "TIME_OVERLAP");
});

test("interim to final revision keeps both texts in history", () => {
  const session = makeLiveSession();
  const segment = addSegment(session, { kind: "INTERIM", text: "交通罰" });
  const revised = reviseSegment(session, segment.segment_id, {
    text: "交通罰鍰與舉發",
    kind: "FINAL",
    received_at: "2026-09-17T10:00:36+08:00",
    finalized_at: "2026-09-17T10:00:38+08:00",
  });
  assert.equal(revised.ok, true, revised.reason);
  assert.equal(segment.text, "交通罰鍰與舉發");
  assert.equal(segment.revisions.length, 2);
  assert.equal(segment.revisions[0].kind, "INTERIM");
  assert.equal(segment.revisions[0].text, "交通罰");
  assert.equal(segment.revisions[1].kind, "FINAL");
  assert.notEqual(segment.revisions[0].content_sha256, segment.revisions[1].content_sha256);
});

test("provider confidence above 1.0 is rejected (not a truth probability)", () => {
  const session = makeLiveSession();
  const result = appendSegment(session, {
    t_start_seconds: 0,
    t_end_seconds: 10,
    text: "x",
    kind: "FINAL",
    provider_confidence: 1.5,
    received_at: "2026-09-17T10:00:10+08:00",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "INVALID_CONFIDENCE");
});

// ── A5/A6: Seek locators degrade honestly; gaps stay visible ──────────────────

test("locator uses official stream time when seek semantics are known", () => {
  const session = makeLiveSession({
    seek: { kind: "STREAM_TIME", base_url: BASE_CONFIG.seek.base_url, offset_seconds: 600 },
  });
  const segment = addSegment(session, { t_start_seconds: 120, t_end_seconds: 150 });
  const locator = resolveSegmentLocator(session, segment);
  assert.equal(locator.kind, "STREAM_TIME");
  assert.equal(locator.time_seconds, 720);
  assert.equal(locator.url, BASE_CONFIG.seek.base_url);
  assert.equal(locator.reliable_seek, true);
});

test("locator degrades to time text when seek semantics are unknown", () => {
  const session = makeLiveSession({ seek: { kind: "NONE" } });
  const segment = addSegment(session, { t_start_seconds: 65, t_end_seconds: 90 });
  const locator = resolveSegmentLocator(session, segment);
  assert.equal(locator.kind, "TIME_TEXT");
  assert.equal(locator.reliable_seek, false);
  assert.equal(locator.url, null);
  assert.equal(locator.display, "01:05");
});

test("gap intervals are visible timeline entries, not empty content", () => {
  const session = makeLiveSession();
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 300 });
  const opened = openGap(session, { at: "2026-09-17T10:05:00+08:00", reason: "STREAM_DISCONNECT" });
  assert.equal(opened.ok, true, opened.reason);
  const closed = closeGap(session, opened.gap.gap_id, { at: "2026-09-17T10:06:30+08:00" });
  assert.equal(closed.ok, true, closed.reason);
  addSegment(session, {
    t_start_seconds: 390,
    t_end_seconds: 420,
    received_at: "2026-09-17T10:07:05+08:00",
  });
  const timeline = buildTimeline(session);
  const gapEntry = timeline.find((entry) => entry.kind === "GAP");
  assert.ok(gapEntry, "timeline must contain a visible gap entry");
  assert.equal(gapEntry.gap_reason, "STREAM_DISCONNECT");
  assert.equal(gapEntry.display_empty, false);
  const segmentEntry = timeline.find((entry) => entry.kind === "SEGMENT");
  assert.deepEqual(segmentEntry.badges, ["LIVE", "AI_TRANSCRIPT", "UNCHECKED"]);
});

test("overlapping gap intervals are rejected", () => {
  const session = makeLiveSession();
  const first = openGap(session, { at: "2026-09-17T10:05:00+08:00", reason: "STREAM_DISCONNECT" });
  assert.equal(first.ok, true);
  const second = openGap(session, { at: "2026-09-17T10:05:30+08:00", reason: "ASR_OUTAGE" });
  assert.equal(second.ok, false);
  assert.equal(second.reason, "GAP_ALREADY_OPEN");
});

test("segments cannot be appended while a gap is open", () => {
  const session = makeLiveSession();
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 60 });
  openGap(session, { at: "2026-09-17T10:05:00+08:00", reason: "STREAM_DISCONNECT" });
  const result = appendSegment(session, {
    t_start_seconds: 60,
    t_end_seconds: 90,
    text: "不應存在",
    kind: "FINAL",
    received_at: "2026-09-17T10:05:30+08:00",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "GAP_OPEN");
});

test("a segment spanning a closed gap interval is rejected", () => {
  const session = makeLiveSession();
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 300 });
  const gap = openGap(session, { at: "2026-09-17T10:05:00+08:00", reason: "ASR_OUTAGE" });
  closeGap(session, gap.gap.gap_id, { at: "2026-09-17T10:06:00+08:00" });
  // gap covers relative seconds 300..360; a segment may not claim that dead zone
  const result = appendSegment(session, {
    t_start_seconds: 300,
    t_end_seconds: 360,
    text: "假裝有內容",
    kind: "FINAL",
    received_at: "2026-09-17T10:06:30+08:00",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "TIME_IN_GAP");
});

test("open gap marks the session DEGRADED; closing restores LIVE", () => {
  const session = makeLiveSession();
  const gap = openGap(session, { at: "2026-09-17T10:02:00+08:00", reason: "PROVIDER_TIMEOUT" });
  assert.equal(session.status, "DEGRADED");
  closeGap(session, gap.gap.gap_id, { at: "2026-09-17T10:02:40+08:00" });
  assert.equal(session.status, "LIVE");
});

// ── A7: Deterministic profile highlighting (navigation only) ──────────────────

test("profile terms highlight segments without modifying transcript", () => {
  const session = makeLiveSession();
  const target = addSegment(session, { text: "交通執法與警政預算" });
  const other = addSegment(session, {
    t_start_seconds: 30,
    t_end_seconds: 60,
    text: "環保業務報告",
    received_at: "2026-09-17T10:01:05+08:00",
  });
  const beforeHash = target.content_sha256;
  const result = highlightSegments(session, {
    profile_id: "POLICE_POLICY",
    terms: ["交通", "警政", "警察局"],
  });
  assert.equal(result.ok, true);
  assert.equal(result.kind, "NAVIGATION_PRIORITY");
  const hit = result.matches.find((m) => m.segment_id === target.segment_id);
  assert.ok(hit);
  assert.deepEqual(hit.matched_terms.sort(), ["交通", "警政"].sort());
  assert.equal(result.matches.some((m) => m.segment_id === other.segment_id), false);
  assert.equal(target.content_sha256, beforeHash);
  assert.equal(target.text, "交通執法與警政預算");
});

// ── A8: Live search returns provisional-flagged results ───────────────────────

test("search returns matching segments with provisional locator", () => {
  const session = makeLiveSession();
  addSegment(session, { text: "移工查緝獎勵" });
  addSegment(session, {
    t_start_seconds: 30,
    t_end_seconds: 60,
    text: "交通罰鍰",
    received_at: "2026-09-17T10:01:05+08:00",
  });
  const result = searchProvisional(session, "移工");
  assert.equal(result.ok, true);
  assert.equal(result.matches.length, 1);
  assert.equal(result.matches[0].locator.kind, "STREAM_TIME");
  assert.equal(result.matches[0].evidence_state, "PROVISIONAL");
});

// ── A9: Bookmarks survive revisions and keep scope ────────────────────────────

test("bookmark keeps pointing at the same range after interim→final revision", () => {
  const session = makeLiveSession();
  const segment = addSegment(session, { kind: "INTERIM", text: "預算" });
  const bookmark = createBookmark(session, {
    segment_ids: [segment.segment_id],
    reason_code: "MANUAL_WATCH",
    note: "回看這段",
    at: "2026-09-17T10:00:40+08:00",
  });
  assert.equal(bookmark.ok, true, bookmark.reason);
  assert.equal(bookmark.bookmark.kind, "WATCH");
  const hashBefore = bookmark.bookmark.segment_hashes[segment.segment_id];
  reviseSegment(session, segment.segment_id, {
    text: "預算與決算",
    kind: "FINAL",
    received_at: "2026-09-17T10:00:45+08:00",
  });
  const stillThere = session.bookmarks.find((b) => b.bookmark_id === bookmark.bookmark.bookmark_id);
  assert.ok(stillThere);
  assert.deepEqual(stillThere.time_range, { t_start_seconds: 0, t_end_seconds: 30 });
  assert.equal(stillThere.segment_hashes[segment.segment_id], hashBefore);
});

test("bookmark requires known segment ids", () => {
  const session = makeLiveSession();
  const result = createBookmark(session, {
    segment_ids: ["NOPE"],
    reason_code: "MANUAL_WATCH",
    at: "2026-09-17T10:00:40+08:00",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "UNKNOWN_SEGMENT");
});

// ── A10: Duration / budget boundaries ─────────────────────────────────────────

test("segments beyond max_duration are rejected", () => {
  const session = makeLiveSession({ budget: { max_duration_seconds: 60 } });
  const result = appendSegment(session, {
    t_start_seconds: 60,
    t_end_seconds: 90,
    text: "超過上限",
    kind: "FINAL",
    received_at: "2026-09-17T10:01:30+08:00",
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "MAX_DURATION_EXCEEDED");
});

test("provider budget stop marks a visible gap and blocks further transport", () => {
  const session = makeLiveSession({
    budget: { max_duration_seconds: 7200, max_provider_seconds: 60 },
  });
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 60 });
  const blocked = appendSegment(session, {
    t_start_seconds: 60,
    t_end_seconds: 120,
    text: "超出預算",
    kind: "FINAL",
    received_at: "2026-09-17T10:02:05+08:00",
  });
  assert.equal(blocked.ok, false);
  assert.equal(blocked.reason, "BUDGET_EXCEEDED");
  assert.equal(session.status, "DEGRADED");
  const gap = session.gap_intervals.find((g) => g.reason === "BUDGET_STOP");
  assert.ok(gap, "budget stop must leave a visible gap interval");
  assert.equal(gap.open, true);
});

test("checkSessionBounds ends the session at max_duration", () => {
  const session = makeLiveSession({ budget: { max_duration_seconds: 120 } });
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 60 });
  const result = checkSessionBounds(session, { at: "2026-09-17T10:02:00+08:00" });
  assert.equal(result.bounded, true);
  assert.equal(session.status, "ENDED");
  assert.equal(session.end_reason, "MAX_DURATION");
  const after = appendSegment(session, {
    t_start_seconds: 60,
    t_end_seconds: 90,
    text: "x",
    kind: "FINAL",
    received_at: "2026-09-17T10:02:10+08:00",
  });
  assert.equal(after.ok, false);
});

test("cost estimate is only computed when a price is supplied", () => {
  const session = makeLiveSession({ budget: { price_per_hour_usd: 0.15 } });
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 3600 });
  assert.equal(session.cost.estimated_cost_usd, 0.15);
  const plain = makeLiveSession();
  addSegment(plain, { t_start_seconds: 0, t_end_seconds: 30 });
  assert.equal(plain.cost.estimated_cost_usd, null);
});

// ── A11: End / crash / restart ────────────────────────────────────────────────

test("endLiveSession closes open gaps and ends the session", () => {
  const session = makeLiveSession();
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 60 });
  openGap(session, { at: "2026-09-17T10:02:00+08:00", reason: "STREAM_DISCONNECT" });
  const ended = endLiveSession(session, { at: "2026-09-17T10:30:00+08:00" });
  assert.equal(ended.ok, true);
  assert.equal(session.status, "ENDED");
  assert.equal(session.ended_at, "2026-09-17T10:30:00+08:00");
  assert.equal(session.gap_intervals.every((g) => !g.open), true);
});

test("crashed session keeps a visible open gap; restart supersedes it", () => {
  const session = makeLiveSession();
  addSegment(session, { t_start_seconds: 0, t_end_seconds: 60 });
  const failed = failSession(session, { at: "2026-09-17T10:05:00+08:00", reason: "SESSION_CRASH" });
  assert.equal(failed.ok, true);
  assert.equal(session.status, "FAILED");
  const crashGap = session.gap_intervals.find((g) => g.reason === "SESSION_CRASH");
  assert.ok(crashGap);
  assert.equal(crashGap.open, true);
  const restarted = startLiveSession({
    ...BASE_CONFIG,
    supersedes_session_id: session.session_id,
    started_at: "2026-09-17T10:07:00+08:00",
  });
  assert.equal(restarted.ok, true);
  assert.equal(restarted.session.supersedes_session_id, session.session_id);
  assert.equal(session.status, "FAILED");
});

// ── A12/A13: Reconciliation receipts ──────────────────────────────────────────

function makeEndedSessionWithBookmark() {
  const session = makeLiveSession();
  const segment = addSegment(session, { text: "交通罰鍰上限" });
  createBookmark(session, {
    segment_ids: [segment.segment_id],
    reason_code: "MANUAL_WATCH",
    at: "2026-09-17T10:00:40+08:00",
  });
  endLiveSession(session, { at: "2026-09-17T10:30:00+08:00" });
  return session;
}

const VOD_SOURCE = Object.freeze({
  source_kind: "VOD",
  url: "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14199&pageno=1",
  content_sha256: "a".repeat(64),
  retrieved_at: "2026-09-17T18:00:00+08:00",
});

const MINUTES_SOURCE = Object.freeze({
  source_kind: "MINUTES",
  url: "https://yishi.tccc.gov.tw/meeting-records/example",
  content_sha256: "b".repeat(64),
  retrieved_at: "2026-09-18T09:00:00+08:00",
});

const TOOL_VERSIONS = Object.freeze({ reconciler_version: "live-reconcile/1", parser_version: "tccc-vod/3" });

test("reconciliation requires an ended session", () => {
  const session = makeLiveSession();
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [],
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "SESSION_NOT_ENDED");
});

test("no post-event source leaves every candidate SOURCE_NOT_YET_AVAILABLE", () => {
  const session = makeEndedSessionWithBookmark();
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [],
    matches: [],
  });
  assert.equal(result.ok, true, result.reason);
  assert.equal(result.receipt.outcome, "PENDING_SOURCES");
  assert.equal(result.receipt.entries[0].status, "SOURCE_NOT_YET_AVAILABLE");
  assert.equal(session.status, "RECONCILING");
});

test("confirmed match produces CONFIRMED_BY_OFFICIAL_MEDIA and RECONCILED", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "timestamp:120-150",
      official_text_sha256: "e".repeat(64),
    }],
  });
  assert.equal(result.ok, true, result.reason);
  const entry = result.receipt.entries[0];
  assert.equal(entry.status, "CONFIRMED_BY_OFFICIAL_MEDIA");
  assert.equal(entry.official_locator, "timestamp:120-150");
  assert.match(entry.before_transcript_sha256, /^[0-9a-f]{64}$/);
  assert.match(entry.after_transcript_sha256, /^[0-9a-f]{64}$/);
  assert.equal(result.receipt.tool_versions.reconciler_version, "live-reconcile/1");
  assert.equal(result.receipt.outcome, "COMPLETE");
  assert.equal(session.status, "RECONCILED");
});

test("minutes match produces CONFIRMED_BY_MINUTES", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-18T10:00:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [MINUTES_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "record:abc",
      official_text_sha256: "e".repeat(64),
    }],
  });
  assert.equal(result.ok, true, result.reason);
  assert.equal(result.receipt.entries[0].status, "CONFIRMED_BY_MINUTES");
});

test("unmatched candidate with available sources becomes UNRESOLVED", () => {
  const session = makeEndedSessionWithBookmark();
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [],
  });
  assert.equal(result.ok, true);
  assert.equal(result.receipt.entries[0].status, "UNRESOLVED");
  assert.equal(result.receipt.outcome, "COMPLETE");
});

test("minutes contradiction supersedes ASR wording and emits no formal fact", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-18T10:00:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [MINUTES_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "SUPERSEDED",
      source_index: 0,
      official_locator: "record:abc#p2",
      official_text_sha256: "c".repeat(64),
    }],
  });
  assert.equal(result.ok, true, result.reason);
  const entry = result.receipt.entries[0];
  assert.equal(entry.status, "SUPERSEDED_TRANSCRIPT");
  assert.notEqual(entry.before_transcript_sha256, entry.after_transcript_sha256);
  assert.equal(toFormalEvidence(result.receipt).length, 0);
});

test("dropped false positive is terminal and non-publishable", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [{ bookmark_id: bookmarkId, verdict: "FALSE_POSITIVE", source_index: 0 }],
  });
  assert.equal(result.ok, true);
  assert.equal(result.receipt.entries[0].status, "DROPPED_FALSE_POSITIVE");
  assert.equal(toFormalEvidence(result.receipt).length, 0);
});

test("a later reconcile creates a superseding receipt instead of rewriting", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const first = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [],
    matches: [],
  });
  assert.equal(first.receipt.receipt_no, 1);
  const reopened = reopenReconciliation(session);
  assert.equal(reopened.ok, true, reopened.reason);
  const second = reconcileSession(session, {
    reconciled_at: "2026-09-18T20:00:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "timestamp:120-150",
      official_text_sha256: "e".repeat(64),
    }],
  });
  assert.equal(second.ok, true, second.reason);
  assert.equal(second.receipt.receipt_no, 2);
  assert.equal(second.receipt.supersedes_receipt_id, first.receipt.receipt_id);
  assert.equal(session.receipts.length, 2);
  assert.equal(session.receipts[0].entries[0].status, "SOURCE_NOT_YET_AVAILABLE");
});

test("receipt marks stale when an official post-event source is revised", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "timestamp:120-150",
      official_text_sha256: "e".repeat(64),
    }],
  });
  const receipt = result.receipt;
  const entriesBefore = JSON.stringify(receipt.entries);
  const marked = markReceiptStale(receipt, {
    source_url: VOD_SOURCE.url,
    observed_sha256: "d".repeat(64),
    marked_at: "2026-09-19T08:00:00+08:00",
  });
  assert.equal(marked.stale, true);
  assert.equal(marked.stale_reason, "SOURCE_REVISED");
  assert.equal(JSON.stringify(receipt.entries), entriesBefore);
  const noop = markReceiptStale(receipt, {
    source_url: VOD_SOURCE.url,
    observed_sha256: "a".repeat(64),
    marked_at: "2026-09-19T09:00:00+08:00",
  });
  assert.equal(noop.stale, true);
  assert.equal(noop.stale_reason, "SOURCE_REVISED");
});

test("a stale receipt emits no formal evidence until re-reconciled", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "timestamp:120-150",
      official_text_sha256: "e".repeat(64),
    }],
  });
  assert.equal(toFormalEvidence(result.receipt).length, 1);
  markReceiptStale(result.receipt, {
    source_url: VOD_SOURCE.url,
    observed_sha256: "d".repeat(64),
    marked_at: "2026-09-19T08:00:00+08:00",
  });
  assert.equal(toFormalEvidence(result.receipt).length, 0);
});

test("agenda sources cannot carry a confirmation verdict", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const agenda = {
    source_kind: "AGENDA",
    url: "https://www.tccc.gov.tw/agenda/fixture",
    content_sha256: "f".repeat(64),
  };
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [agenda],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "agenda:item-3",
      official_text_sha256: "e".repeat(64),
    }],
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "AGENDA_NOT_VERDICT_EVIDENCE");
});

test("confirmed matches must hash the official text they checked", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "timestamp:120-150",
    }],
  });
  assert.equal(result.ok, false);
  assert.equal(result.reason, "MISSING_OFFICIAL_TEXT_HASH");
});

// ── A14: Formal evidence only after reconciliation ────────────────────────────

test("toFormalEvidence emits official evidence only for confirmed entries", () => {
  const session = makeEndedSessionWithBookmark();
  const bookmarkId = session.bookmarks[0].bookmark_id;
  const result = reconcileSession(session, {
    reconciled_at: "2026-09-17T18:30:00+08:00",
    tool_versions: TOOL_VERSIONS,
    post_event_sources: [VOD_SOURCE],
    matches: [{
      bookmark_id: bookmarkId,
      verdict: "CONFIRMED",
      source_index: 0,
      official_locator: "timestamp:120-150",
      official_text_sha256: "e".repeat(64),
    }],
  });
  const evidence = toFormalEvidence(result.receipt);
  assert.equal(evidence.length, 1);
  const record = evidence[0];
  assert.equal(record.evidence_type, "ORAL_OFFICIAL");
  assert.equal(record.content_label, "ORAL_OFFICIAL");
  assert.equal(record.verification_status, "AUTO_PASS");
  assert.equal(record.verified_via, "POST_EVENT_RECONCILIATION");
  assert.equal(validateCouncilEvidence(record).valid, true);
  const asCandidate = {
    stable_id: record.evidence_id,
    verification_status: record.verification_status,
    content_disposition: "HOME_CANDIDATE",
    evidence_ids: [record.evidence_id],
    official_url: record.official_url,
    reason_codes: ["COUNCIL_ATTENTION"],
  };
  assert.equal(checkEligibility(asCandidate).eligible, true);
});

// ── A15: Fixture scenarios cover the required regression set ──────────────────

test("all required live-session fixtures exist", () => {
  for (const name of [
    "normal_30min",
    "interim_final_correction",
    "stream_gap",
    "asr_reconnect",
    "speaker_label_drift",
    "budget_stop",
    "crash_restart",
    "vod_later",
    "minutes_contradiction",
    "no_post_event_source",
  ]) {
    assert.ok(LIVE_SESSION_FIXTURES[name], `missing fixture ${name}`);
  }
});

for (const [name, fixture] of Object.entries(LIVE_SESSION_FIXTURES)) {
  test(`fixture ${name} meets its declared expectations`, () => {
    const { session, results } = runLiveFixture(fixture);
    const failures = results.filter((r) => r.ok === false && !r.expected_failure);
    assert.deepEqual(failures, [], `unexpected op failures: ${JSON.stringify(failures)}`);
    const silentlySucceeded = results.filter((r) => r.expected_failure && r.ok !== false);
    assert.deepEqual(silentlySucceeded, [], `expected-failure ops must fail: ${JSON.stringify(silentlySucceeded)}`);
    const expected = fixture.expect;
    if (expected.status) assert.equal(session.status, expected.status, `${name}.status`);
    if (expected.segment_count !== undefined) assert.equal(session.segments.length, expected.segment_count);
    if (expected.gap_count !== undefined) assert.equal(session.gap_intervals.length, expected.gap_count);
    if (expected.receipt_outcome) {
      const last = session.receipts.at(-1);
      assert.equal(last?.outcome, expected.receipt_outcome);
    }
    if (expected.entry_status) {
      const last = session.receipts.at(-1);
      assert.equal(last?.entries[0]?.status, expected.entry_status);
    }
  });
}

test("fixture execution is deterministic (same content hash twice)", () => {
  const a = runLiveFixture(LIVE_SESSION_FIXTURES.normal_30min);
  const b = runLiveFixture(LIVE_SESSION_FIXTURES.normal_30min);
  assert.equal(a.session.content_sha256, b.session.content_sha256);
});
