import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const defaultFeedPath = resolve(webRoot, "public/data/intelligence-feed.json");
const defaultOutputPath = resolve(webRoot, "public/data/intelligence-feed-home.json");
// Written next to the home feed. Only what the archive list renders and searches.
const SEARCH_INDEX_NAME = "intelligence-feed-search.json";
const SEARCH_INDEX_FIELDS = ["stable_id", "title", "committee", "source_id", "official_url"];

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function parseArguments(args) {
  const paths = { feedPath: defaultFeedPath, outputPath: defaultOutputPath };
  for (let index = 0; index < args.length; index += 1) {
    const option = args[index];
    const value = args[index + 1];
    if (!["--feed", "--output"].includes(option) || !value || value.startsWith("--")) {
      throw new Error(`usage: sync-home-feed.mjs [--feed <path>] [--output <path>]`);
    }
    paths[option === "--feed" ? "feedPath" : "outputPath"] = resolve(value);
    index += 1;
  }
  return paths;
}

function sortJsonKeys(value) {
  if (Array.isArray(value)) return value.map(sortJsonKeys);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, sortJsonKeys(value[key])]),
  );
}

export function buildHomeFeed(feed, feedBytes) {
  if (
    feed?.schema_version !== 1
    || typeof feed.collection_run_id !== "string"
    || !feed.collection_run_id.trim()
    || typeof feed.generated_at !== "string"
    || !feed.generated_at.trim()
    || !isRecord(feed.source_summary)
    || !Array.isArray(feed.items)
    || feed.items.some((item) => !isRecord(item))
  ) {
    throw new Error("invalid intelligence feed");
  }
  if (!Buffer.isBuffer(feedBytes) && !(feedBytes instanceof Uint8Array)) {
    throw new TypeError("feedBytes must contain the original feed bytes");
  }

  const projection = {
    schema_version: 1,
    kind: "GOVINTEL_HOME_FEED",
    collection_run_id: feed.collection_run_id,
    generated_at: feed.generated_at,
    source_summary: feed.source_summary,
    feed_sha256: createHash("sha256").update(feedBytes).digest("hex"),
    total_item_count: feed.items.length,
    items: feed.items.filter((item) => item.eligibility === "HOME_CANDIDATE"),
    archive_preview: feed.items.slice(0, 8),
  };
  if (Object.hasOwn(feed, "source_policy")) {
    projection.source_policy = feed.source_policy;
  }
  return sortJsonKeys(projection);
}

export function renderHomeFeed(feed, feedBytes) {
  return `${JSON.stringify(buildHomeFeed(feed, feedBytes), null, 2)}\n`;
}

export function buildSearchIndex(feed, feedBytes) {
  // Same input contract and generation binding as the home feed.
  const home = buildHomeFeed(feed, feedBytes);
  return {
    schema_version: 1,
    kind: "GOVINTEL_ARCHIVE_SEARCH_INDEX",
    collection_run_id: home.collection_run_id,
    feed_sha256: home.feed_sha256,
    fields: SEARCH_INDEX_FIELDS,
    items: feed.items.map((item) => Object.fromEntries(
      SEARCH_INDEX_FIELDS.filter((field) => item[field] != null).map((field) => [field, item[field]]),
    )),
  };
}

export function renderSearchIndex(feed, feedBytes) {
  // Compact: this file is fetched whole on the first archive search.
  return `${JSON.stringify(buildSearchIndex(feed, feedBytes))}\n`;
}

async function writeIfChanged(path, rendered) {
  let current;
  try {
    current = await readFile(path, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  if (current === rendered) return false;
  await writeFile(path, rendered, "utf8");
  return true;
}

async function syncHomeFeed(feedPath = defaultFeedPath, outputPath = defaultOutputPath) {
  const feedBytes = await readFile(feedPath);
  const feed = JSON.parse(feedBytes.toString("utf8"));
  // Render both before writing either, so invalid input leaves no partial output.
  const rendered = renderHomeFeed(feed, feedBytes);
  const searchIndex = renderSearchIndex(feed, feedBytes);
  const searchPath = resolve(dirname(outputPath), SEARCH_INDEX_NAME);
  const changed = await writeIfChanged(outputPath, rendered);
  const searchChanged = await writeIfChanged(searchPath, searchIndex);
  console.log(
    `HOME_FEED_SYNC_OK items=${feed.items.filter((item) => item.eligibility === "HOME_CANDIDATE").length} ` +
    `total=${feed.items.length} output=${outputPath} changed=${changed} ` +
    `search_index=${searchPath} search_changed=${searchChanged}`,
  );
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const { feedPath, outputPath } = parseArguments(process.argv.slice(2));
  await syncHomeFeed(feedPath, outputPath);
}
