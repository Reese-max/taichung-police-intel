import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { once } from "node:events";
import test from "node:test";
import { setTimeout as delay } from "node:timers/promises";
import { loadConditionDatasets } from "../lib/condition-datasets.js";
import { conditionDatasetStatus } from "../lib/local-conditions.js";

const names = ["v2-daily-brief", "intelligence-feed", "source-status", "public-query-replay"];
const bytes = Object.fromEntries(await Promise.all(names.map(async name => [name,
  await readFile(new URL(`../public/data/${name}.json`, import.meta.url), "utf8")])));

function assertPartialDatasets(loaded) {
  assert.equal(loaded.datasets["source-status"], null);
  for (const name of names.filter(name => name !== "source-status")) {
    assert.deepEqual(loaded.datasets[name], JSON.parse(bytes[name]));
  }
  assert.ok(loaded.datasets["public-query-replay"]?.bundle_sha256);
  assert.equal(loaded.replayError, null);
  const status = conditionDatasetStatus(loaded.datasets["v2-daily-brief"], loaded.datasets["intelligence-feed"], loaded.datasets["source-status"]);
  assert.equal(status.present, false);
  assert.equal(status.complete, false);
  assert.match(status.reason, /未完整取得/);
}

for (const phase of ["headers", "body"]) {
  for (const completion of ["before", "after"]) {
    test(`a source stalled during ${phase} preserves datasets completing ${completion} its deadline`, { timeout: 3000 }, async t => {
      const stalled = Promise.withResolvers();
      const healthyRead = Promise.withResolvers();
      const releaseHealthy = Promise.withResolvers();
      const disconnected = Promise.withResolvers();
      const controllers = [];
      const requests = new Map();
      const completed = new Set();
      // Advance only the stalled request's deadline after observing its phase.
      // Real 120 ms timers also expired healthy requests on busy/cold CI runners.
      // Keep real fetch/body consumption, default duration and independent signals.
      t.mock.method(AbortSignal, "timeout", ms => {
        assert.equal(ms, 12000);
        const controller = new AbortController();
        controllers.push(controller);
        return controller.signal;
      });
      const server = createServer((request, response) => {
        const name = request.url.split("/").at(-1).replace(/\.json$/, "");
        if (name === "source-status") {
          response.once("close", disconnected.resolve);
          if (phase === "body") {
            response.writeHead(200, { "Content-Type": "application/json" });
            response.write('{"schema_version":1,');
          } else stalled.resolve();
          return; // Keep this source open until the request aborts.
        }
        releaseHealthy.promise.then(() => {
          response.writeHead(200, { "Content-Type": "application/json" });
          response.end(bytes[name]);
        });
      });
      t.after(async () => {
        releaseHealthy.resolve();
        controllers.forEach(controller => controller.abort());
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
      });
      server.listen(0, "127.0.0.1");
      await once(server, "listening");
      const base = `http://127.0.0.1:${server.address().port}`;
      const started = Date.now();
      const loading = loadConditionDatasets("/candidate", {
        fetchImpl: async (url, options) => {
          const name = url.split("/").at(-1).replace(/\.json$/, "");
          assert.equal(options.cache, "no-store");
          requests.set(name, options.signal);
          const response = await fetch(`${base}${url}`, options);
          return {
            ok: response.ok, status: response.status,
            json: async () => {
              if (name === "source-status") stalled.resolve();
              const data = await response.json();
              completed.add(name);
              if (completed.size === 3) healthyRead.resolve();
              return data;
            },
          };
        },
      });
      await stalled.promise;
      assert.deepEqual([...requests.keys()], names);
      assert.equal(new Set(requests.values()).size, 4);
      if (completion === "before") {
        releaseHealthy.resolve();
        await healthyRead.promise;
      }
      const sourceSignal = requests.get("source-status");
      const sourceController = controllers.find(controller => controller.signal === sourceSignal);
      assert.ok(sourceController);
      sourceController.abort(new DOMException("Source deadline elapsed", "TimeoutError"));
      await disconnected.promise;
      if (completion === "after") releaseHealthy.resolve();
      assertPartialDatasets(await loading);
      assert.ok(Date.now() - started < 1500);
      assert.deepEqual([...completed].sort(), names.filter(name => name !== "source-status").sort());
      assert.ok([...requests.entries()].every(([name, signal]) => signal.aborted === (name === "source-status")));
    });
  }

  test(`the real timeout expires while reading ${phase}`, { timeout: 3000 }, async () => {
    let expired;
    // No socket startup or large HTTP transfer competes with this short timer.
    // delay keeps the event loop alive for AbortSignal.timeout's unref'ed timer.
    const loading = loadConditionDatasets("/candidate", {
      timeoutMs: 20,
      fetchImpl: async (url, { signal }) => {
        const name = url.split("/").at(-1).replace(/\.json$/, "");
        if (name !== "source-status") return new Response(bytes[name]);
        const waitForAbort = () => new Promise((resolve, reject) => {
          signal.addEventListener("abort", () => { expired = signal.reason; reject(expired); }, { once: true });
        });
        if (phase === "headers") return waitForAbort();
        return { ok: true, json: waitForAbort };
      },
    });
    const [loaded] = await Promise.all([loading, delay(30)]);
    assert.equal(expired?.name, "TimeoutError");
    assertPartialDatasets(loaded);
  });
}
