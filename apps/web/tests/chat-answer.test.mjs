import assert from "node:assert/strict";
import test from "node:test";
import {
  CHAT_CONTEXT_KEYS,
  CHAT_QUICK_ACTIONS,
  summarizeChat,
  sanitizeChatContext,
} from "../lib/chat-answer.js";

test("chat quick actions cover the issue-29 entry points", () => {
  assert.ok(CHAT_QUICK_ACTIONS.length >= 6 && CHAT_QUICK_ACTIONS.length <= 12);
  const labels = CHAT_QUICK_ACTIONS.map((action) => action.label).join(" ");
  for (const required of ["今日重要事件", "異動", "官方證據", "統計", "沒有資料"]) {
    assert.ok(labels.includes(required), `missing quick action: ${required}`);
  }
  for (const action of CHAT_QUICK_ACTIONS) {
    assert.ok(typeof action.id === "string" && action.id.length <= 64);
    assert.ok(typeof action.text === "string" && action.text.length <= 512);
  }
});

test("sanitizeChatContext keeps only the minimal multi-turn keys", () => {
  const context = sanitizeChatContext({
    schema_version: 99,
    selected_event_id: "PE-1",
    selected_region: "location:tc-xitun",
    selected_agency: "agency:tc-police",
    selected_category: "traffic_control",
    selected_time_window: { time_from: "2026-09-26", time_to: "2026-09-27", time_zone: "Asia/Taipei" },
    last_result_event_ids: ["PE-1", "PE-2"],
    last_query_receipt: { tool_name: "search_events", arguments_sha256: "a".repeat(64) },
    transcript: "使用者私人對話不應保存",
    tool: "evil",
  });
  assert.equal(context.schema_version, 1);
  assert.equal(context.selected_event_id, "PE-1");
  assert.equal(context.selected_region, "location:tc-xitun");
  assert.deepEqual(context.last_result_event_ids, ["PE-1", "PE-2"]);
  assert.ok(!("transcript" in context));
  assert.ok(!("tool" in context));
  for (const key of Object.keys(context)) {
    assert.ok(CHAT_CONTEXT_KEYS.has(key) || key === "schema_version", `unexpected context key ${key}`);
  }
  assert.equal(sanitizeChatContext("nope"), null);
  assert.equal(sanitizeChatContext({ selected_event_id: 7 }), null);
});

test("summarizeChat keeps unverified and conflicts out of the verified section", () => {
  const summary = summarizeChat({
    schema_version: 1,
    intent: "search_events",
    status: "OK",
    resolved_request: { tool: "search_events", arguments: { district: "location:tc-xitun", time_from: "2026-09-26", time_to: "2026-09-27" } },
    lines: ["已驗證 1 件；待確認 1 件；來源衝突 1 件"],
    verified: [{ public_event_id: "PE-1", canonical_title: "管制", trust_tier: "VERIFIED" }],
    unverified: [{ public_event_id: "PE-2", canonical_title: "媒體訊號", trust_tier: "DISCOVERY_UNVERIFIED" }],
    conflicts: [{ public_event_id: "PE-3", canonical_title: "衝突", trust_tier: "CONFLICT" }],
    stale: [],
    evidence_links: [{ event_id: "PE-1", official_url: "https://police.example/e/PE-1", evidence_id: "EV-1" }],
    gaps: [{ source_id: "S-029", reason: "SOURCE_INCOMPLETE" }],
    freshness: "STALE",
    trust: { discovery_unverified_count: 1 },
    notices: [],
    context: { schema_version: 1, last_result_event_ids: ["PE-1"] },
  });
  const verified = summary.sections.find((section) => section.kind === "verified");
  const unverified = summary.sections.find((section) => section.kind === "unverified");
  const conflicts = summary.sections.find((section) => section.kind === "conflicts");
  assert.equal(verified.items.length, 1);
  assert.equal(unverified.items.length, 1);
  assert.equal(conflicts.items.length, 1);
  assert.equal(summary.freshness, "STALE");
  assert.equal(summary.gaps.length, 1);
  assert.equal(summary.evidenceLinks[0].official_url, "https://police.example/e/PE-1");
  assert.ok(summary.resolvedText.includes("location:tc-xitun"));
});
