import { safeHttpsUrl } from "./public-query.js";

export const KIND_LABELS = Object.freeze({
  bill: "議會議案", bill_gov: "議會市府提案", bill_other: "議會其他議案", ly_bill: "立法院議案", ly_interp: "立法院質詢",
  tender: "招標", award: "決標", dataset: "開放資料集", traffic_event: "即時路況事件",
});

// Council names follow national-sources.v1.json; central filters follow the W8 API contract.
export const JURISDICTIONS = Object.freeze([
  "臺北市", "新北市", "桃園市", "臺中市", "臺南市", "高雄市", "基隆市", "新竹市",
  "嘉義市", "新竹縣", "苗栗縣", "彰化縣", "南投縣", "雲林縣", "嘉義縣", "屏東縣",
  "宜蘭縣", "花蓮縣", "臺東縣", "澎湖縣", "金門縣", "連江縣", "立法院", "全國",
]);

const FIELDS = "id source_id kind jurisdiction external_id title case_number event_date authority official_url provider_url license rights_class fetched_at".split(" ");
const validCursor = value => value === null || (typeof value === "string" && value.length >= 1
  && value.length <= 256 && !/[^A-Za-z0-9_-]/.test(value));
const fail = code => { throw Object.assign(new Error(errorMessage(0, code)), { code }); };

export function buildSearchUrl(base, filters = {}, cursor = null) {
  if (!filters || typeof filters !== "object" || Array.isArray(filters)) fail("INVALID_ARGUMENTS");
  let url;
  try { url = new URL(base); } catch { fail("INVALID_GATEWAY"); }
  if (url.username || url.password || url.search || url.hash
    || (url.protocol !== "https:" && !(url.protocol === "http:" && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)))) fail("INVALID_GATEWAY");
  url.pathname = `${url.pathname.replace(/\/+$/, "").replace(/\/query$/, "")}/national/search`;
  for (const key of ["q", "jurisdiction", "kind", "from", "to"]) {
    const raw = filters[key];
    if (raw == null || raw === "") continue;
    if (typeof raw !== "string") fail("INVALID_ARGUMENTS");
    const value = raw.trim();
    if (!value) continue;
    if (key === "from" || key === "to") {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(Date.parse(value))
        || new Date(value).toISOString().slice(0, 10) !== value) fail("INVALID_ARGUMENTS");
    } else if (value.length > (key === "q" ? 1000 : 100) || value.includes("\0")) fail("INVALID_ARGUMENTS");
    url.searchParams.set(key, value);
  }
  if (url.searchParams.has("from") && url.searchParams.has("to")
    && url.searchParams.get("from") > url.searchParams.get("to")) fail("INVALID_ARGUMENTS");
  if (!validCursor(cursor)) fail("INVALID_ARGUMENTS");
  if (cursor !== null) url.searchParams.set("cursor", cursor);
  url.searchParams.set("limit", "20");
  return url.href;
}

export function validateNationalResponse(json) {
  if (!json || !Array.isArray(json.records) || !validCursor(json.next_cursor)) fail("INVALID_RESPONSE");
  const records = json.records.map(row => {
    if (!row || typeof row !== "object" || Array.isArray(row)
      || !Number.isSafeInteger(row.id) || row.id < 1) fail("INVALID_RESPONSE");
    const record = {};
    for (const key of FIELDS) {
      const value = row[key] ?? null;
      if (key === "official_url") record[key] = typeof value === "string" ? safeHttpsUrl(value) : null;
      else {
        if (key !== "id" && value !== null && typeof value !== "string") fail("INVALID_RESPONSE");
        record[key] = value;
      }
    }
    return record;
  });
  const total = Number.isSafeInteger(json.total) && json.total >= 0 && typeof json.total_capped === "boolean"
    ? { total: json.total, total_capped: json.total_capped } : {};
  return { records, next_cursor: json.next_cursor, ...total };
}

export function errorMessage(status, code) {
  if (status === 503 || code === "NATIONAL_STORE_UNAVAILABLE") return "全國資料庫目前無法使用（可能尚未載入或今日額度已滿），請稍後再試";
  if (status === 429 || code === "RATE_LIMITED") return "查詢次數過多，請稍候再試。";
  if (status === 400 || code === "INVALID_ARGUMENTS") return "查詢條件不正確，請確認日期為 YYYY-MM-DD、起日不晚於迄日，以及關鍵字長度。";
  if (status === 404 || code === "NOT_FOUND") return "找不到這筆資料或查詢服務，請重新搜尋。";
  if (code === "TIMEOUT" || code === "TimeoutError") return "查詢逾時，請稍後再試。";
  if (code === "INVALID_RESPONSE") return "查詢回應格式無法驗證，請稍後再試。";
  if (code === "INVALID_GATEWAY") return "全國查詢服務網址設定不正確，請聯絡網站管理者。";
  if (code === "NETWORK_ERROR" || code === "TypeError") return "無法連線至全國查詢服務，請檢查網路後再試。";
  return "全國查詢服務發生錯誤，請稍後再試。";
}
