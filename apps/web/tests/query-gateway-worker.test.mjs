import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker, { buildSnapshot, createReleaseManifest } from "../../../workers/query-gateway/src/index.js";

const base = new URL("../public/data/", import.meta.url);
const origin = "https://reese-max.github.io/taichung-police-intel";
const endpoint = "https://govintel-query-gateway.example/query";
const mcpEndpoint = "https://govintel-query-gateway.example/mcp";
const env = { PUBLIC_ORIGIN: origin, ALLOWED_ORIGINS: "https://reese-max.github.io", CF_VERSION_METADATA: { id: "test-worker", tag: "a".repeat(40) } };
const artifactNames = ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"];


// Extend each independently controlled publication fixture with its matching
// release; the production Worker must never bypass release admission for tests.
function withFixtureRelease(fetcher) {
  return async (url, options) => {
    if (!String(url).endsWith("/release.json")) return fetcher(url, options);
    const snapshot = await buildSnapshot(env, async name => {
      const response = await fetcher(`${origin}/data/${name}`, options);
      if (!response.ok) throw new Error("fixture publication is unavailable");
      const bytes = await response.arrayBuffer();
      return { bytes, hash: createHash("sha256").update(new Uint8Array(bytes)).digest("hex") };
    });
    return Response.json(await createReleaseManifest(snapshot, env.CF_VERSION_METADATA.tag));
  };
}

// Positive freshness cases need a complete synthetic publication, independent
// of restored production rows, failures and dates. Keep the approved policy and
// exercise the real release/evidence gates; this fixture says nothing about the
// current availability or freshness of the official sources.
async function completePublicationFixture() {
  const policyBytes = await readFile(new URL("source-policy.json", base));
  const policy = JSON.parse(policyBytes.toString("utf8"));
  const stamp = new Date(Date.now() - 1_000).toISOString();
  const collectionRunId = "CR-TEST-COMPLETE-PUBLICATION";
  const encode = value => Buffer.from(JSON.stringify(value), "utf8");
  const sourceId = policy.active_source_ids[0];
  const feed = {
    schema_version: 1, collection_run_id: collectionRunId, generated_at: stamp,
    items: [1, 2].map(index => ({
      stable_id: `WORKER-FIXTURE-${index}`, title: `測試官方標題 ${index}`,
      source_id: sourceId, source_role: "PRIMARY_OFFICIAL",
      official_url: `https://example.gov.tw/notices/${index}`,
      published_at: stamp, data_as_of: stamp, fetched_at: stamp,
      source_health: "PASS", window_completeness: "COMPLETE_WITH_ITEMS", freshness_status: "FRESH",
      content_sha256: createHash("sha256").update(`worker-fixture-${index}`).digest("hex"),
    })),
  };
  const status = {
    schema_version: 1, generated_at: stamp,
    latest_collection_run: { collection_run_id: collectionRunId, finished_at: stamp, status: "SUCCEEDED" },
    sources: policy.active_source_ids.map(id => ({
      source_id: id, source_name: `Synthetic fixture ${id}`, source_health: "PASS",
      window_completeness: id === sourceId ? "COMPLETE_WITH_ITEMS" : "COMPLETE_ZERO",
      result: "NO_NEW_ITEM", freshness_status: "FRESH", data_as_of: stamp,
      last_checked_at: stamp, last_success_at: stamp,
    })),
  };
  const brief = {
    schema_version: 1, generated_at: stamp, source_status_generated_at: stamp,
    source_collection_run_id: collectionRunId, publication_status: "READY", snapshot_complete: true,
  };
  return {
    "intelligence-feed.json": encode(feed),
    "source-status.json": encode(status),
    "v2-daily-brief.json": encode(brief),
    "source-policy.json": policyBytes,
  };
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
  const bytes = await completePublicationFixture();
  const feed = JSON.parse(bytes["intelligence-feed.json"].toString("utf8"));
  const statusDoc = JSON.parse(bytes["source-status.json"].toString("utf8"));
  const brief = JSON.parse(bytes["v2-daily-brief.json"].toString("utf8"));
  const now = new Date(Date.now() - 1_000).toISOString();
  const collectionRunId = "CR-ISSUE32-WORKER-ANSWER-GATE";
  const noMatchTerm = "zzzz-govintel-no-match-20261001";
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

  globalThis.fetch = withFixtureRelease(async url => {
    const target = new URL(url);
    const name = target.pathname.split("/").at(-1);
    assert.equal(target.href, `${origin}/data/${name}`);
    const content = bytes[name];
    assert.ok(content, `unexpected publication artifact: ${target.pathname}`);
    return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
  });
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

    const exact = (await query("search_evidence", { canonical_id: item.stable_id, limit: 8, expected_generation: generation })).body;
    assert.equal(exact.total_matches, 1);
    assert.equal(exact.result_count, 1);
    assert.equal(exact.query_coverage.requested_scope.canonical_id, item.stable_id);
    assert.equal(exact.results[0].canonical_id, item.stable_id);
    assert.equal(exact.results[0].canonical_ref.evidence_id, `PUB-${item.stable_id}`);
    assert.match(exact.results[0].canonical_ref.document_version_id, /^DOCV-[A-F0-9]{20}$/);

    const unscoped = (await query("search_evidence", { limit: 1, expected_generation: generation })).body;
    assert.deepEqual(Object.keys(unscoped.query_coverage.requested_scope), []);

    const noMatch = (await query("search_evidence", { q: noMatchTerm, limit: 8, expected_generation: generation })).body;

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

test("Worker rejects a cursor offset beyond the filtered result set", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => [name, await readFile(new URL(name, base))]),
  ));
  globalThis.fetch = withFixtureRelease(async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    return new Response(bytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
  });
  const query = async (tool, args) => {
    const response = await worker.fetch(new Request(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool, arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    const publication = (await query("get_publication_receipt", {})).body;
    const generation = publication.publication_receipt.generation_id;
    const all = (await query("search_evidence", { limit: 1 })).body;
    const filterHash = createHash("sha256").update("[null,null,null,null]").digest("hex");
    const cursor = Buffer.from(JSON.stringify({ generation, filters: filterHash, offset: all.total_matches + 1 }))
      .toString("base64").replace(/\+/g, "-").replace(/\//g, "_");
    const paged = await query("search_evidence", { cursor, limit: 8 });
    assert.equal(paged.status, 400);
    assert.equal(paged.body.error.code, "INVALID_ARGUMENTS");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Worker serves the last good snapshot when a rebuild fails", async () => {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const freshBytes = await completePublicationFixture();
  const query = async (instance, args) => {
    const response = await instance.fetch(new Request(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: "search_evidence", arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    const healthy = (await import("../../../workers/query-gateway/src/index.js?snapshot-fallback")).default;
    let upstreamCalls = 0;
    globalThis.fetch = withFixtureRelease(async url => {
      upstreamCalls += 1;
      const name = new URL(url).pathname.split("/").at(-1);
      return new Response(freshBytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const noMatchTerm = "zzzz-govintel-no-match-20261001";
    const first = await query(healthy, { q: noMatchTerm, limit: 1 });
    assert.equal(first.status, 200);
    // Baseline: the same snapshot is fresh and may answer a bounded no-match,
    // so every degraded assertion below has a RECENT state to contradict.
    assert.equal(first.body.freshness, "RECENT");
    assert.deepEqual(first.body.source_gaps, []);
    assert.equal(first.body.answerable_no_match, true);
    assert.equal(first.body.query_coverage.can_state_bounded_no_match, true);

    globalThis.fetch = async () => { upstreamCalls += 1; return new Response("upstream", { status: 503 }); };
    Date.now = () => originalNow() + 31_000;
    const degraded = await query(healthy, { q: noMatchTerm, limit: 1 });
    assert.equal(degraded.status, 200);
    assert.equal(degraded.body.query_generation_id, first.body.query_generation_id);
    // Serving the last usable index must stay visibly degraded: an unlabelled
    // projection would let a zero-match query read as a current answer.
    assert.equal(degraded.body.freshness, "STALE");
    assert.equal(degraded.body.answerable_no_match, false);
    assert.equal(degraded.body.query_coverage.can_state_bounded_no_match, false);
    assert.equal(degraded.body.query_coverage.status, "PARTIAL");
    const gap = degraded.body.source_gaps.find((row) => row.reason === "INDEX_REBUILD_FAILED");
    assert.ok(gap, JSON.stringify(degraded.body.source_gaps));
    assert.ok(Date.parse(gap.since) > 0);

    const failures = upstreamCalls;
    Date.now = () => originalNow() + 32_000;
    const held = await query(healthy, { q: noMatchTerm, limit: 1 });
    assert.equal(held.body.source_gaps.find((row) => row.reason === "INDEX_REBUILD_FAILED").since, gap.since);
    assert.equal(upstreamCalls, failures, "a degraded index must not refetch every artifact per request");

    Date.now = () => originalNow() + 61_000;
    const again = await query(healthy, { q: noMatchTerm, limit: 1 });
    assert.ok(upstreamCalls > failures, "the rebuild must be retried after the backoff");
    assert.equal(again.body.source_gaps.find((row) => row.reason === "INDEX_REBUILD_FAILED").since, gap.since);

    const health = await healthy.fetch(new Request("https://govintel-query-gateway.example/health"), env);
    const healthBody = await health.json();
    assert.equal(health.status, 200);
    assert.equal(healthBody.status, "degraded");

    const cold = (await import("../../../workers/query-gateway/src/index.js?snapshot-cold-failure")).default;
    const unavailable = await query(cold, { limit: 1 });
    assert.equal(unavailable.status, 503);
    assert.equal(unavailable.body.error.code, "UPSTREAM_UNAVAILABLE");
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
  }
});

test("Worker fails closed instead of serving a superseded generation", async () => {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => [name, await readFile(new URL(name, base))]),
  ));
  const serve = table => async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    return new Response(table[name], { status: 200, headers: { "Content-Type": "application/json" } });
  };
  const query = async (instance, args) => {
    const response = await instance.fetch(new Request(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: "search_evidence", arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    const instance = (await import("../../../workers/query-gateway/src/index.js?integrity-fallback")).default;
    globalThis.fetch = withFixtureRelease(serve(bytes));
    const first = await query(instance, { limit: 1 });
    assert.equal(first.status, 200);

    // Artifacts that no longer describe one collection run are an integrity
    // failure, not an upstream outage: the previous generation must not answer.
    const brief = JSON.parse(bytes["v2-daily-brief.json"].toString("utf8"));
    brief.source_collection_run_id = `${brief.source_collection_run_id}-SUPERSEDED`;
    globalThis.fetch = withFixtureRelease(serve({ ...bytes, "v2-daily-brief.json": Buffer.from(JSON.stringify(brief), "utf8") }));
    Date.now = () => originalNow() + 31_000;
    const rejected = await query(instance, { limit: 1 });
    assert.equal(rejected.status, 503);
    assert.equal(rejected.body.error.code, "QUERY_TEMPORARILY_UNAVAILABLE");
    assert.equal(rejected.body.error.message, "release binding is unavailable");
    assert.equal(rejected.body.query_generation_id, undefined);
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
  }
});

test("Worker fails closed when a served publication body cannot be parsed", async () => {
  const originalFetch = globalThis.fetch;
  const originalNow = Date.now;
  const bytes = Object.fromEntries(await Promise.all(
    artifactNames.map(async name => [name, await readFile(new URL(name, base))]),
  ));
  const query = async (instance, args) => {
    const response = await instance.fetch(new Request(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: "search_evidence", arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    const instance = (await import("../../../workers/query-gateway/src/index.js?corrupt-body")).default;
    globalThis.fetch = withFixtureRelease(async url => {
      const name = new URL(url).pathname.split("/").at(-1);
      return new Response(bytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const first = await query(instance, { limit: 1 });
    assert.equal(first.status, 200);

    // A served-but-unparseable artifact is a broken publication, not an outage:
    // the previous generation must not answer for it.
    globalThis.fetch = withFixtureRelease(async url => {
      const name = new URL(url).pathname.split("/").at(-1);
      const content = name === "intelligence-feed.json" ? "not-json" : bytes[name];
      return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
    });
    Date.now = () => originalNow() + 31_000;
    const rejected = await query(instance, { limit: 1 });
    assert.equal(rejected.status, 503);
    assert.equal(rejected.body.error.code, "QUERY_TEMPORARILY_UNAVAILABLE");
    assert.equal(rejected.body.query_generation_id, undefined);
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
  }
});

test("Worker incomplete publication flags cannot admit current claims", async t => {
  const originalFetch = globalThis.fetch;
  try {
    for (const flag of ["collection-status", "publication-status", "snapshot-incomplete"]) {
      await t.test(flag, async () => {
        const bytes = await completePublicationFixture();
        const status = JSON.parse(bytes["source-status.json"].toString("utf8"));
        const brief = JSON.parse(bytes["v2-daily-brief.json"].toString("utf8"));
        if (flag === "collection-status") status.latest_collection_run.status = "PARTIAL";
        if (flag === "publication-status") brief.publication_status = "PARTIAL";
        if (flag === "snapshot-incomplete") brief.snapshot_complete = false;
        bytes["source-status.json"] = Buffer.from(JSON.stringify(status), "utf8");
        bytes["v2-daily-brief.json"] = Buffer.from(JSON.stringify(brief), "utf8");
        const item = JSON.parse(bytes["intelligence-feed.json"].toString("utf8")).items[0];
        globalThis.fetch = withFixtureRelease(async url => {
          const name = new URL(url).pathname.split("/").at(-1);
          return new Response(bytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
        });
        const instance = (await import(`../../../workers/query-gateway/src/index.js?incomplete-${flag}`)).default;
        const query = async (tool, args) => {
          const response = await instance.fetch(new Request(endpoint, {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ tool, arguments: args }),
          }), env);
          assert.equal(response.status, 200);
          return response.json();
        };
        const empty = await query("search_evidence", { q: "zzzz-govintel-no-match-20261001", limit: 1 });
        assert.equal(empty.freshness, "PARTIAL");
        assert.ok(empty.source_gaps.some(gap => gap.reason === "INCOMPLETE_PUBLICATION"));
        assert.equal(empty.answerable_no_match, false);
        const answer = await query("validate_answer", { claims: [{
          schema_version: 1, claim_id: "incomplete-current-title", text: item.title,
          claim_type: "STATUS", temporal_scope: "CURRENT",
          proposition: { subject: `publication:${item.stable_id}:title`, value: item.title },
          cited_evidence_ids: [`PUB-${item.stable_id}`],
        }] });
        assert.equal(answer.final_claims[0].support_status, "STALE");
      });
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Worker treats a partially collected source as incomplete scope", async () => {
  const originalFetch = globalThis.fetch;
  const fresh = await completePublicationFixture();
  const status = JSON.parse(fresh["source-status.json"].toString("utf8"));
  // Healthy-looking except that the run itself reported a partial result.
  status.sources[0].result = "PARTIAL";
  globalThis.fetch = withFixtureRelease(async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    const content = name === "source-status.json" ? Buffer.from(JSON.stringify(status), "utf8") : fresh[name];
    return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
  });
  try {
    const instance = (await import("../../../workers/query-gateway/src/index.js?partial-result")).default;
    const response = await instance.fetch(new Request(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: "search_evidence", arguments: { q: "zzzz-govintel-no-match-20261001", limit: 1 } }),
    }), env);
    const body = await response.json();
    assert.equal(response.status, 200);
    assert.ok(body.source_gaps.some((gap) => gap.reason === "SOURCE_INCOMPLETE"), JSON.stringify(body.source_gaps));
    assert.notEqual(body.freshness, "RECENT");
    assert.equal(body.answerable_no_match, false);
    assert.equal(body.query_coverage.can_state_bounded_no_match, false);
    assert.equal(body.query_coverage.status, "PARTIAL");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Worker inherits the source freshness for an item without its own", async () => {
  const originalFetch = globalThis.fetch;
  const fresh = await completePublicationFixture();
  const feed = JSON.parse(fresh["intelligence-feed.json"].toString("utf8"));
  const item = feed.items.find(row => row.stable_id);
  assert.ok(item, "fixture needs a feed item");
  delete item.freshness_status;
  const staleStatus = JSON.parse(fresh["source-status.json"].toString("utf8"));
  const serve = table => async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    return new Response(table[name], { status: 200, headers: { "Content-Type": "application/json" } });
  };
  const query = async (instance, args) => {
    const response = await instance.fetch(new Request(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: "search_evidence", arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    globalThis.fetch = withFixtureRelease(serve({ ...fresh, "intelligence-feed.json": Buffer.from(JSON.stringify(feed), "utf8") }));
    const inherited = (await query(
      (await import("../../../workers/query-gateway/src/index.js?fresh-source")).default,
      { canonical_id: item.stable_id, limit: 1 },
    )).body;
    assert.equal(inherited.results[0].verification_status, "VERIFIED");

    for (const source of staleStatus.sources) source.freshness_status = "STALE";
    globalThis.fetch = withFixtureRelease(serve({
      ...fresh,
      "intelligence-feed.json": Buffer.from(JSON.stringify(feed), "utf8"),
      "source-status.json": Buffer.from(JSON.stringify(staleStatus), "utf8"),
    }));
    const fromStaleSource = (await query(
      (await import("../../../workers/query-gateway/src/index.js?stale-source")).default,
      { canonical_id: item.stable_id, limit: 1 },
    )).body;
    assert.equal(fromStaleSource.results[0].verification_status, "STALE");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Worker never reports a stale publication item as current official evidence", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = await completePublicationFixture();
  const call = async (instance, tool, args) => {
    const response = await instance.fetch(new Request(endpoint, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool, arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    const feed = JSON.parse(bytes["intelligence-feed.json"].toString("utf8"));
    const item = feed.items.find(row => row.stable_id && row.official_url?.startsWith("https://"));
    assert.ok(item, "fixture needs an official evidence locator");
    feed.items = feed.items.map(row => (row.stable_id === item.stable_id ? { ...row, freshness_status: "STALE" } : row));
    globalThis.fetch = withFixtureRelease(async url => {
      const name = new URL(url).pathname.split("/").at(-1);
      const content = name === "intelligence-feed.json" ? Buffer.from(JSON.stringify(feed), "utf8") : bytes[name];
      return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const instance = (await import("../../../workers/query-gateway/src/index.js?stale-evidence")).default;
    const exact = await call(instance, "search_evidence", { canonical_id: item.stable_id, limit: 5 });
    assert.equal(exact.body.result_count, 1);
    assert.equal(exact.body.results[0].verification_status, "STALE");

    const gated = await call(instance, "validate_answer", {
      claims: [{
        schema_version: 1, claim_id: "CLM-STALE", text: item.title,
        claim_type: "STATUS", temporal_scope: "CURRENT",
        proposition: { subject: `publication:${item.stable_id}:title`, value: item.title },
        cited_evidence_ids: [`PUB-${item.stable_id}`],
      }],
    });
    assert.equal(gated.status, 200);
    assert.equal(gated.body.final_claims[0].support_status, "STALE");
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
