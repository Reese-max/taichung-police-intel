"use client";

import { useState } from "react";

const ENDPOINT = process.env.NEXT_PUBLIC_QUERY_GATEWAY_URL || "/query";

function QueryResult({ response }) {
  const results = Array.isArray(response.results) ? response.results : [];
  const sources = Array.isArray(response.sources) ? response.sources : [];
  const brief = response.brief;
  const coverage = response.query_coverage;
  return (
    <div className="v2-query-result" role="status">
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
      {sources.length > 0 && (
        <ul className="v2-query-source-list">
          {sources.map((source) => (
            <li key={source.source_id}>
              <strong>{source.source_id}</strong> {source.source_name} · {source.source_health} · {source.freshness_status}
            </li>
          ))}
        </ul>
      )}
      {results.length > 0 ? (
        <ul className="v2-query-result-list">
          {results.map((item) => (
            <li key={item.canonical_id}>
              <a href={item.official_url} target="_blank" rel="noreferrer">{item.title}</a>
              <small>{item.source_id} · {item.freshness_status || "UNKNOWN"}</small>
            </li>
          ))}
        </ul>
      ) : response.result_type === "publication_metadata" ? (
        <p className="v2-query-empty">
          {response.answerable_no_match ? "核准快照內沒有符合條件的資料。" : "目前不能把零結果解讀成沒有事件，請先處理上方資料缺口。"}
        </p>
      ) : null}
      <small className="v2-query-receipt">publication hash: {response.publication_hash}</small>
      <small className="v2-query-receipt" data-testid="query-generation">query generation: {response.query_generation_id}</small>
      {response.retention && (
        <small className="v2-query-receipt">公開投影：{response.retention.public_projection} · 權利狀態需審查</small>
      )}
    </div>
  );
}

export default function QueryGatewayPanel() {
  const [query, setQuery] = useState("");
  const [response, setResponse] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");

  async function run(tool, argumentsValue) {
    setState("loading");
    setError("");
    try {
      const result = await fetch(ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool, arguments: argumentsValue }),
      });
      const payload = await result.json();
      if (!result.ok || payload.error) throw new Error(payload.error?.message || `Gateway HTTP ${result.status}`);
      setResponse(payload);
      setState("ready");
    } catch (reason) {
      setResponse(null);
      setError("查詢服務目前無法連線；Dashboard 仍可使用。" + (reason?.message ? `（${reason.message}）` : ""));
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
          <p className="v2-eyebrow">唯讀查詢 · publication metadata</p>
          <h2 id="v2-query-title">Ask GovIntel</h2>
        </div>
        <span>Web / MCP 共用 Gateway</span>
      </div>
      <p className="v2-query-note">
        只查核准的公開快照與來源健康狀態；目前不是全文搜尋，也不會把結果當成即時事件或勤務指令。
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
      <div className="v2-query-actions" aria-label="GovIntel 快速查詢">
        <button type="button" onClick={() => run("get_current_brief", {})} disabled={state === "loading"}>目前 Brief</button>
        <button type="button" onClick={() => run("get_source_health", {})} disabled={state === "loading"}>來源健康</button>
      </div>
      {state === "loading" && <p className="v2-query-message" role="status">正在核對公開快照……</p>}
      {state === "error" && <p className="v2-query-message error" role="alert">{error}</p>}
      {state === "ready" && response && <QueryResult response={response} />}
    </section>
  );
}
