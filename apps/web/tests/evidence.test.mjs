import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { findActiveIndex, formatTime, groupWordsBySegment, validateEvidence } from "../lib/evidence.js";
import { buildSourceStatus, nextUpdateAt } from "../lib/source-status.js";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const canary = JSON.parse(await readFile(resolve(projectRoot, "groq-asr-canary-2026-08-14.json"), "utf8"));

test("官方絕對時間可由片段時間戳重建", () => {
  assert.equal(formatTime(canary.source.clip_start_seconds + 168.3), "20:48");
  assert.equal(formatTime(canary.source.clip_start_seconds + 168.3, true), "20:48.30");
  assert.equal(findActiveIndex(canary.asr.segments, 168.32), 45);
});

test("1,036 個逐字時間戳全部歸入段落", () => {
  validateEvidence(canary);
  const groups = groupWordsBySegment(canary.asr.segments, canary.asr.words);
  assert.equal(groups.flat().length, 1036);
  assert.ok(groups[45].some((word) => String(word.word).trim()));
});

test("線上來源狀態保留缺口、LKG 與下一次臺北排程", () => {
  const now = new Date("2026-08-22T01:00:00Z");
  const status = buildSourceStatus({
    source_id: "S-006",
    name: "質詢順序表",
    source_run_id: "SR-NOW",
    source_health: "FAILED",
    window_completeness: "PARTIAL",
    data_as_of: "2026-08-20T00:00:00Z",
    lkg_source_run_id: "SR-OLD",
    lkg_completed_at: "2026-08-21T10:30:00Z",
    lkg_manifest_sha256: "a".repeat(64),
  }, now);
  assert.deepEqual(status.intelligence_gaps, ["SOURCE_FAILED", "WINDOW_PARTIAL", "VERY_STALE_DATA"]);
  assert.equal(status.last_known_good.source_run_id, "SR-OLD");
  assert.equal(status.next_update_at, "2026-08-22T18:30:00.000+08:00");
  assert.equal(nextUpdateAt("2026-08-22T11:00:00Z"), "2026-08-23T06:30:00.000+08:00");
});
