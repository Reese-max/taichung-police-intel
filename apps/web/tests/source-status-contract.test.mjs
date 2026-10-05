import { readFile } from "node:fs/promises";
import test from "node:test";
import assert from "node:assert/strict";

import { failedSourceFeedFailures, sourceManifestFailures } from "../../../scripts/source-status-contract.mjs";
import { validateSourceStatus } from "../lib/source-status.js";

const hash = "a".repeat(64);
const knownGood = {
  source_run_id: "SR-DEMO-20260924-EVENING-029",
  manifest_sha256: hash,
};
const sourceStatus = JSON.parse(await readFile(new URL("../public/data/source-status.json", import.meta.url), "utf8"));

function failedSource() {
  return {
    source_id: "S-029",
    source_health: "FAILED",
    result: "FAILED",
    window_completeness: "PARTIAL",
    manifest_sha256: null,
    error_code: "CONNECTIONERROR",
    intelligence_gaps: ["SOURCE_FAILED", "WINDOW_PARTIAL", "STALE_DATA"],
    last_known_good: knownGood,
  };
}

test("checked-in public source status has a validated source snapshot", () => {
  assert.equal(validateSourceStatus(sourceStatus), sourceStatus);
});

test("public source status requires a non-empty, identified snapshot", () => {
  assert.throws(() => validateSourceStatus({ schema_version: 1, sources: [] }), /來源狀態/);
  assert.throws(() => validateSourceStatus({ schema_version: 1, generated_at: "2026-09-11T00:00:00+08:00", sources: [{}] }), /source_id/);
  const duplicate = structuredClone(sourceStatus);
  duplicate.sources.push({ ...duplicate.sources[0] });
  assert.throws(() => validateSourceStatus(duplicate), /source_id/);
  const malformed = structuredClone(sourceStatus);
  malformed.sources[0].intelligence_gaps = { status: "FAILED" };
  assert.throws(() => validateSourceStatus(malformed), /無法驗證/);
});

test("failed S-029 fetch retains LKG without inventing a current manifest", () => {
  assert.deepEqual(sourceManifestFailures(failedSource()), []);
  assert.deepEqual(failedSourceFeedFailures({
    stable_id: "S-029-item",
    source_id: "S-029",
    source_health: "FAILED",
    change_type: "LKG",
    eligibility: "INELIGIBLE_SOURCE_FAILED",
  }, new Set(["S-029"])), []);
});

test("failed source without a verified baseline remains blocked", () => {
  const source = failedSource();
  source.last_known_good = { ...knownGood, manifest_sha256: "bad" };
  assert.deepEqual(sourceManifestFailures(source), ["demo-status:S-029:failed-source-missing-lkg"]);
});

test("failed source cannot claim a new manifest or hide its failure", () => {
  const source = failedSource();
  source.manifest_sha256 = hash;
  source.intelligence_gaps = ["STALE_DATA"];
  assert.deepEqual(sourceManifestFailures(source), [
    "demo-status:S-029:failed-source-has-current-manifest",
    "demo-status:S-029:failed-source-not-marked",
  ]);
});

test("successful source requires a valid current manifest and LKG", () => {
  const source = { ...failedSource(), source_health: "PASS", result: "NO_NEW_ITEM", manifest_sha256: hash };
  assert.deepEqual(sourceManifestFailures(source), []);
  source.manifest_sha256 = null;
  assert.deepEqual(sourceManifestFailures(source), ["demo-status:S-029:invalid-manifest"]);
  source.manifest_sha256 = hash;
  source.last_known_good = null;
  assert.deepEqual(sourceManifestFailures(source), ["demo-status:S-029:missing-lkg"]);
});

test("failed-source feed item cannot be promoted as a new home candidate", () => {
  assert.deepEqual(failedSourceFeedFailures({
    stable_id: "S-029-item",
    source_id: "S-029",
    source_health: "FAILED",
    change_type: "NEW",
    eligibility: "HOME_CANDIDATE",
  }, new Set(["S-029"])), ["feed:S-029-item:failed-source-not-lkg"]);
});

test("status-failed source cannot be promoted by a feed row claiming PASS", () => {
  assert.deepEqual(failedSourceFeedFailures({
    stable_id: "S-029-item",
    source_id: "S-029",
    source_health: "PASS",
    change_type: "NEW",
    eligibility: "HOME_CANDIDATE",
  }, new Set(["S-029"])), ["feed:S-029-item:failed-source-not-lkg"]);
});

test("feed failure must agree with source status", () => {
  assert.deepEqual(failedSourceFeedFailures({
    stable_id: "S-029-item",
    source_id: "S-029",
    source_health: "FAILED",
    change_type: "LKG",
    eligibility: "INELIGIBLE_SOURCE_FAILED",
  }, new Set()), ["feed:S-029-item:failed-source-not-lkg"]);
});
