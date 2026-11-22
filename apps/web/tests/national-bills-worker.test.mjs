import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import test, { after } from "node:test";
import worker, { buildSnapshot, createReleaseManifest } from "../../../workers/query-gateway/src/index.js";

const sqlite = new DatabaseSync(":memory:");
sqlite.exec(await readFile(new URL("../../../national/schema.sql", import.meta.url), "utf8"));
sqlite.exec("INSERT INTO sources(source_id,provider,official_authority,runner,rights_class,license,robots_status,registry_sha256,updated_at) VALUES ('N-COUNCIL-TXG','fixture','fixture','fixture','METADATA_ONLY','fixture','fixture','fixture','fixture'), ('N-OTHER','fixture','fixture','fixture','METADATA_ONLY','fixture','fixture','fixture','fixture')");
const insert = sqlite.prepare("INSERT INTO records (id,source_id,kind,external_id,title,case_number,official_url,fetched_at,content_sha256,rights_class,proposer,cosigners,committee,outcome) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)");
for (const [id, term, session, proposer, outcome] of [
  [1, 4, 3, "王小明, 吳瓊華", "照案通過"], [2, 4, 8, "王小明", "照案通過"],
  [3, 4, 9, "吳瓊華", "退回"], [4, 4, 10, "吳瓊華", "照案通過"],
  [5, 5, 1, "王小明", "照案通過"], [6, 4, 8, null, null],
]) insert.run(id, "N-COUNCIL-TXG", "bill", String(id), "建請改善道路安全設施案", "第" + term + "屆 第" + session + "次 定期會 議警字第001號",
  "https://yishi.tccc.gov.tw/proposals/" + id, "2020-01-01T00:00:00Z", "hash" + id, "METADATA_ONLY",
  proposer, "張瀞分, 陳大明", "警消環衛委員會-警察", outcome);
insert.run(7, "N-OTHER", "bill", "7", "建請改善道路安全設施案", "第9屆 第9次", "https://example.org/", "2020-01-01T00:00:00Z", "hash7", "METADATA_ONLY", "王小明", null, null, null);

const db = { prepare(sql) { return { bind(...values) { return {
  all: async () => ({ success: true, results: sqlite.prepare(sql).all(...values) }),
  first: async () => sqlite.prepare(sql).get(...values) ?? null,
}; } }; } };
const env = { PUBLIC_ORIGIN: "https://publication.example", CF_VERSION_METADATA: { id: "national-bill-test", tag: "a".repeat(40) }, NATIONAL_DB: db };
const names = ["intelligence-feed.json", "source-status.json", "v2-daily-brief.json", "source-policy.json"];
const bytes = Object.fromEntries(await Promise.all(names.map(async name => [name, await readFile(new URL("../public/data/" + name, import.meta.url))])));
const snapshot = await buildSnapshot(env, async name => ({ bytes: bytes[name], hash: createHash("sha256").update(bytes[name]).digest("hex") }));
const release = await createReleaseManifest(snapshot, env.CF_VERSION_METADATA.tag);
const originalFetch = globalThis.fetch;
globalThis.fetch = async url => {
  const name = String(url).split("/").at(-1);
  return name === "release.json" ? Response.json(release) : new Response(bytes[name], { status: bytes[name] ? 200 : 404 });
};
after(() => { globalThis.fetch = originalFetch; sqlite.close(); });
let seq = 0;
async function call(name, args) {
  const response = await worker.fetch(new Request("https://gateway.example/mcp", { method: "POST",
    headers: { "Content-Type": "application/json", "CF-Connecting-IP": "national-" + ++seq },
    body: JSON.stringify({ jsonrpc: "2.0", id: seq, method: "tools/call", params: { name, arguments: args } }) }), env);
  assert.equal(response.status, 200);
  const result = (await response.json()).result;
  assert.equal(result.isError, false, result.content?.[0]?.text);
  return result.structuredContent;
}

test("MCP bill search filters and numeric session ordering across cursors", async () => {
  const all = await call("search_council_bills", { q: "道路安全" });
  assert.deepEqual(all.records.map(row => row.evidence_id), ["NAT-5", "NAT-4", "NAT-3", "NAT-6", "NAT-2", "NAT-1"]);
  assert.match(all.note, /no official date/i);
  assert.deepEqual((await call("search_council_bills", { proposer: "王小" })).records.map(row => row.evidence_id), ["NAT-5", "NAT-2", "NAT-1"]);
  assert.deepEqual((await call("search_council_bills", { proposer: "吳瓊華" })).records.map(row => row.evidence_id), ["NAT-4", "NAT-3", "NAT-1"]);
  assert.deepEqual((await call("search_council_bills", { session: "第4屆 第8次", outcome: "通過" })).records.map(row => row.evidence_id), ["NAT-2"]);
  const first = await call("search_council_bills", { limit: 2 });
  const second = await call("search_council_bills", { limit: 2, cursor: first.next_cursor });
  assert.deepEqual(first.records.map(row => row.evidence_id), ["NAT-5", "NAT-4"]);
  assert.deepEqual(second.records.map(row => row.evidence_id), ["NAT-3", "NAT-6"]);
});

test("national HTTP proposer filters use a source bound and return public fields", async () => {
  const request = path => worker.fetch(new Request("https://gateway.example" + path, { headers: { "CF-Connecting-IP": "national-" + ++seq } }), env);
  assert.equal((await request("/national/search?proposer=王小")).status, 400);
  const body = await (await request("/national/search?source_id=N-COUNCIL-TXG&proposer=王小")).json();
  assert.deepEqual(body.records.map(row => row.id), [5, 2, 1]);
  assert.equal(body.records[0].proposer, "王小明");
  const indexed = await (await request("/national/search?source_id=N-COUNCIL-TXG&proposer=吳瓊華")).json();
  assert.deepEqual(indexed.records.map(row => row.id), [4, 3, 1]);
});

test("national claims verify field values, report fetch time, and block mismatches and unknown IDs", async () => {
  const claim = (id, field, value) => ({ claim_id: field + id, text: "不應直接回傳此文字", claim_type: "STATUS",
    proposition: { subject: "national:" + id + ":" + field, value }, cited_evidence_ids: ["NAT-" + id] });
  for (const [field, value] of [["title", "改善道路安全設施"], ["proposer", "吳瓊華"], ["cosigners", "陳大明, 張瀞分"],
    ["case_number", "第4屆 第3次 定期會 議警字第001號"], ["committee", "警消環衛委員會-警察"],
    ["outcome", "照案通過"], ["official_url", "https://yishi.tccc.gov.tw/proposals/1"]]) {
    const result = await call("validate_answer", { claims: [claim(1, field, value)] });
    assert.equal(result.gate_status, "PASS", field);
    assert.equal(result.final_claims[0].validation_status, "PASS");
    assert.equal(result.final_claims[0].data_basis.fetched_at, "2020-01-01T00:00:00Z");
    assert.notEqual(result.final_claims[0].support_status, "STALE");
    assert.ok(!result.answer.join("").includes("不應直接回傳此文字"));
  }
  for (const item of [claim(1, "title", "道路安全"), claim(1, "proposer", "吳瓊"), claim(1, "outcome", "未通過")]) {
    const result = await call("validate_answer", { claims: [item] });
    assert.equal(result.gate_status, "BLOCKED");
    assert.equal(result.final_claims[0].reason_code, "VALUE_MISMATCH");
  }
  const missing = await call("validate_answer", { claims: [claim(999, "title", "不存在的議案標題")] });
  assert.equal(missing.final_claims[0].validation_status, "NO_EVIDENCE");
});

test("mixed publication and national claims preserve the publication gate result", async () => {
  const item = snapshot.items.find(row => row.source_role === "PRIMARY_OFFICIAL");
  assert.ok(item);
  const publication = { claim_id: "pub", text: "publication", claim_type: "STATUS",
    proposition: { subject: "publication:" + item.canonical_id + ":title", value: item.title },
    cited_evidence_ids: ["PUB-" + item.canonical_id] };
  const national = { claim_id: "nat", text: "bill", claim_type: "STATUS",
    proposition: { subject: "national:1:title", value: "建請改善道路安全設施案" }, cited_evidence_ids: ["NAT-1"] };
  const one = await call("validate_answer", { claims: [publication] });
  const both = await call("validate_answer", { claims: [publication, national] });
  assert.deepEqual(both.answer_evidence_receipt.claims[0], one.answer_evidence_receipt.claims[0]);
  assert.deepEqual(both.final_claims[0], one.final_claims[0]);
  assert.equal(both.final_claims[1].validation_status, "PASS");
  const rejected = await call("validate_answer", { claims: [publication, { ...national,
    proposition: { ...national.proposition, value: "錯誤案由" } }] });
  assert.equal(rejected.gate_status, "BLOCKED");
  assert.deepEqual(rejected.answer, []);
});

test("MCP advertises the bill tool, validates bounds and fails closed without D1", async () => {
  async function rpc(input, binding = env) {
    const response = await worker.fetch(new Request("https://gateway.example/mcp", { method: "POST",
      headers: { "Content-Type": "application/json", "CF-Connecting-IP": "national-" + ++seq },
      body: JSON.stringify({ jsonrpc: "2.0", id: seq, ...input }) }), binding);
    return (await response.json()).result;
  }
  const listed = await rpc({ method: "tools/list" });
  assert.ok(listed.tools.some(tool => tool.name === "search_council_bills" && tool.annotations.readOnlyHint));
  for (const args of [{ limit: 0 }, { limit: 21 }, { cursor: "!" }, { proposer: "人".repeat(51) }, { unexpected: true }]) {
    const result = await rpc({ method: "tools/call", params: { name: "search_council_bills", arguments: args } });
    assert.equal(JSON.parse(result.content[0].text).error.code, "INVALID_ARGUMENTS");
  }
  const withoutD1 = { ...env, NATIONAL_DB: undefined };
  const claim = { text: "議案", claim_type: "STATUS", proposition: { subject: "national:1:title", value: "議案" }, cited_evidence_ids: ["NAT-1"] };
  for (const [name, args] of [["search_council_bills", {}], ["validate_answer", { claims: [claim] }]]) {
    const result = await rpc({ method: "tools/call", params: { name, arguments: args } }, withoutD1);
    assert.equal(JSON.parse(result.content[0].text).error.code, "CAPABILITY_NOT_AVAILABLE");
  }
});

test("MCP keeps its 256 KiB response cap for unusually large bill fields", async () => {
  sqlite.prepare("UPDATE records SET outcome=? WHERE id=6").run("過".repeat(50000));
  try {
    const response = await worker.fetch(new Request("https://gateway.example/mcp", { method: "POST",
      headers: { "Content-Type": "application/json", "CF-Connecting-IP": "national-" + ++seq },
      body: JSON.stringify({ jsonrpc: "2.0", id: seq, method: "tools/call",
        params: { name: "search_council_bills", arguments: { limit: 20 } } }) }), env);
    const text = await response.text();
    assert.equal(response.status, 500);
    assert.equal(JSON.parse(text).error.code, "RESPONSE_TOO_LARGE");
    assert.ok(new TextEncoder().encode(text).byteLength <= 256 * 1024);
  } finally {
    sqlite.prepare("UPDATE records SET outcome=NULL WHERE id=6").run();
  }
});
