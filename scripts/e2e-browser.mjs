#!/usr/bin/env node
/** Real-browser checks for the same-checkout candidate. */
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

const args = process.argv.slice(2);
const value = (name) => {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] : null;
};
const base = value("base-url");
const out = value("out") || ".";
if (!base) {
  console.error("usage: e2e-browser.mjs --base-url URL [--out DIR]");
  process.exit(2);
}
mkdirSync(out, { recursive: true });

const checks = [];
const screenshots = [];
const record = (id, ok, detail) => checks.push({ id, status: ok ? "PASS" : "FAIL", detail });
const shot = async (page, name) => {
  const file = path.join(out, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  screenshots.push(file);
};

async function launch() {
  try {
    return await chromium.launch({ channel: "chrome" });
  } catch {}
  const candidates = [
    process.env.GOVINTEL_CHROME_PATH,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  for (const executablePath of candidates) {
    try {
      return await chromium.launch({ executablePath });
    } catch {}
  }
  return chromium.launch();
}

async function main() {
  const browser = await launch();
  try {
    const page = await browser.newPage();
    await page.goto(`${base}/`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForSelector(".v2-archive-list a", { timeout: 15000 });
    record("dashboard_loaded", true, "V2 dashboard rendered with official archive links");

    const version = await page.$("[data-testid=candidate-version]");
    const versionText = version ? await version.innerText() : "";
    record("version_surface_present", /資料版本與涵蓋範圍/.test(versionText), versionText.slice(0, 180));
    const generation = await page.$("[data-testid=query-generation]");
    const generationText = generation ? await generation.innerText() : "";
    record("generation_surface_present", /[0-9a-f]{12,}/i.test(generationText), generationText || "missing");

    const queryInput = page.locator(".v2-query-form input");
    await queryInput.fill("警察");
    await page.locator(".v2-query-form button").click();
    await page.waitForSelector(".v2-query-result", { timeout: 15000 });
    const queryText = await page.locator(".v2-query-result").innerText();
    record("browser_query_returns_typed_status", /STALE|RECENT|PARTIAL|UNKNOWN/.test(queryText), queryText.slice(0, 220));
    record("browser_query_exposes_publication_hash", /publication hash: [0-9a-f]{64}/i.test(queryText), queryText.slice(-100));
    await shot(page, "query");

    const archiveInput = page.locator(".v2-archive-search input");
    const initialCount = await page.locator(".v2-archive-list a").count();
    await archiveInput.fill("S-009");
    await page.waitForTimeout(100);
    const filteredCount = await page.locator(".v2-archive-list a").count();
    const filteredLabels = await page.locator(".v2-archive-list a span").allInnerTexts();
    record("browser_archive_filters_results", filteredCount > 0 && filteredLabels.every((label) => /提案/.test(label)), `${initialCount} -> ${filteredCount}`);

    const link = page.locator(".v2-archive-list a").first();
    const href = await link.getAttribute("href");
    const target = await link.getAttribute("target");
    record("source_link_is_official_https", /^https:\/\//.test(href || "") && target === "_blank", `${href} target=${target}`);
    try {
      // rel="noreferrer" intentionally removes the opener; Chromium reports
      // the new tab on the context instead of the source page's popup event.
      const popupPromise = page.context().waitForEvent("page", { timeout: 10000 });
      await link.click();
      const popup = await popupPromise;
      await popup.waitForLoadState("domcontentloaded", { timeout: 15000 }).catch(() => {});
      record("source_link_opens_in_browser", /^https:\/\//.test(popup.url()), popup.url());
      await popup.close();
    } catch (error) {
      record("source_link_opens_in_browser", false, `${error.name}: ${error.message}`);
    }
    await shot(page, "source");

    await archiveInput.fill("zzzqqq-no-such-record");
    await page.waitForSelector(".v2-archive-list p", { timeout: 10000 }).catch(() => {});
    const emptyText = await page.locator(".v2-archive-list").innerText();
    record("valid_empty_is_distinct", /找不到符合關鍵字的歷史資料/.test(emptyText) && !/無法安全呈現|錯誤/.test(emptyText), emptyText);
    await shot(page, "empty");

    await page.reload({ waitUntil: "networkidle" });
    const after = await page.locator("[data-testid=query-generation]").innerText().catch(() => "");
    record("refresh_preserves_generation", !generationText || after === generationText, `before=${generationText} after=${after}`);
    const response = await page.request.get(`${base}/api/version`);
    const versionDoc = response.ok() ? await response.json() : null;
    record("http_version_is_readable", Boolean(versionDoc?.generation_id && versionDoc?.policy_hash), JSON.stringify(versionDoc));
  } finally {
    await browser.close();
  }
}

try {
  await main();
} catch (error) {
  record("browser_run", false, `${error.name}: ${String(error.message).slice(0, 300)}`);
}
const failed = checks.filter((check) => check.status === "FAIL");
const result = { status: failed.length === 0 && checks.length > 0 ? "PASS" : "FAIL", passed: checks.length - failed.length, total: checks.length, checks, screenshots };
console.log(JSON.stringify(result));
process.exit(result.status === "PASS" ? 0 : 1);
