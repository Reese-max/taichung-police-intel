import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { buildSourceStatus, freshnessStatus, lastKnownGoodAge, lastKnownGoodAgeLabel } from "../lib/source-status.js";

const now = new Date("2026-10-05T10:00:00Z");
const completed = "2026-10-03T10:00:00Z";

test("saved LKG age grows with explicit observation time without changing any clock", () => {
  const saved = { source_run_id: "SR-OLD", completed_at: completed, data_as_of: "2026-09-01T00:00:00Z" };
  const before = structuredClone(saved);
  assert.equal(lastKnownGoodAge(saved, now).age_hours, 48);
  assert.equal(lastKnownGoodAge(saved, now.getTime() + 86400000).age_hours, 72);
  assert.deepEqual(saved, before);
});

test("offset/current/fractional clocks yield truthful durations and labels", () => {
  for (const [value, hours] of [["2026-10-03T18:00:00+08:00", 48], [now.toISOString(), 0], ["2026-10-05T09:30:00Z", 0.5]]) {
    assert.equal(lastKnownGoodAge({ completed_at: value }, now).age_hours, hours);
  }
  assert.equal(lastKnownGoodAgeLabel({ completed_at: completed }, now), "48 小時");
  assert.equal(lastKnownGoodAgeLabel({ completed_at: "2026-10-05T09:59:59Z" }, now), "不到 0.1 小時");
});

test("missing LKG never borrows successful-fetch/check/official/publication timestamps", () => {
  for (const saved of [null, [], {}, { completed_at: null }, { completed_at: "" },
    { last_success_at: completed }, { last_checked_at: completed }, { data_as_of: completed }, { generated_at: completed }]) {
    assert.equal(lastKnownGoodAge(saved, now).status, "UNKNOWN");
    assert.equal(lastKnownGoodAge(saved, now).age_hours, null);
    assert.match(lastKnownGoodAgeLabel(saved, now), /^未知/);
  }
});

test("invalid calendar/offset/type and future completion stay UNKNOWN rather than zero", () => {
  for (const value of [true, 1, {}, "invalid", "2026-10-03", "2026-10-03T10:00:00", "2026-02-30T10:00:00Z",
    "0000-01-01T00:00:00Z", "2026-10-03T10:00:00+08:60", "2026-10-03T10:00:00+24:00",
    "2026-10-03T25:00:00Z", "2026-10-03T10:00:61Z", "2026-10-06T10:00:00Z"]) {
    assert.equal(lastKnownGoodAge({ completed_at: value }, now).status, "UNKNOWN");
    assert.equal(lastKnownGoodAge({ completed_at: value }, now).age_hours, null);
  }
  assert.match(lastKnownGoodAgeLabel({ completed_at: "2026-10-06T10:00:00Z" }, now), /晚於本次檢視時間/);
});

test("invalid observation time cannot claim zero age", () => {
  for (const observed of [null, undefined, new Date("invalid"), NaN, Infinity, now.getTime() + 0.5, "2026-10-05T10:00:00Z", true]) {
    assert.equal(lastKnownGoodAge({ completed_at: completed }, observed).reason, "INVALID_OBSERVED_AT");
    assert.equal(lastKnownGoodAge({ completed_at: completed }, observed).age_hours, null);
  }
});

test("microsecond completion precision does not turn a future clock into zero age", () => {
  for (const value of ["2026-10-05T10:00:00.000001Z", "2026-10-05T18:00:00.000001+08:00"]) {
    assert.equal(lastKnownGoodAge({ completed_at: value }, now).reason, "FUTURE_COMPLETED_AT");
    assert.equal(lastKnownGoodAge({ completed_at: value }, now).age_hours, null);
  }
  for (const value of ["2026-10-05T09:59:59.999999Z", "2026-10-05T17:59:59.999999+08:00"]) {
    assert.equal(lastKnownGoodAge({ completed_at: value }, now).age_hours, 1 / 3_600_000_000);
  }
  assert.equal(lastKnownGoodAge({ completed_at: "1969-12-31T23:59:59.999999Z" }, new Date(0)).age_hours, 1 / 3_600_000_000);
});

test("actual status builder preserves completion precision and its existing Date clock contract", () => {
  for (const [value, status, hours] of [["2026-10-05T10:00:00.000001Z", "UNKNOWN", null],
    ["2026-10-05T18:00:00.000001+08:00", "UNKNOWN", null],
    ["2026-10-05T09:59:59.999999Z", "KNOWN", 1 / 3_600_000_000],
    ["2026-10-05T17:59:59.999999+08:00", "KNOWN", 1 / 3_600_000_000],
    [new Date(completed), "KNOWN", 48]]) {
    const actual = buildSourceStatus({ source_id: "S-004", lkg_source_run_id: "SR-OLD", lkg_completed_at: value }, now);
    assert.equal(actual.last_known_good_age.status, status);
    assert.equal(actual.last_known_good_age.age_hours, hours);
    assert.equal(actual.last_success_at, new Date(value).toISOString());
    assert.equal(actual.last_known_good.completed_at, value instanceof Date ? value.toISOString() : value);
  }
  assert.equal(buildSourceStatus({ lkg_source_run_id: "SR-OLD", lkg_completed_at: new Date("invalid") }, now).last_known_good_age.status, "UNKNOWN");
});

test("both clock lanes use representable UTC years1 through9999 and keep their valid boundaries", () => {
  for (const value of ["9999-12-31T23:59:59-08:00", "0001-01-01T00:00:00+08:00"]) {
    assert.equal(lastKnownGoodAge({ completed_at: value }, now).status, "UNKNOWN");
    assert.equal(lastKnownGoodAge({ completed_at: completed }, new Date(value)).reason, "INVALID_OBSERVED_AT");
  }
  for (const value of ["0001-01-01T00:00:00Z", "9999-12-31T23:59:59Z"]) {
    assert.equal(lastKnownGoodAge({ completed_at: value }, new Date(value)).age_hours, 0);
  }
});

test("age of successful acquisition does not change official-data freshness", () => {
  assert.equal(lastKnownGoodAge({ completed_at: now.toISOString() }, now).age_hours, 0);
  assert.equal(freshnessStatus("2026-01-01T00:00:00Z", now), "VERY_STALE");
  const actual = buildSourceStatus({ source_id: "S-006", name: "test", source_run_id: "SR-NOW", source_health: "FAILED",
    window_completeness: "PARTIAL", data_as_of: "2026-01-01T00:00:00Z", lkg_source_run_id: "SR-OLD",
    lkg_completed_at: completed, lkg_manifest_sha256: "a".repeat(64) }, now);
  assert.equal(actual.last_known_good_age.age_hours, 48);
  assert.equal(actual.freshness_status, "VERY_STALE");
  assert.equal(actual.source_health, "FAILED");
  for (const value of [null, "invalid", "2026-10-06T10:00:00Z"]) {
    const row = buildSourceStatus({ source_id: "S-006", name: "test", lkg_source_run_id: "SR-OLD", lkg_completed_at: value }, now);
    assert.equal(row.last_known_good_age.status, "UNKNOWN");
    assert.equal(row.last_known_good_age.age_hours, null);
  }
});

test("actual offline Python acquisition/legacy/failure controls run in the web gate", () => {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const result = spawnSync(process.env.PYTHON || "python", ["-m", "unittest", "discover", "-s", "tests", "-p", "test_lkg_age.py", "-v"],
    { cwd: root, encoding: "utf8", timeout: 30000, maxBuffer: 2 * 1024 * 1024 });
  assert.equal(result.status, 0, result.stdout + result.stderr);
});
