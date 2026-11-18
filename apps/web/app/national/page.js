"use client";

import { useEffect, useRef, useState } from "react";
import { buildSearchUrl, validateNationalResponse, errorMessage, KIND_LABELS, JURISDICTIONS } from "../../lib/national-search.js";
import "./national.css";

const GATEWAY = (process.env.NEXT_PUBLIC_QUERY_GATEWAY_URL || "").trim();
const EMPTY = { q: "", jurisdiction: "", kind: "", from: "", to: "" };

export default function NationalPage() {
  const [draft, setDraft] = useState(EMPTY);
  const [results, setResults] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");
  const sequence = useRef(0);
  const keyword = useRef(null);
  useEffect(() => () => { sequence.current += 1; }, []);

  async function search(cursor = null) {
    if (!GATEWAY) return;
    const request = ++sequence.current;
    const submitted = cursor ? results.filters : { ...draft };
    setState("loading");
    setError("");
    if (!cursor) setResults(null);
    try {
      const url = buildSearchUrl(GATEWAY, submitted, cursor);
      const response = await fetch(url, { cache: "no-store", signal: AbortSignal.timeout(12000) });
      let json;
      try { json = await response.json(); }
      catch {
        throw Object.assign(new Error(), { status: response.status, code: "INVALID_RESPONSE" });
      }
      if (!response.ok) throw Object.assign(new Error(), { status: response.status, code: json?.error });
      const data = validateNationalResponse(json);
      if (sequence.current !== request) return;
      setResults(previous => ({
        records: cursor ? [...new Map([...previous.records, ...data.records].map(row => [row.id, row])).values()] : data.records,
        next_cursor: data.next_cursor, filters: submitted,
      }));
      setState("ready");
    } catch (reason) {
      if (sequence.current !== request) return;
      setError(errorMessage(reason.status, reason.name === "TimeoutError" ? "TIMEOUT" : reason.code || reason.name));
      setState("error");
    }
  }

  function clear() {
    sequence.current += 1;
    setDraft(EMPTY); setResults(null); setError(""); setState("idle");
    keyword.current?.focus();
  }

  const changed = results && Object.keys(EMPTY).some(key => draft[key] !== results.filters[key]);
  const field = key => ({ value: draft[key], onChange: event => setDraft(previous => ({ ...previous, [key]: event.target.value })) });
  const status = !GATEWAY ? "尚未設定全國查詢服務（gateway），目前無法搜尋。"
    : state === "loading" ? "載入中，請稍候。"
      : state === "error" ? error
        : results ? (results.records.length ? `已載入 ${results.records.length} 筆資料。${results.next_cursor ? "可載入更多。" : "已顯示全部搜尋結果。"}` : "沒有符合條件的資料，請調整條件後再試。")
          : "輸入條件後按搜尋；留白可瀏覽目前已載入的中繼資料。";

  return <main className="national-page" aria-labelledby="national-title">
    <header>
      <h1 id="national-title">全國資料</h1>
      <p>查找議案、質詢、採購與開放資料，回到官方原頁核對。</p>
      <div className="national-notice">
        <p>這裡只顯示中繼資料與官方連結，不是治理回答的唯一證據。</p>
        <p>資料每日更新，部分來源有每日寫入上限，首次回填需要數天。TDX 未設定金鑰。</p>
      </div>
    </header>

    <form className="national-form" onSubmit={event => { event.preventDefault(); search(); }}>
      <div className="pq-field national-keyword">
        <label htmlFor="national-q">關鍵字</label>
        <input id="national-q" ref={keyword} type="search" maxLength={1000} aria-describedby="national-keyword-help" {...field("q")} />
        <small id="national-keyword-help">3 個字以上全文比對，1–2 個字比對標題</small>
      </div>
      <div className="pq-field">
        <label htmlFor="national-jurisdiction">縣市／機關</label>
        <select id="national-jurisdiction" aria-describedby="national-jurisdiction-help" {...field("jurisdiction")}>
          <option value="">全部縣市／機關</option>
          {JURISDICTIONS.map(value => <option key={value} value={value}>{value}</option>)}
        </select>
        <small id="national-jurisdiction-help">未標示縣市／機關的資料，請選「全部」並用類型篩選。</small>
      </div>
      <div className="pq-field">
        <label htmlFor="national-kind">類型</label>
        <select id="national-kind" {...field("kind")}>
          <option value="">全部類型</option>
          {Object.entries(KIND_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      </div>
      <div className="pq-field">
        <label htmlFor="national-from">日期起日</label>
        <input id="national-from" type="date" max={draft.to || undefined} {...field("from")} />
      </div>
      <div className="pq-field">
        <label htmlFor="national-to">日期迄日</label>
        <input id="national-to" type="date" min={draft.from || undefined} {...field("to")} />
      </div>
      <div className="national-actions">
        <button className="pq-btn-primary" type="submit" disabled={!GATEWAY || state === "loading"}>搜尋</button>
        <button className="pq-btn-secondary" type="button" onClick={clear}>清除</button>
      </div>
    </form>

    <div className="national-status" role="status" aria-live="polite" aria-atomic="true">
      <p>{status}</p>
      {changed && <p>條件已修改，請重新搜尋；「載入更多」仍沿用上次搜尋條件。</p>}
      {state === "error" && results && <p>目前保留先前結果，可按「載入更多」重試。</p>}
    </div>
    <section aria-labelledby="national-results-title" aria-busy={state === "loading"}>
      <h2 id="national-results-title">搜尋結果</h2>
      <ul className="national-results">
        {(results?.records || []).map(row => <li key={row.id}>
          <article>
            <p className="national-kind">{Object.hasOwn(KIND_LABELS, row.kind) ? KIND_LABELS[row.kind] : row.kind || "類型未提供"}</p>
            <h3>{row.title || "標題未提供"}</h3>
            <dl>
              {[["縣市／機關範圍", row.jurisdiction], ["機關", row.authority], ["案號", row.case_number], ["日期", row.event_date], ["授權", row.license]].map(([label, value]) =>
                <div key={label}><dt>{label}</dt><dd>{value || "未提供"}</dd></div>)}
            </dl>
            {row.official_url ? <a className="national-official" href={row.official_url} target="_blank" rel="noreferrer">開啟官方原頁（另開視窗）</a> : <p>官方連結無法驗證</p>}
          </article>
        </li>)}
      </ul>
      {results?.next_cursor && <button className="pq-btn-secondary" type="button" disabled={state === "loading"} onClick={() => search(results.next_cursor)}>載入更多</button>}
    </section>
  </main>;
}
