import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  PROVISIONAL_BADGES,
  formatStreamClock,
  projectLocator,
  projectReceiptSummary,
  projectSearchResults,
  projectTimeline,
  validateLiveViewSession,
  createLiveWatch,
} from "../lib/live-session-view.js";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const liveFixture = JSON.parse(
  await readFile(resolve(projectRoot, "tests/fixtures/live-meeting/session-live.json"), "utf8"),
);
const reconciledFixture = JSON.parse(
  await readFile(resolve(projectRoot, "tests/fixtures/live-meeting/session-reconciled.json"), "utf8"),
);

test("every timeline entry stays provisional and gaps are never rendered as empty", () => {
  const entries = projectTimeline(liveFixture);
  assert.deepEqual(
    entries.map((entry) => entry.kind),
    ["SEGMENT", "GAP", "SEGMENT"],
  );
  for (const entry of entries) {
    assert.equal(entry.display_empty, false);
    assert.equal(entry.provisional, true);
  }
  const gap = entries.find((entry) => entry.kind === "GAP");
  assert.ok(gap.gap_id);
  assert.equal(gap.reason, "ASR_DISCONNECT");
  assert.ok(gap.badges.includes("GAP"));
  const segment = entries[0];
  assert.deepEqual(segment.badges, [...PROVISIONAL_BADGES]);
  assert.equal(segment.evidence_state, "LIVE_ASR_PROVISIONAL");
});

test("search results carry provisional locators and never fabricate deep links", () => {
  const result = projectSearchResults(liveFixture, "交通");
  assert.equal(result.provisional, true);
  assert.equal(result.matches.length, 1);
  const hit = result.matches[0];
  assert.equal(hit.evidence_state, "LIVE_ASR_PROVISIONAL");
  assert.equal(hit.locator.kind, "TIME_TEXT");
  assert.equal(hit.locator.url, null);
  assert.equal(hit.locator.reliable_seek, false);
  assert.equal(hit.locator.display, "stream+00:00:00");
  assert.equal(projectSearchResults(liveFixture, "不存在").matches.length, 0);
  assert.throws(() => projectSearchResults(liveFixture, "  "), /empty/i);
});

test("locator uses the verified STREAM_TIME contract only when present", () => {
  const session = {
    ...liveFixture,
    seek: {
      kind: "STREAM_TIME",
      base_url: "https://vod.tccc.gov.tw/live/demo.m3u8",
      offset_seconds: 12,
    },
  };
  const locator = projectLocator(session, session.segments[0].segment_id);
  assert.equal(locator.kind, "STREAM_TIME");
  assert.equal(locator.reliable_seek, true);
  assert.equal(locator.url, "https://vod.tccc.gov.tw/live/demo.m3u8");
  assert.equal(locator.time_seconds, 12);
  const degraded = projectLocator(liveFixture, liveFixture.segments[0].segment_id);
  assert.equal(degraded.kind, "TIME_TEXT");
  assert.equal(degraded.url, null);
});

test("receipt summary surfaces staleness and supersession instead of rewriting", () => {
  const summary = projectReceiptSummary(reconciledFixture);
  assert.equal(summary.length, reconciledFixture.reconciliation_receipts.length);
  const current = summary.find((entry) => entry.current);
  assert.equal(current.outcome, "CONFIRMED_BY_OFFICIAL_MEDIA");
  assert.equal(current.stale, false);
  assert.equal(current.verification_status, "OFFICIAL_RECONCILED");
  assert.notEqual(current.verification_status, "AUTO_PASS");
});

test("formatStreamClock renders zero-padded stream relative time", () => {
  assert.equal(formatStreamClock(0), "stream+00:00:00");
  assert.equal(formatStreamClock(95.4), "stream+00:01:35");
  assert.equal(formatStreamClock(3661), "stream+01:01:01");
});

test("local session import fails closed on source identity and timeline overlap", () => {
  assert.equal(validateLiveViewSession(liveFixture), liveFixture);
  assert.throws(() => validateLiveViewSession({ ...liveFixture, source_id: "S-999" }), /source/i);
  const changed = structuredClone(liveFixture);
  changed.segments[1].start_seconds = 1;
  assert.throws(() => validateLiveViewSession(changed), /overlap/i);
});

test("WATCH preserves the selected provisional revision without editing the session", () => {
  const before = JSON.stringify(liveFixture);
  const segment = liveFixture.segments[0];
  const watch = createLiveWatch(liveFixture, segment.segment_id);
  assert.equal(watch.status, "WATCH");
  assert.equal(watch.provisional, true);
  assert.equal(watch.stream_content_hash, liveFixture.stream_content_hash);
  assert.equal(watch.session_revision, liveFixture.session_revision);
  assert.equal(watch.text, segment.text);
  assert.equal(watch.revision, segment.revision);
  assert.equal(JSON.stringify(liveFixture), before);
});

test("seek URL credentials and invalid offsets degrade to time text", () => {
  for (const seek of [
    { kind: "STREAM_TIME", base_url: "https://user:secret@vod.tccc.gov.tw/a", offset_seconds: 0 },
    { kind: "STREAM_TIME", base_url: "https://vod.tccc.gov.tw/a", offset_seconds: -1 },
    { kind: "STREAM_TIME", base_url: "https://vod.tccc.gov.tw/a", offset_seconds: "invalid" },
  ]) {
    assert.equal(projectLocator({ ...liveFixture, seek }, liveFixture.segments[0]).kind, "TIME_TEXT");
  }
});

test("malformed local JSON is rejected before React renders nested metadata", () => {
  for (const mutate of [
    (state) => { state.provider_usage = { calls: null }; },
    (state) => { state.reconciliation_receipts = [null]; },
    (state) => { state.segments[0].revision_history = [null]; },
    (state) => { state.segments[0].profile_relevance = { matched_terms: [null] }; },
    (state) => { state.status = {}; },
    (state) => { state.budget_seconds = {}; },
    (state) => { state.gap_intervals[0].reason = {}; },
  ]) {
    const state = structuredClone(liveFixture);
    mutate(state);
    assert.throws(() => validateLiveViewSession(state), /Invalid/);
  }
});
