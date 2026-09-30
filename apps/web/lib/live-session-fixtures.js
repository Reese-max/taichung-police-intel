// apps/web/lib/live-session-fixtures.js
// Executable fixtures for the live-meeting provisional session contract.
// Each scenario is frozen input data replayed through the real live-session
// API by runLiveFixture(); tests assert the declared expectations.

import {
  appendSegment,
  beginIngest,
  checkSessionBounds,
  closeGap,
  createBookmark,
  endLiveSession,
  failSession,
  openGap,
  reconcileSession,
  reopenReconciliation,
  reviseSegment,
  startLiveSession,
} from "./live-session.js";

const BASE_CONFIG = Object.freeze({
  opt_in: true,
  requested_by: "fixture-operator@example.test",
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

const TOOL_VERSIONS = Object.freeze({ reconciler_version: "live-reconcile/1", parser_version: "tccc-vod/3" });

const VOD_SOURCE = Object.freeze({
  source_kind: "VOD",
  url: "https://vod.tccc.gov.tw/wb_news02.asp?url=92&ano=14199&pageno=1",
  content_sha256: "a".repeat(64),
  retrieved_at: "2026-09-17T18:00:00+08:00",
});

const MINUTES_SOURCE = Object.freeze({
  source_kind: "MINUTES",
  url: "https://yishi.tccc.gov.tw/meeting-records/fixture-2026-09-17",
  content_sha256: "b".repeat(64),
  retrieved_at: "2026-09-18T09:00:00+08:00",
});

export const LIVE_SESSION_FIXTURES = Object.freeze({
  normal_30min: Object.freeze({
    description: "30-minute session, clean ingest, operator stop",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 600], text: "警政預算與決算報告", at: "2026-09-17T10:10:00+08:00" },
      { op: "segment", t: [600, 1200], text: "交通執法與事故防制檢討", at: "2026-09-17T10:20:00+08:00" },
      { op: "segment", t: [1200, 1800], text: "失聯移工查緝獎勵", at: "2026-09-17T10:30:00+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
    ]),
    expect: Object.freeze({ status: "ENDED", segment_count: 3, gap_count: 0 }),
  }),

  interim_final_correction: Object.freeze({
    description: "provider revises interim text to a different final text",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 30], text: "交通罰", kind: "INTERIM", at: "2026-09-17T10:00:35+08:00" },
      { op: "revise", segment: "last", text: "交通罰鍰與舉發", kind: "FINAL", at: "2026-09-17T10:00:38+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
    ]),
    expect: Object.freeze({ status: "ENDED", segment_count: 1, gap_count: 0 }),
  }),

  stream_gap: Object.freeze({
    description: "90-second stream disconnect; ingest pauses then resumes",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 300], text: "警政預算質詢", at: "2026-09-17T10:05:00+08:00" },
      { op: "open_gap", reason: "STREAM_DISCONNECT", at: "2026-09-17T10:05:00+08:00" },
      { op: "segment", t: [300, 360], text: "斷線期間不應有內容", at: "2026-09-17T10:05:40+08:00", expected_failure: true },
      { op: "close_gap", at: "2026-09-17T10:06:30+08:00" },
      { op: "segment", t: [390, 600], text: "重連後恢復辨識", at: "2026-09-17T10:10:05+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
    ]),
    expect: Object.freeze({ status: "ENDED", segment_count: 2, gap_count: 1 }),
  }),

  asr_reconnect: Object.freeze({
    description: "ASR outage gap; a segment may not claim the dead interval",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 240], text: "議程報告", at: "2026-09-17T10:04:05+08:00" },
      { op: "open_gap", reason: "ASR_OUTAGE", at: "2026-09-17T10:04:00+08:00" },
      { op: "close_gap", at: "2026-09-17T10:05:30+08:00" },
      { op: "segment", t: [240, 330], text: "覆蓋缺口", at: "2026-09-17T10:06:00+08:00", expected_failure: true },
      { op: "segment", t: [330, 600], text: "重連後內容", at: "2026-09-17T10:10:05+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
    ]),
    expect: Object.freeze({ status: "ENDED", segment_count: 2, gap_count: 1 }),
  }),

  speaker_label_drift: Object.freeze({
    description: "provider speaker label drifts between interim and final",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 30], text: "本席質詢警政", kind: "INTERIM", speaker_label: "SPEAKER_0", at: "2026-09-17T10:00:35+08:00" },
      { op: "revise", segment: "last", text: "本席質詢警政預算", kind: "FINAL", speaker_label: "SPEAKER_1", at: "2026-09-17T10:00:38+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
    ]),
    expect: Object.freeze({ status: "ENDED", segment_count: 1, gap_count: 0 }),
  }),

  budget_stop: Object.freeze({
    description: "provider-seconds budget exhausts; transport blocked, gap visible",
    config: Object.freeze({ budget: { max_duration_seconds: 7200, max_provider_seconds: 60 } }),
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 60], text: "預算內辨識", at: "2026-09-17T10:01:05+08:00" },
      { op: "segment", t: [60, 120], text: "超出預算", at: "2026-09-17T10:02:05+08:00", expected_failure: true },
    ]),
    expect: Object.freeze({ status: "DEGRADED", segment_count: 1, gap_count: 1 }),
  }),

  crash_restart: Object.freeze({
    description: "session crashes mid-meeting; coverage gap stays open",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 60], text: "當機前內容", at: "2026-09-17T10:01:05+08:00" },
      { op: "fail", reason: "SESSION_CRASH", at: "2026-09-17T10:05:00+08:00" },
    ]),
    expect: Object.freeze({ status: "FAILED", segment_count: 1, gap_count: 1 }),
  }),

  vod_later: Object.freeze({
    description: "official VOD published after the meeting; pending then confirmed",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 120], text: "警政預算質詢", at: "2026-09-17T10:02:05+08:00" },
      { op: "bookmark", segment: "last", reason_code: "MANUAL_WATCH", at: "2026-09-17T10:03:00+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
      {
        op: "reconcile",
        at: "2026-09-17T18:30:00+08:00",
        post_event_sources: [],
        matches: [],
        tool_versions: TOOL_VERSIONS,
      },
      { op: "reopen" },
      {
        op: "reconcile",
        at: "2026-09-18T20:00:00+08:00",
        post_event_sources: [VOD_SOURCE],
        matches: [{
          bookmark: "last",
          verdict: "CONFIRMED",
          source_index: 0,
          official_locator: "timestamp:0-120",
          official_text_sha256: "e".repeat(64),
        }],
        tool_versions: TOOL_VERSIONS,
      },
    ]),
    expect: Object.freeze({
      status: "RECONCILED",
      segment_count: 1,
      gap_count: 0,
      receipt_outcome: "COMPLETE",
      entry_status: "CONFIRMED_BY_OFFICIAL_MEDIA",
    }),
  }),

  minutes_contradiction: Object.freeze({
    description: "official minutes contradict the ASR wording; official wins",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 120], text: "罰鍰上限五萬元", at: "2026-09-17T10:02:05+08:00" },
      { op: "bookmark", segment: "last", reason_code: "MANUAL_WATCH", at: "2026-09-17T10:03:00+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
      {
        op: "reconcile",
        at: "2026-09-18T10:00:00+08:00",
        post_event_sources: [MINUTES_SOURCE],
        matches: [{
          bookmark: "last",
          verdict: "SUPERSEDED",
          source_index: 0,
          official_locator: "record:fixture#p2",
          official_text_sha256: "c".repeat(64),
        }],
        tool_versions: TOOL_VERSIONS,
      },
    ]),
    expect: Object.freeze({
      status: "RECONCILED",
      segment_count: 1,
      receipt_outcome: "COMPLETE",
      entry_status: "SUPERSEDED_TRANSCRIPT",
    }),
  }),

  no_post_event_source: Object.freeze({
    description: "no official post-event source yet; candidates stay pending",
    script: Object.freeze([
      { op: "begin", at: "2026-09-17T10:00:05+08:00" },
      { op: "segment", t: [0, 120], text: "警政預算質詢", at: "2026-09-17T10:02:05+08:00" },
      { op: "bookmark", segment: "last", reason_code: "FOLLOW_UP", at: "2026-09-17T10:03:00+08:00" },
      { op: "end", at: "2026-09-17T10:30:00+08:00" },
      {
        op: "reconcile",
        at: "2026-09-17T18:30:00+08:00",
        post_event_sources: [],
        matches: [],
        tool_versions: TOOL_VERSIONS,
      },
    ]),
    expect: Object.freeze({
      status: "RECONCILING",
      segment_count: 1,
      receipt_outcome: "PENDING_SOURCES",
      entry_status: "SOURCE_NOT_YET_AVAILABLE",
    }),
  }),
});

/**
 * Replays a fixture script through the real session API.
 * `segment: "last"` / `bookmark: "last"` resolve to the most recent entity.
 * @param {object} fixture
 * @returns {{session: object|null, results: object[]}}
 */
export function runLiveFixture(fixture) {
  const config = { ...BASE_CONFIG, ...(fixture.config || {}), budget: { ...BASE_CONFIG.budget, ...(fixture.config?.budget || {}) } };
  const started = startLiveSession(config);
  if (!started.ok) {
    return { session: null, results: [{ op: "start", ok: false, reason: started.reason, expected_failure: false }] };
  }
  const session = started.session;
  const results = [];
  let lastSegmentId = null;
  let lastGapId = null;
  let lastBookmarkId = null;

  const record = (op, result) => {
    results.push({ op: op.op, ok: result.ok === true, reason: result.reason ?? null, expected_failure: op.expected_failure === true });
  };

  for (const op of fixture.script) {
    switch (op.op) {
      case "begin":
        record(op, beginIngest(session, { at: op.at }));
        break;
      case "segment": {
        const result = appendSegment(session, {
          t_start_seconds: op.t[0],
          t_end_seconds: op.t[1],
          text: op.text,
          kind: op.kind ?? "FINAL",
          speaker_label: op.speaker_label ?? null,
          provider_confidence: op.confidence ?? null,
          language: "zh",
          received_at: op.at,
        });
        if (result.ok) lastSegmentId = result.segment.segment_id;
        record(op, result);
        break;
      }
      case "revise": {
        const target = op.segment === "last" ? lastSegmentId : op.segment;
        record(op, reviseSegment(session, target, {
          text: op.text,
          kind: op.kind ?? "FINAL",
          speaker_label: op.speaker_label,
          received_at: op.at,
        }));
        break;
      }
      case "open_gap": {
        const result = openGap(session, { at: op.at, reason: op.reason });
        if (result.ok) lastGapId = result.gap.gap_id;
        record(op, result);
        break;
      }
      case "close_gap":
        record(op, closeGap(session, lastGapId, { at: op.at }));
        break;
      case "bookmark": {
        const target = op.segment === "last" ? lastSegmentId : op.segment;
        const result = createBookmark(session, {
          segment_ids: [target],
          reason_code: op.reason_code,
          note: op.note,
          at: op.at,
        });
        if (result.ok) lastBookmarkId = result.bookmark.bookmark_id;
        record(op, result);
        break;
      }
      case "end":
        record(op, endLiveSession(session, { at: op.at }));
        break;
      case "fail":
        record(op, failSession(session, { at: op.at, reason: op.reason }));
        break;
      case "bounds":
        record(op, checkSessionBounds(session, { at: op.at }));
        break;
      case "reopen":
        record(op, reopenReconciliation(session));
        break;
      case "reconcile":
        record(op, reconcileSession(session, {
          reconciled_at: op.at,
          tool_versions: op.tool_versions,
          post_event_sources: op.post_event_sources,
          matches: (op.matches || []).map((match) => ({
            ...match,
            bookmark_id: match.bookmark === "last" ? lastBookmarkId : match.bookmark_id ?? match.bookmark,
          })),
        }));
        break;
      default:
        results.push({ op: op.op, ok: false, reason: "UNKNOWN_OP", expected_failure: false });
    }
  }
  return { session, results };
}
