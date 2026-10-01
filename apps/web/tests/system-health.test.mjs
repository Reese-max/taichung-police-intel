import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const healthUrl = new URL("../public/data/system-health.json", import.meta.url);
const componentUrl = new URL("../components/V2DailyDashboard.js", import.meta.url);
const healthRouteUrl = new URL("../app/api/system-health.json/route.js", import.meta.url);
const measured = metric => metric && metric.measured === true && typeof metric.value === "number";

test("system health receipt exposes lane and stage evidence", async () => {
  const health = JSON.parse(await readFile(healthUrl, "utf8"));
  assert.equal(health.schema_version, 1);
  assert.ok(["HEALTHY", "DEGRADED", "STALE", "PARTIAL", "BLOCKED", "UNKNOWN"].includes(health.overall));
  assert.deepEqual(Object.keys(health.lanes).sort(), ["discovery", "publication", "query"]);
  assert.ok(Array.isArray(health.stages) && health.stages.length >= 4);
  assert.ok(health.stages.some((stage) => stage.stage === "public_http_verification"));
  assert.equal(typeof health.operator_summary?.message, "string");
  assert.ok(health.stages.every((stage) => Object.hasOwn(stage, "last_success_at")));
  assert.ok(Array.isArray(health.review_inbox));
});

test("receipt publishes a versioned stage model covering the end-to-end chain", async () => {
  const health = JSON.parse(await readFile(healthUrl, "utf8"));
  assert.equal(health.stage_model?.version, "govintel-e2e-stages.v1");
  assert.ok(Array.isArray(health.stage_model?.stages) && health.stage_model.stages.length >= 10);
  for (const stageId of [
    "collection",
    "parsing",
    "fusion_verification",
    "canonical_validation",
    "publication_build",
    "deployment",
    "public_http_verification",
    "query_index",
    "mcp_web_query",
    "upstream_operating_state",
  ]) {
    assert.ok(health.stage_model.stage_ids.includes(stageId), `missing stage ${stageId}`);
  }
  const reported = new Set(health.stages.map(stage => stage.stage));
  for (const stageId of health.stage_model.stage_ids) {
    assert.ok(reported.has(stageId), `stage model advertises unreported stage ${stageId}`);
  }
  assert.ok(health.stages.every(stage => Object.hasOwn(stage, "error_stage")));
  assert.ok(health.stages.every(stage => Object.hasOwn(stage, "last_success_at")));
});

test("receipt publishes measurement-only SLO fields that stay unknown when unproven", async () => {
  const health = JSON.parse(await readFile(healthUrl, "utf8"));
  assert.equal(health.slo?.status, "MEASUREMENT_ONLY");
  assert.equal(health.slo.thresholds, null);
  const metrics = health.slo.metrics;
  assert.ok(metrics && Object.keys(metrics).length >= 9);
  for (const [name, metric] of Object.entries(metrics)) {
    assert.ok(Object.hasOwn(metric, "unit"), name);
    assert.equal(typeof metric.measured, "boolean", name);
    if (!metric.measured) {
      assert.equal(metric.value, null, name);
      assert.ok(metric.reason, name);
    }
  }
  assert.ok(measured(metrics.stale_source_ratio));
  assert.ok(measured(metrics.collection_success_ratio));
  // No production HTTP receipt is checked in, so publish→public-visible must stay unknown.
  assert.equal(metrics.publish_to_public_visible_ms.measured, false);
  assert.equal(health.latency_metrics.publish_to_visible_ms, null);
});

test("upstream operating state is reported without overriding GovIntel health", async () => {
  const health = JSON.parse(await readFile(healthUrl, "utf8"));
  assert.ok(["ACTIVE", "DEGRADED", "RESTORING", "PAUSED", "UNKNOWN"].includes(health.upstream?.upstream_operating_state));
  assert.equal(health.upstream.overrides_govintel_health, false);
  assert.notEqual(health.lanes.publication, "BLOCKED");
});

test("machine-readable system-health route serves the saved receipt", async () => {
  const route = await readFile(healthRouteUrl, "utf8");
  assert.match(route, /system-health\.json/);
  assert.match(route, /force-static/);
  assert.match(route, /Response\.json/);
});

test("dashboard renders the stage model, SLO metrics and upstream state", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /v2-stage-model/);
  assert.match(source, /v2-slo-metrics/);
  assert.match(source, /v2-upstream-operating-state/);
  assert.match(source, /不覆蓋 GovIntel 自身健康/);
});

test("dashboard renders the saved health receipt without treating UNKNOWN as success", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /system-health\.json/);
  assert.match(source, /v2-system-health/);
  assert.match(source, /v2-review-inbox/);
  assert.match(source, /v2-operator-summary/);
  assert.match(source, /local-review\.js/);
  assert.match(source, /建立本機 feedback/);
  assert.match(source, /addLocalReviewFeedback/);
  assert.match(source, /canonical writer/);
  assert.match(source, /UNKNOWN 不會被解讀成成功/);
});
