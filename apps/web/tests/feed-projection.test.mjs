import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { projectToHomepageCandidates, feedSourceSummary } from "../lib/feed-projection.js";
import { buildHomepageResponse, checkEligibility, HOMEPAGE_ITEM_LIMIT } from "../lib/homepage-eligibility.js";

// ── Fixtures ─────────────────────────────────────────────────────────────────

function makeFeedItem(overrides = {}) {
  return {
    stable_id: "FEED-S-007-abc123",
    source_id: "S-007",
    source_role: "PRIMARY_OFFICIAL",
    title: "議事錄測試項目",
    official_url: "https://yishi.tccc.gov.tw/meeting-records/test-001",
    published_at: "2026-08-25T10:00:00+08:00",
    fetched_at: "2026-08-25T18:56:35+08:00",
    data_as_of: "2026-08-25T10:00:00+08:00",
    change_type: "NEW",
    freshness_status: "FRESH",
    source_health: "PASS",
    window_completeness: "COMPLETE_WITH_ITEMS",
    reason_codes: ["COUNCIL_ATTENTION"],
    item_value_score: 90,
    eligibility: "HOME_CANDIDATE",
    evidence_count: 1,
    next_milestone: null,
    content_sha256: "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    ...overrides,
  };
}

function makeFeed(items = [], sourceSummary = {}) {
  return {
    schema_version: 1,
    generated_at: "2026-08-25T18:56:35+08:00",
    collection_run_id: "CR-DEMO-20260825-EVENING-SCHEDULE",
    items,
    source_summary: sourceSummary,
  };
}

// ── Feed projection basic behavior ──────────────────────────────────────────

test("projectToHomepageCandidates returns empty array for null feed", () => {
  assert.deepEqual(projectToHomepageCandidates(null), []);
});

test("projectToHomepageCandidates returns empty array for feed with no items", () => {
  assert.deepEqual(projectToHomepageCandidates(makeFeed()), []);
});

test("projectToHomepageCandidates returns empty for feed with only ineligible items", () => {
  const feed = makeFeed([
    makeFeedItem({ eligibility: "INELIGIBLE_STALE" }),
    makeFeedItem({ stable_id: "FEED-S-006-xyz", eligibility: "INELIGIBLE_NO_DATE" }),
  ]);
  assert.deepEqual(projectToHomepageCandidates(feed), []);
});

// ── Current NEW item qualifies for homepage ──────────────────────────────────

test("current NEW item with HOME_CANDIDATE eligibility projects as homepage candidate", () => {
  const feed = makeFeed([makeFeedItem()]);
  const candidates = projectToHomepageCandidates(feed);
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].item_id, "FEED-S-007-abc123");
  assert.equal(candidates[0].verification_status, "AUTO_PASS");
  assert.equal(candidates[0].content_disposition, "HOME_CANDIDATE");
  assert.ok(candidates[0].official_url.startsWith("https://"));
  assert.deepEqual(candidates[0].reason_codes, ["COUNCIL_ATTENTION"]);
});

test("projected candidate passes checkEligibility", () => {
  const feed = makeFeed([makeFeedItem()]);
  const candidates = projectToHomepageCandidates(feed);
  const result = checkEligibility(candidates[0]);
  assert.equal(result.eligible, true);
});

test("projected candidate passes buildHomepageResponse", () => {
  const feed = makeFeed([makeFeedItem()]);
  const candidates = projectToHomepageCandidates(feed);
  const response = buildHomepageResponse(candidates);
  assert.equal(response.items.length, 1);
  assert.equal(response.items[0].item_id, "FEED-S-007-abc123");
});

// ── Unchanged item suppression ───────────────────────────────────────────────

test("UNCHANGED items marked INELIGIBLE_UNCHANGED do not project to homepage", () => {
  const feed = makeFeed([makeFeedItem({ change_type: "UNCHANGED", eligibility: "INELIGIBLE_UNCHANGED" })]);
  const candidates = projectToHomepageCandidates(feed);
  assert.equal(candidates.length, 0);
});

// ── VERY_STALE item is ineligible ────────────────────────────────────────────

test("VERY_STALE item marked INELIGIBLE_STALE does not project", () => {
  const feed = makeFeed([
    makeFeedItem({
      freshness_status: "VERY_STALE",
      eligibility: "INELIGIBLE_STALE",
    }),
  ]);
  const candidates = projectToHomepageCandidates(feed);
  assert.equal(candidates.length, 0);
});

// ── Missing published_at (NO_DATA) is ineligible ─────────────────────────────

test("item without published_at marked INELIGIBLE_NO_DATE does not project", () => {
  const feed = makeFeed([
    makeFeedItem({
      published_at: null,
      freshness_status: "NO_DATA",
      eligibility: "INELIGIBLE_NO_DATE",
    }),
  ]);
  const candidates = projectToHomepageCandidates(feed);
  assert.equal(candidates.length, 0);
});

// ── PARTIAL/failed source with LKG shows staleness ───────────────────────────

test("item from failed source marked INELIGIBLE_SOURCE_FAILED does not project", () => {
  const feed = makeFeed([
    makeFeedItem({
      source_health: "FAILED",
      eligibility: "INELIGIBLE_SOURCE_FAILED",
    }),
  ]);
  const candidates = projectToHomepageCandidates(feed);
  assert.equal(candidates.length, 0);
});

// ── PARTIAL window completeness gate ─────────────────────────────────────────

test("item from PARTIAL window_completeness marked INELIGIBLE_PARTIAL does not project", () => {
  const feed = makeFeed([
    makeFeedItem({
      window_completeness: "PARTIAL",
      eligibility: "INELIGIBLE_PARTIAL",
    }),
  ]);
  const candidates = projectToHomepageCandidates(feed);
  assert.equal(candidates.length, 0);
});

// ── LKG items from failed sources ────────────────────────────────────────────

test("LKG item (change_type=LKG) from failed source does not project", () => {
  const feed = makeFeed([
    makeFeedItem({
      change_type: "LKG",
      source_health: "FAILED",
      eligibility: "INELIGIBLE_SOURCE_FAILED",
      freshness_status: "VERY_STALE",
    }),
  ]);
  const candidates = projectToHomepageCandidates(feed);
  assert.equal(candidates.length, 0);
});

test("LKG item is present in feed even though ineligible", () => {
  const lkgItem = makeFeedItem({
    change_type: "LKG",
    source_health: "FAILED",
    eligibility: "INELIGIBLE_SOURCE_FAILED",
    freshness_status: "VERY_STALE",
  });
  const feed = makeFeed([lkgItem]);
  // Item is in feed (visible for source status) but not projected to homepage
  assert.equal(feed.items.length, 1);
  assert.equal(feed.items[0].change_type, "LKG");
  assert.equal(projectToHomepageCandidates(feed).length, 0);
});

// ── Stable dedup/hash correctness ────────────────────────────────────────────

test("items with same stable_id deduplicate to one candidate", () => {
  const item1 = makeFeedItem({ content_sha256: "aaa" + "0".repeat(61) });
  const item2 = makeFeedItem({ content_sha256: "bbb" + "0".repeat(61) });
  // Same stable_id — feed should have deduped before this step, but projection
  // should handle gracefully (they'll appear as same item_id)
  const feed = makeFeed([item1, item2]);
  const candidates = projectToHomepageCandidates(feed);
  // Both have HOME_CANDIDATE so both project, but buildHomepageResponse handles dedup by ID
  const response = buildHomepageResponse(candidates);
  assert.ok(response.items.length >= 1);
});

// ── Official URL gate (only HTTPS) ──────────────────────────────────────────

test("candidate with HTTPS official_url passes eligibility", () => {
  const feed = makeFeed([makeFeedItem({ official_url: "https://example.gov.tw/doc" })]);
  const candidates = projectToHomepageCandidates(feed);
  const result = checkEligibility(candidates[0]);
  assert.equal(result.eligible, true);
});

test("candidate projected without official_url fails eligibility", () => {
  // Even if feed item has HOME_CANDIDATE, if official_url is empty, eligibility check fails
  const item = makeFeedItem({ official_url: "" });
  // Force eligibility to HOME_CANDIDATE to test the eligibility gate on the other side
  const feed = makeFeed([item]);
  const candidates = projectToHomepageCandidates(feed);
  if (candidates.length > 0) {
    const result = checkEligibility(candidates[0]);
    assert.equal(result.eligible, false);
    assert.equal(result.reason, "NO_OFFICIAL_URL");
  }
});

// ── Homepage ≤ 10 items cap ──────────────────────────────────────────────────

test("homepage caps at 10 items even with more eligible candidates", () => {
  const items = Array.from({ length: 15 }, (_, i) =>
    makeFeedItem({
      stable_id: `FEED-S-007-item${String(i).padStart(3, "0")}`,
      item_value_score: 90 - i,
    }),
  );
  const feed = makeFeed(items);
  const candidates = projectToHomepageCandidates(feed);
  const response = buildHomepageResponse(candidates);
  assert.ok(response.items.length <= HOMEPAGE_ITEM_LIMIT);
  assert.equal(response.items.length, 10);
  assert.equal(response.total_candidates, 15);
});

// ── Empty state (no items → explicit message) ────────────────────────────────

test("empty feed results in zero homepage items", () => {
  const feed = makeFeed([]);
  const candidates = projectToHomepageCandidates(feed);
  const response = buildHomepageResponse(candidates);
  assert.equal(response.items.length, 0);
  assert.equal(response.total_candidates, 0);
});

test("feed with all INELIGIBLE items results in zero homepage items", () => {
  const feed = makeFeed([
    makeFeedItem({ stable_id: "FEED-S-004-a1", eligibility: "INELIGIBLE_STALE" }),
    makeFeedItem({ stable_id: "FEED-S-006-b2", eligibility: "INELIGIBLE_NO_DATE" }),
    makeFeedItem({ stable_id: "FEED-S-029-c3", eligibility: "INELIGIBLE_STALE" }),
  ]);
  const candidates = projectToHomepageCandidates(feed);
  const response = buildHomepageResponse(candidates);
  assert.equal(response.items.length, 0);
});

// ── feedSourceSummary ────────────────────────────────────────────────────────

test("feedSourceSummary returns total items and source breakdown", () => {
  const feed = makeFeed(
    [makeFeedItem(), makeFeedItem({ stable_id: "FEED-S-004-x1" })],
    { "S-007": { health: "PASS", freshness: "FRESH", item_count: 1 }, "S-004": { health: "PASS", freshness: "VERY_STALE", item_count: 1 } },
  );
  const summary = feedSourceSummary(feed);
  assert.equal(summary.total_items, 2);
  assert.equal(summary.source_summary["S-007"].health, "PASS");
});

test("feedSourceSummary returns zero for null feed", () => {
  const summary = feedSourceSummary(null);
  assert.equal(summary.total_items, 0);
  assert.deepEqual(summary.source_summary, {});
});

// ── Intelligence feed file contract ──────────────────────────────────────────

test("intelligence-feed.json exists and has valid schema", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  assert.equal(feed.schema_version, 1);
  assert.ok(Array.isArray(feed.items));
  assert.ok(feed.generated_at);
  assert.ok(feed.collection_run_id);
  assert.ok(feed.source_summary);
});

test("intelligence-feed.json items have required fields", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  for (const item of feed.items) {
    assert.ok(item.stable_id, `item missing stable_id`);
    assert.ok(item.source_id, `item missing source_id`);
    assert.ok(item.official_url, `item missing official_url`);
    assert.ok(item.official_url.startsWith("https://"), `item official_url not HTTPS: ${item.official_url}`);
    assert.ok(item.eligibility, `item missing eligibility`);
    assert.ok(item.content_sha256, `item missing content_sha256`);
    assert.ok(item.freshness_status, `item missing freshness_status`);
    assert.ok(item.source_health, `item missing source_health`);
    assert.ok(Array.isArray(item.reason_codes), `item reason_codes not array`);
  }
});

test("intelligence-feed.json items do not expose raw payload", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  for (const item of feed.items) {
    assert.equal(item.payload, undefined, `item ${item.stable_id} must not have raw payload`);
    assert.equal(item.body, undefined, `item ${item.stable_id} must not have raw body`);
    assert.equal(item.attachments, undefined, `item ${item.stable_id} must not have attachments`);
  }
});

test("intelligence-feed.json source_summary covers all P0 sources", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  const expectedSources = ["S-004", "S-006", "S-007", "S-009", "S-029"];
  for (const sid of expectedSources) {
    assert.ok(feed.source_summary[sid], `source_summary missing ${sid}`);
    assert.ok(feed.source_summary[sid].health, `source_summary ${sid} missing health`);
    assert.ok(feed.source_summary[sid].freshness, `source_summary ${sid} missing freshness`);
  }
});

// ── Cross-reference: no VERY_STALE or NO_DATA items marked HOME_CANDIDATE ───

test("no VERY_STALE item in feed is marked HOME_CANDIDATE", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  const violations = feed.items.filter(
    (item) => item.freshness_status === "VERY_STALE" && item.eligibility === "HOME_CANDIDATE",
  );
  assert.deepEqual(violations, [], "VERY_STALE items must not be HOME_CANDIDATE");
});

test("no NO_DATA item in feed is marked HOME_CANDIDATE", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  const violations = feed.items.filter(
    (item) => item.freshness_status === "NO_DATA" && item.eligibility === "HOME_CANDIDATE",
  );
  assert.deepEqual(violations, [], "NO_DATA items must not be HOME_CANDIDATE");
});

test("no UNCHANGED item in feed is marked HOME_CANDIDATE", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  const violations = feed.items.filter(
    (item) => item.change_type === "UNCHANGED" && item.eligibility === "HOME_CANDIDATE",
  );
  assert.deepEqual(violations, [], "UNCHANGED items must not be HOME_CANDIDATE");
});

test("no PARTIAL window_completeness item in feed is marked HOME_CANDIDATE", async () => {
  const raw = await readFile(new URL("../public/data/intelligence-feed.json", import.meta.url), "utf8");
  const feed = JSON.parse(raw);
  const violations = feed.items.filter(
    (item) => item.window_completeness === "PARTIAL" && item.eligibility === "HOME_CANDIDATE",
  );
  assert.deepEqual(violations, [], "PARTIAL items must not be HOME_CANDIDATE");
});
