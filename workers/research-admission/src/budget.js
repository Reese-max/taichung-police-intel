import { deny, LEDGER_KEY, MAX_SUBJECTS, object } from "./config.js";
import { validateClaims } from "./auth.js";
const zero = () => ({ calls: 0, tokens: 0, usdMicros: 0 });
function validCounters(value) {
  return object(value) && Object.keys(value).length === 3 && ["calls", "tokens", "usdMicros"].every(key => Number.isSafeInteger(value[key]) && value[key] >= 0);
}
function validLedger(value) {
  if (!object(value) || value.version !== 1 || Object.keys(value).length !== 4 || typeof value.day !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value.day) || !Number.isFinite(Date.parse(`${value.day}T00:00:00Z`)) || new Date(`${value.day}T00:00:00Z`).toISOString().slice(0, 10) !== value.day || !validCounters(value.global) || !object(value.users)) return false;
  const entries = Object.entries(value.users);
  if (entries.length > MAX_SUBJECTS || !entries.every(([key, counters]) => /^[0-9a-f]{64}$/.test(key) && validCounters(counters))) return false;
  // Reject inconsistent storage instead of resetting or undercounting usage.
  return ["calls", "tokens", "usdMicros"].every(field => entries.reduce((sum, [, counters]) => sum + BigInt(counters[field]), 0n) === BigInt(value.global[field]));
}
export async function reserve(storage, verified, config, { now = Date.now, signal } = {}) {
  if (!storage || typeof storage.transaction !== "function") deny("UNAVAILABLE");
  // This is the ONLY read-modify-write path. The fixed global object is shared
  // by every subject. No remote I/O or externally visible side effects in here:
  // the runtime may replay a transaction closure while resolving contention.
  return storage.transaction(async txn => {
    const saved = await txn.get(LEDGER_KEY);
    const nowMs = now();
    validateClaims(verified.claims, config, nowMs);
    if (signal?.aborted) deny("CANCELLED");
    const day = new Date(nowMs).toISOString().slice(0, 10);
    if (saved !== undefined && !validLedger(saved)) deny("LEDGER_INVALID");
    if (saved?.day > day) deny("CLOCK_ROLLBACK");
    const ledger = saved?.day === day ? saved : { version: 1, day, global: zero(), users: {} };
    if (!Object.hasOwn(ledger.users, verified.userKey) && Object.keys(ledger.users).length >= MAX_SUBJECTS) deny("USER_CAPACITY_REACHED", 429);
    const user = ledger.users[verified.userKey] || zero();
    const charge = config.reservation;
    const fits = (current, increment, cap) => current <= cap && increment <= cap - current;
    if (!fits(ledger.global.calls, charge.calls, config.limits.globalCalls) || !fits(ledger.global.tokens, charge.tokens, config.limits.globalTokens) || !fits(ledger.global.usdMicros, charge.usdMicros, config.limits.globalUsdMicros) || !fits(user.calls, charge.calls, config.limits.userCalls) || !fits(user.tokens, charge.tokens, config.limits.userTokens)) deny("BUDGET_EXHAUSTED", 429);
    const add = counters => ({ calls: counters.calls + charge.calls, tokens: counters.tokens + charge.tokens, usdMicros: counters.usdMicros + charge.usdMicros });
    ledger.global = add(ledger.global); ledger.users[verified.userKey] = add(user);
    await txn.put(LEDGER_KEY, ledger);
    return true;
  });
}
