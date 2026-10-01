import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker from "../../../workers/query-gateway/src/index.js";

const base = new URL("../public/data/", import.meta.url);
const origin = "https://reese-max.github.io/taichung-police-intel";
const endpoint = "https://govintel-query-gateway.example/query";
const env = { PUBLIC_ORIGIN: origin, ALLOWED_ORIGINS: "https://reese-max.github.io" };
const artifactNames = ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"];

// The checked-in publication is an archived snapshot, so freshness assertions
// need an equivalent snapshot whose own timestamps are current. Only the
// clock-derived fields move; collection run, policy binding and item rows are
// the canonical ones.
function refreshedPublicationArtifacts(bytes) {
  const stamp = new Date().toISOString();
  const read = name => JSON.parse(bytes[name].toString("utf8"));
  const encode = value => Buffer.from(JSON.stringify(value), "utf8");
  const feed = read("intelligence-feed.json");
  const status = read("source-status.json");
  const brief = read("v2-daily-brief.json");
  feed.generated_at = stamp;
  status.generated_at = stamp;
  status.latest_collection_run.finished_at = stamp;
  brief.generated_at = stamp;
  brief.source_status_generated_at = stamp;
  status.sources = status.sources.map(source => ({
    ...source,
    source_health: "PASS",
    window_completeness: "COMPLETE_WITH_ITEMS",
    freshness_status: "FRESH",
    last_checked_at: stamp,
    last_success_at: stamp,
  }));
  return {
    "intelligence-feed.json": encode(feed),
    "source-status.json": encode(status),
    "v2-daily-brief.json": encode(brief),
    "source-policy.json": bytes["source-policy.json"],
  };
}

test("Worker search applies q and preserves official evidence and publication binding", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => [name, await readFile(new URL(name, base))]),
  ));
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
      method: "POST", headers: { "Content-Type": "application/json", "Origin": "https://reese-max.github.io" },
      body: JSON.stringify({ tool, arguments: args }),
    }), env);
    return { status: response.status, body: await response.json() };
  };
  try {
    const feed = JSON.parse(bytes["intelligence-feed.json"].toString("utf8"));
    const item = feed.items.find(row => row.stable_id && row.official_url?.startsWith("https://"));
    assert.ok(item, "fixture needs an official evidence locator");
    const noMatchTerm = "zzzz-govintel-no-match-20260929";
    assert.ok(feed.items.every(row => !JSON.stringify(row).includes(noMatchTerm)));
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
  globalThis.fetch = async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    return new Response(bytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
  };
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
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => [name, await readFile(new URL(name, base))]),
  ));
  const freshBytes = refreshedPublicationArtifacts(bytes);
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
    globalThis.fetch = async url => {
      upstreamCalls += 1;
      const name = new URL(url).pathname.split("/").at(-1);
      return new Response(freshBytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
    };
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
    globalThis.fetch = serve(bytes);
    const first = await query(instance, { limit: 1 });
    assert.equal(first.status, 200);

    // Artifacts that no longer describe one collection run are an integrity
    // failure, not an upstream outage: the previous generation must not answer.
    const brief = JSON.parse(bytes["v2-daily-brief.json"].toString("utf8"));
    brief.source_collection_run_id = `${brief.source_collection_run_id}-SUPERSEDED`;
    globalThis.fetch = serve({ ...bytes, "v2-daily-brief.json": Buffer.from(JSON.stringify(brief), "utf8") });
    Date.now = () => originalNow() + 31_000;
    const rejected = await query(instance, { limit: 1 });
    assert.equal(rejected.status, 503);
    assert.equal(rejected.body.error.code, "UPSTREAM_UNAVAILABLE");
    assert.match(rejected.body.error.message, /cross-generation publication artifacts/);
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
    globalThis.fetch = async url => {
      const name = new URL(url).pathname.split("/").at(-1);
      return new Response(bytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
    };
    const first = await query(instance, { limit: 1 });
    assert.equal(first.status, 200);

    // A served-but-unparseable artifact is a broken publication, not an outage:
    // the previous generation must not answer for it.
    globalThis.fetch = async url => {
      const name = new URL(url).pathname.split("/").at(-1);
      const content = name === "intelligence-feed.json" ? "not-json" : bytes[name];
      return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
    };
    Date.now = () => originalNow() + 31_000;
    const rejected = await query(instance, { limit: 1 });
    assert.equal(rejected.status, 503);
    assert.equal(rejected.body.error.code, "UPSTREAM_UNAVAILABLE");
    assert.equal(rejected.body.query_generation_id, undefined);
  } finally {
    globalThis.fetch = originalFetch;
    Date.now = originalNow;
  }
});

test("Worker treats a partially collected source as incomplete scope", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = Object.fromEntries(await Promise.all(
    artifactNames.map(async name => [name, await readFile(new URL(name, base))]),
  ));
  const fresh = refreshedPublicationArtifacts(bytes);
  const status = JSON.parse(fresh["source-status.json"].toString("utf8"));
  // Healthy-looking except that the run itself reported a partial result.
  status.sources[0].result = "PARTIAL";
  globalThis.fetch = async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    const content = name === "source-status.json" ? Buffer.from(JSON.stringify(status), "utf8") : fresh[name];
    return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
  };
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
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("Worker inherits the source freshness for an item without its own", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = Object.fromEntries(await Promise.all(
    artifactNames.map(async name => [name, await readFile(new URL(name, base))]),
  ));
  const fresh = refreshedPublicationArtifacts(bytes);
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
    globalThis.fetch = serve({ ...fresh, "intelligence-feed.json": Buffer.from(JSON.stringify(feed), "utf8") });
    const inherited = (await query(
      (await import("../../../workers/query-gateway/src/index.js?fresh-source")).default,
      { canonical_id: item.stable_id, limit: 1 },
    )).body;
    assert.equal(inherited.results[0].verification_status, "VERIFIED");

    for (const source of staleStatus.sources) source.freshness_status = "STALE";
    globalThis.fetch = serve({
      ...fresh,
      "intelligence-feed.json": Buffer.from(JSON.stringify(feed), "utf8"),
      "source-status.json": Buffer.from(JSON.stringify(staleStatus), "utf8"),
    });
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
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => [name, await readFile(new URL(name, base))]),
  ));
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
    globalThis.fetch = async url => {
      const name = new URL(url).pathname.split("/").at(-1);
      const content = name === "intelligence-feed.json" ? Buffer.from(JSON.stringify(feed), "utf8") : bytes[name];
      return new Response(content, { status: 200, headers: { "Content-Type": "application/json" } });
    };
    const instance = (await import("../../../workers/query-gateway/src/index.js?stale-evidence")).default;
    const exact = await call(instance, "search_evidence", { canonical_id: item.stable_id, limit: 5 });
    assert.equal(exact.body.result_count, 1);
    assert.equal(exact.body.results[0].verification_status, "STALE");

    const gated = await call(instance, "validate_answer", {
      claims: [{
        claim_id: "CLM-STALE", claim_type: "STATUS", temporal_scope: "CURRENT",
        proposition: { subject: `publication:${item.stable_id}:title`, value: item.title },
        cited_evidence_ids: [`PUB-${item.stable_id}`],
      }],
    });
    assert.equal(gated.status, 200);
    assert.notEqual(gated.body.final_claims[0].support_status, "SUPPORTED");
  } finally {
    globalThis.fetch = originalFetch;
  }
});
