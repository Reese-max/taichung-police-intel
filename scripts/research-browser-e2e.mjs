// Research-page runtime QA with synthetic OFFLINE responses. Never calls a model.
import { chromium } from "playwright-core";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createGovernedPolicyFixture } from "../apps/web/tests/governed-policy-fixture.mjs";
import { createDocumentResearchFixture, completion, supportedProducer, supportedCritic } from "../apps/web/tests/document-research-fixture.mjs";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const publicRoot = resolve(root, "apps/web/out");
const output = resolve(process.env.GOVINTEL_RESEARCH_BROWSER_OUTPUT || "/tmp/govintel-research-browser");
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };
const sourceStatus = JSON.parse(await readFile(resolve(root, "apps/web/public/data/source-status.json"), "utf8"));
const release = { release_id: "OFFLINE-RESEARCH-BROWSER", code_sha: execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim(), publication_generation: sourceStatus.latest_collection_run.collection_run_id };
const checks = [];
const server = createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const path = resolve(publicRoot, `.${pathname.endsWith("/") ? `${pathname}index.html` : pathname}`);
    if (path !== publicRoot && !path.startsWith(`${publicRoot}${sep}`)) throw new Error("path refused");
    const bytes = await readFile(path);
    response.writeHead(200, { "Content-Type": types[extname(path)] || "application/octet-stream" }); response.end(bytes);
  } catch { response.writeHead(404); response.end("not found"); }
});
await mkdir(output, { recursive: true });
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
const origin = `http://127.0.0.1:${server.address().port}`;
let browser, governedFixture;
const originalFetch = globalThis.fetch;
globalThis.fetch = async () => { throw new Error("Unexpected Node-side network request in offline research browser QA"); };
try {
  governedFixture = await createGovernedPolicyFixture();
  const module = await governedFixture.loadWorker("research-browser");
  const bytes = await governedFixture.publication();
  const snapshot = await module.buildSnapshot({ PUBLIC_ORIGIN: "https://offline.example.invalid" }, async name => ({ bytes: bytes[name], hash: createHash("sha256").update(bytes[name]).digest("hex") }));
  snapshot.release = release;
  const corpus = await createDocumentResearchFixture(snapshot, { texts: [
    "FICTIONAL OFFLINE DOCUMENT. 交通公告記載的開始時間為 16:00。",
    "FICTIONAL OFFLINE DOCUMENT. 交通改善預算列示 120 萬元。",
  ] });
  let offlineProviderRequests = 0;
  async function documentResponse(input, conflict = false) {
    let stage = 0;
    return module.executeResearch(snapshot, input, corpus.env, new Request(`${origin}/research`, { method: "POST" }), async (_url, options) => {
      offlineProviderRequests++;
      const payload = JSON.parse(JSON.parse(options.body).messages[1].content);
      const output = stage++ === 0 ? supportedProducer(payload) : supportedCritic(payload);
      if (conflict && output.reviews) output.reviews.forEach(row => { row.verdict = "CONFLICT"; });
      return completion(output);
    });
  }
  browser = await chromium.launch({ ...(process.env.GOVINTEL_CHROME_PATH ? { executablePath: process.env.GOVINTEL_CHROME_PATH } : {}), headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = []; const calls = []; let mode = "blocked"; let requestObserved;
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.origin !== origin) throw new Error(`Unexpected external request: ${url.origin}`);
    if (url.pathname === "/data/release.json") return route.fulfill({ json: release });
    if (url.pathname === "/research" && req.method() === "POST") {
      const input = req.postDataJSON(); calls.push(input); requestObserved?.();
      const currentMode = mode;
      if (["documents", "synthesis"].includes(input.mode) && ["document-fixture", "synthesis-fixture", "conflict-fixture"].includes(currentMode)) {
        return route.fulfill({ json: await documentResponse(input, currentMode === "conflict-fixture") });
      }
      if (currentMode === "slow") await new Promise(resolve => setTimeout(resolve, 1000));
      if (currentMode === "error") return route.fulfill({ status: 503, json: { error: { code: "PROVIDER_UNAVAILABLE", message: "SYNTHETIC_UPSTREAM_PRIVATE_MARKER" } } });
      const payload = { schema_version: 1, status: "METADATA_ONLY", reason_code: currentMode === "metadata" ? "DISABLED" : "RIGHTS_BLOCKED", provider: { name: "MiniMax", state: "DISABLED" }, question: input.question, answer: [],
        sources: currentMode === "metadata" ? [{ evidence_id: "OFFLINE-FICTIONAL-1", source_id: "OFFLINE-SOURCE", title: "離線測試：交通管制公告索引（非真實公告）", official_url: "https://example.test/fictional", published_at: "2026-10-01T01:00:00Z", data_as_of: null, fetched_at: "2026-10-05T02:00:00Z" }] : [],
        source_gaps: [{ source_id: null, reason: "STALE_SNAPSHOT" }], query_generation_id: "offline-fixture-generation", data_status: "STALE", coverage_limitation: "離線測試資料。僅提供索引，不代表現實事件或最新狀態。", release };
      return route.fulfill({ json: payload }).catch(() => {});
    }
    // Keep the QA completely offline, including unexpected third-party assets.
    return route.continue();
  });
  await page.goto(`${origin}/ask/`);
  await page.waitForURL(url => url.origin === origin && url.pathname.replace(/\/$/, "") === "/research");
  assert.equal(await page.getByRole("navigation", { name: "主要導覽" }).getByRole("link", { name: "公開研究", exact: true }).count(), 1);
  assert.equal(await page.getByRole("link", { name: "Ask GovIntel", exact: true }).count(), 0);
  checks.push("legacy /ask alias replaces navigation into the single /research entry");
  await page.getByRole("heading", { name: "帶著問題，回到官方來源" }).waitFor();
  const checkbox = page.getByRole("checkbox", { name: "僅輸入公開且不含個資的問題" });
  const submit = page.getByRole("button", { name: /送出研究問題/ });
  assert.equal(await submit.isEnabled(), false);
  await page.getByLabel("輸入研究問題", { exact: true }).fill("交通管制");
  assert.equal(await submit.isEnabled(), false); await checkbox.check(); await submit.click();
  await page.getByText(/來源的模型使用權利尚未核准/).waitFor();
  assert.equal(calls.length, 1); assert.equal(calls[0].public_data_only, true); assert.equal(await page.locator(".research-answer").count(), 0);
  checks.push("public-data consent gate and blocked-rights no-answer state");
  mode = "metadata"; await page.getByLabel("繼續追問", { exact: true }).fill("公告時間"); await checkbox.check(); await submit.click();
  await page.getByRole("link", { name: /離線測試：交通管制公告索引/ }).waitFor();
  assert.deepEqual(calls[1].history, [{ role: "user", content: "交通管制" }]);
  assert.equal(await page.locator(".research-dates").last().getByText("未提供", { exact: true }).count(), 1);
  checks.push("follow-up user context, source citation, separate dates and gaps");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: resolve(output, "research-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await page.screenshot({ path: resolve(output, "research-mobile.png"), fullPage: true });
  checks.push("desktop and 390px mobile rendering without horizontal overflow");
  mode = "slow"; await page.getByLabel("繼續追問", { exact: true }).fill("測試取消"); await checkbox.check();
  const inFlight = new Promise(resolve => { requestObserved = resolve; });
  await page.locator(".research-composer").evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
  await page.getByRole("button", { name: "停止等待", exact: true }).waitFor();
  await page.waitForFunction(() => document.querySelector(".research-pending"));
  // Wait until transport actually started so cancellation exercises an in-flight request.
  let startTimer;
  try { await Promise.race([inFlight, new Promise((_, reject) => { startTimer = setTimeout(() => reject(new Error("Research transport did not start within 10 seconds")), 10000); })]); }
  finally { clearTimeout(startTimer); requestObserved = null; }
  assert.equal(calls.length, 3); await page.getByRole("button", { name: "停止等待", exact: true }).click();
  await page.getByText(/已停止等待，未顯示本次回答/).waitFor();
  await page.getByRole("button", { name: "開始新對話", exact: true }).click();
  await page.getByRole("heading", { name: "你想查找哪類公開資料？" }).waitFor();
  await page.waitForTimeout(1200); assert.equal(await page.locator(".research-turn").count(), 0); assert.equal(await checkbox.isChecked(), false);
  checks.push("duplicate-submit guard, cancellation, reset and late-response suppression");
  mode = "error"; await page.getByLabel("輸入研究問題", { exact: true }).fill("新的問題"); await checkbox.check(); await submit.click();
  await page.locator(".research-error").waitFor();
  assert.deepEqual(calls.at(-1).history, []); assert.equal((await page.locator("body").innerText()).includes("SYNTHETIC_UPSTREAM_PRIVATE_MARKER"), false);
  checks.push("new conversation clears history and server errors do not reflect raw text");
  await page.getByRole("link", { name: "查看來源狀態 ↗" }).click(); await page.waitForURL("**/sources/");
  await page.goBack(); await page.getByRole("heading", { name: "你想查找哪類公開資料？" }).waitFor();
  assert.equal(await page.locator(".research-turn").count(), 0); assert.deepEqual(errors, []);
  checks.push("navigation back does not resurrect conversation; zero page errors");
  mode = "document-fixture";
  await page.getByLabel("研究模式", { exact: true }).selectOption("documents");
  await page.getByLabel("輸入研究問題", { exact: true }).fill("交通"); await checkbox.check(); await submit.click();
  await page.locator(".research-document-evidence").waitFor();
  assert.equal(offlineProviderRequests, 0);
  assert.equal(await page.locator(".research-extract").count(), 2);
  assert.equal(await page.locator(".research-synthesis").count(), 0);
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: resolve(output, "research-documents-mobile.png"), fullPage: true });
  checks.push("actual document route renders exact permission-pinned excerpts with zero provider requests");
  await page.getByRole("button", { name: "開始新對話", exact: true }).click();
  mode = "synthesis-fixture";
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.getByLabel("研究模式", { exact: true }).selectOption("synthesis");
  await page.getByLabel("輸入研究問題", { exact: true }).fill("交通"); await checkbox.check(); await submit.click();
  await page.getByRole("heading", { name: "模型研究草稿 · 待人工核對" }).waitFor();
  assert.equal(offlineProviderRequests, 2);
  await page.getByText("引用已核對，模型語意仍需人工核對", { exact: true }).waitFor();
  assert.equal(await page.locator(".research-answer").count(), 0);
  assert.ok(await page.locator(".research-claim-citations").count());
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: resolve(output, "research-synthesis-desktop.png"), fullPage: true });
  checks.push("actual two-stage synthesis adapter renders only a qualified research draft with bound exact citations");
  await page.getByRole("button", { name: "開始新對話", exact: true }).click();
  mode = "conflict-fixture";
  await page.getByLabel("研究模式", { exact: true }).selectOption("synthesis");
  await page.getByLabel("輸入研究問題", { exact: true }).fill("交通"); await checkbox.check(); await submit.click();
  await page.locator(".research-document-evidence").waitFor();
  assert.equal(offlineProviderRequests, 4);
  assert.equal(await page.locator(".research-synthesis").count(), 0);
  assert.equal(await page.locator(".research-answer").count(), 0);
  assert.deepEqual(errors, []);
  checks.push("critic conflict withholds model prose and preserves only exact excerpts");
  await writeFile(resolve(output, "receipt.json"), JSON.stringify({ status: "PASS", mode: "OFFLINE_BROWSER_MOCKS", code_sha: release.code_sha, live_provider_calls: 0, offline_mock_provider_requests: offlineProviderRequests, checks }, null, 2));
  console.log("RESEARCH_BROWSER_OFFLINE_PASS");
} catch (error) {
  await writeFile(resolve(output, "receipt.json"), JSON.stringify({ status: "FAIL", mode: "OFFLINE_BROWSER_MOCKS", code_sha: release.code_sha, live_provider_calls: 0, checks, error: String(error) }, null, 2));
  throw error;
} finally { await browser?.close(); await governedFixture?.cleanup(); globalThis.fetch = originalFetch; await new Promise(resolve => server.close(resolve)); }
