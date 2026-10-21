import { canonicalJson } from "./public-query.js";

const RELEASE_FIELDS = ["schema_version", "release_id", "code_sha", "publication_generation", "publication_hash",
  "artifact_hashes", "source_policy_hash", "query_generation", "evidence_catalog_hash", "built_at"];

export function releaseBindingMatches(actual, expected) {
  const hash = value => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
  return expected?.schema_version === 1 && typeof expected.code_sha === "string" && /^[a-f0-9]{40}$/.test(expected.code_sha)
    && typeof expected.publication_generation === "string" && expected.publication_generation.trim().length > 0
    && typeof expected.built_at === "string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(expected.built_at) && Number.isFinite(Date.parse(expected.built_at))
    && ["release_id", "publication_hash", "source_policy_hash", "query_generation", "evidence_catalog_hash"].every(field => hash(expected[field]))
    && ["feed", "status", "brief"].every(field => hash(expected.artifact_hashes?.[field]))
    && expected.artifact_hashes.brief === expected.publication_hash
    && RELEASE_FIELDS.every(field => canonicalJson(actual?.[field]) === canonicalJson(expected[field]));
}

function gatewayError(message, code) {
  return Object.assign(new Error(message), { code });
}

export async function queryGateway(endpoint, tool, args, context = null) {
  if (!endpoint) throw gatewayError("未設定正式查詢服務。", "CAPABILITY_NOT_AVAILABLE");
  const fetchImpl = context?.fetchImpl || fetch;
  const request = async (url, options) => {
    try {
      return await fetchImpl(url, { ...options, signal: AbortSignal.timeout(12000) });
    } catch (error) {
      if (error?.name === "TimeoutError") throw gatewayError("查詢服務逾時，請稍後重試。", "TIMEOUT");
      throw gatewayError("查詢服務目前無法連線，請稍後重試。", "FAILED");
    }
  };
  let release;
  const remote = !["localhost", "127.0.0.1", "[::1]"].includes(new URL(endpoint, "http://localhost").hostname);
  if (remote) {
    if (!context?.codeSha || !context?.publicationGeneration) {
      throw gatewayError("缺少發布版本，請重新載入頁面後查詢。", "CAPABILITY_NOT_AVAILABLE");
    }
    const response = await request(`${context.basePath ?? ""}/data/release.json`, { cache: "no-store" });
    if (!response.ok) throw gatewayError("無法核對發布版本，請稍後重試。", "PENDING_UPDATE");
    try { release = await response.json(); }
    catch { throw gatewayError("發布版本資料無法驗證，請稍後重試。", "PENDING_UPDATE"); }
    if (!releaseBindingMatches(release, release) || release.code_sha !== context.codeSha || release.publication_generation !== context.publicationGeneration) {
      throw gatewayError("發布版本已變更，請重新載入頁面後查詢。", "PENDING_UPDATE");
    }
  }
  const normalized = endpoint.replace(/\/+$/, "");
  const url = normalized.endsWith("/query") ? normalized : `${normalized}/query`;
  const response = await request(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool, arguments: args, ...(release ? { release_id: release.release_id } : {}) }),
  });
  let payload;
  try { payload = await response.json(); }
  catch { throw gatewayError("查詢回應格式無法驗證，請稍後重試。", "INVALID_RESPONSE"); }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw gatewayError("查詢回應格式無法驗證，請稍後重試。", "INVALID_RESPONSE");
  }
  if (!response.ok || payload.error) throw gatewayError(payload.error?.message || `Gateway HTTP ${response.status}`, payload.error?.code || "FAILED");
  if (release && (!releaseBindingMatches(payload.release, release)
      || payload.publication_id !== release.publication_generation || payload.publication_hash !== release.publication_hash
      || payload.query_generation_id !== release.query_generation || payload.policy?.policy_hash !== release.source_policy_hash
      || payload.receipt?.publication_hash !== release.publication_hash || payload.receipt?.query_generation_id !== release.query_generation)) {
    throw gatewayError("查詢回應版本已變更，請重新載入頁面後查詢。", "PENDING_UPDATE");
  }
  return payload;
}
