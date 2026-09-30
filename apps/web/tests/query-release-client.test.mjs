import assert from "node:assert/strict";
import test from "node:test";
import { queryGateway } from "../lib/query-release-client.js";

const context = { basePath: "/taichung-police-intel", codeSha: "a".repeat(40), publicationGeneration: "run-62" };
const release = { code_sha: context.codeSha, publication_generation: context.publicationGeneration, release_id: "b".repeat(64) };

test("production UI pins query to its displayed publication and checks the response release", async t => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push([url, options]);
    return Response.json(url.endsWith("release.json") ? release : { release, results: [] });
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
