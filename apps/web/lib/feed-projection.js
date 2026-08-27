// apps/web/lib/feed-projection.js
// Transforms intelligence-feed.json items into homepage-eligible candidates
// compatible with buildHomepageResponse() from homepage-eligibility.js.
//
// This module bridges the Python collector output (intelligence-feed.json)
// and the existing homepage eligibility pipeline.

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEFAULT_FEED_PATH = resolve(__dirname, "../public/data/intelligence-feed.json");

/**
 * Loads the intelligence feed from the specified path.
 * Returns null if the file does not exist or is unreadable.
 * @param {string} [feedPath]
 * @returns {Promise<object|null>}
 */
export async function loadFeed(feedPath = DEFAULT_FEED_PATH) {
  try {
    const raw = await readFile(feedPath, "utf-8");
    const feed = JSON.parse(raw);
    if (feed.schema_version !== 1 || !Array.isArray(feed.items)) {
      return null;
    }
    return feed;
  } catch {
    return null;
  }
}

/**
 * Maps feed items to homepage candidate shape expected by buildHomepageResponse().
 * Only items with eligibility === "HOME_CANDIDATE" are projected.
 * @param {object} feed - The parsed intelligence-feed.json
 * @returns {object[]} - Array of homepage candidates
 */
export function projectToHomepageCandidates(feed) {
  if (!feed || !Array.isArray(feed.items)) return [];

  return feed.items
    .filter((item) => item.eligibility === "HOME_CANDIDATE")
    .map((item) => ({
      item_id: item.stable_id,
      stable_id: item.stable_id,
      verification_status: "AUTO_PASS",
      content_disposition: "HOME_CANDIDATE",
      evidence_ids: [item.stable_id],
      official_url: item.official_url,
      reason_codes: item.reason_codes || ["HIGH_VALUE"],
      item_value_score: item.item_value_score || 50,
      source_id: item.source_id,
      source_role: item.source_role,
      title: item.title,
      title_zh: item.title_zh || item.title,
      published_at: item.published_at,
      fetched_at: item.fetched_at,
      data_as_of: item.data_as_of,
      change_type: item.change_type,
      freshness_status: item.freshness_status,
      source_health: item.source_health,
      window_completeness: item.window_completeness,
      evidence_count: item.evidence_count || 1,
      next_milestone: item.next_milestone || null,
      content_sha256: item.content_sha256,
      committee: item.committee || "",
    }));
}

/**
 * Returns a summary of sources from the feed for the empty-state display.
 * @param {object} feed
 * @returns {object} - { total_items, source_summary }
 */
export function feedSourceSummary(feed) {
  if (!feed) return { total_items: 0, source_summary: {} };
  return {
    total_items: feed.items?.length || 0,
    source_summary: feed.source_summary || {},
  };
}
