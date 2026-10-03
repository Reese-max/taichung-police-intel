import assert from "node:assert/strict";
import test from "node:test";

import {
  addLocalCondition,
  cancelLocalCondition,
  emptyLocalConditions,
  getUnreadUpdates,
  loadLocalConditions,
  markLocalRead,
  matchPublishedItems,
  projectLocalConditions,
  saveLocalConditions,
  updateLocalCondition,
  validateLocalConditions,
} from "../lib/local-conditions.js";

const T0 = "2026-09-20T01:00:00+08:00";
const T1 = "2026-09-21T01:00:00+08:00";
const T2 = "2026-09-22T01:00:00+08:00";

function condition(overrides = {}) {
  return {
    condition_id: overrides.condition_id || "cond-1",
    enabled: overrides.enabled !== false,
    filters: overrides.filters || { source_id: ["S-032"], keywords: ["交通"] },
    created_at: overrides.created_at || T0,
    updated_at: overrides.updated_at || T0,
    baseline_generation: overrides.baseline_generation || null,
  };
}

function publishedItem(overrides = {}) {
  return {
    event_id: overrides.event_id || "event-1",
    identity: overrides.identity || "S-032:traffic-1",
    source_id: overrides.source_id || "S-032",
    change_type: overrides.change_type || "NEW",
    headline: overrides.headline || "交通管制公告",
    what_changed: overrides.what_changed || "新設交通管制",
    source_version: overrides.source_version || 1,
    detected_at: overrides.detected_at || T1,
  };
}

function storage(value) {
  const map = new Map();
  if (value !== undefined) map.set("govintel.v2.conditions.v1", JSON.stringify(value));
  return {
    getItem(key) { return map.get(key) || null; },
    setItem(key, value) { map.set(key, value); },
  };
}

test("empty condition state has correct schema", () => {
  const state = emptyLocalConditions();
  assert.equal(state.schema_version, 1);
  assert.equal(state.mode, "LOCAL_CONDITION_TRACKING");
  assert.deepEqual(state.conditions, {});
  assert.deepEqual(state.read_entries, {});
  assert.equal(state.last_updated_at, null);
});

test("invalid state throws on validation", () => {
  assert.throws(() => validateLocalConditions(null), /格式不相容/);
  assert.throws(() => validateLocalConditions({ schema_version: 2 }), /格式不相容/);
  assert.throws(() => validateLocalConditions({ schema_version: 1, mode: "WRONG" }), /格式不相容/);
  assert.throws(() => validateLocalConditions({ schema_version: 1, mode: "LOCAL_CONDITION_TRACKING", conditions: [] }), /結構不完整/);
});

test("add condition is idempotent", () => {
  const state = emptyLocalConditions();
  const first = addLocalCondition(state, condition(), T0);
  assert.equal(Object.keys(first.conditions).length, 1);
  const same = addLocalCondition(first, condition(), T1);
  assert.equal(Object.keys(same.conditions).length, 1);
  assert.equal(same.conditions["cond-1"].updated_at, new Date(T0).toISOString());
});

test("add condition generates id when missing", () => {
  const state = emptyLocalConditions();
  const next = addLocalCondition(state, condition({ condition_id: undefined }), T0);
  const keys = Object.keys(next.conditions);
  assert.ok(keys.length === 1);
  assert.ok(keys[0].startsWith("cond-"));
});

test("update condition modifies filters and timestamps", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition(), T0);
  const updated = updateLocalCondition(added, "cond-1", { filters: { keywords: ["路口"] } }, T1);
  assert.deepEqual(updated.conditions["cond-1"].filters, { keywords: ["路口"] });
  assert.equal(updated.conditions["cond-1"].updated_at, new Date(T1).toISOString());
});

test("cancel condition disables it", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition(), T0);
  const cancelled = cancelLocalCondition(added, "cond-1", T1);
  assert.equal(cancelled.conditions["cond-1"].enabled, false);
  assert.equal(cancelled.conditions["cond-1"].updated_at, new Date(T1).toISOString());
});

test("match published items against active conditions", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition({ filters: { source_id: ["S-032"], keywords: ["交通"] } }), T0);
  const items = [
    publishedItem({ event_id: "e1", headline: "交通管制公告" }),
    publishedItem({ event_id: "e2", source_id: "S-001", headline: "警政新聞" }),
    publishedItem({ event_id: "e3", headline: "道路施工" }),
  ];
  const matches = matchPublishedItems(added, items);
  assert.equal(matches.length, 2);
  assert.ok(matches.some((m) => m.event_id === "e1"));
  assert.ok(matches.some((m) => m.event_id === "e3"));
  assert.ok(!matches.some((m) => m.event_id === "e2"));
});

test("match returns hit reasons for each condition", () => {
  const state = emptyLocalConditions();
  const c1 = addLocalCondition(state, condition({ condition_id: "c1", filters: { source_id: ["S-032"] } }), T0);
  const c2 = addLocalCondition(c1, condition({ condition_id: "c2", filters: { keywords: ["交通"] } }), T0);
  const items = [publishedItem({ event_id: "e1", source_id: "S-032", headline: "交通管制" })];
  const matches = matchPublishedItems(c2, items);
  assert.equal(matches.length, 1);
  assert.equal(matches[0].event_id, "e1");
  assert.ok(Array.isArray(matches[0].hit_condition_ids));
  assert.ok(matches[0].hit_condition_ids.includes("c1"));
  assert.ok(matches[0].hit_condition_ids.includes("c2"));
});

test("same event same change hitting multiple conditions shows once", () => {
  const state = emptyLocalConditions();
  const c1 = addLocalCondition(state, condition({ condition_id: "c1", filters: { source_id: ["S-032"] } }), T0);
  const c2 = addLocalCondition(c1, condition({ condition_id: "c2", filters: { keywords: ["交通"] } }), T0);
  const items = [publishedItem({ event_id: "e1", source_id: "S-032", headline: "交通管制", source_version: 1 })];
  const matches = matchPublishedItems(c2, items);
  assert.equal(matches.length, 1);
});

test("mark local read records version", () => {
  const state = emptyLocalConditions();
  const read = markLocalRead(state, "c1", "e1", 1, T1);
  assert.equal(read.read_entries["e1#v1"].read_at, new Date(T1).toISOString());
  assert.deepEqual(read.read_entries["e1#v1"].condition_ids, ["c1"]);
});

test("marking read again does not duplicate condition ids", () => {
  const state = emptyLocalConditions();
  const r1 = markLocalRead(state, "c1", "e1", 1, T1);
  const r2 = markLocalRead(r1, "c2", "e1", 1, T1);
  assert.deepEqual(r2.read_entries["e1#v1"].condition_ids, ["c1", "c2"]);
});

test("different versions of same event are tracked separately", () => {
  const state = emptyLocalConditions();
  const v1 = markLocalRead(state, "c1", "e1", 1, T1);
  const v2 = markLocalRead(v1, "c1", "e1", 2, T2);
  assert.ok(v2.read_entries["e1#v1"]);
  assert.ok(v2.read_entries["e1#v2"]);
  assert.equal(v2.read_entries["e1#v1"].read_at, new Date(T1).toISOString());
  assert.equal(v2.read_entries["e1#v2"].read_at, new Date(T2).toISOString());
});

test("get unread updates returns only unread items", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition(), T0);
  const read = markLocalRead(added, "cond-1", "e1", 1, T1);
  const items = [
    publishedItem({ event_id: "e1", source_version: 1 }),
    publishedItem({ event_id: "e2", source_version: 1 }),
    publishedItem({ event_id: "e3", source_version: 2 }),
  ];
  const unread = getUnreadUpdates(read, items);
  assert.equal(unread.length, 2);
  assert.ok(unread.some((u) => u.event_id === "e2"));
  assert.ok(unread.some((u) => u.event_id === "e3"));
});

test("get unread updates deduplicates by event+version", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition(), T0);
  const items = [
    publishedItem({ event_id: "e1", source_version: 1 }),
    publishedItem({ event_id: "e1", source_version: 1 }),
  ];
  const unread = getUnreadUpdates(added, items);
  assert.equal(unread.length, 1);
});

test("disabled conditions do not match", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition(), T0);
  const cancelled = cancelLocalCondition(added, "cond-1", T1);
  const items = [publishedItem({ event_id: "e1" })];
  const matches = matchPublishedItems(cancelled, items);
  assert.equal(matches.length, 0);
});

test("project local conditions returns matching items with read status", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition(), T0);
  const read = markLocalRead(added, "cond-1", "e1", 1, T1);
  const items = [
    publishedItem({ event_id: "e1", source_version: 1 }),
    publishedItem({ event_id: "e2", source_version: 1 }),
  ];
  const projected = projectLocalConditions(read, items);
  assert.equal(projected.length, 2);
  assert.equal(projected[0].read, true);
  assert.equal(projected[1].read, false);
  assert.ok(projected[0].hit_condition_ids.includes("cond-1"));
});

test("storage round-trip preserves state", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition(), T0);
  const saved = saveLocalConditions(added, storage());
  const loaded = loadLocalConditions(storage(saved));
  assert.deepEqual(loaded.conditions, added.conditions);
  assert.deepEqual(loaded.read_entries, added.read_entries);
});

test("save throws when storage is unavailable", () => {
  const state = emptyLocalConditions();
  assert.throws(() => saveLocalConditions(state, null), /儲存/);
});

test("load returns empty when no storage", () => {
  const state = loadLocalConditions(null);
  assert.deepEqual(state, emptyLocalConditions());
});

test("load returns empty when key missing", () => {
  const state = loadLocalConditions(storage());
  assert.deepEqual(state, emptyLocalConditions());
});

test("add condition validates required filter fields", () => {
  const state = emptyLocalConditions();
  assert.throws(() => addLocalCondition(state, condition({ filters: { invalid: true } }), T0), /條件格式/);
});

test("update missing condition throws", () => {
  const state = emptyLocalConditions();
  assert.throws(() => updateLocalCondition(state, "missing", {}, T0), /找不到條件/);
});

test("cancel missing condition throws", () => {
  const state = emptyLocalConditions();
  assert.throws(() => cancelLocalCondition(state, "missing", T0), /找不到條件/);
});

test("baseline generation is set on first match", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition({ baseline_generation: null }), T0);
  const matches = matchPublishedItems(added, [publishedItem()], "gen-1");
  assert.equal(added.conditions["cond-1"].baseline_generation, null);
});
