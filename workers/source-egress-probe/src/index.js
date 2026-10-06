// Temporary, authenticated diagnostic. The workflow deletes this Worker after
// three fixed official GETs. No origin bytes or authentication are published.
export const SOURCE_URLS = Object.freeze({
  "S-029": "https://www.rdec.taichung.gov.tw/12047/12142/12145",
  "S-001": "https://www.police.taichung.gov.tw/ch/home.jsp?id=1&parentpath=0&mcustomize=news_list.jsp",
  "S-019": "https://www.rdec.taichung.gov.tw/12047/12142/12186",
});
const MAX_BYTES = 2 * 1024 * 1024;
const reply = (data, status = 200) => Response.json(data, {status, headers:{"Cache-Control":"no-store"}});

export function createHandler({ fetchImpl = fetch, now = Date.now, cryptoImpl = crypto, timeoutMs = 15000 } = {}) {
  return async (request, env) => {
    const expiry = Number(env.EXPIRES_AT);
    if (typeof env.PROBE_TOKEN !== "string" || !/^[a-f0-9]{64}$/.test(env.PROBE_TOKEN) ||
        !Number.isSafeInteger(expiry) || expiry <= now() || expiry - now() > 15 * 60 * 1000 ||
        request.headers.get("Authorization") !== `Bearer ${env.PROBE_TOKEN}`) {
      return reply({error:"UNAUTHENTICATED"}, 403);
    }
    const incoming = new URL(request.url);
    const sourceId = incoming.searchParams.get("source");
    if (request.method !== "GET" || incoming.pathname !== "/probe" ||
        [...incoming.searchParams.keys()].some(key => key !== "source") || incoming.searchParams.getAll("source").length !== 1 ||
        !Object.hasOwn(SOURCE_URLS, sourceId)) return reply({error:"INVALID_FIXED_PROBE"}, 400);
    const officialUrl = SOURCE_URLS[sourceId];
    const controller = new AbortController();
    let rejectTimeout;
    const deadline = new Promise((_, reject) => {rejectTimeout = reject;});
    const timer = setTimeout(() => {controller.abort(); rejectTimeout(new Error("TIMEOUT"));}, timeoutMs);
    let reader;
    const observedAt = new Date(now()).toISOString();
    const start = now();
    const base = {schema_version:1, scope:"TRANSPORT_ONLY_NOT_SOURCE_RECOVERY", source_id:sourceId,
                  official_url:officialUrl, observed_at:observedAt, promotion_eligible:false};
    try {
      const response = await Promise.race([fetchImpl(officialUrl, {
        method:"GET", redirect:"manual", signal:controller.signal,
        headers:{"User-Agent":"GovIntelSourceEgressProbe/1.0 (+bounded public-source diagnostic)", "Accept-Language":"zh-TW"},
      }), deadline]);
      if (response.status >= 300 && response.status < 400) {
        void response.body?.cancel();
        return reply({...base, status:"REDIRECT_NOT_FOLLOWED", http_status:response.status, elapsed_ms:now()-start});
      }
      reader = response.body?.getReader();
      if (!reader) throw new Error("EMPTY_RESPONSE");
      const chunks = []; let size = 0;
      while (true) {
        const {value, done} = await Promise.race([reader.read(), deadline]);
        if (done) break;
        size += value.byteLength;
        if (size > MAX_BYTES) throw new Error("BODY_BUDGET_EXCEEDED");
        chunks.push(value);
      }
      const body = new Uint8Array(size); let offset = 0;
      for (const chunk of chunks) {body.set(chunk,offset); offset += chunk.byteLength;}
      const sha = [...new Uint8Array(await cryptoImpl.subtle.digest("SHA-256",body))].map(byte=>byte.toString(16).padStart(2,"0")).join("");
      return reply({...base, status:response.status===200 ? "HTTP_CAPTURED_NOT_PARSED" : "HTTP_FAILED", http_status:response.status,
                    bytes:size, body_sha256:sha, content_type:response.headers.get("Content-Type"), elapsed_ms:now()-start,
                    original_bytes_republished:false, colo:request.cf?.colo ?? null});
    } catch(error) {
      const reason = ["TIMEOUT","EMPTY_RESPONSE","BODY_BUDGET_EXCEEDED"].includes(error.message) ? error.message : "ORIGIN_FETCH_FAILED";
      return reply({...base, status:"FAILED", reason, elapsed_ms:now()-start});
    } finally {
      clearTimeout(timer);controller.abort();void reader?.cancel().catch(()=>{});
    }
  };
}
export default {fetch:createHandler()};
