#!/usr/bin/env node
/** Offline real-browser acceptance. Build apps/web before running. */
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { createServer } from "node:http";
import { mkdir, readFile, stat, writeFile } from "node:fs/promises";
import { dirname, extname, join, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const exported = join(root, "apps/web/out");
const out = join(root, "output/playwright/issue-13");
await mkdir(out, { recursive: true });
execFileSync("python", ["-X", "utf8", "scripts/live-meeting.py", "replay-recording", "--out", out], { cwd: root, stdio: "pipe" });
const live = JSON.parse(await readFile(join(out, "session-live.json"), "utf8"));
const receipts = JSON.parse(await readFile(join(out, "session-reconciled.json"), "utf8"));
await stat(join(exported, "live/index.html"));
const mime = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json" };
const server = createServer(async (request, response) => {
  try {
    let file = resolve(exported, `.${decodeURIComponent(new URL(request.url, "http://localhost").pathname)}`);
    if (!file.startsWith(exported + sep) && file !== exported) throw new Error("outside export");
    if ((await stat(file)).isDirectory()) file = join(file, "index.html");
    response.writeHead(200, { "Content-Type": mime[extname(file)] || "application/octet-stream" });
    response.end(await readFile(file));
  } catch { response.writeHead(404); response.end(); }
});
await new Promise((done) => server.listen(0, "127.0.0.1", done));
const base = `http://127.0.0.1:${server.address().port}`;
const checks = [];
let browser;
try {
  try { browser = await chromium.launch({ channel: "chrome", headless: true }); }
  catch { browser = await chromium.launch({ headless: true, ...(process.env.GOVINTEL_CHROME_PATH ? { executablePath: process.env.GOVINTEL_CHROME_PATH } : {}) }); }
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
  const errors = [];
  page.on("pageerror", (error) => errors.push(String(error)));
  // Every non-loopback request is intercepted. Only the known seek fixture
  // gets media bytes; this is never an official-origin availability canary.
  const audio = Buffer.alloc(44 + 16000 * 2 * 30);
  audio.write("RIFF", 0); audio.writeUInt32LE(audio.length - 8, 4); audio.write("WAVEfmt ", 8);
  audio.writeUInt32LE(16, 16); audio.writeUInt16LE(1, 20); audio.writeUInt16LE(1, 22);
  audio.writeUInt32LE(16000, 24); audio.writeUInt32LE(32000, 28); audio.writeUInt16LE(2, 32); audio.writeUInt16LE(16, 34);
  audio.write("data", 36); audio.writeUInt32LE(audio.length - 44, 40);
  await page.route("**/*", async (route) => {
    const url = route.request().url();
    if (url.startsWith(base + "/")) return route.continue();
    if (url === "https://vod.tccc.gov.tw/owned-browser-unseekable.wav") return route.fulfill({ status: 200, contentType: "audio/wav", body: audio });
    if (url === "https://vod.tccc.gov.tw/owned-browser-fixture.wav") {
      const start = Number(route.request().headers().range?.match(/^bytes=(\d+)-/)?.[1] || 0);
      return route.fulfill({ status: 206, headers: {
        "Content-Type": "audio/wav", "Accept-Ranges": "bytes",
        "Content-Range": `bytes ${start}-${audio.length - 1}/${audio.length}`,
      }, body: audio.subarray(start) });
    }
    return route.abort();
  });
  const upload = async (value) => {
    await page.locator('input[type="file"]').setInputFiles({ name: "session.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(value)) });
    await page.waitForFunction(({ status, text }) => document.querySelector("main").textContent.includes(status)
      && document.querySelector('[data-kind="SEGMENT"] p')?.textContent === text,
    { status: value.status, text: value.segments[0].text });
  };
  await page.goto(base + "/live/", { waitUntil: "networkidle" });
  assert.match(await page.locator("h1").innerText(), /公開會議即時導航/);
  assert.equal(await page.locator("video").count(), 0);
  checks.push("explicit_local_import_no_autostart");
  await upload(live);
  assert.equal(await page.locator('[data-kind="SEGMENT"]').count(), 55);
  assert.equal(await page.locator('[data-kind="GAP"]').count(), 3);
  assert.match(await page.locator("main").innerText(), /LIVE \/ AI 轉錄 \/ 未核對/);
  checks.push("rolling_segments_and_visible_150_second_gaps");
  await page.getByRole("searchbox").fill("no-such-topic");
  assert.equal(await page.locator('[data-kind="SEGMENT"]').count(), 0);
  assert.equal(await page.locator('[data-kind="GAP"]').count(), 3);
  await page.getByRole("searchbox").fill("交通");
  assert.equal(await page.locator('[data-kind="SEGMENT"]').count(), 50);
  assert.match(await page.locator("main").innerText(), /前 50 段/);
  checks.push("search_keeps_gap_context_and_provisional_labels");
  await page.getByRole("button", { name: "加入 WATCH", exact: true }).first().click();
  const original = live.segments[0].text;
  const updated = structuredClone(live);
  const previous = structuredClone(updated.segments[0]);
  delete previous.revision_history;
  updated.segments[0].revision_history.push(previous);
  updated.segments[0].revision += 1;
  updated.segments[0].text = "更正後的交通暫定文字";
  await upload(updated);
  const add = page.getByRole("button", { name: "加入 WATCH", exact: true }).first();
  if (await add.isEnabled()) await add.click();
  const watchSection = page.locator("main > ul").first();
  assert.match(await watchSection.innerText(), new RegExp(original));
  assert.equal(await watchSection.locator("li").count(), 1);
  const pendingDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "下載 WATCH" }).click();
  const downloaded = JSON.parse(await readFile(await (await pendingDownload).path(), "utf8"));
  assert.equal(downloaded.watches[0].text, original);
  assert.equal(downloaded.watches[0].stream_content_hash, live.stream_content_hash);
  checks.push("watch_retains_original_revision_and_download");
  await page.reload({ waitUntil: "networkidle" });
  await upload(live);
  assert.match(await page.locator("main > ul").first().innerText(), new RegExp(original));
  checks.push("watch_survives_browser_reload");
  const broken = { ...live, provider_usage: { calls: null } };
  await page.locator('input[type="file"]').setInputFiles({ name: "invalid.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(broken)) });
  await page.locator('main [role="alert"]').waitFor();
  assert.equal(await page.locator('[data-kind="SEGMENT"]').count(), 55);
  checks.push("invalid_json_preserves_the_loaded_session");
  await upload(receipts);
  assert.match(await page.locator("main").innerText(), /SUPERSEDED_TRANSCRIPT/);
  assert.match(await page.locator("main").innerText(), /FIXTURE_ONLY/);
  assert.doesNotMatch(await page.locator("main").innerText(), /AUTO_PASS/);
  checks.push("post_event_receipts_stay_fixture_only");
  const seekable = { ...live, seek: { kind: "STREAM_TIME", base_url: "https://vod.tccc.gov.tw/owned-browser-fixture.wav", offset_seconds: 3 } };
  await upload(seekable);
  await page.getByRole("button", { name: "回看 stream+00:00:03", exact: true }).click();
  try {
    await page.waitForFunction(() => document.querySelector("video")?.currentTime >= 2.9, null, { timeout: 10000 });
  } catch (error) {
    console.error(await page.locator("video").evaluate((video) => ({
      currentTime: video.currentTime, duration: video.duration, readyState: video.readyState,
      networkState: video.networkState, src: video.currentSrc, error: video.error?.message,
      status: [...document.querySelectorAll('[role="status"]')].map((row) => row.textContent),
    })));
    throw error;
  }
  checks.push("verified_seek_moves_the_real_media_clock");
  await upload({ ...seekable, seek: { ...seekable.seek, base_url: "https://vod.tccc.gov.tw/owned-browser-unseekable.wav" } });
  await page.getByRole("button", { name: "回看 stream+00:00:03", exact: true }).click();
  await page.getByText(/無法定位.*請依時間回看/).waitFor({ timeout: 10000 });
  checks.push("unseekable_media_falls_back_to_time_text");
  await page.screenshot({ path: join(out, "desktop.png") });
  await page.setViewportSize({ width: 320, height: 850 });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await page.screenshot({ path: join(out, "narrow.png") });
  assert.deepEqual(errors, []);
  checks.push("narrow_layout_and_no_browser_errors");
  const receipt = { scope: "OFFLINE_BROWSER_FIXTURE_ONLY", passed: checks.length, failed: 0, checks };
  await writeFile(join(out, "browser-receipt.json"), JSON.stringify(receipt, null, 2) + "\n");
  console.log(JSON.stringify(receipt));
} finally {
  await browser?.close();
  await new Promise((done) => server.close(done));
}
