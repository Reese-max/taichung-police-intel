import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";
import { createGovernedPolicyFixture } from "./governed-policy-fixture.mjs";

const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` :
  value && typeof value === "object" ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
const hash = value => createHash("sha256").update(value).digest("hex");

// Use the real Worker release and governance gates. All inputs are fictional,
// offline copies; no real source permission or live provider is touched.
async function fixtureRun(run, mutate = () => {}) {
  const fixture = await createGovernedPolicyFixture({ briefReviewed: true });
  const originalFetch = globalThis.fetch;
  try {
    const module = await fixture.loadWorker("chat-turn");
    const values = Object.fromEntries(Object.entries(await fixture.publication()).map(([name, bytes]) => [name, JSON.parse(bytes)]));
    mutate(values);
    const bytes = Object.fromEntries(Object.entries(values).map(([name, value]) => [name, Buffer.from(JSON.stringify(value))]));
    const env = { PUBLIC_ORIGIN: "https://publication.example.test", CF_VERSION_METADATA: { id: "chat-test", tag: "a".repeat(40) } };
    const snapshot = await module.buildSnapshot(env, async name => ({ bytes: bytes[name], hash: hash(bytes[name]) }));
    const release = await module.createReleaseManifest(snapshot, env.CF_VERSION_METADATA.tag);
    globalThis.fetch = async url => {
      const name = String(url).split("/").at(-1);
      assert.ok(name === "release.json" || bytes[name], `unexpected external fetch: ${url}`);
      return name === "release.json" ? Response.json(release) : new Response(bytes[name]);
    };
    let requestNumber = 0;
    const call = async (tool, args, transport = "query") => {
      const response = await module.default.fetch(new Request(`https://gateway.example.test/${transport}`, {
        method: "POST", headers: { "Content-Type": "application/json", "CF-Connecting-IP": `fixture-${++requestNumber}` },
        body: JSON.stringify(transport === "mcp" ? { jsonrpc: "2.0", id: requestNumber, method: "tools/call", params: { name: tool, arguments: args } } : { tool, arguments: args }),
      }), env);
      const body = await response.json();
      return { status: response.status, isError: body.result?.isError ?? Boolean(body.error),
        payload: transport === "mcp" ? body.result?.structuredContent ?? JSON.parse(body.result.content[0].text) : body };
    };
    const get = async path => (await module.default.fetch(new Request(`https://gateway.example.test/${path}`, { headers: { "CF-Connecting-IP": `fixture-${++requestNumber}` } }), env)).json();
    const list = async () => (await (await module.default.fetch(new Request("https://gateway.example.test/mcp", {
      method: "POST", headers: { "Content-Type": "application/json", "CF-Connecting-IP": `fixture-${++requestNumber}` },
      body: JSON.stringify({ jsonrpc: "2.0", id: requestNumber, method: "tools/list" }),
    }), env)).json()).result.tools;
    const raw = async (body, transport) => {
      const response = await module.default.fetch(new Request(`https://gateway.example.test/${transport}`, {
        method: "POST", headers: { "Content-Type": "application/json", "CF-Connecting-IP": `fixture-${++requestNumber}` }, body,
      }), env);
      return { status: response.status, payload: await response.json() };
    };
    await run({ call, get, list, raw, values, snapshot, fixture });
  } finally { globalThis.fetch = originalFetch; await fixture.cleanup(); }
}

test("chat availability agrees between HTTP capabilities and MCP tools/list", () => fixtureRun(async ({ get, list }) => {
  const capabilities = await get("capabilities");
  const tools = await list();
  assert.deepEqual(capabilities.capabilities, tools.map(tool => tool.name));
  assert.ok(capabilities.capabilities.includes("chat_turn"));
  assert.ok(!capabilities.unavailable_capabilities.includes("chat_turn"));
  for (const name of ["search_events", "get_event", "compare_event_versions", "query_statistics"]) {
    assert.ok(capabilities.unavailable_capabilities.includes(name));
    assert.ok(!tools.some(tool => tool.name === name));
  }
  const schema = tools.find(tool => tool.name === "chat_turn").inputSchema;
  assert.equal(schema.additionalProperties, false);
  assert.equal(schema.properties.text.maxLength, 512);
}));

test("unsupported event/statistics questions return bounded conversation unavailable over HTTP and MCP", () => fixtureRun(async ({ call }) => {
  for (const args of [{ text: "這週末西屯區有哪些交通管制？" }, { quick_action: "statistics" },
    { quick_action: "weekend-events" }, { text: "今年詐欺統計" }, { quick_action: "by-district" }]) {
    for (const transport of ["query", "mcp"]) {
      const { status, isError, payload } = await call("chat_turn", args, transport);
      assert.equal(status, 200); assert.equal(isError, false);
      assert.equal(payload.result_type, "conversation");
      assert.equal(payload.chat.status, "CAPABILITY_NOT_AVAILABLE");
      assert.ok(payload.chat.lines.length);
      assert.equal(payload.chat.query_receipt, null);
      assert.deepEqual(payload.chat.statistics, []);
      assert.deepEqual(payload.chat.event_ids, []);
      assert.equal(payload.chat.no_match, null);
    }
  }
}));

test("no-data quick action retains the actual source-health payload and receipt", () => fixtureRun(async ({ call }) => {
  const direct = (await call("get_source_health", {})).payload;
  const { status, payload } = await call("chat_turn", { quick_action: "no-data" });
  assert.equal(status, 200);
  assert.deepEqual(payload.chat.resolved_request, { tool: "get_source_health", arguments: {} });
  assert.deepEqual(payload.chat.sources, direct.sources);
  assert.deepEqual(payload.chat.gaps, direct.source_gaps);
  assert.equal(payload.chat.freshness, direct.freshness);
  assert.equal(payload.chat.status, "RECENT");
  assert.equal(payload.chat.query_receipt.tool_name, "get_source_health");
  assert.equal(payload.chat.query_receipt.arguments_sha256, direct.receipt.arguments_sha256);
  assert.equal(payload.chat.context.last_query_receipt.query_generation_id, payload.query_generation_id);
  assert.ok(payload.chat.sources[0].last_checked_at);
}));

test("official metadata evidence retains links, scope, dates, results and source health", () => fixtureRun(async ({ call }) => {
  const direct = (await call("search_evidence", { q: "測試官方標題 1" })).payload;
  const { status, payload } = await call("chat_turn", { text: "測試官方標題 1", quick_action: "official-evidence" });
  assert.equal(status, 200); assert.equal(payload.result_type, "conversation");
  assert.deepEqual(payload.chat.results, direct.results);
  assert.equal(payload.chat.query_receipt.result_count, 1);
  assert.equal(payload.receipt.result_count, 1);
  assert.equal(payload.chat.evidence_links[0].official_url, direct.results[0].official_url);
  assert.equal(payload.chat.evidence_links[0].published_at, direct.results[0].published_at);
  assert.equal(payload.chat.evidence_links[0].fetched_at, direct.results[0].fetched_at);
  assert.deepEqual(payload.chat.evidence_ids, []);
  assert.match(payload.chat.lines.join(" "), /索引|中繼資料/);
}));

for (const state of ["STALE", "PARTIAL", "FAILED", "UNKNOWN"]) {
  test(`chat retains ${state} and cannot turn zero results into an event absence`, () => fixtureRun(async ({ call }) => {
    const args = { text: "unmatched-fictional-term", quick_action: "official-evidence" };
    const direct = (await call("search_evidence", { q: args.text })).payload;
    const { status, payload } = await call("chat_turn", args);
    assert.equal(status, 200);
    assert.equal(payload.freshness, direct.freshness);
    assert.deepEqual(payload.source_gaps, direct.source_gaps);
    assert.deepEqual(payload.query_coverage, direct.query_coverage);
    assert.equal(payload.chat.freshness, direct.freshness);
    assert.equal(payload.chat.status, state);
    assert.equal(payload.chat.no_match.status, "UNBOUNDED_NO_MATCH");
    assert.equal(payload.chat.no_match.answerable, false);
    assert.ok(payload.chat.gaps.length);
    const health = (await call("chat_turn", { quick_action: "no-data" })).payload;
    assert.equal(health.chat.status, state);
    assert.equal(health.chat.sources[0].source_health, state === "FAILED" ? "FAILED" : "PASS");
  }, values => {
    const source = values["source-status.json"].sources[0];
    if (state === "STALE") source.freshness_status = "STALE";
    if (state === "PARTIAL") source.window_completeness = "PARTIAL";
    if (state === "FAILED") { source.source_health = "FAILED"; source.window_completeness = "PARTIAL"; }
    if (state === "UNKNOWN") source.freshness_status = "UNKNOWN";
  }));
}

test("complete zero metadata matches remain bounded to indexed metadata", () => fixtureRun(async ({ call }) => {
  const payload = (await call("chat_turn", { text: "unmatched-fictional-term", quick_action: "official-evidence" })).payload;
  assert.equal(payload.chat.status, "BOUNDED_NO_MATCH");
  assert.equal(payload.chat.no_match.answerable, true);
  assert.match(payload.chat.no_match.scope, /metadata|中繼資料/);
  assert.match(payload.chat.no_match.message, /事件/);
}));

test("malformed chat input is INVALID_ARGUMENTS in both transports", () => fixtureRun(async ({ call, raw }) => {
  for (const args of [{}, { text: 3 }, { text: [] }, { text: " " }, { text: "a".repeat(513) },
    { quick_action: "unknown" }, { quick_action: {} }, { text: false, quick_action: "no-data" },
    { text: "查官方證據", context: [] }, { text: "查官方證據", context: { selected_region: 42 } },
    { text: "查官方證據", context: { selected_time_window: { time_from: "tomorrow" } } },
    { text: "查官方證據", context: { last_result_event_ids: [5] } }]) {
    for (const transport of ["query", "mcp"]) {
      const { status, isError, payload } = await call("chat_turn", args, transport);
      assert.equal(status, transport === "mcp" ? 200 : 400, JSON.stringify(args));
      assert.equal(isError, true); assert.equal(payload.error.code, "INVALID_ARGUMENTS");
    }
  }
  for (const body of ["{", "null", "[]", "1"]) for (const transport of ["query", "mcp"]) {
    const result = await raw(body, transport);
    assert.equal(result.status, 400);
    assert.equal(result.payload.error.code, "INVALID_ARGUMENTS");
  }
}));

test("unavailable chat stays unavailable when the publication itself is stale", () => fixtureRun(async ({ call }) => {
  for (const transport of ["query", "mcp"]) {
    const { status, payload } = await call("chat_turn", { quick_action: "weekend-events" }, transport);
    assert.equal(status, 200);
    assert.equal(payload.chat.status, "CAPABILITY_NOT_AVAILABLE");
    assert.equal(payload.freshness, "STALE");
    assert.equal(payload.chat.freshness, "STALE");
    assert.ok(payload.chat.gaps.some(gap => gap.reason === "STALE_SNAPSHOT"));
  }
}, values => {
  const stamp = new Date(Date.now() - 2 * 86_400_000).toISOString();
  for (const name of ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json"]) values[name].generated_at = stamp;
  values["v2-daily-brief.json"].source_status_generated_at = stamp;
}));

test("context narrowing never silently broadens unsupported filters or trusts a prior receipt", () => fixtureRun(async ({ call, fixture }) => {
  const sid = fixture.policy.active_source_ids[0];
  const context = { schema_version: 1, selected_agency: sid, transcript: "PRIVATE_TRANSCRIPT", tool: "query_statistics",
    last_query_receipt: { tool_name: "FAKE", publication_hash: "f".repeat(64), raw: "PRIVATE_RECEIPT" } };
  const payload = (await call("chat_turn", { quick_action: "official-evidence", context })).payload;
  assert.deepEqual(payload.chat.resolved_request.arguments, { source_id: sid });
  assert.ok(payload.chat.results.every(row => row.source_id === sid));
  assert.equal(payload.chat.context.selected_agency, sid);
  assert.equal(payload.chat.context.last_query_receipt.publication_hash, payload.publication_hash);
  assert.doesNotMatch(JSON.stringify(payload), /PRIVATE_|FAKE/);
  for (const filter of [{ selected_region: "西屯區" }, { selected_category: "traffic_control" },
    { selected_agency: "交通局" }, { selected_event_id: "EVENT-FICTIONAL" },
    { selected_time_window: { time_from: "2026-10-01", time_to: "2026-10-07" } }]) {
    const narrowed = (await call("chat_turn", { quick_action: "official-evidence", context: filter })).payload;
    assert.equal(narrowed.chat.status, "CAPABILITY_NOT_AVAILABLE");
    assert.deepEqual(narrowed.chat.results, []);
    assert.equal(narrowed.chat.query_receipt, null);
  }
}));

test("caller receipt identity is stable and Web/MCP chat semantics match", () => fixtureRun(async ({ call }) => {
  const args = { text: "測試官方標題", quick_action: "official-evidence" };
  const web = (await call("chat_turn", args)).payload;
  const mcp = (await call("chat_turn", args, "mcp")).payload;
  assert.equal(web.receipt.arguments_sha256, hash(canonical(args)));
  assert.equal(mcp.receipt.arguments_sha256, web.receipt.arguments_sha256);
  assert.equal(web.chat.query_receipt.arguments_sha256, hash(canonical({ q: args.text })));
  assert.equal(web.chat.query_receipt.publication_hash, web.receipt.publication_hash);
  assert.equal(web.chat.query_receipt.query_generation_id, web.receipt.query_generation_id);
  const stable = value => Array.isArray(value) ? value.map(stable) : value && typeof value === "object" ?
    Object.fromEntries(Object.entries(value).filter(([key]) => !["query_id", "queried_at", "issued_at"].includes(key)).map(([key, item]) => [key, stable(item)])) : value;
  assert.deepEqual(stable(web), stable(mcp));
  const other = (await call("chat_turn", { ...args, text: "測試官方標題 1" })).payload;
  assert.notEqual(other.receipt.arguments_sha256, web.receipt.arguments_sha256);
}));
