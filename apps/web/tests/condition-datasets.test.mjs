import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { once } from "node:events";
import test from "node:test";
import { loadConditionDatasets } from "../lib/condition-datasets.js";
import { conditionDatasetStatus } from "../lib/local-conditions.js";

const names = ["v2-daily-brief", "intelligence-feed", "source-status", "public-query-replay"];
const bytes = Object.fromEntries(await Promise.all(names.map(async name => [name,
  await readFile(new URL(`../public/data/${name}.json`, import.meta.url), "utf8")])));

for (const phase of ["headers", "body"]) {
  test(`a source stalled during ${phase} does not block other tracking datasets`, { timeout: 3000 }, async () => {
    const server = createServer((request, response) => {
      const name = request.url.split("/").at(-1).replace(/\.json$/, "");
      if (name === "source-status") {
        if (phase === "body") { response.writeHead(200, { "Content-Type": "application/json" }); response.write('{"schema_version":1,'); }
        return; // Deliberately keep this source open until the request aborts.
      }
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(bytes[name]);
    });
    server.listen(0, "127.0.0.1");
    await once(server, "listening");
    try {
      const base = `http://127.0.0.1:${server.address().port}`;
      const started = Date.now();
      const loaded = await loadConditionDatasets("/candidate", {
        // Use a shorter deadline to exercise actual fetch abort, not a fake
        // immediately rejected promise, without adding 12 seconds to every CI.
        timeoutMs: 120,
        fetchImpl: (url, options) => fetch(`${base}${url}`, options),
      });
      assert.ok(Date.now() - started < 1500);
      assert.equal(loaded.datasets["source-status"], null);
      assert.deepEqual(loaded.datasets["intelligence-feed"], JSON.parse(bytes["intelligence-feed"]));
      assert.ok(loaded.datasets["public-query-replay"]?.bundle_sha256);
      assert.equal(loaded.replayError, null);
      const status = conditionDatasetStatus(loaded.datasets["v2-daily-brief"], loaded.datasets["intelligence-feed"], loaded.datasets["source-status"]);
      assert.equal(status.present, false);
      assert.equal(status.complete, false);
      assert.match(status.reason, /未完整取得/);
    } finally {
      server.closeAllConnections();
      await new Promise(resolve => server.close(resolve));
    }
  });
}
