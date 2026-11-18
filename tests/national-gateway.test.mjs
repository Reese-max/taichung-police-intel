import assert from "node:assert/strict";
import test from "node:test";
import gateway from "../workers/query-gateway/src/index.js";
import { encodeCursor } from "../workers/query-gateway/src/national.js";

let sequence = 0;
function fakeDB(rows = []) {
  const calls = [];
  return {
    calls,
    prepare(sql) {
      return { bind(...values) {
        calls.push({ sql, values });
        return { all: async () => ({ success: true, results: rows }), first: async () => rows[0] ?? null };
      } };
    },
  };
}
const row = (id, event_date = "2026-11-18") => ({ id, event_date, title: "測試標題", source_id: "N-COUNCIL-TXG",
  official_url: "https://yishi.tccc.gov.tw/", full_text: "private", content_sha256: "private", internal_secret: "private" });
async function request(path, db, options = {}) {
  const headers = { "CF-Connecting-IP": `fixture-${++sequence}`, ...options.headers };
  return gateway.fetch(new Request(`https://gateway.example${path}`, { ...options, headers }), {
    NATIONAL_DB: db, ALLOWED_ORIGINS: "https://allowed.example",
  });
}

test("search and record expose only public fields, independent of publication snapshot", async () => {
  const expected = "id source_id kind jurisdiction external_id title case_number event_date authority official_url provider_url license rights_class fetched_at".split(" ").sort();
  const db = fakeDB([row(1)]);
  const search = await request("/national/search", db);
  assert.equal(search.status, 200);
  const body = await search.json();
  assert.deepEqual(Object.keys(body.records[0]).sort(), expected);
  assert.equal(body.next_cursor, null);
  assert.deepEqual(Object.keys(await (await request("/national/record/1", db)).json()).sort(), expected);
  assert.equal(db.calls[0].values.at(-1), 21);
  assert.match(db.calls[0].sql, /ORDER BY r.event_date DESC,r.id DESC/);
});

test("limit caps at 50 and keyset cursor round trips including undated rows", async () => {
  const db = fakeDB(Array.from({ length: 51 }, (_, i) => row(100 - i)));
  const response = await request("/national/search?limit=999", db);
  const body = await response.json();
  assert.equal(body.records.length, 50);
  assert.equal(db.calls[0].values.at(-1), 51);
  assert.match(body.next_cursor, /^[A-Za-z0-9_-]+$/);
  const next = fakeDB([]);
  assert.equal((await request(`/national/search?cursor=${body.next_cursor}`, next)).status, 200);
  assert.deepEqual(next.calls[0].values, ["2026-11-18", "2026-11-18", 51, 21]);
  assert.match(next.calls[0].sql, /OR r.event_date IS NULL/);
  const undated = fakeDB([]);
  await request(`/national/search?cursor=${encodeCursor(row(7, null))}`, undated);
  assert.deepEqual(undated.calls[0].values, [7, 21]);
  assert.match(undated.calls[0].sql, /r.event_date IS NULL AND r.id < \?/);
});

test("bad cursors, limits and dates return 400 before querying", async () => {
  const db = fakeDB();
  const cursors = ["", "!", "a", btoa("null"), btoa('{}'), btoa('{"date":"2026-02-30","id":1}'),
    btoa('{"date":"2026-11-18","id":-1}'), btoa('{"date":null,"id":"1"}'),
    btoa('{"date":null,"id":1,"extra":true}')];
  for (const cursor of cursors) assert.equal((await request(`/national/search?cursor=${encodeURIComponent(cursor)}`, db)).status, 400);
  for (const params of ["limit=0", "limit=-1", "limit=abc", "limit=1.5", "from=2026-02-30", "from=2026-11-18&to=2026-01-01"])
    assert.equal((await request(`/national/search?${params}`, db)).status, 400);
  assert.equal(db.calls.length, 0);
});

test("FTS syntax becomes one quoted phrase and filters are bound", async () => {
  const db = fakeDB();
  const q = '警察局" OR title:* NOT "消防';
  const jurisdiction = "臺中市' OR 1=1--";
  const params = new URLSearchParams({ q, jurisdiction, kind: "bill", from: "2026-01-01", to: "2026-12-31" });
  assert.equal((await request(`/national/search?${params}`, db)).status, 200);
  assert.match(db.calls[0].sql, /records_fts MATCH \?/);
  assert.equal(db.calls[0].values[0], '"警察局"" OR title:* NOT ""消防"');
  assert.equal(db.calls[0].values[1], jurisdiction);
  assert.ok(!db.calls[0].sql.includes(jurisdiction));
});

test("short queries use escaped LIKE for percent underscore and backslash", async () => {
  for (const [q, expected] of [["%_", "%\\%\\_%"], ["\\", "%\\\\%"], ["警察", "%警察%"], ["😀", "%😀%"]]) {
    const db = fakeDB();
    assert.equal((await request(`/national/search?q=${encodeURIComponent(q)}`, db)).status, 200);
    assert.match(db.calls[0].sql, /LIKE \? ESCAPE '\\'/);
    assert.equal(db.calls[0].values[0], expected);
  }
});

test("unavailable binding and failed queries return sanitized 503", async () => {
  for (const db of [undefined, { prepare() { throw new Error("credential private detail"); } },
    { prepare() { return { bind() { return { all: async () => ({ success: false }) }; } }; } }]) {
    const response = await request("/national/search", db);
    assert.equal(response.status, 503);
    assert.deepEqual(await response.json(), { error: "NATIONAL_STORE_UNAVAILABLE" });
  }
  const response = await request("/national/record/1", { prepare() { return { bind() {
    return { first: async () => { throw new Error("private"); } };
  } }; } });
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "NATIONAL_STORE_UNAVAILABLE" });
});

test("record IDs are positive safe integers and missing records return 404", async () => {
  const db = fakeDB();
  for (const id of ["0", "-1", "01", "1.5", "1e2", "abc", "9007199254740992", "1/extra", ""])
    assert.equal((await request(`/national/record/${id}`, db)).status, 400);
  assert.equal(db.calls.length, 0);
  assert.equal((await request("/national/record/42", db)).status, 404);
  assert.deepEqual(db.calls[0].values, [42]);
});

test("national routes retain gateway CORS and rate limits", async () => {
  const db = fakeDB();
  const allowed = await request("/national/search", db, { headers: { Origin: "https://allowed.example" } });
  assert.equal(allowed.headers.get("Access-Control-Allow-Origin"), "https://allowed.example");
  assert.equal((await request("/national/search", db, { headers: { Origin: "https://evil.example" } })).status, 403);
  assert.equal((await request("/national/search", db, { method: "OPTIONS", headers: { Origin: "https://allowed.example" } })).status, 204);
  let response;
  for (let i = 0; i < 61; i++) response = await request("/national/search", db, { headers: { "CF-Connecting-IP": "fixed-rate-test" } });
  assert.equal(response.status, 429);
});
