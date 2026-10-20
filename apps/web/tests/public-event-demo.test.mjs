import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { populationBackgroundForEvent } from "../lib/population-background.js";

const dataUrl = new URL("../public/data/public-event-demo.json", import.meta.url);
const componentUrl = new URL("../components/PublicEventFusionDemo.js", import.meta.url);
const dashboardUrl = new URL("../components/V2DailyDashboard.js", import.meta.url);

test("confirmed fixture geography selects only verified historical D1 background", async () => {
  const data = JSON.parse(await readFile(dataUrl, "utf8"));
  const population = JSON.parse(await readFile(new URL("../public/data/population-112Y12M.json", import.meta.url), "utf8"));
  const background = await populationBackgroundForEvent(data.previous_event, population);
  assert.equal(background.role, "BACKGROUND_ONLY");
  assert.equal(background.period, "112Y12M");
  assert.equal(background.value, 235441);
  assert.equal(background.source_url, population.source_url);
  assert.equal(background.dataset_id, new URL(population.source_url).searchParams.get("COL"));
  assert.equal(background.resource_id, population.source_receipt.csv_filename);
  assert.equal(background.source_receipt.raw_csv_sha256, population.source_receipt.raw_csv_sha256);
  assert.match(background.note, /歷史.*不代表即時人流/);
  await assert.rejects(populationBackgroundForEvent(data.current_event, population), /CONFIRMED/);
  await assert.rejects(populationBackgroundForEvent({ ...data.previous_event, district_id: null }, population), /行政區/);
  await assert.rejects(populationBackgroundForEvent(data.previous_event, { ...population, period: "115Y12M" }), /固定期別/);
});

test("public event demo is explicitly fixture-only and preserves the fusion evidence chain", async () => {
  const data = JSON.parse(await readFile(dataUrl, "utf8"));
  assert.equal(data.kind, "GOVINTEL_PUBLIC_EVENT_DEMO");
  assert.equal(data.status, "FIXTURE_ONLY");
  assert.equal(data.production_verified, false);
  assert.equal(data.current_event.public_event_id, data.previous_event.public_event_id);
  assert.equal(data.current_event.independent_source_count, 3);
  assert.equal(data.current_event.source_provenance.length, 3);
  assert.deepEqual(data.current_event.source_provenance[0].acquisition_paths, ["DIRECT_OFFICIAL"]);
  assert.equal(data.documents.length, 3);
  assert.equal(data.current_event.fusion_status, "CONFLICT");
  assert.deepEqual(data.current_event.conflict_fields, ["event_start_at"]);
  assert.equal(data.previous_event.fusion_status, "CONFIRMED");
  assert.equal(data.current_event.linked_document_versions.length, 4);
  assert.deepEqual(data.manual_actions.map((action) => action.action), ["SPLIT", "MERGE"]);
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
  assert.match(component, /證據取得路徑/);
  assert.match(component, /production_verified !== false/);
});
