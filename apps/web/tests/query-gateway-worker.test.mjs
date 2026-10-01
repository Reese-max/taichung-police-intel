import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker from "../../../workers/query-gateway/src/index.js";

const base = new URL("../public/data/", import.meta.url);
const origin = "https://reese-max.github.io/taichung-police-intel";
const endpoint = "https://govintel-query-gateway.example/query";
const env = { PUBLIC_ORIGIN: origin, ALLOWED_ORIGINS: "https://reese-max.github.io" };

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
  await assert.rejects(
    () => validateAnswer(snapshot, [claim], () => ({
      gate_status: "BLOCKED",
      final_claims: [],
      removed_claims: [],
      receipt: {
        schema_version: 1, validator_version: "answer-evidence-gate/3", gate_status: "BLOCKED",
        failure_reason: "DUPLICATE_EVIDENCE_ID", publication_hash: null, claim_ids: [], evidence_ids: [], claims: [],
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
