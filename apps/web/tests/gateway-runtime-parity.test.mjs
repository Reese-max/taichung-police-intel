// Issue #29: Web (Cloudflare Worker) and MCP stdio (Python Gateway) answer the same typed
// primitives from the same publication. All inputs are the fictional governed fixture.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import { createGovernedPolicyFixture } from "./governed-policy-fixture.mjs";

const hash = value => createHash("sha256").update(value).digest("hex");
const PUBLICATION = ["source-policy.json", "intelligence-feed.json", "source-status.json", "v2-daily-brief.json"];
// Transport and clock fields that legitimately differ between two calls.
const VOLATILE = new Set(["query_id", "queried_at", "issued_at", "server_version", "release", "receipt"]);

function comparable(value) {
  if (Array.isArray(value)) return value.map(comparable);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).filter(key => !VOLATILE.has(key)).sort().map(key => [key, comparable(value[key])]));
  }
  return value;
}

async function bothRuntimes(run, { mutate = () => {}, ...options } = {}) {
  const fixture = await createGovernedPolicyFixture({ briefReviewed: true, ...options });
  const originalFetch = globalThis.fetch;
  let python;
  try {
    const dataDir = join(fixture.repoRoot, "apps/web/public/data");
    const values = Object.fromEntries(await Promise.all(PUBLICATION.map(async name => [name, JSON.parse(await readFile(join(dataDir, name), "utf8"))])));
    mutate(values);
    await Promise.all(PUBLICATION.map(name => writeFile(join(dataDir, name), JSON.stringify(values[name], null, 2) + "\n", "utf8")));
    const bytes = await fixture.publication();
    const module = await fixture.loadWorker("runtime-parity");
    const env = { PUBLIC_ORIGIN: "https://publication.example.test", CF_VERSION_METADATA: { id: "parity", tag: "a".repeat(40) } };
    const release = await module.createReleaseManifest(await module.buildSnapshot(env, async name => ({ bytes: bytes[name], hash: hash(bytes[name]) })), env.CF_VERSION_METADATA.tag);
    globalThis.fetch = async url => {
      const name = String(url).split("/").at(-1);
      assert.ok(name === "release.json" || bytes[name], `unexpected external fetch: ${url}`);
      return name === "release.json" ? Response.json(release) : new Response(bytes[name]);
    };
    let id = 0;
    const worker = async (name, args) => {
      const response = await module.default.fetch(new Request("https://gateway.example.test/mcp", { method: "POST",
        headers: { "Content-Type": "application/json", "CF-Connecting-IP": `parity-${++id}` },
        body: JSON.stringify({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } }) }), env);
      return (await response.json()).result;
    };
    python = spawn(process.env.PYTHON || "python", ["-X", "utf8", "-B", join(fixture.repoRoot, "scripts/query-gateway-stdio.py")],
      { cwd: fixture.repoRoot, stdio: ["pipe", "pipe", "pipe"] });
    const exited = new Promise(resolve => python.once("close", resolve));
    let buffer = ""; let stderr = ""; const waiting = [];
    python.stdout.setEncoding("utf8");
    python.stdout.on("data", chunk => {
      buffer += chunk;
      for (let at = buffer.indexOf("\n"); at >= 0; at = buffer.indexOf("\n")) {
        const line = buffer.slice(0, at); buffer = buffer.slice(at + 1);
        waiting.shift()?.(JSON.parse(line));
      }
    });
    python.stderr.on("data", chunk => { stderr += chunk; });
    const stdio = (name, args) => new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error(`python gateway did not answer ${name}: ${stderr}`)), 20_000);
      waiting.push(value => { clearTimeout(timer); resolve(value.result); });
      python.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: ++id, method: "tools/call", params: { name, arguments: args } }) + "\n");
    });
    const outcome = result => result.isError ? { error: JSON.parse(result.content[0].text).error.code } : result.structuredContent;
    const call = async (name, args) => ({ web: outcome(await worker(name, args)), mcp: outcome(await stdio(name, args)) });
    await run(call);
    python.stdin.end();
    await exited;
  } finally {
    // The Python process holds the fixture directory open on Windows until it exits.
    if (python && python.exitCode === null && python.signalCode === null) {
      const closed = new Promise(resolve => python.once("close", resolve));
      python.kill(); await closed;
    }
    globalThis.fetch = originalFetch;
    await fixture.cleanup();
  }
}

function assertParity({ web, mcp }, label) {
  assert.deepEqual(comparable(web), comparable(mcp), label);
}

test("the same filters return the same canonical IDs, order, status, gaps and coverage on Web and MCP", () => bothRuntimes(async call => {
  for (const args of [{ q: "測試" }, { q: "zzzz" }, { source_id: "S-004" }, { source_id: "S-006" }, {}, { q: "測試", limit: 1 }]) {
    const pair = await call("search_evidence", args);
    assertParity(pair, `search_evidence ${JSON.stringify(args)}`);
  }
  for (const args of [{}, { source_id: "S-006" }]) assertParity(await call("get_source_health", args), `get_source_health ${JSON.stringify(args)}`);
  assertParity(await call("get_publication_receipt", {}), "get_publication_receipt");
  assertParity(await call("get_current_brief", {}), "get_current_brief");
}));

test("each runtime's cursor leads to the same next page", () => bothRuntimes(async call => {
  const first = await call("search_evidence", { q: "測試", limit: 1 });
  assert.equal(first.web.has_more, true); assert.equal(first.mcp.has_more, true);
  const web = await call("search_evidence", { q: "測試", limit: 1, cursor: first.web.next_cursor });
  const mcp = await call("search_evidence", { q: "測試", limit: 1, cursor: first.mcp.next_cursor });
  assert.deepEqual(web.web.results.map(row => row.canonical_id), mcp.mcp.results.map(row => row.canonical_id));
  assert.notDeepEqual(web.web.results.map(row => row.canonical_id), first.web.results.map(row => row.canonical_id));
  // One cursor string for one page, accepted by either runtime.
  assert.equal(first.web.next_cursor, first.mcp.next_cursor);
  assertParity(web, "page two from the Web cursor");
}));

test("invalid requests fail with the same error code", () => bothRuntimes(async call => {
  for (const [name, args] of [["search_evidence", { q: "x".repeat(513) }], ["search_evidence", { unknown: 1 }], ["search_evidence", { limit: 0 }],
    ["get_source_health", { source_id: 7 }], ["search_events", {}], ["validate_answer", { claims: [] }]]) {
    const pair = await call(name, args);
    assert.ok(pair.web.error && pair.mcp.error, `${name} ${JSON.stringify(args)} should fail on both: ${JSON.stringify(pair)}`);
    assert.equal(pair.web.error, pair.mcp.error, `${name} ${JSON.stringify(args)}`);
  }
}));

test("a failed source and its gap read the same on Web and MCP", () => bothRuntimes(async call => {
  for (const args of [{}, { source_id: "S-006" }]) assertParity(await call("get_source_health", args), `get_source_health ${JSON.stringify(args)}`);
  for (const args of [{ q: "測試" }, { source_id: "S-006" }]) assertParity(await call("search_evidence", args), `search_evidence ${JSON.stringify(args)}`);
}, { mutate: values => {
  const row = values["source-status.json"].sources.find(source => source.source_id === "S-006");
  Object.assign(row, { source_health: "FAILED", window_completeness: "NOT_RUN" });
} }));
