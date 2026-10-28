"use client";

import { useEffect, useRef, useState } from "react";
import { CHAT_ASSIST_NOT_SENT, CHAT_ASSIST_REASON_LABELS, CHAT_QUICK_ACTIONS, requestChat, summarizeChat } from "../lib/chat-answer.js";
import { safeHttpsUrl } from "../lib/public-query.js";

const TIERS = { verified: "VERIFIED", unverified: "UNVERIFIED", conflicts: "CONFLICT", stale: "STALE" };

export default function QueryChat({ endpoint, releaseContext }) {
  const [text, setText] = useState("");
  const [context, setContext] = useState({ schema_version: 1 });
  const [response, setResponse] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [assist, setAssist] = useState(false);
  const [publicOnly, setPublicOnly] = useState(false);
  const active = useRef(null);
  useEffect(() => () => { active.current = null; }, []);

  async function run(input) {
    if (active.current) return;
    const request = {};
    active.current = request;
    setBusy(true); setError(null); setResponse(null);
    try {
      const payload = await requestChat(endpoint, { ...input, context }, releaseContext);
      if (active.current !== request) return;
      setResponse(payload); setContext(payload.chat.context);
    } catch (reason) {
      if (active.current === request) setError({ code: reason.code || "FAILED", message: reason.message });
    } finally {
      if (active.current === request) { active.current = null; setBusy(false); }
    }
  }

  const summary = response ? summarizeChat(response.chat) : null;
  const gaps = summary?.gaps.length ? summary.gaps : response?.source_gaps || [];
  return <section className="pq-panel pq-chat" aria-labelledby="pq-chat-title">
    <h2 id="pq-chat-title">Ask GovIntel · 對話查詢</h2>
    <p>以自然語言查詢並縮小地區、時間或事件。能力依伺服器核准資料而定；對話條件只保留在本頁，重新整理即清除。</p>
    <div className="pq-actions" aria-label="對話快速查詢">{CHAT_QUICK_ACTIONS.map(action => <button key={action.id} type="button" disabled={busy} onClick={() => run({ quickAction: action.id })}>{action.label}</button>)}</div>
    <fieldset className="pq-assist" disabled={busy}>
      <legend>AI 解析問題（實驗）</legend>
      <label><input type="checkbox" checked={assist} onChange={event => setAssist(event.target.checked)} /> 由 AI 判斷要查哪一種資料；事實仍只來自下方的官方資料，回答須通過證據核對</label>
      {assist && <label><input type="checkbox" checked={publicOnly} onChange={event => setPublicOnly(event.target.checked)} /> 我確認問題只含公開、不含個人的資訊（問題會送到站方核准的 AI 服務）</label>}
    </fieldset>
    <form className="pq-search-row pq-keyword-row" onSubmit={event => { event.preventDefault(); run(assist ? { text, assist: true, publicDataOnly: publicOnly } : { text }); }}>
      <div className="pq-field"><label htmlFor="pq-chat-question">公開資料問題或追問</label><input id="pq-chat-question" value={text} maxLength={512} disabled={busy} onChange={event => setText(event.target.value)} placeholder="例如：這週末臺中有哪些活動？ → 只看西屯" /></div>
      <button type="submit" disabled={busy || !text.trim()}>送出問題</button>
      <button type="button" disabled={busy} onClick={() => { setContext({ schema_version: 1 }); setResponse(null); setError(null); setText(""); }}>清除查詢脈絡</button>
    </form>
    {busy && <p role="status">正在核對公開資料……</p>}
    {error && <div className="pq-error" role="alert"><strong>對話查詢暫不可用</strong><p>查詢失敗不能當作零結果；下方公開查詢與 Dashboard 仍可使用。</p><p>{error.code} · {error.message}</p></div>}
    {summary && <div aria-live="polite" aria-label="對話查詢結果">
      <p><strong>{summary.statusLabel} · {summary.status}</strong> · 新鮮度：{response.freshness}</p>
      <p>{response.verification_summary}</p>
      {summary.parser && <p>解析方式：{summary.parser.kind === "llm" ? `AI 解析（${summary.parser.version}）` :
        `${CHAT_ASSIST_NOT_SENT.has(summary.parser.fallback_reason) ? "規則（問題未送出給 AI" : "規則（AI 解析未成功，問題可能已送出，改用規則"}：${CHAT_ASSIST_REASON_LABELS[summary.parser.fallback_reason] || summary.parser.fallback_reason}）`}
        {summary.parser.time_window && ` · 時間條件：${summary.parser.time_window.label}（${summary.parser.time_window.time_from}～${summary.parser.time_window.time_to}，${summary.parser.time_window.time_zone}）`}</p>}
      {summary.assistedAnswer && (summary.assistedAnswer.status === "ANSWER_READY"
        ? <section aria-label="AI 挑選並通過證據核對的回答"><h3>AI 挑選並通過證據核對的回答</h3><ul>{summary.assistedAnswer.answer.map((line, index) => <li key={index}>{line}</li>)}</ul>
          <p>核對器：{summary.assistedAnswer.answer_evidence_receipt.validator_version} · 只代表公告標題的索引記載，不證明標題中的事件事實。</p></section>
        : <p>AI 回答：未產生（{CHAT_ASSIST_REASON_LABELS[summary.assistedAnswer.reason_code] || summary.assistedAnswer.reason_code}）</p>)}
      {summary.lines.map((line, index) => <p key={index}>{line}</p>)}
      {gaps.length > 0 && <div className="pq-gaps"><strong>資料缺口：</strong><ul>{gaps.map((gap, index) => <li key={index}>{gap.source_id || "整體"} · {gap.status || gap.source_health} · {gap.reason}</li>)}</ul></div>}
      {summary.notices.map((notice, index) => <p key={index}>{typeof notice === "string" ? notice : `${notice.code} · ${notice.message}`}</p>)}
      {summary.sections.map(section => <section key={section.kind} aria-label={TIERS[section.kind]}><h3>{section.label} · {TIERS[section.kind]}</h3><ul>{section.items.map((item, index) => <li key={item.public_event_id || index}>
        {typeof item === "string" ? item : <><strong>{item.result_number ? `第 ${item.result_number} 件 · ` : ""}</strong>{item.canonical_title} · {item.public_event_id} · {item.event_status}<button type="button" disabled={busy} onClick={() => run({ text: `${item.public_event_id} 詳情` })}>查看{item.result_number ? `第 ${item.result_number} 件` : "事件"}詳情</button></>}
      </li>)}</ul></section>)}
      {summary.statistics.length > 0 && <div className="pq-chat-table"><table><caption>官方統計 · 不產生治安排名</caption><thead><tr>{["地區", "指標", "期間", "數值／單位", "狀態", "資料集／來源", "更新時間"].map(label => <th scope="col" key={label}>{label}</th>)}</tr></thead><tbody>{summary.statistics.map(row => <tr key={row.statistic_id}><td>{row.geography}</td><td>{row.metric}</td><td>{row.period}</td><td>{row.value} {row.unit}</td><td>{row.provisional ? "暫計" : "定案"}</td><td>{row.dataset_id} · {row.source_id}</td><td>{row.updated_at}</td></tr>)}</tbody></table></div>}
      {summary.sources.length > 0 && <ul>{summary.sources.map(source => <li key={source.source_id}>{source.source_id} · {source.source_health} · {source.freshness_status} · {source.window_completeness}</li>)}</ul>}
      {summary.evidenceLinks.length > 0 && <section aria-label="官方證據"><h3>官方原文與精確定位</h3><ul>{summary.evidenceLinks.map((link, index) => {
        const url = safeHttpsUrl(link.official_url);
        return <li key={index}>{url ? <a href={url} target="_blank" rel="noreferrer">{link.title || link.evidence_id || "官方原文"}</a> : "未提供安全的官方原文連結"}<p>{link.source_id} · {link.evidence_id} · {link.document_version_id}</p><p>{link.evidence_locator}</p></li>;
      })}</ul></section>}
      {response.truncated && <p role="status">結果已截斷，本頁不代表全部；請縮小條件，或使用下方公開查詢分頁。</p>}
      <details className="pq-receipt"><summary>實際查詢條件與證據收據</summary><p>{summary.resolvedText}</p><p>查詢 ID：{response.query_id}</p><p>發布 hash：{response.publication_hash}</p><p>資料世代：{response.query_generation_id}</p><pre>{JSON.stringify(summary.queryReceipt || response.receipt, null, 2)}</pre></details>
    </div>}
  </section>;
}
