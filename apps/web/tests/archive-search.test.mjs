import assert from "node:assert/strict";
import test from "node:test";

import { searchArchive } from "../lib/archive-search.js";

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
  assert.deepEqual(
    searchArchive({ homeFeed, fullFeed: null, query: "交通" }),
    { kind: "needs_full_index", items: preview },
  );
});

test("archive search refuses a full index from another collection generation", () => {
  const result = searchArchive({
    homeFeed,
    fullFeed: { collection_run_id: "run-2", items: [{ title: "交通" }] },
    query: "交通",
  });
  assert.equal(result.kind, "generation_mismatch");
  assert.deepEqual(result.items, preview);
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
