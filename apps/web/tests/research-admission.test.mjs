import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import worker, { createAdmissionHandler, ResearchBudget } from "../../../workers/research-admission/src/index.js";
import { readBoundedJson } from "../../../workers/research-admission/src/auth.js";
import { CONTRACT, GLOBAL_OBJECT_NAME, LEDGER_KEY, loadConfig } from "../../../workers/research-admission/src/config.js";
import { selectResearchSources } from "../../../workers/query-gateway/src/research.js";

const EPOCH = Date.parse("2026-10-05T12:00:00Z");
const ISSUER = "https://offline-test.cloudflareaccess.com";
const AUD = "offline-audience-only";
const SUBJECTS = ["synthetic-user-a", "synthetic-user-b"];
const keyPair = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
const publicJwk = { ...await crypto.subtle.exportKey("jwk", keyPair.publicKey), kid: "offline-key", alg: "RS256", use: "sig" };
const base64url = value => Buffer.from(typeof value === "string" ? value : JSON.stringify(value)).toString("base64url");
async function jwt(overrides = {}, headerPatch = {}, signingKey = keyPair.privateKey) {
  const header = base64url({ alg: "RS256", typ: "JWT", kid: "offline-key", ...headerPatch });
  const claims = base64url({ iss: ISSUER, aud: [AUD], sub: SUBJECTS[0], iat: EPOCH / 1000 - 60, nbf: EPOCH / 1000 - 60, exp: EPOCH / 1000 + 3600, ...overrides });
  const input = `${header}.${claims}`;
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", signingKey, new TextEncoder().encode(input));
  return `${input}.${Buffer.from(signature).toString("base64url")}`;
}
const goodToken = await jwt();
function config(patch = {}) {
  return { ADMISSION_ENABLED: "true", RESEARCH_BILLING_REVIEWED: "true", ACCESS_TEAM_DOMAIN: ISSUER, ACCESS_AUD: AUD, ACCESS_ALLOWED_SUBJECTS: JSON.stringify(SUBJECTS), RESEARCH_DAILY_USER_CALLS: "10", RESEARCH_DAILY_GLOBAL_CALLS: "20", RESEARCH_DAILY_USER_TOKENS: "174080", RESEARCH_DAILY_GLOBAL_TOKENS: "348160", RESEARCH_INPUT_USD_MICROS_PER_MILLION_TOKENS: "1000000", RESEARCH_OUTPUT_USD_MICROS_PER_MILLION_TOKENS: "2000000", RESEARCH_DAILY_GLOBAL_USD_MICROS: "1000000", ...patch };
}
// Offline transactional storage model: commits a cloned snapshot only when the
// closure succeeds. A shared queue models the storage serialization contract.
class TransactionStorage {
  constructor(data = new Map()) { this.data = data; this.tail = Promise.resolve(); this.transactions = 0; this.writes = 0; }
  transaction(callback) {
    const run = this.tail.then(async () => {
      this.transactions++;
      const draft = structuredClone(this.data); let writes = 0;
      const result = await callback({ get: async key => { await Promise.resolve(); return structuredClone(draft.get(key)); }, put: async (key, value) => { writes++; draft.set(key, structuredClone(value)); } });
      this.data = draft; this.writes += writes; return result;
    });
    this.tail = run.catch(() => {}); return run;
  }
  ledger() { return this.data.get(LEDGER_KEY); }
}
function setup({ env = config(), storage = new TransactionStorage(), now = () => EPOCH, fetchImpl } = {}) {
  const requests = [];
  const localFetch = fetchImpl || (async (url, options) => {
    requests.push({ url, options });
    assert.equal(url, `${ISSUER}/cdn-cgi/access/certs`);
    assert.equal(options.method, "GET"); assert.equal(options.redirect, "error"); assert.equal(options.credentials, "omit");
    assert.deepEqual(options.headers, { Accept: "application/json" });
    return Response.json({ keys: [publicJwk] });
  });
  return { handle: createAdmissionHandler({ env, storage, now, fetchImpl: localFetch }), storage, requests, env };
}
function request(token = goodToken, body = CONTRACT, options = {}) {
  return new Request(options.url || "https://research-admission/authorize", { method: options.method || "POST", headers: { "Content-Type": "application/json", ...(token === null ? {} : { "CF-Access-Jwt-Assertion": token }), ...options.headers }, ...(!["GET", "HEAD"].includes(options.method) && { body: JSON.stringify(body) }), ...(options.signal && { signal: options.signal }) });
}
async function result(handle, req = request()) { const reply = await handle(req); return { status: reply.status, body: await reply.json(), headers: reply.headers }; }
async function rejectedWithoutReservation(handle, storage, req) {
  const reply = await result(handle, req); assert.equal(reply.body.allowed, false); assert.equal(storage.transactions, 0); assert.equal(storage.writes, 0); return reply;
}

test("disabled default returns allowed:false without storage or network", async () => {
  for (const env of [{}, config({ ADMISSION_ENABLED: "false" }), config({ ADMISSION_ENABLED: true })]) {
    const h = setup({ env }); const reply = await rejectedWithoutReservation(h.handle, h.storage, request());
    assert.equal(reply.body.code, "DISABLED"); assert.equal(h.requests.length, 0);
  }
});
test("signed Access identity authorizes exactly one persistent bounded reservation", async () => {
  const h = setup(); const reply = await result(h.handle);
  assert.deepEqual(reply.body, { allowed: true }); assert.equal(reply.status, 200); assert.equal(reply.headers.get("cache-control"), "no-store");
  assert.equal(h.requests.length, 1); assert.equal(h.storage.writes, 1);
  const ledger = h.storage.ledger();
  assert.equal(ledger.day, "2026-10-05"); assert.deepEqual(ledger.global, { calls: 1, tokens: 17408, usdMicros: 18432 });
  assert.match(Object.keys(ledger.users)[0], /^[a-f0-9]{64}$/);
  for (const sensitive of [SUBJECTS[0], ISSUER, AUD, goodToken]) assert.ok(!JSON.stringify(ledger).includes(sensitive));
});
test("no header/cookie/email or client-supplied identity can substitute for a signed JWT", async () => {
  const h = setup();
  await rejectedWithoutReservation(h.handle, h.storage, request(null, CONTRACT, { headers: { "Cf-Access-Authenticated-User-Email": SUBJECTS[0], "X-User-Id": SUBJECTS[0], Authorization: `Bearer ${goodToken}`, Cookie: `CF_Authorization=${goodToken}` } }));
  assert.equal(h.requests.length, 0);
});
test("wrong algorithm, token header URLs, critical headers and malformed signatures fail closed", async () => {
  const headers = [{ alg: "none" }, { alg: "HS256" }, { alg: "RS512" }, { kid: "" }, { typ: "wrong" }, { jku: "https://attacker.invalid/keys" }, { x5u: "https://attacker.invalid/key" }, { jwk: publicJwk }, { crit: ["b64"], b64: false }];
  for (const patch of headers) {
    const h = setup(); await rejectedWithoutReservation(h.handle, h.storage, request(await jwt({}, patch))); assert.equal(h.requests.length, 0);
  }
  for (const token of ["", "x.y.z", "a.b", `${goodToken}=`, goodToken.replace(/\.[^.]+$/, ".AA"), "x".repeat(16385)]) {
    const h = setup(); await rejectedWithoutReservation(h.handle, h.storage, request(token));
  }
});
test("forged signature and unknown key never reserve", async () => {
  const alternate = await crypto.subtle.generateKey({ name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" }, true, ["sign", "verify"]);
  for (const token of [await jwt({}, {}, alternate.privateKey), await jwt({}, { kid: "unrecognized" })]) {
    const h = setup(); assert.equal((await rejectedWithoutReservation(h.handle, h.storage, request(token))).status, 403);
  }
});
test("issuer/audience/subject/exp/nbf/iat checks reject even validly signed tokens", async () => {
  const claims = [{ iss: "https://attacker.invalid" }, { iss: `${ISSUER}/` }, { iss: "https://other.cloudflareaccess.com" }, { aud: "other" }, { aud: [] }, { aud: [AUD, 42] }, { aud: null }, { sub: "not-allowlisted" }, { sub: "*" }, { sub: "" }, { sub: null }, { exp: EPOCH / 1000 }, { exp: EPOCH / 1000 - 1 }, { exp: "9999999999" }, { exp: null }, { nbf: EPOCH / 1000 + 1 }, { nbf: "0" }, { iat: EPOCH / 1000 + 1 }, { iat: null }];
  for (const patch of claims) {
    const h = setup(); const reply = await rejectedWithoutReservation(h.handle, h.storage, request(await jwt(patch)));
    assert.equal(reply.status, 403, JSON.stringify(patch)); assert.equal(h.requests.length, 0);
  }
  const h = setup(); assert.equal((await result(h.handle, request(await jwt({ aud: AUD, nbf: undefined, iat: undefined })))).body.allowed, true);
});
test("no arbitrary request fields, prompt/source content, alternative models, or reduced reservations", async () => {
  const bodies = [{ ...CONTRACT, question: "secret test question" }, { ...CONTRACT, sources: [] }, { ...CONTRACT, user: SUBJECTS[0] }, { ...CONTRACT, model: "other" }, { ...CONTRACT, provider: "Other" }, { ...CONTRACT, max_input_tokens: 1 }, { ...CONTRACT, max_completion_tokens: 1023 }, { ...CONTRACT, max_completion_tokens: "1024" }, { ...CONTRACT, operation: undefined }, [], null];
  for (const body of bodies) { const h = setup(); await rejectedWithoutReservation(h.handle, h.storage, request(goodToken, body)); assert.equal(h.requests.length, 0); }
  const h = setup(); const reply = await rejectedWithoutReservation(h.handle, h.storage, request(goodToken, { extra: "x".repeat(1025) })); assert.equal(reply.status, 413);
});
test("fixed route and JSON-only requests have no permissive fallback", async () => {
  for (const options of [{ url: "https://research-admission/authorize?user=anything" }, { url: "https://research-admission/authorize/" }, { url: "https://research-admission/refund" }, { method: "GET" }, { headers: { "Content-Type": "text/plain" } }]) {
    const h = setup(); await rejectedWithoutReservation(h.handle, h.storage, request(goodToken, CONTRACT, options));
  }
});
test("malformed/absent owner config cannot initiate JWKS fetches or reservations", async () => {
  const patches = [{ RESEARCH_BILLING_REVIEWED: "false" }, { ACCESS_TEAM_DOMAIN: "https://offline-test.cloudflareaccess.com.attacker.invalid" }, { ACCESS_TEAM_DOMAIN: "https://offline-test.cloudflareaccess.com/" }, { ACCESS_TEAM_DOMAIN: "https://offline-test.cloudflareaccess.com:443" }, { ACCESS_TEAM_DOMAIN: "https://name:pass@offline-test.cloudflareaccess.com" }, { ACCESS_TEAM_DOMAIN: "http://offline-test.cloudflareaccess.com" }, { ACCESS_TEAM_DOMAIN: "https://a.b.cloudflareaccess.com" }, { ACCESS_TEAM_DOMAIN: `${ISSUER}?x=1` }, { ACCESS_TEAM_DOMAIN: undefined }, { ACCESS_AUD: "" }, { ACCESS_AUD: "*" }, { ACCESS_ALLOWED_SUBJECTS: "[]" }, { ACCESS_ALLOWED_SUBJECTS: '["*"]' }, { ACCESS_ALLOWED_SUBJECTS: '"user"' }, { ACCESS_ALLOWED_SUBJECTS: JSON.stringify([SUBJECTS[0], SUBJECTS[0]]) }, { ACCESS_ALLOWED_SUBJECTS: JSON.stringify(Array.from({ length: 101 }, (_, i) => `user-${i}`)) }, { RESEARCH_DAILY_USER_CALLS: "0" }, { RESEARCH_DAILY_GLOBAL_CALLS: "-1" }, { RESEARCH_DAILY_GLOBAL_TOKENS: "1e9" }, { RESEARCH_DAILY_USER_TOKENS: "1.5" }, { RESEARCH_INPUT_USD_MICROS_PER_MILLION_TOKENS: "" }, { RESEARCH_OUTPUT_USD_MICROS_PER_MILLION_TOKENS: "9007199254740992" }, { RESEARCH_DAILY_GLOBAL_USD_MICROS: undefined }];
  for (const patch of patches) {
    const h = setup({ env: config(patch) }); const reply = await rejectedWithoutReservation(h.handle, h.storage, request());
    assert.equal(reply.body.code, "CONFIG_INVALID", JSON.stringify(patch)); assert.equal(h.requests.length, 0);
  }
});
test("price math rounds up, uses integer microdollars and never silently supplies rates", () => {
  assert.equal(loadConfig(config({ RESEARCH_INPUT_USD_MICROS_PER_MILLION_TOKENS: "1", RESEARCH_OUTPUT_USD_MICROS_PER_MILLION_TOKENS: "1" })).reservation.usdMicros, 1);
  assert.equal(loadConfig(config()).reservation.usdMicros, 18432);
});
test("per-user calls and tokens deny without consuming someone else's global allowance", async () => {
  for (const patch of [{ RESEARCH_DAILY_USER_CALLS: "1" }, { RESEARCH_DAILY_USER_TOKENS: "17408" }]) {
    const h = setup({ env: config(patch) }); assert.equal((await result(h.handle)).body.allowed, true);
    const before = structuredClone(h.storage.ledger());
    assert.equal((await result(h.handle)).body.code, "BUDGET_EXHAUSTED"); assert.deepEqual(h.storage.ledger(), before);
    assert.equal((await result(h.handle, request(await jwt({ sub: SUBJECTS[1] })))).body.allowed, true);
    assert.equal(h.storage.ledger().global.calls, 2);
  }
});
test("global calls, tokens and spending separately enforce hard reservation limits", async () => {
  for (const patch of [{ RESEARCH_DAILY_GLOBAL_CALLS: "1" }, { RESEARCH_DAILY_GLOBAL_TOKENS: "17408" }, { RESEARCH_DAILY_GLOBAL_USD_MICROS: "18432" }]) {
    const h = setup({ env: config(patch) }); assert.equal((await result(h.handle)).body.allowed, true);
    const before = structuredClone(h.storage.ledger());
    const denied = await result(h.handle, request(await jwt({ sub: SUBJECTS[1] })));
    assert.equal(denied.status, 429); assert.equal(denied.body.code, "BUDGET_EXHAUSTED"); assert.deepEqual(h.storage.ledger(), before);
  }
  const h = setup({ env: config({ RESEARCH_DAILY_GLOBAL_USD_MICROS: "18431" }) });
  assert.equal((await result(h.handle)).body.allowed, false); assert.equal(h.storage.writes, 0);
});
test("100 simultaneous reservations across two users cannot exceed the global cap", async () => {
  const h = setup({ env: config({ RESEARCH_DAILY_USER_CALLS: "100", RESEARCH_DAILY_GLOBAL_CALLS: "7", RESEARCH_DAILY_USER_TOKENS: "1740800", RESEARCH_DAILY_GLOBAL_TOKENS: "1740800" }) });
  const otherToken = await jwt({ sub: SUBJECTS[1] });
  const replies = await Promise.all(Array.from({ length: 100 }, (_, i) => result(h.handle, request(i % 2 ? otherToken : goodToken))));
  assert.equal(replies.filter(reply => reply.body.allowed).length, 7); assert.equal(h.storage.writes, 7); assert.equal(h.storage.ledger().global.calls, 7);
  assert.equal(h.storage.ledger().global.tokens, 7 * 17408); assert.equal(h.requests.length, 1);
});
test("global state survives handler replacement and rollover cannot move backwards", async () => {
  let time = EPOCH;
  const env = config({ RESEARCH_DAILY_GLOBAL_CALLS: "1" }); const storage = new TransactionStorage();
  let h = setup({ env, storage, now: () => time }); assert.equal((await result(h.handle)).body.allowed, true);
  h = setup({ env, storage, now: () => time }); assert.equal((await result(h.handle)).body.allowed, false);
  time = Date.parse("2026-10-06T00:00:00Z");
  const tomorrow = await jwt({ nbf: time / 1000 - 10, iat: time / 1000 - 10, exp: time / 1000 + 600 });
  assert.equal((await result(h.handle, request(tomorrow))).body.allowed, true); assert.equal(storage.ledger().day, "2026-10-06"); assert.equal(storage.ledger().global.calls, 1);
  time = EPOCH;
  assert.equal((await result(h.handle)).body.code, "CLOCK_ROLLBACK"); assert.equal(storage.ledger().day, "2026-10-06");
});
test("JWT exp is rechecked inside the transaction after waiting for storage", async () => {
  let time = EPOCH; const storage = new TransactionStorage();
  const original = storage.transaction.bind(storage);
  storage.transaction = async callback => { time = EPOCH + 3600000; return original(callback); };
  const h = setup({ storage, now: () => time }); const reply = await result(h.handle);
  assert.equal(reply.body.code, "UNAUTHENTICATED"); assert.equal(storage.writes, 0);
});
test("corrupt or unavailable storage fails closed and never reports allowed:true", async () => {
  const storage = new TransactionStorage(new Map([[LEDGER_KEY, { day: "2026-10-05", global: { calls: -1 } }]]));
  const h = setup({ storage }); assert.equal((await result(h.handle)).body.code, "LEDGER_INVALID"); assert.equal(storage.writes, 0);
  const failed = setup({ storage: { transaction: async () => { throw new Error("synthetic private storage error"); } } });
  assert.deepEqual((await result(failed.handle)).body, { allowed: false, code: "UNAVAILABLE" });
});
test("repeated requests each consume a new reservation; no idempotent reuse/refund endpoint exists", async () => {
  const h = setup();
  assert.equal((await result(h.handle)).body.allowed, true); assert.equal((await result(h.handle)).body.allowed, true);
  assert.equal(h.storage.ledger().global.calls, 2);
  assert.equal((await result(h.handle, request(goodToken, CONTRACT, { url: "https://research-admission/refund" }))).status, 404);
  assert.equal(h.storage.ledger().global.calls, 2);
});
test("JWKS caching coalesces requests, never fetches attacker key URLs, and expires closed", async () => {
  let time = EPOCH; let fail = false; let fetched = 0;
  const h = setup({ now: () => time, fetchImpl: async (url, options) => {
    fetched++; assert.equal(url, `${ISSUER}/cdn-cgi/access/certs`); assert.equal(options.redirect, "error");
    if (fail) throw new Error("offline JWKS failure"); return Response.json({ keys: [publicJwk] });
  } });
  assert.equal((await result(h.handle)).body.allowed, true);
  for (let i = 0; i < 3; i++) assert.equal((await result(h.handle, request(await jwt({}, { kid: `unknown-${i}` })))).body.allowed, false);
  assert.equal(fetched, 1); fail = true; time += 300001;
  assert.equal((await result(h.handle)).body.allowed, false); assert.equal(fetched, 2);
  assert.equal((await result(h.handle)).body.allowed, false); assert.equal(fetched, 2); assert.equal(h.storage.writes, 1);
  fail = false; time += 5001; assert.equal((await result(h.handle)).body.allowed, true); assert.equal(fetched, 3);
});
test("redirects, bad URLs, oversized, duplicate, private, weak and algorithm-confused JWKS fail closed", async () => {
  const factories = [
    () => Response.redirect("https://attacker.invalid/keys", 302),
    () => { const r = Response.json({ keys: [publicJwk] }); Object.defineProperty(r, "url", { value: "https://attacker.invalid/keys" }); return r; },
    () => { const r = Response.json({ keys: [publicJwk] }); Object.defineProperty(r, "redirected", { value: true }); return r; },
    () => new Response("not JSON", { headers: { "Content-Type": "text/html" } }),
    () => new Response("{"),
    () => Response.json({ keys: [publicJwk], excess: "x".repeat(32769) }),
    () => Response.json({ keys: [] }),
    () => Response.json({ keys: [publicJwk, publicJwk] }),
    () => Response.json({ keys: [{ ...publicJwk, d: "not-a-private-key" }] }),
    () => Response.json({ keys: [{ ...publicJwk, alg: "HS256" }] }),
    () => Response.json({ keys: [{ ...publicJwk, use: "enc" }] }),
    () => Response.json({ keys: [{ ...publicJwk, n: "AQAB" }] }),
  ];
  for (const factory of factories) {
    const h = setup({ fetchImpl: async () => factory() }); await rejectedWithoutReservation(h.handle, h.storage, request());
  }
});
test("request streams are bounded, time out and honor cancellation", async () => {
  let cancelled = false;
  const stalled = new Response(new ReadableStream({ cancel() { cancelled = true; } }));
  await assert.rejects(readBoundedJson(stalled, 1024, { timeoutMs: 15 })); assert.equal(cancelled, true);
  const controller = new AbortController(); controller.abort(); const h = setup();
  assert.equal((await result(h.handle, request(goodToken, CONTRACT, { signal: controller.signal }))).body.allowed, false); assert.equal(h.storage.writes, 0);
});
test("never logs raw JWTs, subject identifiers, errors, or any request content", async () => {
  const captured = []; const originals = {};
  try {
    for (const method of ["log", "warn", "error", "info", "debug"]) { originals[method] = console[method]; console[method] = (...args) => captured.push(args); }
    const h = setup(); await result(h.handle); await result(h.handle, request("bad.token.signature"));
    const denied = setup({ fetchImpl: async () => { throw new Error(goodToken); } });
    const reply = await result(denied.handle); assert.ok(!JSON.stringify(reply.body).includes(goodToken));
  } finally { for (const [method, original] of Object.entries(originals)) console[method] = original; }
  assert.deepEqual(captured, []);
});
test("worker dispatch always uses the one fixed global DO and strips identity lookalike headers", async () => {
  const seen = []; const h = setup();
  const env = { ...h.env, RESEARCH_BUDGET: {
    idFromName(name) { seen.push(name); return "synthetic-do-id"; },
    get(id) { assert.equal(id, "synthetic-do-id"); return { fetch: req => { assert.equal(req.headers.has("x-user-id"), false); return h.handle(req); } }; },
  } };
  const reply = await worker.fetch(request(goodToken, CONTRACT, { headers: { "x-user-id": SUBJECTS[1] } }), env);
  assert.equal((await reply.json()).allowed, true); assert.deepEqual(seen, [GLOBAL_OBJECT_NAME]);
  assert.equal((await (await worker.fetch(request(), config())).json()).allowed, false);
  assert.equal(typeof ResearchBudget.prototype.fetch, "function");
});
test("existing provider adapter consumes admission without sending question/source contents", async () => {
  const h = setup({ env: config({ RESEARCH_DAILY_GLOBAL_CALLS: "1" }) }); let providerCalls = 0;
  const env = { RESEARCH_ENABLED: "true", MINIMAX_API_KEY: "synthetic-unused-test-key", MINIMAX_BILLING_REVIEWED: "true", RESEARCH_ADMISSION: { fetch: async req => {
    assert.deepEqual(await req.clone().json(), CONTRACT); return h.handle(req);
  } } };
  const provider = async () => { providerCalls++; assert.equal(h.storage.ledger().global.calls, 1); return Response.json({ choices: [{ finish_reason: "stop", message: { content: '{"evidence_ids":["offline-evidence"]}' } }] }); };
  const sources = [{ evidence_id: "offline-evidence", source_id: "S-001", title: "Synthetic public title" }];
  assert.deepEqual(await selectResearchSources({ question: "Synthetic public query", history: [] }, sources, env, request(), provider), ["offline-evidence"]);
  await assert.rejects(selectResearchSources({ question: "Synthetic public query", history: [] }, sources, env, request(), provider), error => error.code === "ADMISSION_DENIED");
  assert.equal(providerCalls, 1);
});
test("example config is disabled, private-only and contains no preapproved spending or identity", async () => {
  const content = await readFile(new URL("../../../workers/research-admission/wrangler.example.jsonc", import.meta.url), "utf8");
  const parsed = JSON.parse(content.replace(/^\s*\/\/.*$/gm, ""));
  assert.equal(parsed.workers_dev, false); assert.equal(parsed.preview_urls, false); assert.deepEqual(parsed.routes, []);
  assert.equal(parsed.observability.enabled, false); assert.equal(parsed.vars.ADMISSION_ENABLED, "false");
  assert.equal(parsed.vars.RESEARCH_BILLING_REVIEWED, "false"); assert.equal(parsed.vars.ACCESS_ALLOWED_SUBJECTS, "[]");
});

test("inconsistent global totals, malformed dates and reduced same-day caps never reset usage", async () => {
  const h = setup(); await result(h.handle);
  for (const modify of [value => { value.global.calls = 0; }, value => { value.day = "2026-02-31"; }, value => { value.extra = "unexpected"; }]) {
    const ledger = structuredClone(h.storage.ledger()); modify(ledger);
    const storage = new TransactionStorage(new Map([[LEDGER_KEY, ledger]])); const corrupt = setup({ storage });
    assert.equal((await result(corrupt.handle)).body.code, "LEDGER_INVALID"); assert.equal(storage.writes, 0);
  }
  const tightened = setup({ env: config({ RESEARCH_DAILY_GLOBAL_TOKENS: "1" }), storage: h.storage });
  assert.equal((await result(tightened.handle)).body.code, "BUDGET_EXHAUSTED"); assert.equal(h.storage.ledger().global.calls, 1);
});
test("stalled JWKS headers or body are cancelled after one bounded attempt", async () => {
  for (const stallBody of [false, true]) {
    let calls = 0; let signal; let cancelled = false;
    const h = setup({ fetchImpl: async (_url, options) => {
      calls++; signal = options.signal;
      return stallBody ? new Response(new ReadableStream({ cancel() { cancelled = true; } }), { headers: { "Content-Type": "application/json" } }) : new Promise(() => {});
    } });
    const before = Date.now(); const reply = await rejectedWithoutReservation(h.handle, h.storage, request());
    assert.equal(reply.status, 503); assert.equal(calls, 1); assert.equal(signal.aborted, true);
    assert.ok(Date.now() - before < 5000); if (stallBody) assert.equal(cancelled, true);
  }
});
test("a provider failure keeps its committed reservation and is never retried", async () => {
  const h = setup({ env: config({ RESEARCH_DAILY_GLOBAL_CALLS: "1" }) }); let providerCalls = 0;
  const env = { RESEARCH_ENABLED: "true", MINIMAX_API_KEY: "synthetic-unused-test-key", MINIMAX_BILLING_REVIEWED: "true", RESEARCH_ADMISSION: { fetch: h.handle } };
  const input = { question: "Synthetic public query", history: [] }; const sources = [{ evidence_id: "test-id", title: "Synthetic title" }];
  const provider = async () => { providerCalls++; throw new Error("offline failure"); };
  await assert.rejects(selectResearchSources(input, sources, env, request(), provider));
  assert.equal(h.storage.ledger().global.calls, 1); assert.equal(providerCalls, 1);
  await assert.rejects(selectResearchSources(input, sources, env, request(), provider), error => error.code === "ADMISSION_DENIED");
  assert.equal(h.storage.ledger().global.calls, 1); assert.equal(providerCalls, 1);
});
