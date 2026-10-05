"use client";

import { useEffect, useRef, useState } from "react";
import {
  CHAT_QUICK_ACTIONS, CHAT_STATUS_LABELS, clearChatContext, loadChatContext,
  saveChatContext, summarizeChat,
} from "../../lib/chat-answer.js";
import { queryGateway } from "../../lib/query-release-client.js";
import { safeHttpsUrl } from "../../lib/public-query.js";
import "./ask.css";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const GATEWAY = process.env.NEXT_PUBLIC_QUERY_GATEWAY_URL || "";

const TIER_LABELS = { VERIFIED: "已驗證", DISCOVERY_UNVERIFIED: "待確認", CONFLICT: "來源衝突", STALE: "可能過期" };
const UNAVAILABLE_CODES = new Set(["CAPABILITY_NOT_AVAILABLE", "FAILED", "TIMEOUT", "UPSTREAM_UNAVAILABLE", "PENDING_UPDATE"]);

function OfficialLink({ url, children = "開啟官方原文" }) {
  const safe = safeHttpsUrl(url);
  return safe ? <a href={safe} target="_blank" rel="noreferrer">{children}</a> : <span>沒有可驗證的官方連結</span>;
}

function EventCard({ item }) {
  return <li className="ask-event-card">
    <div className="ask-event-head"><span className={`ask-tier ask-tier-${item.trust_tier || "UNKNOWN"}`}>{TIER_LABELS[item.trust_tier] || "未知"}</span><code>{item.public_event_id}</code></div>
    <strong>{item.canonical_title}</strong>
    <dl><div><dt>類型</dt><dd>{item.event_type || "未提供"}</dd></div><div><dt>地區</dt><dd>{item.district_id || "未提供"}</dd></div><div><dt>狀態</dt><dd>{item.event_status || "未知"}</dd></div></dl>
  </li>;
}

function AnswerView({ message }) {
  const summary = summarizeChat(message.chat);
  return <div className="ask-answer" role="group" aria-label="回答">
    <div className="ask-answer-meta">
      <span className="ask-status">{summary.statusLabel}</span>
      {summary.freshness && <span className="ask-freshness">時效：{summary.freshness}</span>}
    </div>
    {summary.resolvedText && <p className="ask-resolved">實際查詢：<code>{summary.resolvedText}</code></p>}
    {summary.lines.map((line, index) => <p key={index} className="ask-line">{line}</p>)}
    {summary.sections.map((section) => <section key={section.kind} className={`ask-section ask-${section.kind}`}>
      <h3>{section.label}（{section.items.length}）</h3>
      <ul>{section.items.map((item) => <EventCard key={item.public_event_id} item={item} />)}</ul>
    </section>)}
    {summary.statistics.length > 0 && <section className="ask-section"><h3>統計（{summary.statistics.length}）</h3>
      <ul>{summary.statistics.map((row) => <li key={row.statistic_id}>{row.geography} {row.metric} {row.period}：{row.value}{row.unit}（{row.provisional ? "暫計" : "定案"}）<OfficialLink url={row.official_url} /></li>)}</ul>
    </section>}
    {summary.comparison && <section className="ask-section"><h3>版本比較</h3>
      <p>{summary.comparison.comparison_status === "COMPARED" ? `變更欄位：${(summary.comparison.changed_fields || []).join("、") || "無"}（${summary.comparison.materiality}）` : "沒有可比較的版本"}</p>
    </section>}
    {summary.sources.length > 0 && <section className="ask-section"><h3>來源狀態（{summary.sources.length}）</h3>
      <ul>{summary.sources.map((source) => <li key={source.source_id}>{source.source_id}：{source.source_health || "未知"}／{source.window_completeness || "未知"}／{source.freshness_status || "未知"}</li>)}</ul>
    </section>}
    {summary.evidenceLinks.length > 0 && <section className="ask-section"><h3>官方證據（{summary.evidenceLinks.length}）</h3>
      <ul>{summary.evidenceLinks.map((link, index) => <li key={index}><OfficialLink url={link.official_url}>{link.title || "官方原文"}</OfficialLink><small>{link.source_id} · {link.evidence_id || "無證據 ID"} · {link.document_version_id || "無版本"}</small></li>)}</ul>
    </section>}
    {summary.gaps.length > 0 && <div className="ask-gaps" role="alert"><strong>涵蓋限制與缺口：</strong><ul>{summary.gaps.map((gap, index) => <li key={index}>{gap.source_id || "整體"} · {gap.reason}</li>)}</ul></div>}
    {summary.notices.length > 0 && <ul className="ask-notices">{summary.notices.map((notice, index) => <li key={index}>{notice.code}：{notice.message}</li>)}</ul>}
    {summary.quickActionHints.length > 0 && <p className="ask-hint">可以改用下方快速查詢，或描述地區、時間與類型。</p>}
    <details className="ask-receipt"><summary>查詢收據</summary>
      <p>發布 hash：{summary.publicationHash || message.publication_hash || "未提供"}</p>
      <p>查詢 ID：{message.query_id || "未提供"}</p>
      <p>參數 hash：{summary.queryReceipt?.arguments_sha256 || "未提供"}</p>
      <p>產生時間：{message.generated_at || "未提供"}</p>
    </details>
  </div>;
}

export default function AskPage() {
  const [context, setContext] = useState({ schema_version: 1 });
  const [messages, setMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const [publicationGeneration, setPublicationGeneration] = useState(null);
  const sequence = useRef(0);

  useEffect(() => {
    setContext(loadChatContext(globalThis.sessionStorage));
    fetch(`${BASE_PATH}/data/source-status.json`, { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => setPublicationGeneration(data?.latest_collection_run?.collection_run_id || null))
      .catch(() => setPublicationGeneration(null));
    return () => { sequence.current += 1; };
  }, []);

  function persistContext(next) {
    setContext(next);
    saveChatContext(globalThis.sessionStorage, next);
  }

  async function send(text, quickAction = null) {
    const content = (text || "").trim();
    if (!content && !quickAction) return;
    const request = ++sequence.current;
    setState("loading"); setError(null);
    if (content) setMessages((list) => [...list.slice(-19), { role: "user", text: content }]);
    try {
      if (!GATEWAY) throw Object.assign(new Error("未設定正式查詢服務"), { code: "CAPABILITY_NOT_AVAILABLE" });
      const data = await queryGateway(GATEWAY, "chat_turn", {
        ...(content ? { text: content } : {}),
        ...(quickAction ? { quick_action: quickAction } : {}),
        context,
      }, {
        basePath: BASE_PATH, codeSha: process.env.NEXT_PUBLIC_RELEASE_CODE_SHA,
        publicationGeneration,
      });
      if (request !== sequence.current) return;
      const nextContext = summarizeChat(data.chat).context;
      persistContext(nextContext);
      setMessages((list) => [...list.slice(-19), { role: "assistant", chat: data.chat, query_id: data.query_id, publication_hash: data.publication_hash, generated_at: data.generated_at }]);
      setState("ready");
    } catch (reason) {
      if (request !== sequence.current) return;
      setError({ code: reason.code || "FAILED", message: reason.message });
      setState("error");
    }
  }

  const unavailable = error && UNAVAILABLE_CODES.has(error.code);
  const contextChips = [
    context.selected_event_id && `事件 ${context.selected_event_id}`,
    context.selected_region && `地區 ${context.selected_region}`,
    context.selected_time_window && `期間 ${context.selected_time_window.time_from}–${context.selected_time_window.time_to}`,
    context.selected_category && `類型 ${context.selected_category}`,
    context.selected_agency && `機關 ${context.selected_agency}`,
  ].filter(Boolean);

  return <main className="ask-page" aria-labelledby="ask-title">
    <header className="ask-header">
      <p className="ask-eyebrow">GovIntel AI · Ask GovIntel</p>
      <h1 id="ask-title">對話式公開查詢</h1>
      <p className="ask-desc">問題會被解析成明確的唯讀查詢，與 MCP 共用同一個 Query Gateway；回答保留驗證狀態、時效、來源缺口與官方證據連結。對話內容不會寫入公開資料庫。</p>
      <a href={`${BASE_PATH}/public-query/`}>改用條件式公開查詢</a>
    </header>

    <div className="ask-quick" role="group" aria-label="快速查詢">
      {CHAT_QUICK_ACTIONS.map((action) => (
        <button key={action.id} type="button" disabled={state === "loading"}
          onClick={() => send(action.text)}>{action.label}</button>
      ))}
    </div>

    {unavailable && <div className="ask-unavailable" role="alert">
      <strong>對話查詢目前無法使用{error.code ? `（${error.code}）` : ""}。</strong>
      <p>{error.message}</p>
      <p>已發布的靜態資料不受影響：<a href={`${BASE_PATH}/public-query/`}>公開查詢</a>、<a href={`${BASE_PATH}/`}>首頁概覽</a>、<a href={`${BASE_PATH}/sources/`}>來源狀態</a> 仍可閱讀。</p>
    </div>}
    {error && !unavailable && <div className="ask-error" role="alert"><strong>{error.code} · 查詢未完成</strong><p>{error.message}</p><p>查詢失敗不能解讀成沒有事件。</p></div>}

    {contextChips.length > 0 && <div className="ask-context" role="status">
      <span>目前條件：</span>{contextChips.map((chip) => <span key={chip} className="ask-chip">{chip}</span>)}
      <button type="button" className="ask-reset" onClick={() => { persistContext({ schema_version: 1 }); clearChatContext(globalThis.sessionStorage); }}>清除條件</button>
    </div>}

    <ol className="ask-thread" aria-live="polite">
      {messages.map((message, index) => <li key={index} className={`ask-msg ask-${message.role}`}>
        {message.role === "user" ? <p>{message.text}</p> : <AnswerView message={message} />}
      </li>)}
      {state === "loading" && <li className="ask-msg ask-assistant"><p role="status">正在核對資料……</p></li>}
    </ol>
    {!messages.length && state === "idle" && <p className="ask-hint">輸入問題或選擇快速查詢。可追問：只看某地區、第幾件詳情、跟昨天比、把官方證據給我。</p>}

    <form className="ask-form" onSubmit={(event) => { event.preventDefault(); send(draft); setDraft(""); }}>
      <label htmlFor="ask-input" className="ask-sr">輸入問題</label>
      <input id="ask-input" type="text" value={draft} maxLength={512} disabled={state === "loading"}
        onChange={(event) => setDraft(event.target.value)} placeholder="例如：這週末臺中有哪些活動？" autoComplete="off" />
      <button type="submit" disabled={state === "loading" || !draft.trim()}>送出</button>
    </form>
  </main>;
}
