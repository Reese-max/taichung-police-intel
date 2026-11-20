"use client";

import { useEffect, useState } from "react";
import {
  claimLabel, highlightSegments, spansForSide, validateChangeSummaryDemo,
} from "../../lib/change-summary-view.js";
import "./change-summary.css";

const DATA_URL = `${process.env.NEXT_PUBLIC_BASE_PATH || ""}/data/change-summary-demo.json`;

function Side({ title, text, spans }) {
  return (
    <section className="cs-side" aria-label={title}>
      <h3>{title}</h3>
      <p>
        {highlightSegments(text, spans).map((segment, index) => (
          segment.quoted
            ? <mark key={index}>{segment.text}</mark>
            : <span key={index}>{segment.text}</span>
        ))}
      </p>
    </section>
  );
}

export default function ChangeSummaryPage() {
  const [demo, setDemo] = useState(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetch(DATA_URL, { cache: "no-store", signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("示範資料暫時無法取得。");
        return response.json();
      })
      .then((data) => {
        if (!validateChangeSummaryDemo(data)) throw new Error("示範資料未通過原文核對，不顯示摘要。");
        setDemo(data);
      })
      .catch((reason) => { if (reason?.name !== "AbortError") setError(reason.message); });
    return () => controller.abort();
  }, []);

  if (error) return <main className="cs-page"><p role="alert">{error}</p></main>;
  if (!demo) return <main className="cs-page"><p>載入示範資料中…</p></main>;
  const { summary } = demo;
  const disclosure = summary.ai_disclosure;
  return (
    <main className="cs-page">
      <h1>變更摘要（AI 候選，待人工覆核）</h1>
      <p className="cs-badge" role="note">
        FIXTURE_ONLY：合成示範公告，不是官方紀錄。以下摘要由 {disclosure.provider}
        {disclosure.model ? `（${disclosure.model}）` : ""} 產生為「僅候選草稿」，尚未經人工核對，不得視為已發布。
      </p>
      <ol className="cs-statements">
        {summary.statements.map((statement, index) => (
          <li key={index}>
            <span className="cs-type">{claimLabel(statement.claim_type)}</span>
            <strong>{statement.text}</strong>
            <ul>
              {statement.evidence.map((span, spanIndex) => (
                <li key={spanIndex}>
                  {span.side === "before" ? "舊版原文" : "新版原文"}第 {span.start + 1}–{span.end} 字：
                  <q>{span.quote}</q>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
      <div className="cs-compare">
        <Side title="舊版原文" text={demo.before_text} spans={spansForSide(summary.statements, "before")} />
        <Side title="新版原文" text={demo.after_text} spans={spansForSide(summary.statements, "after")} />
      </div>
      <p className="cs-meta">
        來源：<a href={summary.official_url}>{summary.official_url}</a>（示範網域）· 版本 {summary.before_version} → {summary.after_version}
        · 摘要 SHA-256 {summary.summary_sha256.slice(0, 12)}… · 提示版本 {disclosure.prompt_version}
      </p>
      <p className="cs-meta">畫面已在瀏覽器中重新核對：每段引文都等於所顯示原文的對應位置。核對只證明引文存在，不證明摘要語意完全正確；請以官方原文為準。</p>
    </main>
  );
}
