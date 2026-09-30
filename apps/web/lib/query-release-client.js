export async function queryGateway(endpoint, tool, args, context = null) {
  let release;
  const remote = !["localhost", "127.0.0.1", "[::1]"].includes(new URL(endpoint, "http://localhost").hostname);
  if (remote) {
    if (!context) throw new Error("缺少發布版本，請重新載入頁面後查詢。");
    const response = await fetch(`${context.basePath}/data/release.json`, { cache: "no-store" });
    if (!response.ok) throw new Error("無法核對發布版本，請稍後重試。");
    release = await response.json();
    if (!context.codeSha || !context.publicationGeneration || !release?.release_id ||
        release.code_sha !== context.codeSha || release.publication_generation !== context.publicationGeneration) {
      throw new Error("發布版本已變更，請重新載入頁面後查詢。");
    }
  }
  const url = endpoint.endsWith("/query") ? endpoint : `${endpoint.replace(/\/$/, "")}/query`;
  const response = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tool, arguments: args, ...(release ? { release_id: release.release_id } : {}) }),
  });
  const payload = await response.json();
  if (!response.ok || payload.error) throw new Error(payload.error?.message || `Gateway HTTP ${response.status}`);
  if (release && ["release_id", "code_sha", "publication_generation"].some(field => payload.release?.[field] !== release[field])) {
    throw new Error("查詢回應版本已變更，請重新載入頁面後查詢。");
  }
  return payload;
}
