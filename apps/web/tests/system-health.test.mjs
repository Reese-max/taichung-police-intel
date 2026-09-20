import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const healthUrl = new URL("../public/data/system-health.json", import.meta.url);
const componentUrl = new URL("../components/V2DailyDashboard.js", import.meta.url);

test("system health receipt exposes lane and stage evidence", async () => {
  const health = JSON.parse(await readFile(healthUrl, "utf8"));
  assert.equal(health.schema_version, 1);
  assert.ok(["HEALTHY", "DEGRADED", "STALE", "PARTIAL", "BLOCKED", "UNKNOWN"].includes(health.overall));
  assert.deepEqual(Object.keys(health.lanes).sort(), ["discovery", "publication", "query"]);
  assert.ok(Array.isArray(health.stages) && health.stages.length >= 4);
  assert.ok(health.stages.some((stage) => stage.stage === "public_http_verification"));
});

test("dashboard renders the saved health receipt without treating UNKNOWN as success", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /system-health\.json/);
  assert.match(source, /v2-system-health/);
  assert.match(source, /UNKNOWN 不會被解讀成成功/);
});
