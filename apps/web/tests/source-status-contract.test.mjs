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

const policy = JSON.parse(await readFile(new URL("../public/data/source-policy.json", import.meta.url), "utf8"));
const officialRevision = JSON.parse(await readFile(new URL("../../../tests/fixtures/date-provenance/s006-official-revision-metadata.json", import.meta.url), "utf8"));

function sourceStatusWithOfficialRevision() {
  const candidate = structuredClone(sourceStatus);
  const {record_origin: _audit, ...metadata} = officialRevision;
  Object.assign(candidate.sources.find(row => row.source_id === "S-006"), structuredClone(metadata));
  return candidate;
}

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
  assert.equal(validateSourceStatus(sourceStatus, policy), sourceStatus);
});

test("fresh official revision validates against the compact public schema1 policy", () => {
  const candidate = sourceStatusWithOfficialRevision();
  const before = structuredClone(candidate);
  assert.equal(policy.schema_version, 1);
  assert.equal(policy.active_sources, undefined);
  assert.equal(validateSourceStatus(candidate, policy), candidate);
  assert.deepEqual(candidate, before);
});

test("compact policy revision validation rejects unapproved documents and mismatched policy bindings", () => {
  for (const url of ["https://unapproved.example.test/fixture.pdf", "https://www.tccc.gov.tw/fixture.doc", "https://user:secret@www.tccc.gov.tw/fixture.pdf"]) {
    const candidate = sourceStatusWithOfficialRevision();
    const source = candidate.sources.find(row => row.source_id === "S-006");
    source.data_as_of_evidence.official_url = url;
    source.source_url = url;
    assert.throws(() => validateSourceStatus(candidate, policy), /official date evidence origin/, url);
  }
  for (const key of ["policy_hash", "catalog_hash", "policy_version"]) {
    const candidatePolicy = {...policy, [key]: key === "policy_version" ? policy[key] + 1 : "0".repeat(64)};
    assert.throws(() => validateSourceStatus(sourceStatusWithOfficialRevision(), candidatePolicy), /official date evidence origin/, key);
  }
  const poisonedPolicy = {...policy, active_sources: [{source_id: "S-006", entrypoint: "https://unapproved.example.test/fixture.pdf"}]};
  assert.equal(validateSourceStatus(sourceStatusWithOfficialRevision(), poisonedPolicy).sources.length, sourceStatus.sources.length);
});

test("public source status requires a non-empty, identified snapshot", () => {
  assert.throws(() => validateSourceStatus({ schema_version: 1, sources: [] }), /來源狀態/);
  assert.throws(() => validateSourceStatus({ ...sourceStatus, sources: [{}] }, policy), /source_id/);
  const duplicate = structuredClone(sourceStatus);
  duplicate.sources.push({ ...duplicate.sources[0] });
  assert.throws(() => validateSourceStatus(duplicate, policy), /source_id/);
  const malformed = structuredClone(sourceStatus);
  malformed.sources[0].intelligence_gaps = { status: "FAILED" };
  assert.throws(() => validateSourceStatus(malformed, policy), /無法驗證/);
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


test("source snapshot requires its gateway collection run", () => {
  const missing = structuredClone(sourceStatus);delete missing.latest_collection_run;
  assert.throws(() => validateSourceStatus(missing, policy), /來源狀態/);
});

test("source snapshot timestamps require explicit timezone and real calendar date", () => {
  for (const value of ["2026-09-11", "Fri, 11 Sep 2026 00:00:00 GMT", "2026-02-30T00:00:00+08:00", "2026-09-11T00:00:00"]) {
    const malformed = structuredClone(sourceStatus);malformed.generated_at=value;
    assert.throws(() => validateSourceStatus(malformed, policy), /來源狀態/, value);
  }
});

test("source snapshot covers exactly the active source policy", () => {
  for (const replacement of [sourceStatus.sources.slice(0,1), sourceStatus.sources.slice(1), sourceStatus.sources.map((row,index)=>index ? row : {...row,source_id:"S-999"})]) {
    const malformed = {...sourceStatus,sources:replacement};
    assert.throws(() => validateSourceStatus(malformed, policy), /來源狀態/);
  }
});

test("source snapshot rejects unsupported producer state values", () => {
  for (const [field,value] of [["source_health","PASSED"],["result","SUCCESS"],["window_completeness","COMPLETE"],["freshness_status","CURRENT"]]) {
    const malformed = structuredClone(sourceStatus);malformed.sources[0][field]=value;
    assert.throws(() => validateSourceStatus(malformed, policy), /來源狀態/, field);
  }
});

test("source snapshot must be the public competition projection", () => {
  assert.throws(() => validateSourceStatus({...sourceStatus,mode:"INTERNAL"}, policy), /來源狀態/);
});


test("source snapshot validates all displayed timestamp fields without mutating valid payload", () => {
  for (const field of ["data_as_of","last_checked_at","last_success_at","next_update_at"]) {
    const malformed=structuredClone(sourceStatus);malformed.sources[0][field]="2026-02-30T00:00:00+08:00";
    assert.throws(()=>validateSourceStatus(malformed,policy),/來源狀態/,field);
  }
  const leap=structuredClone(sourceStatus);leap.generated_at="2028-02-29T12:34:56.123456Z";
  const before=structuredClone(leap);assert.equal(validateSourceStatus(leap,policy),leap);assert.deepEqual(leap,before);
  const failed=structuredClone(sourceStatus);Object.assign(failed.sources[0],{source_health:"FAILED",result:"FAILED",window_completeness:"PARTIAL",freshness_status:"NO_DATA",data_as_of:null,last_success_at:null});
  assert.equal(validateSourceStatus(failed,policy),failed);
});

test("missing or malformed active source policy fails closed", () => {
  for (const invalid of [undefined, {schema_version:1,active_source_ids:[]}, {...policy,active_source_ids:[policy.active_source_ids[0],policy.active_source_ids[0]]}]) {
    assert.throws(()=>validateSourceStatus(sourceStatus,invalid),/來源狀態/);
  }
});
