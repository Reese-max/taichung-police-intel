import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  normalizeQueryFilters, queryReplay, replayTrackingItems, requestGateway,
  safeHttpsUrl, trackingFilters, validateReplay,
} from "../lib/public-query.js";

const bundle = JSON.parse(await readFile(new URL("../public/data/public-query-replay.json", import.meta.url), "utf8"));

test("saved projection signature rejects altered content and mixed generations", async () => {
  await validateReplay(bundle);
  const altered = structuredClone(bundle);
  altered.snapshots.R3.events[0].canonical_title = "changed without a receipt";
  await assert.rejects(validateReplay(altered), /hash/);
  const mixed = structuredClone(bundle);
  mixed.snapshots.R1.generation_id = mixed.snapshots.R3.generation_id;
  await assert.rejects(validateReplay(mixed), /hash/);
});

test("same-road different dates/districts remain separate and version difference preserves daily/road", async () => {
  const all = await queryReplay(bundle, { road: "測試路 A 至 B" });
  assert.ok(all.total_matches >= 3);
  const specific = await queryReplay(bundle, { road: "測試路 A 至 B", district: "西屯區（合成）", time_from: "2026-10-08T09:00:00+08:00", time_to: "2026-10-09T17:00:00+08:00" });
  assert.equal(specific.events.length, 1);
  const comparison = specific.events[0].comparison;
  assert.equal(comparison.before.fields.effective_to, "2026-10-07T17:00:00+08:00");
  assert.equal(comparison.after.fields.effective_to, "2026-10-09T17:00:00+08:00");
  assert.equal(comparison.before.fields.daily, "09:00–17:00");
  assert.equal(comparison.before.fields.daily, comparison.after.fields.daily);
  assert.equal(comparison.before.fields.road, comparison.after.fields.road);
  assert.deepEqual(comparison.changed_fields, ["effective_to"]);
});

test("pagination and deterministic receipts bind the exact snapshot and submitted filters", async () => {
  const first = await queryReplay(bundle, { road: "測試路" }, { limit: 1 });
  const repeated = await queryReplay(bundle, { road: "測試路" }, { limit: 1 });
  assert.equal(first.query_id, repeated.query_id);
  assert.equal(first.events[0].public_event_id, repeated.events[0].public_event_id);
  assert.equal(first.has_more, true);
  const next = await queryReplay(bundle, { road: "測試路" }, { cursor: first.next_cursor, limit: 1 });
  assert.notEqual(first.events[0].public_event_id, next.events[0].public_event_id);
  await assert.rejects(queryReplay(bundle, { q: "different" }, { cursor: first.next_cursor }), /分頁/);
  await assert.rejects(queryReplay(bundle, { road: "測試路" }, { snapshot: "R1", cursor: first.next_cursor }), /分頁/);
});

test("Taipei date inputs and tracking handoff retain submitted values without timezone drift", () => {
  const filters = normalizeQueryFilters({ q: " 施工 ", road: "測試路 A 至 B", time_from: "2026-10-08T09:00", time_to: "2026-10-09T17:00" });
  assert.equal(filters.time_from, "2026-10-08T09:00+08:00");
  assert.deepEqual(trackingFilters(filters).keywords, ["施工"]);
  assert.equal(trackingFilters(filters).road, "測試路 A 至 B");
  assert.equal(trackingFilters(filters).time_semantics, "event_overlap");
  assert.throws(() => normalizeQueryFilters({ time_from: "2026-10-09T09:00", time_to: "2026-10-08T09:00" }), /不能早於/);
});

test("no matches, missing dates, failed sources and lost later revisions cannot establish safety", async () => {
  const no = await queryReplay(bundle, { q: "does not exist" });
  assert.equal(no.result_count, 0);
  assert.equal(no.answerable_no_match, false);
  const dated = await queryReplay(bundle, { time_from: "2026-10-08T09:00:00+08:00" });
  assert.ok(!dated.events.some((event) => event.document_id === "DOC-ROAD-AB-UNDATED"));
  const failed = await queryReplay(bundle, {}, { snapshot: "R4" });
  assert.ok(failed.source_gaps.some((gap) => gap.status === "FAILED"));
  const partial = await queryReplay(bundle, {}, { snapshot: "R5" });
  assert.ok(partial.source_gaps.some((gap) => gap.status === "PARTIAL"));
  assert.ok(partial.source_gaps.some((gap) => gap.status === "PENDING_UPDATE"));
  const latest = replayTrackingItems(bundle.snapshots.R3).find((item) => item.event_id === "PE-SYN-DOC-ROAD-AB");
  assert.equal(latest.namespace, "demo:commute");
  assert.ok(latest.source_version >= 2);
});

test("links reject script/credential URLs and synthetic locations never claim official evidence", () => {
  assert.equal(safeHttpsUrl("javascript:alert(1)"), null);
  assert.equal(safeHttpsUrl("https://secret@example.org"), null);
  assert.equal(safeHttpsUrl("https://synthetic.invalid/DOC/UPD"), null);
  assert.equal(safeHttpsUrl("https://www.tccc.gov.tw/"), "https://www.tccc.gov.tw/");
});

test("live query calls the existing Gateway and keeps capability failures distinct from zero rows", async () => {
  let called;
  const fetcher = async (url, options) => {
    called = { url, body: JSON.parse(options.body) };
    return { ok: false, json: async () => ({ error: { code: "CAPABILITY_NOT_AVAILABLE", message: "no configured event store" } }) };
  };
  await assert.rejects(requestGateway("https://gateway.test/query", "search_events", { q: "施工" }, fetcher), (error) => error.code === "CAPABILITY_NOT_AVAILABLE");
  assert.deepEqual(called.body, { tool: "search_events", arguments: { q: "施工" } });
});
