import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { buildSearchUrl, validateNationalResponse, errorMessage, KIND_LABELS, JURISDICTIONS } from "../lib/national-search.js";

test("search URL omits empty/unknown filters, encodes text, and fixes page size", () => {
  const url = new URL(buildSearchUrl("https://gateway.example/query/", {
    q: "  警察 & 消防  ", jurisdiction: "臺中市", kind: "bill", from: "", to: " ", limit: 50, full_text: "secret",
  }));
  assert.equal(url.pathname, "/national/search");
  assert.deepEqual(Object.fromEntries(url.searchParams), { q: "警察 & 消防", jurisdiction: "臺中市", kind: "bill", limit: "20" });
  assert.equal(buildSearchUrl("http://localhost:8787", {}), "http://localhost:8787/national/search?limit=20");
  assert.equal(new URL(buildSearchUrl("https://gateway.example/", {}, "next_A-1")).searchParams.get("cursor"), "next_A-1");
});

test("date and query validation rejects impossible dates and reversed ranges", () => {
  for (const from of ["2026-2-01", "2026-02-29", "2026-04-31", "2026-13-01", "2026-01-01T00:00:00Z"]) {
    assert.throws(() => buildSearchUrl("https://gateway.example", { from }), { code: "INVALID_ARGUMENTS" });
  }
  assert.throws(() => buildSearchUrl("https://gateway.example", { from: "2026-02-02", to: "2026-02-01" }), { code: "INVALID_ARGUMENTS" });
  assert.equal(new URL(buildSearchUrl("https://gateway.example", { from: "2024-02-29", to: "2024-02-29" })).searchParams.get("to"), "2024-02-29");
  for (const q of ["x".repeat(1001), "a\0b", {}]) assert.throws(() => buildSearchUrl("https://gateway.example", { q }));
  for (const base of ["", "javascript:alert(1)", "https://user:pass@gateway.example", "http://remote.example", "https://gateway.example?token=x"]) {
    assert.throws(() => buildSearchUrl(base), { code: "INVALID_GATEWAY" });
  }
});

test("response projects only public fields, preserves unknown kinds and never mutates input", () => {
  const row = { id: 1, title: "公開議案", kind: "future_kind", official_url: "https://data.ly.gov.tw/", full_text: "private", content_sha256: "internal" };
  const output = validateNationalResponse({ records: [row], next_cursor: null, debug: "private" });
  assert.deepEqual(Object.keys(output), ["records", "next_cursor"]);
  assert.deepEqual(Object.keys(output.records[0]), "id source_id kind jurisdiction external_id title case_number event_date authority official_url provider_url license rights_class fetched_at".split(" "));
  assert.equal(output.records[0].kind, "future_kind");
  assert.equal(output.records[0].official_url, row.official_url);
  assert.equal(output.records[0].case_number, null);
  assert.equal(row.full_text, "private");
  for (const records of [null, [null], [{ id: 0 }], [{ id: 1, title: {} }]]) {
    assert.throws(() => validateNationalResponse({ records, next_cursor: null }), { code: "INVALID_RESPONSE" });
  }
});

test("unsafe official links are null", () => {
  for (const official_url of ["http://example.gov.tw", "javascript:alert(1)", "https://user:pass@example.gov.tw/", "https://official.invalid", "/relative", {}, null]) {
    assert.equal(validateNationalResponse({ records: [{ id: 1, official_url }], next_cursor: null }).records[0].official_url, null);
  }
});

test("cursor accepts only null or 1–256 URL-safe characters", () => {
  for (const next_cursor of ["", "x".repeat(257), "abc=", "a/b", "a+b", "a b", "abc\n", 12, {}, undefined]) {
    assert.throws(() => validateNationalResponse({ records: [], next_cursor }), { code: "INVALID_RESPONSE" });
    if (next_cursor !== undefined) assert.throws(() => buildSearchUrl("https://gateway.example", {}, next_cursor), { code: "INVALID_ARGUMENTS" });
  }
  for (const next_cursor of [null, "a", "a_-Z09", "x".repeat(256)]) {
    assert.equal(validateNationalResponse({ records: [], next_cursor }).next_cursor, next_cursor);
  }
});

test("all service error states have useful Chinese explanations", () => {
  assert.equal(errorMessage(503), "全國資料庫目前無法使用（可能尚未載入或今日額度已滿），請稍後再試");
  assert.equal(errorMessage(0, "NATIONAL_STORE_UNAVAILABLE"), errorMessage(503));
  for (const [status, code, pattern] of [[429, null, /次數過多/], [400, null, /YYYY-MM-DD/],
    [404, null, /找不到/], [0, "NETWORK_ERROR", /無法連線/], [0, "TypeError", /無法連線/],
    [0, "TIMEOUT", /逾時/], [0, "TimeoutError", /逾時/], [0, "INVALID_RESPONSE", /格式無法驗證/],
    [0, "INVALID_GATEWAY", /網址設定不正確/], [500, null, /服務發生錯誤/]]) {
    assert.match(errorMessage(status, code), pattern);
  }
});

test("jurisdictions match the 22 registry councils plus the central API filters", async () => {
  const registry = JSON.parse(await readFile(new URL("../../../docs/govintel/national-sources.v1.json", import.meta.url), "utf8"));
  const councils = registry.sources.filter(source => source.national_source_id.startsWith("N-COUNCIL-"));
  assert.equal(councils.length, 22);
  assert.deepEqual(JURISDICTIONS, [...councils.map(source => source.scope.jurisdiction), "立法院", "全國"]);
  assert.equal(new Set(JURISDICTIONS).size, 24);
  assert.deepEqual(Object.keys(KIND_LABELS), ["bill", "bill_gov", "bill_other", "ly_bill", "ly_interp", "tender", "award", "dataset", "traffic_event"]);
});

test("shared navigation retains destinations and adds national; page has accessible states and explicit paging", async () => {
  const frame = await readFile(new URL("../components/GovIntelFrame.js", import.meta.url), "utf8");
  for (const route of ["/", "/public-query/", "/national/", "/research/", "/tracking/", "/live/", "/sources/"]) {
    assert.ok(frame.includes(`["${route}",`));
  }
  assert.match(frame, /\["\/national\/", "全國資料"\]/);
  const page = await readFile(new URL("../app/national/page.js", import.meta.url), "utf8");
  assert.match(page, /aria-live="polite"/);
  assert.match(page, /target="_blank" rel="noreferrer"/);
  assert.match(page, /載入更多/);
  assert.match(page, /官方連結無法驗證/);
  assert.doesNotMatch(page, /IntersectionObserver|dangerouslySetInnerHTML|<dialog/);
});
