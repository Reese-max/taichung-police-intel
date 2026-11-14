import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { buildHomeFeed, renderHomeFeed } from "../scripts/sync-home-feed.mjs";

const scriptPath = fileURLToPath(new URL("../scripts/sync-home-feed.mjs", import.meta.url));
const fullFeedPath = new URL("../public/data/intelligence-feed.json", import.meta.url);
const homeFeedPath = new URL("../public/data/intelligence-feed-home.json", import.meta.url);

function assertSortedObjectKeys(value) {
  if (Array.isArray(value)) {
    value.forEach(assertSortedObjectKeys);
    return;
  }
  if (!value || typeof value !== "object") return;
  const keys = Object.keys(value);
  assert.deepEqual(keys, [...keys].sort());
  Object.values(value).forEach(assertSortedObjectKeys);
}

test("checked-in home feed is the deterministic projection of the complete feed", async () => {
  const [feedBytes, checkedInBytes] = await Promise.all([
    readFile(fullFeedPath),
    readFile(homeFeedPath),
  ]);
  const feed = JSON.parse(feedBytes.toString("utf8"));
  const expected = buildHomeFeed(feed, feedBytes);
  const checkedIn = JSON.parse(checkedInBytes.toString("utf8"));

  assert.deepEqual(checkedIn, expected);
  assertSortedObjectKeys(checkedIn);
  assert.equal(checkedIn.feed_sha256, createHash("sha256").update(feedBytes).digest("hex"));
  assert.equal(checkedIn.total_item_count, feed.items.length);
  assert.deepEqual(checkedIn.items, feed.items.filter((item) => item.eligibility === "HOME_CANDIDATE"));
  assert.ok(checkedIn.items.every((item) => item.eligibility === "HOME_CANDIDATE"));
  assert.deepEqual(checkedIn.archive_preview, feed.items.slice(0, 8));
  assert.ok(checkedIn.archive_preview.length <= 8);
  assert.equal(checkedIn.collection_run_id, feed.collection_run_id);
  assert.equal(checkedIn.generated_at, feed.generated_at);
  assert.deepEqual(checkedIn.source_summary, feed.source_summary);
  if (Object.hasOwn(feed, "source_policy")) {
    assert.deepEqual(checkedIn.source_policy, feed.source_policy);
  }
  assert.equal(checkedInBytes.toString("utf8"), `${JSON.stringify(expected, null, 2)}\n`);
  assert.ok(!checkedInBytes.includes(0x0d), "generated JSON must use LF line endings");
});

test("sync script is idempotent when run repeatedly", async () => {
  execFileSync(process.execPath, [scriptPath], { encoding: "utf8" });
  const first = await readFile(homeFeedPath);
  const secondOutput = execFileSync(process.execPath, [scriptPath], { encoding: "utf8" });
  const second = await readFile(homeFeedPath);

  assert.deepEqual(second, first);
  assert.match(secondOutput, /changed=false/);
});

test("large complete feeds produce a much smaller home projection", () => {
  const feed = {
    schema_version: 1,
    collection_run_id: "large-run",
    generated_at: "2026-11-15T00:00:00Z",
    source_summary: { "S-009": { item_count: 5000 } },
    items: Array.from({ length: 5000 }, (_, index) => ({
      stable_id: `proposal-${index}`,
      eligibility: [99, 2500, 4999].includes(index) ? "HOME_CANDIDATE" : "INELIGIBLE",
      title: `議會提案 ${index}`,
      committee: "大型合成測試",
      details: "測試資料".repeat(300),
    })),
  };
  const feedBytes = Buffer.from(JSON.stringify(feed), "utf8");
  const homeBytes = Buffer.from(renderHomeFeed(feed, feedBytes), "utf8");
  const homeFeed = JSON.parse(homeBytes.toString("utf8"));

  assert.equal(homeFeed.items.length, 3);
  assert.ok(homeBytes.length < feedBytes.length / 10,
    `Expected home output (${homeBytes.length}) to be <10% of feed (${feedBytes.length})`);
});

test("homepage reads the slim feed while retaining complete-feed export links", async () => {
  const [page, packageJson] = await Promise.all([
    readFile(new URL("../app/page.js", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);
  const pkg = JSON.parse(packageJson.toString("utf8"));

  assert.match(page, /FEED_URL = `\$\{BASE_PATH\}\/data\/intelligence-feed-home\.json`/);
  assert.match(page, /item\.eligibility === "HOME_CANDIDATE"/);
  assert.match(page, /feedMeta\?\.source_summary/);
  assert.match(page, /href=\{`\$\{BASE_PATH\}\/data\/intelligence-feed\.json`\}/);
  assert.match(page, /href=\{`\$\{BASE_PATH\}\/data\/feed-export\.csv`\}/);
  assert.match(pkg.scripts.predev, /node scripts\/sync-home-feed\.mjs$/);
  assert.match(pkg.scripts.prebuild, /node scripts\/sync-home-feed\.mjs$/);
});
