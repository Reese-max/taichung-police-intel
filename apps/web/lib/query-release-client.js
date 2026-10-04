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
    if (!release?.release_id || release.code_sha !== context.codeSha || release.publication_generation !== context.publicationGeneration) {
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
  if (release && ["release_id", "code_sha", "publication_generation"].some(field => payload.release?.[field] !== release[field])) {
    throw gatewayError("查詢回應版本已變更，請重新載入頁面後查詢。", "PENDING_UPDATE");
  }
  return payload;
}
