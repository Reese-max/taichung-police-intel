"use client";

import { useState } from "react";
import { FEEDBACK_REASONS } from "../lib/local-review.js";
import { queryGateway } from "../lib/query-release-client.js";
import { validateControlledAnswer } from "../lib/controlled-answer-client.js";
import { indexQueryPresentation } from "../lib/query-presentation.js";

const ENDPOINT = process.env.NEXT_PUBLIC_QUERY_GATEWAY_URL || "/query";
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const FEEDBACK_REASON_LABELS = {
  FALSE_MERGE: "誤合併",
  MISSED_MERGE: "漏合併",
  WRONG_ENTITY: "實體錯誤",
  NOT_RELEVANT: "不相關",
  MISSING_EVENT: "漏事件",
  WRONG_CHANGE_CLASSIFICATION: "異動分類錯誤",
  UNSUPPORTED_ANSWER: "回答缺少支持",
  WRONG_STATISTIC_SCOPE: "統計範圍錯誤",
  BAD_SOURCE_MAPPING: "來源 mapping 錯誤",
};

async function sha256Json(value) {
  if (!globalThis.crypto?.subtle) throw new Error("瀏覽器不提供輸出 hash，未建立 feedback 草稿");
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}


async function gatewayFeedbackItem(response, targetType) {
  const queryId = response?.query_id;
  const version = response?.query_generation_id || response?.publication_id;
  if (!queryId || !version) throw new Error("查詢收據缺少可驗證的版本 binding");
  const outputHash = await sha256Json(response);
  const answerHash = response.answer_evidence_receipt?.answer_sha256;
  return {
    review_id: `${targetType}-${queryId}`,
    reason: "WRONG_CHANGE_CLASSIFICATION",
    status: "OPEN",
    source_version: String(version),
    evidence_sha256: outputHash,
    original_output_sha256: outputHash,
    feedback_target: {
      type: targetType,
      id: targetType === "ANSWER" && answerHash ? answerHash : String(queryId),
      version: String(version),
    },
    entity_ids: {
      query_id: String(queryId),
      publication_id: String(response.publication_id || ""),
      publication_hash: String(response.publication_hash || ""),
      output_sha256: outputHash,
      ...(answerHash ? { answer_sha256: String(answerHash) } : {}),
    },
  };
}

function GatewayFeedbackForm({ response, targetType, onFeedback, disabled }) {
  const [reason, setReason] = useState(targetType === "ANSWER" ? "UNSUPPORTED_ANSWER" : "NOT_RELEVANT");
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");

  async function submit(event) {
    event.preventDefault();
    setState("saving");
    setError("");
    try {
      const saved = await onFeedback(await gatewayFeedbackItem(response, targetType), reason);
      if (saved === false) throw new Error("本機覆核尚未載入，未建立 feedback 草稿");
      setState("saved");
    } catch (caught) {
      setState("error");
      setError(caught?.message || "未建立 feedback 草稿");
    }
  }

  return (
    <form className="v2-review-feedback v2-query-feedback" onSubmit={submit}>
      <label>
        <span>{targetType === "ANSWER" ? "回報回答" : "回報查詢"}</span>
        <select value={reason} onChange={(event) => setReason(event.target.value)} disabled={disabled || state === "saving"}>
          {FEEDBACK_REASONS.map((value) => <option key={value} value={value}>{FEEDBACK_REASON_LABELS[value] || value}</option>)}
        </select>
      </label>
      <button type="submit" disabled={disabled || state === "saving"}>建立本機 feedback</button>
      {state === "saved" && <small role="status">已建立本機 feedback 草稿。</small>}
      {error && <small className="error" role="alert">{error}</small>}
    </form>
  );
}

function QueryResult({ response, onFeedback, feedbackReady, releaseContext }) {
  const results = Array.isArray(response.results) ? response.results : [];
  const sources = Array.isArray(response.sources) ? response.sources : [];
  const brief = response.brief;
  const publicationReceipt = response.publication_receipt;
  const coverage = response.query_coverage;
  const presentation = response.tool_name === "search_evidence" ? indexQueryPresentation(response) : null;
  const [answerState, setAnswerState] = useState("idle");
  const [answerResponse, setAnswerResponse] = useState(null);
  const [answerError, setAnswerError] = useState("");

  async function validateResults() {
    if (!response.results?.length) return;
    setAnswerState("loading");
    setAnswerError("");
    try {
      const payload = await validateControlledAnswer(ENDPOINT, response, releaseContext);
      setAnswerResponse(payload);
      setAnswerState("ready");
    } catch (reason) {
      setAnswerResponse(null);
      setAnswerError(reason?.message || "受控回答核對失敗");
      setAnswerState("error");
    }
  }

  return (
    <div className="v2-query-result" role="status">
      {presentation && <div className="v2-query-empty" data-testid={presentation.kind === "unavailable" ? "index-unavailable" : "index-query-state"}>
        {presentation.kind === "unavailable" && <strong>公開索引暫不可用</strong>}
        {presentation.message && <p>{presentation.message}</p>}
        {presentation.kind === "unavailable" && <p>這不是零筆資料。<a href={`${BASE_PATH}/#v2-archive-title`}>閱讀既有歷史資料與原文</a>。</p>}
      </div>}
      <details className="v2-query-receipt"><summary>查看查詢與來源技術收據</summary>
      <small>唯讀查詢 · publication metadata · Web / MCP 共用 Gateway</small>
      <div className="v2-query-result-heading">
        <strong>{response.freshness}</strong>
        <span>{response.result_type}</span>
      </div>
      <p>{response.verification_summary}</p>
      {coverage && (
        <p className="v2-query-coverage">
          覆蓋狀態：{coverage.status} · 已核對 {coverage.covered_sources?.length || 0}/{coverage.required_sources?.length || 0} 個必要來源
          {coverage.missing_required_sources?.length ? ` · 缺口：${coverage.missing_required_sources.join(", ")}` : ""}
          {coverage.stale_required_sources?.length ? ` · 過期：${coverage.stale_required_sources.join(", ")}` : ""}
        </p>
      )}
      {brief && (
        <p>
          Brief 狀態：{brief.publication_status || "未提供"} · 快照變更 {brief.overview?.current_change_count || 0} 件
        </p>
      )}
      {publicationReceipt && (
        <p data-testid="publication-receipt">
          發布收據：{publicationReceipt.publication_id} · {publicationReceipt.collection_status || "UNKNOWN"} / {publicationReceipt.publication_status || "UNKNOWN"} · {publicationReceipt.current_as_of_server_clock ? "目前可用" : "需注意新鮮度"}
        </p>
      )}
      {sources.length > 0 && (
        <ul className="v2-query-source-list">
          {sources.map((source) => (
            <li key={source.source_id}>
              <strong>{source.source_id}</strong> {source.source_name} · {source.source_health} · {source.freshness_status}
            </li>
          ))}
        </ul>
      )}
      <small className="v2-query-receipt">publication hash: {response.publication_hash}</small>
      <small className="v2-query-receipt" data-testid="query-generation">query generation: {response.query_generation_id}</small>
      {response.retention && <small className="v2-query-receipt">公開投影：{response.retention.public_projection} · 權利狀態需審查</small>}
      </details>
      {results.length > 0 ? (
        <ul className="v2-query-result-list">
          {results.map((item) => (
            <li key={item.canonical_id}>
              <a href={item.official_url} target="_blank" rel="noreferrer">{item.title}</a>
              <small>{item.source_id} · {item.verification_status || item.freshness_status || "UNKNOWN"}</small>
            </li>
          ))}
        </ul>
      ) : null}
      {results.length > 0 && response.result_type === "publication_metadata" && (
        <section className="v2-query-answer-gate" aria-label="受控回答核對">
          <p>索引已列出。摘要需另行逐句核對；核對失敗時保留上方索引，未核對的回答不顯示。</p>
          <button type="button" onClick={validateResults} disabled={answerState === "loading"}>
            {answerState === "loading" ? "正在核對回答……" : "核對索引摘要"}
          </button>
          {answerState === "error" && <p className="v2-query-message error" role="alert">{answerError}</p>}
          {answerResponse && answerState === "ready" && (
            <div data-testid="answer-evidence-result">
              <p>{answerResponse.gate_status === "PASS" ? "索引摘要已逐句核對。" : "索引摘要已核對；未核對或過期的內容不作為目前事實。"}</p>
              {answerResponse.answer?.map((text, index) => <p key={`${text}-${index}`}>{text}</p>)}
              <small className="v2-query-receipt">validator: {answerResponse.answer_evidence_receipt?.validator_version}</small>
              <small className="v2-query-receipt">answer evidence: {answerResponse.answer_evidence_receipt?.evidence_ids?.join(", ") || "none"}</small>
              {onFeedback && (
                <GatewayFeedbackForm
                  response={answerResponse}
                  targetType="ANSWER"
                  onFeedback={onFeedback}
                  disabled={!feedbackReady}
                />
              )}
            </div>
          )}
        </section>
      )}
      {onFeedback && (
        <div className="v2-query-feedback-list" aria-label="查詢 feedback">
          <GatewayFeedbackForm
            response={response}
            targetType="QUERY"
            onFeedback={onFeedback}
            disabled={!feedbackReady}
          />
          {response.result_type === "answer_evidence" && (
            <GatewayFeedbackForm
              response={response}
              targetType="ANSWER"
              onFeedback={onFeedback}
              disabled={!feedbackReady}
            />
          )}
        </div>
      )}
    </div>
  );
}

export default function QueryGatewayPanel({ onFeedback, feedbackReady = false, publicationGeneration }) {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");
  const releaseContext = process.env.NEXT_PUBLIC_QUERY_GATEWAY_URL ? {
    basePath: process.env.NEXT_PUBLIC_BASE_PATH || "",
    codeSha: process.env.NEXT_PUBLIC_RELEASE_CODE_SHA,
    publicationGeneration,
  } : null;

  async function run(tool, argumentsValue) {
    setState("loading");
    setError("");
    try {
      const payload = await queryGateway(ENDPOINT, tool, argumentsValue, releaseContext);
      if (tool === "search_evidence" && !Array.isArray(payload.results)) throw new Error("索引回應格式無法核對。");
      setResponse(payload);
      setState("ready");
    } catch (reason) {
      setResponse(null);
      setError(reason?.message || "查詢服務目前無法連線。");
      setState("error");
    }
  }

  function submit(event) {
    event.preventDefault();
    run("search_evidence", { q: query.trim(), limit: 8 });
  }

  return (
    <section className="v2-query" aria-labelledby="v2-query-title">
      <div className="v2-section-heading">
        <div>
          <p className="v2-eyebrow">公開索引關鍵字查詢</p>
          <h2 id="v2-query-title">Ask GovIntel</h2>
        </div>
        <span>標題與來源中介資料</span>
      </div>
      <p className="v2-query-note">
        只查核准的公開快照與來源健康狀態；目前不是全文搜尋，也不會把結果當成即時事件或勤務指令。
        <a href={`${BASE_PATH}/#v2-archive-title`}>閱讀既有歷史資料與原文</a>。
      </p>
      <form className="v2-query-form" onSubmit={submit}>
        <label>
          <span>搜尋官方資料索引</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="例如：議事、交通、警力"
            maxLength={512}
          />
        </label>
        <button type="submit" disabled={state === "loading"}>查詢</button>
      </form>
      <details className="v2-query-receipt"><summary>查看來源與發布狀態</summary><div className="v2-query-actions" aria-label="GovIntel 快速查詢">
        <button type="button" onClick={() => run("get_current_brief", {})} disabled={state === "loading"}>目前 Brief</button>
        <button type="button" onClick={() => run("get_publication_receipt", {})} disabled={state === "loading"}>發布收據</button>
        <button type="button" onClick={() => run("get_source_health", {})} disabled={state === "loading"}>來源健康</button>
      </div></details>
      {state === "loading" && <p className="v2-query-message" role="status">正在核對公開快照……</p>}
      {state === "error" && <div className="v2-query-message error" role="alert"><strong>索引查詢暫不可用</strong><p>這不是零筆資料；Dashboard 仍可使用。<a href={`${BASE_PATH}/#v2-archive-title`}>閱讀既有歷史資料與原文</a>。</p><details><summary>查看技術原因</summary><p>{error}</p></details></div>}
      {state === "ready" && response && (
        <QueryResult key={response.query_id} response={response} onFeedback={onFeedback} feedbackReady={feedbackReady} releaseContext={releaseContext} />
      )}
    </section>
  );
}
