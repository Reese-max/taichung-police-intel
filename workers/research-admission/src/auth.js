import { deny, object } from "./config.js";
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", { fatal: true });
const JWT_MAX_BYTES = 16384;
const JWKS_MAX_BYTES = 32768;
const JWKS_TTL_MS = 300000;
const JWKS_FAILURE_BACKOFF_MS = 5000;
const IO_TIMEOUT_MS = 3000;

export async function readBoundedJson(message, maxBytes, { signal = message.signal, timeoutMs = IO_TIMEOUT_MS } = {}) {
  if (!message.body) deny("INVALID_REQUEST", 400);
  const reader = message.body.getReader();
  const chunks = []; let size = 0; let rejectPending;
  const interrupted = new Promise((_, reject) => { rejectPending = reject; });
  const abort = () => { rejectPending(new Error("Interrupted")); void reader.cancel().catch(() => {}); };
  const timer = setTimeout(abort, timeoutMs);
  signal?.addEventListener("abort", abort, { once: true });
  if (signal?.aborted) abort();
  try {
    while (true) {
      const { value, done } = await Promise.race([reader.read(), interrupted]);
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) deny("REQUEST_TOO_LARGE", 413);
      chunks.push(value);
    }
    if (signal?.aborted) throw new Error("Interrupted");
    const bytes = new Uint8Array(size); let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    try { return JSON.parse(decoder.decode(bytes)); } catch { deny("INVALID_REQUEST", 400); }
  } finally {
    clearTimeout(timer); signal?.removeEventListener("abort", abort);
    void reader.cancel().catch(() => {});
  }
}
function decode64(value) {
  if (typeof value !== "string" || !/^[A-Za-z0-9_-]+$/.test(value) || value.length % 4 === 1) deny("UNAUTHENTICATED", 403);
  let binary;
  try { binary = atob(value.replaceAll("-", "+").replaceAll("_", "/")); } catch { deny("UNAUTHENTICATED", 403); }
  if (btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "") !== value) deny("UNAUTHENTICATED", 403);
  return Uint8Array.from(binary, char => char.charCodeAt(0));
}
function decodeJson(value) {
  try { return JSON.parse(decoder.decode(decode64(value))); } catch { deny("UNAUTHENTICATED", 403); }
}
function parseToken(token) {
  if (typeof token !== "string" || token.length > JWT_MAX_BYTES) deny("UNAUTHENTICATED", 403);
  const segments = token.split(".");
  if (segments.length !== 3) deny("UNAUTHENTICATED", 403);
  const header = decodeJson(segments[0]); const claims = decodeJson(segments[1]);
  if (!object(header) || header.alg !== "RS256" || typeof header.kid !== "string" || !header.kid.length || header.kid.length > 128 || (header.typ !== undefined && header.typ !== "JWT") || Object.keys(header).some(key => !["alg", "kid", "typ"].includes(key)) || !object(claims)) deny("UNAUTHENTICATED", 403);
  const signature = decode64(segments[2]);
  if (signature.length < 256 || signature.length > 512) deny("UNAUTHENTICATED", 403);
  return { header, claims, signature, signed: encoder.encode(`${segments[0]}.${segments[1]}`) };
}
export function validateClaims(claims, config, nowMs) {
  const now = Math.floor(nowMs / 1000);
  if (!Number.isSafeInteger(now) || now < 0 || !Number.isSafeInteger(claims.exp) || claims.exp <= now) deny("UNAUTHENTICATED", 403);
  if (claims.nbf !== undefined && (!Number.isSafeInteger(claims.nbf) || claims.nbf > now || claims.nbf >= claims.exp)) deny("UNAUTHENTICATED", 403);
  if (claims.iat !== undefined && (!Number.isSafeInteger(claims.iat) || claims.iat > now || claims.iat >= claims.exp)) deny("UNAUTHENTICATED", 403);
  const audiences = typeof claims.aud === "string" ? [claims.aud] : claims.aud;
  if (claims.iss !== config.issuer || !Array.isArray(audiences) || !audiences.length || audiences.length > 16 || audiences.some(aud => typeof aud !== "string") || !audiences.includes(config.audience) || typeof claims.sub !== "string" || !config.subjects.has(claims.sub)) deny("UNAUTHENTICATED", 403);
}
function validJwks(payload) {
  if (!object(payload) || !Array.isArray(payload.keys) || !payload.keys.length || payload.keys.length > 8) throw new Error("Invalid JWKS");
  const keys = new Map();
  for (const key of payload.keys) {
    if (!object(key) || key.kty !== "RSA" || key.alg !== "RS256" || key.use !== "sig" || typeof key.kid !== "string" || !key.kid.length || key.kid.length > 128 || keys.has(key.kid) || ["d", "p", "q", "dp", "dq", "qi", "oth"].some(name => Object.hasOwn(key, name))) throw new Error("Invalid JWKS");
    const modulus = decode64(key.n); const exponent = decode64(key.e);
    if (modulus.length < 256 || modulus.length > 512 || !(modulus[0] & 128) || exponent.length !== 3 || exponent[0] !== 1 || exponent[1] !== 0 || exponent[2] !== 1 || (key.key_ops !== undefined && (!Array.isArray(key.key_ops) || key.key_ops.length !== 1 || key.key_ops[0] !== "verify"))) throw new Error("Invalid JWKS");
    // Import only the expected public parameters, never URLs or embedded keys.
    keys.set(key.kid, { kty: "RSA", alg: "RS256", use: "sig", n: key.n, e: key.e, ext: true });
  }
  return keys;
}
export function createVerifier({ fetchImpl = fetch, now = Date.now, cryptoImpl = crypto } = {}) {
  let cache; let inFlight; let blockedUntil = 0;
  async function fetchKeys(issuer) {
    const url = `${issuer}/cdn-cgi/access/certs`;
    const controller = new AbortController(); let rejectTimeout;
    const timedOut = new Promise((_, reject) => { rejectTimeout = reject; });
    const timer = setTimeout(() => { controller.abort(); rejectTimeout(new Error("JWKS timeout")); }, IO_TIMEOUT_MS);
    try {
      const work = (async () => {
        const reply = await fetchImpl(url, { method: "GET", redirect: "error", credentials: "omit", cache: "no-store", signal: controller.signal, headers: { Accept: "application/json" } });
        if (!reply.ok || reply.redirected || (reply.url && reply.url !== url) || !["application/json", "application/jwk-set+json"].includes(reply.headers.get("content-type")?.split(";")[0].trim().toLowerCase())) {
          void reply.body?.cancel().catch(() => {}); throw new Error("JWKS unavailable");
        }
        return validJwks(await readBoundedJson(reply, JWKS_MAX_BYTES, { signal: controller.signal }));
      })();
      return await Promise.race([work, timedOut]);
    } finally { clearTimeout(timer); controller.abort(); }
  }
  async function keyFor(config, kid) {
    if (cache?.issuer === config.issuer && cache.expiresAt > now()) return cache.keys.get(kid);
    if (now() < blockedUntil) throw new Error("JWKS unavailable");
    // One bounded fetch per refresh, including simultaneous JWT checks. Unknown
    // kid values cannot force refreshes of an otherwise fresh cache.
    if (!inFlight) {
      inFlight = fetchKeys(config.issuer).then(keys => {
        cache = { issuer: config.issuer, keys, expiresAt: now() + JWKS_TTL_MS }; return cache;
      }).catch(() => { cache = undefined; blockedUntil = now() + JWKS_FAILURE_BACKOFF_MS; throw new Error("JWKS unavailable"); }).finally(() => { inFlight = undefined; });
    }
    const result = await inFlight;
    if (result.issuer !== config.issuer) throw new Error("JWKS unavailable");
    return result.keys.get(kid);
  }
  return async function verify(token, config) {
    const parsed = parseToken(token);
    // Early rejection reduces unauthenticated work; checked again after crypto
    // and inside the reservation transaction, so these are never trusted claims.
    validateClaims(parsed.claims, config, now());
    const jwk = await keyFor(config, parsed.header.kid);
    if (!jwk) deny("UNAUTHENTICATED", 403);
    let verified = false;
    try {
      const key = await cryptoImpl.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["verify"]);
      verified = await cryptoImpl.subtle.verify("RSASSA-PKCS1-v1_5", key, parsed.signature, parsed.signed);
    } catch { deny("UNAUTHENTICATED", 403); }
    if (!verified) deny("UNAUTHENTICATED", 403);
    validateClaims(parsed.claims, config, now());
    const hash = await cryptoImpl.subtle.digest("SHA-256", encoder.encode(`${config.issuer}\0${config.audience}\0${parsed.claims.sub}`));
    return { claims: parsed.claims, userKey: Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("") };
  };
}
