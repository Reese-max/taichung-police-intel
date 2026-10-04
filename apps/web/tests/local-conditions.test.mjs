import assert from "node:assert/strict";
import test from "node:test";

import {
  addLocalCondition,
  clearLocalConditions,
  conditionDatasetStatus,
  cancelLocalCondition,
  emptyLocalConditions,
  getUnreadUpdates,
  loadLocalConditions,
  initializeConditionBaselines,
  markDisplayedUpdatesRead,
  markLocalRead,
  matchPublishedItems,
  projectLocalConditions,
  saveLocalConditions,
  saveLocalConditionRequest,
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

test("projection does not mutate baseline state", () => {
  const state = emptyLocalConditions();
  const added = addLocalCondition(state, condition({ baseline_generation: null }), T0);
  const matches = matchPublishedItems(added, [publishedItem()], "gen-1");
  assert.equal(added.conditions["cond-1"].baseline_generation, null);
});

test("equivalent conditions deduplicate independently of ID, order, and keyword case", () => {
  const first = addLocalCondition(emptyLocalConditions(), { filters: { keywords: ["ROAD", "交通"], source_id: ["S-032", "S-001"] } }, T0);
  const second = addLocalCondition(first, { condition_id: "another", filters: { source_id: ["S-001", "S-032"], keywords: ["交通", "road"] } }, T1);
  assert.deepEqual(second, first);
});

test("initial data stays baseline, later material version is unread, and layout hashes do not notify", () => {
  const base = addLocalCondition(emptyLocalConditions(), condition(), T0);
  const first = initializeConditionBaselines(base, [publishedItem()], { generation: "g1", generated_at: T0, snapshot_complete: true });
  assert.equal(first.conditions["cond-1"].baseline_generation, "g1");
  assert.equal(getUnreadUpdates(first, [publishedItem()]).length, 0);
  assert.equal(projectLocalConditions(first, [publishedItem()])[0].update_kind, "INITIAL");
  const revision = publishedItem({ source_version: 2, change_type: "DEADLINE_CHANGED" });
  assert.equal(getUnreadUpdates(first, [revision]).length, 1);
  assert.equal(getUnreadUpdates(first, [{ ...revision, source_version: 3, materiality: "FORMAT_ONLY" }]).length, 0);
  assert.equal(getUnreadUpdates(first, [{ ...publishedItem(), content_sha256: "new-layout-hash" }]).length, 0);
});

test("backfilled old announcements are history even when acquired after baseline", () => {
  const base = initializeConditionBaselines(addLocalCondition(emptyLocalConditions(), condition(), T1), [], { generation: "g1", generated_at: T1 });
  const old = { ...publishedItem(), published_at: T0, acquired_at: T2 };
  assert.equal(projectLocalConditions(base, [old])[0].update_kind, "HISTORICAL");
  assert.equal(getUnreadUpdates(base, [old]).length, 0);
  const lateCapture = { ...old, published_at: T2, end_at: T0 };
  assert.equal(projectLocalConditions(base, [lateCapture])[0].update_kind, "HISTORICAL");
});

test("read coverage requires all currently matched conditions; cancelling one preserves the remaining read", () => {
  let state = addLocalCondition(emptyLocalConditions(), { condition_id: "c1", filters: { source_id: ["S-032"] } }, T0);
  state = addLocalCondition(state, { condition_id: "c2", filters: { keywords: ["交通"] } }, T0);
  const item = publishedItem();
  const oneRead = markLocalRead(state, "c1", item.event_id, 1, T1);
  assert.equal(getUnreadUpdates(oneRead, [item]).length, 1);
  const allRead = markDisplayedUpdatesRead(state, projectLocalConditions(state, [item]), T1);
  assert.equal(getUnreadUpdates(allRead, [item]).length, 0);
  const cancelled = cancelLocalCondition(allRead, "c1", T2);
  assert.equal(getUnreadUpdates(cancelled, [item]).length, 0);
});

test("marking displayed v2 does not mark later v3 or unseen rows", () => {
  const state = addLocalCondition(emptyLocalConditions(), condition(), T0);
  const v2 = publishedItem({ source_version: 2, change_type: "REVISED" });
  const next = markDisplayedUpdatesRead(state, projectLocalConditions(state, [v2]), T1);
  assert.deepEqual(getUnreadUpdates(next, [v2, publishedItem({ source_version: 3, change_type: "REVISED" }), publishedItem({ event_id: "other" })]).map((item) => [item.event_id, item.source_version]), [["event-1", 3], ["other", 1]]);
  assert.throws(() => markDisplayedUpdatesRead(next, [{ ...v2, change_key: "wrong" }]), /版本不一致/);
});

test("filter edits and re-enablement reset baseline while preserving previously read versions", () => {
  let state = initializeConditionBaselines(addLocalCondition(emptyLocalConditions(), condition(), T0), [publishedItem()], { generation: "g1", generated_at: T0 });
  state = markLocalRead(state, "cond-1", "event-1", 2, T1);
  const changed = updateLocalCondition(state, "cond-1", { filters: { keywords: ["道路"] } }, T1);
  assert.equal(changed.conditions["cond-1"].baseline_initialized, false);
  assert.equal(changed.conditions["cond-1"].condition_version, 2);
  assert.ok(changed.read_entries["event-1#v2"]);
  const enabled = updateLocalCondition(cancelLocalCondition(state, "cond-1", T1), "cond-1", { enabled: true }, T2);
  assert.equal(enabled.conditions["cond-1"].baseline_initialized, false);
});

test("synthetic read and conditions are isolated from published data", () => {
  let state = addLocalCondition(emptyLocalConditions(), { condition_id: "real", filters: { keywords: ["交通"] } }, T0);
  state = addLocalCondition(state, { condition_id: "demo", filters: { keywords: ["交通"] }, namespace: "demo:commute" }, T0);
  const item = publishedItem();
  const demo = projectLocalConditions(state, [item], { namespace: "demo:commute" });
  assert.deepEqual(demo[0].hit_condition_ids, ["demo"]);
  state = markDisplayedUpdatesRead(state, demo, T1);
  assert.equal(getUnreadUpdates(state, [item], { namespace: "demo:commute" }).length, 0);
  assert.equal(getUnreadUpdates(state, [item]).length, 1);
  assert.ok(state.read_entries["demo:commute::event-1#v1"]);
});

test("partial checks do not advance successful completeness, and mixed generations refuse baseline", () => {
  const base = addLocalCondition(emptyLocalConditions(), condition(), T0);
  const partial = initializeConditionBaselines(base, [publishedItem()], { generation: "g1", generated_at: T0, snapshot_complete: false });
  assert.equal(partial.conditions["cond-1"].baseline_complete, false);
  assert.equal(partial.last_successful_check, undefined);
  assert.throws(() => initializeConditionBaselines(base, [], { generation_mixed: true }), /世代不一致/);
  const pub = { source_collection_run_id: "g1", generated_at: T0, snapshot_complete: true };
  const archive = { collection_run_id: "g1", generated_at: T0, items: [] };
  const status = { latest_collection_run: { collection_run_id: "g1" }, generated_at: T0, sources: [{ source_id: "S-032", source_health: "FAILED", freshness_status: "STALE" }] };
  assert.equal(conditionDatasetStatus(pub, archive, status).complete, false);
  assert.equal(conditionDatasetStatus(pub, { ...archive, collection_run_id: "g2" }, status).mixed, true);
  assert.equal(conditionDatasetStatus(pub, archive, null).present, false);
});

test("shared query save is atomic on storage failure and never overwrites unknown format", () => {
  const target = storage({ schema_version: 2 });
  assert.throws(() => saveLocalConditionRequest(condition(), [], {}, target), /格式不相容/);
  assert.equal(JSON.parse(target.getItem("govintel.v2.conditions.v1")).schema_version, 2);
  const quota = { getItem: () => null, setItem: () => { throw new Error("quota"); } };
  assert.throws(() => saveLocalConditionRequest(condition(), [publishedItem()], { generation: "g1" }, quota), /quota/);
});

test("storage contains IDs and filters only; explicit clear removes just the tracking key", () => {
  const values = new Map([["unrelated", "retained"]]);
  const target = { getItem: (key) => values.get(key) || null, setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
  const next = saveLocalConditionRequest(condition(), [{ ...publishedItem(), full_document: "private-document-body" }], { generation: "g1", generated_at: T0 }, target);
  assert.equal(JSON.stringify(next).includes("private-document-body"), false);
  clearLocalConditions(target);
  assert.equal(values.has("govintel.v2.conditions.v1"), false);
  assert.equal(values.get("unrelated"), "retained");
});

test("reserved condition IDs and corrupt read indexes fail closed", () => {
  assert.throws(() => addLocalCondition(emptyLocalConditions(), { condition_id: "__proto__", filters: {} }), /ID 無效/);
  const state = markLocalRead(emptyLocalConditions(), "c1", "event-1", 1, T0);
  state.read_entries.wrong = state.read_entries["event-1#v1"];
  assert.throws(() => validateLocalConditions(state), /索引無法驗證/);
});
