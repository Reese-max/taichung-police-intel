import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildSnapshot, createReleaseManifest } from "../../../workers/query-gateway/src/index.js";
import { runControlledAnswerPipeline } from "../lib/controlled-answer-client.js";

const origin = "https://publication.example.test";
const endpoint = "https://gateway.example.test/query";
const codeSha = "a".repeat(40);
const env = { PUBLIC_ORIGIN: origin, ALLOWED_ORIGINS: "https://web.example.test", CF_VERSION_METADATA: { id: "offline-fixture", tag: codeSha } };
let fixtureNumber = 0;

async function actualWorkerFixture(t, title = "官方索引標題") {
  // Each independently constructed publication must get a fresh actual Worker
  // cache, rather than reusing a release from another fixture's timestamp.
  const { default: worker } = await import(`../../../workers/query-gateway/src/index.js?draftFixture=${++fixtureNumber}`);
  const policyBytes = await readFile(new URL("../public/data/source-policy.json", import.meta.url));
  const policy = JSON.parse(policyBytes);
  const stamp = new Date(Date.now() - 1000).toISOString();
  const run = "CR-OFFLINE-DRAFT-PIPELINE";
  const sourceId = policy.active_source_ids[0];
  const encode = value => Buffer.from(JSON.stringify(value));
  const values = {
    "source-policy.json": policyBytes,
    "intelligence-feed.json": encode({ schema_version: 1, collection_run_id: run, generated_at: stamp,
      items: [{ stable_id: "DRAFT-OFFICIAL", title, source_id: sourceId, source_role: "PRIMARY_OFFICIAL",
        official_url: "https://example.gov.tw/offline-notice", published_at: stamp, data_as_of: stamp, fetched_at: stamp,
        freshness_status: "FRESH", content_sha256: "b".repeat(64) }] }),
    "source-status.json": encode({ schema_version: 1, generated_at: stamp,
      latest_collection_run: { collection_run_id: run, status: "SUCCEEDED" },
      sources: policy.active_source_ids.map(id => ({ source_id: id, source_name: `Offline fixture ${id}`,
        source_health: "PASS", window_completeness: "COMPLETE_WITH_ITEMS", result: "NEW_ITEMS",
        freshness_status: "FRESH", last_checked_at: stamp, data_as_of: stamp })) }),
    "v2-daily-brief.json": encode({ schema_version: 1, generated_at: stamp, source_status_generated_at: stamp,
      source_collection_run_id: run, publication_status: "READY", snapshot_complete: true }),
  };
  const readArtifact = async name => ({ bytes: values[name], hash: createHash("sha256").update(values[name]).digest("hex") });
  const snapshot = await buildSnapshot(env, readArtifact);
  const release = await createReleaseManifest(snapshot, codeSha);
  t.mock.method(globalThis, "fetch", async url => {
    const name = String(url).split("/").at(-1);
    if (name === "release.json") return Response.json(release);
    if (!Object.hasOwn(values, name)) throw new Error(`No external calls allowed: ${url}`);
    return new Response(values[name], { status: 200 });
  });
  const calls = [];
  const context = { codeSha, publicationGeneration: run, fetchImpl: async (url, options) => {
    if (String(url).endsWith("release.json")) return Response.json(release);
    const body = JSON.parse(options.body);
    calls.push(body.tool);
    const response = await worker.fetch(new Request(endpoint, { ...options,
      headers: { ...options.headers, Origin: "https://web.example.test" } }), env);
    return response;
  } };
  return { context, calls, sourceId };
}

test("actual query transport always gates its generated metadata draft before returning an answer", async t => {
  const fixture = await actualWorkerFixture(t);
  const result = await runControlledAnswerPipeline(endpoint, { q: "官方", limit: 1 }, fixture.context);
  assert.deepEqual(fixture.calls, ["search_evidence", "validate_answer"]);
  assert.equal(result.draft_kind, "PUBLICATION_METADATA");
  assert.equal(result.answer.gate_status, "PASS");
  assert.equal(result.answer.answer_evidence_receipt.claims.length, 1);
  assert.match(result.answer.answer[0], /官方索引標題/);
});

test("all typed adapter statements reach the actual Worker and unlocated facts are qualified", async t => {
  const fixture = await actualWorkerFixture(t);
  const result = await runControlledAnswerPipeline(endpoint, { q: "官方", limit: 1 }, fixture.context, async response => {
    fixture.calls.push("structured_draft_adapter");
    assert.equal(response.results[0].canonical_id, "DRAFT-OFFICIAL");
    const statements = [
      { claim_type: "TIME", text: "16:00", proposition: { subject: "event:offline:time", value: "16:00" } },
      { claim_type: "LOCATION", text: "西屯", proposition: { subject: "event:offline:location", value: "西屯" } },
      { claim_type: "CAUSE", text: "豪雨", proposition: { subject: "event:offline:cause", value: "豪雨" } },
      { claim_type: "STATISTIC", text: "10 件（2026-10，臺中）", proposition: { subject: "statistic:offline:count",
        value: { value: "10", period: "2026-10", geography: "臺中", unit: "件" } } },
    ].map(s => ({ ...s, temporal_scope: "CURRENT", cited_evidence_ids: ["PUB-DRAFT-OFFICIAL"] }));
    return { schema_version: 1, statements };
  });
  assert.deepEqual(fixture.calls, ["search_evidence", "structured_draft_adapter", "validate_answer"]);
  assert.equal(result.draft_kind, "STRUCTURED_ADAPTER");
  assert.deepEqual(result.answer.answer_evidence_receipt.claims.map(c => c.claim_type), ["TIME", "LOCATION", "CAUSE", "STATISTIC"]);
  assert.ok(result.answer.answer_evidence_receipt.claims.every(c => c.support_status === "UNSUPPORTED"));
  assert.equal(result.answer.gate_status, "QUALIFIED");
  assert.match(result.answer.answer.join(" "), /官方來源未說明原因/);
  assert.doesNotMatch(result.answer.answer.join(" "), /16:00|西屯|豪雨|10 件/);
});

test("an arbitrary prose adapter cannot issue a gate request or release a title-only answer", async t => {
  const fixture = await actualWorkerFixture(t);
  await assert.rejects(runControlledAnswerPipeline(endpoint, { q: "官方" }, fixture.context,
    async () => "因豪雨，16:00封路"), /草稿|逐句/);
  assert.deepEqual(fixture.calls, ["search_evidence"]);
});

test("a genuine empty query does not invent a draft or bypass its coverage result", async t => {
  const fixture = await actualWorkerFixture(t);
  let adapterCalls = 0;
  const result = await runControlledAnswerPipeline(endpoint, { q: "NO-OFFLINE-MATCH" }, fixture.context,
    async () => { adapterCalls++; throw new Error("must not invent a draft"); });
  assert.equal(result.answer, null);
  assert.equal(result.draft_kind, "NO_MATCH");
  assert.equal(adapterCalls, 0);
  assert.deepEqual(fixture.calls, ["search_evidence"]);
  assert.equal(result.response.query_coverage.status, "COVERED_BOUNDED_SCOPE");
});

const statistic = { value: "10", period: "官方期", geography: "臺中", unit: "件" };
const compoundTitle = "官方公告因豪雨，16:00封路，10件";

for (const claimType of ["TIME", "CAUSE", "LOCATION", "STATISTIC", "AGENCY"]) {
  for (const field of ["title", "source_id"]) {
    test(`actual metadata ${field} cannot be laundered as ${claimType}, including normalized namespaces`, async t => {
      const title = claimType === "STATISTIC" ? "10 件（官方期，臺中）" : compoundTitle;
      const fixture = await actualWorkerFixture(t, title);
      const subject = `publication:DRAFT-OFFICIAL:${field}`;
      const subjects = [subject, subject.replace("publication", "publi cation"),
        subject.replace("publication:", "publication :"), subject.replace("publication", "publi\u00a0cation"),
        subject.replace(field, field === "title" ? "ti tle" : "source_ id")];
      for (const candidate of subjects) {
        fixture.calls.length = 0;
        const value = claimType === "STATISTIC" ? statistic : field === "title" ? title : fixture.sourceId;
        const text = claimType === "STATISTIC" ? title : value;
        await assert.rejects(runControlledAnswerPipeline(endpoint, { q: "官方" }, fixture.context, async () => ({
          schema_version: 1, statements: [{ text, claim_type: claimType, temporal_scope: "CURRENT",
            proposition: { subject: candidate, value }, cited_evidence_ids: ["PUB-DRAFT-OFFICIAL"] }],
        })), /索引|metadata/);
        assert.deepEqual(fixture.calls, ["search_evidence"], candidate);
      }
    });
  }
}

test("actual STATUS preserves compound publication titles and source metadata without asserting embedded event facts", async t => {
  const fixture = await actualWorkerFixture(t, compoundTitle);
  const result = await runControlledAnswerPipeline(endpoint, { q: "官方" }, fixture.context, async response => ({
    schema_version: 1, statements: [
      { text: response.results[0].title, claim_type: "STATUS", temporal_scope: "CURRENT",
        proposition: { subject: "publication:DRAFT-OFFICIAL:title", value: response.results[0].title },
        cited_evidence_ids: ["PUB-DRAFT-OFFICIAL"] },
      { text: response.results[0].source_id, claim_type: "STATUS", temporal_scope: "CURRENT",
        proposition: { subject: "publi cation:DRAFT-OFFICIAL:source_ id", value: response.results[0].source_id },
        cited_evidence_ids: ["PUB-DRAFT-OFFICIAL"] },
    ],
  }));
  assert.deepEqual(fixture.calls, ["search_evidence", "validate_answer"]);
  assert.deepEqual(result.answer.answer_evidence_receipt.claims.map(c => [c.claim_type, c.support_status,
    c.propositions[0].subject]), [
    ["STATUS", "SUPPORTED", "publication:DRAFT-OFFICIAL:title"],
    ["STATUS", "SUPPORTED", "publication:DRAFT-OFFICIAL:source_id"],
  ]);
  assert.match(result.answer.answer[0], /官方公告因豪雨/);
});

test("unknown DATE and DISTANCE metadata labels remain rejected before the actual gate", async t => {
  const fixture = await actualWorkerFixture(t, compoundTitle);
  for (const claimType of ["DATE", "DISTANCE"]) {
    fixture.calls.length = 0;
    await assert.rejects(runControlledAnswerPipeline(endpoint, { q: "官方" }, fixture.context, async () => ({
      schema_version: 1, statements: [{ text: compoundTitle, claim_type: claimType, temporal_scope: "CURRENT",
        proposition: { subject: "publication:DRAFT-OFFICIAL:title", value: compoundTitle },
        cited_evidence_ids: ["PUB-DRAFT-OFFICIAL"] }],
    })), /草稿|逐句/);
    assert.deepEqual(fixture.calls, ["search_evidence"]);
  }
});
