import { createHash } from "node:crypto";
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";

const webRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const feedPath = resolve(webRoot, "public/data/intelligence-feed.json");
const outputPath = resolve(webRoot, "public/data/intelligence-feed-home.json");

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
  if (feed?.schema_version !== 1 || !Array.isArray(feed.items)) {
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

async function syncHomeFeed() {
  const feedBytes = await readFile(feedPath);
  const feed = JSON.parse(feedBytes.toString("utf8"));
  const rendered = renderHomeFeed(feed, feedBytes);
  let current;
  try {
    current = await readFile(outputPath, "utf8");
  } catch (error) {
    if (error.code !== "ENOENT") throw error;
  }
  const unchanged = current === rendered;
  if (!unchanged) {
    await writeFile(outputPath, rendered, "utf8");
  }
  console.log(
    `HOME_FEED_SYNC_OK items=${feed.items.filter((item) => item.eligibility === "HOME_CANDIDATE").length} ` +
    `total=${feed.items.length} output=${outputPath} changed=${!unchanged}`,
  );
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await syncHomeFeed();
}
