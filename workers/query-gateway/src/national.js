// Public metadata only. The national store is independent of publication snapshots.
const FIELDS = "id source_id kind jurisdiction external_id title case_number event_date authority official_url provider_url license rights_class fetched_at".split(" ");
const SELECT = FIELDS.map(field => `r.${field}`).join(",");
const project = row => Object.fromEntries(FIELDS.map(field => [field, row[field] ?? null]));
const positiveId = value => /^[1-9]\d*$/.test(String(value)) && Number.isSafeInteger(Number(value));
const validDate = value => typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)
  && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;

export function encodeCursor(row) {
  return btoa(JSON.stringify({ date: row.event_date, id: row.id })).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function decodeCursor(value) {
  if (!/^[A-Za-z0-9_-]{1,256}$/.test(value)) throw new Error("invalid cursor");
  const cursor = JSON.parse(atob(value.replaceAll("-", "+").replaceAll("_", "/")));
  if (!cursor || Object.keys(cursor).sort().join(",") !== "date,id"
      || typeof cursor.id !== "number" || !positiveId(cursor.id)
      || (cursor.date !== null && !validDate(cursor.date))) throw new Error("invalid cursor");
  return cursor;
}

// Totals only for full-text (3+ chars) queries: a 2-char LIKE count scans every row (82k reads on 2026-11-23).
const TOTAL_CAP = 1000;

function searchQuery(params) {
  const conditions = [], values = [];
  const q = (params.get("q") || "").trim();
  if (q.length > 1000 || q.includes("\0")) throw new Error("invalid q");
  if ([...q].length >= 3) {
    conditions.push("r.id IN (SELECT rowid FROM records_fts WHERE records_fts MATCH ?)");
    values.push(`"${q.replaceAll('"', '""')}"`);
  } else if (q) {
    conditions.push("r.title LIKE ? ESCAPE '\\'");
    values.push(`%${q.replace(/[\\%_]/g, "\\$&")}%`);
  }
  for (const key of ["jurisdiction", "kind"]) {
    if (params.has(key)) {
      const value = params.get(key);
      if (!value || value.length > 100) throw new Error("invalid filter");
      conditions.push(`r.${key} = ?`);
      values.push(value);
    }
  }
  for (const [key, operator] of [["from", ">="], ["to", "<="]]) {
    if (params.has(key)) {
      const value = params.get(key);
      if (!validDate(value)) throw new Error("invalid date");
      conditions.push(`r.event_date ${operator} ?`);
      values.push(value);
    }
  }
  if (params.has("from") && params.has("to") && params.get("from") > params.get("to")) throw new Error("invalid range");
  const count = [...q].length >= 3 && !params.has("cursor") ? {
    sql: `SELECT COUNT(*) AS total FROM (SELECT 1 FROM records r WHERE ${conditions.join(" AND ")} LIMIT ${TOTAL_CAP + 1})`,
    values: [...values] } : null;
  if (params.has("cursor")) {
    const cursor = decodeCursor(params.get("cursor"));
    // SQLite DESC puts null dates last; retain undated rows on every page.
    if (cursor.date === null) {
      conditions.push("(r.event_date IS NULL AND r.id < ?)");
      values.push(cursor.id);
    } else {
      conditions.push("(r.event_date < ? OR (r.event_date = ? AND r.id < ?) OR r.event_date IS NULL)");
      values.push(cursor.date, cursor.date, cursor.id);
    }
  }
  const rawLimit = params.get("limit") ?? "20";
  if (!/^\d+$/.test(rawLimit) || !Number.isSafeInteger(Number(rawLimit)) || Number(rawLimit) < 1) throw new Error("invalid limit");
  const limit = Math.min(50, Number(rawLimit));
  values.push(limit + 1);
  return { sql: `SELECT ${SELECT} FROM records r${conditions.length ? " WHERE " + conditions.join(" AND ") : ""} ORDER BY r.event_date DESC,r.id DESC LIMIT ?`, values, limit, count };
}

export async function handleNational(request, env, responseJson) {
  const reply = (payload, status) => responseJson(payload, status, request, env);
  const url = new URL(request.url);
  if (request.method !== "GET") return reply({ error: "NOT_FOUND" }, 404);
  const search = url.pathname === "/national/search";
  const record = url.pathname.startsWith("/national/record/");
  if (!search && !record) return reply({ error: "NOT_FOUND" }, 404);
  let query;
  try {
    if (search) query = searchQuery(url.searchParams);
    else {
      const id = url.pathname.slice("/national/record/".length);
      if (!positiveId(id)) throw new Error("invalid id");
      query = { sql: `SELECT ${SELECT} FROM records r WHERE r.id = ?`, values: [Number(id)] };
    }
  } catch {
    return reply({ error: "INVALID_ARGUMENTS" }, 400);
  }
  try {
    if (!env.NATIONAL_DB) throw new Error("missing binding");
    const statement = env.NATIONAL_DB.prepare(query.sql).bind(...query.values);
    if (record) {
      const row = await statement.first();
      return row ? reply(project(row), 200) : reply({ error: "NOT_FOUND" }, 404);
    }
    const result = await statement.all();
    if (result.success === false || !Array.isArray(result.results)) throw new Error("query failed");
    const rows = result.results.slice(0, query.limit);
    let total;
    if (query.count) {
      // The total is optional; a failed count must not fail the search itself.
      try { total = (await env.NATIONAL_DB.prepare(query.count.sql).bind(...query.count.values).first())?.total; } catch { total = undefined; }
    }
    return reply({ records: rows.map(project), next_cursor: result.results.length > query.limit
      ? encodeCursor(rows.at(-1)) : null,
      ...(Number.isSafeInteger(total) && total >= 0 ? { total: Math.min(total, TOTAL_CAP), total_capped: total > TOTAL_CAP } : {}) }, 200);
  } catch {
    return reply({ error: "NATIONAL_STORE_UNAVAILABLE" }, 503);
  }
}
