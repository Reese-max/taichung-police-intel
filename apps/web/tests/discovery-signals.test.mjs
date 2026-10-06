import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { isValidDiscoverySignals } from "../lib/discovery-signals.js";

const dataUrl = new URL("../public/data/discovery-signals.json", import.meta.url);
const componentUrl = new URL("../components/DiscoverySignalsPanel.js", import.meta.url);
const dashboardUrl = new URL("../components/V2DailyDashboard.js", import.meta.url);

const FORBIDDEN_CONFIRMED_WORDING = /已證實|官方指出/;

test("checked-in discovery signals payload is fixture-only and separates confirmed from pending", async () => {
  const payload = JSON.parse(await readFile(dataUrl, "utf8"));
  assert.equal(payload.schema_version, 1);
  assert.equal(payload.kind, "GOVINTEL_DISCOVERY_SIGNALS");
  assert.equal(payload.status, "FIXTURE_ONLY");
  assert.equal(payload.production_verified, false);
  assert.ok(isValidDiscoverySignals(payload));
  assert.ok(payload.confirmed.length > 0);
  for (const row of payload.confirmed) {
    assert.equal(row.verification_status, "VERIFIED_OFFICIAL");
    assert.ok(row.official_document_versions.length > 0);
    assert.ok(row.official_urls.every((url) => url.startsWith("https://")));
  }
  assert.ok(payload.pending.length <= payload.limits.pending_max);
  for (const row of payload.pending) {
    assert.notEqual(row.verification_status, "VERIFIED_OFFICIAL");
    assert.doesNotMatch(row.note, FORBIDDEN_CONFIRMED_WORDING);
  }
  assert.equal(payload.counts.confirmed_count, payload.confirmed.length);
  assert.equal(payload.counts.pending_shown, payload.pending.length);
  assert.ok(payload.counts.pending_total >= payload.counts.pending_shown);
});

test("validator fails closed on malformed or over-claiming payloads", () => {
  const base = {
    schema_version: 1,
    kind: "GOVINTEL_DISCOVERY_SIGNALS",
    status: "FIXTURE_ONLY",
    production_verified: false,
    generated_at: "2026-09-21T00:00:00Z",
    upstream: {
      feed_id: "taiwan-intel-dashboard:govintel-discovery",
      generation_id: "gdf-abc",
      generated_at: "2026-09-21T00:00:00Z",
      operating_state: "ACTIVE",
      current: true,
      signal_mode: "CURRENT",
      content_hash: null,
    },
    confirmed: [],
    pending: [],
    limits: { pending_max: 5 },
    counts: {
      feed_item_count: 0,
      relevant_count: 0,
      confirmed_count: 0,
      pending_total: 0,
      pending_shown: 0,
      expired_count: 0,
      conflict_count: 0,
      no_official_match_count: 0,
      new_public_event_count: 0,
      existing_event_match_count: 0,
      canonical_change_count: 0,
      truncated: false,
    },
  };
  assert.ok(isValidDiscoverySignals(base));
  assert.equal(isValidDiscoverySignals(null), false);
  assert.equal(isValidDiscoverySignals({}), false);
  assert.equal(isValidDiscoverySignals({ ...base, kind: "OTHER" }), false);
  assert.equal(isValidDiscoverySignals({ ...base, schema_version: 2 }), false);
  assert.equal(
    isValidDiscoverySignals({ ...base, upstream: { ...base.upstream, signal_mode: "PRODUCTION" } }),
    false,
  );
  assert.equal(
    isValidDiscoverySignals({ ...base, status: "LIVE", upstream: { ...base.upstream, current: false, operating_state: "RESTORING", signal_mode: "CANARY_ONLY" } }),
    false,
    "a non-current upstream must never render as a live production signal",
  );
  const confirmedWithoutEvidence = {
    ...base,
    confirmed: [{
      candidate_id: "gd-1111111111111111",
      headline: "x",
      verification_status: "VERIFIED_OFFICIAL",
      authority: "media",
      official_document_versions: [],
      official_urls: [],
      public_event_ids: [],
      canonical_write: false,
    }],
  };
  confirmedWithoutEvidence.counts = { ...base.counts, confirmed_count: 1 };
  assert.equal(isValidDiscoverySignals(confirmedWithoutEvidence), false);
  const mediaWithOfficialPointer = structuredClone(confirmedWithoutEvidence);
  mediaWithOfficialPointer.confirmed[0].official_document_versions = ["official:v1"];
  mediaWithOfficialPointer.confirmed[0].official_urls = ["https://www.traffic.taichung.gov.tw/fictional/pointer"];
  assert.equal(isValidDiscoverySignals(mediaWithOfficialPointer), false, "official pointers do not promote a media claim");
  const overLimit = {
    ...base,
    pending: Array.from({ length: 6 }, (_, index) => ({
      candidate_id: `gd-${String(index).padStart(16, "0")}`,
      headline: "x",
      verification_status: "NO_OFFICIAL_MATCH",
      authority: "media",
      note: "尚未找到官方確認",
      discovered_at: "2026-09-20T00:00:00Z",
      canonical_write: false,
    })),
  };
  overLimit.counts = { ...base.counts, pending_shown: 6, pending_total: 6 };
  assert.equal(isValidDiscoverySignals(overLimit), false);
  const mediaClaim = {
    ...base,
    pending: [{
      candidate_id: "gd-0000000000000000",
      headline: "x",
      verification_status: "VERIFIED_OFFICIAL",
      authority: "media",
      note: "尚未找到官方確認",
      discovered_at: "2026-09-20T00:00:00Z",
      canonical_write: false,
    }],
  };
  mediaClaim.counts = { ...base.counts, pending_shown: 1, pending_total: 1 };
  assert.equal(isValidDiscoverySignals(mediaClaim), false);
});

test("component keeps pending wording separate from confirmed evidence", async () => {
  const [component, dashboard] = await Promise.all([
    readFile(componentUrl, "utf8"),
    readFile(dashboardUrl, "utf8"),
  ]);
  assert.match(dashboard, /DiscoverySignalsPanel/);
  assert.match(component, /discovery-signals\.json/);
  assert.match(component, /已確認/);
  assert.match(component, /待官方確認/);
  assert.match(component, /尚未找到官方確認/);
  assert.match(component, /HISTORICAL_REPLAY_ONLY/);
  assert.match(component, /CANARY_ONLY/);
  assert.match(component, /GAP_VISIBLE/);
  assert.match(component, /isValidDiscoverySignals/);
  assert.doesNotMatch(component, FORBIDDEN_CONFIRMED_WORDING);
});
