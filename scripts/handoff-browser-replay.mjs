#!/usr/bin/env node
// Actual V2 browser flow with labeled synthetic historical source fixtures.
// No live source, model, user or production acceptance is implied.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { readFile, writeFile, mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = fileURLToPath(new URL("../", import.meta.url));
const out = path.resolve(process.argv[2] || "/tmp/govintel-handoff-replay");
const site = path.join(root, "apps/web/out");
const key = "govintel.v2.handoff.v1";
const sha = (value) => createHash("sha256").update(value).digest("hex");
const clone = (value) => JSON.parse(JSON.stringify(value));
const codeSha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
const fixtureLabel = "合成歷史流程重播；不是現行官方公告、真實使用者或 production 驗收";
const docs = new Map();
const source = (name, version) => {
  const time = version === 1 ? "17:00" : "16:00";
  const url = `https://example.gov.tw/handoff-replay?item=${name}&v=${version}`;
  const body = `<h1>${fixtureLabel}</h1><p>示例 ${name} 公告 v${version}：${time}</p>`;
  docs.set(url, body);
  return {
    identity: `S-032:handoff-replay-${name}`, source_id: "S-032", stable_key: `handoff-replay-${name}`,
    headline: `合成歷史重播 ${name} 交通公告 ${time}`, title: `合成歷史重播 ${name} 交通公告 ${time}`,
    official_url: url, source_version: version, source_sha256: sha(body),
    source_document_version: `DEMO-DOCV-${name}-${version}`, change_type: version === 1 ? "NEW" : "REVISED",
    what_changed: fixtureLabel, why_it_matters: "本機追蹤與版本保存驗收", recommended_action: "核對示例版本",
    temporal_basis: "DETECTED_CHANGE", affected_roles: [], verification_status: "DETERMINISTIC_PASS",
  };
};
const a1 = source("A", 1), a2 = source("A", 2), a3 = source("A", 3), b1 = source("B", 1), b2 = source("B", 2);
const baseBrief = JSON.parse(await readFile(path.join(root, "apps/web/public/data/v2-daily-brief.json"), "utf8"));
const baseStatus = JSON.parse(await readFile(path.join(root, "apps/web/public/data/source-status.json"), "utf8"));
let phase = 1, health = "PASS";
function packet() {
  const generated_at = `2026-09-${20 + phase}T01:00:00+08:00`;
  const run = `SYNTHETIC-HANDOFF-RUN-${phase}`;
  const items = phase === 2 ? [] : [phase < 3 ? a1 : phase < 5 ? a2 : a3, phase < 5 ? b1 : b2];
  const brief = { ...clone(baseBrief), generated_at, source_status_generated_at: generated_at,
    source_collection_run_id: run, profile_views: [], priority_items: items, tracking_items: [], other_changes: [],
    overview: { ...baseBrief.overview, current_change_count: items.length, priority_count: items.length, tracking_count: 0 },
    status_message: fixtureLabel,
  };
  const status = { ...clone(baseStatus), generated_at, latest_collection_run: { ...baseStatus.latest_collection_run, collection_run_id: run },
    sources: [{ ...clone(baseStatus.sources[0]), source_id: "S-032", source_name: `合成歷史重播 S-032（${fixtureLabel}）`,
      source_url: a1.official_url, current_source_run_id: `${run}-SOURCE-032`,
      source_health: health === "PARTIAL" ? "DEGRADED" : health,
      result: health === "PASS" ? "NEW_ITEMS" : health,
      window_completeness: health === "PASS" ? "COMPLETE_WITH_ITEMS" : health === "FAILED" ? "NOT_RUN" : "PARTIAL",
      intelligence_gaps: health === "PASS" ? [] : ["合成重播：來源擷取缺口"],
    }],
  };
  const feed = { schema_version: 1, generated_at, collection_run_id: run, items: [], fixture_label: fixtureLabel };
  return { brief, status, feed };
}
await mkdir(out, { recursive: true });
await readFile(path.join(site, "index.html"));
const server = createServer(async (req, res) => {
  try {
    let name = decodeURIComponent(new URL(req.url, "http://localhost").pathname);
    if (name.endsWith("/")) name += "index.html";
    const file = path.resolve(site, `.${name}`);
    if (!file.startsWith(site + path.sep)) throw new Error("outside site");
    const types = { ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".html": "text/html", ".svg": "image/svg+xml" };
    res.setHeader("Content-Type", types[path.extname(file)] || "application/octet-stream");
    res.end(await readFile(file));
  } catch { res.writeHead(404).end(); }
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const base = `http://127.0.0.1:${server.address().port}`;
let browser;
const checks = [], downloads = [];
try {
  browser = await chromium.launch({ executablePath: process.env.GOVINTEL_CHROME_PATH || "/usr/bin/chromium", args: ["--no-sandbox"], headless: true });
  const context = await browser.newContext({ acceptDownloads: true, viewport: { width: 1280, height: 900 } });
  await context.route("**/*", (route) => new URL(route.request().url()).origin === base ? route.continue() : route.abort());
  await context.route((url) => docs.has(url.href), (route) => route.fulfill({ contentType: "text/html", body: docs.get(route.request().url()) }));
  await context.route("**/data/v2-daily-brief.json", (route) => route.fulfill({ json: packet().brief }));
  await context.route("**/data/source-status.json", (route) => route.fulfill({ json: packet().status }));
  await context.route("**/data/intelligence-feed.json", (route) => route.fulfill({ json: packet().feed }));
  const page = await context.newPage(); page.setDefaultTimeout(10000);
  const state = () => page.evaluate((storageKey) => JSON.parse(localStorage.getItem(storageKey)), key);
  let receivedBriefHash;
  const reopen = async () => {
    const [response] = await Promise.all([
      page.waitForResponse((value) => value.url().endsWith("/data/v2-daily-brief.json")), page.goto(base),
    ]);
    receivedBriefHash = sha(await response.body());
    await page.getByTestId("local-handoff-toolbar").waitFor();
  };
  const download = async (format, label) => {
    const [saved] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: `匯出 ${format}`, exact: true }).click()]);
    const file = path.join(out, `${label}.${format === "JSON" ? "json" : "md"}`);
    await saved.saveAs(file); const bytes = await readFile(file); downloads.push({ file: path.basename(file), sha256: sha(bytes) });
    return format === "JSON" ? JSON.parse(bytes.toString()) : bytes.toString();
  };
  await reopen();
  await page.getByRole("button", { name: "加入追蹤", exact: true }).first().click();
  await page.getByRole("button", { name: "加入追蹤", exact: true }).click();
  assert.equal(Object.keys((await state()).watch_items).length, 2);
  await page.getByRole("button", { name: "確認／更新交班版本", exact: true }).click();
  await page.waitForFunction((k) => JSON.parse(localStorage.getItem(k)).handoffs.length === 1, key);
  const v1 = clone((await state()).handoffs[0]);
  assert.equal(v1.publication.brief_sha256, receivedBriefHash);
  assert.deepEqual((await download("JSON", "day1-v1")).handoff, v1); checks.push("add_and_confirm_v1");
  phase = 2; await reopen();
  assert.equal(Object.keys((await state()).watch_items).length, 2);
  assert.equal(await page.getByTestId("v2-tracking-list").locator("article").count(), 2);
  assert.deepEqual((await state()).handoffs[0], v1); checks.push("zero_changes_reload_preserves_tracking");
  phase = 3; await reopen();
  await page.getByTestId("v2-tracking-list").getByText(/需重新核對/).waitFor();
  const stale = await download("JSON", "day3-v1-needs-review");
  assert.deepEqual(stale.handoff, v1); assert.equal(stale.review_warnings.length, 1);
  assert.equal(stale.review_warnings[0].identity, a1.identity);
  assert.equal(stale.review_warnings[0].confirmed_source_sha256, a1.source_sha256);
  assert.equal(stale.review_warnings[0].latest_source_sha256, a2.source_sha256);
  const markdown = await download("Markdown", "day3-v1-needs-review");
  assert.ok(markdown.includes("NEEDS_REVIEW") && markdown.includes(a1.source_document_version));
  checks.push("claim_scoped_invalidation_and_immutable_v1_export");
  await page.screenshot({ path: path.join(out, "day3-needs-review.png"), fullPage: true });
  phase = 1; await reopen();
  await page.getByRole("button", { name: "確認／更新交班版本", exact: true }).click();
  await page.getByRole("alert").getByText(/已知較新來源版本/).waitFor();
  assert.equal((await state()).handoffs.length, 1);
  assert.deepEqual((await state()).handoffs[0], v1);
  assert.equal((await download("JSON", "retained-v1-still-needs-review")).review_warnings.length, 1);
  checks.push("retained_old_snapshot_cannot_clear_review");
  phase = 3; await reopen();
  for (const status of ["FAILED", "PARTIAL"]) {
    health = status; await reopen();
    await page.getByTestId("v2-tracking-list").getByText(/合成重播：來源擷取缺口/).first().waitFor();
    assert.ok(Object.values((await state()).watch_items).every((watch) => watch.status !== "RESOLVED"));
    assert.ok((await page.getByTestId("v2-tracking-list").innerText()).includes(status === "PARTIAL" ? "DEGRADED" : status));
    checks.push(`${status.toLowerCase()}_source_retains_watch_and_gap`);
  }
  phase = 4; health = "PASS"; await reopen();
  await page.getByRole("button", { name: "確認／更新交班版本", exact: true }).click();
  await page.waitForFunction((k) => JSON.parse(localStorage.getItem(k)).handoffs.length === 2, key);
  const v2 = await download("JSON", "day4-v2");
  assert.equal(v2.handoff.publication.brief_sha256, receivedBriefHash);
  assert.deepEqual((await state()).handoffs[0], v1); assert.equal(v2.handoff.items[0].source_version, 2);
  assert.deepEqual(v2.review_warnings, []); checks.push("manual_confirm_v2_keeps_both_versions");
  for (const saved of [v1, v2.handoff]) {
    const entry = saved.items.find((row) => row.identity === a1.identity);
    const [response, popup] = await Promise.all([
      context.waitForEvent("response", (value) => value.url() === entry.evidence.official_url),
      page.waitForEvent("popup"), page.evaluate((url) => { window.open(url, "_blank"); }, entry.evidence.official_url),
    ]);
    await popup.waitForLoadState("domcontentloaded");
    assert.equal(sha(await response.body()), entry.source_sha256);
    assert.ok(entry.evidence.locator.endsWith(`#v${entry.source_version}`));
    await popup.close();
  }
  checks.push("both_exact_fixture_evidence_versions_replayed");
  await page.reload(); assert.equal((await state()).handoffs.length, 2); checks.push("browser_reload_replays_history");
  await page.getByTestId("v2-tracking-list").getByRole("button", { name: "標記已處理", exact: true }).first().click();
  await page.getByTestId("v2-tracking-list").getByRole("button", { name: "停止追蹤", exact: true }).click();
  assert.equal(await page.getByTestId("v2-tracking-list").locator("article").count(), 0);
  phase = 5; await reopen();
  assert.equal(await page.getByTestId("v2-tracking-list").locator("article").count(), 0);
  assert.deepEqual(Object.values((await state()).watch_items).map((watch) => watch.status).sort(), ["DISMISSED", "RESOLVED"]);
  checks.push("source_updates_preserve_terminal_watch_statuses");
  const final = await state(); assert.equal(Object.keys(final.watch_items).length, 2); assert.equal(final.handoffs.length, 2);
  await writeFile(path.join(out, "final-state.json"), JSON.stringify(final, null, 2)); checks.push("resolve_and_dismiss_keep_audit_history");
  await writeFile(path.join(out, "source-fixtures.json"), JSON.stringify({ label: fixtureLabel, sources: [a1, a2, a3, b1, b2], docs: Object.fromEntries(docs) }, null, 2));
  await writeFile(path.join(out, "receipt.json"), JSON.stringify({ status: "PASS", code_sha: codeSha, scope: "SYNTHETIC_HISTORICAL_LOCAL_BROWSER_REPLAY", label: fixtureLabel, checks, downloads, provider_calls: 0, live_source_calls: 0, production_verified: false }, null, 2));
  console.log(`HANDOFF_BROWSER_REPLAY_OK checks=${checks.length} code_sha=${codeSha} output=${out}`);
} finally { if (browser) await browser.close(); await new Promise((resolve) => server.close(resolve)); }
