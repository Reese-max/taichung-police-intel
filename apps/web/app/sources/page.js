"use client";

import { useEffect, useState } from "react";

const STATUS_URL = `${process.env.NEXT_PUBLIC_BASE_PATH || ""}/data/source-status.json`;
const HEALTH = { PASS: "成功取得", FAILED: "取得失敗", UNKNOWN: "尚無可核對結果" };
const WINDOW = { COMPLETE_ZERO: "本次已查範圍內沒有新增", COMPLETE_WITH_ITEMS: "本次已查範圍內有資料", PARTIAL: "本次涵蓋不完整" };

function dateLabel(value) {
  const date = new Date(value || "");
  return Number.isFinite(date.getTime())
    ? date.toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false })
    : "尚無紀錄";
}

function sourceLink(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && /(^|\.)(tccc|taichung)\.gov\.tw$/.test(url.hostname)
      ? url.href
      : null;
  } catch { return null; }
}

export default function SourcesPage() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState("");
  const [now, setNow] = useState(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(new DOMException("來源狀態載入逾時，請稍後重新整理。", "TimeoutError")), 10000);
    setNow(Date.now());
    fetch(STATUS_URL, { cache: "no-store", signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error("來源狀態暫時無法取得；請稍後重新整理。");
        return response.json();
      })
      .then(data => {
        if (!data || !Array.isArray(data.sources) || !data.sources.length ||
            data.sources.some(source => !source || typeof source.source_id !== "string" || !source.source_id) ||
            new Set(data.sources.map(source => source.source_id)).size !== data.sources.length) {
          throw new Error("來源狀態缺少可核對紀錄。");
        }
        setStatus(data);
      })
      .catch(reason => { if (reason.name !== "AbortError") setError(reason.message); })
      .finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, []);
  const generated = Date.parse(status?.generated_at || "");
  const snapshotOld = now !== null && Number.isFinite(generated) && now - generated > 16 * 60 * 60 * 1000;
  const sources = status?.sources || [];
  return (
    <main className="govintel-sources" data-testid="public-sources">
      <h1>來源狀態</h1>
      <p>核對已取得資料的時間與涵蓋範圍。來源失聯、沒有新公告與官方資料日期較舊，代表不同狀態。</p>
      {error ? <p role="alert" className="govintel-source-warning">{error}</p> : !status ? <p role="status">正在載入來源紀錄…</p> : (
        <>
          <p>快照產生：<time dateTime={status.generated_at}>{dateLabel(status.generated_at)}</time>（臺北時間）</p>
          <p className="govintel-source-warning">
            {snapshotOld ? "這份保存快照已超過 16 小時，請回查官方來源。" : "以下僅呈現已取得的來源快照。"}
            清單涵蓋有限；沒有新項目不能證明現況未變，也不能作為道路安全或管制解除的依據。
          </p>
          <div className="govintel-source-grid">
            {sources.map(source => {
              const officialUrl = sourceLink(source.source_url);
              return (
                <article key={source.source_id} className="govintel-source-card">
                  <h2>{source.source_name || source.source_id}</h2>
                  <dl>
                    <div><dt>本次取得</dt><dd>{HEALTH[source.source_health] || "狀態待核對"}</dd></div>
                    <div><dt>本次涵蓋</dt><dd>{WINDOW[source.window_completeness] || "涵蓋範圍未知"}</dd></div>
                    <div><dt>最後檢查</dt><dd>{dateLabel(source.last_checked_at)}</dd></div>
                    <div><dt>最後成功取得</dt><dd>{dateLabel(source.last_success_at)}</dd></div>
                    <div><dt>官方資料截至</dt><dd>{dateLabel(source.data_as_of)}</dd></div>
                  </dl>
                  {["STALE", "VERY_STALE"].includes(source.freshness_status) && <p>官方資料日期較舊，不能解讀為目前沒有事件。</p>}
                  {source.source_health !== "PASS" && <p>本次來源未完整取得；已保存資料可供回查，不能據此推定公告已解除或移除。</p>}
                  {officialUrl && <a href={officialUrl} target="_blank" rel="noreferrer">開啟官方來源 ↗</a>}
                </article>
              );
            })}
          </div>
        </>
      )}
    </main>
  );
}
