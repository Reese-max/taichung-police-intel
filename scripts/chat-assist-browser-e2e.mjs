// Ask GovIntel chat runtime QA with synthetic OFFLINE responses. Never calls a model:
// /query goes to the real Worker over the fictional governed fixture, /chat to the
// real assisted-chat handler with a mocked provider and admission.
import { chromium } from "playwright-core";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile, mkdir, writeFile, stat } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { createGovernedPolicyFixture } from "../apps/web/tests/governed-policy-fixture.mjs";
import { extname, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));
const publicRoot = resolve(root, "apps/web/out");
const output = resolve(process.env.GOVINTEL_CHAT_BROWSER_OUTPUT || "/tmp/govintel-chat-browser");
const types = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" };
const PROVIDER = "https://api.minimax.io/v1/chat/completions";
const WORKER_ORIGIN = "https://offline.example.invalid";
const sourceStatus = JSON.parse(await readFile(resolve(root, "apps/web/public/data/source-status.json"), "utf8"));
const codeSha = execFileSync("git", ["rev-parse", "HEAD"], { cwd: root, encoding: "utf8" }).trim();
const release = { code_sha: codeSha, publication_generation: sourceStatus.latest_collection_run.collection_run_id };
const hash = value => createHash("sha256").update(value).digest("hex");
const completion = value => Response.json({ choices: [{ finish_reason: "stop", message: { content: JSON.stringify(value) } }] });
const checks = [];
const server = createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://localhost").pathname);
    const path = resolve(publicRoot, `.${pathname.endsWith("/") ? `${pathname}index.html` : pathname}`);
    if (path !== publicRoot && !path.startsWith(`${publicRoot}${sep}`)) throw new Error("path refused");
    const servedPath = (await stat(path)).isDirectory() ? resolve(path, "index.html") : path;
    const bytes = await readFile(servedPath);
    response.writeHead(200, { "Content-Type": types[extname(servedPath)] || "application/octet-stream" }); response.end(bytes);
  } catch { response.writeHead(404); response.end("not found"); }
});
await mkdir(output, { recursive: true });
await new Promise((resolve, reject) => { server.once("error", reject); server.listen(0, "127.0.0.1", resolve); });
const origin = `http://127.0.0.1:${server.address().port}`;
let browser, governedFixture, page;
const originalFetch = globalThis.fetch;
let offlineProviderRequests = 0; let offlineAdmissions = 0;
try {
  governedFixture = await createGovernedPolicyFixture({ briefReviewed: true });
  const module = await governedFixture.loadWorker("chat-assist-browser");
  const bytes = await governedFixture.publication();
  const load = async name => ({ bytes: bytes[name], hash: hash(bytes[name]) });
  // The Worker's own snapshot answers /query; the deterministic path binds no release locally.
  const workerEnv = { PUBLIC_ORIGIN: WORKER_ORIGIN, ALLOWED_ORIGINS: origin, CF_VERSION_METADATA: { id: "chat-browser", tag: codeSha } };
  const workerRelease = await module.createReleaseManifest(await module.buildSnapshot(workerEnv, load), codeSha);
  // /chat always binds the release, so its snapshot carries the generation the page renders.
  const snapshot = await module.buildSnapshot({ PUBLIC_ORIGIN: WORKER_ORIGIN }, load);
  snapshot.generatedFrom = { ...snapshot.generatedFrom, collection_run_id: release.publication_generation };
  Object.assign(release, await module.createReleaseManifest(snapshot, codeSha));
  snapshot.release = release;
  globalThis.fetch = async url => {
    const name = String(url).startsWith(`${WORKER_ORIGIN}/`) ? String(url).split("/").at(-1) : null;
    if (name === "release.json") return Response.json(workerRelease);
    if (name && bytes[name]) return new Response(bytes[name]);
    throw new Error(`Unexpected Node-side network request in offline chat browser QA: ${url}`);
  };
  const ready = { RESEARCH_ENABLED: "true", MINIMAX_API_KEY: "OFFLINE_FAKE_KEY", MINIMAX_BILLING_REVIEWED: "true",
    RESEARCH_ADMISSION: { fetch: async () => { offlineAdmissions++; return Response.json({ allowed: true }); } } };
  let mode = "disabled"; let ip = 0; const calls = [];
  async function assisted(input) {
    const replies = mode === "llm" || mode === "tamper" ? [{ tool: "search_evidence", q: "測試" },
      payload => completion({ evidence_ids: payload.sources.slice(0, 1).map(row => row.evidence_id) })] : mode === "reject" ? [{ tool: "validate_answer" }] : [];
    const result = await module.executeAssistedChat(snapshot, input, mode === "disabled" ? {} : ready, new Request(`${origin}/chat`, { method: "POST" }), async (url, options) => {
      assert.equal(url, PROVIDER); offlineProviderRequests++;
      const reply = replies.shift(); assert.ok(reply, "unexpected provider call");
      return typeof reply === "function" ? reply(JSON.parse(JSON.parse(options.body).messages[1].content)) : completion(reply);
    });
    if (mode === "tamper" && result.chat.assisted_answer.answer.length) result.chat.assisted_answer.answer = ["官方來源已核對：publication:X:title=未經核對的文字。"];
    return result;
  }
  browser = await chromium.launch({ ...(process.env.GOVINTEL_CHROME_PATH ? { executablePath: process.env.GOVINTEL_CHROME_PATH } : {}), headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, locale: "zh-TW", timezoneId: "Asia/Taipei" });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/*", async route => {
    const req = route.request(); const url = new URL(req.url());
    if (url.origin !== origin) throw new Error(`Unexpected external request: ${url.origin}`);
    if (url.pathname === "/data/release.json") return route.fulfill({ json: release });
    if (req.method() === "POST" && ["/query", "/chat"].includes(url.pathname)) {
      const input = req.postDataJSON(); calls.push({ path: url.pathname, input });
      if (url.pathname === "/chat") {
        if (input.release_id !== release.release_id) return route.fulfill({ status: 503, json: { error: { code: "QUERY_TEMPORARILY_UNAVAILABLE", message: "release" } } });
        return route.fulfill({ json: await assisted(input) });
      }
      const response = await module.default.fetch(new Request(`${WORKER_ORIGIN}/query`, { method: "POST", body: JSON.stringify(input),
        headers: { "Content-Type": "application/json", Origin: origin, "CF-Connecting-IP": `chat-browser-${++ip}` } }), workerEnv);
      return route.fulfill({ status: response.status, json: await response.json() });
    }
    return route.continue();
  });
  await page.goto(`${origin}/public-query/`);
  const chat = page.locator(".pq-chat");
  await chat.getByRole("heading", { name: /Ask GovIntel/ }).waitFor();
  const question = chat.getByLabel("公開資料問題或追問", { exact: true });
  const submit = chat.getByRole("button", { name: "送出問題", exact: true });
  const assist = chat.getByRole("checkbox", { name: /由 AI 判斷要查哪一種資料/ });

  await question.fill("查官方證據 測試"); await submit.click();
  await chat.getByText("以下只列已核准公開快照的索引中繼資料，尚未驗證語意主張或現況。").waitFor();
  assert.equal(calls.at(-1).path, "/query"); assert.equal(calls.at(-1).input.public_data_only, undefined);
  assert.equal(await chat.getByText(/^解析方式：/).count(), 0);
  checks.push("AI switch off: the question goes to the deterministic /query chat_turn with no parser or AI answer shown");

  await assist.check();
  const attest = chat.getByRole("checkbox", { name: /我確認問題只含公開、不含個人的資訊/ });
  const before = calls.length;
  await question.fill("測試官方公告"); await submit.click();
  await chat.getByText(/PUBLIC_DATA_CONFIRMATION_REQUIRED/).waitFor();
  assert.equal(calls.length, before);
  checks.push("AI switch on without the public-data attestation sends nothing");

  await attest.check(); await submit.click();
  await chat.getByText(/^解析方式：規則（問題未送出給 AI：站方未啟用 AI 解析）/).waitFor();
  await chat.getByText("AI 回答：未產生（站方未啟用 AI 解析）").waitFor();
  assert.equal(calls.at(-1).path, "/chat"); assert.equal(calls.at(-1).input.public_data_only, true);
  assert.equal(offlineProviderRequests, 0);
  checks.push("disabled provider: /chat answers with the rules and says the question was not sent");

  mode = "llm"; await submit.click();
  await chat.getByRole("heading", { name: "AI 挑選並通過證據核對的回答", exact: true }).waitFor();
  await chat.getByText(/^解析方式：AI 解析（chat-intent-llm\/1）/).waitFor();
  assert.equal(await chat.locator('section[aria-label="AI 挑選並通過證據核對的回答"] li').count(), 1);
  assert.match(await chat.locator('section[aria-label="AI 挑選並通過證據核對的回答"] li').first().innerText(), /^官方來源已核對：publication:.+:title=/);
  assert.equal(offlineProviderRequests, 2); assert.equal(offlineAdmissions, 2);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: resolve(output, "chat-assist-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
  await chat.screenshot({ path: resolve(output, "chat-assist-mobile.png") });
  checks.push("mocked model: gate-checked answer rendered with the AI parser line; 390px without horizontal overflow");

  mode = "reject"; await submit.click();
  await chat.getByText(/^解析方式：規則（AI 解析未成功，問題可能已送出，改用規則：AI 輸出未通過核對）/).waitFor();
  assert.equal(await chat.locator('section[aria-label="AI 挑選並通過證據核對的回答"]').count(), 0);
  checks.push("rejected model output: rules fallback that says the question may have been sent");

  mode = "tamper"; await submit.click();
  await chat.getByText("對話回應或發布收據無法驗證，未顯示回答。", { exact: false }).waitFor();
  assert.equal(await chat.locator('section[aria-label="AI 挑選並通過證據核對的回答"]').count(), 0);
  assert.equal((await page.locator("body").innerText()).includes("未經核對的文字"), false);
  checks.push("an answer whose text no longer matches its receipt is refused, not rendered");

  assert.equal((await page.locator("body").innerText()).includes("OFFLINE_FAKE_KEY"), false);
  assert.deepEqual(errors, []);
  checks.push("zero page errors; no credential text on the page");
  await writeFile(resolve(output, "receipt.json"), JSON.stringify({ status: "PASS", mode: "OFFLINE_BROWSER_MOCKS", code_sha: codeSha, live_provider_calls: 0,
    offline_mock_provider_requests: offlineProviderRequests, offline_mock_admissions: offlineAdmissions, checks }, null, 2));
  console.log("CHAT_ASSIST_BROWSER_OFFLINE_PASS");
} catch (error) {
  await page?.screenshot({ path: resolve(output, "failure.png"), fullPage: true }).catch(() => {});
  if (page) await writeFile(resolve(output, "failure-context.json"), JSON.stringify({ url: page.url(), body: (await page.locator("body").innerText().catch(() => "")).slice(0, 12000) }, null, 2));
  await writeFile(resolve(output, "receipt.json"), JSON.stringify({ status: "FAIL", mode: "OFFLINE_BROWSER_MOCKS", code_sha: codeSha, live_provider_calls: 0, checks, error: String(error) }, null, 2));
  throw error;
} finally { await browser?.close(); await governedFixture?.cleanup(); globalThis.fetch = originalFetch; await new Promise(resolve => server.close(resolve)); }
