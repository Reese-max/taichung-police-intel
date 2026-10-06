// All identity, price and budget policy comes from owner-controlled Worker vars.
export const CONTRACT = Object.freeze({ operation: "govintel-research", provider: "MiniMax", model: "MiniMax-M2.7", max_completion_tokens: 1024, max_input_tokens: 16384 });
export const GLOBAL_OBJECT_NAME = "govintel-research-global-v1";
export const LEDGER_KEY = "ledger-v1";
export const MAX_SUBJECTS = 100;
export const object = value => value !== null && typeof value === "object" && !Array.isArray(value);
export class AdmissionError extends Error {
  constructor(code, status = 503) { super(code); this.code = code; this.status = status; }
}
export function deny(code, status = 503) { throw new AdmissionError(code, status); }
const identifier = value => typeof value === "string" && value.length > 0 && value.length <= 256 && /^[\x21-\x7e]+$/.test(value) && !value.includes("*");
function positiveInteger(value) {
  if (typeof value !== "string" || !/^[1-9]\d{0,15}$/.test(value) || !Number.isSafeInteger(Number(value))) deny("CONFIG_INVALID");
  return Number(value);
}
export function loadConfig(env) {
  if (env?.ADMISSION_ENABLED !== "true") deny("DISABLED");
  if (env.RESEARCH_BILLING_REVIEWED !== "true") deny("CONFIG_INVALID");
  // Never derive a fetch destination from JWT iss/jku/x5u or an incoming URL.
  if (typeof env.ACCESS_TEAM_DOMAIN !== "string" || !/^https:\/\/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com$/.test(env.ACCESS_TEAM_DOMAIN)) deny("CONFIG_INVALID");
  if (!identifier(env.ACCESS_AUD)) deny("CONFIG_INVALID");
  let subjects;
  try { subjects = JSON.parse(env.ACCESS_ALLOWED_SUBJECTS); } catch { deny("CONFIG_INVALID"); }
  if (!Array.isArray(subjects) || subjects.length < 1 || subjects.length > MAX_SUBJECTS || subjects.some(subject => !identifier(subject)) || new Set(subjects).size !== subjects.length) deny("CONFIG_INVALID");
  const limits = {
    userCalls: positiveInteger(env.RESEARCH_DAILY_USER_CALLS),
    globalCalls: positiveInteger(env.RESEARCH_DAILY_GLOBAL_CALLS),
    userTokens: positiveInteger(env.RESEARCH_DAILY_USER_TOKENS),
    globalTokens: positiveInteger(env.RESEARCH_DAILY_GLOBAL_TOKENS),
    globalUsdMicros: positiveInteger(env.RESEARCH_DAILY_GLOBAL_USD_MICROS),
  };
  const inputRate = positiveInteger(env.RESEARCH_INPUT_USD_MICROS_PER_MILLION_TOKENS);
  const outputRate = positiveInteger(env.RESEARCH_OUTPUT_USD_MICROS_PER_MILLION_TOKENS);
  // Exact integer arithmetic, rounded UP to a microdollar. No assumed prices.
  const numerator = BigInt(CONTRACT.max_input_tokens) * BigInt(inputRate) + BigInt(CONTRACT.max_completion_tokens) * BigInt(outputRate);
  const usdMicros = Number((numerator + 999999n) / 1000000n);
  if (!Number.isSafeInteger(usdMicros) || usdMicros < 1) deny("CONFIG_INVALID");
  return { issuer: env.ACCESS_TEAM_DOMAIN, audience: env.ACCESS_AUD, subjects: new Set(subjects), limits,
    reservation: { calls: 1, tokens: CONTRACT.max_input_tokens + CONTRACT.max_completion_tokens, usdMicros } };
}
export function validateContract(value) {
  if (!object(value) || Object.keys(value).length !== Object.keys(CONTRACT).length || Object.entries(CONTRACT).some(([key, expected]) => value[key] !== expected)) deny("INVALID_REQUEST", 400);
}
export function validateRoute(request) {
  const url = new URL(request.url);
  if (url.pathname !== "/authorize" || url.search) deny("NOT_FOUND", 404);
  if (request.method !== "POST") deny("METHOD_NOT_ALLOWED", 405);
}
export function response(allowed, code, status = 200) {
  return Response.json(code ? { allowed, code } : { allowed }, { status, headers: { "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" } });
}
export function failure(error) {
  return error instanceof AdmissionError ? response(false, error.code, error.status) : response(false, "UNAVAILABLE", 503);
}
