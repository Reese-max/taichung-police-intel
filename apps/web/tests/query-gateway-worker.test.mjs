import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker from "../../../workers/query-gateway/src/index.js";

const base = new URL("../public/data/", import.meta.url);
const origin = "https://reese-max.github.io/taichung-police-intel";
const endpoint = "https://govintel-query-gateway.example/query";
const mcpEndpoint = "https://govintel-query-gateway.example/mcp";
const env = { PUBLIC_ORIGIN: origin, ALLOWED_ORIGINS: "https://reese-max.github.io" };

async function readPublicationBytes() {
  return Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => [name, await readFile(new URL(name, base))]),
  ));
}

function addFixtureItem(feed, item, { suffix, title, sourceRole, freshnessStatus, officialUrl }) {
  const row = {
    ...item,
    stable_id: `ISSUE32-${suffix}`,
    title,
    source_role: sourceRole,
    freshness_status: freshnessStatus,
    official_url: officialUrl || item.official_url,
    published_at: item.published_at,
    data_as_of: item.data_as_of,
    fetched_at: item.fetched_at,
    content_sha256: createHash("sha256").update(`${suffix}:${title}`).digest("hex"),
  };
  feed.items.push(row);
  return row;
}

test("Worker does not leak upstream failure details to clients", async () => {
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => new Response("upstream exploded", { status: 500 });
  try {
    const response = await worker.fetch(new Request(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://reese-max.github.io" },
      body: JSON.stringify({ tool: "get_current_brief", arguments: {} }),
    }), env);
    assert.equal(response.status, 503);
    const body = await response.json();
    assert.equal(body.error.code, "UPSTREAM_UNAVAILABLE");
    assert.doesNotMatch(JSON.stringify(body), /intelligence-feed\.json|HTTP 500|upstream exploded/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Worker query and MCP answer routes share a server-controlled evidence gate", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = await readPublicationBytes();
  const feed = JSON.parse(bytes["intelligence-feed.json"].toString("utf8"));
  const statusDoc = JSON.parse(bytes["source-status.json"].toString("utf8"));
  const brief = JSON.parse(bytes["v2-daily-brief.json"].toString("utf8"));
  const now = new Date(Date.now() - 1_000).toISOString();
  const collectionRunId = "CR-ISSUE32-WORKER-ANSWER-GATE";
  feed.collection_run_id = collectionRunId;
  feed.generated_at = now;
  statusDoc.latest_collection_run.collection_run_id = collectionRunId;
  statusDoc.latest_collection_run.finished_at = now;
  statusDoc.generated_at = now;
  brief.source_collection_run_id = collectionRunId;
  brief.source_status_generated_at = now;
  brief.generated_at = now;
  brief.publication_status = "READY";
  brief.snapshot_complete = true;

  const item = feed.items.find(row => row.official_url?.startsWith("https://"));
  assert.ok(item, "fixture needs an official evidence locator");
  item.source_role = "PRIMARY_OFFICIAL";
  item.freshness_status = "FRESH";
  const source = statusDoc.sources.find(row => row.source_id === item.source_id);
  assert.ok(source, "fixture needs a matching source status row");
  source.source_health = "PASS";
  source.freshness_status = "FRESH";
  source.window_completeness = "COMPLETE_WITH_ITEMS";
  source.last_checked_at = now;
  source.data_as_of = now;

  const staleItem = addFixtureItem(feed, item, {
    suffix: "STALE",
    title: "交通措施歷史標題",
    sourceRole: "PRIMARY_OFFICIAL",
    freshnessStatus: "STALE",
  });
  const mediaItem = addFixtureItem(feed, item, {
    suffix: "MEDIA",
    title: "媒體影片聲稱目前封路",
    sourceRole: "DISCOVERY_UNVERIFIED",
    freshnessStatus: "FRESH",
    officialUrl: "https://media.example.test/watch/issue-32",
  });
  const unmarkedItem = addFixtureItem(feed, item, {
    suffix: "UNMARKED",
    title: "缺少來源角色標記的資料",
    sourceRole: undefined,
    freshnessStatus: "FRESH",
  });
  bytes["intelligence-feed.json"] = Buffer.from(JSON.stringify(feed), "utf8");
  bytes["source-status.json"] = Buffer.from(JSON.stringify(statusDoc), "utf8");
  bytes["v2-daily-brief.json"] = Buffer.from(JSON.stringify(brief), "utf8");
  globalThis.fetch = async url => {
    const target = new URL(url);
    const name = target.pathname.split("/").at(-1);
    assert.equal(target.href, `${origin}/data/${name}`);
    const content = bytes[name];
    assert.ok(content, `unexpected publication artifact: ${target.pathname}`);
    return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
  };
  const query = async (tool, args) => {
    const response = await worker.fetch(new Request(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://reese-max.github.io" },
      body: JSON.stringify({ tool, arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  const mcp = async (tool, args) => {
    const response = await worker.fetch(new Request(mcpEndpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json", Origin: "https://reese-max.github.io" },
      body: JSON.stringify({ jsonrpc: "2.0", id: "issue-32-answer-gate", method: "tools/call", params: { name: tool, arguments: args } }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    const publication = (await query("get_publication_receipt", {})).body;
    const generation = publication.publication_receipt.generation_id;
    const feedHash = createHash("sha256").update(bytes["intelligence-feed.json"]).digest("hex");
    const all = (await query("search_evidence", { q: "", limit: 1, expected_generation: generation })).body;
    assert.ok(all.total_matches > 1);
    const matching = (await query("search_evidence", { q: item.stable_id, limit: 8, expected_generation: generation })).body;
    assert.equal(matching.total_matches, 1);
    assert.equal(matching.result_count, 1);
    assert.equal(matching.query_coverage.requested_scope.text, item.stable_id);
    assert.equal(matching.results[0].canonical_id, item.stable_id);
    assert.equal(matching.results[0].official_url, item.official_url);
    assert.match(matching.results[0].official_url, /^https:\/\/[^/]+\.gov\.tw\//);
    assert.equal(matching.results[0].canonical_ref.artifact_sha256, feedHash);
    assert.equal(matching.publication_hash, publication.publication_receipt.publication_hash);
    assert.equal(matching.query_generation_id, generation);
    assert.equal(matching.receipt.query_generation_id, generation);
    const noMatch = (await query("search_evidence", { q: "zzzz-govintel-no-match-20260929", limit: 8, expected_generation: generation })).body;
    assert.equal(noMatch.total_matches, 0);
    assert.equal(noMatch.result_count, 0);
    assert.deepEqual(noMatch.results, []);
    assert.ok(Array.isArray(noMatch.source_gaps));
    assert.equal(noMatch.answerable_no_match, noMatch.freshness === "RECENT" && noMatch.source_gaps.length === 0);
    assert.equal(noMatch.query_generation_id, generation);
    const mismatch = await query("search_evidence", { q: item.stable_id, expected_generation: "wrong-generation" });
    assert.equal(mismatch.status, 400);
    assert.equal(mismatch.body.error.code, "INVALID_ARGUMENTS");

    const claims = [
      {
        schema_version: 1,
        claim_id: "official-title",
        text: "caller supplied text must not be rendered",
        claim_type: "STATUS",
        temporal_scope: "CURRENT",
        proposition: { subject: `publication:${item.stable_id}:title`, value: item.title },
        cited_evidence_ids: [`PUB-${item.stable_id}`],
      },
      {
        schema_version: 1,
        claim_id: "unsupported-cause",
        text: "因豪雨提前一小時",
        claim_type: "CAUSE",
        temporal_scope: "CURRENT",
        proposition: { subject: `publication:${item.stable_id}:cause`, value: "豪雨" },
        cited_evidence_ids: [`PUB-${item.stable_id}`],
      },
      {
        schema_version: 1,
        claim_id: "stale-title-as-current",
        text: "目前狀況如下",
        claim_type: "STATUS",
        temporal_scope: "CURRENT",
        proposition: { subject: `publication:${staleItem.stable_id}:title`, value: staleItem.title },
        cited_evidence_ids: [`PUB-${staleItem.stable_id}`],
      },
      {
        schema_version: 1,
        claim_id: "media-only-title",
        text: mediaItem.title,
        claim_type: "STATUS",
        temporal_scope: "CURRENT",
        proposition: { subject: `publication:${mediaItem.stable_id}:title`, value: mediaItem.title },
        cited_evidence_ids: [`PUB-${mediaItem.stable_id}`],
      },
      {
        schema_version: 1,
        claim_id: "unmarked-title",
        text: unmarkedItem.title,
        claim_type: "STATUS",
        temporal_scope: "CURRENT",
        proposition: { subject: `publication:${unmarkedItem.stable_id}:title`, value: unmarkedItem.title },
        cited_evidence_ids: [`PUB-${unmarkedItem.stable_id}`],
      },
    ];
    const queryGate = await query("validate_answer", { claims });
    const mcpGate = await mcp("validate_answer", { claims });
    assert.equal(queryGate.status, 200);
    assert.equal(mcpGate.status, 200);
    assert.equal(mcpGate.body.result.isError, false);
    const queryAnswer = queryGate.body;
    const mcpAnswer = mcpGate.body.result.structuredContent;
    for (const answer of [queryAnswer, mcpAnswer]) {
      assert.equal(answer.gate_status, "QUALIFIED");
      assert.deepEqual(Object.fromEntries(answer.final_claims.map(claim => [claim.claim_id, claim.support_status])), {
        "official-title": "SUPPORTED",
        "unsupported-cause": "UNSUPPORTED",
        "stale-title-as-current": "STALE",
        "media-only-title": "UNSUPPORTED",
        "unmarked-title": "UNSUPPORTED",
      });
      assert.ok(answer.answer.some(text => text.includes("官方來源已核對")));
      assert.ok(answer.answer.some(text => text.includes("官方資料可能已過期")));
      assert.ok(answer.answer.includes("官方來源未說明原因。"));
      assert.ok(answer.answer.every(text => !text.includes("因豪雨提前一小時") && !text.includes(mediaItem.title) && !text.includes(unmarkedItem.title)));
      const receipt = answer.answer_evidence_receipt;
      assert.equal(receipt.validator_version, "answer-evidence-gate/3");
      assert.deepEqual(receipt.claim_ids, claims.map(claim => claim.claim_id));
      assert.equal(receipt.publication_hash, answer.publication_hash);
      assert.ok(receipt.evidence_ids.includes(`PUB-${item.stable_id}`));
      assert.ok(receipt.evidence_ids.includes(`PUB-${staleItem.stable_id}`));
      assert.equal(receipt.evidence_ids.includes(`PUB-${mediaItem.stable_id}`), false);
      assert.equal(receipt.evidence_ids.includes(`PUB-${unmarkedItem.stable_id}`), false);
    }
    assert.deepEqual(mcpAnswer.answer, queryAnswer.answer);
    assert.deepEqual(mcpAnswer.final_claims, queryAnswer.final_claims);
    assert.deepEqual(mcpAnswer.answer_evidence_receipt, queryAnswer.answer_evidence_receipt);

    const forged = await query("validate_answer", { claims: [{ ...claims[1], evidence: [{ evidence_id: "CALLER-FORGED" }] }] });
    assert.equal(forged.status, 400);
    assert.equal(forged.body.error.code, "INVALID_ARGUMENTS");
  } finally {
    globalThis.fetch = originalFetch;
  }
});


test("Worker answer gate host fails closed on a refused or partially indexed validation", async () => {
  // The Worker is the deployed Web/MCP gate host; it must refuse a verdict the
  // validator never actually reached instead of stamping the publication hash
  // onto it and returning a normal 200 answer_evidence envelope.
  const { validateAnswer } = await import("../../../workers/query-gateway/src/index.js");
  const snapshot = {
    brief: { generated_at: "2026-09-21T09:00:00Z" },
    generatedFrom: { brief_sha256: "f".repeat(64) },
    sources: [{
      source_id: "S-009",
      source_health: "PASS",
      window_completeness: "COMPLETE_WITH_ITEMS",
      freshness_status: "FRESH",
      last_checked_at: "2026-09-21T08:00:00Z",
    }],
    items: [{
      canonical_id: "ITEM-1",
      stable_id: "ITEM-1",
      title: "交通管制提前至 16:00",
      source_id: "S-009",
      source_role: "PRIMARY_OFFICIAL",
      official_url: "https://example.gov.tw/traffic/notice-1",
      content_sha256: "e".repeat(64),
      trust_tier: "CANONICAL_PUBLICATION",
      published_at: "2026-09-21T07:00:00Z",
    }],
  };
  const claim = {
    schema_version: 1,
    claim_id: "traffic-time",
    text: "交通管制提前至 16:00",
    claim_type: "TIME",
    temporal_scope: "CURRENT",
    proposition: { subject: "traffic:start", value: "16:00" },
  };
  // A refused verdict carries a full evidence count on purpose: the refusal
  // alone must fail the request, independently of the coverage check.
  await assert.rejects(
    () => validateAnswer(snapshot, [claim], () => ({
      gate_status: "BLOCKED",
      final_claims: [],
      removed_claims: [],
      receipt: {
        schema_version: 1, validator_version: "answer-evidence-gate/3", gate_status: "BLOCKED",
        failure_reason: "DUPLICATE_EVIDENCE_ID", publication_hash: "f".repeat(64),
        indexed_evidence_count: 1, claim_ids: [], evidence_ids: [], claims: [],
      },
    })),
    (error) => {
      assert.equal(error.code, "GATE_FAILED");
      assert.equal(error.status, 503);
      return true;
    },
  );

  await assert.rejects(
    () => validateAnswer(snapshot, [claim], () => ({
      gate_status: "QUALIFIED",
      final_claims: [],
      removed_claims: [],
      receipt: {
        schema_version: 1, validator_version: "answer-evidence-gate/3", gate_status: "QUALIFIED",
        publication_hash: "f".repeat(64), claim_ids: [], evidence_ids: [], indexed_evidence_count: 0, claims: [],
      },
    })),
    (error) => {
      assert.equal(error.code, "GATE_FAILED");
      assert.equal(error.status, 503);
      return true;
    },
  );
});
