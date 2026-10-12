#!/usr/bin/env node
// Local UI fault injection only. Served feed bytes retain their existing schema
// checks; this does not grant source permission or call a query/model provider.
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const args = process.argv.slice(2);
const option = (name, fallback) => {
  const index = args.indexOf(`--${name}`);
  return index < 0 ? fallback : args[index + 1];
};
const base = new URL(option("base-url", "http://127.0.0.1:4317/"));
if (!["localhost", "127.0.0.1", "[::1]"].includes(base.hostname)) throw new Error("This fault test requires a local server.");
const output = path.resolve(option("out", "output/playwright/history-fallback"));
await mkdir(output, { recursive: true });
const publicRoot = new URL("../apps/web/public/data/", import.meta.url);
const feedBytes = await readFile(new URL("intelligence-feed.json", publicRoot));
const briefBytes = await readFile(new URL("v2-daily-brief.json", publicRoot));
const feed = JSON.parse(feedBytes);
const incompatibleBrief = { ...JSON.parse(briefBytes), generator_version: -1 };
const keyword = "議事";
const expectedMatches = feed.items.filter(item => [item.title, item.committee, item.source_id]
  .filter(Boolean).join(" ").toLocaleLowerCase("zh-Hant").includes(keyword)).slice(0, 20).length;
if (expectedMatches < 1) throw new Error("The checked-in history fixture needs a real matching row for this test.");
const scenarios = [
  { id: "brief-503-feed-ok", brief: "503", feed: "ok", available: true },
  { id: "brief-incompatible-feed-ok", brief: "incompatible", feed: "ok", available: true },
  { id: "brief-and-feed-503", brief: "503", feed: "503", available: false },
  { id: "normal", brief: "ok", feed: "ok", available: true },
];
const viewports = [
  { id: "desktop", width: 1440, height: 1000 },
  { id: "phone", width: 390, height: 844 },
];
const checks = [], externalRequests = [], browserErrors = [], injections = [];
const browser = await chromium.launch({ headless: true,
  ...(process.env.GOVINTEL_CHROME_PATH ? { executablePath: process.env.GOVINTEL_CHROME_PATH } : { channel: "chrome" }) });
try {
  for (const scenario of scenarios) {
    for (const viewport of viewports) {
      const id = `${scenario.id}-${viewport.id}`;
      const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, serviceWorkers: "block" });
      const page = await context.newPage();
      page.on("pageerror", error => browserErrors.push({ id, message: error.message }));
      await context.route("**/*", async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin !== base.origin) {
          externalRequests.push({ id, url: request.url() });
          return route.abort();
        }
        if (url.pathname.endsWith("/data/v2-daily-brief.json")) {
          injections.push({ id, artifact: "brief", outcome: scenario.brief });
          if (scenario.brief === "503") return route.fulfill({ status: 503, body: "OFFLINE_BRIEF_FAULT" });
          return route.fulfill({ status: 200, contentType: "application/json", body: scenario.brief === "incompatible" ? JSON.stringify(incompatibleBrief) : briefBytes });
        }
        if (url.pathname.endsWith("/data/intelligence-feed.json")) {
          injections.push({ id, artifact: "feed", outcome: scenario.feed });
          return scenario.feed === "503" ? route.fulfill({ status: 503, body: "OFFLINE_FEED_FAULT" })
            : route.fulfill({ status: 200, contentType: "application/json", body: feedBytes });
        }
        if (url.pathname.endsWith("/query") && request.method() === "POST") {
          injections.push({ id, artifact: "query", outcome: "503_NO_PROVIDER_CALL" });
          return route.fulfill({ status: 503, json: { error: { code: "OFFLINE_UI_FAULT", message: "本機故障測試：查詢不可用。" } } });
        }
        return route.continue();
      });
      try {
        await page.goto(base.href, { waitUntil: "networkidle", timeout: 60000 });
        if (scenario.brief === "ok") await page.locator(".v2-metrics").waitFor();
        else await page.getByText("本期情報暫時無法安全呈現", { exact: true }).waitFor();
        await page.locator(".v2-query-form input").fill(keyword);
        await page.locator(".v2-query-form button").click();
        const fallback = page.locator(".v2-query-message.error a");
        await fallback.waitFor();
        await fallback.click();
        const section = page.locator(".v2-archive");
        const heading = page.locator("#v2-archive-title");
        const headingExists = await heading.count() === 1;
        const headingInViewport = headingExists && await heading.evaluate(element => {
          const rect = element.getBoundingClientRect();
          return rect.bottom > 0 && rect.top < innerHeight;
        });
        const targetReached = new URL(page.url()).hash === "#v2-archive-title" && headingInViewport;
        let matchCount = 0, historicalLabels = false, unavailable = false, falseNoData = false;
        if (await section.isVisible()) {
          if (scenario.available) {
            const search = section.locator(".v2-archive-search input");
            await search.fill(keyword);
            const rows = section.locator(".v2-archive-list a");
            matchCount = await rows.count();
            historicalLabels = matchCount > 0 && (await rows.allTextContents()).every(text => text.includes("歷史資料"));
          } else {
            unavailable = await section.locator(".v2-archive-unavailable").isVisible();
            matchCount = await section.locator(".v2-archive-list a").count();
            falseNoData = /0 筆(?:歷史)?索引|找不到符合關鍵字/.test(await section.innerText());
          }
        }
        const ok = targetReached && (scenario.available
          ? matchCount === expectedMatches && historicalLabels
          : unavailable && matchCount === 0 && !falseNoData);
        const screenshot = `${id}.png`;
        await page.screenshot({ path: path.join(output, screenshot), fullPage: false });
        checks.push({ id, status: ok ? "PASS" : "FAIL", expected_available: scenario.available,
          targetReached, headingExists, headingInViewport, matchCount, expectedMatches: scenario.available ? expectedMatches : null,
          historicalLabels, unavailable, falseNoData, screenshot });
      } catch (error) {
        checks.push({ id, status: "FAIL", error: error.message });
      } finally { await context.close(); }
    }
  }
} finally {
  await browser.close();
  const receipt = { kind: "LOCAL_HISTORY_FALLBACK_FAULT_TEST", source: fileURLToPath(publicRoot),
    production_verified: false, base_url: base.href, viewports, keyword, checks, injections, externalRequests, browserErrors,
    note: "Only local artifact fault injection and an unavailable query transport. No source admission, policy change, provider call, or tracking/feedback save action." };
  await writeFile(path.join(output, "receipt.json"), JSON.stringify(receipt, null, 2));
  const failures = checks.filter(check => check.status === "FAIL").length;
  console.log(JSON.stringify({ checks, failures, browserErrors, externalRequests, receipt: path.join(output, "receipt.json") }));
  process.exitCode = failures || browserErrors.length || externalRequests.length ? 1 : 0;
}
