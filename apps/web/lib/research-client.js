// Browser-side transport only. Provider credentials and model calls stay on the Gateway.
export const RESEARCH_QUESTION_LIMIT = 512;
export const RESEARCH_HISTORY_LIMIT = 4;
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

export function buildResearchRequest(question, history = [], publicDataOnly = false) {
  if (publicDataOnly !== true) throw problem("請先確認僅輸入公開且不含個資的問題。", "PUBLIC_DATA_CONFIRMATION_REQUIRED");
  if (typeof question !== "string" || !question.trim() || question.trim().length > RESEARCH_QUESTION_LIMIT) {
    throw problem(`請輸入 1 至 ${RESEARCH_QUESTION_LIMIT} 字的公開資訊問題。`, "INVALID_QUESTION");
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
  return { question: question.trim(), history: prior, public_data_only: true };
}

export function validateResearchResponse(payload, question) {
  if (!object(payload) || payload.schema_version !== 1 || !["METADATA_ONLY", "ANSWER_READY"].includes(payload.status)
      || !object(payload.provider) || payload.provider.name !== "MiniMax" || !PROVIDER_STATES.has(payload.provider.state)
      || payload.question !== question || !optionalText(payload.reason_code, 128)
      || !Array.isArray(payload.answer) || payload.answer.length > 32 || payload.answer.some(line => !text(line, 8000))
      || !Array.isArray(payload.sources) || payload.sources.length > 64
      || !Array.isArray(payload.source_gaps) || payload.source_gaps.length > 256
      || !text(payload.query_generation_id, 256) || !text(payload.data_status, 128)
      || !text(payload.coverage_limitation, 8000)) {
    throw problem("研究回應格式無法核對，未顯示回答。", "INVALID_RESPONSE");
  }
  if ((payload.status === "METADATA_ONLY" && payload.answer.length)
      || (payload.status === "ANSWER_READY" && (!payload.answer.length || !payload.sources.length || payload.provider.state !== "READY"))) {
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
  return payload;
}

export function researchExplanation(result) {
  const reason = result.reason_code;
  const reasons = {
    RIGHTS_BLOCKED: "來源的模型使用權利尚未核准。本次未將問題或來源內容送至 MiniMax；僅列出可公開的索引。",
    NO_APPROVED_SOURCES: "目前沒有符合使用條件的核准來源。本次未將問題或來源內容送至 MiniMax。",
    NO_MATCH: "這份有限快照未找到符合的索引，本次未送至 MiniMax。這不代表現實沒有相關事件。",
    INPUT_RESTRICTED: "問題不符合公開資料研究限制，本次未送至 MiniMax。請勿輸入個資或內部資料。",
    ADMISSION_DENIED: "本次請求未通過模型使用審查，未將問題或來源內容送至 MiniMax。",
    PROVIDER_UNAVAILABLE: "模型服務未完成回答。目前僅顯示可核對的來源索引，請稍後重試。",
    NO_RELEVANT_SELECTION: "模型未選出相關且核准的引用，本次沒有釋出回答。可先查看來源索引，或縮小問題範圍。",
    OUTPUT_REJECTED: "模型輸出未通過證據核對，未顯示該輸出。可先查看下方來源與資料缺口。",
  };
  if (reasons[reason]) return reasons[reason];
  const states = {
    DISABLED: "模型尚未啟用。本次未將問題或來源內容送至 MiniMax；僅查詢核准的公開索引。",
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
  const body = buildResearchRequest(input.question, input.history, input.publicDataOnly);
  const controller = new AbortController();
  const abort = () => controller.abort(context.signal?.reason);
  context.signal?.addEventListener("abort", abort, { once: true });
  if (context.signal?.aborted) abort();
  let timedOut = false;
  const timeout = setTimeout(() => { timedOut = true; controller.abort(); }, context.timeoutMs ?? 30000);
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
    return validateResearchResponse(payload, body.question);
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
