#!/usr/bin/env node
/**
 * Real-browser e2e for the single-checkout GovIntel candidate.
 * Drives a real Chromium/Chrome over loopback HTTP: inputs a query, reads
 * results, opens source links, checks version/generation surfaces, reloads.
 * Prints a single JSON result line on stdout; exits non-zero on failure.
 */
import { mkdirSync } from "node:fs";
import path from "node:path";
import { chromium } from "playwright-core";

const args = process.argv.slice(2);
function argValue(name) {
  const index = args.indexOf(`--${name}`);
  return index >= 0 ? args[index + 1] : null;
}
const BASE = argValue("base-url");
const OUT = argValue("out") || ".";
if (!BASE) {
  console.error("usage: e2e-browser.mjs --base-url URL [--out DIR]");
  process.exit(2);
}
mkdirSync(OUT, { recursive: true });

const CHROME_CANDIDATES = [
  process.env.GOVINTEL_CHROME_PATH,
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
  "/usr/bin/chromium-browser",
].filter(Boolean);

const checks = [];
const screenshots = [];
function record(id, ok, detail) {
  checks.push({ id, status: ok ? "PASS" : "FAIL", detail });
}
async function shot(page, name) {
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  screenshots.push(file);
}

async function launch() {
  try {
    return await chromium.launch({ channel: "chrome" });
  } catch {}
  for (const executablePath of CHROME_CANDIDATES) {
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
    await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 30000 });
    await page.waitForSelector(".v2-archive-list a", { timeout: 15000 });
    record("dashboard_loaded", true, "V2 dashboard rendered with archive entries");

    const provenance = await page.waitForSelector("[data-testid=candidate-version]", { timeout: 10000 });
    const provenanceText = await provenance.innerText();
    record(
      "version_surface_present",
      /資料版本與涵蓋範圍/.test(provenanceText) && /保存快照 · 非即時資料/.test(provenanceText),
      provenanceText.slice(0, 160).replace(/\n/g, " "),
    );

    const generationBadge = await provenance.$(".v2-generation-badge");
    const badgeText = generationBadge ? await generationBadge.innerText() : "";
    record("generation_consistency_badge", /同一世代|拒絕混版/.test(badgeText), badgeText);

    const queryGeneration = await page.$("[data-testid=query-generation]");
    const generationText = queryGeneration ? await queryGeneration.innerText() : "";
    record(
      "query_index_generation_displayed",
      /[0-9a-f]{12,}…/.test(generationText),
      generationText || "missing",
    );

    const capabilities = await page.$("[data-testid=candidate-capabilities]");
    const capabilityText = capabilities ? await capabilities.innerText() : "";
    record(
      "capability_not_available_listed",
      /CAPABILITY_NOT_AVAILABLE/.test(capabilityText),
      capabilityText.slice(0, 160).replace(/\n/g, " ") || "missing",
    );

    const initialCount = await page.locator(".v2-archive-list a").count();
    record("archive_entries_present", initialCount > 0, `${initialCount} archive entries`);

    await page.fill(".v2-archive-search input", "S-004");
    await page.waitForFunction(
      (initial) => document.querySelectorAll(".v2-archive-list a").length < initial,
      initialCount,
      { timeout: 10000 },
    ).catch(() => {});
    const filteredCount = await page.locator(".v2-archive-list a").count();
    const filteredLabels = await page.locator(".v2-archive-list a span").allInnerTexts();
    record(
      "browser_query_filters_results",
      filteredCount > 0 && filteredCount < initialCount && filteredLabels.every((t) => /議事日程/.test(t)),
      `${initialCount} -> ${filteredCount} results for query S-004`,
    );
    await shot(page, "after-query");

    const firstHref = await page.locator(".v2-archive-list a").first().getAttribute("href");
    const firstTarget = await page.locator(".v2-archive-list a").first().getAttribute("target");
    record(
      "source_link_opens_official_https",
      /^https:\/\//.test(firstHref || "") && firstTarget === "_blank",
      `${firstHref} target=${firstTarget}`,
    );

    const healthToggle = await page.$(".v2-source-health > summary");
    if (healthToggle) await healthToggle.click();
    const healthText = (await page.locator(".v2-source-health").innerText()).replace(/\n/g, " ");
    record(
      "freshness_and_gaps_visible",
      /PASS|STALE|FAILED/.test(healthText) && /缺口|最後檢查/.test(healthText),
      healthText.slice(0, 160),
    );
    await shot(page, "source-health");

    await page.fill(".v2-archive-search input", "zzzqqq-no-such-record");
    await page.waitForSelector(".v2-archive-list p", { timeout: 10000 }).catch(() => {});
    const emptyText = await page.locator(".v2-archive-list").innerText();
    record(
      "valid_empty_distinct_from_error",
      /找不到符合關鍵字的歷史資料/.test(emptyText) && !/無法安全呈現|錯誤/.test(emptyText),
      emptyText.slice(0, 120).replace(/\n/g, " "),
    );
    await shot(page, "empty-query");

    await page.fill(".v2-archive-search input", "");
    await page.reload({ waitUntil: "networkidle" });
    const provenanceAfter = await page.waitForSelector("[data-testid=candidate-version]", { timeout: 15000 });
    const generationAfter = await provenanceAfter.$eval(
      "[data-testid=query-generation]",
      (node) => node.innerText,
    ).catch(() => "");
    record(
      "refresh_preserves_generation",
      generationAfter === generationText,
      `before=${generationText} after=${generationAfter}`,
    );

    const statusResponse = await page.request.get(`${BASE}/api/version`);
    const versionDoc = statusResponse.ok() ? await statusResponse.json() : null;
    const httpGeneration = typeof versionDoc?.generation_id === "string" ? versionDoc.generation_id : "";
    record(
      "http_version_matches_page",
      Boolean(httpGeneration && generationText.startsWith(httpGeneration.slice(0, 16))),
      httpGeneration ? `http generation ${httpGeneration.slice(0, 16)}…` : "no version doc",
    );

    const mixedBadgeCheck = badgeText.includes("拒絕混版");
    record("page_not_mixed_generation", !mixedBadgeCheck, badgeText || "badge missing");
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
const result = {
  status: failed.length === 0 && checks.length > 0 ? "PASS" : "FAIL",
  passed: checks.length - failed.length,
  total: checks.length,
  checks,
  screenshots,
};
console.log(JSON.stringify(result));
process.exit(result.status === "PASS" ? 0 : 1);
