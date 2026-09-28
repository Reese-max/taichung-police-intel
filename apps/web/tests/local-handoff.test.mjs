import assert from "node:assert/strict";
import test from "node:test";

import {
  addLocalWatch,
  confirmLocalHandoff,
  emptyLocalHandoff,
  exportLocalHandoff,
  loadLocalHandoff,
  projectLocalTracking,
  saveLocalHandoff,
  setLocalWatchStatus,
  syncLocalHandoff,
} from "../lib/local-handoff.js";

const T0 = "2026-09-20T01:00:00+08:00";
const T1 = "2026-09-21T01:00:00+08:00";

function item(version = 1, hash = `hash-${version}`) {
  return {
    identity: "S-032:traffic-1",
    source_id: "S-032",
    stable_key: "traffic-1",
    title: version === 1 ? "交通管制公告" : "交通管制公告（修正）",
    official_url: "https://example.gov.tw/traffic-1",
    source_version: version,
    source_sha256: hash,
    source_document_version: `DOCV-${version}`,
  };
}

function publication() {
  return {
    source_collection_run_id: "RUN-1",
    generated_at: T0,
    publication_hash: "a".repeat(64),
  };
}

test("local watch is persistent and idempotent", () => {
  const first = addLocalWatch(emptyLocalHandoff(), item(), T0);
  const same = addLocalWatch(first, item(), T1);
  assert.equal(Object.keys(same.watch_items).length, 1);
  assert.deepEqual(same.watch_items[Object.keys(same.watch_items)[0]], first.watch_items[Object.keys(first.watch_items)[0]]);
});

test("source version change reopens only the tracked item", () => {
  const first = addLocalWatch(emptyLocalHandoff(), item(), T0);
  const second = syncLocalHandoff(first, [item(2)], publication(), T1);
  const watch = Object.values(second.watch_items)[0];
  assert.equal(watch.status, "NEEDS_REVIEW");
  assert.equal(watch.invalidations.length, 1);
  assert.equal(watch.invalidations[0].before.version, 1);
  assert.equal(watch.invalidations[0].after.version, 2);
});

test("confirmed handoff keeps v1, creates v2, and exports exact locator", () => {
  const watched = addLocalWatch(emptyLocalHandoff(), item(), T0);
  const v1 = confirmLocalHandoff(watched, [item()], publication(), T0);
  const changed = syncLocalHandoff(v1, [item(2)], publication(), T1);
  const v2 = confirmLocalHandoff(changed, [item(2)], { ...publication(), generated_at: T1 }, T1);
  assert.equal(v2.handoffs.length, 2);
  assert.equal(v2.handoffs[0].items[0].source_version, 1);
  assert.equal(v2.handoffs[1].items[0].source_version, 2);
  assert.match(v2.handoffs[1].items[0].evidence.locator, /S-032:traffic-1#v2/);
  assert.match(exportLocalHandoff(v2, "markdown").content, /https:\/\/example\.gov\.tw\/traffic-1/);
  assert.match(exportLocalHandoff(v2, "json").content, /"brief_version": 2/);
});

test("resolved watch leaves the active projection but remains in history", () => {
  const watched = addLocalWatch(emptyLocalHandoff(), item(), T0);
  const watchId = Object.keys(watched.watch_items)[0];
  const resolved = setLocalWatchStatus(watched, watchId, "RESOLVED", T1);
  assert.deepEqual(projectLocalTracking(resolved, [item()]), []);
  assert.equal(resolved.watch_items[watchId].status, "RESOLVED");
});

test("terminal watch can be re-added against a newer source version", () => {
  const watched = addLocalWatch(emptyLocalHandoff(), item(), T0);
  const watchId = Object.keys(watched.watch_items)[0];
  const resolved = setLocalWatchStatus(watched, watchId, "RESOLVED", T1);
  const reopened = addLocalWatch(resolved, item(2), T1);
  assert.equal(reopened.watch_items[watchId].status, "WATCHING");
  assert.equal(reopened.watch_items[watchId].tracked_version, 2);
  assert.equal(reopened.watch_items[watchId].created_at, "2026-09-20T17:00:00.000Z");
});

test("local storage round-trip validates the same state", () => {
  const storage = {
    value: null,
    getItem() { return this.value; },
    setItem(_key, value) { this.value = value; },
  };
  const state = addLocalWatch(emptyLocalHandoff(), item(), T0);
  saveLocalHandoff(state, storage);
  assert.deepEqual(loadLocalHandoff(storage), state);
});
