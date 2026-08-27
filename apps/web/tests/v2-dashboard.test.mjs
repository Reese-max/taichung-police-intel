import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";


const briefUrl = new URL("../public/data/v2-daily-brief.json", import.meta.url);
const feedUrl = new URL("../public/data/intelligence-feed.json", import.meta.url);
const statusUrl = new URL("../public/data/source-status.json", import.meta.url);
const componentUrl = new URL("../components/V2DailyDashboard.js", import.meta.url);
const layoutUrl = new URL("../app/layout.js", import.meta.url);

async function json(url) {
  return JSON.parse(await readFile(url, "utf8"));
}

test("checked-in V2 brief uses the police-user publication schema", async () => {
  const brief = await json(briefUrl);
  assert.equal(brief.schema_version, 1);
  assert.equal(brief.mode, "V2_SHADOW");
  assert.equal(brief.generator_version, 2);
  assert.ok(Array.isArray(brief.audience) && brief.audience.length > 0);
  assert.ok(Array.isArray(brief.priority_items));
  assert.ok(Array.isArray(brief.tracking_items));
  assert.ok(Array.isArray(brief.other_changes));
  assert.ok(brief.priority_items.length <= 3);
  assert.ok(brief.tracking_items.length <= 5);
});

test("V2 brief and official-source bundle refer to one collection run", async () => {
  const [brief, feed, status] = await Promise.all([
    json(briefUrl),
    json(feedUrl),
    json(statusUrl),
  ]);
  assert.equal(brief.source_collection_run_id, feed.collection_run_id);
  assert.equal(
    brief.source_collection_run_id,
    status.latest_collection_run.collection_run_id,
  );
  assert.equal(brief.generated_at, feed.generated_at);
  assert.equal(brief.source_status_generated_at, status.generated_at);
});

test("baseline separates current changes from the historical archive", async () => {
  const [brief, feed] = await Promise.all([json(briefUrl), json(feedUrl)]);
  assert.equal(brief.overview.archive_total, feed.items.length);
  assert.equal(brief.overview.current_change_count, 0);
  assert.equal(brief.overview.priority_count, 0);
  assert.match(brief.status_message, /未偵測到可確認的新增或修正/);
  assert.ok(brief.overview.legacy_home_candidate_count > brief.overview.current_change_count);
});

test("daily publication never promotes CONFIRMED or UNCHANGED to Top 3", async () => {
  const brief = await json(briefUrl);
  const prohibited = new Set(["CONFIRMED", "UNCHANGED", "REMOVAL_CANDIDATE"]);
  assert.deepEqual(
    brief.priority_items.filter((item) => prohibited.has(item.change_type)),
    [],
  );
});

test("formal layout renders V2 before the collapsed legacy interface", async () => {
  const source = await readFile(layoutUrl, "utf8");
  const dashboardIndex = source.indexOf("<V2DailyDashboard />");
  const legacyIndex = source.indexOf("legacy-system-details");
  const childrenIndex = source.indexOf("{children}");
  assert.ok(dashboardIndex >= 0, "V2 dashboard must be rendered");
  assert.ok(legacyIndex > dashboardIndex, "legacy interface must follow V2");
  assert.ok(childrenIndex > legacyIndex, "legacy children must remain inside the collapsed section");
  assert.match(source, /臺中警政每日情資/);
});

test("V2 dashboard is police-first, Top 3 capped, and evidence-bound", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /v2-daily-brief\.json/);
  assert.match(source, /priority_items\.slice\(0, 3\)/);
  assert.match(source, /本期沒有需要處理的重要變更/);
  assert.match(source, /why_it_matters/);
  assert.match(source, /recommended_action/);
  assert.match(source, /affected_roles/);
  assert.match(source, /開啟官方來源/);
  assert.match(source, /DETERMINISTIC_PASS/);
  assert.doesNotMatch(source, /AUTO_PASS/);
});

test("V2 dashboard distinguishes fetch failure from a valid zero-change period", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /fetch_failed/);
  assert.match(source, /schema_invalid/);
  assert.match(source, /這不是「零筆情報」/);
  assert.match(source, /本期沒有需要處理的重要變更/);
});

test("historical feed is explicitly labelled archive rather than current intelligence", async () => {
  const source = await readFile(componentUrl, "utf8");
  assert.match(source, /歷史資料庫/);
  assert.match(source, /不代表本期新增/);
  assert.match(source, /歷史資料 · 開啟官方來源/);
});
