import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { buildControlledAnswerClaims, validateControlledAnswer } from "../lib/controlled-answer-client.js";

const query = { query_generation_id: "query-32", publication_hash: "c".repeat(64),
  results: [{ canonical_id: "official-32", title: "官方公告" }] };
const statement = (claim_type, value) => {
  const proposition = { subject: `event:32:${claim_type.toLowerCase()}`, value };
  const text = claim_type === "STATISTIC" ? `${value.value} ${value.unit}（${value.period}，${value.geography}）` : String(value);
  return { text, claim_type, temporal_scope: "CURRENT", proposition,
    cited_evidence_ids: ["PUB-official-32"] };
};
const draft = { schema_version: 1, statements: [statement("TIME", "16:00"), statement("LOCATION", "西屯") ] };
const endpoint = "http://localhost/query";
function gateResponse(claims, answer = ["未找到可驗證的官方證據，此項說法已移除。"] ) {
  return { query_generation_id: query.query_generation_id, publication_hash: query.publication_hash,
    gate_status: "QUALIFIED", answer, answer_evidence_receipt: {
      schema_version: 1, validator_version: "answer-evidence-gate/3", renderer_version: "controlled-answer-renderer/1",
      gate_status: "QUALIFIED",
      publication_hash: query.publication_hash,
      answer_sha256: createHash("sha256").update(JSON.stringify(answer)).digest("hex"),
      claim_ids: claims.map(c => c.claim_id),
      claims: claims.map(c => ({ claim_id: c.claim_id, claim_type: c.claim_type, support_status: "UNSUPPORTED",
        original_text: c.text, propositions: [c.proposition] })),
    } };
}

test("a supplied typed draft sends every factual statement instead of replacing it with titles", () => {
  const statements = [statement("TIME", "16:00"), statement("LOCATION", "西屯"), statement("CAUSE", "豪雨"),
    statement("STATISTIC", { value: "10", period: "2026-10", geography: "臺中", unit: "件" })];
  const claims = buildControlledAnswerClaims({ ...query, draft: { schema_version: 1, statements } });
  assert.deepEqual(claims.map(c => c.claim_type), ["TIME", "LOCATION", "CAUSE", "STATISTIC"]);
  assert.deepEqual(claims.map(c => c.proposition), statements.map(s => s.proposition));
});

test("free prose and unmapped draft text fail closed instead of being silently discarded", () => {
  for (const bad of ["因豪雨而於16:00提前交通管制", { ...draft, text: "另有未核對原因" }, { schema_version: 1, statements: [] }]) {
    assert.throws(() => buildControlledAnswerClaims({ ...query, draft: bad }), /草稿|逐句|claim/i);
  }
});

test("each draft statement must be factual, typed, bounded and citation-mapped", () => {
  for (const changed of [{ claim_type: "OTHER" }, { proposition: null }, { cited_evidence_ids: [] }, { text: "" },
    { evidence: [{ evidence_id: "CALLER-FORGED" }] }, { text: "16:00，因為豪雨" }]) {
    assert.throws(() => buildControlledAnswerClaims({ ...query, draft: {
      schema_version: 1, statements: [{ ...draft.statements[0], ...changed }],
    } }), /草稿|逐句|claim/i);
  }
});

test("a gate receipt that omits a draft claim cannot release an answer", async t => {
  const claims = draft.statements.map((s, i) => ({ ...s, schema_version: 1, claim_id: `draft-${i + 1}` }));
  t.mock.method(globalThis, "fetch", async () => Response.json(gateResponse(claims.slice(0, 1))));
  await assert.rejects(validateControlledAnswer(endpoint, { ...query, draft }), /逐句|收據|證據/);
});

test("an answer changed after its controlled-renderer receipt is refused", async t => {
  const claims = [{ claim_id: "publication-official-32", claim_type: "STATUS", text: "官方公告",
    proposition: { subject: "publication:official-32:title", value: "官方公告" } }];
  const response = gateResponse(claims);
  response.answer = ["因豪雨，16:00封路。"];
  t.mock.method(globalThis, "fetch", async () => Response.json(response));
  await assert.rejects(validateControlledAnswer(endpoint, query), /收據|證據/);
});

test("claim text cannot smuggle an unsupported second fact behind a supported proposition", () => {
  assert.throws(() => buildControlledAnswerClaims({ ...query, draft: { schema_version: 1, statements: [{
    text: "官方公告，因豪雨而於16:00封路。", claim_type: "STATUS", temporal_scope: "CURRENT",
    proposition: { subject: "publication:official-32:title", value: "官方公告" }, cited_evidence_ids: ["PUB-official-32"],
  }] } }), /逐句|草稿/);
});

test("a receipt for another proposition or claim type cannot cover a draft statement", async t => {
  const claims = [{ claim_id: "publication-official-32", claim_type: "STATUS", text: "官方公告",
    proposition: { subject: "publication:official-32:title", value: "官方公告" } }];
  for (const change of [{ propositions: [{ subject: "other:title", value: "官方公告" }] },
    { claim_type: "CAUSE" }, { original_text: "額外文字" }]) {
    const response = gateResponse(claims);
    Object.assign(response.answer_evidence_receipt.claims[0], change);
    t.mock.method(globalThis, "fetch", async () => Response.json(response));
    await assert.rejects(validateControlledAnswer(endpoint, query), /收據|證據/);
    t.mock.restoreAll();
  }
});
