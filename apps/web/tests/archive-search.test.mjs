import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  getArchiveSearchView,
  searchArchive,
  shouldLoadFullArchive,
} from "../lib/archive-search.js";

const preview = Array.from({ length: 8 }, (_, index) => ({
  stable_id: `preview-${index}`,
  title: `預覽項目 ${index}`,
}));

const homeFeed = {
  collection_run_id: "run-1",
  archive_preview: preview,
};

test("archive search returns the compact preview when query is empty", () => {
  assert.deepEqual(
    searchArchive({ homeFeed, fullFeed: null, query: "   " }),
    { kind: "preview", items: preview },
  );
});

test("archive search requests the full index only for a non-empty query", () => {
  assert.deepEqual(searchArchive({ homeFeed, fullFeed: null, query: "交通" }), {
    kind: "needs_full_index",
    items: [],
  });
});

test("archive search refuses a full index from another collection generation", () => {
  const result = searchArchive({
    homeFeed,
    fullFeed: { collection_run_id: "run-2", items: [{ title: "交通" }] },
    query: "交通",
  });
  assert.equal(result.kind, "generation_mismatch");
  assert.deepEqual(result.items, []);
});

test("archive search hides previews while the full index is loading", () => {
  const view = getArchiveSearchView({
    homeFeed,
    fullFeed: null,
    query: "交通",
    loadState: "loading",
  });

  assert.deepEqual(view.items, []);
  assert.equal(view.statusMessage, "正在載入完整歷史索引…");
  assert.equal(view.showNoResults, false);
});

test("archive search failure never reports zero matching records", () => {
  const view = getArchiveSearchView({
    homeFeed,
    fullFeed: null,
    query: "交通",
    loadState: "failed",
  });

  assert.deepEqual(view.items, []);
  assert.equal(view.statusMessage, "完整歷史索引載入失敗，請稍後再試。");
  assert.equal(view.showNoResults, false);
});

test("archive search reports no matches only after a successful empty search", () => {
  const view = getArchiveSearchView({
    homeFeed,
    fullFeed: { collection_run_id: "run-1", items: [{ title: "完全不同" }] },
    query: "交通",
    loadState: "loaded",
  });

  assert.deepEqual(view.items, []);
  assert.equal(view.statusMessage, null);
  assert.equal(view.showNoResults, true);
});

test("archive search allows one retry after the query changes", () => {
  const request = {
    homeFeed,
    fullFeed: null,
    loadState: "failed",
    query: "交通",
    queryRevision: 2,
    lastRequestedRevision: 1,
  };

  assert.equal(shouldLoadFullArchive(request), true);
  assert.equal(shouldLoadFullArchive({ ...request, queryRevision: 1 }), false);
});

test("archive search starts the first download regardless of revision bookkeeping", () => {
  const request = { homeFeed, fullFeed: null, loadState: "idle", query: "交通" };

  assert.equal(shouldLoadFullArchive(request), true);
  assert.equal(shouldLoadFullArchive({ ...request, queryRevision: 3, lastRequestedRevision: 3 }), true);
  assert.equal(shouldLoadFullArchive({ ...request, loadState: "loading", queryRevision: 4, lastRequestedRevision: 3 }), false);
  assert.equal(shouldLoadFullArchive({ ...request, query: "   " }), false);
  assert.equal(shouldLoadFullArchive({ ...request, fullFeed: { items: [] } }), false);
});

test("archive search does not repeat a failed request for an unchanged query", () => {
  assert.equal(shouldLoadFullArchive({
    homeFeed,
    fullFeed: null,
    loadState: "failed",
    query: "交通",
    queryRevision: 4,
    lastRequestedRevision: 4,
  }), false);
});

test("archive search rejects full indexes from another generation without results", () => {
  const view = getArchiveSearchView({
    homeFeed,
    fullFeed: { collection_run_id: "run-2", items: [{ title: "交通" }] },
    query: "交通",
    loadState: "loaded",
  });

  assert.equal(view.kind, "generation_mismatch");
  assert.deepEqual(view.items, []);
  assert.equal(view.showNoResults, false);
});

test("archive search distinguishes an invalid full index and returns no preview", () => {
  const result = searchArchive({
    homeFeed,
    fullFeed: { collection_run_id: "run-1", items: null },
    query: "交通",
  });

  assert.equal(result.kind, "invalid_full_index");
  assert.deepEqual(result.items, []);
});

test("archive search matches title, committee, and source id and caps at 20", () => {
  const items = Array.from({ length: 25 }, (_, index) => ({
    stable_id: `item-${index}`,
    title: index % 3 === 0 ? `交通標題 ${index}` : `一般標題 ${index}`,
    committee: index % 3 === 1 ? `交通委員會 ${index}` : "",
    source_id: index % 3 === 2 ? "S-交通" : "S-001",
  }));
  const result = searchArchive({
    homeFeed,
    fullFeed: { collection_run_id: "run-1", items },
    query: "交通",
  });

  assert.equal(result.kind, "results");
  assert.equal(result.items.length, 20);
  assert.deepEqual(result.items, items.filter((item) => (
    item.title.includes("交通")
      || item.committee.includes("交通")
      || item.source_id.includes("交通")
  )).slice(0, 20));
});

test("browser archive checks wait for results and target the distinct zero-results marker", async () => {
  const e2e = await readFile(new URL("../../../scripts/e2e-browser.mjs", import.meta.url), "utf8");
  const dashboard = await readFile(new URL("../components/V2DailyDashboard.js", import.meta.url), "utf8");

  assert.doesNotMatch(e2e, /waitForTimeout\(100\)/);
  assert.match(e2e, /archiveSearchState/);
  assert.match(e2e, /archive-no-results/);
  assert.doesNotMatch(e2e, /waitForSelector\("\.v2-archive-list p"/);
  assert.match(dashboard, /let cancelled = false;[\s\S]*?fullArchiveRequestCancelled\.current = \(\) => cancelled;[\s\S]*?cancelled = true;/);
  assert.match(dashboard, /const isCancelled = fullArchiveRequestCancelled\.current;[\s\S]*?if \(isCancelled\(\)\) return;/);
});
