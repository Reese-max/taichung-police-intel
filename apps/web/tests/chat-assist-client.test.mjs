import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { CHAT_ASSIST_NOT_SENT, requestChat, summarizeChat } from "../lib/chat-answer.js";
import { queryGateway } from "../lib/query-release-client.js";

const context = { basePath: "/taichung-police-intel", codeSha: "a".repeat(40), publicationGeneration: "run-62" };
const release = {
  schema_version: 1, code_sha: context.codeSha, publication_generation: context.publicationGeneration, release_id: "b".repeat(64),
  publication_hash: "c".repeat(64), artifact_hashes: { feed: "d".repeat(64), status: "e".repeat(64), brief: "c".repeat(64) },
  source_policy_hash: "f".repeat(64), query_generation: "1".repeat(64), evidence_catalog_hash: "2".repeat(64), built_at: "2026-10-01T00:00:00Z",
};
const receipt = { tool_name: "chat_turn", publication_hash: release.publication_hash, query_generation_id: release.query_generation };
const ANSWER = ["官方來源已核對：publication:X:title=測試。"];
const URL_OF = canonical => `https://example.test/n/${canonical}`;
const claim = (value = "測試", id = "PUB-X") => ({ claim_type: "STATUS", support_status: "SUPPORTED",
  propositions: [{ subject: `publication:${id.slice(4)}:title`, value }],
  supporting_evidence: [{ evidence_id: id, locator: `${URL_OF(id.slice(4))}#publication:${id.slice(4)}` }] });
const REPLACED = ["官方來源已核對：publication:X:title=替換。"];
const gateReceipt = (answer = ANSWER, claims = [claim()]) => ({ gate_status: "PASS", validator_version: "answer-evidence-gate/3",
  renderer_version: "controlled-answer-renderer/1", publication_hash: release.publication_hash, evidence_ids: ["PUB-X"], claims,
  answer_sha256: createHash("sha256").update(JSON.stringify(answer)).digest("hex") });
const ready = (answer, receipt, ids = ["PUB-X"]) => ({ assisted_answer: { status: "ANSWER_READY", reason_code: null, answer, selected_evidence_ids: ids, answer_evidence_receipt: receipt } });
function payload(chatFields = {}) {
  return {
    release, schema_version: 1, result_type: "conversation", query_id: "q-1", publication_id: release.publication_generation,
    publication_hash: release.publication_hash, query_generation_id: release.query_generation, policy: { policy_hash: release.source_policy_hash },
    freshness: "RECENT", verification_summary: "僅代表已核對的公開快照範圍。", source_gaps: [], receipt,
    chat: { schema_version: 1, status: "RECENT", resolved_request: { tool: "search_evidence", arguments: { q: "測試" } }, lines: ["索引中繼資料"],
      verified: [], unverified: [], conflicts: [], stale: [], statistics: [], sources: [], notices: [], gaps: [],
      evidence_links: [{ canonical_id: "X", source_id: "S-004", official_url: URL_OF("X"), title: "測 試" }],
      publication_hash: release.publication_hash, query_receipt: null, context: { schema_version: 1 },
      parser: { kind: "llm", version: "chat-intent-llm/1", time_window: null },
      assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: gateReceipt() },
      ...chatFields },
  };
}
function gateway(reply, calls) {
  return async (url, options = {}) => {
    calls.push([url, options]);
    return Response.json(String(url).endsWith("release.json") ? release : reply);
  };
}

test("the assisted question goes to /chat with the attestation and the bound release", async () => {
  const calls = [];
  const result = await requestChat("https://gateway.example/query", { text: " 有沒有測試公告？ ", assist: true, publicDataOnly: true },
    { ...context, fetchImpl: gateway(payload(), calls) });
  assert.equal(result.chat.assisted_answer.status, "ANSWER_READY");
  assert.equal(calls[0][0], "/taichung-police-intel/data/release.json");
  assert.equal(calls[1][0], "https://gateway.example/chat");
  assert.deepEqual(JSON.parse(calls[1][1].body), { tool: "chat_turn", arguments: { text: "有沒有測試公告？", context: { schema_version: 1 } },
    public_data_only: true, release_id: release.release_id });
});

test("a local Gateway still binds the release for the assisted route but not for /query", async () => {
  const calls = [];
  await requestChat("http://localhost:8787", { text: "測試", assist: true, publicDataOnly: true },
    { publicationGeneration: context.publicationGeneration, fetchImpl: gateway(payload(), calls) });
  assert.equal(calls[0][0], "/data/release.json"); assert.equal(calls[1][0], "http://localhost:8787/chat");
  const local = [];
  await queryGateway("http://localhost:8787/query", "get_source_health", {}, { fetchImpl: gateway({ ok: true }, local) });
  assert.equal(local.length, 1); assert.equal(local[0][0], "http://localhost:8787/query");
  assert.deepEqual(JSON.parse(local[0][1].body), { tool: "get_source_health", arguments: {} });
  await assert.rejects(queryGateway("http://localhost:8787", "chat_turn", {}, null, { route: "research" }), error => error.code === "CAPABILITY_NOT_AVAILABLE");
});

test("without the attestation, or for a quick action, nothing is sent", async () => {
  const calls = [];
  const fetchImpl = gateway(payload(), calls);
  await assert.rejects(requestChat("https://gateway.example", { text: "測試", assist: true }, { ...context, fetchImpl }),
    error => error.code === "PUBLIC_DATA_CONFIRMATION_REQUIRED");
  await assert.rejects(requestChat("https://gateway.example", { quickAction: "statistics", assist: true, publicDataOnly: true }, { ...context, fetchImpl }),
    error => error.code === "INVALID_ARGUMENTS");
  await assert.rejects(requestChat("https://gateway.example", { text: "測試", quickAction: "statistics", assist: true, publicDataOnly: true }, { ...context, fetchImpl }),
    error => error.code === "INVALID_ARGUMENTS");
  assert.equal(calls.length, 0);
});

test("an assisted reply with an unverifiable parser or answer is never shown", async () => {
  const bad = [
    { parser: undefined }, { assisted_answer: undefined }, { parser: { kind: "model", version: "x" } },
    { parser: { kind: "rules", version: "chat-rules/1", fallback_reason: "free text reason" } },
    { parser: { kind: "llm", version: "chat-intent-llm/1", time_window: { time_from: "next week", time_to: "2026-11-01", time_zone: "Asia/Taipei", label: "x" } } },
    { parser: { kind: "llm", version: "chat-intent-llm/1", time_window: { time_from: "2026-10-31", time_to: "2026-11-01", time_zone: "UTC", label: "x" } } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: [], selected_evidence_ids: [], answer_evidence_receipt: gateReceipt([]) } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, selected_evidence_ids: ["PUB-X"] } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: [{ text: "object" }], selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: gateReceipt() } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: { ...gateReceipt(), validator_version: "" } } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, answer_evidence_receipt: gateReceipt() } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, selected_evidence_ids: ["PUB-Y"], answer_evidence_receipt: gateReceipt() } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ["官方來源已核對：publication:X:title=替換。"], selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: gateReceipt() } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ["這是模型寫的結論。"], selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: gateReceipt(["這是模型寫的結論。"]) } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: { ...gateReceipt(), claims: [{ support_status: "PARTIAL" }] } } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: { ...gateReceipt(), publication_hash: "0".repeat(64) } } },
    { assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ANSWER, selected_evidence_ids: ["PUB-X"], answer_evidence_receipt: { ...gateReceipt(), renderer_version: "free-text/1" } } },
    ready(ANSWER, { ...gateReceipt(), gate_status: "BLOCKED" }),
    // the text and its hash replaced together still disagree with the proposition
    ready(["官方來源已核對：publication:X:title=替換。"], gateReceipt(["官方來源已核對：publication:X:title=替換。"])),
    ready(ANSWER, gateReceipt(ANSWER, [claim("測試", "PUB-Y")])),
    // text and proposition replaced together, the hash left as the Worker computed it
    ready(["官方來源已核對：publication:X:title=替換。"], gateReceipt(ANSWER, [claim("替換")])),
    ready(ANSWER, { ...gateReceipt(), evidence_ids: ["PUB-Y"] }),
    // answer, proposition and hash all replaced together: the reply's own evidence title disagrees
    ready(REPLACED, gateReceipt(REPLACED, [claim("替換")])),
    // answer, proposition and the reply's evidence title all replaced: only the Worker's hash disagrees
    { evidence_links: [{ canonical_id: "X", source_id: "S-004", official_url: URL_OF("X"), title: "替換" }], ...ready(REPLACED, gateReceipt(ANSWER, [claim("替換")])) },
    ready(ANSWER, gateReceipt(ANSWER, [{ ...claim(), supporting_evidence: [{ evidence_id: "PUB-X", locator: "https://evil.test/n/X#publication:X" }] }])),
    { evidence_links: [], ...ready(ANSWER, gateReceipt()) },
    ready(ANSWER, gateReceipt(ANSWER, [null])),
    ready(ANSWER, gateReceipt(ANSWER, [{ ...claim(), propositions: "publication:X:title=測試" }])),
    { parser: { kind: "rules", version: "chat-rules/1", fallback_reason: "DISABLED",
      time_window: { time_from: "2026-10-31", time_to: "2026-11-01", time_zone: "Asia/Taipei", label: "x".repeat(440) } },
      assisted_answer: { status: "NOT_ATTEMPTED", reason_code: "DISABLED", answer: [] } },
    ready(ANSWER, gateReceipt(ANSWER, [{ ...claim(), supporting_evidence: [{ evidence_id: "PUB-Y" }] }])),
    ready(ANSWER, gateReceipt(ANSWER, [{ ...claim(), claim_type: "CAUSE" }])),
    ready(ANSWER, gateReceipt(ANSWER, [{ ...claim(), propositions: [...claim().propositions, { subject: "publication:X:title", value: "x" }] }])),
    { assisted_answer: { status: "NO_ANSWER", reason_code: "OUTPUT_REJECTED", answer: ["模型草稿"] } },
    { assisted_answer: { status: "DRAFT", reason_code: null, answer: [] } },
  ];
  for (const fields of bad) {
    const calls = [];
    await assert.rejects(requestChat("https://gateway.example", { text: "測試", assist: true, publicDataOnly: true }, { ...context, fetchImpl: gateway(payload(fields), calls) }),
      error => error.code === "INVALID_RESPONSE", JSON.stringify(fields));
  }
  const fallback = { parser: { kind: "rules", version: "chat-rules/1", fallback_reason: "DISABLED" },
    assisted_answer: { status: "NOT_ATTEMPTED", reason_code: "DISABLED", answer: [] } };
  const ok = await requestChat("https://gateway.example", { text: "測試", assist: true, publicDataOnly: true }, { ...context, fetchImpl: gateway(payload(fallback), []) });
  assert.deepEqual(summarizeChat(ok.chat).parser, fallback.parser);
  assert.deepEqual(summarizeChat(ok.chat).assistedAnswer, fallback.assisted_answer);
});

test("only reasons reached before any provider transmission count as not sent", () => {
  // research.js: DISABLED/KEY_MISSING/BILLING_REVIEW_REQUIRED/ADMISSION_UNCONFIGURED stop before admission,
  // ADMISSION_DENIED stops before the provider; the rest may follow a sent request.
  assert.deepEqual([...CHAT_ASSIST_NOT_SENT].sort(), ["ADMISSION_DENIED", "ADMISSION_UNCONFIGURED", "BILLING_REVIEW_REQUIRED", "DISABLED", "KEY_MISSING"]);
  for (const code of ["OUTPUT_REJECTED", "PROVIDER_UNAVAILABLE", "TIMEOUT"]) assert.equal(CHAT_ASSIST_NOT_SENT.has(code), false);
});

test("a deterministic reply that carries assisted fields is never shown as checked", async () => {
  for (const fields of [{ assisted_answer: { status: "ANSWER_READY", reason_code: null, answer: ["未經核對的文字"], answer_evidence_receipt: { validator_version: "" } }, parser: undefined },
    { parser: { kind: "llm", version: "chat-intent-llm/1", time_window: null }, assisted_answer: undefined }]) {
    await assert.rejects(requestChat("https://gateway.example", { text: "測試" }, { ...context, fetchImpl: gateway(payload(fields), []) }),
      error => error.code === "INVALID_RESPONSE", JSON.stringify(fields));
  }
});

test("deterministic chat replies are unchanged and carry no assisted fields", async () => {
  const calls = [];
  const plain = payload({ parser: undefined, assisted_answer: undefined });
  const result = await requestChat("https://gateway.example", { text: "測試" }, { ...context, fetchImpl: gateway(plain, calls) });
  assert.equal(calls[1][0], "https://gateway.example/query");
  assert.equal(JSON.parse(calls[1][1].body).public_data_only, undefined);
  assert.equal(summarizeChat(result.chat).parser, null); assert.equal(summarizeChat(result.chat).assistedAnswer, null);
});
