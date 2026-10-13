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

for (const status of ["RESOLVED", "DISMISSED"]) test(`${status} watch stays outside homepage tracking after a source update`, () => {
  const confirmed = confirmLocalHandoff(addLocalWatch(emptyLocalHandoff(), item(), T0), [item()], publication(), T0);
  const watchId = Object.keys(confirmed.watch_items)[0];
  const terminal = setLocalWatchStatus(confirmed, watchId, status, T1);
  const synced = syncLocalHandoff(terminal, [item(2)], publication(), T1);
  assert.equal(synced.watch_items[watchId].status, status);
  assert.deepEqual(projectLocalTracking(synced, [item(2)]), []);
  assert.deepEqual(synced.handoffs, confirmed.handoffs);
  assert.equal(synced.watch_items[watchId].invalidations.length, 1);
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

test("export derives claim-scoped review warnings without rewriting the confirmed version", () => {
  const other = { ...item(), identity: "S-032:traffic-2", stable_key: "traffic-2" };
  const watched = addLocalWatch(addLocalWatch(emptyLocalHandoff(), item(), T0), other, T0);
  const confirmed = confirmLocalHandoff(watched, [item(), other], publication(), T0);
  const original = JSON.stringify(confirmed.handoffs[0]);
  const changed = syncLocalHandoff(confirmed, [item(2), other], publication(), T1);
  const beforeExport = JSON.stringify(changed);
  const payload = JSON.parse(exportLocalHandoff(changed, "json").content);
  assert.deepEqual(payload.handoff, confirmed.handoffs[0]);
  assert.equal(payload.review_warnings.length, 1);
  const warning = payload.review_warnings[0];
  assert.equal(warning.warning, "NEEDS_REVIEW");
  assert.equal(warning.claim_id, confirmed.handoffs[0].items[0].claim_id);
  assert.equal(warning.confirmed_source_version, 1);
  assert.equal(warning.latest_source_version, 2);
  assert.equal(warning.confirmed_source_sha256, "hash-1");
  assert.equal(warning.latest_source_sha256, "hash-2");
  assert.equal(warning.evidence_locator, "S-032:traffic-1#v1");
  assert.equal(warning.latest_source_document_version, "DOCV-2");
  const markdown = exportLocalHandoff(changed, "markdown").content;
  for (const value of ["NEEDS_REVIEW", warning.claim_id, "S-032:traffic-1#v1", "DOCV-1", "hash-1", "a".repeat(64)]) {
    assert.ok(markdown.includes(value), `export must preserve ${value}`);
  }
  assert.equal(JSON.stringify(changed.handoffs[0]), original);
  assert.equal(JSON.stringify(changed), beforeExport);
});

test("same-version hash correction is exported as review required", () => {
  const confirmed = confirmLocalHandoff(addLocalWatch(emptyLocalHandoff(), item(), T0), [item()], publication(), T0);
  const changed = syncLocalHandoff(confirmed, [item(1, "corrected-hash")], publication(), T1);
  const payload = JSON.parse(exportLocalHandoff(changed, "json").content);
  assert.equal(payload.review_warnings.length, 1);
  assert.equal(payload.review_warnings[0].confirmed_source_sha256, "hash-1");
  assert.equal(payload.review_warnings[0].latest_source_sha256, "corrected-hash");
});

test("new confirmations clear warnings and preserve the original invalidation resolution receipt", () => {
  const v1 = confirmLocalHandoff(addLocalWatch(emptyLocalHandoff(), item(), T0), [item()], publication(), T0);
  const revised = syncLocalHandoff(v1, [item(2)], publication(), T1);
  const v2 = confirmLocalHandoff(revised, [item(2)], publication(), T1);
  const watchId = Object.keys(v2.watch_items)[0];
  const receipt = v2.watch_items[watchId].invalidations[0].resolved_in_handoff_id;
  const v3 = confirmLocalHandoff(v2, [item(2)], publication(), T1);
  const payload = JSON.parse(exportLocalHandoff(v3, "json").content);
  assert.deepEqual(payload.review_warnings, []);
  assert.equal(v3.watch_items[watchId].invalidations[0].resolved_in_handoff_id, receipt);
  assert.deepEqual(v3.handoffs[0], v1.handoffs[0]);
});

for (const corrected of [item(2), item(1, "corrected-hash")]) test(`retained old source cannot clear revision ${corrected.source_version}/${corrected.source_sha256}`, () => {
  const v1 = confirmLocalHandoff(addLocalWatch(emptyLocalHandoff(), item(), T0), [item()], publication(), T0);
    const revised = syncLocalHandoff(v1, [corrected], publication(), T1);
    // An older publication can be reloaded after the newer version was seen.
    const retained = syncLocalHandoff(revised, [item()], publication(), T1);
    const original = JSON.stringify(retained);
    assert.throws(() => confirmLocalHandoff(retained, [item()], publication(), T1), /較新|版本|雜湊/);
    assert.equal(JSON.stringify(retained), original);
    assert.equal(Object.values(retained.watch_items)[0].status, "NEEDS_REVIEW");
    assert.equal(JSON.parse(exportLocalHandoff(retained, "json").content).review_warnings.length, 1);
});

test("document-only correction remains known after resolution and permits a later source revision", () => {
  const v1 = confirmLocalHandoff(addLocalWatch(emptyLocalHandoff(), item(), T0), [item()], publication(), T0);
  const corrected = { ...item(), source_document_version: "DOCV-CORRECTED" };
  const revised = syncLocalHandoff(v1, [corrected], publication(), T1);
  assert.equal(Object.values(revised.watch_items)[0].status, "NEEDS_REVIEW");
  assert.throws(() => confirmLocalHandoff(revised, [item()], publication(), T1), /較新|版本|雜湊/);
  const v2 = confirmLocalHandoff(revised, [corrected], publication(), T1);
  const original = JSON.stringify(v2);
  assert.throws(() => confirmLocalHandoff(v2, [item()], publication(), T1), /較新|版本|雜湊/);
  assert.equal(JSON.stringify(v2), original);
  const v3 = confirmLocalHandoff(syncLocalHandoff(v2, [item(2)], publication(), T1), [item(2)], publication(), T1);
  assert.deepEqual(v3.handoffs.slice(0, 2), v2.handoffs);
  assert.equal(v3.handoffs[2].items[0].source_document_version, "DOCV-2");
  assert.equal(Object.values(v3.watch_items)[0].invalidations[0].resolved_in_handoff_id,
    Object.values(v2.watch_items)[0].invalidations[0].resolved_in_handoff_id);
});

for (const corrected of [item(1, "corrected-hash"), { ...item(), source_document_version: "DOCV-CORRECTED" }]) {
  test(`retained publication after resolution cannot reverse ${corrected.source_sha256}/${corrected.source_document_version}`, () => {
    const v1 = confirmLocalHandoff(addLocalWatch(emptyLocalHandoff(), item(), T0), [item()], publication(), T0);
    const synced = syncLocalHandoff(v1, [corrected], publication(), T1);
    const v2 = confirmLocalHandoff(synced, [corrected], { ...publication(), generated_at: T1 }, T1);
    for (const observed of [T0, T1]) {
      const replay = syncLocalHandoff(v2, [item()], publication(), observed);
      const original = JSON.stringify(replay);
      assert.throws(() => confirmLocalHandoff(replay, [item()], publication(), T1), /較新|版本|雜湊/);
      assert.equal(JSON.stringify(replay), original);
      assert.deepEqual(replay.handoffs, v2.handoffs);
      assert.equal(Object.values(replay.watch_items)[0].invalidations.length, 1);
    }
    const later = "2026-09-22T01:00:00+08:00";
    const reverted = syncLocalHandoff(v2, [item()], publication(), later);
    assert.equal(Object.values(reverted.watch_items)[0].status, "NEEDS_REVIEW");
    const v3 = confirmLocalHandoff(reverted, [item()], { ...publication(), generated_at: later }, later);
    assert.deepEqual(v3.handoffs.slice(0, 2), v2.handoffs);
    assert.equal(v3.handoffs[2].items[0].source_document_version, item().source_document_version);
  });
}

test("failed or partial source shows a per-watch gap and never resolves local tracking", () => {
  const watched = addLocalWatch(emptyLocalHandoff(), item(), T0);
  const noChanges = syncLocalHandoff(watched, [], publication(), T1);
  for (const source_health of ["FAILED", "DEGRADED"]) {
    const rows = projectLocalTracking(noChanges, [], { sources: [{
      source_id: "S-032", source_health, intelligence_gaps: ["replay fetch unavailable"],
    }] });
    assert.equal(rows.length, 1);
    assert.equal(rows[0].watch_status, "WATCHING");
    assert.equal(rows[0].source_health, source_health);
    assert.deepEqual(rows[0].source_gaps, ["replay fetch unavailable"]);
    assert.match(rows[0].recommended_action, /replay fetch unavailable/);
  }
});

test("local state and exported handoff never copy sensitive input fields", () => {
  const unsafe = { ...item(), internal_case_id: "PRIVATE_CASE_REPLAY", user_note: "PRIVATE_NOTE_REPLAY" };
  const confirmed = confirmLocalHandoff(addLocalWatch(emptyLocalHandoff(), unsafe, T0), [unsafe], publication(), T0);
  assert.doesNotMatch(JSON.stringify(confirmed), /PRIVATE_CASE_REPLAY|PRIVATE_NOTE_REPLAY/);
  assert.doesNotMatch(exportLocalHandoff(confirmed, "json").content, /PRIVATE_CASE_REPLAY|PRIVATE_NOTE_REPLAY/);
});
