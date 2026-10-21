import assert from "node:assert/strict";
import test from "node:test";
import { queryGateway } from "../lib/query-release-client.js";

const context = { basePath: "/taichung-police-intel", codeSha: "a".repeat(40), publicationGeneration: "run-62" };
const release = {
  schema_version: 1, code_sha: context.codeSha, publication_generation: context.publicationGeneration, release_id: "b".repeat(64),
  publication_hash: "c".repeat(64), artifact_hashes: { feed: "d".repeat(64), status: "e".repeat(64), brief: "c".repeat(64) },
  source_policy_hash: "f".repeat(64), query_generation: "1".repeat(64), evidence_catalog_hash: "2".repeat(64), built_at: "2026-10-01T00:00:00Z",
};
const query = {
  release, results: [], publication_id: release.publication_generation, publication_hash: release.publication_hash,
  query_generation_id: release.query_generation, policy: { policy_hash: release.source_policy_hash },
  receipt: { publication_hash: release.publication_hash, query_generation_id: release.query_generation },
};

test("production UI pins query to its displayed publication and checks the response release", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push([url, options]);
    return Response.json(url.endsWith("release.json") ? release : query);
  });
  const result = await queryGateway("https://gateway.example", "search_evidence", { q: "test" }, context);
  assert.equal(result.release.release_id, release.release_id);
  assert.equal(calls[0][0], "/taichung-police-intel/data/release.json");
  assert.equal(calls[0][1].cache, "no-store");
  assert.equal(calls[1][0], "https://gateway.example/query");
  assert.equal(JSON.parse(calls[1][1].body).release_id, release.release_id);
});

test("old UI code or publication cannot send a query against a newer release", async t => {
  for (const field of ["code_sha", "publication_generation", "release_id"]) {
    const calls = [];
    t.mock.method(globalThis, "fetch", async url => {
      calls.push(url);
      return Response.json({ ...release, [field]: field === "release_id" ? null : "different" });
    });
    await assert.rejects(queryGateway("https://gateway.example/query", "get_current_brief", {}, context), /版本/);
    assert.equal(calls.length, 1);
    t.mock.restoreAll();
  }
});

test("a release change during the request never renders the query response", async t => {
  t.mock.method(globalThis, "fetch", async url => Response.json(url.endsWith("release.json")
    ? release : { release: { ...release, release_id: "different" }, results: [{ title: "mixed release" }] }));
  await assert.rejects(queryGateway("https://gateway.example/query", "search_evidence", {}, context), /版本/);
});

test("same release labels cannot hide missing or changed data and policy bindings", async () => {
  const mutations = Object.keys(release).flatMap(field => [
    value => { delete value[field]; },
    value => { value[field] = field === "schema_version" ? 2 : "different"; },
  ]);
  for (const key of ["feed", "status", "brief"]) {
    mutations.push(value => { value.artifact_hashes[key] = "0".repeat(64); });
  }
  for (const mutate of mutations) {
    const changed = structuredClone(release); mutate(changed);
    const fetchImpl = async url => Response.json(url.endsWith("release.json") ? release : { ...query, release: changed });
    await assert.rejects(queryGateway("https://gateway.example/query", "search_evidence", {}, { ...context, fetchImpl }),
      error => error.code === "PENDING_UPDATE");
  }
});

test("a matching release cannot relabel old query content, policy or receipt", async () => {
  for (const path of [["publication_id"], ["publication_hash"], ["query_generation_id"], ["policy", "policy_hash"],
    ["receipt", "publication_hash"], ["receipt", "query_generation_id"], ["receipt"], ["policy"]]) {
    for (const value of ["old-generation", undefined]) {
      const changed = structuredClone(query);
      const parent = path.length === 1 ? changed : changed[path[0]];
      if (value === undefined) delete parent[path.at(-1)];
      else parent[path.at(-1)] = value;
      const fetchImpl = async url => Response.json(url.endsWith("release.json") ? release : changed);
      await assert.rejects(queryGateway("https://gateway.example/query", "search_evidence", {}, { ...context, fetchImpl }),
        error => error.code === "PENDING_UPDATE", path.join("."));
    }
  }
});

test("an incomplete release manifest blocks before the query is posted", async () => {
  for (const field of Object.keys(release)) {
    let calls = 0;
    const changed = { ...release }; delete changed[field];
    const fetchImpl = async () => { calls++; return Response.json(changed); };
    await assert.rejects(queryGateway("https://gateway.example/query", "search_evidence", {}, { ...context, fetchImpl }),
      error => error.code === "PENDING_UPDATE", field);
    assert.equal(calls, 1);
  }
});

test("equivalent artifact key order and Worker deployment metadata preserve the binding", async () => {
  const bound = { ...release, artifact_hashes: { brief: release.publication_hash, status: release.artifact_hashes.status, feed: release.artifact_hashes.feed }, worker_version: "worker-161" };
  const fetchImpl = async url => Response.json(url.endsWith("release.json") ? release : { ...query, release: bound });
  assert.deepEqual(await queryGateway("https://gateway.example/query", "search_evidence", {}, { ...context, fetchImpl }), { ...query, release: bound });
});

test("local gateway remains usable without a production release context", async t => {
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/query");
    assert.deepEqual(JSON.parse(options.body), { tool: "get_current_brief", arguments: {} });
    return Response.json({ brief: {} });
  });
  assert.deepEqual(await queryGateway("/query", "get_current_brief", {}), { brief: {} });
});

test("explicit loopback Python gateway preserves the documented local development path", async t => {
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "http://127.0.0.1:8788/query");
    assert.deepEqual(JSON.parse(options.body), { tool: "get_current_brief", arguments: {} });
    return Response.json({ brief: {} });
  });
  assert.deepEqual(await queryGateway("http://127.0.0.1:8788/query", "get_current_brief", {}, {
    basePath: "", codeSha: undefined, publicationGeneration: "local",
  }), { brief: {} });
});

test("protocol-relative remote gateway also requires a release context", async t => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("unbound request sent"); });
  await assert.rejects(queryGateway("//gateway.example/query", "get_current_brief", {}), /缺少發布版本/);
});

test("both release admission and query requests are bounded and can use the injected client", async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push([url, options]);
    assert.ok(options.signal instanceof AbortSignal);
    return Response.json(url.endsWith("release.json") ? release : query);
  };
  await queryGateway("https://gateway.example/query/", "search_evidence", {}, { ...context, fetchImpl });
  assert.equal(calls.length, 2);
  assert.equal(calls[1][0], "https://gateway.example/query");
});

test("gateway capability and unavailable errors preserve their typed status", async t => {
  for (const code of ["CAPABILITY_NOT_AVAILABLE", "QUERY_TEMPORARILY_UNAVAILABLE", "GATE_FAILED"]) {
    t.mock.method(globalThis, "fetch", async () => Response.json({ error: { code, message: "typed refusal" } }, { status: 503 }));
    await assert.rejects(queryGateway("/query", "search_events", {}), error => error.code === code);
    t.mock.restoreAll();
  }
});

test("release mismatches expose pending update and cannot send a query", async () => {
  let calls = 0;
  await assert.rejects(queryGateway("https://gateway.example", "search_evidence", {}, {
    ...context, fetchImpl: async () => { calls += 1; return Response.json({ ...release, code_sha: "different" }); },
  }), error => error.code === "PENDING_UPDATE");
  assert.equal(calls, 1);
});

test("missing publication context blocks production requests before network work", async t => {
  t.mock.method(globalThis, "fetch", () => { throw new Error("unbound network request"); });
  for (const missing of [null, { ...context, codeSha: null }, { ...context, publicationGeneration: null }]) {
    await assert.rejects(queryGateway("https://gateway.example", "search_evidence", {}, missing), error => error.code === "CAPABILITY_NOT_AVAILABLE");
  }
});

test("a timed out fetch becomes a typed timeout, while malformed responses fail closed", async t => {
  t.mock.method(globalThis, "fetch", async () => { throw new DOMException("deadline", "TimeoutError"); });
  await assert.rejects(queryGateway("/query", "search_evidence", {}), error => error.code === "TIMEOUT");
  t.mock.restoreAll();
  t.mock.method(globalThis, "fetch", async () => Response.json(0));
  await assert.rejects(queryGateway("/query", "search_evidence", {}), error => error.code === "INVALID_RESPONSE");
});
