"use client";

import { useEffect, useMemo, useState } from "react";


const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const BRIEF_URL = `${BASE_PATH}/data/v2-daily-brief.json`;
const ARCHIVE_URL = `${BASE_PATH}/data/intelligence-feed.json`;
const STATUS_URL = `${BASE_PATH}/data/source-status.json`;

const SOURCE_NAMES = {
  "S-004": "議事日程",
  "S-006": "質詢順序",
  "S-007": "議事錄",
  "S-009": "各項提案",
  "S-029": "專案報告",
};

const CHANGE_LABELS = {
  NEW: "本次首次偵測",
  REVISED: "內容修正",
  STATUS_CHANGED: "狀態變更",
  DEADLINE_CHANGED: "時程變更",
  REMOVED: "確認移除",
};

const TIME_BASIS_LABELS = {
  OFFICIAL_DATE: "官方日期",
  FIRST_SEEN: "系統首次偵測",
  DETECTED_CHANGE: "系統偵測變更",
};

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
  return response.json();
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

function PublicationStatus({ publication }) {
  const health = publication.source_health || {};
  const isReady = publication.publication_status === "READY";
  return (
    <div className={`v2-publication-status ${isReady ? "ready" : "partial"}`} role="status">
      <span className="v2-status-dot" aria-hidden="true" />
      <div>
        <strong>{isReady ? "本期資料完整" : "本期資料部分完成"}</strong>
        <span>
          {health.pass_count || 0} 個來源正常
          {health.stale_count ? ` · ${health.stale_count} 個資料較舊` : ""}
          {health.failed_count ? ` · ${health.failed_count} 個來源失敗` : ""}
          {health.gap_count ? ` · ${health.gap_count} 個情報缺口` : ""}
        </span>
      </div>
    </div>
  );
}

function Metric({ value, label, emphasis = false }) {
  return (
    <div className={`v2-metric ${emphasis ? "emphasis" : ""}`}>
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  );
}

function ActionCard({ item, index }) {
  const affectedRoles = Array.isArray(item.affected_roles) ? item.affected_roles : [];
  return (
    <article className="v2-action-card" data-testid="v2-priority-card">
      <div className="v2-action-card-heading">
        <div>
          <span className="v2-rank">{String(index + 1).padStart(2, "0")}</span>
          <span className="v2-change-type">
            {CHANGE_LABELS[item.change_type] || item.change_type}
          </span>
        </div>
        <span className="v2-source-tag">
          {SOURCE_NAMES[item.source_id] || item.source_name || item.source_id}
        </span>
      </div>

      <h3>{item.headline}</h3>

      <dl className="v2-action-grid">
        <div>
          <dt>發生什麼事</dt>
          <dd>{item.what_changed}</dd>
        </div>
        <div>
          <dt>為什麼重要</dt>
          <dd>{item.why_it_matters}</dd>
        </div>
        <div>
          <dt>建議處置</dt>
          <dd>{item.recommended_action}</dd>
        </div>
        <div>
          <dt>時間依據</dt>
          <dd>
            {TIME_BASIS_LABELS[item.temporal_basis] || item.temporal_basis}
            {item.deadline ? ` · ${formatDateTime(item.deadline)}` : ""}
          </dd>
        </div>
      </dl>

      <div className="v2-role-row" aria-label="可能受影響角色">
        {affectedRoles.map((role) => (
          <span key={role}>{role}</span>
        ))}
      </div>

      <div className="v2-action-footer">
        <span>
          {item.verification_status === "DETERMINISTIC_PASS"
            ? "規則驗證通過"
            : "待驗證"}
        </span>
        <a href={item.official_url} target="_blank" rel="noreferrer">
          開啟官方來源
        </a>
      </div>
    </article>
  );
}

function SourceHealthSummary({ sourceStatus }) {
  const sources = Array.isArray(sourceStatus?.sources) ? sourceStatus.sources : [];
  if (!sources.length) return null;

  return (
    <details className="v2-source-health">
      <summary>查看五個官方來源健康狀態</summary>
      <div className="v2-source-health-grid">
        {sources.map((source) => (
          <article key={source.source_id}>
            <div>
              <span
                className={`v2-source-dot ${source.source_health === "PASS" ? "pass" : "fail"}`}
                aria-hidden="true"
              />
              <strong>{source.source_id}</strong>
            </div>
            <h3>{SOURCE_NAMES[source.source_id] || source.source_name}</h3>
            <p>
              {source.source_health} · {source.freshness_status}
            </p>
            <small>最後檢查：{formatDateTime(source.last_checked_at)}</small>
            {source.intelligence_gaps?.length > 0 && (
              <small>缺口：{source.intelligence_gaps.join("、")}</small>
            )}
            <a href={source.source_url} target="_blank" rel="noreferrer">
              官方來源
            </a>
          </article>
        ))}
      </div>
    </details>
  );
}

export default function V2DailyDashboard() {
  const [publication, setPublication] = useState(null);
  const [archive, setArchive] = useState(null);
  const [sourceStatus, setSourceStatus] = useState(null);
  const [loadState, setLoadState] = useState("loading");
  const [loadError, setLoadError] = useState("");
  const [archiveQuery, setArchiveQuery] = useState("");

  useEffect(() => {
    let cancelled = false;

    fetchJson(BRIEF_URL)
      .then((data) => {
        if (cancelled) return;
        if (
          data?.schema_version !== 1
          || data?.mode !== "V2_SHADOW"
          || data?.generator_version !== 2
          || !data?.overview
          || !Array.isArray(data?.priority_items)
        ) {
          setLoadState("schema_invalid");
          setLoadError("V2 每日情報格式不相容，已停止呈現以避免誤判。");
          return;
        }
        setPublication(data);
        setLoadState("ready");
      })
      .catch((error) => {
        if (cancelled) return;
        setLoadState("fetch_failed");
        setLoadError(`無法讀取本期 V2 情報：${error.message}`);
      });

    fetchJson(ARCHIVE_URL)
      .then((data) => {
        if (!cancelled && data?.schema_version === 1 && Array.isArray(data?.items)) {
          setArchive(data);
        }
      })
      .catch(() => {});

    fetchJson(STATUS_URL)
      .then((data) => {
        if (!cancelled && data?.schema_version === 1) setSourceStatus(data);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, []);

  const filteredArchive = useMemo(() => {
    const items = Array.isArray(archive?.items) ? archive.items : [];
    const needle = archiveQuery.trim().toLocaleLowerCase("zh-Hant");
    if (!needle) return items.slice(0, 8);
    return items
      .filter((item) => {
        const haystack = [item.title, item.committee, item.source_id]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase("zh-Hant");
        return haystack.includes(needle);
      })
      .slice(0, 20);
  }, [archive, archiveQuery]);

  const overview = publication?.overview || {};
  const priorityItems = Array.isArray(publication?.priority_items)
    ? publication.priority_items.slice(0, 3)
    : [];
  const trackingItems = Array.isArray(publication?.tracking_items)
    ? publication.tracking_items.slice(0, 5)
    : [];
  const otherChanges = Array.isArray(publication?.other_changes)
    ? publication.other_changes
    : [];

  return (
    <main className="v2-home" id="v2-daily-intelligence">
      <header className="v2-hero">
        <div>
          <p className="v2-eyebrow">公開來源 · 政策與議會追蹤</p>
          <h1>臺中警政每日情資</h1>
          <p className="v2-hero-copy">
            只呈現本期真正新增、修正、狀態或時程變更；既有資料留在歷史區，不冒充今日情報。
          </p>
        </div>
        {publication && (
          <div className="v2-updated">
            <span>本期更新</span>
            <strong>{formatDateTime(publication.generated_at)}</strong>
            <small>Asia/Taipei</small>
          </div>
        )}
      </header>

      {loadState === "loading" && (
        <section className="v2-system-message" role="status">
          正在載入本期每日情報與來源健康狀態……
        </section>
      )}

      {loadState !== "loading" && loadState !== "ready" && (
        <section className="v2-system-message error" role="alert">
          <strong>本期情報暫時無法安全呈現</strong>
          <p>{loadError}</p>
          <p>這不是「零筆情報」；請改看下方歷史與來源證據區。</p>
        </section>
      )}

      {loadState === "ready" && publication && (
        <>
          <PublicationStatus publication={publication} />

          <section className="v2-metrics" aria-label="本期情報摘要">
            <Metric value={overview.current_change_count || 0} label="本期真正變更" emphasis />
            <Metric value={overview.priority_count || 0} label="今日重點" />
            <Metric value={overview.tracking_count || 0} label="持續追蹤" />
            <Metric value={overview.archive_total || 0} label="歷史資料" />
          </section>

          <section className="v2-priority-section" aria-labelledby="v2-priority-title">
            <div className="v2-section-heading">
              <div>
                <p className="v2-eyebrow">10 秒掌握</p>
                <h2 id="v2-priority-title">今日重點</h2>
              </div>
              <span>最多 3 件</span>
            </div>

            {priorityItems.length === 0 ? (
              <div className="v2-empty-priority" data-testid="v2-empty-priority" role="status">
                <strong>本期沒有需要處理的重要變更</strong>
                <p>{publication.status_message}</p>
                <p>
                  已檢查 {publication.source_health?.pass_count || 0} 個正常官方來源；
                  {overview.archive_total || 0} 筆既有資料保留於歷史區，不列為今日情資。
                </p>
              </div>
            ) : (
              <div className="v2-action-list">
                {priorityItems.map((item, index) => (
                  <ActionCard key={item.event_id} item={item} index={index} />
                ))}
              </div>
            )}
          </section>

          {trackingItems.length > 0 && (
            <section className="v2-tracking-section" aria-labelledby="v2-tracking-title">
              <div className="v2-section-heading">
                <div>
                  <p className="v2-eyebrow">近期節點</p>
                  <h2 id="v2-tracking-title">持續追蹤</h2>
                </div>
                <span>最多 5 件</span>
              </div>
              <div className="v2-tracking-list">
                {trackingItems.map((item) => (
                  <a key={item.event_id} href={item.official_url} target="_blank" rel="noreferrer">
                    <strong>{item.headline}</strong>
                    <span>{item.recommended_action}</span>
                  </a>
                ))}
              </div>
            </section>
          )}

          {otherChanges.length > 0 && (
            <details className="v2-other-changes">
              <summary>查看其他 {otherChanges.length} 件真正變更</summary>
              <div>
                {otherChanges.map((item) => (
                  <a key={item.event_id} href={item.official_url} target="_blank" rel="noreferrer">
                    <span>{CHANGE_LABELS[item.change_type] || item.change_type}</span>
                    <strong>{item.headline}</strong>
                  </a>
                ))}
              </div>
            </details>
          )}

          <section className="v2-archive" aria-labelledby="v2-archive-title">
            <div className="v2-section-heading">
              <div>
                <p className="v2-eyebrow">背景資料</p>
                <h2 id="v2-archive-title">歷史資料庫</h2>
              </div>
              <span>{overview.archive_total || 0} 筆索引</span>
            </div>
            <p className="v2-archive-note">
              以下資料是目前可查詢的官方歷史索引，不代表本期新增，也不會自動列入今日重點。
            </p>
            <label className="v2-archive-search">
              <span>搜尋歷史資料</span>
              <input
                type="search"
                value={archiveQuery}
                onChange={(event) => setArchiveQuery(event.target.value)}
                placeholder="輸入分局、交通、警力、提案等關鍵字"
              />
            </label>
            {archive ? (
              <div className="v2-archive-list">
                {filteredArchive.map((item) => (
                  <a key={item.stable_id} href={item.official_url} target="_blank" rel="noreferrer">
                    <span>{SOURCE_NAMES[item.source_id] || item.source_id}</span>
                    <strong>{item.title || "（官方資料未提供標題）"}</strong>
                    <small>歷史資料 · 開啟官方來源</small>
                  </a>
                ))}
                {filteredArchive.length === 0 && <p>找不到符合關鍵字的歷史資料。</p>}
              </div>
            ) : (
              <p className="v2-archive-unavailable">歷史索引暫時無法載入，V2 今日情報仍可獨立使用。</p>
            )}
          </section>

          <SourceHealthSummary sourceStatus={sourceStatus} />
        </>
      )}
    </main>
  );
}
