import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const dataUrl = new URL("../public/data/public-event-demo.json", import.meta.url);
const componentUrl = new URL("../components/PublicEventFusionDemo.js", import.meta.url);
const dashboardUrl = new URL("../components/V2DailyDashboard.js", import.meta.url);

test("public event demo is explicitly fixture-only and preserves the fusion evidence chain", async () => {
  const data = JSON.parse(await readFile(dataUrl, "utf8"));
  assert.equal(data.kind, "GOVINTEL_PUBLIC_EVENT_DEMO");
  assert.equal(data.status, "FIXTURE_ONLY");
  assert.equal(data.production_verified, false);
  assert.equal(data.current_event.public_event_id, data.previous_event.public_event_id);
  assert.equal(data.current_event.independent_source_count, 3);
  assert.equal(data.documents.length, 3);
  assert.equal(data.current_event.fusion_status, "CONFLICT");
  assert.deepEqual(data.current_event.conflict_fields, ["event_start_at"]);
  assert.equal(data.background_preview.period, "2026-08");
  assert.match(data.background_preview.source_url, /^https:\/\//);
});

test("public event demo exposes safe local confirm, merge, and split previews", async () => {
  const [component, dashboard] = await Promise.all([
    readFile(componentUrl, "utf8"),
    readFile(dashboardUrl, "utf8"),
  ]);
  assert.match(dashboard, /PublicEventFusionDemo/);
  assert.match(component, /FIXTURE_ONLY/);
  assert.match(component, /預覽人工確認/);
  assert.match(component, /預覽人工合併/);
  assert.match(component, /預覽人工拆分/);
  assert.match(component, /未改寫 canonical state/);
  assert.match(component, /production_verified !== false/);
});
