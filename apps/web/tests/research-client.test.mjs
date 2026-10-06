import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { createHash } from "node:crypto";
import { buildResearchRequest, researchEndpoint, researchDate, researchExplanation, requestResearch, validateResearchResponse, verifyResearchDocumentHashes } from "../lib/research-client.js";

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
  assert.deepEqual(buildResearchRequest(" 交通 ", [], true), { question: "交通", history: [], public_data_only: true, mode: "metadata" });
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
  assert.deepEqual(JSON.parse(calls[1][1].body), { question: "交通", history: [], public_data_only: true, mode: "metadata", release_id: release.release_id });
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

// Fictional OFFLINE client contract fixture, never a rights grant or live source.
function documentResponse(mode = "documents") {
  const quote = "離線測試：文件記載交通項目。";
  const byteLength = new TextEncoder().encode(quote).byteLength;
  const citation = {
    schema_version: 1, source_id: "OFFLINE-SOURCE", document_id: "OFFLINE-DOC", document_version: "v1",
    source_role: "PRIMARY_REFERENCE", evidence_type: "WRITTEN_OFFICIAL", parser_version: "offline-text-v1",
    official_url: "https://offline.example.test/document", requested_url: "https://offline.example.test/document", origin: "https://offline.example.test",
    published_at: "2026-10-01T01:00:00Z", fetched_at: "2026-10-05T02:00:00Z", passage_id: "PASSAGE-offline",
    generation_id: "DOCGEN-offline", offset_basis: "EXACT_TEXT_UTF16_AND_UTF8_END_EXCLUSIVE",
    start_utf16: 0, end_utf16: quote.length, start_utf8: 0, end_utf8: byteLength,
    quote_start_utf16: 0, quote_end_utf16: quote.length, quote_start_utf8: 0, quote_end_utf8: byteLength,
    content_sha256: "1".repeat(64), document_binding_sha256: "2".repeat(64), passage_sha256: "3".repeat(64), quote_sha256: "4".repeat(64), citation_sha256: "5".repeat(64),
  };
  const compilation = {
    schema_version: 1, validator_version: "document-evidence/1", kind: "EXTRACTIVE_EVIDENCE_COMPILATION", gate_status: "QUALIFIED",
    publication_tier: "RESEARCH_ONLY", semantic_verification: "NOT_PERFORMED", can_assert_current: false, can_state_zero_events: false,
    model_transmission_allowed: false, source_health: "NOT_ASSESSED", window_completeness: "UNKNOWN", generation_id: "DOCGEN-offline",
    compiled_at: "2026-10-05T03:00:00Z", limitation: "離線測試，摘錄不是語意驗證。", source_policy_hash: "6".repeat(64), permission_policy_hash: "7".repeat(64),
    corpus_sha256: "8".repeat(64), draft_sha256: "9".repeat(64), compilation_sha256: "a".repeat(64),
    excerpts: [{ quote, support_status: "EXACT_QUOTE_ONLY", citation }], gaps: [{ code: "COVERAGE_NOT_ESTABLISHED" }],
  };
  const response = { ...structuredClone(metadata), mode, status: "EXTRACTS_READY", reason_code: null, provider_transmission_attempted: false, document_evidence: compilation,
    sources: [{ evidence_id: `DOC-${citation.document_binding_sha256}`, source_id: citation.source_id, title: "OFFLINE-DOC", official_url: citation.official_url, published_at: citation.published_at, fetched_at: citation.fetched_at, data_as_of: null }] };
  if (mode === "synthesis") {
    response.status = "SYNTHESIS_DRAFT"; response.provider.state = "READY"; response.provider_transmission_attempted = true;
    response.synthesis = {
      schema_version: 1, semantic_verification: "AI_REVIEWED_NOT_FORMALLY_VERIFIED", publication_tier: "RESEARCH_ONLY", gate_status: "QUALIFIED",
      can_assert_current: false, can_state_zero_events: false, limitation: "引用已核對，模型語意仍需人工核對。",
      claims: [{ claim_id: "CLAIM-1", text: "文件提及交通項目，時效仍待確認。", temporal_scope: "HISTORICAL_OR_UNDATED", citations: [{ quote, citation: structuredClone(citation) }] }],
      held_claims: [{ claim_id: "CLAIM-2", reason_code: "CRITIC_INSUFFICIENT" }],
      receipt: { schema_version: 1, validator_version: "document-research/1", generation_id: compilation.generation_id, source_policy_hash: compilation.source_policy_hash,
        permission_policy_hash: compilation.permission_policy_hash, corpus_sha256: compilation.corpus_sha256, evidence_compilation_sha256: compilation.compilation_sha256,
        producer_run_id: "offline-producer", critic_run_id: "offline-critic", producer_model: "MiniMax-M2.7", critic_model: "MiniMax-M2.7",
        producer_prompt_version: "document-producer/1", critic_prompt_version: "document-critic/1", producer_output_sha256: "b".repeat(64),
        critic_output_sha256: "c".repeat(64), claims_sha256: "d".repeat(64), receipt_sha256: "e".repeat(64), generated_at: compilation.compiled_at },
    };
  }
  return response;
}

test("research mode is explicit, bounded and defaults to metadata", () => {
  assert.equal(buildResearchRequest("交通", [], true).mode, "metadata");
  for (const mode of ["documents", "synthesis"]) assert.equal(buildResearchRequest("交通", [], true, mode).mode, mode);
  for (const mode of [null, "fulltext", "metadata ", 2]) assert.throws(() => buildResearchRequest("交通", [], true, mode), error => error.code === "INVALID_MODE");
  assert.equal(buildResearchRequest("文".repeat(171), [], true).question.length, 171);
  for (const mode of ["documents", "synthesis"]) {
    assert.equal(buildResearchRequest("文".repeat(170), [], true, mode).mode, mode);
    assert.throws(() => buildResearchRequest("文".repeat(171), [], true, mode), error => error.code === "DOCUMENT_QUERY_TOO_LARGE" && error.message.includes("UTF-8"));
  }
});

test("document and synthesis results are separate from formal answer-ready output", () => {
  for (const mode of ["documents", "synthesis"]) {
    const response = documentResponse(mode);
    assert.equal(validateResearchResponse(response, "交通", mode), response);
    assert.deepEqual(response.answer, []);
    assert.throws(() => validateResearchResponse({ ...response, status: "ANSWER_READY", answer: ["fake formal answer"] }, "交通", mode), error => error.code === "INVALID_RESPONSE");
    assert.throws(() => validateResearchResponse(response, "交通", "metadata"), error => error.code === "INVALID_RESPONSE");
    assert.throws(() => validateResearchResponse({ ...response, answer: ["smuggled prose"] }, "交通", mode), error => error.code === "INVALID_RESPONSE");
  }
});

test("document citations must bind a known source, immutable document version, generation and exact quote offsets", () => {
  const changes = [
    response => { response.sources[0].evidence_id = "DOC-UNKNOWN-v1"; },
    response => { response.sources[0].source_id = "UNKNOWN"; },
    response => { response.document_evidence.excerpts[0].citation.generation_id = "wrong-generation"; },
    response => { response.document_evidence.excerpts[0].citation.official_url = "https://attacker.example.test/"; },
    response => { response.document_evidence.excerpts[0].citation.quote_end_utf16++; },
    response => { response.document_evidence.excerpts[0].citation.quote_end_utf8++; },
    response => { response.document_evidence.excerpts[0].citation.quote_sha256 = "not-a-hash"; },
    response => { response.document_evidence.excerpts[0].support_status = "AUTO_PASS"; },
    response => { response.document_evidence.can_assert_current = true; },
    response => { response.document_evidence.can_state_zero_events = true; },
    response => { response.document_evidence.semantic_verification = "VERIFIED"; },
    response => { response.document_evidence.model_transmission_allowed = true; },
    response => { response.document_evidence.excerpts.push(structuredClone(response.document_evidence.excerpts[0])); },
  ];
  for (const mutate of changes) { const response = documentResponse(); mutate(response); assert.throws(() => validateResearchResponse(response, "交通", "documents"), error => error.code === "INVALID_RESPONSE"); }
});

test("synthesis cannot change a quote, citation, review receipt, temporal scope or formal tier", () => {
  const changes = [
    response => { response.synthesis.claims[0].citations[0].quote = "different quotation"; },
    response => { response.synthesis.claims[0].citations[0].citation.source_id = "UNKNOWN"; },
    response => { response.synthesis.claims[0].citations[0].citation.official_url = "https://attacker.example.test/"; },
    response => { response.synthesis.claims[0].citations[0].citation.generation_id = "other"; },
    response => { response.synthesis.receipt.evidence_compilation_sha256 = "f".repeat(64); },
    response => { response.synthesis.receipt.generation_id = "other"; },
    response => { response.synthesis.receipt.critic_run_id = response.synthesis.receipt.producer_run_id; },
    response => { response.synthesis.claims[0].temporal_scope = "CURRENT"; },
    response => { response.synthesis.claims[0].citations = []; },
    response => { response.synthesis.held_claims[0].claim_id = "CLAIM-1"; },
    response => { response.synthesis.publication_tier = "FORMAL"; },
    response => { response.synthesis.gate_status = "AUTO_PASS"; },
    response => { response.provider_transmission_attempted = false; },
  ];
  for (const mutate of changes) { const response = documentResponse("synthesis"); mutate(response); assert.throws(() => validateResearchResponse(response, "交通", "synthesis"), error => error.code === "INVALID_RESPONSE"); }
});

test("mode mismatch, missing transport state and silent fallback all fail closed", () => {
  const documents = documentResponse();
  assert.throws(() => validateResearchResponse({ ...documents, mode: undefined }, "交通", "documents"), error => error.code === "INVALID_RESPONSE");
  assert.throws(() => validateResearchResponse({ ...documents, provider_transmission_attempted: undefined }, "交通", "documents"), error => error.code === "INVALID_RESPONSE");
  assert.throws(() => validateResearchResponse({ ...documents, provider_transmission_attempted: true }, "交通", "documents"), error => error.code === "INVALID_RESPONSE");
  assert.throws(() => validateResearchResponse({ ...metadata, mode: "documents", provider_transmission_attempted: false }, "交通", "documents"), error => error.code === "INVALID_RESPONSE");
  const blocked = { ...metadata, mode: "documents", provider_transmission_attempted: false, sources: [], reason_code: "DOCUMENTS_UNCONFIGURED" };
  assert.equal(validateResearchResponse(blocked, "交通", "documents"), blocked);
  const extracts = documentResponse("synthesis"); delete extracts.synthesis; extracts.status = "EXTRACTS_READY"; extracts.reason_code = "SYNTHESIS_REVIEW_FAILED";
  assert.equal(validateResearchResponse(extracts, "交通", "synthesis"), extracts);
});

test("recorded provider transmission outranks setup or rights wording after a failed stage", () => {
  for (const reason_code of ["ADMISSION_DENIED", "REVIEW_ADMISSION_DENIED", "SYNTHESIS_REVIEW_FAILED", "DOCUMENT_RIGHTS_BLOCKED", "INPUT_RESTRICTED"]) {
    const response = { ...documentResponse("synthesis"), status: "EXTRACTS_READY", reason_code };
    assert.doesNotMatch(researchExplanation(response), /未送至|未將.*送至|未嘗試傳送/);
  }
  assert.match(researchExplanation(documentResponse("synthesis")), /引用已核對，模型語意仍需人工核對/);
  assert.match(researchExplanation(documentResponse()), /未呼叫模型/);
});

test("request carries exact selected mode and rejects a differently bound response", async () => {
  for (const mode of ["documents", "synthesis"]) {
    const calls = [];
    const response = await requestResearch("/query", { ...input, mode }, { ...context, fetchImpl: client(hashedDocumentResponse(mode), calls) });
    assert.equal(response.mode, mode);
    assert.equal(JSON.parse(calls[1][1].body).mode, mode);
  }
  await assert.rejects(requestResearch("/query", { ...input, mode: "documents" }, { ...context, fetchImpl: client(documentResponse("synthesis")) }), error => error.code === "INVALID_RESPONSE");
});

test("single conversation entry has a replace-only /ask alias and no legacy gateway or storage", async () => {
  const alias = await readFile(new URL("../app/ask/page.js", import.meta.url), "utf8");
  const nav = await readFile(new URL("../components/GovIntelFrame.js", import.meta.url), "utf8");
  const page = await readFile(new URL("../app/research/research-chat.js", import.meta.url), "utf8");
  const evidence = await readFile(new URL("../app/research/evidence-results.js", import.meta.url), "utf8");
  assert.match(alias, /router\.replace\("\/research\/"\)/);
  assert.match(alias, /<Link href="\/research\/" replace>/);
  assert.doesNotMatch(alias, /router\.push|chat_turn|fetch\(|localStorage|sessionStorage/);
  assert.equal((nav.match(/\["\/research\/"/g) || []).length, 1);
  assert.doesNotMatch(nav, /\["\/ask\/"/);
  assert.match(page, /useState\("metadata"\)/);
  assert.match(page, /htmlFor="research-mode"/);
  assert.match(page, /setMode\(event\.target\.value\); setConsent\(false\)/);
  assert.match(page, /setMode\("metadata"\); setConsent\(false\)/);
  assert.match(evidence, /引用已核對，模型語意仍需人工核對/);
  assert.match(evidence, /<p>\{excerpt\.quote\}<\/p>/);
  assert.match(evidence, /<p className="research-claim-text">\{claim\.text\}<\/p>/);
  assert.doesNotMatch(evidence, /dangerouslySetInnerHTML|marked\(|Markdown|AUTO_PASS|已驗證答案/);
});


function hashedDocumentResponse(mode = "documents") {
  const payload = documentResponse(mode);
  const canonical = value => Array.isArray(value) ? `[${value.map(canonical).join(",")}]` : value !== null && typeof value === "object" ? `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}` : JSON.stringify(value);
  const digest = value => createHash("sha256").update(value).digest("hex");
  const citation = payload.document_evidence.excerpts[0].citation;
  const metadataKeys = ["schema_version", "document_id", "document_version", "source_id", "evidence_type", "requested_url", "official_url", "origin", "published_at", "fetched_at", "content_sha256", "parser_version"];
  citation.document_binding_sha256 = digest(canonical(Object.fromEntries(metadataKeys.map(key => [key, citation[key]]))));
  citation.quote_sha256 = digest(payload.document_evidence.excerpts[0].quote);
  const { citation_sha256: _citationHash, ...citationCore } = citation;
  citation.citation_sha256 = digest(canonical(citationCore));
  payload.sources[0].evidence_id = `DOC-${citation.document_binding_sha256}`;
  const { compilation_sha256: _compilationHash, ...compilationCore } = payload.document_evidence;
  payload.document_evidence.compilation_sha256 = digest(canonical(compilationCore));
  if (payload.synthesis) {
    payload.synthesis.claims[0].citations[0].citation = structuredClone(citation);
    payload.synthesis.receipt.evidence_compilation_sha256 = payload.document_evidence.compilation_sha256;
    payload.synthesis.receipt.claims_sha256 = digest(canonical(payload.synthesis.claims));
    const { receipt_sha256: _receiptHash, ...receiptCore } = payload.synthesis.receipt;
    payload.synthesis.receipt.receipt_sha256 = digest(canonical(receiptCore));
  }
  return payload;
}

test("transport hash verification binds document IDs and versions, exact quotes, compilations and synthesis receipts", async () => {
  for (const mode of ["documents", "synthesis"]) {
    const payload = hashedDocumentResponse(mode);
    assert.equal(await verifyResearchDocumentHashes(payload), payload);
  }
  const mutations = [
    payload => { payload.document_evidence.excerpts[0].citation.document_version = "forged-version"; },
    payload => { payload.document_evidence.excerpts[0].citation.document_id = "forged-document"; },
    payload => { payload.document_evidence.excerpts[0].quote = "變更字數相同也不應通過引用。"; },
    payload => { payload.document_evidence.excerpts[0].citation.source_role = "FORGED_ROLE"; },
    payload => { payload.document_evidence.limitation = "forged-coverage"; },
    payload => { payload.synthesis.claims[0].text = "forged claim with copied hash"; },
    payload => { payload.synthesis.receipt.critic_output_sha256 = "f".repeat(64); },
  ];
  for (const mutate of mutations) {
    const payload = hashedDocumentResponse("synthesis"); mutate(payload);
    await assert.rejects(verifyResearchDocumentHashes(payload), error => error.code === "INVALID_RESPONSE");
  }
});

test("a copied binding hash beside an altered document version never reaches the UI transport result", async () => {
  const payload = hashedDocumentResponse(); payload.document_evidence.excerpts[0].citation.document_version = "forged-version";
  await assert.rejects(requestResearch("/query", { ...input, mode: "documents" }, { ...context, fetchImpl: client(payload) }), error => error.code === "INVALID_RESPONSE");
});

test("held-all reviews keep an empty claim list with a bound review receipt, never withheld prose", () => {
  const payload = documentResponse("synthesis"); payload.status = "EXTRACTS_READY"; payload.reason_code = "NO_SUPPORTED_CLAIMS";
  payload.synthesis.gate_status = "BLOCKED"; payload.synthesis.claims = [];
  assert.equal(validateResearchResponse(payload, "交通", "synthesis"), payload);
  payload.synthesis.claims = [{ claim_id: "withheld", text: "must not render" }];
  assert.throws(() => validateResearchResponse(payload, "交通", "synthesis"), error => error.code === "INVALID_RESPONSE");
});
