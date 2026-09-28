import test from "node:test";
import assert from "node:assert/strict";

import { failedSourceFeedFailures, sourceManifestFailures } from "../../../scripts/source-status-contract.mjs";

const hash = "a".repeat(64);
const knownGood = {
  source_run_id: "SR-DEMO-20260924-EVENING-029",
  manifest_sha256: hash,
};

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

test("failed S-029 fetch retains LKG without inventing a current manifest", () => {
  assert.deepEqual(sourceManifestFailures(failedSource()), []);
  assert.deepEqual(failedSourceFeedFailures({
    stable_id: "S-029-item",
    source_health: "FAILED",
    change_type: "LKG",
    eligibility: "INELIGIBLE_SOURCE_FAILED",
  }), []);
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
    source_health: "FAILED",
    change_type: "NEW",
    eligibility: "HOME_CANDIDATE",
  }), ["feed:S-029-item:failed-source-not-lkg"]);
});
