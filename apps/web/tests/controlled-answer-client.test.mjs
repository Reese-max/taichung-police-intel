import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { validateControlledAnswer } from "../lib/controlled-answer-client.js";

const endpoint = "https://gateway.example/query";
const context = { codeSha: "a".repeat(40), publicationGeneration: "run-62" };
const release = {
  schema_version: 1, code_sha: context.codeSha, publication_generation: context.publicationGeneration, release_id: "b".repeat(64),
  publication_hash: "c".repeat(64), artifact_hashes: { feed: "d".repeat(64), status: "e".repeat(64), brief: "c".repeat(64) },
  source_policy_hash: "f".repeat(64), query_generation: "1".repeat(64), evidence_catalog_hash: "2".repeat(64), built_at: "2026-10-01T00:00:00Z",
};
const query = {
  query_generation_id: release.query_generation, publication_hash: release.publication_hash,
  results: [{ canonical_id: "official-62", title: "官方公告" }],
};
const answer = {
  release, query_generation_id: query.query_generation_id, publication_hash: query.publication_hash,
  publication_id: release.publication_generation, policy: { policy_hash: release.source_policy_hash },
  receipt: { publication_hash: release.publication_hash, query_generation_id: release.query_generation },
  gate_status: "PASS", answer: ["已核對官方公告"], answer_evidence_receipt: {
    schema_version: 1, validator_version: "answer-evidence-gate/3", renderer_version: "controlled-answer-renderer/1",
    gate_status: "PASS", publication_hash: query.publication_hash,
    answer_sha256: createHash("sha256").update(JSON.stringify(["已核對官方公告"])).digest("hex"),
    claim_ids: ["publication-official-62"], claims: [{ claim_id: "publication-official-62", claim_type: "STATUS",
      original_text: "官方公告", support_status: "SUPPORTED", propositions: [{ subject: "publication:official-62:title", value: "官方公告" }] }],
  },
};

test("controlled answers pin the displayed query generation and the current release", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push([url, options]);
    return Response.json(url.endsWith("release.json") ? release : answer);
  });
  assert.deepEqual(await validateControlledAnswer(endpoint, query, context), answer);
  const request = JSON.parse(calls[1][1].body);
  assert.equal(request.release_id, release.release_id);
  assert.equal(request.tool, "validate_answer");
  assert.equal(request.arguments.expected_generation, query.query_generation_id);
  assert.deepEqual(request.arguments.claims[0].cited_evidence_ids, ["PUB-official-62"]);
});

test("a release change before answer validation prevents the answer request", async t => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => {
    calls += 1;
    return Response.json({ ...release, code_sha: "d".repeat(40) });
  });
  await assert.rejects(validateControlledAnswer(endpoint, query, context), /版本/);
  assert.equal(calls, 1);
});

test("answers from another query generation or publication are never displayed", async t => {
  for (const field of ["query_generation_id", "publication_hash"]) {
    t.mock.method(globalThis, "fetch", async url => Response.json(url.endsWith("release.json") ? release : { ...answer, [field]: "different" }));
    await assert.rejects(validateControlledAnswer(endpoint, query, context), /版本/);
    t.mock.restoreAll();
  }
});

test("refused or malformed gate envelopes fail closed", async t => {
  for (const changed of [{ gate_status: "BLOCKED" }, { answer_evidence_receipt: null }, { answer: null }]) {
    t.mock.method(globalThis, "fetch", async url => Response.json(url.endsWith("release.json") ? release : { ...answer, ...changed }));
    await assert.rejects(validateControlledAnswer(endpoint, query, context), /證據/);
    t.mock.restoreAll();
  }
});
