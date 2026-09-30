import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import * as gateway from "../../../workers/query-gateway/src/index.js";

const data = new URL("../public/data/", import.meta.url);
const origin = "https://reese-max.github.io/taichung-police-intel";
const codeSha = "a".repeat(40);
const env = { PUBLIC_ORIGIN: origin, CF_VERSION_METADATA: { id: "worker-version-62", tag: codeSha } };
const builtAt = "2026-09-30T00:00:00Z";
let scenario = 0;

async function fixture(transform = () => {}) {
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
      .map(async name => [name, await readFile(new URL(name, data))]),
  ));
  transform(bytes);
  const snapshot = await gateway.buildSnapshot(env, async name => ({
    value: JSON.parse(bytes[name]), hash: createHash("sha256").update(bytes[name]).digest("hex"),
  }));
  const release = await gateway.createReleaseManifest(snapshot, codeSha, builtAt);
  bytes["release.json"] = JSON.stringify(release);
  return { bytes, snapshot, release };
}

async function withWorker(t, bytes) {
  t.mock.method(globalThis, "fetch", async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    return new Response(bytes[name] || "missing", { status: bytes[name] ? 200 : 404 });
  });
  const { default: worker } = await import(`../../../workers/query-gateway/src/index.js?release-test=${scenario++}`);
  return async (path, body, runtime = env) => {
    const response = await worker.fetch(new Request(`https://gateway.example${path}`, body ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : {}), runtime);
    return { status: response.status, body: await response.json() };
  };
}

test("release builder reuses the canonical snapshot and never claims deployment", async () => {
  const { snapshot, release } = await fixture();
  assert.equal(release.code_sha, codeSha);
  assert.equal(release.publication_generation, snapshot.generatedFrom.collection_run_id);
  assert.equal(release.publication_hash, snapshot.generatedFrom.brief_sha256);
  assert.equal(release.source_policy_hash, snapshot.policyBinding.policy_hash);
  assert.equal(release.query_generation, snapshot.generationId);
  assert.match(release.evidence_catalog_hash, /^[a-f0-9]{64}$/);
  assert.equal(release.built_at, builtAt);
  assert.equal(release.production_verified, false);
  assert.equal(release.evidence_level, "BUILD_ONLY");
  for (const field of ["worker_version", "pages_deployment", "deployed_at", "anonymous_http_verified_at"]) {
    assert.equal(release[field], null, field);
  }
  assert.equal((await gateway.createReleaseManifest(snapshot, codeSha, builtAt)).release_id, release.release_id);
  await assert.rejects(gateway.createReleaseManifest(snapshot, "main", builtAt), /code SHA/i);
  await assert.rejects(gateway.createReleaseManifest(snapshot, codeSha, "yesterday"), /built_at/);
});

test("release CLI produces a manifest without modifying canonical input", async () => {
  const directory = await mkdtemp(join(tmpdir(), "govintel-release-"));
  try {
    const output = join(directory, "release.json");
    execFileSync(process.execPath, [fileURLToPath(new URL("../../../scripts/build-release-manifest.mjs", import.meta.url)),
      "--data-dir", fileURLToPath(data), "--output", output, "--code-sha", codeSha], { encoding: "utf8" });
    const result = JSON.parse(await readFile(output, "utf8"));
    assert.equal(result.release_id, (await fixture()).release.release_id);
    assert.equal(result.production_verified, false);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("health, capabilities, query and MCP expose the same release and official evidence", async t => {
  const { bytes, release, snapshot } = await fixture();
  const request = await withWorker(t, bytes);
  const health = await request("/health");
  const capabilities = await request("/capabilities");
  const query = await request("/query", { tool: "search_evidence", arguments: { q: "", limit: 1 }, release_id: release.release_id });
  const mcp = await request("/mcp", { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "get_publication_receipt", arguments: {} } });
  for (const response of [health, capabilities, query, mcp]) assert.equal(response.status, 200);
  for (const payload of [health.body, capabilities.body, query.body, mcp.body.result.structuredContent]) {
    assert.equal(payload.release.release_id, release.release_id);
    assert.equal(payload.release.worker_version, env.CF_VERSION_METADATA.id);
    assert.equal(payload.release.production_verified, false);
  }
  const row = query.body.results[0];
  assert.equal(row.official_url, snapshot.items.find(item => item.canonical_id === row.canonical_id).official_url);
  assert.match(row.official_url, /^https:\/\/[^/]+\.gov\.tw\//);
  const mismatch = await request("/query", { tool: "get_current_brief", arguments: {}, release_id: "old-pages-release" });
  assert.equal(mismatch.status, 503);
  assert.equal(mismatch.body.error.code, "QUERY_TEMPORARILY_UNAVAILABLE");
});

test("missing and mismatched release bindings fail closed on every entry point", async t => {
  const { bytes, release } = await fixture();
  for (const field of ["missing", "release_id", "code_sha", "publication_generation", "publication_hash",
    "source_policy_hash", "query_generation", "evidence_catalog_hash", "artifact_hashes", "built_at"]) {
    await t.test(field, async child => {
      const changed = { ...bytes };
      if (field === "missing") delete changed["release.json"];
      else changed["release.json"] = JSON.stringify({ ...release, [field]: "tampered" });
      const request = await withWorker(child, changed);
      for (const [path, body] of [["/health"], ["/capabilities"], ["/query", { tool: "get_current_brief", arguments: {} }],
        ["/mcp", { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "search_evidence", arguments: {} } }]]) {
        const response = await request(path, body);
        assert.equal(response.status, 503, `${field} ${path}`);
        assert.equal(response.body.error.code, "QUERY_TEMPORARILY_UNAVAILABLE");
        assert.equal(response.body.results, undefined);
      }
      // The gateway never writes to the static publication when a binding fails.
      assert.equal(changed["v2-daily-brief.json"], bytes["v2-daily-brief.json"]);
    });
  }
});

test("a cached release cannot be reused by a different Worker code version", async t => {
  const { bytes } = await fixture();
  const request = await withWorker(t, bytes);
  assert.equal((await request("/health")).status, 200);
  const changed = { ...env, CF_VERSION_METADATA: { id: "other-version", tag: "b".repeat(40) } };
  assert.equal((await request("/health", undefined, changed)).status, 503);
});

test("wall-clock freshness changes do not change the immutable evidence binding", async t => {
  const { bytes, release } = await fixture();
  t.mock.method(Date, "now", () => Date.parse("2030-01-01T00:00:00Z"));
  const request = await withWorker(t, bytes);
  const response = await request("/query", { tool: "search_evidence", arguments: {} });
  assert.equal(response.status, 200);
  assert.equal(response.body.release.evidence_catalog_hash, release.evidence_catalog_hash);
  assert.equal(response.body.answerable_no_match, false);
  assert.notEqual(response.body.freshness, "RECENT");
});

test("answer receipts bind the actual gate catalog as a release becomes stale", async t => {
  let now = Date.parse(builtAt);
  t.mock.method(Date, "now", () => now);
  const { bytes, release, snapshot } = await fixture(bytes => {
    for (const name of ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json"]) {
      const doc = JSON.parse(bytes[name]);
      doc.generated_at = builtAt;
      if (doc.items) for (const row of doc.items) row.freshness_status = "FRESH";
      if (doc.sources) for (const row of doc.sources) Object.assign(row, {
        source_health: "PASS", window_completeness: "COMPLETE_WITH_ITEMS", freshness_status: "FRESH", last_checked_at: builtAt,
      });
      if (doc.latest_collection_run) doc.latest_collection_run.status = "SUCCEEDED";
      if (doc.source_collection_run_id) Object.assign(doc, {
        source_status_generated_at: builtAt, publication_status: "READY", snapshot_complete: true,
      });
      bytes[name] = JSON.stringify(doc);
    }
  });
  const item = snapshot.items.find(item => item.official_url);
  const request = await withWorker(t, bytes);
  const body = { tool: "validate_answer", arguments: { claims: [{
    claim_type: "STATUS", temporal_scope: "CURRENT", text: "official title",
    proposition: { subject: `publication:${item.canonical_id}:title`, value: item.title },
  }] } };
  const fresh = await request("/query", body);
  assert.equal(fresh.status, 200);
  assert.equal(fresh.body.gate_status, "PASS");
  assert.equal(fresh.body.answer_evidence_receipt.evidence_catalog_hash, release.evidence_catalog_hash);
  now += 17 * 60 * 60 * 1000;
  const stale = await request("/query", body);
  assert.equal(stale.status, 200);
  assert.equal(stale.body.final_claims[0].support_status, "STALE");
  assert.equal(stale.body.release.release_id, release.release_id);
  assert.notEqual(stale.body.answer_evidence_receipt.evidence_catalog_hash, fresh.body.answer_evidence_receipt.evidence_catalog_hash);
});
