// All provider traffic and fictional rights permissions here are OFFLINE MOCKS.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { createGovernedPolicyFixture } from "./governed-policy-fixture.mjs";
import { CHAT_INTENT_LIMITS, CHAT_TIME_EXPRESSIONS, resolveTimeExpression, validateChatIntent } from "../../../workers/query-gateway/src/chat-assist.js";
import { requestChat } from "../lib/chat-answer.js";

const hash = value => createHash("sha256").update(value).digest("hex");
const PROVIDER = "https://api.minimax.io/v1/chat/completions";
const SOURCES = ["S-004", "S-006"];
const QUESTION = "反詐相關的公告";
// Wednesday 2026-10-28 11:00 in Taipei.
const NOW = Date.parse("2026-10-28T03:00:00Z");
const completion = value => Response.json({ choices: [{ finish_reason: "stop", message: { content: JSON.stringify(value) } }] });
const request = () => new Request("https://worker.test/chat", { method: "POST" });
const chat = (text, extra = {}) => ({ tool: "chat_turn", arguments: { text, ...extra }, public_data_only: true });

function ready(log) {
  return { RESEARCH_ENABLED: "true", MINIMAX_API_KEY: "OFFLINE_FAKE_KEY", MINIMAX_BILLING_REVIEWED: "true",
    RESEARCH_ADMISSION: { fetch: async req => { log.admissions.push(await req.json()); return Response.json({ allowed: true }); } } };
}
// Each provider reply is used once, in order; any extra call fails the test.
function provider(log, replies) {
  return async (url, options) => {
    assert.equal(url, PROVIDER);
    const body = JSON.parse(options.body);
    log.calls.push({ system: body.messages[0].content, payload: JSON.parse(body.messages[1].content) });
    const reply = replies.shift();
    assert.ok(reply, "unexpected provider call");
    return typeof reply === "function" ? reply(log.calls.at(-1).payload) : completion(reply);
  };
}

async function withSnapshot(run, options = { briefReviewed: true }) {
  const fixture = await createGovernedPolicyFixture(options);
  try {
    const module = await fixture.loadWorker("chat-assist");
    const bytes = await fixture.publication();
    const snapshot = await module.buildSnapshot({ PUBLIC_ORIGIN: "https://example.test" }, async name => ({ bytes: bytes[name], hash: hash(bytes[name]) }));
    snapshot.release = { release_id: "offline-release" };
    await run(module, snapshot, fixture, bytes);
  } finally { await fixture.cleanup(); }
}

test("relative times resolve on the server clock to explicit Asia/Taipei dates", () => {
  const at = (expression, extra = {}, now = NOW) => resolveTimeExpression({ time_expression: expression, ...extra }, now);
  const expect = (from, to, label) => ({ time_from: from, time_to: to, time_zone: "Asia/Taipei", label });
  assert.equal(at("none"), null);
  assert.equal(resolveTimeExpression({}, NOW), null);
  assert.deepEqual(at("today"), expect("2026-10-28", "2026-10-28", "今天"));
  assert.deepEqual(at("yesterday"), expect("2026-10-27", "2026-10-27", "昨天"));
  assert.deepEqual(at("day_before_yesterday"), expect("2026-10-26", "2026-10-26", "前天"));
  assert.deepEqual(at("tomorrow"), expect("2026-10-29", "2026-10-29", "明天"));
  assert.deepEqual(at("day_after_tomorrow"), expect("2026-10-30", "2026-10-30", "後天"));
  assert.deepEqual(at("this_week"), expect("2026-10-26", "2026-11-01", "這週"));
  assert.deepEqual(at("next_week"), expect("2026-11-02", "2026-11-08", "下週"));
  assert.deepEqual(at("this_weekend"), expect("2026-10-31", "2026-11-01", "這週末"));
  assert.deepEqual(at("next_weekend"), expect("2026-11-07", "2026-11-08", "下週末"));
  assert.deepEqual(at("last_days", { last_days: 3 }), expect("2026-10-26", "2026-10-28", "最近3天"));
  assert.deepEqual(at("last_week"), expect("2026-10-22", "2026-10-28", "最近一週"));
  assert.deepEqual(at("dates", { date_from: "2026-11-01", date_to: "2026-11-02" }), expect("2026-11-01", "2026-11-02", "指定日期"));
  // 00:30 on the 28th in Taipei is still the 27th in UTC; Sunday keeps its own weekend.
  assert.deepEqual(at("today", {}, Date.parse("2026-10-27T16:30:00Z")), expect("2026-10-28", "2026-10-28", "今天"));
  const sunday = Date.parse("2026-11-01T02:00:00Z");
  assert.deepEqual(at("this_weekend", {}, sunday), expect("2026-10-31", "2026-11-01", "這週末"));
  assert.deepEqual(at("this_week", {}, sunday), expect("2026-10-26", "2026-11-01", "這週"));
});

test("the Worker's time windows match intel_v2/conversation.py for the same day", () => {
  const phrases = { today: "今天", yesterday: "昨天", day_before_yesterday: "前天", tomorrow: "明天", day_after_tomorrow: "後天",
    this_week: "這週", next_week: "下週", this_weekend: "這週末", next_weekend: "下週末", last_week: "最近一週", last_days: "最近3天" };
  const root = fileURLToPath(new URL("../../../", import.meta.url));
  for (const [day, now] of [["2026-10-28", NOW], ["2026-11-01", Date.parse("2026-11-01T02:00:00Z")], ["2026-10-31", Date.parse("2026-10-31T15:59:00Z")]]) {
    const script = ["import json, sys", "from datetime import date", "from intel_v2.conversation import parse_time_window",
      "phrases = json.loads(sys.argv[1])", `today = date.fromisoformat("${day}")`,
      "print(json.dumps({key: parse_time_window(text, today) for key, text in phrases.items()}, ensure_ascii=False))"].join("\n");
    const run = spawnSync(process.env.PYTHON || "python", ["-X", "utf8", "-B", "-c", script, JSON.stringify(phrases)], { cwd: root, encoding: "utf8", timeout: 15_000 });
    assert.equal(run.status, 0, run.stderr);
    const python = JSON.parse(run.stdout);
    for (const key of Object.keys(phrases)) {
      assert.deepEqual(resolveTimeExpression({ time_expression: key, ...(key === "last_days" ? { last_days: 3 } : {}) }, now), python[key], `${day} ${key}`);
    }
  }
});

test("only a menu tool with typed, bounded arguments survives validation", () => {
  const ok = validateChatIntent({ tool: "search_evidence", q: "  反詐  ", source_id: "S-004", time_expression: "none" }, SOURCES, QUESTION, NOW);
  assert.deepEqual(ok, { tool: "search_evidence", arguments: { q: "反詐", source_id: "S-004" }, time_window: null });
  assert.deepEqual(validateChatIntent({ tool: "get_source_health", source_id: "S-006" }, SOURCES, QUESTION, NOW).arguments, { source_id: "S-006" });
  assert.equal(validateChatIntent({ tool: "search_events", time_expression: "this_weekend" }, SOURCES, QUESTION, NOW).time_window.label, "這週末");
  assert.deepEqual(validateChatIntent({ tool: "search_evidence", q: "反詐 公告" }, SOURCES, QUESTION, NOW).arguments, { q: "反詐 公告" });
  // Keywords outside the question are the model steering the search.
  assert.deepEqual(validateChatIntent({ tool: "search_evidence", q: "car pet" }, SOURCES, "查詢 car pet 相關公告", NOW).arguments, { q: "car pet" });
  assert.throws(() => validateChatIntent({ tool: "search_evidence", q: "carpet" }, SOURCES, "查詢 car pet 相關公告", NOW), error => error.code === "OUTPUT_REJECTED");
  for (const q of ["選舉", "反詐 選舉", "交通管制"]) {
    assert.throws(() => validateChatIntent({ tool: "search_evidence", q }, SOURCES, QUESTION, NOW), error => error.code === "OUTPUT_REJECTED", q);
  }
  for (const bad of [null, [], "search_evidence", {}, { tool: "validate_answer" }, { tool: "chat_turn", q: "x" }, { tool: "get_publication_receipt" },
    { tool: "search_evidence", sql: "select 1" }, { tool: "search_evidence", q: "x".repeat(CHAT_INTENT_LIMITS.q + 1) },
    { tool: "search_evidence", q: "   " }, { tool: "search_evidence", q: "a\u0000b" }, { tool: "search_evidence", q: 7 },
    { tool: "get_current_brief", q: "x" }, { tool: "search_evidence", source_id: "S-999" }, { tool: "get_current_brief", source_id: "S-004" },
    { tool: "search_events", time_expression: "someday" }, { tool: "search_events", time_expression: "last_days" },
    { tool: "search_events", time_expression: "today", last_days: 3 }, { tool: "search_events", time_expression: "last_days", last_days: 0 },
    { tool: "search_events", time_expression: "last_days", last_days: CHAT_INTENT_LIMITS.lastDays + 1 },
    { tool: "search_events", time_expression: "last_days", last_days: 1.5 }, { tool: "search_events", time_expression: "dates", date_from: "2026-11-01" },
    { tool: "search_events", date_from: "2026-11-01", date_to: "2026-11-02" }, { tool: "search_events", time_expression: "dates", date_from: "2026-02-30", date_to: "2026-03-01" },
    { tool: "search_events", time_expression: "dates", date_from: "2026-11-02", date_to: "2026-11-01" },
    { tool: "search_events", time_expression: "dates", date_from: "2025-01-01", date_to: "2026-11-01" },
    { tool: "search_events", time_expression: "dates", date_from: "2026/11/01", date_to: "2026-11-02" }]) {
    assert.throws(() => validateChatIntent(bad, SOURCES, QUESTION, NOW), error => error.code === "OUTPUT_REJECTED", JSON.stringify(bad));
  }
  assert.ok(CHAT_TIME_EXPRESSIONS.includes("none"));
});

test("a disabled provider answers with the deterministic rules and sends nothing", () => withSnapshot(async (module, snapshot) => {
  const result = await module.executeAssistedChat(snapshot, chat("查官方證據 測試"), {}, request(), () => { throw new Error("unexpected provider call"); });
  assert.equal(result.result_type, "conversation"); assert.equal(result.receipt.tool_name, "chat_turn");
  assert.deepEqual(result.chat.resolved_request, { tool: "search_evidence", arguments: { q: "測試" } });
  assert.deepEqual(result.chat.parser, { kind: "rules", version: "chat-rules/1", fallback_reason: "DISABLED" });
  assert.deepEqual(result.chat.assisted_answer, { status: "NOT_ATTEMPTED", reason_code: "DISABLED", answer: [] });
  assert.equal(result.chat.results.length, 2);
}));

test("a validated intent runs the typed query and only gate-checked text becomes the answer", () => withSnapshot(async (module, snapshot) => {
  const log = { admissions: [], calls: [] };
  const result = await module.executeAssistedChat(snapshot, chat("有沒有測試相關的官方公告？"), ready(log), request(), provider(log, [
    { tool: "search_evidence", q: "測試" },
    payload => completion({ evidence_ids: payload.sources.slice(0, 1).map(row => row.evidence_id) }),
  ]));
  assert.equal(log.calls.length, 2); assert.equal(log.admissions.length, 2);
  // The intent call carries the question, the menu and public source IDs, never publication text.
  const intent = log.calls[0].payload;
  assert.deepEqual(Object.keys(intent).sort(), ["question", "source_ids", "time_expressions", "tools"]);
  assert.equal(intent.question, "有沒有測試相關的官方公告？");
  assert.deepEqual(intent.source_ids, snapshot.sources.map(row => row.source_id));
  assert.equal(JSON.stringify(intent).includes("測試官方標題"), false);
  assert.deepEqual(result.chat.parser, { kind: "llm", version: "chat-intent-llm/1", time_window: null });
  assert.deepEqual(result.chat.resolved_request, { tool: "search_evidence", arguments: { q: "測試" } });
  const answer = result.chat.assisted_answer;
  assert.equal(answer.status, "ANSWER_READY"); assert.equal(answer.reason_code, null);
  assert.equal(answer.answer.length, 1); assert.match(answer.answer[0], /^官方來源已核對：publication:.*:title=/);
  assert.equal(answer.answer_evidence_receipt.validator_version, "answer-evidence-gate/3");
  assert.deepEqual(answer.selected_evidence_ids, [log.calls[1].payload.sources[0].evidence_id]);
  const receipt = answer.answer_evidence_receipt;
  assert.equal(receipt.renderer_version, "controlled-answer-renderer/1"); assert.equal(receipt.publication_hash, result.publication_hash);
  assert.ok(receipt.evidence_ids.includes(answer.selected_evidence_ids[0]));
  assert.deepEqual(receipt.claims.map(claim => claim.support_status), ["SUPPORTED"]);
  assert.equal(receipt.answer_sha256, hash(JSON.stringify(answer.answer)));
}));

test("rejected or failed model output falls back to the rules without an answer", () => withSnapshot(async (module, snapshot) => {
  for (const reply of [{ tool: "validate_answer", claims: [] }, { tool: "search_evidence", q: "x", url: "https://evil.test" },
    () => Response.json({ choices: [{ finish_reason: "stop", message: { content: "我直接回答你：沒有事件。" } }] }),
    () => new Response("OFFLINE_FAKE_KEY upstream error", { status: 500 })]) {
    const log = { admissions: [], calls: [] };
    const result = await module.executeAssistedChat(snapshot, chat("查官方證據 測試"), ready(log), request(), provider(log, [reply]));
    assert.equal(log.calls.length, 1);
    assert.equal(result.chat.parser.kind, "rules");
    assert.ok(["OUTPUT_REJECTED", "PROVIDER_UNAVAILABLE"].includes(result.chat.parser.fallback_reason));
    assert.deepEqual(result.chat.resolved_request, { tool: "search_evidence", arguments: { q: "測試" } });
    assert.equal(result.chat.assisted_answer.status, "NOT_ATTEMPTED"); assert.deepEqual(result.chat.assisted_answer.answer, []);
    assert.equal(JSON.stringify(result).includes("OFFLINE_FAKE_KEY"), false);
  }
}));

test("a parsed time window is reported and never silently broadened", () => withSnapshot(async (module, snapshot) => {
  const log = { admissions: [], calls: [] };
  const result = await module.executeAssistedChat(snapshot, chat("這週末的測試公告"), ready(log), request(), provider(log, [
    { tool: "search_evidence", q: "測試", time_expression: "this_weekend" }]));
  assert.equal(log.calls.length, 1);
  assert.deepEqual(result.chat.parser.time_window, resolveTimeExpression({ time_expression: "this_weekend" }));
  assert.equal(result.chat.status, "CAPABILITY_NOT_AVAILABLE"); assert.deepEqual(result.chat.results, []);
  assert.ok(result.chat.notices.some(notice => notice.includes("selected_time_window")));
  assert.deepEqual(result.chat.assisted_answer, { status: "NOT_ATTEMPTED", reason_code: "QUERY_NOT_EXECUTED", answer: [] });
}));

test("model-parsed narrowing carries into the next turn and is never dropped", () => withSnapshot(async (module, snapshot) => {
  const log = { admissions: [], calls: [] };
  const first = await module.executeAssistedChat(snapshot, chat("測試公告，只看 S-004"), ready(log), request(), provider(log, [
    { tool: "search_evidence", q: "測試", source_id: "S-004" }]));
  assert.equal(first.chat.context.selected_agency, "S-004");
  // The follow-up runs on the rules (provider off) and still keeps the source.
  const second = await module.executeAssistedChat(snapshot, chat("查官方證據 官方", { context: first.chat.context }), {}, request(), () => { throw new Error("unexpected"); });
  assert.deepEqual(second.chat.resolved_request.arguments, { source_id: "S-004", q: "官方" });
  const weekend = await module.executeAssistedChat(snapshot, chat("這週末的測試公告"), ready(log), request(), provider(log, [
    { tool: "search_evidence", q: "測試", time_expression: "this_weekend" }]));
  assert.deepEqual(weekend.chat.context.selected_time_window, weekend.chat.parser.time_window);
  const next = await module.executeAssistedChat(snapshot, chat("查官方證據 測試", { context: weekend.chat.context }), {}, request(), () => { throw new Error("unexpected"); });
  assert.equal(next.chat.status, "CAPABILITY_NOT_AVAILABLE"); assert.deepEqual(next.chat.results, []);
  assert.ok(next.chat.notices.some(notice => notice.includes("selected_time_window")));
  assert.deepEqual(next.chat.context.selected_time_window, weekend.chat.parser.time_window);
}));

test("questions the Worker cannot answer stay unavailable and skip the answer step", () => withSnapshot(async (module, snapshot) => {
  const log = { admissions: [], calls: [] };
  const result = await module.executeAssistedChat(snapshot, chat("臺中詐欺最近三個月趨勢"), ready(log), request(), provider(log, [
    { tool: "query_statistics", time_expression: "last_days", last_days: 90 }]));
  assert.equal(log.calls.length, 1); assert.equal(result.chat.status, "CAPABILITY_NOT_AVAILABLE");
  assert.equal(result.chat.parser.time_window.label, "最近90天");
  assert.deepEqual(result.chat.assisted_answer, { status: "NOT_ATTEMPTED", reason_code: "TOOL_HAS_NO_ANSWER_STEP", answer: [] });
}));

test("without formal admission no publication text is sent for an answer", () => withSnapshot(async (module, snapshot) => {
  snapshot.formalAdmission = { ...snapshot.formalAdmission, status: "RIGHTS_BLOCKED" };
  const log = { admissions: [], calls: [] };
  const result = await module.executeAssistedChat(snapshot, chat("測試公告"), ready(log), request(), provider(log, [{ tool: "search_evidence", q: "測試" }]));
  assert.equal(log.calls.length, 1); assert.equal(log.admissions.length, 1);
  assert.deepEqual(result.chat.assisted_answer, { status: "NOT_ATTEMPTED", reason_code: "RIGHTS_BLOCKED", answer: [] });
}));

test("personal data, a missing attestation or extra fields stop before admission or provider traffic", () => withSnapshot(async (module, snapshot) => {
  const log = { admissions: [], calls: [] };
  const fetchImpl = provider(log, []);
  await assert.rejects(module.executeAssistedChat(snapshot, chat("我的電話 0912345678 有沒有被詐騙"), ready(log), request(), fetchImpl), error => error.code === "INPUT_RESTRICTED");
  for (const bad of [{ ...chat("測試"), public_data_only: false }, { tool: "chat_turn", arguments: { text: "測試" } }, { ...chat("測試"), mode: "documents" },
    { tool: "search_evidence", arguments: { text: "測試" }, public_data_only: true }, chat("測試", { quick_action: "statistics" }), { ...chat("測試"), arguments: null },
    chat("測試", { context: "not an object" }), chat("測試", { context: { selected_time_window: { time_from: "later", time_to: "soon" } } })]) {
    await assert.rejects(module.executeAssistedChat(snapshot, bad, ready(log), request(), fetchImpl), error => error.code === "INVALID_ARGUMENTS", JSON.stringify(bad));
  }
  assert.equal(log.calls.length, 0); assert.equal(log.admissions.length, 0);
}));

test("over HTTP with every gate open, /chat answers through the browser client while MCP and /query call no model", () => withSnapshot(async (module, snapshot, fixture, bytes) => {
  const originalFetch = globalThis.fetch;
  const log = { admissions: [], calls: [] };
  const replies = [];
  const env = { ...ready(log), PUBLIC_ORIGIN: "https://publication.test", ALLOWED_ORIGINS: "https://ui.test", CF_VERSION_METADATA: { id: "test-worker", tag: "a".repeat(40) } };
  const release = await module.createReleaseManifest(snapshot, env.CF_VERSION_METADATA.tag);
  const fromProvider = provider(log, replies);
  globalThis.fetch = async (url, options) => {
    if (String(url) === PROVIDER) return fromProvider(url, options);
    const name = String(url).split("/").at(-1);
    return name === "release.json" ? Response.json(release) : new Response(bytes[name], { status: bytes[name] ? 200 : 404 });
  };
  let ip = 0;
  const post = (path, body, headers = {}) => module.default.fetch(new Request(`https://worker.test/${path}`, { method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": `chat-${++ip}`, ...headers }, body: typeof body === "string" ? body : JSON.stringify(body) }), env);
  try {
    for (const [body, status] of [[chat("查官方證據 測試"), 503], [{ ...chat("查官方證據 測試"), release_id: "wrong" }, 503],
      [{ ...chat("查官方證據 測試"), release_id: release.release_id, public_data_only: false }, 400]]) {
      assert.equal((await post("chat", body)).status, status, JSON.stringify(body));
    }
    assert.equal((await post("chat", "{}", { Origin: "https://evil.test" })).status, 403);
    assert.equal((await post("chat", "x".repeat(8193))).status, 413);
    // Deterministic transports with the provider fully enabled: no admission, no provider call, no assisted fields.
    const query = await (await post("query", { tool: "chat_turn", arguments: { text: "查官方證據 測試" } })).json();
    assert.equal(query.chat.parser, undefined); assert.equal(query.chat.assisted_answer, undefined);
    assert.equal((await post("query", { tool: "chat_turn", arguments: { text: "查官方證據 測試", public_data_only: true } })).status, 400);
    const tools = (await (await post("mcp", { jsonrpc: "2.0", id: 1, method: "tools/list" })).json()).result.tools;
    assert.deepEqual(Object.keys(tools.find(tool => tool.name === "chat_turn").inputSchema.properties).sort(), ["context", "quick_action", "text"]);
    const mcp = (await (await post("mcp", { jsonrpc: "2.0", id: 2, method: "tools/call", params: { name: "chat_turn", arguments: { text: "查官方證據 測試" } } })).json()).result.structuredContent;
    assert.equal(mcp.chat.parser, undefined);
    assert.deepEqual(mcp.chat.resolved_request, query.chat.resolved_request);
    assert.equal(log.calls.length, 0); assert.equal(log.admissions.length, 0);
    // The browser client against the real route: the Worker's answer passes the client's checks...
    const viaWorker = tamper => async (url, options) => {
      if (String(url).endsWith("release.json")) return Response.json(release);
      const response = await post("chat", JSON.parse(options.body));
      if (!tamper) return response;
      const value = await response.json(); tamper(value); return Response.json(value);
    };
    const clientContext = { basePath: "", codeSha: release.code_sha, publicationGeneration: release.publication_generation };
    replies.push({ tool: "search_evidence", q: "測試官方" }, payload => completion({ evidence_ids: payload.sources.slice(0, 1).map(row => row.evidence_id) }));
    const answered = await requestChat("https://gateway.example/query", { text: "測試官方公告", assist: true, publicDataOnly: true }, { ...clientContext, fetchImpl: viaWorker() });
    assert.equal(answered.chat.parser.kind, "llm");
    assert.equal(answered.chat.assisted_answer.status, "ANSWER_READY"); assert.equal(answered.chat.assisted_answer.answer.length, 1);
    assert.equal(log.calls.length, 2); assert.equal(log.admissions.length, 2);
    // ...and the same answer with its text replaced or its receipt forged is never shown.
    for (const tamper of [value => { value.chat.assisted_answer.answer = ["官方來源已核對：publication:X:title=偽造。"]; },
      value => { value.chat.assisted_answer.answer_evidence_receipt.validator_version = ""; },
      value => { value.chat.assisted_answer.selected_evidence_ids = ["PUB-OTHER"]; }]) {
      replies.push({ tool: "search_evidence", q: "測試官方" }, payload => completion({ evidence_ids: payload.sources.slice(0, 1).map(row => row.evidence_id) }));
      await assert.rejects(requestChat("https://gateway.example/query", { text: "測試官方公告", assist: true, publicDataOnly: true }, { ...clientContext, fetchImpl: viaWorker(tamper) }),
        error => error.code === "INVALID_RESPONSE");
    }
  } finally { globalThis.fetch = originalFetch; }
}));
