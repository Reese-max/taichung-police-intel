"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { safeHttpsUrl } from "../../lib/public-query.js";
import {
  RESEARCH_QUESTION_LIMIT, requestResearch, researchDate, researchExplanation,
} from "../../lib/research-client.js";

const ENDPOINT = process.env.NEXT_PUBLIC_QUERY_GATEWAY_URL || "/query";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const EXAMPLES = ["查找交通管制相關的官方公告", "有哪些議會警政報告的公開索引？", "查找警政預算相關的公開資料"];
const DATA_LABELS = { SNAPSHOT_RECENT: "快照在時間門檻內", PARTIAL: "涵蓋不完整", STALE: "資料已過期", UNKNOWN: "資料狀態未知", SOURCE_NOT_AVAILABLE: "來源尚不可用" };
const GAP_LABELS = {
  INCOMPLETE_PUBLICATION: "發布資料不完整", STALE_SNAPSHOT: "快照已過期", SOURCE_INCOMPLETE: "來源蒐集不完整",
  STALE_SOURCE_DATA: "來源資料已過期", UNKNOWN_SOURCE_FRESHNESS: "來源新鮮度未知", UNKNOWN_CHECK_TIME: "來源核對時間未知",
  FUTURE_CHECK_TIME: "來源核對時間異常", STALE_SOURCE_CHECK: "來源核對已過期", UNKNOWN_PUBLICATION_TIME: "發布時間未知",
  FUTURE_PUBLICATION_TIME: "發布時間異常", INDEX_REBUILD_FAILED: "索引更新失敗", NOT_IN_APPROVED_SNAPSHOT: "不在核准快照內",
};
const ERROR_LABELS = {
  PUBLIC_DATA_CONFIRMATION_REQUIRED: "請先確認問題內容", INVALID_QUESTION: "問題格式不符", INVALID_HISTORY: "對話脈絡無法使用",
  CAPABILITY_NOT_AVAILABLE: "研究服務尚不可用", PENDING_UPDATE: "發布版本待更新", INVALID_RESPONSE: "回應未通過核對",
  TIMEOUT: "等待逾時", FAILED: "連線或查詢失敗", INPUT_RESTRICTED: "問題不符合公開研究限制",
  INVALID_REQUEST: "請求格式不符", RELEASE_MISMATCH: "發布版本不一致", QUERY_TEMPORARILY_UNAVAILABLE: "資料服務暫時無法使用",
  ADMISSION_DENIED: "本次請求未通過使用審查", RATE_LIMITED: "查詢次數已達上限", GATE_FAILED: "證據核對未通過",
};

function Sources({ sources, turnId }) {
  return <section className="research-sources" aria-labelledby={`sources-${turnId}`}>
    <h4 id={`sources-${turnId}`}>官方來源索引 <span>{sources.length} 筆</span></h4>
    <p className="research-small">原文連結會在新分頁開啟。標題只是索引；公告日期、資料截止與擷取時間各自列出。</p>
    {sources.length ? <ol>{sources.map(source => {
      const url = safeHttpsUrl(source.official_url);
      return <li key={source.evidence_id} className="research-source">
        <p className="research-source-title">{url ? <a href={url} target="_blank" rel="noopener noreferrer">{source.title}<span className="research-visually-hidden">（新分頁）</span><span aria-hidden="true"> ↗</span></a> : <span>{source.title}</span>}</p>
        {!url && <p className="research-small">未提供可驗證的安全原文連結</p>}
        <p className="research-source-id">來源：{source.source_id} · 證據：{source.evidence_id}</p>
        <dl className="research-dates">
          <div><dt>公告發布</dt><dd>{researchDate(source.published_at)}</dd></div>
          <div><dt>資料截止</dt><dd>{researchDate(source.data_as_of)}</dd></div>
          <div><dt>擷取時間</dt><dd>{researchDate(source.fetched_at)}</dd></div>
        </dl>
      </li>;
    })}</ol> : <p className="research-empty-sources">本次沒有可列出的索引。這不表示相關事件不存在。</p>}
  </section>;
}

function ResearchResult({ result, turnId }) {
  const ready = result.status === "ANSWER_READY";
  return <div className="research-result">
    <div className="research-result-heading"><h3>{ready ? "已核對索引摘要" : "僅提供來源索引"}</h3><span className={`research-badge ${ready ? "research-badge-ready" : ""}`}>{ready ? "ANSWER_READY" : "METADATA_ONLY"}</span></div>
    <p className="research-result-explanation">{researchExplanation(result)}</p>
    {ready && <div className="research-answer">{result.answer.map((line, index) => <p key={index}>{line}</p>)}</div>}
    <div className="research-coverage"><strong>{DATA_LABELS[result.data_status] || "請核對資料狀態"} · {result.data_status}</strong><p>{result.coverage_limitation}</p></div>
    {result.source_gaps.length > 0 && <details className="research-gaps" open><summary>資料缺口與限制（{result.source_gaps.length}）</summary><ul>{result.source_gaps.map((gap, index) => <li key={index}>
      <strong>{gap.source_id || "整體資料"}</strong>：{GAP_LABELS[gap.reason] || gap.reason}
      {gap.source_health && <span> · 來源健康：{gap.source_health}</span>}
      {gap.freshness_status && <span> · 新鮮度：{gap.freshness_status}</span>}
      {gap.status && <span> · 狀態：{gap.status}</span>}
      {gap.since && <span> · 起始時間：{researchDate(gap.since)}</span>}
    </li>)}</ul></details>}
    <Sources sources={result.sources} turnId={turnId} />
    <details className="research-receipt"><summary>檢視研究收據</summary><dl><div><dt>資料世代</dt><dd>{result.query_generation_id}</dd></div><div><dt>模型服務狀態</dt><dd>{result.provider.name} · {result.provider.state}</dd></div><div><dt>原因代碼</dt><dd>{result.reason_code || "未提供"}</dd></div>{result.release?.release_id && <div><dt>發布版本</dt><dd>{result.release.release_id}</dd></div>}</dl></details>
  </div>;
}

export default function ResearchChat({ publicationGeneration }) {
  const [draft, setDraft] = useState("");
  const [consent, setConsent] = useState(false);
  const [turns, setTurns] = useState([]);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const input = useRef(null);
  const active = useRef(null);
  const sequence = useRef(0);

  useEffect(() => () => {
    sequence.current += 1;
    active.current?.controller.abort();
    active.current = null;
  }, []);

  function stop() {
    const request = active.current;
    if (!request) return;
    sequence.current += 1;
    active.current = null;
    request.controller.abort();
    setTurns(previous => previous.map(turn => turn.id === request.id ? { ...turn, state: "cancelled" } : turn));
    setBusy(false);
    setAnnouncement("已停止等待。已送出的伺服器請求可能仍會繼續處理。");
  }

  function reset() {
    sequence.current += 1;
    active.current?.controller.abort();
    active.current = null;
    setTurns([]); setDraft(""); setConsent(false); setBusy(false); setFormError("");
    setAnnouncement("已清除本頁對話。下一個問題不會帶入先前脈絡。");
    input.current?.focus();
  }

  async function submit(event) {
    event.preventDefault();
    if (active.current) return;
    const question = draft.trim();
    if (!consent || !question || question.length > RESEARCH_QUESTION_LIMIT) {
      setFormError(!consent ? "請勾選公開資料確認後再送出。" : `請輸入 1 至 ${RESEARCH_QUESTION_LIMIT} 字的問題。`);
      return;
    }
    const id = ++sequence.current;
    const controller = new AbortController();
    active.current = { id, controller };
    // A cancelled or failed turn is never carried into the next request.
    const history = turns.filter(turn => turn.state === "complete").map(turn => ({ role: "user", content: turn.question }));
    setTurns(previous => [...previous, { id, question, state: "loading" }]);
    setBusy(true); setDraft(""); setConsent(false); setFormError("");
    setAnnouncement("正在查詢核准索引與核對來源限制。");
    try {
      const result = await requestResearch(ENDPOINT, { question, history, publicDataOnly: true }, {
        basePath: BASE_PATH, codeSha: process.env.NEXT_PUBLIC_RELEASE_CODE_SHA,
        publicationGeneration, signal: controller.signal,
      });
      if (id !== sequence.current) return;
      setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, state: "complete", result } : turn));
      setAnnouncement(`${result.status === "ANSWER_READY" ? "索引摘要已核對" : "本次僅提供來源索引"}，共 ${result.sources.length} 筆來源、${result.source_gaps.length} 項資料缺口。`);
    } catch (error) {
      if (id !== sequence.current) return;
      setTurns(previous => previous.map(turn => turn.id === id ? { ...turn, state: "error", error: { code: error.code || "FAILED", message: error.message } } : turn));
      setDraft(question);
      setAnnouncement("研究未完成，未顯示回答。問題已放回輸入框，可核對後重試。");
    } finally {
      if (id === sequence.current) { active.current = null; setBusy(false); }
    }
  }

  return <main className="research-page" aria-labelledby="research-title">
    <header className="research-header"><div><p className="research-eyebrow">GovIntel AI · 公開資料研究</p><h1 id="research-title">帶著問題，回到官方來源</h1><p>用對話查找核准的公開索引。每一輪都保留原文、資料時間與涵蓋限制；目前只提供索引識別與核對，不推論事件實況。</p></div><span className="research-header-tag">研究 MVP</span></header>
    <div className="research-layout"><div className="research-workspace">
      <section className="research-conversation" aria-labelledby="research-conversation-title">
        <div className="research-section-heading"><h2 id="research-conversation-title">這次研究</h2><button type="button" className="research-reset" onClick={reset} disabled={!turns.length && !draft && !consent}>開始新對話</button></div>
        {!turns.length ? <div className="research-empty"><span className="research-empty-icon" aria-hidden="true">↗</span><h3>你想查找哪類公開資料？</h3><p>先從一個明確的議題開始。以下範例只會填入問題，不會自動送出。</p><div className="research-examples">{EXAMPLES.map(example => <button key={example} type="button" onClick={() => { setDraft(example); setConsent(false); setFormError(""); input.current?.focus(); }}>{example}<span aria-hidden="true"> →</span></button>)}</div></div>
          : <ol className="research-turns">{turns.map((turn, index) => <li key={turn.id} className="research-turn"><div className="research-question"><span>你的問題 · {index + 1}</span><p>{turn.question}</p></div>
            {turn.state === "loading" && <div className="research-pending" aria-busy="true"><span className="research-pending-dot" aria-hidden="true" />正在核對來源、權利與可用服務…</div>}
            {turn.state === "cancelled" && <p className="research-cancelled">已停止等待，未顯示本次回答。已送出的伺服器請求可能繼續處理；這一輪不會加入後續問題脈絡。</p>}
            {turn.state === "error" && <div className="research-error" role="alert"><h3>{ERROR_LABELS[turn.error.code] || "研究未完成"}</h3><p>{turn.error.message}</p><p>查詢失敗不能當作零結果。可重試，或前往<Link href="/public-query/">公開查詢</Link>核對資料。</p><small>原因代碼：{turn.error.code}</small></div>}
            {turn.result && <ResearchResult result={turn.result} turnId={turn.id} />}
          </li>)}</ol>}
      </section>
      <form className="research-composer" onSubmit={submit} aria-busy={busy}>
        <div className="research-composer-heading"><label htmlFor="research-question">{turns.length ? "繼續追問" : "輸入研究問題"}</label><span id="research-count">{draft.length} / {RESEARCH_QUESTION_LIMIT}</span></div>
        <textarea id="research-question" ref={input} value={draft} onChange={event => { setDraft(event.target.value); setConsent(false); setFormError(""); }} maxLength={RESEARCH_QUESTION_LIMIT} rows={3} disabled={busy} placeholder="例如：查找交通管制相關的官方公告" aria-describedby="research-input-note research-count" />
        <p className="research-small" id="research-input-note">問題與最近 4 個已完成的使用者問題會傳到 GovIntel Gateway；模型啟用且通過權利與用量審查時，才會送至 MiniMax。請勿輸入個資、內部勤務或未公開資料。</p>
        <label className="research-consent"><input type="checkbox" checked={consent} onChange={event => { setConsent(event.target.checked); setFormError(""); }} disabled={busy} /><span>僅輸入公開且不含個資的問題</span></label>
        {formError && <p className="research-form-error" role="alert">{formError}</p>}
        <div className="research-composer-actions"><p>對話僅保留在此頁，離開或重整即清除</p>{busy ? <button type="button" className="research-secondary" onClick={stop}>停止等待</button> : <button type="submit" className="research-primary" disabled={!consent || !draft.trim()}>送出研究問題 <span aria-hidden="true">↑</span></button>}</div>
      </form>
      <p className="research-announcement" role="status" aria-live="polite" aria-atomic="true">{announcement}</p>
    </div><aside className="research-sidebar" aria-labelledby="research-boundaries-title"><p className="research-eyebrow">先看研究範圍</p><h2 id="research-boundaries-title">知道依據，也知道缺口</h2>
      <ol className="research-boundaries"><li><span>01</span><div><h3>核准公開索引</h3><p>只查已發布來源的標題與中介資料。目前不是全文搜尋，也不保證涵蓋所有事件。</p></div></li><li><span>02</span><div><h3>可核對才呈現</h3><p>模型未啟用、權利未核准或輸出未通過核對時，只顯示可用索引與原因。</p></div></li><li><span>03</span><div><h3>回到官方原文</h3><p>日期不明即標示未知。公告標題、口頭答覆或決議，不能證明後續執行已完成。</p></div></li></ol>
      <div className="research-sidebar-note"><strong>不作即時判斷</strong><p>這裡的資料不能當作即時路況、道路安全保證或勤務指令。零結果也不代表現實沒有事件。</p></div>
      <p className="research-small">所有完整時間以臺北時間（UTC+08:00）顯示；僅有日期的資料保留原始日期。</p><div className="research-sidebar-links"><Link href="/sources/">查看來源狀態 ↗</Link><Link href="/public-query/">使用公開查詢 ↗</Link></div>
    </aside></div>
  </main>;
}
