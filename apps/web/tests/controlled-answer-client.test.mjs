import assert from "node:assert/strict";
import test from "node:test";
import { validateControlledAnswer } from "../lib/controlled-answer-client.js";

const endpoint = "https://gateway.example/query";
const context = { codeSha: "a".repeat(40), publicationGeneration: "run-62" };
const release = { code_sha: context.codeSha, publication_generation: context.publicationGeneration, release_id: "b".repeat(64) };
const query = {
  query_generation_id: "query-62", publication_hash: "c".repeat(64),
  results: [{ canonical_id: "official-62", title: "官方公告" }],
};
const answer = {
  release, query_generation_id: query.query_generation_id, publication_hash: query.publication_hash,
  gate_status: "PASS", answer: ["已核對官方公告"], answer_evidence_receipt: { validator_version: "answer-evidence-gate/3" },
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
