import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { createHash } from "node:crypto";

import { CLAIM_SCHEMA_VERSION, EVIDENCE_SCHEMA_VERSION, canonicalize } from "../lib/answer-evidence-gate.js";

// The gate host (answer-gate-runner.mjs) is the only thing that binds a gate
// verdict to a publication. Both hosts below used to stamp the caller's own
// publication_hash / evidence_catalog_hash onto whatever receipt the validator
// returned — including a receipt that says the validator refused the draft —
// so a BLOCKED or partially indexed validation was released as a normal
// answer_evidence response. Fail closed instead: non-zero exit, no receipt.

const RUNNER = fileURLToPath(new URL("../../../scripts/answer-gate-runner.mjs", import.meta.url));
const PUBLICATION_HASH = "a".repeat(64);

const CLAIM = {
  schema_version: CLAIM_SCHEMA_VERSION,
  text: "交通管制提前至 16:00 開始",
  claim_type: "TIME",
  temporal_scope: "CURRENT",
  proposition: { subject: "交通管制開始時間", value: "16:00" },
};

function evidence(overrides = {}) {
  return {
    schema_version: EVIDENCE_SCHEMA_VERSION,
    evidence_id: "EV-S009-TRA-1600",
    evidence_type: "WRITTEN_OFFICIAL",
    source_id: "S-009",
    locator: "https://example.gov.tw/traffic/notice-1#p2",
    document_version: "d1",
    is_current: true,
    assertions: [{ subject: "交通管制開始時間", value: "16:00" }],
    ...overrides,
  };
}

const EVIDENCE_CATALOG_HASH = createHash("sha256").update(canonicalize([evidence()]), "utf8").digest("hex");

function run(payload) {
  return spawnSync(process.execPath, [RUNNER], {
    input: JSON.stringify({
      claims: [CLAIM],
      evidence: [evidence()],
      generated_at: "2026-09-21T00:00:00Z",
      publication_hash: PUBLICATION_HASH,
      evidence_catalog_hash: EVIDENCE_CATALOG_HASH,
      ...payload,
    }),
    encoding: "utf8",
  });
}

function releasedReceipt(result) {
  assert.equal(result.status, 0, `runner must fail closed: ${result.stderr}`);
  return JSON.parse(result.stdout).receipt;
}

test("runner releases a receipt bound to the publication for a fully indexed catalog", () => {
  const receipt = releasedReceipt(run({}));
  assert.equal(receipt.gate_status, "PASS");
  assert.equal(receipt.publication_hash, PUBLICATION_HASH);
  assert.equal(receipt.evidence_catalog_hash, EVIDENCE_CATALOG_HASH);
  assert.equal(receipt.indexed_evidence_count, 1);
});

test("runner fails closed instead of stamping a publication binding on a refused gate", () => {
  const result = run({ evidence: [evidence(), evidence()] });
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /evidence|DUPLICATE|duplicate/i);
  assert.doesNotMatch(result.stdout, /"receipt"/);
});

test("runner fails closed when the gate cannot index part of the evidence catalog", () => {
  const foreign = evidence({ evidence_id: "EV-S014-TRA-1700", schema_version: EVIDENCE_SCHEMA_VERSION + 1 });
  const result = run({ evidence: [evidence(), foreign] });
  assert.notEqual(result.status, 0);
  assert.doesNotMatch(result.stdout, /"receipt"/);
});
