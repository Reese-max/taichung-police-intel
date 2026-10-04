"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  canonicalJson, normalizeQueryFilters, queryReplay, replayTrackingItems,
  requestGateway, safeHttpsUrl, trackingFilters, validateReplay,
} from "../../lib/public-query.js";
import { saveLocalConditionRequest } from "../../lib/local-conditions.js";
import "./query.css";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const GATEWAY = process.env.NEXT_PUBLIC_QUERY_GATEWAY_URL || "";
const EMPTY = { q: "", district: "", category: "", road: "", time_from: "", time_to: "" };
const FILTER_LABELS = { q: "關鍵字", district: "行政區", category: "議題／事件類型", road: "路段文字", time_from: "期間起點", time_to: "期間終點" };
const TYPE_LABELS = { traffic_control: "交通管制", large_event: "大型活動", police_announcement: "警政公告" };
const STATUS_LABELS = {
  FAILED: "蒐集或查詢失敗", PARTIAL: "涵蓋不完整", STALE: "資料陳舊", UNKNOWN: "未知",
  CONFLICT: "來源衝突", CAPABILITY_NOT_AVAILABLE: "尚未提供此查詢能力", PENDING_UPDATE: "待更新",
  CANDIDATE: "候選，待核對", SAVED_SYNTHETIC_SNAPSHOT: "有限合成快照",
};
const DISTRICTS = { "location:tc-xitun": "西屯區", "location:tc-nantun": "南屯區", "location:tc-wufeng": "霧峰區", "location:tc-west": "西區" };

function time(value) {
  if (!value) return "未提供";
  if (Number.isNaN(Date.parse(value))) return "時間無法驗證";
  return new Date(value).toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false });
}

function SourceLink({ url, children = "開啟原文" }) {
  const safe = safeHttpsUrl(url);
  return safe ? <a href={safe} target="_blank" rel="noreferrer">{children}</a> : <span>合成定位；沒有真實官方原文</span>;
}

function Documents({ event }) {
  return <details className="pq-documents"><summary>原文與 exact version／locator（{event.documents?.length || 0} 份）</summary>
    <ul>{(event.documents || []).map((doc) => <li key={doc.document_version_id}><SourceLink url={doc.official_url} /> · {doc.source_id}
      <small>版本：{doc.document_version_id}</small><small>定位：{doc.evidence_locator || "未提供"}</small><small>證據 ID：{doc.evidence_id || "未提供"}</small></li>)}</ul>
  </details>;
}

export default function PublicQueryPage() {
  const [mode, setMode] = useState("published");
  const [draft, setDraft] = useState(EMPTY);
  const [bundle, setBundle] = useState(null);
  const [replayError, setReplayError] = useState("");
  const [snapshotId, setSnapshotId] = useState("R3");
  const [sourceStatus, setSourceStatus] = useState(null);
  const [sourceError, setSourceError] = useState("");
  const [results, setResults] = useState(null);
  const [state, setState] = useState("idle");
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("events");
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [detailError, setDetailError] = useState("");
  const [saveNotice, setSaveNotice] = useState("");
  const [saveError, setSaveError] = useState("");
  const dialog = useRef(null);
  const sequence = useRef(0);

  useEffect(() => {
    let cancelled = false;
    async function load(file) {
      const response = await fetch(`${BASE_PATH}/data/${file}`, { cache: "no-store", signal: AbortSignal.timeout(12000) });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    }
    load("public-query-replay.json").then(validateReplay).then((value) => {
      if (!cancelled) setBundle(value);
    }).catch((reason) => { if (!cancelled) setReplayError(reason.message); });
    load("source-status.json").then((value) => {
      if (value?.schema_version !== 1 || !Array.isArray(value.sources)) throw new Error("來源狀態格式無法驗證");
      if (!cancelled) setSourceStatus(value);
    }).catch((reason) => { if (!cancelled) setSourceError(reason.message); });
    return () => { cancelled = true; sequence.current += 1; };
  }, []);

  useEffect(() => {
    if (selectedEvent && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [selectedEvent]);

  const snapshot = bundle?.snapshots[snapshotId];
  const districts = useMemo(() => mode === "replay"
    ? [...new Set((snapshot?.events || []).flatMap((event) => [event.district_id, ...(event.location_candidates || [])]).filter(Boolean))]
    : Object.keys(DISTRICTS), [mode, snapshot]);
  const resultsCurrent = results && results.query_mode === mode && (mode !== "replay" || results.snapshot_id === snapshotId);
  const draftDirty = resultsCurrent && (() => {
    try { return canonicalJson(normalizeQueryFilters(draft)) !== canonicalJson(results.submitted_filters); }
    catch { return true; }
  })();

  function changeMode(value) {
    sequence.current += 1; setMode(value); setResults(null); setState("idle"); setError(null);
    setDraft(EMPTY); setSaveNotice(""); setSaveError("");
  }

  async function handleSearch(cursor = null) {
    const request = ++sequence.current;
    setState("loading"); setError(null); setSaveNotice(""); setSaveError("");
    try {
      const submitted = cursor ? results.submitted_filters : normalizeQueryFilters(draft);
      let data;
      if (mode === "replay") {
        if (!bundle) throw new Error(replayError || "合成快照尚未載入");
        data = await queryReplay(bundle, submitted, { snapshot: snapshotId, cursor, limit: 20 });
      } else {
        if (submitted.road) throw Object.assign(new Error("正式 Gateway 尚無路段精度；可改用標題關鍵字查詢，或選擇合成示例驗證流程。"), { code: "CAPABILITY_NOT_AVAILABLE" });
        const tool = mode === "published" ? "search_evidence" : "search_events";
        data = await requestGateway(GATEWAY, tool, {
          ...submitted, limit: 20, ...(cursor ? { cursor, expected_generation: results.domain_query_generation_id || results.query_generation_id } : {}),
        });
        const rows = mode === "published" ? data.results : data.events;
        if (!data.publication_hash || !data.query_generation_id || !Array.isArray(rows)) {
          throw Object.assign(new Error("查詢收據缺少世代、發布 hash 或資料投影；已拒絕使用。"), { code: "UNKNOWN" });
        }
        if (cursor && (data.publication_hash !== results.publication_hash || data.query_generation_id !== results.query_generation_id)) {
          throw Object.assign(new Error("分頁的資料世代或發布 hash 已改變；請重新查詢。"), { code: "PENDING_UPDATE" });
        }
        data = { ...data, events: mode === "published" ? [] : rows, results: rows, submitted_filters: submitted };
      }
      if (request !== sequence.current) return;
      setResults({ ...data, query_mode: mode }); setState("ready");
    } catch (reason) {
      if (request !== sequence.current) return;
      setResults(null); setError({ code: reason.code || "FAILED", message: reason.message }); setState("error");
    }
  }

  async function handleGetEvent(event) {
    setDetailError(""); setComparison(null);
    if (mode === "replay") { setSelectedEvent(event); setComparison(event.comparison); }
    else {
      try {
        const detail = await requestGateway(GATEWAY, "get_event", { event_id: event.public_event_id });
        if (detail.publication_hash !== results.publication_hash || detail.query_generation_id !== results.query_generation_id) throw new Error("事件詳情與結果的世代不同，請重新查詢。");
        setSelectedEvent(detail.event);
      } catch (reason) { setDetailError(reason.message); }
    }
  }

  async function handleCompareVersions() {
    setDetailError("");
    try {
      const data = await requestGateway(GATEWAY, "compare_event_versions", { event_id: selectedEvent.public_event_id });
      if (data.publication_hash !== results.publication_hash || data.query_generation_id !== results.query_generation_id) throw new Error("版本比較與結果的世代不同，請重新查詢。");
      setComparison(data.comparison);
    } catch (reason) { setDetailError(reason.message); }
  }

  function handleAddToTracking() {
    setSaveError(""); setSaveNotice("");
    try {
      if (!resultsCurrent) throw new Error("請先執行這個模式的查詢");
      const items = mode === "replay" ? replayTrackingItems({ events: results.events }) : results.results.map((event) => ({
        ...event, event_id: event.public_event_id || event.canonical_id, headline: event.canonical_title || event.title,
        source_id: event.source_id || event.independent_source_ids?.[0], source_version: event.source_version || 1,
      }));
      saveLocalConditionRequest({ filters: trackingFilters(results.submitted_filters), namespace: mode === "replay" ? "demo:commute" : "published" }, items, {
        generation: results.query_generation_id, generated_at: results.data_as_of || results.queried_at,
        snapshot_complete: mode === "replay" ? snapshot.snapshot_complete && !results.has_more : false,
      });
      setSaveNotice("已保存這次實際套用的條件；只保存在同一瀏覽器，重新開啟時核對更新。");
    } catch (reason) { setSaveError(reason.message); }
  }

  function closeDetail() { dialog.current?.close(); setSelectedEvent(null); setComparison(null); }

  return <main className="pq-page" aria-labelledby="pq-title">
    <header className="pq-header"><div><p className="pq-eyebrow">GovIntel AI · 免登入公開查詢</p><h1 id="pq-title">公共資訊查詢</h1>
      <p className="pq-desc">先確認條件、日期與來源限制，再保存追蹤。未知時間留空，零結果不代表現實沒有事件；來源消失或到期不代表解除。</p><a href={`${BASE_PATH}/`}>回到已發布資料與原文入口</a></div></header>
    <div className="pq-field pq-mode-select"><label htmlFor="pq-mode">資料模式</label><select id="pq-mode" value={mode} onChange={(event) => changeMode(event.target.value)}>
      <option value="published">已發布原文索引 · 關鍵字查詢</option><option value="events">正式事件查詢 · 能力依已驗收資料而定</option><option value="replay">合成通勤示例 · 保存快照重播</option>
    </select></div>
    {mode === "replay" ? <div className="pq-coverage" role="status"><strong>合成資料，非即時路況、非道路安全保證。</strong><p>{bundle?.notice}</p><p>情境截止：{time(bundle?.data_cutoff)} · 本次重播時間：{time(snapshot?.as_of)}。這是預先保存的測試情境，情境時間不代表今天已發生。</p>
      <label htmlFor="pq-snapshot">重播節點 </label><select id="pq-snapshot" value={snapshotId} disabled={!bundle} onChange={(event) => { sequence.current += 1; setSnapshotId(event.target.value); setResults(null); setState("idle"); setSaveNotice(""); setError(null); }}>{Object.entries(bundle?.snapshots || {}).map(([id, value]) => <option key={id} value={id}>{id} · {value.note}</option>)}</select>{replayError && <p role="alert">{replayError}</p>}
    </div> : <p className="pq-coverage">{mode === "published" ? "只查已發布原文的標題與中介資料，不是全文或精確路段查詢。" : "正式事件資料尚未配置時顯示 CAPABILITY_NOT_AVAILABLE；不以合成示例冒充正式結果。"} 查詢不使用付費模型，也不暗改你的條件。</p>}

    <form className="pq-search-form" onSubmit={(event) => { event.preventDefault(); handleSearch(); }}><div className="pq-search-row">
      <div className="pq-field"><label htmlFor="pq-q">關鍵字（標題查詢）</label><input id="pq-q" type="search" value={draft.q} maxLength={512} onChange={(event) => setDraft({ ...draft, q: event.target.value })} placeholder="例如：施工延期" /></div>
      <div className="pq-field"><label htmlFor="pq-district">行政區</label><select id="pq-district" disabled={mode === "published"} value={draft.district} onChange={(event) => setDraft({ ...draft, district: event.target.value })}><option value="">不限定</option>{districts.map((district) => <option key={district} value={district}>{DISTRICTS[district] || district}</option>)}</select></div>
      <div className="pq-field"><label htmlFor="pq-category">議題／事件類型</label><select id="pq-category" disabled={mode === "published"} value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })}><option value="">不限定</option>{Object.entries(TYPE_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
      <div className="pq-field"><label htmlFor="pq-road">路段文字（不推論座標）</label><input id="pq-road" disabled={mode === "published"} value={draft.road} maxLength={512} onChange={(event) => setDraft({ ...draft, road: event.target.value })} placeholder="合成示例：測試路 A 至 B" aria-describedby="pq-road-note" /><small id="pq-road-note">相同路名的不同地區、日期分開顯示。</small></div>
    </div><fieldset className="pq-period" disabled={mode === "published"}><legend>適用期間：事件區間交集，Asia/Taipei（UTC+08:00）</legend><div className="pq-search-row">
      <div className="pq-field"><label htmlFor="pq-time-from">期間起點</label><input id="pq-time-from" type="datetime-local" value={draft.time_from} onChange={(event) => setDraft({ ...draft, time_from: event.target.value })} /></div><div className="pq-field"><label htmlFor="pq-time-to">期間終點</label><input id="pq-time-to" type="datetime-local" value={draft.time_to} onChange={(event) => setDraft({ ...draft, time_to: event.target.value })} /></div>
    </div></fieldset><div className="pq-actions"><button type="submit" className="pq-btn-primary" disabled={state === "loading" || (mode === "replay" && !bundle)}>查詢</button><button type="button" className="pq-btn-secondary" onClick={() => setDraft(EMPTY)}>重置編輯條件</button></div>{draftDirty && <p role="status">編輯條件尚未重新查詢；下方結果與「加入追蹤」沿用上次實際套用條件。</p>}</form>

    <nav className="pq-tabs" aria-label="查詢內容"><button type="button" aria-pressed={tab === "events"} className={tab === "events" ? "active" : ""} onClick={() => setTab("events")}>公共事件／原文結果</button><button type="button" aria-pressed={tab === "statistics"} className={tab === "statistics" ? "active" : ""} onClick={() => setTab("statistics")}>D1／D2 參考資料</button><button type="button" aria-pressed={tab === "sources"} className={tab === "sources" ? "active" : ""} onClick={() => setTab("sources")}>來源狀態</button></nav>
    {state === "loading" && <p role="status">正在核對資料……</p>}{error && <div className="pq-error" role="alert"><strong>{error.code} · {STATUS_LABELS[error.code] || "查詢未完成"}</strong><p>{error.message}</p><p>查詢失敗不能解讀成沒有事件。<a href={`${BASE_PATH}/`}>已發布原文入口仍可閱讀</a>；也可選擇明確標記的合成示例。</p></div>}{detailError && <p className="pq-error" role="alert">{detailError}</p>}

    {tab === "events" && <section className="pq-panel" aria-label="公共事件結果">{resultsCurrent ? <>
      <div className="pq-applied-filters" aria-label="目前實際套用的查詢條件"><strong>目前套用條件：</strong><ul>{Object.keys(results.submitted_filters).length ? Object.entries(results.submitted_filters).map(([key, value]) => <li key={key}><span>{FILTER_LABELS[key]}</span> <span>{TYPE_LABELS[value] || DISTRICTS[value] || value}</span></li>) : <li>不限定（只查本次資料涵蓋範圍）</li>}</ul><p>條件不會由模型暗中修改；事件模式期間採適用時間交集。</p></div>
      <div className="pq-result-meta"><span>本次有限資料符合：{results.total_matches ?? results.result_count} 筆</span><span>本頁：{results.result_count} 筆</span><span>資料時間：{time(results.data_as_of || results.queried_at)}</span><span>狀態：{STATUS_LABELS[results.status] || results.freshness || "UNKNOWN"}</span></div>
      <details className="pq-receipt"><summary>查詢收據與重播依據</summary><p>查詢 ID：{results.query_id || "未提供"}</p><p>資料世代：{results.query_generation_id}</p><p>發布／快照 hash：{results.publication_hash}</p><p>模式：{results.query_mode === "replay" ? "SYNTHETIC_REPLAY · 規則投影" : "Gateway 公開資料"}</p></details>
      {results.query_coverage && <p className="pq-coverage">涵蓋狀態：{results.query_coverage.status} · 缺口：{results.query_coverage.missing_required_sources?.join("、") || "未提供"} · 過期：{results.query_coverage.stale_required_sources?.join("、") || "未提供"}</p>}{results.source_gaps?.length > 0 && <div className="pq-gaps" role="alert"><strong>涵蓋限制與待更新：</strong><ul>{results.source_gaps.map((gap, index) => <li key={index}>{gap.source_id} · {gap.status} · {gap.reason}</li>)}</ul></div>}
      <button type="button" className="pq-btn-tracking" onClick={handleAddToTracking}>將這組實際套用條件加入追蹤</button>{saveNotice && <p role="status">{saveNotice} <a href={`${BASE_PATH}/tracking/`}>開啟我的追蹤</a></p>}{saveError && <p role="alert">保存失敗，未顯示成功：{saveError}</p>}
      {mode === "published" && <ul className="pq-event-list">{results.results.map((item) => <li className="pq-event-card" key={item.canonical_id}><h2><SourceLink url={item.official_url}>{item.title}</SourceLink></h2><p>{item.source_id} · {item.freshness_status || "UNKNOWN"}</p><p>發布時間：{time(item.published_at)} · 擷取時間：{time(item.observed_at || item.acquired_at)}</p><p>版本：{item.document_version_id || item.source_version || "未提供"} · 定位：{item.evidence_locator || "僅原文索引，未提供 exact locator"}</p></li>)}</ul>}
      {!!results.events.length && <ul className="pq-event-list">{results.events.map((event) => <li className="pq-event-card" key={event.public_event_id}><div className="pq-event-head"><span className="pq-status">{mode === "replay" ? "合成示例" : STATUS_LABELS[event.fusion_status] || event.fusion_status}</span><span>{TYPE_LABELS[event.event_type] || event.event_type}</span><code>{event.public_event_id}</code></div><h2>{event.canonical_title}</h2>
        <dl className="pq-event-facts"><div><dt>行政區</dt><dd>{DISTRICTS[event.district_id] || event.district_id || "未提供"}</dd></div><div><dt>適用起點</dt><dd>{time(event.start_at)}</dd></div><div><dt>適用終點</dt><dd>{time(event.end_at)}</dd></div><div><dt>每日時段</dt><dd>{event.daily ? `${event.daily.start}–${event.daily.end}` : "未提供"}</dd></div><div><dt>路段範圍</dt><dd>{event.road || "未提供路段精度"}</dd></div><div><dt>發布時間</dt><dd>{time(event.published_at)}</dd></div><div><dt>擷取／最後取得</dt><dd>{time(event.acquired_at || event.updated_at)}</dd></div><div><dt>事件狀態</dt><dd>{event.event_status || "UNKNOWN"}；未知不推定解除</dd></div></dl>
        {(event.location_candidates || []).length > 1 && <p>地點有歧義：{event.location_candidates.join("、")}，請修正查詢地區。</p>}<Documents event={event} /><button type="button" className="pq-btn-detail" onClick={() => handleGetEvent(event)}>查看詳情、版本歷史與版本比較</button></li>)}</ul>}
      {!results.results.length && <p className="pq-empty" role="status">有限資料內未找到符合項目。這不代表現實沒有事件，也不代表道路安全或已解除。</p>}{results.has_more && <div className="pq-pagination"><p>仍有更多符合資料；本頁不代表全部。</p><button type="button" disabled={state === "loading"} onClick={() => handleSearch(results.next_cursor)}>下一頁</button><button type="button" onClick={() => handleSearch()}>回到第一頁（重新查詢）</button></div>}
    </> : !error && <p className="pq-hint">請設定條件並查詢。正式能力不可用時會明示限制；可選擇合成通勤示例驗證查詢與追蹤流程。</p>}</section>}

    {tab === "statistics" && <section className="pq-panel" aria-label="D1 D2 參考資料"><h2>參考資料尚待來源驗收</h2><p><strong>CAPABILITY_NOT_AVAILABLE</strong>：目前沒有已驗收的 D1／D2 投影，不回傳假人口、假電話或零筆資料。</p><p>D1 應查行政區代碼、人口／戶數與民國 112 年 12 月固定期別，保留來源；不能稱為目前人口、現場人潮或受影響人數。</p><p>D2 應查原始機關名錄的公開地址與電話，保留座標品質。固定原則：<strong>機關參考，管轄另行確認</strong>；不能以距離推論管轄或警力。</p></section>}
    {tab === "sources" && <section className="pq-panel" aria-label="來源狀態"><h2>已發布來源狀態快照</h2><p>此處與合成通勤來源分開。快照截止：{time(sourceStatus?.generated_at)} · 模式：{sourceStatus?.mode || "UNKNOWN"}。歷史 FRESH 標記不代表今天仍新鮮。</p>{sourceError && <p role="alert">UNKNOWN · 來源狀態無法載入：{sourceError}</p>}<div className="pq-source-grid">{(sourceStatus?.sources || []).map((source) => <article key={source.source_id} className="pq-source-card"><h3>{source.source_id} · {source.source_name}</h3><p>健康：{source.source_health || "UNKNOWN"} · 快照新鮮度：{source.freshness_status || "UNKNOWN"} · 完整性：{source.window_completeness || "UNKNOWN"}</p><p>最後成功：{time(source.last_success_at)}</p><p>最後檢查：{time(source.last_checked_at)}</p><p>資料截止：{time(source.data_as_of)}</p><p>缺口：{source.intelligence_gaps?.join("、") || "快照未列出"}</p><SourceLink url={source.source_url} children="官方來源入口" /></article>)}</div><p>S-001／S-032／S-033／S-031 尚屬候選；此頁不宣稱正式啟用，也不承諾每天新增 100–200 筆。</p></section>}

    <dialog ref={dialog} className="pq-drawer" aria-labelledby="pq-drawer-title" onCancel={closeDetail}><header className="pq-drawer-header"><h2 id="pq-drawer-title">{selectedEvent?.canonical_title || "事件詳情"}</h2><button type="button" onClick={closeDetail} aria-label="關閉詳情">×</button></header>{selectedEvent && <div className="pq-drawer-body"><p>{selectedEvent.public_event_id} · {mode === "replay" ? "合成示例" : "公開查詢版本"}</p><Documents event={selectedEvent} /><h3>版本歷史</h3>{selectedEvent.version_history?.length ? <ol>{selectedEvent.version_history.map((version) => <li key={version.document_version_id}>{version.document_version_id} · 觀測：{time(version.observed_at)}<pre>{JSON.stringify(version.fields, null, 2)}</pre></li>)}</ol> : <p>未提供完整版本歷史。版本數：{selectedEvent.version_count ?? "未知"}</p>}{mode === "events" && <button type="button" onClick={handleCompareVersions}>比較最新兩個可用版本</button>}<h3>版本比較</h3>{comparison?.comparison_status === "COMPARED" ? <><p>實質性：{comparison.materiality} · 變更欄位：{comparison.changed_fields.join("、") || "無"}</p><div className="pq-comparison-versions"><div><h4>前版本：{comparison.before.document_version_id}</h4><pre>{JSON.stringify(comparison.before.fields, null, 2)}</pre></div><div><h4>後版本：{comparison.after.document_version_id}</h4><pre>{JSON.stringify(comparison.after.fields, null, 2)}</pre></div></div><p>未變更的每日時段、路段與起點維持原文值。</p></> : <p>{comparison?.comparison_status || "尚未取得比較"}；未知不補造日期差異。</p>}<button type="button" onClick={closeDetail} className="pq-btn-secondary">關閉詳情</button></div>}</dialog>
  </main>;
}
