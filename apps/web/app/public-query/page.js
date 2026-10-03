"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const EVENT_STORE_URL = `${BASE_PATH}/data/public-events.json`;
const STATISTICS_STORE_URL = `${BASE_PATH}/data/statistics.json`;
const SOURCE_STATUS_URL = `${BASE_PATH}/data/source-status.json`;

const STATUS_LABELS = {
  CONFIRMED: "已確認",
  CANDIDATE: "待人工確認",
  CONFLICT: "來源衝突",
  SPLIT_REQUIRED: "需要拆分",
  PARTIAL_LKG: "部分已知良好",
};

const EVENT_TYPE_LABELS = {
  large_event: "大型活動",
  traffic_control: "交通管制",
  police_announcement: "警政公告",
};

const DISTRICT_LABELS = {
  "location:tc-xitun": "西屯區",
  "location:tc-nantun": "南屯區",
  "location:tc-wufeng": "霧峰區",
  "location:tc-west": "西區",
};

const AGENCY_LABELS = {
  "agency:police": "警察局",
  "agency:traffic": "交通局",
};

const SOURCE_NAMES = {
  "S-001": "警政新聞",
  "S-004": "議事日程",
  "S-006": "質詢順序",
  "S-007": "議事錄",
  "S-009": "各項提案",
  "S-019": "市政會議",
  "S-028": "政府資料開放平台",
  "S-029": "專案報告",
  "S-032": "交通局最新消息",
  "S-033": "新聞局市政新聞",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "未知";
}

function statusClass(status) {
  return `pq-status ${String(status || "unknown").toLowerCase()}`;
}

function eventTypeLabel(type) {
  return EVENT_TYPE_LABELS[type] || type;
}

function districtLabel(id) {
  return DISTRICT_LABELS[id] || id;
}

function agencyLabel(id) {
  return AGENCY_LABELS[id] || id;
}

function sourceLabel(id) {
  return SOURCE_NAMES[id] || id;
}

function formatDateTime(value) {
  if (!value) return "未提供";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "時間格式異常";
  return parsed.toLocaleString("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function formatDate(value) {
  if (!value) return "未提供";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "日期格式異常";
  return parsed.toLocaleDateString("zh-TW", {
    timeZone: "Asia/Taipei",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
  return response.json();
}

function parseTimeInput(value) {
  if (!value) return null;
  if (value.length === 10) {
    return `${value}T00:00:00+08:00`;
  }
  return value;
}

export default function PublicQueryPage() {
  const [eventStore, setEventStore] = useState(null);
  const [statisticsStore, setStatisticsStore] = useState(null);
  const [sourceStatus, setSourceStatus] = useState(null);
  const [loadState, setLoadState] = useState("loading");
  const [loadError, setLoadError] = useState("");

  const [searchParams, setSearchParams] = useState({
    q: "",
    region: "",
    district: "",
    category: "",
    agency: "",
    time_from: "",
    time_to: "",
    verification_status: "",
    event_status: "",
    tracked: false,
    changed_only: false,
    limit: 20,
  });

  const [results, setResults] = useState(null);
  const [searchState, setSearchState] = useState("idle");
  const [searchError, setSearchError] = useState("");
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [comparison, setComparison] = useState(null);
  const [statisticsResults, setStatisticsResults] = useState(null);
  const [activeTab, setActiveTab] = useState("events");

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchJson(EVENT_STORE_URL).catch(() => null),
      fetchJson(STATISTICS_STORE_URL).catch(() => null),
      fetchJson(SOURCE_STATUS_URL).catch(() => null),
    ])
      .then(([events, stats, status]) => {
        if (cancelled) return;
        if (events?.schema_version === 1) setEventStore(events);
        if (stats?.schema_version === 1) setStatisticsStore(stats);
        if (status?.schema_version === 1) setSourceStatus(status);
        setLoadState("ready");
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadState("failed");
          setLoadError(error.message);
        }
      });
    return () => { cancelled = true; };
  }, []);

  const handleSearch = useCallback(async (params) => {
    if (!eventStore) return;
    setSearchState("loading");
    setSearchError("");
    try {
      const response = await fetch(`${BASE_PATH}/api/public-query.json`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool: "search_events", arguments: params }),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error?.message || `HTTP ${response.status}`);
      setResults(data);
      setSearchState("ready");
    } catch (error) {
      setSearchError(error.message);
      setSearchState("error");
    }
  }, [eventStore, BASE_PATH]);

  const handleGetEvent = useCallback(async (eventId) => {
    if (!eventStore) return;
    try {
      const response = await fetch(`${BASE_PATH}/api/public-query.json`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool: "get_event", arguments: { event_id: eventId } }),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error?.message || `HTTP ${response.status}`);
      setSelectedEvent(data.event);
      setComparison(null);
    } catch (error) {
      setSearchError(error.message);
    }
  }, [eventStore, BASE_PATH]);

  const handleCompareVersions = useCallback(async (eventId, beforeVersion, afterVersion) => {
    if (!eventStore) return;
    try {
      const response = await fetch(`${BASE_PATH}/api/public-query.json`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool: "compare_event_versions",
          arguments: { event_id: eventId, before_version: beforeVersion, after_version: afterVersion },
        }),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error?.message || `HTTP ${response.status}`);
      setComparison(data.comparison);
    } catch (error) {
      setSearchError(error.message);
    }
  }, [eventStore, BASE_PATH]);

  const handleQueryStatistics = useCallback(async (params) => {
    if (!statisticsStore) return;
    try {
      const response = await fetch(`${BASE_PATH}/api/public-query.json`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool: "query_statistics", arguments: params }),
      });
      const data = await response.json();
      if (!response.ok || data.error) throw new Error(data.error?.message || `HTTP ${response.status}`);
      setStatisticsResults(data);
    } catch (error) {
      setSearchError(error.message);
    }
  }, [statisticsStore, BASE_PATH]);

  const handleAddToTracking = useCallback((event) => {
    const trackingCondition = {
      type: "public_event_tracking",
      event_id: event.public_event_id,
      title: event.canonical_title,
      filters: { ...searchParams },
      timestamp: new Date().toISOString(),
    };
    localStorage.setItem("govintel_tracking_" + event.public_event_id, JSON.stringify(trackingCondition));
    alert(`已將「${event.canonical_title}」加入本機追蹤條件`);
  }, [searchParams]);

  const appliedFilters = useMemo(() => {
    const filters = [];
    if (searchParams.q) filters.push({ label: "關鍵字", value: searchParams.q });
    if (searchParams.region) filters.push({ label: "區域", value: searchParams.region });
    if (searchParams.district) filters.push({ label: "行政區", value: districtLabel(searchParams.district) });
    if (searchParams.category) filters.push({ label: "事件類型", value: eventTypeLabel(searchParams.category) });
    if (searchParams.agency) filters.push({ label: "機關", value: agencyLabel(searchParams.agency) });
    if (searchParams.time_from) filters.push({ label: "起始時間", value: formatDate(searchParams.time_from) });
    if (searchParams.time_to) filters.push({ label: "結束時間", value: formatDate(searchParams.time_to) });
    if (searchParams.verification_status) filters.push({ label: "驗證狀態", value: statusLabel(searchParams.verification_status) });
    if (searchParams.event_status) filters.push({ label: "事件狀態", value: searchParams.event_status });
    if (searchParams.tracked) filters.push({ label: "僅追蹤中", value: "是" });
    if (searchParams.changed_only) filters.push({ label: "僅有變更", value: "是" });
    return filters;
  }, [searchParams]);

  if (loadState === "loading") {
    return (
      <section className="pq-page" role="status">
        <div className="pq-loading">正在載入公開查詢資料與來源狀態……</div>
      </section>
    );
  }

  if (loadState !== "ready") {
    return (
      <section className="pq-page error" role="alert">
        <strong>公開查詢服務暫時無法載入</strong>
        <p>{loadError}</p>
        <p>請稍後重試，或檢查來源狀態頁面。</p>
      </section>
    );
  }

  return (
    <section className="pq-page" aria-labelledby="pq-title">
      <div className="pq-header">
        <div>
          <p className="pq-eyebrow">公開查詢 · 免登入</p>
          <h1 id="pq-title">公開事件與統計查詢</h1>
          <p className="pq-desc">
            以可見條件查詢已驗證的公共事件與官方統計；結果保留發布時間、原文連結、來源限制與版本差異。
            查詢條件可交接給個人追蹤（本機保存，不上傳）。
          </p>
        </div>
        <div className="pq-data-meta">
          <span>事件資料世代：{eventStore?.generation_id?.slice(0, 12)}…</span>
          <span>統計資料世代：{statisticsStore?.generation_id?.slice(0, 12)}…</span>
          <span>資料更新：{formatDateTime(eventStore?.generated_at)}</span>
        </div>
      </div>

      <form className="pq-search-form" onSubmit={(e) => { e.preventDefault(); handleSearch(searchParams); }}>
        <div className="pq-search-row">
          <div className="pq-field">
            <label htmlFor="pq-q">關鍵字</label>
            <input
              id="pq-q"
              type="search"
              value={searchParams.q}
              onChange={(e) => setSearchParams({ ...searchParams, q: e.target.value })}
              placeholder="標題、路段、議題關鍵字"
              maxLength={512}
            />
          </div>
          <div className="pq-field">
            <label htmlFor="pq-district">行政區</label>
            <select
              id="pq-district"
              value={searchParams.district}
              onChange={(e) => setSearchParams({ ...searchParams, district: e.target.value })}
            >
              <option value="">全部</option>
              <option value="location:tc-xitun">西屯區</option>
              <option value="location:tc-nantun">南屯區</option>
              <option value="location:tc-wufeng">霧峰區</option>
              <option value="location:tc-west">西區</option>
            </select>
          </div>
          <div className="pq-field">
            <label htmlFor="pq-category">事件類型</label>
            <select
              id="pq-category"
              value={searchParams.category}
              onChange={(e) => setSearchParams({ ...searchParams, category: e.target.value })}
            >
              <option value="">全部</option>
              <option value="large_event">大型活動</option>
              <option value="traffic_control">交通管制</option>
              <option value="police_announcement">警政公告</option>
            </select>
          </div>
          <div className="pq-field">
            <label htmlFor="pq-agency">相關機關</label>
            <select
              id="pq-agency"
              value={searchParams.agency}
              onChange={(e) => setSearchParams({ ...searchParams, agency: e.target.value })}
            >
              <option value="">全部</option>
              <option value="agency:police">警察局</option>
              <option value="agency:traffic">交通局</option>
            </select>
          </div>
        </div>
        <div className="pq-search-row">
          <div className="pq-field">
            <label htmlFor="pq-time-from">起始時間</label>
            <input
              id="pq-time-from"
              type="datetime-local"
              value={searchParams.time_from ? searchParams.time_from.replace("Z", "").replace("+08:00", "") : ""}
              onChange={(e) => setSearchParams({ ...searchParams, time_from: parseTimeInput(e.target.value) })}
            />
          </div>
          <div className="pq-field">
            <label htmlFor="pq-time-to">結束時間</label>
            <input
              id="pq-time-to"
              type="datetime-local"
              value={searchParams.time_to ? searchParams.time_to.replace("Z", "").replace("+08:00", "") : ""}
              onChange={(e) => setSearchParams({ ...searchParams, time_to: parseTimeInput(e.target.value) })}
            />
          </div>
          <div className="pq-field">
            <label htmlFor="pq-verification">驗證狀態</label>
            <select
              id="pq-verification"
              value={searchParams.verification_status}
              onChange={(e) => setSearchParams({ ...searchParams, verification_status: e.target.value })}
            >
              <option value="">全部</option>
              <option value="CONFIRMED">已確認</option>
              <option value="CANDIDATE">待人工確認</option>
              <option value="CONFLICT">來源衝突</option>
              <option value="SPLIT_REQUIRED">需要拆分</option>
            </select>
          </div>
          <div className="pq-field pq-checkbox-group">
            <label>
              <input
                type="checkbox"
                checked={searchParams.tracked}
                onChange={(e) => setSearchParams({ ...searchParams, tracked: e.target.checked })}
              />
              僅追蹤中
            </label>
            <label>
              <input
                type="checkbox"
                checked={searchParams.changed_only}
                onChange={(e) => setSearchParams({ ...searchParams, changed_only: e.target.checked })}
              />
              僅有變更
            </label>
          </div>
        </div>
        <div className="pq-actions">
          <button type="submit" disabled={searchState === "loading"} className="pq-btn-primary">
            {searchState === "loading" ? "查詢中…" : "查詢"}
          </button>
          <button type="button" onClick={() => setSearchParams({
            q: "", region: "", district: "", category: "", agency: "",
            time_from: "", time_to: "", verification_status: "", event_status: "",
            tracked: false, changed_only: false, limit: 20,
          })} className="pq-btn-secondary">
            重置條件
          </button>
        </div>
      </form>

      {appliedFilters.length > 0 && (
        <div className="pq-applied-filters" role="status" aria-label="目前套用的查詢條件">
          <strong>目前套用條件：</strong>
          <ul>
            {appliedFilters.map((f, i) => (
              <li key={i}><span className="pq-filter-label">{f.label}</span><span className="pq-filter-value">{f.value}</span></li>
            ))}
          </ul>
        </div>
      )}

      <div className="pq-tabs" role="tablist" aria-label="查詢結果分頁">
        <button
          role="tab"
          aria-selected={activeTab === "events"}
          onClick={() => setActiveTab("events")}
          className={activeTab === "events" ? "active" : ""}
        >
          公共事件
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "statistics"}
          onClick={() => setActiveTab("statistics")}
          className={activeTab === "statistics" ? "active" : ""}
        >
          官方統計
        </button>
        <button
          role="tab"
          aria-selected={activeTab === "sources"}
          onClick={() => setActiveTab("sources")}
          className={activeTab === "sources" ? "active" : ""}
        >
          來源狀態
        </button>
      </div>

      {searchState === "loading" && <div className="pq-loading" role="status">查詢中…</div>}
      {searchState === "error" && <div className="pq-error" role="alert">{searchError}</div>}

      {activeTab === "events" && (
        <div className="pq-panel" role="tabpanel" aria-label="公共事件結果">
          {results ? (
            <>
              <div className="pq-result-meta">
                <span>總符合：{results.total_matches} 筆</span>
                <span>本頁：{results.result_count} 筆</span>
                {results.truncated && <span className="pq-truncated">結果已截斷，僅顯示前 {results.result_count} 筆</span>}
                <span>查詢世代：{results.query_generation_id?.slice(0, 12)}…</span>
                <span>查詢時間：{formatDateTime(results.queried_at)}</span>
              </div>
              {results.query_coverage && (
                <div className="pq-coverage" data-testid="query-coverage">
                  <strong>涵蓋狀態：</strong> {results.query_coverage.status}
                  {results.query_coverage.missing_required_sources?.length > 0 && (
                    <span> · 缺口來源：{results.query_coverage.missing_required_sources.join("、")}</span>
                  )}
                  {results.query_coverage.stale_required_sources?.length > 0 && (
                    <span> · 過期來源：{results.query_coverage.stale_required_sources.join("、")}</span>
                  )}
                  <span> · {results.query_coverage.coverage_limitations?.[0] || "僅代表核准快照範圍"}</span>
                </div>
              )}
              {results.source_gaps?.length > 0 && (
                <div className="pq-gaps" role="alert">
                  <strong>資料缺口：</strong>
                  <ul>
                    {results.source_gaps.map((gap, i) => (
                      <li key={i}>
                        {gap.source_id ? `${gap.source_id}：` : ""}{gap.reason}
                        {gap.freshness_status && `（${gap.freshness_status}）`}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {results.results.length > 0 ? (
                <>
                <ul className="pq-event-list">
                  {results.results.map((event) => (
                    <li key={event.public_event_id} className="pq-event-card">
                      <div className="pq-event-head">
                        <span className={statusClass(event.fusion_status)}>{statusLabel(event.fusion_status)}</span>
                        <span className="pq-event-type">{eventTypeLabel(event.event_type)}</span>
                        <code>{event.public_event_id}</code>
                      </div>
                      <h3>{event.canonical_title}</h3>
                      <dl className="pq-event-facts">
                        <div><dt>事件日期</dt><dd>{formatDate(event.event_date)}</dd></div>
                        <div><dt>時間區間</dt><dd>{formatDateTime(event.start_at)} 〜 {formatDateTime(event.end_at)}</dd></div>
                        <div><dt>行政區</dt><dd>{districtLabel(event.district_id)}</dd></div>
                        {event.location_candidates.length > 1 && (
                          <div><dt>地點候選</dt><dd>{event.location_candidates.map(districtLabel).join("、")}</dd></div>
                        )}
                        <div><dt>相關機關</dt><dd>{event.agency_ids.map(agencyLabel).join("、")}</dd></div>
                        <div><dt>獨立來源</dt><dd>{event.independent_source_count} 個（{event.independent_source_ids.map(sourceLabel).join("、")}）</dd></div>
                        {event.changed_fields?.length > 0 && (
                          <div><dt>有變更欄位</dt><dd>{event.changed_fields.join("、")}</dd></div>
                        )}
                        {event.conflict_fields?.length > 0 && (
                          <div><dt>衝突欄位</dt><dd className="pq-conflict">{event.conflict_fields.join("、")}</dd></div>
                        )}
                      </dl>
                      <details className="pq-documents">
                        <summary>展開 {event.documents?.length || 0} 份官方文件與版本</summary>
                        <div className="pq-document-list">
                          {event.documents?.map((doc) => (
                            <article key={doc.document_version_id} className="pq-document">
                              <div className="pq-doc-head">
                                <strong>{sourceLabel(doc.source_id)}</strong>
                                <a href={doc.official_url} target="_blank" rel="noreferrer">開啟官方來源</a>
                              </div>
                              <small>{doc.document_id} · v{doc.document_version_id.split(":").pop()}</small>
                              {doc.evidence_locator && <small>定位：{doc.evidence_locator}</small>}
                            </article>
                          ))}
                        </div>
                      </details>
                      {event.version_history?.length > 0 && (
                        <details className="pq-versions">
                          <summary>展開版本歷史（{event.version_history.length} 版）</summary>
                          <div className="pq-version-list">
                            {event.version_history.map((ver) => (
                              <article key={ver.document_version_id} className="pq-version">
                                <div><strong>{ver.document_version_id}</strong> · 觀測：{formatDateTime(ver.observed_at)}</div>
                                <div>欄位：{JSON.stringify(ver.fields)}</div>
                                {ver.changed_fields?.length > 0 && <div className="pq-changed">變更：{ver.changed_fields.join("、")}</div>}
                              </article>
                            ))}
                          </div>
                        </details>
                      )}
                      <div className="pq-event-actions">
                        <button
                          type="button"
                          onClick={() => handleGetEvent(event.public_event_id)}
                          className="pq-btn-detail"
                        >
                          查看完整詳情與版本比較
                        </button>
                        <button
                          type="button"
                          onClick={() => handleAddToTracking(event)}
                          className="pq-btn-tracking"
                        >
                          加入追蹤
                        </button>
                        {event.documents?.[0]?.official_url && (
                          <a href={event.documents[0].official_url} target="_blank" rel="noreferrer" className="pq-btn-source">
                            開啟官方來源
                          </a>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
                {results.has_more && (
                  <div className="pq-pagination">
                    <p>還有更多結果。使用捲動或分頁載入（目前為簡化版，僅顯示第一頁）。</p>
                  </div>
                )}
                </>
              ) : (
                <div className="pq-empty" role="status">
                  <p>找不到符合條件的公共事件。</p>
                  {results.answerable_no_match ? (
                    <p>核准快照內確無符合條件資料。</p>
                  ) : (
                    <p>這不代表現實中沒有事件，請參考上方資料缺口與涵蓋狀態。</p>
                  )}
                </div>
              )}
            </>
          ) : searchState === "idle" ? (
            <div className="pq-hint">
              <p>請設定查詢條件並點擊「查詢」，或直接點擊查詢以預設條件瀏覽所有事件。</p>
              <button type="button" onClick={() => handleSearch(searchParams)} className="pq-btn-primary">
                以預設條件查詢全部
              </button>
            </div>
          ) : null}
        </div>
      )}

      {activeTab === "statistics" && statisticsStore && (
        <div className="pq-panel" role="tabpanel" aria-label="官方統計結果">
          <div className="pq-stats-search">
            <div className="pq-field">
              <label htmlFor="stats-dataset">資料集</label>
              <select
                id="stats-dataset"
                value={""}
                onChange={(e) => handleQueryStatistics({ dataset_id: e.target.value, limit: 20 })}
              >
                <option value="">全部</option>
                <option value="CTX-POP">人口統計</option>
                <option value="NPA-STAT-1">詐騙統計</option>
                <option value="NPA-STAT-2">交通事故統計</option>
              </select>
            </div>
            <div className="pq-field">
              <label htmlFor="stats-period-from">起始期別</label>
              <input id="stats-period-from" type="text" placeholder="2026-08" />
            </div>
            <div className="pq-field">
              <label htmlFor="stats-period-to">結束期別</label>
              <input id="stats-period-to" type="text" placeholder="2026-08" />
            </div>
            <button type="button" onClick={() => handleQueryStatistics({ limit: 20 })} className="pq-btn-primary">查詢統計</button>
          </div>
          {statisticsResults && (
            <>
              <div className="pq-result-meta">
                <span>總符合：{statisticsResults.total_matches} 筆</span>
                <span>本頁：{statisticsResults.result_count} 筆</span>
                <span>查詢世代：{statisticsResults.query_generation_id?.slice(0, 12)}…</span>
              </div>
              {statisticsResults.results.length > 0 ? (
                <table className="pq-stats-table">
                  <thead>
                    <tr>
                      <th>統計ID</th>
                      <th>資料集</th>
                      <th>指標</th>
                      <th>期別</th>
                      <th>數值</th>
                      <th>單位</th>
                      <th>地理</th>
                      <th>機關</th>
                      <th>暫定</th>
                      <th>更新時間</th>
                      <th>來源</th>
                    </tr>
                  </thead>
                  <tbody>
                    {statisticsResults.results.map((stat) => (
                      <tr key={stat.statistic_id}>
                        <td>{stat.statistic_id}</td>
                        <td>{stat.dataset_id}</td>
                        <td>{stat.metric}</td>
                        <td>{stat.period}</td>
                        <td>{stat.value.toLocaleString()}</td>
                        <td>{stat.unit}</td>
                        <td>{stat.geography}</td>
                        <td>{stat.agency}</td>
                        <td>{stat.provisional ? "是" : "否"}</td>
                        <td>{formatDateTime(stat.updated_at)}</td>
                        <td><a href={stat.official_url} target="_blank" rel="noreferrer">開啟</a></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p>無符合統計資料。</p>
              )}
            </>
          )}
        </div>
      )}

      {activeTab === "sources" && sourceStatus && (
        <div className="pq-panel" role="tabpanel" aria-label="來源狀態">
          <div className="pq-source-grid">
            {(sourceStatus.sources || []).map((source) => (
              <article key={source.source_id} className="pq-source-card">
                <div className="pq-source-head">
                  <span className={`pq-source-dot ${source.source_health === "PASS" ? "pass" : "fail"}`} />
                  <strong>{source.source_id}</strong>
                </div>
                <h3>{sourceLabel(source.source_id)}</h3>
                <p>{source.source_health} · {source.freshness_status}</p>
                <small>最後檢查：{formatDateTime(source.last_checked_at)}</small>
                {source.intelligence_gaps?.length > 0 && (
                  <small className="pq-gaps">缺口：{source.intelligence_gaps.join("、")}</small>
                )}
                {source.data_as_of && (
                  <small>資料截止：{formatDate(source.data_as_of)}</small>
                )}
                {source.source_url && (
                  <a href={source.source_url} target="_blank" rel="noreferrer">官方來源入口</a>
                )}
              </article>
            ))}
          </div>
        </div>
      )}

      {selectedEvent && (
        <aside className="pq-drawer" role="dialog" aria-modal="true" aria-labelledby="pq-drawer-title">
          <header className="pq-drawer-header">
            <h2 id="pq-drawer-title">{selectedEvent.canonical_title}</h2>
            <button type="button" onClick={() => setSelectedEvent(null)} aria-label="關閉詳情">×</button>
          </header>
          <div className="pq-drawer-body">
            <div className="pq-event-detail">
              <dl>
                <div><dt>事件ID</dt><dd><code>{selectedEvent.public_event_id}</code></dd></div>
                <div><dt>狀態</dt><dd><span className={statusClass(selectedEvent.fusion_status)}>{statusLabel(selectedEvent.fusion_status)}</span></dd></div>
                <div><dt>類型</dt><dd>{eventTypeLabel(selectedEvent.event_type)}</dd></div>
                <div><dt>日期</dt><dd>{formatDate(selectedEvent.event_date)}</dd></div>
                <div><dt>時間</dt><dd>{formatDateTime(selectedEvent.start_at)} 〜 {formatDateTime(selectedEvent.end_at)}</dd></div>
                <div><dt>行政區</dt><dd>{districtLabel(selectedEvent.district_id)}</dd></div>
                <div><dt>機關</dt><dd>{selectedEvent.agency_ids.map(agencyLabel).join("、")}</dd></div>
                <div><dt>來源</dt><dd>{selectedEvent.independent_source_ids.map(sourceLabel).join("、")}</dd></div>
              </dl>
              <details open>
                <summary>官方文件（{selectedEvent.documents?.length || 0} 份）</summary>
                <ul>
                  {selectedEvent.documents?.map((doc) => (
                    <li key={doc.document_version_id}>
                      <a href={doc.official_url} target="_blank" rel="noreferrer">{sourceLabel(doc.source_id)}</a>
                      · v{doc.document_version_id.split(":").pop()}
                      {doc.evidence_locator && ` · ${doc.evidence_locator}`}
                    </li>
                  ))}
                </ul>
              </details>
              {selectedEvent.version_history?.length > 0 && (
                <details open>
                  <summary>版本歷史（{selectedEvent.version_history.length} 版）</summary>
                  <ul>
                    {selectedEvent.version_history.map((ver) => (
                      <li key={ver.document_version_id}>
                        {ver.document_version_id} · 觀測：{formatDateTime(ver.observed_at)}
                        {ver.changed_fields?.length > 0 && ` · 變更：${ver.changed_fields.join("、")}`}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {comparison && comparison.public_event_id === selectedEvent.public_event_id && (
                <details open>
                  <summary>版本比較</summary>
                  <div className="pq-comparison">
                    <p><strong>比較狀態：</strong> {comparison.comparison_status}</p>
                    <p><strong>實質性：</strong> {comparison.materiality}</p>
                    <p><strong>變更欄位：</strong> {comparison.changed_fields.join("、") || "無"}</p>
                    <div className="pq-comparison-versions">
                      <div>
                        <h4>前版本（{comparison.before?.document_version_id}）</h4>
                        <pre>{JSON.stringify(comparison.before?.fields, null, 2)}</pre>
                        <p>觀測：{formatDateTime(comparison.observed_at?.before)}</p>
                      </div>
                      <div>
                        <h4>後版本（{comparison.after?.document_version_id}）</h4>
                        <pre>{JSON.stringify(comparison.after?.fields, null, 2)}</pre>
                        <p>觀測：{formatDateTime(comparison.observed_at?.after)}</p>
                      </div>
                    </div>
                  </div>
                </details>
              )}
              {selectedEvent.version_history?.length > 1 && !comparison && (
                <div className="pq-version-compare">
                  <h4>選擇版本進行比較</h4>
                  <div className="pq-compare-selectors">
                    <select
                      value={""}
                      onChange={(e) => handleCompareVersions(
                        selectedEvent.public_event_id,
                        e.target.value,
                        selectedEvent.version_history[selectedEvent.version_history.length - 1].document_version_id
                      )}
                    >
                      <option value="">選擇前版本</option>
                      {selectedEvent.version_history.slice(0, -1).map((ver) => (
                        <option key={ver.document_version_id} value={ver.document_version_id}>
                          {ver.document_version_id} ({formatDateTime(ver.observed_at)})
                        </option>
                      ))}
                    </select>
                    <span>→</span>
                    <select
                      value={""}
                      onChange={(e) => handleCompareVersions(
                        selectedEvent.public_event_id,
                        selectedEvent.version_history[0].document_version_id,
                        e.target.value
                      )}
                    >
                      <option value="">選擇後版本</option>
                      {selectedEvent.version_history.slice(1).map((ver) => (
                        <option key={ver.document_version_id} value={ver.document_version_id}>
                          {ver.document_version_id} ({formatDateTime(ver.observed_at)})
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              )}
              <div className="pq-drawer-actions">
                <button type="button" onClick={() => setSelectedEvent(null)} className="pq-btn-secondary">關閉</button>
                <button type="button" onClick={() => handleAddToTracking(selectedEvent)} className="pq-btn-tracking">加入追蹤</button>
              </div>
            </div>
          </div>
        </aside>
      )}
    </section>
  );
}
