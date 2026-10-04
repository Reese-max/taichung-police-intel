#!/usr/bin/env node
// Actual UI acceptance for a served checkout. These observations are separate
// from Python fixture scores, real-source validity, model and human experiments.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
const { chromium } = createRequire(import.meta.url)("playwright-core");

const flags = Object.fromEntries(process.argv.slice(2).reduce((rows, item, index, args) => {
  if (item.startsWith("--")) rows.push([item.slice(2), args[index + 1]]);
  return rows;
}, []));
if (!flags["base-url"] || !flags.out) throw new Error("Usage: node scripts/e2e-v6-browser.mjs --base-url URL --out DIR");
const base = new URL(flags["base-url"]);
if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) throw new Error("Invalid base URL");
const root = base.href.replace(/\/$/, "");
const out = path.resolve(flags.out);
await mkdir(out, { recursive: true });
const storageKey = "govintel.v2.conditions.v1";
const checks = [], events = [], contexts = [];
let browser, page, chain = true;
const unread = (target) => target.locator('.lc-update[data-kind="UPDATE"]').filter({ has: target.getByRole("button", { name: "標記這次更新已讀", exact: true }) });
const state = (target) => target.evaluate((key) => JSON.parse(localStorage.getItem(key) || "null"), storageKey);
const waitText = (target, text) => target.getByText(text).first().waitFor({ state: "visible" });
async function observation(target) {
  return target.evaluate((key) => ({
    url: location.href,
    condition_state: JSON.parse(localStorage.getItem(key) || "null"),
    cards: [...document.querySelectorAll('.lc-update[data-kind="UPDATE"]')].map((card) => {
      let comparison = null;
      try { comparison = JSON.parse(card.querySelector("pre")?.textContent || "null"); } catch {}
      return { text: card.textContent, update_id: comparison?.after?.document_version_id || null,
        unread: [...card.querySelectorAll("button")].some((button) => button.textContent === "標記這次更新已讀") };
    }),
    status_text: [...document.querySelectorAll('[role="status"], [role="alert"]')].map((node) => node.textContent),
  }), storageKey);
}
async function check(id, target, fn, { dependent = false } = {}) {
  if (dependent && !chain) { checks.push({ id, status: "NOT_RUN", reason: "prerequisite flow failed" }); return; }
  try {
    const details = await fn();
    const screenshot = `${id}.png`;
    await target.screenshot({ path: path.join(out, screenshot), fullPage: true });
    checks.push({ id, status: "PASS", details, screenshot });
    events.push({ check: id, observed: await observation(target) });
  } catch (error) {
    if (dependent) chain = false;
    const screenshot = `${id}-FAIL.png`;
    try { await target.screenshot({ path: path.join(out, screenshot), fullPage: true }); } catch {}
    checks.push({ id, status: "FAIL", error: String(error.message), screenshot });
    try { events.push({ check: id, observed: await observation(target) }); } catch {}
  }
}
async function newPage(options = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: "zh-TW", timezoneId: "Asia/Taipei", ...options });
  contexts.push(context);
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true });
  const target = await context.newPage();
  target.setDefaultTimeout(9000);
  return target;
}
async function replayQuery(target, snapshot = "R1") {
  await target.goto(`${root}/public-query/`);
  await target.locator("#pq-mode").selectOption("replay");
  await target.locator('#pq-snapshot option[value="R1"]').waitFor({ state: "attached" });
  await target.locator("#pq-snapshot").selectOption(snapshot);
}
async function roadFilters(target) {
  await target.locator("#pq-road").fill("測試路 A 至 B");
  await target.locator("#pq-time-from").fill("2026-10-05T09:00");
  await target.locator("#pq-time-to").fill("2026-10-07T17:00");
  await target.locator("#pq-daily-from").fill("09:00");
  await target.locator("#pq-daily-to").fill("17:00");
}
async function submit(target) {
  await target.locator('form button[type="submit"]').click();
  await target.locator(".pq-results, .pq-error").first().waitFor({ state: "visible" });
}
async function tracking(target, snapshot = "R1", { navigate = false } = {}) {
  if (navigate) await target.goto(`${root}/tracking/`);
  await target.getByLabel("資料範圍", { exact: false }).selectOption("demo:commute");
  await target.getByLabel("重播資料截止", { exact: false }).selectOption(snapshot);
  await waitText(target, new RegExp(`合成重播 ${snapshot}`));
  await target.waitForFunction((key) => {
    const value = JSON.parse(localStorage.getItem(key) || "null");
    return value && Object.values(value.conditions).some((condition) => condition.namespace === "demo:commute" && condition.baseline_initialized);
  }, storageKey);
}
async function unreadCount(target, expected) {
  await target.waitForFunction((n) => [...document.querySelectorAll('.lc-update[data-kind="UPDATE"] button')].filter((button) => button.textContent === "標記這次更新已讀").length === n, expected);
  assert.equal(await unread(target).count(), expected);
}

let fatal = null;
try {
  browser = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH || "/usr/bin/chromium", headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"] });
  page = await newPage();
  await check("01-visible-navigation", page, async () => {
    await page.goto(`${root}/`);
    const nav = page.getByRole("navigation", { name: "主要導覽" });
    for (const name of ["概覽", "公開查詢", "我的追蹤", "來源狀態"]) assert.equal(await nav.getByRole("link", { name, exact: true }).isVisible(), true);
    assert.equal(await page.getByRole("heading", { name: "查找公告，追蹤你在意的變動" }).isVisible(), true);
    return { routes_visible: ["/", "/public-query/", "/tracking/", "/sources/"] };
  });
  await check("02-save-query-condition", page, async () => {
    await replayQuery(page); await roadFilters(page); await submit(page);
    assert.equal(await page.locator(".pq-event-card").count(), 1);
    await page.getByRole("button", { name: "將這組實際套用條件加入追蹤", exact: true }).click();
    await waitText(page, /已保存/);
    const saved = await state(page);
    const condition = Object.values(saved.conditions).find((row) => row.namespace === "demo:commute");
    assert.equal(condition.filters.road, "測試路 A 至 B");
    assert.equal(condition.filters.time_to, "2026-10-07T17:00+08:00");
    assert.equal(condition.filters.daily_from, "09:00"); assert.equal(condition.filters.daily_to, "17:00");
    return { filters: condition.filters, namespace: condition.namespace };
  }, { dependent: true });
  await check("03-reopen-formatting-suppressed", page, async () => {
    await tracking(page, "R2", { navigate: true }); await unreadCount(page, 0);
    assert.equal(Object.values((await state(page)).conditions)[0].enabled, true);
    return { snapshot: "R2", observed_unread_count: 0, scope: "same browser condition saved from R1" };
  }, { dependent: true });
  await check("04-extension-and-read", page, async () => {
    await tracking(page, "R3"); await unreadCount(page, 1);
    const card = unread(page).first(); assert.match(await card.textContent(), /UPD-005/);
    const saved = await state(page); const condition = Object.values(saved.conditions)[0];
    assert.equal(Date.parse(condition.tracked_time_to), Date.parse("2026-10-09T17:00:00+08:00"));
    assert.equal(condition.filters.time_to, "2026-10-07T17:00+08:00");
    assert.equal(condition.enabled, true);
    await card.getByRole("button", { name: "標記這次更新已讀", exact: true }).click(); await unreadCount(page, 0);
    return { displayed_update_id: "UPD-005", observed_unread_before_read: 1, derived_tracked_end: condition.tracked_time_to, original_filter_end: condition.filters.time_to };
  }, { dependent: true });
  await check("05-read-persists-across-reopen", page, async () => {
    await page.reload(); await tracking(page, "R3"); await unreadCount(page, 0);
    assert.ok(Object.keys((await state(page)).read_entries).length > 0);
    return { snapshot: "R3", observed_unread_count: 0, operation: "document reload restores saved IDs" };
  }, { dependent: true });
  for (const snapshot of ["R4", "R5"]) await check(`06-${snapshot}-gap-is-not-complete`, page, async () => {
    await tracking(page, snapshot); await waitText(page, /此快照有資料缺口/);
    assert.equal(Object.values((await state(page)).conditions)[0].enabled, true);
    return { snapshot, condition_remains_enabled: true, coverage: "explicit gap, no complete-zero inference" };
  }, { dependent: true });
  await check("07-expiry-does-not-cancel", page, async () => {
    await tracking(page, "R6"); const condition = Object.values((await state(page)).conditions)[0];
    assert.equal(condition.enabled, true);
    assert.ok(Date.parse(condition.tracked_time_to) <= Date.parse("2026-10-09T17:10:00+08:00"));
    await unreadCount(page, 0);
    return { snapshot: "R6", condition_remains_enabled: true, derived_end_has_passed: true, scope: "does not assert Python observation-status equivalence" };
  }, { dependent: true });
  await check("08-explicit-lift-read-keeps-condition", page, async () => {
    await tracking(page, "R7"); await unreadCount(page, 1);
    const card = unread(page).first(); assert.match(await card.textContent(), /UPD-006/); assert.match(await card.textContent(), /解除/);
    await card.getByRole("button", { name: "標記這次更新已讀", exact: true }).click(); await unreadCount(page, 0);
    assert.equal(Object.values((await state(page)).conditions)[0].enabled, true);
    return { displayed_update_id: "UPD-006", explicit_lift_visible: true, read_does_not_cancel_condition: true };
  }, { dependent: true });
  await check("09-user-cancel-stops-prompts", page, async () => {
    await page.getByRole("button", { name: "取消追蹤條件", exact: true }).click();
    await waitText(page, /已停止此條件的提示/); await tracking(page, "R9"); await unreadCount(page, 0);
    assert.equal(Object.values((await state(page)).conditions)[0].enabled, false);
    return { snapshot: "R9", observed_unread_count: 0, user_cancelled: true };
  }, { dependent: true });
  await check("10-namespace-isolation", page, async () => {
    await page.getByLabel("資料範圍", { exact: false }).selectOption("published");
    await waitText(page, "尚未保存此資料範圍的條件；可先從公開查詢加入。");
    assert.equal(await page.locator(".lc-condition").count(), 0);
    assert.ok(Object.values((await state(page)).conditions).some((row) => row.namespace === "demo:commute"));
    return { saved_demo_state_retained: true, published_condition_count: 0 };
  }, { dependent: true });

  const validation = await newPage();
  await check("11-invalid-filter-range", validation, async () => {
    await replayQuery(validation); await roadFilters(validation);
    await validation.locator("#pq-time-from").fill("2026-10-09T09:00"); await submit(validation);
    await waitText(validation, /結束時間不能早於起始時間/);
    assert.equal(await validation.locator(".pq-event-card").count(), 0);
    return { reversed_range_rejected: true };
  });
  await check("12-no-match-does-not-claim-safety", validation, async () => {
    await replayQuery(validation); await validation.locator("#pq-road").fill("不存在的合成路名"); await submit(validation);
    await waitText(validation, /不代表道路安全或已解除/);
    assert.equal(await validation.locator(".pq-event-card").count(), 0);
    return { finite_no_match: true, reality_and_safety_inference_rejected: true };
  });
  for (const [snapshot, label] of [["R4", "FAILED"], ["R5", "PARTIAL"]]) await check(`13-query-${snapshot}-source-gap`, validation, async () => {
    await replayQuery(validation, snapshot); await validation.locator("#pq-road").fill("測試路 A 至 B"); await submit(validation);
    await waitText(validation, new RegExp(label));
    assert.equal(await validation.locator(".pq-gaps").isVisible(), true);
    return { snapshot, source_gap_label: label, cached_records_not_complete_source_success: true };
  });
  const badHash = await newPage();
  await badHash.route("**/data/public-query-replay.json", async (route) => {
    const response = await route.fetch(); const json = await response.json(); json.notice += " integrity mutation";
    await route.fulfill({ response, json });
  });
  await check("14-replay-hash-mutation-refused", badHash, async () => {
    await badHash.goto(`${root}/tracking/`); await waitText(badHash, /合成重播未啟用.*hash/);
    assert.equal(await badHash.locator('option[value="demo:commute"]').count(), 0);
    return { controlled_data_mutation_refused: true, demo_activation: "blocked" };
  });
  const quota = await newPage();
  await quota.addInitScript((key) => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function (name, value) {
      if (name === key) throw new DOMException("Controlled storage quota failure", "QuotaExceededError");
      return original.call(this, name, value);
    };
  }, storageKey);
  await check("15-storage-failure-no-success", quota, async () => {
    await replayQuery(quota); await roadFilters(quota); await submit(quota);
    await quota.getByRole("button", { name: "將這組實際套用條件加入追蹤", exact: true }).click();
    await waitText(quota, /保存失敗，未顯示成功/);
    assert.equal(await state(quota), null);
    return { controlled_quota_failure_visible: true, condition_saved: false };
  });
  const sources = await newPage();
  await sources.route("**/data/source-status.json", (route) => route.fulfill({ status: 503, body: "Controlled source failure" }));
  await check("16-source-page-failure-not-zero", sources, async () => {
    await sources.goto(`${root}/sources/`); await waitText(sources, /來源狀態暫時無法取得/);
    assert.equal(await sources.locator(".govintel-source-card").count(), 0);
    assert.equal(await sources.getByRole("alert").count(), 1);
    return { controlled_503_visible: true, source_state_not_reported_as_successful_zero: true };
  });
  const mobile = await newPage({ viewport: { width: 375, height: 812 }, isMobile: true, deviceScaleFactor: 1 });
  await check("17-mobile-navigation-and-dialog", mobile, async () => {
    const widths = [];
    for (const route of ["/", "/public-query/", "/tracking/", "/sources/"]) {
      await mobile.goto(`${root}${route}`);
      await mobile.getByRole("navigation", { name: "主要導覽" }).waitFor({ state: "visible" });
      const width = await mobile.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
      widths.push({ route, ...width }); assert.ok(width.document <= width.viewport + 1, JSON.stringify(width));
    }
    await replayQuery(mobile, "R3"); await mobile.locator("#pq-road").fill("測試路 A 至 B"); await submit(mobile);
    const details = mobile.getByRole("button", { name: "查看詳情、版本歷史與版本比較", exact: true }).first();
    await details.click(); await mobile.locator("dialog[open]").waitFor({ state: "visible" });
    await mobile.keyboard.press("Escape"); await mobile.locator("dialog[open]").waitFor({ state: "hidden" });
    assert.equal(await details.evaluate((node) => node === document.activeElement), true);
    return { widths, dialog_escape_closes: true, focus_returns_to_trigger: true };
  });
} catch (error) { fatal = String(error.stack || error); }
finally {
  for (let index = 0; index < contexts.length; index++) {
    try { await contexts[index].tracing.stop({ path: path.join(out, `context-${index + 1}-trace.zip`) }); } catch {}
    await contexts[index].close();
  }
  if (browser) await browser.close();
}
let driverCommit = null;
try { driverCommit = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim(); } catch {}
const receipt = {
  schema_version: 1, status: fatal || checks.some((row) => row.status !== "PASS") ? "FAIL" : "PASS",
  execution_scope: "ACTUAL_BROWSER_FINITE_SYNTHETIC_UI_FLOW", base_url: root,
  generated_at: new Date().toISOString(), driver_checkout_commit: driverCommit,
  served_checkout_identity: "Not inferred from driver checkout; bind this receipt to the server build separately.",
  browser: "Chromium", viewport_primary: { width: 1280, height: 900 }, mobile_width: 375,
  checks, fatal, observed_browser_events: events,
  not_measured: ["Python gold TP/FP/FN equivalence", "production source coverage", "human task time and effectiveness", "semantic AI gain", "event-level holdout generalization"],
  metrics: { browser_precision: null, browser_recall: null, human_participants: null },
};
await writeFile(path.join(out, "browser-acceptance.json"), JSON.stringify(receipt, null, 2) + "\n");
console.log(JSON.stringify({ status: receipt.status, passed: checks.filter((row) => row.status === "PASS").length, total: checks.length, output: path.join(out, "browser-acceptance.json"), failures: checks.filter((row) => row.status !== "PASS").map(({ id, status, error }) => ({ id, status, error })), fatal }));
process.exitCode = receipt.status === "PASS" ? 0 : 1;
