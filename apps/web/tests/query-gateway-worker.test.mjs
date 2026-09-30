import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";
import worker from "../../../workers/query-gateway/src/index.js";

const base = new URL("../public/data/", import.meta.url);
const origin = "https://reese-max.github.io/taichung-police-intel";
const endpoint = "https://govintel-query-gateway.example/query";
const env = { PUBLIC_ORIGIN: origin, ALLOWED_ORIGINS: "https://reese-max.github.io" };

test("Python manifests match Worker evidence defaults and deterministic ID ordering", async (t) => {
  const originalFetch = globalThis.fetch;
  const python = process.platform === "win32" ? "python" : "python3";
  for (const variant of ["freshness-default", "id-order"]) {
    await t.test(variant, async () => {
      const directory = await mkdtemp(join(tmpdir(), "govintel-release-"));
      try {
        const bytes = Object.fromEntries(await Promise.all(
          ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"]
            .map(async name => [name, await readFile(new URL(name, base))]),
        ));
        const feed = JSON.parse(bytes["intelligence-feed.json"]);
        const item = feed.items.find(row => row.official_url?.startsWith("https://"));
        if (variant === "freshness-default") delete item.freshness_status;
        else feed.items = ["item-A", "item-a", "item-\u{10000}", "item-\uE000"].map(stable_id => ({ ...item, stable_id }));
        bytes["intelligence-feed.json"] = Buffer.from(JSON.stringify(feed));
        for (const [name, raw] of Object.entries(bytes)) await writeFile(join(directory, name), raw);
        execFileSync(python, ["-X", "utf8", fileURLToPath(new URL("../../../scripts/release-manifest.py", import.meta.url)),
          "build", "--data-dir", directory, "--output", join(directory, "release.json"), "--code-sha", "c".repeat(40)], { stdio: "pipe" });
        bytes["release.json"] = await readFile(join(directory, "release.json"));
        globalThis.fetch = async url => new Response(bytes[new URL(url).pathname.split("/").at(-1)], { status: 200 });
        const isolatedWorker = (await import(`../../../workers/query-gateway/src/index.js?${variant}`)).default;
        const response = await isolatedWorker.fetch(new Request("https://govintel-query-gateway.example/health"), env);
        assert.equal(response.status, 200, `${variant}: ${await response.text()}`);
      } finally {
        globalThis.fetch = originalFetch;
        await rm(directory, { recursive: true, force: true });
      }
    });
  }
});

test("Worker search applies q and preserves official evidence and publication binding", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json", "release.json"]
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

test("Worker refuses a release manifest with a mixed publication generation", async () => {
  const originalFetch = globalThis.fetch;
  const bytes = Object.fromEntries(await Promise.all(
    ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json", "release.json"]
      .map(async name => [name, await readFile(new URL(name, base))]),
  ));
  const release = JSON.parse(bytes["release.json"].toString("utf8"));
  release.publication_generation = "mixed-generation";
  bytes["release.json"] = Buffer.from(JSON.stringify(release));
  globalThis.fetch = async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    return new Response(bytes[name], { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const isolatedWorker = (await import("../../../workers/query-gateway/src/index.js?mixed-release" + Date.now())).default;
    const response = await isolatedWorker.fetch(new Request("https://govintel-query-gateway.example/health"), env);
    const payload = await response.json();
    assert.equal(response.status, 503);
    assert.equal(payload.error.code, "UPSTREAM_UNAVAILABLE");
    assert.match(payload.error.message, /release_id does not match/);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
