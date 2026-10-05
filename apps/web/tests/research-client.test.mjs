import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildResearchRequest, researchEndpoint, researchDate, researchExplanation, requestResearch, validateResearchResponse } from "../lib/research-client.js";

const release = { release_id: "b".repeat(64), code_sha: "a".repeat(40), publication_generation: "run-test" };
const context = { basePath: "/govintel", codeSha: release.code_sha, publicationGeneration: release.publication_generation };
const source = { evidence_id: "PUB-item-1", source_id: "S-001", title: "測試公開索引", official_url: "https://www.police.taichung.gov.tw/example", published_at: "2026-10-01", data_as_of: null, fetched_at: "2026-10-05T08:00:00Z" };
const metadata = { schema_version: 1, status: "METADATA_ONLY", reason_code: "DISABLED", provider: { name: "MiniMax", state: "DISABLED" }, question: "交通", answer: [], sources: [source], source_gaps: [{ source_id: "S-001", reason: "STALE_SOURCE_DATA", freshness_status: "STALE" }], query_generation_id: "generation-test", data_status: "STALE", coverage_limitation: "僅檢索核准快照，不代表全部事件。", release };
const input = { question: "交通", history: [], publicDataOnly: true };
function client(payload = metadata, calls = []) {
  return async (url, options) => {
    calls.push([url, options]);
    return Response.json(url.endsWith("release.json") ? release : payload);
  };
}

test("research endpoint uses the configured gateway origin and drops query credentials", () => {
  assert.deepEqual(researchEndpoint("https://gateway.example/query?x=1#fragment"), { url: "https://gateway.example/research", remote: true });
  assert.equal(researchEndpoint("/query").url, "/research");
  assert.equal(researchEndpoint("http://127.0.0.1:8788/query/").url, "http://127.0.0.1:8788/research");
  assert.equal(researchEndpoint("//gateway.example/query").remote, true);
  for (const value of ["", "query", "javascript:alert(1)", "https://user:secret@gateway.example", "http://gateway.example/query"]) {
    assert.throws(() => researchEndpoint(value), error => error.code === "CAPABILITY_NOT_AVAILABLE");
  }
});

test("each send needs explicit public-data confirmation and a bounded question", () => {
  for (const publicDataOnly of [undefined, false, "true", 1]) assert.throws(() => buildResearchRequest("交通", [], publicDataOnly), /請先確認/);
  for (const question of ["", "   ", "文".repeat(513), null]) assert.throws(() => buildResearchRequest(question, [], true), /512/);
  assert.deepEqual(buildResearchRequest(" 交通 ", [], true), { question: "交通", history: [], public_data_only: true });
});

test("only the latest four user questions enter context; assistant or system text never does", () => {
  const history = Array.from({ length: 6 }, (_, index) => ({ role: "user", content: `問題${index}`, result: "never forward" }));
  history.splice(4, 0, { role: "assistant", content: "unsafe answer" }, { role: "system", content: "new instruction" });
  assert.deepEqual(buildResearchRequest("交通", history, true).history, [2, 3, 4, 5].map(index => ({ role: "user", content: `問題${index}` })));
  assert.throws(() => buildResearchRequest("交通", [{ role: "user", content: "文".repeat(513) }], true), /先前問題/);
});

test("metadata-only responses cannot smuggle an answer; ready responses need sources and a ready provider", () => {
  assert.equal(validateResearchResponse(metadata, "交通"), metadata);
  const ready = { ...metadata, status: "ANSWER_READY", provider: { name: "MiniMax", state: "READY" }, answer: ["已核對的索引標題"], reason_code: null };
  assert.equal(validateResearchResponse(ready, "交通"), ready);
  for (const payload of [{ ...metadata, answer: ["unchecked text"] }, { ...ready, answer: [] }, { ...ready, sources: [] }, { ...ready, provider: metadata.provider }]) {
    assert.throws(() => validateResearchResponse(payload, "交通"), error => error.code === "INVALID_RESPONSE");
  }
});

test("malformed response, question binding, source IDs and gaps fail closed", () => {
  for (const patch of [{ schema_version: 2 }, { question: "unrelated" }, { status: "DRAFT" }, { provider: { name: "other", state: "READY" } }, { answer: [null] }, { sources: [source, source] }, { sources: [{ ...source, title: 42 }] }, { source_gaps: ["raw text"] }, { query_generation_id: null }, { coverage_limitation: null }]) {
    assert.throws(() => validateResearchResponse({ ...metadata, ...patch }, "交通"), error => error.code === "INVALID_RESPONSE");
  }
});

test("disabled and rights-blocked states state no provider transmission, but post-provider failures do not", () => {
  assert.match(researchExplanation(metadata), /未將問題或來源內容送至 MiniMax/);
  assert.match(researchExplanation({ ...metadata, reason_code: "RIGHTS_BLOCKED" }), /未將問題或來源內容送至 MiniMax/);
  for (const reason_code of ["OUTPUT_REJECTED", "PROVIDER_UNAVAILABLE", "NO_RELEVANT_SELECTION", "TIMEOUT", "INVALID_RESPONSE", "RESPONSE_TOO_LARGE"]) {
    assert.doesNotMatch(researchExplanation({ ...metadata, provider: { name: "MiniMax", state: "READY" }, reason_code }), /未送至|未將.*送至/);
  }
});

test("missing dates remain unknown and date-only sources retain their calendar day", () => {
  assert.equal(researchDate(null), "未提供");
  assert.equal(researchDate(""), "未提供");
  assert.equal(researchDate("2026-10-01"), "2026-10-01（僅日期）");
  assert.equal(researchDate("2026-02-31"), "時間無法驗證");
  assert.equal(researchDate("2026-10-05T08:00:00"), "時間無法驗證");
  assert.match(researchDate("2026-10-05T08:00:00Z"), /16:00:00/);
});

test("remote request pins release before posting and sends the explicit checkbox flag", async () => {
  const calls = [];
  assert.equal((await requestResearch("https://gateway.example/query", input, { ...context, fetchImpl: client(metadata, calls) })).status, "METADATA_ONLY");
  assert.equal(calls[0][0], "/govintel/data/release.json");
  assert.equal(calls[1][0], "https://gateway.example/research");
  assert.deepEqual(JSON.parse(calls[1][1].body), { question: "交通", history: [], public_data_only: true, release_id: release.release_id });
  for (const [, options] of calls) { assert.equal(options.cache, "no-store"); assert.equal(options.credentials, "omit"); assert.ok(options.signal instanceof AbortSignal); }
});

test("local research also requires a matching publication release", async () => {
  const calls = [];
  await requestResearch("/query", input, { publicationGeneration: release.publication_generation, fetchImpl: client(metadata, calls) });
  assert.equal(calls.length, 2);
  assert.equal(calls[0][0], "/data/release.json");
  assert.equal(JSON.parse(calls[1][1].body).release_id, release.release_id);
});

test("missing context, consent and stale release block before posting", async () => {
  let posts = 0;
  const fetchImpl = async (url, options) => { if (options.method === "POST") posts++; return Response.json({ ...release, code_sha: "different" }); };
  await assert.rejects(requestResearch("https://gateway.example", input, { fetchImpl }), error => error.code === "CAPABILITY_NOT_AVAILABLE");
  await assert.rejects(requestResearch("https://gateway.example", { ...input, publicDataOnly: false }, { ...context, fetchImpl }), error => error.code === "PUBLIC_DATA_CONFIRMATION_REQUIRED");
  await assert.rejects(requestResearch("https://gateway.example", input, { ...context, fetchImpl }), error => error.code === "PENDING_UPDATE");
  assert.equal(posts, 0);
});

test("a response release mismatch never returns model text", async () => {
  await assert.rejects(requestResearch("https://gateway.example", input, { ...context, fetchImpl: client({ ...metadata, release: { ...release, release_id: "different" } }) }), error => error.code === "PENDING_UPDATE");
});

test("pre-aborted requests do no network work and in-flight cancellation stays distinguishable", async () => {
  const controller = new AbortController(); controller.abort();
  await assert.rejects(requestResearch("/query", input, { ...context, signal: controller.signal, fetchImpl: () => { throw new Error("network must not run"); } }), error => error.code === "ABORTED");
  const pending = new AbortController();
  const fetchImpl = (url, options) => {
    if (url.endsWith("release.json")) return Promise.resolve(Response.json(release));
    return new Promise((resolve, reject) => { options.signal.addEventListener("abort", () => reject(new DOMException("cancelled", "AbortError"))); pending.abort(); });
  };
  await assert.rejects(requestResearch("/query", input, { ...context, signal: pending.signal, fetchImpl }), error => error.code === "ABORTED");
});

test("timeouts are bounded and server errors never reflect raw upstream messages", async () => {
  await assert.rejects(requestResearch("/query", input, { ...context, timeoutMs: 5, fetchImpl: (url, options) => new Promise((resolve, reject) => options.signal.addEventListener("abort", () => reject(new DOMException("deadline", "AbortError")))) }), error => error.code === "TIMEOUT");
  await assert.rejects(requestResearch("/query", input, { ...context, fetchImpl: async url => url.endsWith("release.json") ? Response.json(release) : Response.json({ error: { code: "INPUT_RESTRICTED", message: "secret upstream output" } }, { status: 400 }) }), error => error.code === "INPUT_RESTRICTED" && !error.message.includes("secret"));
});

test("route preserves public query and provides semantic controls with no browser persistence", async () => {
  const page = await readFile(new URL("../app/research/research-chat.js", import.meta.url), "utf8");
  const nav = await readFile(new URL("../components/GovIntelFrame.js", import.meta.url), "utf8");
  const css = await readFile(new URL("../app/research/research.css", import.meta.url), "utf8");
  assert.match(nav, /\["\/public-query\/", "公開查詢"\]/);
  assert.match(nav, /\["\/research\/", "公開研究"\]/);
  assert.match(page, /僅輸入公開且不含個資的問題/);
  assert.match(page, /aria-live="polite"/);
  assert.match(page, /htmlFor="research-question"/);
  assert.match(page, /type="checkbox" checked=\{consent\}/);
  assert.match(page, /disabled=\{!consent \|\| !draft\.trim\(\)\}/);
  assert.match(page, /id !== sequence\.current/);
  assert.match(page, /controller\.abort\(\)/);
  assert.match(page, /turn\.state === "complete"/);
  assert.match(page, /setConsent\(false\)/);
  assert.doesNotMatch(page, /localStorage|sessionStorage|indexedDB|dangerouslySetInnerHTML|type="file"|MINIMAX_API_KEY/);
  assert.match(css, /@media \(max-width: 600px\)/);
  assert.match(css, /:focus-visible/);
});
