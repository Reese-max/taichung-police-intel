import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test, { after } from "node:test";
import { createGovernedPolicyFixture } from "./governed-policy-fixture.mjs";

const governed = await createGovernedPolicyFixture({ rightsReviewed: true });
after(() => governed.cleanup());
const gateway = await governed.loadWorker("release-builder");
const origin = "https://reese-max.github.io/taichung-police-intel";
const codeSha = "a".repeat(40);
const env = { PUBLIC_ORIGIN: origin, CF_VERSION_METADATA: { id: "worker-version-62", tag: codeSha } };
const builtAt = "2026-09-30T00:00:00Z";

async function fixture(transform = () => {}) {
  const bytes = await governed.publication();
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
  const { default: worker } = await governed.loadWorker("release-test");
  return async (path, body, runtime = env) => {
    const response = await worker.fetch(new Request(`https://gateway.example${path}`, body ? {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    } : {}), runtime);
    return { status: response.status, body: await response.json() };
  };
}

async function cliFixture() {
  const directory = await mkdtemp(join(tmpdir(), "govintel-release-"));
  try {
    const dataDirectory = join(directory, "data");
    const output = join(directory, "release.json");
    const bytes = await governed.publication();
    await mkdir(dataDirectory);
    await Promise.all(Object.entries(bytes).map(([name, value]) => writeFile(join(dataDirectory, name), value)));
    const script = join(governed.repoRoot, "scripts/build-release-manifest.mjs");
    // Resolve the unchanged CLI against this fixture's fictional approved policy.
    await copyFile(fileURLToPath(new URL("../../../scripts/build-release-manifest.mjs", import.meta.url)), script);
    return {
      bytes, dataDirectory,
      async build() {
        execFileSync(process.execPath, [script,
          "--data-dir", dataDirectory, "--output", output, "--code-sha", codeSha], { encoding: "utf8" });
        return JSON.parse(await readFile(output, "utf8"));
      },
      cleanup: () => rm(directory, { recursive: true, force: true }),
    };
  } catch (error) {
    await rm(directory, { recursive: true, force: true });
    throw error;
  }
}

test("release builder reuses the canonical snapshot and never claims deployment", async () => {
  const { snapshot, release } = await fixture();
  assert.equal(snapshot.policy.schema_version, 2);
  assert.equal(snapshot.items.length, 2);
  assert.equal(snapshot.formalAdmission.status, "ADMITTED");
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
  const cli = await cliFixture();
  try {
    const before = Object.fromEntries(await Promise.all(Object.keys(cli.bytes)
      .map(async name => [name, await readFile(join(cli.dataDirectory, name))])));
    const result = await cli.build();
    assert.equal(result.release_id, (await fixture()).release.release_id);
    assert.equal(result.production_verified, false);
    assert.equal(result.evidence_level, "BUILD_ONLY");
    for (const [name, bytes] of Object.entries(before)) {
      assert.deepEqual(await readFile(join(cli.dataDirectory, name)), bytes, `${name} input bytes changed`);
    }
  } finally {
    await cli.cleanup();
  }
});

test("canonical artifact bytes survive Git checkout with either autocrlf setting", async () => {
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  const directory = await mkdtemp(join(tmpdir(), "govintel-release-checkout-"));
  try {
    const repository = join(directory, "repository");
    const emptyAttributes = join(directory, "empty-attributes");
    await mkdir(repository);
    await writeFile(emptyAttributes, "");
    const git = args => execFileSync("git", ["-c", `core.attributesFile=${emptyAttributes}`, ...args], {
      cwd: repository, env: { ...process.env, GIT_ATTR_NOSYSTEM: "1" },
    });
    git(["init", "--quiet"]);
    // Exercise the actual repository rules without external attributes or an old release fixture.
    await copyFile(join(root, ".gitattributes"), join(repository, ".gitattributes"));
    const publication = Object.fromEntries(Object.entries(await governed.publication())
      .map(([name, bytes]) => [name, Buffer.from(bytes.toString("utf8").replaceAll("\r\n", "\n"))]));
    const paths = Object.keys(publication).map(name => `apps/web/public/data/${name}`);
    await mkdir(join(repository, "apps/web/public/data"), { recursive: true });
    await Promise.all(Object.entries(publication)
      .map(([name, bytes]) => writeFile(join(repository, "apps/web/public/data", name), bytes)));
    git(["-c", "core.autocrlf=false", "add", "--", ".gitattributes", ...paths]);
    const expected = new Map(paths.map(path => [path, git(["show", `:${path}`])]));
    for (const [name, bytes] of Object.entries(publication)) {
      assert.deepEqual(expected.get(`apps/web/public/data/${name}`), bytes, `${name} index bytes changed`);
    }
    for (const autocrlf of ["true", "false"]) {
      const checkout = join(directory, autocrlf);
      await mkdir(checkout);
      git(["-c", `core.autocrlf=${autocrlf}`, "checkout-index",
        `--prefix=${checkout.replaceAll("\\", "/")}/`, "--", ...paths]);
      for (const path of paths) {
        const bytes = await readFile(join(checkout, path));
        assert.deepEqual(bytes, expected.get(path), `${path} bytes differ with core.autocrlf=${autocrlf}`);
        assert.equal(bytes.includes(Buffer.from("\r\n")), false, `${path} must retain LF bytes`);
      }
    }
    // Without the rules, this same Git checkout must exercise CRLF conversion.
    await writeFile(join(repository, ".gitattributes"), "");
    git(["-c", "core.autocrlf=false", "add", "--", ".gitattributes"]);
    const negative = join(directory, "without-attributes");
    await mkdir(negative);
    git(["-c", "core.autocrlf=true", "checkout-index",
      `--prefix=${negative.replaceAll("\\", "/")}/`, "--", paths[0]]);
    assert.equal((await readFile(join(negative, paths[0]))).includes(Buffer.from("\r\n")), true,
      "the negative control must convert LF bytes when the attributes are absent");
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("release CLI and Worker bind raw bytes even when publication JSON is unchanged", async t => {
  for (const [name, field] of [["intelligence-feed.json", "feed"], ["source-status.json", "status"],
    ["v2-daily-brief.json", "brief"]]) {
    await t.test(name, async child => {
      const cli = await cliFixture();
      try {
        const original = await cli.build();
        const baseline = await withWorker(child, { ...cli.bytes, "release.json": JSON.stringify(original) });
        assert.equal((await baseline("/health")).status, 200);
        const changedBytes = Buffer.concat([cli.bytes[name], Buffer.from("\n")]);
        assert.deepEqual(JSON.parse(changedBytes), JSON.parse(cli.bytes[name]), "only raw whitespace changes");
        await writeFile(join(cli.dataDirectory, name), changedBytes);
        const changed = { ...cli.bytes, [name]: changedBytes, "release.json": JSON.stringify(original) };
        const rejected = await withWorker(child, changed);
        const rejectedResponse = await rejected("/query", { tool: "search_evidence", arguments: {} });
        assert.equal(rejectedResponse.status, 503);
        assert.equal(rejectedResponse.body.error.code, "QUERY_TEMPORARILY_UNAVAILABLE");
        assert.equal(rejectedResponse.body.results, undefined);
        const rebuilt = await cli.build();
        assert.equal(rebuilt.artifact_hashes[field], createHash("sha256").update(changedBytes).digest("hex"));
        assert.notEqual(rebuilt.artifact_hashes[field], original.artifact_hashes[field]);
        assert.notEqual(rebuilt.release_id, original.release_id);
        assert.equal(rebuilt.production_verified, false);
        assert.equal(rebuilt.evidence_level, "BUILD_ONLY");
        const accepted = await withWorker(child, { ...changed, "release.json": JSON.stringify(rebuilt) });
        const acceptedResponse = await accepted("/query", { tool: "search_evidence", arguments: {} });
        assert.equal(acceptedResponse.status, 200);
        assert.equal(acceptedResponse.body.release.release_id, rebuilt.release_id);
      } finally {
        await cli.cleanup();
      }
    });
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

test("a release outage cannot mask a concurrent publication integrity failure", async t => {
  let now = Date.parse(builtAt);
  t.mock.method(Date, "now", () => now);
  const { bytes } = await fixture();
  const request = await withWorker(t, bytes);
  assert.equal((await request("/health")).status, 200);
  now += 31_000;
  t.mock.method(globalThis, "fetch", async url => {
    const name = new URL(url).pathname.split("/").at(-1);
    if (name === "release.json") return new Response("unavailable", { status: 503 });
    if (name === "intelligence-feed.json") return new Response("corrupt body");
    return new Response(bytes[name]);
  });
  const rejected = await request("/query", { tool: "search_evidence", arguments: {} });
  assert.equal(rejected.status, 503);
  assert.equal(rejected.body.error.code, "QUERY_TEMPORARILY_UNAVAILABLE");
  assert.equal(rejected.body.results, undefined);
  t.mock.method(globalThis, "fetch", async () => new Response("unavailable", { status: 503 }));
  const stillRejected = await request("/query", { tool: "search_evidence", arguments: {} });
  assert.equal(stillRejected.status, 503, "an outage must not revive a generation invalidated by integrity failure");
  assert.equal(stillRejected.body.results, undefined);
});

test("an expired cached publication cannot degrade across Worker deployments", async t => {
  let now = Date.parse(builtAt);
  t.mock.method(Date, "now", () => now);
  const { bytes } = await fixture();
  const request = await withWorker(t, bytes);
  assert.equal((await request("/health")).status, 200);
  now += 31_000;
  t.mock.method(globalThis, "fetch", async () => new Response("unavailable", { status: 503 }));
  const otherDeployment = { ...env, CF_VERSION_METADATA: { id: "different-worker", tag: "b".repeat(40) } };
  const rejected = await request("/health", undefined, otherDeployment);
  assert.equal(rejected.status, 503);
  assert.equal(rejected.body.release, undefined);
});
