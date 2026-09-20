import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  assessPublication,
  MAX_SNAPSHOT_AGE_MS,
  REQUIRED_PUBLICATION_SOURCE_IDS,
} from "../lib/publication-freshness.mjs";

const now = Date.parse("2026-09-16T12:00:00+08:00");
const stamp = "2026-09-16T08:00:00+08:00";
const source = (source_id) => ({
  source_id,
  last_checked_at: stamp,
  source_health: "PASS",
  window_completeness: "COMPLETE_ZERO",
  result: "NO_NEW_ITEM",
});
function fixture() {
  return {
    brief: {
      generated_at: stamp,
      source_status_generated_at: stamp,
      source_collection_run_id: "run-1",
      publication_status: "READY",
      snapshot_complete: true,
    },
    status: {
      generated_at: stamp,
      latest_collection_run: { collection_run_id: "run-1", status: "SUCCEEDED" },
      sources: REQUIRED_PUBLICATION_SOURCE_IDS.map(source),
    },
  };
}
const check = ({ brief, status }, at = now) => assessPublication(brief, status, at);

test("required UI coverage is derived from the canonical active catalog", async () => {
  const catalog = JSON.parse(await readFile(new URL("../../../docs/govintel/source-catalog.v2.json", import.meta.url)));
  assert.deepEqual(
    REQUIRED_PUBLICATION_SOURCE_IDS,
    catalog.sources.filter(source => source.status === "PRODUCTION_ACTIVE").map(source => source.source_id).sort(),
  );
});

test("recent matched complete snapshot allows bounded zero-change wording", () => {
  assert.equal(check(fixture()).state, "RECENT");
  assert.equal(check(fixture()).canReassure, true);
});
test("READY snapshot expires without a backend rebuild", () => {
  const f = fixture();
  assert.equal(check(f, now + MAX_SNAPSHOT_AGE_MS).state, "STALE");
  assert.equal(f.brief.publication_status, "READY");
});
test("oldest source check, not new build timestamp, determines age", () => {
  const f = fixture();
  f.status.sources[0].last_checked_at = "2026-09-11T08:23:26+08:00";
  assert.equal(check(f).state, "STALE");
});
for (const state of ["FAILED", "PARTIAL", "RUNNING", "UNKNOWN"]) {
  test(`run ${state} cannot reassure`, () => {
    const f = fixture();
    f.status.latest_collection_run.status = state;
    assert.equal(check(f).canReassure, false);
  });
}
for (const field of ["source_health", "window_completeness", "result"]) {
  test(`missing ${field} does not become a successful empty result`, () => {
    const f = fixture();
    delete f.status.sources[0][field];
    assert.equal(check(f).state, "PARTIAL");
  });
}
test("failed or partial publication cannot reassure", () => {
  const f = fixture();
  f.brief.publication_status = "PARTIAL";
  assert.equal(check(f).state, "PARTIAL");
  f.brief.publication_status = "READY";
  f.brief.snapshot_complete = false;
  assert.equal(check(f).state, "PARTIAL");
});
test("cross-run status remains unknown", () => {
  const f = fixture();
  f.status.latest_collection_run.collection_run_id = "run-2";
  assert.equal(check(f).state, "UNKNOWN");
});
test("same run with a different generation version remains unknown", () => {
  const f = fixture();
  f.brief.source_status_generated_at = "2026-09-16T09:00:00+08:00";
  assert.equal(check(f).state, "UNKNOWN");
});
for (const badStamp of [null, "bad", "2026-09-16T08:00:00", "2027-09-16T08:00:00+08:00"]) {
  test(`invalid source timestamp ${badStamp} remains unknown`, () => {
    const f = fixture();
    f.status.sources[0].last_checked_at = badStamp;
    assert.equal(check(f).state, "UNKNOWN");
  });
}
test("missing status and empty coverage remain unknown", () => {
  const f = fixture();
  assert.equal(assessPublication(f.brief, null, now).state, "UNKNOWN");
  f.status.sources = [];
  assert.equal(check(f).state, "UNKNOWN");
});
test("missing one required source cannot masquerade as complete coverage", () => {
  const f = fixture();
  f.status.sources = f.status.sources.filter((s) => s.source_id !== "S-029");
  assert.equal(check(f).state, "UNKNOWN");
  assert.equal(check(f).canReassure, false);
});
test("five arbitrary unique source IDs cannot masquerade as required coverage", () => {
  const f = fixture();
  f.status.sources = ["X-001", "X-002", "X-003", "X-004", "X-005"].map(source);
  assert.equal(check(f).state, "UNKNOWN");
});
test("clock is checked after hydration and invalid policy is rejected", () => {
  const f = fixture();
  assert.equal(check(f, null).state, "UNKNOWN");
  assert.equal(assessPublication(f.brief, f.status, now, 0).state, "UNKNOWN");
});
test("old reference statistics do not imply the recent collection failed", () => {
  const f = fixture();
  f.status.sources[0].data_as_of = "2023-12-01T00:00:00+08:00";
  f.status.sources[0].freshness_status = "VERY_STALE";
  assert.equal(check(f).state, "RECENT");
});
test("threshold is deterministic and configurable", () => {
  const f = fixture();
  const t = Date.parse(stamp);
  assert.equal(check(f, t + MAX_SNAPSHOT_AGE_MS).state, "RECENT");
  assert.equal(check(f, t + MAX_SNAPSHOT_AGE_MS + 1).state, "STALE");
  assert.equal(assessPublication(f.brief, f.status, now, 60_000).state, "STALE");
});
test("dashboard uses the policy for reassurance, clock ticks and recovery control", async () => {
  const s = await readFile(new URL("../components/V2DailyDashboard.js", import.meta.url), "utf8");
  assert.match(s, /assessPublication\(publication, sourceStatus, nowMs\)/);
  assert.match(s, /assessment\.canReassure/);
  assert.match(s, /setInterval\(updateClock, 60_000\)/);
  assert.match(s, /clearInterval/);
  assert.match(s, /重新載入資料/);
  assert.match(s, /快照重點/);
});

test("malformed source rows fail closed rather than crashing", () => {
  const f = fixture();
  f.status.sources[0] = null;
  assert.equal(check(f).state, "UNKNOWN");
});
test("duplicate source coverage cannot masquerade as a complete source set", () => {
  const f = fixture();
  f.status.sources[4] = { ...f.status.sources[0] };
  assert.equal(check(f).state, "UNKNOWN");
});
