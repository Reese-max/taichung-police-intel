// Browser-side transport only. Provider credentials and model calls stay on the Gateway.
export const RESEARCH_QUESTION_LIMIT = 512;
export const RESEARCH_HISTORY_LIMIT = 4;
export const RESEARCH_MODES = Object.freeze(["metadata", "documents", "synthesis"]);
const PROVIDER_STATES = new Set(["DISABLED", "KEY_MISSING", "BILLING_REVIEW_REQUIRED", "ADMISSION_UNCONFIGURED", "READY"]);
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

function problem(message, code) { return Object.assign(new Error(message), { code }); }
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function text(value, limit = 2000) { return typeof value === "string" && value.trim().length > 0 && value.length <= limit; }
function optionalText(value, limit = 2000) { return value == null || text(value, limit); }

export function researchEndpoint(endpoint) {
  if (!text(endpoint)) throw problem("尚未設定研究服務。", "CAPABILITY_NOT_AVAILABLE");
  if (!endpoint.startsWith("/") && !/^https?:\/\//i.test(endpoint)) throw problem("研究服務網址無法驗證。", "CAPABILITY_NOT_AVAILABLE");
  let url;
  try { url = new URL(endpoint, "https://localhost"); }
  catch { throw problem("研究服務網址無法驗證。", "CAPABILITY_NOT_AVAILABLE"); }
  if (url.username || url.password || !(url.protocol === "https:" || (url.protocol === "http:" && LOCAL_HOSTS.has(url.hostname)))) {
    throw problem("研究服務必須使用安全連線。", "CAPABILITY_NOT_AVAILABLE");
  }
  const relative = endpoint.startsWith("/") && !endpoint.startsWith("//");
  url.pathname = "/research"; url.search = ""; url.hash = "";
  return { url: relative ? url.pathname : url.href, remote: !relative && !LOCAL_HOSTS.has(url.hostname) };
}

export function buildResearchRequest(question, history = [], publicDataOnly = false, mode = "metadata") {
  if (!RESEARCH_MODES.includes(mode)) throw problem("研究模式無法辨識，請重新選擇。", "INVALID_MODE");
  if (publicDataOnly !== true) throw problem("請先確認僅輸入公開且不含個資的問題。", "PUBLIC_DATA_CONFIRMATION_REQUIRED");
  if (typeof question !== "string" || !question.trim() || question.trim().length > RESEARCH_QUESTION_LIMIT) {
    throw problem(`請輸入 1 至 ${RESEARCH_QUESTION_LIMIT} 字的公開資訊問題。`, "INVALID_QUESTION");
  }
  if (mode !== "metadata" && new TextEncoder().encode(question.trim()).byteLength > 512) {
    throw problem("全文模式問題請控制在 512 UTF-8 位元組內（中文約 170 字）。", "DOCUMENT_QUERY_TOO_LARGE");
  }
  if (!Array.isArray(history)) throw problem("對話脈絡無法驗證，請開始新對話。", "INVALID_HISTORY");
  // Only prior user questions can enter model context. Answers, sources, drafts,
  // system messages, and other UI fields are never forwarded as history.
  const prior = history.filter(turn => turn?.role === "user").slice(-RESEARCH_HISTORY_LIMIT).map(turn => {
    if (typeof turn.content !== "string" || !turn.content.trim() || turn.content.trim().length > RESEARCH_QUESTION_LIMIT) {
      throw problem("先前問題超出研究範圍，請開始新對話。", "INVALID_HISTORY");
    }
    return { role: "user", content: turn.content.trim() };
  });
  return { question: question.trim(), history: prior, public_data_only: true, mode };
}

const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const integer = value => Number.isSafeInteger(value) && value >= 0;
const bytes = value => new TextEncoder().encode(value).byteLength;
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (object(value)) return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  return JSON.stringify(value);
}
function safeDocumentUrl(value) {
  try { const url = new URL(value); return url.protocol === "https:" && !url.username && !url.password && !url.hash && url.href === value; }
  catch { return false; }
}
function invalidEvidence() { throw problem("文件引用或研究草稿無法核對，未顯示內容。", "INVALID_RESPONSE"); }

function validateDocumentEvidence(compilation, sources) {
  if (!object(compilation) || compilation.schema_version !== 1 || compilation.validator_version !== "document-evidence/1"
      || compilation.kind !== "EXTRACTIVE_EVIDENCE_COMPILATION" || compilation.gate_status !== "QUALIFIED"
      || compilation.publication_tier !== "RESEARCH_ONLY" || compilation.semantic_verification !== "NOT_PERFORMED"
      || compilation.can_assert_current !== false || compilation.can_state_zero_events !== false
      || compilation.model_transmission_allowed !== false || compilation.source_health !== "NOT_ASSESSED"
      || compilation.window_completeness !== "UNKNOWN" || !text(compilation.generation_id, 128)
      || !text(compilation.compiled_at, 64) || !text(compilation.limitation, 8000)
      || !["source_policy_hash", "permission_policy_hash", "corpus_sha256", "draft_sha256", "compilation_sha256"].every(key => hash(compilation[key]))
      || !Array.isArray(compilation.excerpts) || !compilation.excerpts.length || compilation.excerpts.length > 8
      || !Array.isArray(compilation.gaps) || compilation.gaps.length > 256
      || compilation.gaps.some(gap => !object(gap) || !text(gap.code, 128)
        || !["source_id", "document_id", "document_version", "document"].every(key => optionalText(gap[key], 512)))) invalidEvidence();
  const seen = new Set();
  for (const excerpt of compilation.excerpts) {
    const citation = excerpt?.citation;
    if (!object(excerpt) || !text(excerpt.quote, 1024) || bytes(excerpt.quote) > 1024 || excerpt.support_status !== "EXACT_QUOTE_ONLY"
        || !object(citation) || citation.schema_version !== 1
        || !["source_id", "document_id", "document_version", "source_role", "parser_version", "passage_id"].every(key => text(citation[key], 512))
        || !["WRITTEN_OFFICIAL", "ORAL_OFFICIAL", "RESOLUTION"].includes(citation.evidence_type)
        || !safeDocumentUrl(citation.official_url) || !safeDocumentUrl(citation.requested_url)
        || new URL(citation.official_url).origin !== citation.origin
        || !["published_at", "fetched_at"].every(key => optionalText(citation[key], 128))
        || citation.generation_id !== compilation.generation_id
        || citation.offset_basis !== "EXACT_TEXT_UTF16_AND_UTF8_END_EXCLUSIVE"
        || !["content_sha256", "document_binding_sha256", "passage_sha256", "quote_sha256", "citation_sha256"].every(key => hash(citation[key]))
        || !["start_utf16", "end_utf16", "start_utf8", "end_utf8", "quote_start_utf16", "quote_end_utf16", "quote_start_utf8", "quote_end_utf8"].every(key => integer(citation[key]))
        || citation.quote_start_utf16 < citation.start_utf16 || citation.quote_end_utf16 > citation.end_utf16
        || citation.quote_start_utf8 < citation.start_utf8 || citation.quote_end_utf8 > citation.end_utf8
        || citation.quote_end_utf16 - citation.quote_start_utf16 !== excerpt.quote.length
        || citation.quote_end_utf8 - citation.quote_start_utf8 !== bytes(excerpt.quote)
        || seen.has(citation.citation_sha256)) invalidEvidence();
    seen.add(citation.citation_sha256);
    const source = sources.find(row => row.evidence_id === `DOC-${citation.document_binding_sha256}`);
    if (!source || source.title !== citation.document_id || source.source_id !== citation.source_id || source.official_url !== citation.official_url
        || (source.published_at ?? null) !== (citation.published_at ?? null)
        || (source.fetched_at ?? null) !== (citation.fetched_at ?? null)) invalidEvidence();
  }
  return compilation;
}

function validateSynthesis(synthesis, compilation, blocked = false) {
  if (!object(synthesis) || synthesis.schema_version !== 1 || synthesis.semantic_verification !== "AI_REVIEWED_NOT_FORMALLY_VERIFIED"
      || synthesis.publication_tier !== "RESEARCH_ONLY" || synthesis.gate_status !== (blocked ? "BLOCKED" : "QUALIFIED")
      || synthesis.can_assert_current !== false || synthesis.can_state_zero_events !== false || !text(synthesis.limitation, 8000)
      || !Array.isArray(synthesis.claims) || (blocked ? synthesis.claims.length !== 0 : !synthesis.claims.length) || synthesis.claims.length > 8
      || !Array.isArray(synthesis.held_claims) || synthesis.held_claims.length > 8 || (blocked && !synthesis.held_claims.length)) invalidEvidence();
  const receipt = synthesis.receipt;
  if (!object(receipt) || receipt.schema_version !== 1 || receipt.validator_version !== "document-research/1"
      || receipt.generation_id !== compilation.generation_id || receipt.source_policy_hash !== compilation.source_policy_hash
      || receipt.permission_policy_hash !== compilation.permission_policy_hash || receipt.corpus_sha256 !== compilation.corpus_sha256
      || receipt.evidence_compilation_sha256 !== compilation.compilation_sha256
      || !text(receipt.producer_run_id, 128) || !text(receipt.critic_run_id, 128) || receipt.producer_run_id === receipt.critic_run_id
      || receipt.producer_model !== "MiniMax-M2.7" || receipt.critic_model !== "MiniMax-M2.7"
      || receipt.producer_prompt_version !== "document-producer/1" || receipt.critic_prompt_version !== "document-critic/1"
      || !["producer_output_sha256", "critic_output_sha256", "claims_sha256", "receipt_sha256"].every(key => hash(receipt[key]))
      || !text(receipt.generated_at, 64)) invalidEvidence();
  const ids = new Set(), usedCitations = new Set();
  for (const claim of synthesis.claims) {
    if (!object(claim) || !text(claim.claim_id, 128) || ids.has(claim.claim_id) || !text(claim.text, 8000)
        || claim.temporal_scope !== "HISTORICAL_OR_UNDATED" || !Array.isArray(claim.citations)
        || !claim.citations.length || claim.citations.length > 8) invalidEvidence();
    ids.add(claim.claim_id);
    const claimCitations = new Set();
    for (const entry of claim.citations) {
      if (!object(entry) || !object(entry.citation) || !text(entry.quote, 1024)) invalidEvidence();
      const excerpt = compilation.excerpts.find(row => row.citation.citation_sha256 === entry.citation.citation_sha256);
      if (!excerpt || excerpt.quote !== entry.quote || canonical(excerpt.citation) !== canonical(entry.citation)
          || claimCitations.has(entry.citation.citation_sha256)) invalidEvidence();
      claimCitations.add(entry.citation.citation_sha256); usedCitations.add(entry.citation.citation_sha256);
    }
  }
  if (!blocked && usedCitations.size !== compilation.excerpts.length) invalidEvidence();
  for (const held of synthesis.held_claims) {
    if (!object(held) || !text(held.claim_id, 128) || ids.has(held.claim_id)
        || !["CRITIC_CONFLICT", "CRITIC_INSUFFICIENT", "UNSUPPORTED_TEMPORAL_OR_SCOPE"].includes(held.reason_code)) invalidEvidence();
    ids.add(held.claim_id);
  }
}

export function validateResearchResponse(payload, question, mode = "metadata") {
  if (!RESEARCH_MODES.includes(mode) || !object(payload) || payload.schema_version !== 1
      || !["METADATA_ONLY", "ANSWER_READY", "EXTRACTS_READY", "SYNTHESIS_DRAFT"].includes(payload.status)
      || (payload.mode ?? "metadata") !== mode
      || (mode !== "metadata" && typeof payload.provider_transmission_attempted !== "boolean")
      || (mode === "documents" && payload.provider_transmission_attempted !== false)
      || !object(payload.provider) || payload.provider.name !== "MiniMax" || !PROVIDER_STATES.has(payload.provider.state)
      || payload.question !== question || !optionalText(payload.reason_code, 128)
      || !Array.isArray(payload.answer) || payload.answer.length > 32 || payload.answer.some(line => !text(line, 8000))
      || !Array.isArray(payload.sources) || payload.sources.length > (mode === "metadata" ? 64 : 8)
      || !Array.isArray(payload.source_gaps) || payload.source_gaps.length > 256
      || !text(payload.query_generation_id, 256) || !text(payload.data_status, 128)
      || !text(payload.coverage_limitation, 8000)) {
    throw problem("研究回應格式無法核對，未顯示回答。", "INVALID_RESPONSE");
  }
  if ((payload.status !== "ANSWER_READY" && payload.answer.length)
      || (payload.status === "ANSWER_READY" && (mode !== "metadata" || !payload.answer.length || !payload.sources.length || payload.provider.state !== "READY"))) {
    throw problem("回答狀態與證據不一致，未顯示回答。", "INVALID_RESPONSE");
  }
  if (payload.sources.some(source => !object(source) || !text(source.evidence_id, 512)
      || !text(source.source_id, 256) || !text(source.title, 2000) || !optionalText(source.official_url, 4096)
      || !["published_at", "data_as_of", "fetched_at"].every(key => optionalText(source[key], 128)))
      || new Set(payload.sources.map(source => source.evidence_id)).size !== payload.sources.length
      || payload.source_gaps.some(gap => !object(gap) || !text(gap.reason, 2000)
        || !["source_id", "status", "source_health", "freshness_status", "since"].every(key => optionalText(gap[key], 512)))) {
    throw problem("研究來源或資料缺口無法核對，未顯示回答。", "INVALID_RESPONSE");
  }
  if (["EXTRACTS_READY", "SYNTHESIS_DRAFT"].includes(payload.status)) {
    if (mode === "metadata") invalidEvidence();
    validateDocumentEvidence(payload.document_evidence, payload.sources);
    if (payload.status === "SYNTHESIS_DRAFT") {
      if (mode !== "synthesis" || payload.provider.state !== "READY" || payload.provider_transmission_attempted !== true) invalidEvidence();
      validateSynthesis(payload.synthesis, payload.document_evidence);
    } else if (payload.synthesis != null) {
      if (mode !== "synthesis" || payload.reason_code !== "NO_SUPPORTED_CLAIMS" || payload.provider_transmission_attempted !== true || payload.provider.state !== "READY") invalidEvidence();
      validateSynthesis(payload.synthesis, payload.document_evidence, true);
    }
  } else if (payload.document_evidence != null || payload.synthesis != null || (mode !== "metadata" && payload.sources.length)) invalidEvidence();
  return payload;
}
// The response's document identity is an opaque metadata hash. Recompute it,
// rather than trusting a copied hash beside an altered document ID or version.
// This checks response integrity, not source authenticity or semantic truth.
export async function verifyResearchDocumentHashes(payload) {
  if (!payload.document_evidence) return payload;
  if (!globalThis.crypto?.subtle) invalidEvidence();
  const digest = async value => {
    const hashBytes = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return Array.from(new Uint8Array(hashBytes), byte => byte.toString(16).padStart(2, "0")).join("");
  };
  const documentFields = ["schema_version", "document_id", "document_version", "source_id", "evidence_type", "requested_url", "official_url", "origin", "published_at", "fetched_at", "content_sha256", "parser_version"];
  const compilation = payload.document_evidence;
  for (const excerpt of compilation.excerpts) {
    const metadata = Object.fromEntries(documentFields.map(key => [key, excerpt.citation[key]]));
    const { citation_sha256: citationHash, ...citation } = excerpt.citation;
    if (await digest(canonical(metadata)) !== citation.document_binding_sha256
        || await digest(excerpt.quote) !== citation.quote_sha256
        || await digest(canonical(citation)) !== citationHash) invalidEvidence();
  }
  const { compilation_sha256: compilationHash, ...compilationCore } = compilation;
  if (await digest(canonical(compilationCore)) !== compilationHash) invalidEvidence();
  if (payload.synthesis) {
    const { receipt_sha256: receiptHash, ...receiptCore } = payload.synthesis.receipt;
    if (await digest(canonical(payload.synthesis.claims)) !== receiptCore.claims_sha256
        || await digest(canonical(receiptCore)) !== receiptHash) invalidEvidence();
  }
  return payload;
}

export function researchExplanation(result) {
  const reason = result.reason_code;
  // Dispatch tracking outranks reason-code wording: a failed critic/admission
  // step cannot erase the producer transmission that already happened.
  const preProviderReasons = new Set(["RIGHTS_BLOCKED", "NO_APPROVED_SOURCES", "NO_MATCH", "INPUT_RESTRICTED", "ADMISSION_DENIED", "DOCUMENTS_UNCONFIGURED", "DOCUMENTS_INVALID", "DOCUMENT_RIGHTS_BLOCKED", "DOCUMENT_PERMISSION_EXPIRED", "NO_DOCUMENT_MATCH", "MODEL_TRANSMISSION_NOT_APPROVED"]);
  if (result.provider_transmission_attempted === true && (preProviderReasons.has(reason) || result.provider.state !== "READY")) {
    return "本次已嘗試將核准內容傳到 MiniMax，但未取得可釋出的研究草稿。請核對可用原文摘錄與資料限制。";
  }
  const reasons = {
    DOCUMENTS_UNCONFIGURED: "伺服器尚未提供核准文件庫。本次沒有文件摘錄，也未改查其他模式或送至 MiniMax。",
    DOCUMENTS_INVALID: "伺服器文件無法通過核對。本次未釋出文件內容，也未送至 MiniMax。",
    DOCUMENT_RIGHTS_BLOCKED: "文件使用或摘錄權利尚未核准。本次未釋出文件內容，也未送至 MiniMax。",
    DOCUMENT_PERMISSION_EXPIRED: "文件使用許可已到期，需要重新審查。本次未送至 MiniMax。",
    NO_DOCUMENT_MATCH: "核准文件庫未找到符合段落，本次未送至 MiniMax。這不代表現實沒有相關事件。",
    MODEL_TRANSMISSION_NOT_APPROVED: "文件的模型傳送權利尚未核准。本次未將問題或文件送至 MiniMax；可用原文摘錄只供你自行核對。",
    REVIEW_ADMISSION_DENIED: "第二階段的模型檢查未取得使用或用量許可，未釋出研究草稿。已送出的第一階段內容不會因此撤回。",
    SYNTHESIS_REVIEW_FAILED: "第二階段的模型檢查未完成，未釋出研究草稿。下方只保留可核對的原文摘錄。",
    NO_SUPPORTED_CLAIMS: "模型草稿沒有可釋出的主張。下方只保留精確原文摘錄，不展示被保留的模型文字。",
    RIGHTS_BLOCKED: result.mode && result.mode !== "metadata" ? "來源或文件使用權利尚未核准。本次未將問題或文件送至 MiniMax，也沒有改用其他研究模式。" : "來源的模型使用權利尚未核准。本次未將問題或來源內容送至 MiniMax；僅列出可公開的索引。",
    NO_APPROVED_SOURCES: "目前沒有符合使用條件的核准來源。本次未將問題或來源內容送至 MiniMax。",
    NO_MATCH: "這份有限快照未找到符合的索引，本次未送至 MiniMax。這不代表現實沒有相關事件。",
    INPUT_RESTRICTED: "問題不符合公開資料研究限制，本次未送至 MiniMax。請勿輸入個資或內部資料。",
    ADMISSION_DENIED: "本次請求未通過模型使用審查，未將問題或來源內容送至 MiniMax。",
    PROVIDER_UNAVAILABLE: "模型服務未完成回答。目前只保留可核對的來源資料，請稍後重試。",
    NO_RELEVANT_SELECTION: "模型未選出相關且核准的引用，本次沒有釋出回答。可先查看來源索引，或縮小問題範圍。",
    OUTPUT_REJECTED: "模型輸出未通過證據核對，未顯示該輸出。可先查看下方來源與資料缺口。",
  };
  if (reasons[reason]) return reasons[reason];
  if (result.status === "SYNTHESIS_DRAFT") return "引用已核對，模型語意仍需人工核對。這是研究草稿，不是正式答案。";
  if (result.status === "EXTRACTS_READY" && result.mode === "documents") return "已整理可回查的精確原文摘錄；這個模式未呼叫模型。摘錄一致不代表語意或現況已核實。";
  const states = {
    DISABLED: "模型尚未啟用。本次未將問題或來源內容送至 MiniMax；僅保留可用的核准來源資料。",
    KEY_MISSING: "伺服器尚未設定模型服務。本次未將問題或來源內容送至 MiniMax。",
    BILLING_REVIEW_REQUIRED: "模型用量與付費審查尚未完成。本次未將問題或來源內容送至 MiniMax。",
    ADMISSION_UNCONFIGURED: "模型使用審查尚未設定。本次未將問題或來源內容送至 MiniMax。",
  };
  return states[result.provider.state] || (result.status === "ANSWER_READY"
    ? "以下索引識別摘要已通過伺服器證據核對，不代表公告所述事件已核實。請一併閱讀原文、資料時間與涵蓋限制。"
    : "本次僅取得來源中介資料，沒有可釋出的模型回答。索引標題不代表事件事實已核對。");
}

export function researchDate(value) {
  if (!value) return "未提供";
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))?$/.test(value)
      || Number.isNaN(Date.parse(value))
      || new Date(`${value.slice(0, 10)}T00:00:00Z`).toISOString().slice(0, 10) !== value.slice(0, 10)) return "時間無法驗證";
  if (value.length === 10) return `${value}（僅日期）`;
  return new Intl.DateTimeFormat("zh-TW", { timeZone: "Asia/Taipei", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(new Date(value));
}

export async function requestResearch(endpoint, input, context = {}) {
  const { url, remote } = researchEndpoint(endpoint);
  const body = buildResearchRequest(input.question, input.history, input.publicDataOnly, input.mode);
  const controller = new AbortController();
  const abort = () => controller.abort(context.signal?.reason);
  context.signal?.addEventListener("abort", abort, { once: true });
  if (context.signal?.aborted) abort();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, context.timeoutMs ?? 45000);
  const fetchImpl = context.fetchImpl || fetch;
  async function request(address, options = {}) {
    controller.signal.throwIfAborted();
    const response = await fetchImpl(address, { cache: "no-store", credentials: "omit", ...options, signal: controller.signal });
    controller.signal.throwIfAborted();
    return response;
  }
  async function json(response) {
    try { const value = await response.json(); controller.signal.throwIfAborted(); return value; }
    catch (error) { if (controller.signal.aborted) throw error; throw problem("研究回應格式無法核對，未顯示回答。", "INVALID_RESPONSE"); }
  }
  try {
    if (!text(context.publicationGeneration, 256) || (remote && !text(context.codeSha, 128))) {
      throw problem("缺少發布版本，請重新載入頁面後查詢。", "CAPABILITY_NOT_AVAILABLE");
    }
    const releaseResponse = await request(`${context.basePath || ""}/data/release.json`);
    if (!releaseResponse.ok) throw problem("無法核對發布版本，請稍後重試。", "PENDING_UPDATE");
    const release = await json(releaseResponse);
    if (!text(release?.release_id, 256) || !text(release?.code_sha, 128)
        || (context.codeSha && release.code_sha !== context.codeSha)
        || release.publication_generation !== context.publicationGeneration) {
      throw problem("發布版本已變更，請重新載入頁面後查詢。", "PENDING_UPDATE");
    }
    body.release_id = release.release_id;
    const response = await request(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const payload = await json(response);
    if (!response.ok || payload?.error) {
      // Do not reflect unknown server messages, which could echo the question or
      // contain raw upstream output. The UI displays bounded, known error codes.
      const code = typeof payload?.error?.code === "string" && /^[A-Z][A-Z0-9_]{0,127}$/.test(payload.error.code) ? payload.error.code : "FAILED";
      throw problem("研究請求未完成，沒有可顯示的回答。", code);
    }
    if (release && ["release_id", "code_sha", "publication_generation"].some(field => payload?.release?.[field] !== release[field])) {
      throw problem("回應發布版本已變更，請重新載入頁面後查詢。", "PENDING_UPDATE");
    }
    const checked = validateResearchResponse(payload, body.question, body.mode);
    await verifyResearchDocumentHashes(checked);
    controller.signal.throwIfAborted();
    return checked;
  } catch (error) {
    if (context.signal?.aborted) throw problem("已停止等待本次研究。", "ABORTED");
    if (timedOut || error?.name === "TimeoutError") throw problem("研究服務逾時，未取得回答。請稍後重試。", "TIMEOUT");
    if (error?.code && typeof error.code === "string") throw error;
    throw problem("研究服務目前無法連線，未取得回答。", "FAILED");
  } finally {
    clearTimeout(timeout);
    context.signal?.removeEventListener("abort", abort);
  }
}
