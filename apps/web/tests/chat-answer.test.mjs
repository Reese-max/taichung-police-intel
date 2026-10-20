import assert from "node:assert/strict";
import test from "node:test";
import * as chatClient from "../lib/chat-answer.js";
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

function chatEnvelope(context = { schema_version: 1, selected_event_id: "PE-1" }) {
  const binding = { publication_hash: "a".repeat(64), query_generation_id: "generation-29" };
  return {
    schema_version: 1, query_id: "query-29", tool_name: "chat_turn", result_type: "conversation", ...binding,
    freshness: "STALE", source_gaps: [{ source_id: "S-029", reason: "SOURCE_INCOMPLETE" }], truncated: true,
    receipt: { schema_version: 1, tool_name: "chat_turn", arguments_sha256: "b".repeat(64), ...binding },
    chat: { schema_version: 1, publication_hash: binding.publication_hash, status: "UNBOUNDED_NO_MATCH",
      context, query_receipt: { tool_name: "search_events", ...binding }, lines: ["來源失敗，無法確認。"] },
  };
}

test("grouped trust tiers retain the original ordinal used by follow-up selection", () => {
  const summary = summarizeChat({
    verified: [{ public_event_id: "PE-2" }], unverified: [{ public_event_id: "PE-1" }],
    context: { last_result_event_ids: ["PE-1", "PE-2"] },
  });
  assert.equal(summary.sections[0].items[0].result_number, 2);
  assert.equal(summary.sections[1].items[0].result_number, 1);
  assert.deepEqual(summary.context.last_result_event_ids, ["PE-1", "PE-2"]);
});

test("Web chat sends only a typed turn and minimal context through the shared gateway", async () => {
  const calls = [];
  const payload = chatEnvelope();
  const result = await chatClient.requestChat("/query", {
    text: " 第一件有交通管制嗎？ ",
    context: { schema_version: 1, selected_region: "location:tc-xitun", last_result_event_ids: ["PE-1"],
      last_query_receipt: { query_id: "previous", transcript: "PRIVATE_RECEIPT" }, transcript: "PRIVATE_TRANSCRIPT" },
    history: ["PRIVATE_HISTORY"],
  }, { fetchImpl: async (url, options) => { calls.push([url, JSON.parse(options.body)]); return Response.json(payload); } });
  assert.deepEqual(calls, [["/query", { tool: "chat_turn", arguments: {
    text: "第一件有交通管制嗎？", context: { schema_version: 1, selected_region: "location:tc-xitun",
      last_result_event_ids: ["PE-1"], last_query_receipt: { query_id: "previous" } },
  } }]]);
  assert.deepEqual(result, payload);
  assert.equal(result.freshness, "STALE");
  assert.equal(result.truncated, true);
  assert.equal(result.source_gaps[0].reason, "SOURCE_INCOMPLETE");
});

test("follow-up carries the returned event identity and quick actions stay server resolved", async () => {
  const payload = chatEnvelope();
  const calls = [];
  const client = { fetchImpl: async (_url, options) => { calls.push(JSON.parse(options.body)); return Response.json(payload); } };
  const first = await chatClient.requestChat("/query", { quickAction: "weekend-events" }, client);
  await chatClient.requestChat("/query", { text: "跟昨天比改了什麼？", context: first.chat.context }, client);
  assert.equal(calls[0].arguments.quick_action, "weekend-events");
  assert.equal(calls[0].arguments.text, undefined);
  assert.equal(calls[1].arguments.context.selected_event_id, "PE-1");
});

test("invalid input cannot silently drop narrowing or send a request", async () => {
  const client = { fetchImpl: () => { assert.fail("invalid turn sent to gateway"); } };
  for (const input of [{ text: "" }, { text: 3 }, { text: "a".repeat(513) }, { quickAction: "write" },
    { text: "只看西屯", context: { selected_region: 7 } }]) {
    await assert.rejects(chatClient.requestChat("/query", input, client), error => error.code === "INVALID_ARGUMENTS");
  }
});

test("chat refuses malformed or mixed-publication answers before rendering", async () => {
  const mutations = [
    value => { value.schema_version = 2; }, value => { value.result_type = "publication_metadata"; },
    value => { value.query_id = ""; }, value => { value.chat.schema_version = 2; },
    value => { value.chat.publication_hash = "other"; }, value => { value.receipt.query_generation_id = "other"; },
    value => { value.chat.query_receipt.publication_hash = "other"; },
    value => { value.chat.context.selected_event_id = 7; },
    value => { value.chat.lines = [{ status: "FAILED" }]; },
    value => { value.chat.verified = [null]; },
    value => { value.chat.verified = [{ public_event_id: "PE-1", canonical_title: {} }]; },
    value => { value.chat.evidence_links = [{ evidence_locator: {} }]; },
    value => { value.chat.statistics = [{ value: {} }]; },
    value => { value.chat.sources = [{ source_health: {} }]; },
    value => { value.source_gaps = [{ reason: {} }]; },
  ];
  for (const mutate of mutations) {
    const payload = chatEnvelope(); mutate(payload);
    await assert.rejects(chatClient.requestChat("/query", { text: "只看西屯" }, {
      fetchImpl: async () => Response.json(payload),
    }), error => error.code === "INVALID_RESPONSE");
  }
});

test("Web chat retains release admission and surfaces a gateway outage", async () => {
  const release = { release_id: "release-29", code_sha: "c".repeat(40), publication_generation: "run-29" };
  const calls = [];
  await chatClient.requestChat("https://gateway.example/query", { quickAction: "no-data" }, {
    codeSha: release.code_sha, publicationGeneration: release.publication_generation,
    fetchImpl: async (url, options) => { calls.push([url, options]); return Response.json(url.endsWith("release.json") ? release : { ...chatEnvelope(), release }); },
  });
  assert.equal(calls.length, 2);
  assert.equal(JSON.parse(calls[1][1].body).release_id, release.release_id);
  await assert.rejects(chatClient.requestChat("/query", { text: "查官方證據" }, {
    fetchImpl: async () => { throw new Error("offline"); },
  }), error => error.code === "FAILED");
});
