"use client";

import { useEffect, useMemo, useState } from "react";
import { assessPublication } from "../lib/publication-freshness.mjs";
import {
  addLocalWatch,
  confirmLocalHandoff,
  emptyLocalHandoff,
  exportLocalHandoff,
  latestHandoff,
  loadLocalHandoff,
  projectLocalTracking,
  saveLocalHandoff,
  setLocalWatchStatus,
  syncLocalHandoff,
} from "../lib/local-handoff.js";
import {
  emptyLocalReview,
  exportLocalReview,
  loadLocalReview,
  projectLocalReview,
  saveLocalReview,
  setLocalReviewDecision,
  syncLocalReview,
} from "../lib/local-review.js";
import QueryGatewayPanel from "./QueryGatewayPanel.js";


const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const BRIEF_URL = `${BASE_PATH}/data/v2-daily-brief.json`;
const ARCHIVE_URL = `${BASE_PATH}/data/intelligence-feed.json`;
const STATUS_URL = `${BASE_PATH}/data/source-status.json`;
const SYSTEM_HEALTH_URL = `${BASE_PATH}/data/system-health.json`;

const SOURCE_NAMES = {
  "S-004": "議事日程",
  "S-006": "質詢順序",
  "S-007": "議事錄",
  "S-009": "各項提案",
  "S-029": "專案報告",
  "S-001": "警政新聞",
  "S-019": "市政會議／專案報告",
  "S-032": "交通局最新消息",
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

const RELEVANCE_LABELS = {
  affected_role_match: "職責命中",
  topic_match: "議題命中",
  source_priority: "來源優先",
  deadline_within_72h: "72 小時內期限",
  status_changed: "狀態變更",
  explicit_follow_up: "明確後續追蹤",
  default: "綜合預設",
};

const REVIEW_STATUS_LABELS = {
  OPEN: "待覆核",
  KEEP_WATCHING: "保持追蹤",
  RESOLVED: "本機已處理",
  DISMISSED: "本機暫時忽略",
  CANONICAL_ONLY: "尚未建立本機覆核紀錄",
};

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} HTTP ${response.status}`);
  return response.json();
}

function samePublicationGeneration(brief, feed, sourceStatus) {
  const briefRun = brief?.source_collection_run_id || brief?.collection_run_id;
  const feedRun = feed?.collection_run_id || feed?.source_collection_run_id;
  const statusRun = sourceStatus?.latest_collection_run?.collection_run_id || sourceStatus?.collection_run_id;
  return Boolean(
    briefRun && briefRun === feedRun && briefRun === statusRun
      && brief?.generated_at === feed?.generated_at
      && brief?.generated_at === sourceStatus?.generated_at,
  );
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

function PublicationStatus({ publication, assessment }) {
  const health = publication.source_health || {};
  const isReady = assessment.canReassure;
  const labels = { RECENT: "近期監測快照", STALE: "已保存快照過期", PARTIAL: "監測範圍不完整", UNKNOWN: "資料時效待核對" };
  return (
    <div className={`v2-publication-status ${isReady ? "ready" : "partial"}`} role="status">
      <span className="v2-status-dot" aria-hidden="true" />
      <div>
        <strong>{labels[assessment.state]}</strong>
        <span>
          快照記錄 {health.pass_count || 0} 個來源正常
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

function PublicationProvenance({ publication, archive, sourceStatus, candidate, generationMixed }) {
  const briefRun = publication?.source_collection_run_id || publication?.collection_run_id;
  const feedRun = archive?.collection_run_id || archive?.source_collection_run_id;
  const statusRun = sourceStatus?.latest_collection_run?.collection_run_id || sourceStatus?.collection_run_id;
  return (
    <section className="v2-provenance" data-testid="candidate-version" aria-labelledby="v2-provenance-title">
      <div className="v2-section-heading">
        <div>
          <p className="v2-eyebrow">版本識別</p>
          <h2 id="v2-provenance-title">資料版本與涵蓋範圍</h2>
        </div>
        <span data-testid="generation-badge">{generationMixed ? "世代不一致 · 已拒絕混版" : "同一世代"}</span>
      </div>
      <dl>
        <div><dt>簡報世代</dt><dd>{briefRun || "未提供"}</dd></div>
        <div><dt>資料庫世代</dt><dd>{feedRun || "未提供"}</dd></div>
        <div><dt>來源狀態世代</dt><dd>{statusRun || "未提供"}</dd></div>
        <div><dt>資料性質</dt><dd>保存快照 · 非即時資料</dd></div>
        <div><dt>查詢索引世代</dt><dd data-testid="query-generation">{candidate?.query_generation_id || "查詢後提供"}</dd></div>
        {candidate?.policy_hash && <div><dt>來源政策</dt><dd>v{candidate.policy_version} · {candidate.policy_hash.slice(0, 12)}…</dd></div>}
      </dl>
      {candidate?.unavailable_capabilities?.length > 0 && (
        <p data-testid="candidate-capabilities">
          未提供能力：{candidate.unavailable_capabilities.map((row) => `${row.capability_id} · ${row.status}`).join("、")}
        </p>
      )}
    </section>
  );
}

function ReviewInboxPanel({ health, localReview, onDecision, onExport, notice, error }) {
  const canonicalItems = Array.isArray(health.review_inbox) ? health.review_inbox : [];
  const items = projectLocalReview(localReview, canonicalItems);
  const hasLocalItems = Boolean(localReview && Object.keys(localReview.items || {}).length);
  return (
    <section className="v2-review-inbox" data-testid="v2-review-inbox" aria-label="Review Inbox">
      <div className="v2-review-heading">
        <strong>Review Inbox：{health.review_inbox_total ?? canonicalItems.length} 件</strong>
        <span>公開摘要；本機操作不會修改 canonical state。</span>
      </div>
      {items.length > 0 ? (
        <ul>
          {items.slice(0, 5).map((item) => {
            const status = item.local_status || "OPEN";
            const localActionDisabled = !localReview || status === "CANONICAL_ONLY";
            return (
              <li key={item.review_id || `${item.source_id}-${item.observed_at}`} className="v2-review-item">
                <div>
                  <strong>{item.reason || item.status}</strong>
                  <span>{item.entity_ids?.source_id || item.source_id || "UNKNOWN"}</span>
                  <small>{REVIEW_STATUS_LABELS[status] || status} · v{item.source_version || "?"}</small>
                </div>
                <div className="v2-review-actions">
                  {status !== "KEEP_WATCHING" && (
                    <button type="button" onClick={() => onDecision(item.review_id, "KEEP_WATCHING")} disabled={localActionDisabled}>
                      保持追蹤
                    </button>
                  )}
                  {status !== "RESOLVED" && (
                    <button type="button" onClick={() => onDecision(item.review_id, "RESOLVED")} disabled={localActionDisabled}>
                      標記已處理
                    </button>
                  )}
                  {status !== "DISMISSED" && (
                    <button type="button" onClick={() => onDecision(item.review_id, "DISMISSED")} disabled={localActionDisabled}>
                      暫時忽略
                    </button>
                  )}
                  {status !== "OPEN" && (
                    <button type="button" onClick={() => onDecision(item.review_id, "OPEN")} disabled={localActionDisabled}>
                      重新開啟
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      ) : <span>目前沒有需要人工覆核的契約漂移。</span>}
      <div className="v2-review-footer">
        <small>合併、拆分、修正 mapping 仍須由 canonical writer 執行；本頁不提供假寫入。</small>
        <button type="button" onClick={onExport} disabled={!hasLocalItems}>
          匯出本機覆核紀錄
        </button>
      </div>
      {notice && <small role="status">{notice}</small>}
      {error && <small className="error" role="alert">{error}</small>}
    </section>
  );
}

function SystemHealthSummary({ health, localReview, onDecision, onExport, notice, error }) {
  if (!health || !health.lanes || !Array.isArray(health.stages)) return null;
  return (
    <details className="v2-system-health" data-testid="v2-system-health">
      <summary>端到端系統健康：{health.overall}</summary>
      <div className="v2-system-health-content">
        <div className="v2-health-lanes" aria-label="系統健康 lanes">
          {Object.entries(health.lanes).map(([lane, status]) => (
            <div key={lane} className="v2-health-lane">
              <strong>{lane}</strong>
              <span>{status}</span>
            </div>
          ))}
        </div>
        <ul className="v2-health-stage-list">
          {health.stages.map((stage) => (
            <li key={`${stage.lane}-${stage.stage}`}>
              <span>{stage.lane} / {stage.stage}</span>
              <strong>{stage.outcome}</strong>
              <small>{stage.error_class || `最後觀測：${formatDateTime(stage.ended_at || stage.last_success_at)}`}</small>
            </li>
          ))}
        </ul>
        <ReviewInboxPanel
          health={health}
          localReview={localReview}
          onDecision={onDecision}
          onExport={onExport}
          notice={notice}
          error={error}
        />
        <p className="v2-health-note">此 receipt 只反映已保存的處理鏈證據；UNKNOWN 不會被解讀成成功。</p>
      </div>
    </details>
  );
}

function ActionCard({ item, index, tracked, onAddWatch, storageReady }) {
  const affectedRoles = Array.isArray(item.affected_roles) ? item.affected_roles : [];
  const relevanceReasons = Array.isArray(item.profile_relevance?.reason_codes)
    ? item.profile_relevance.reason_codes
    : [];
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

      {relevanceReasons.length > 0 && (
        <div className="v2-profile-relevance" data-testid="profile-relevance" aria-label="角色檔排序依據">
          <span>排序依據</span>
          {relevanceReasons.map((reason) => (
            <span key={reason} title={reason}>{RELEVANCE_LABELS[reason] || reason}</span>
          ))}
        </div>
      )}

      <div className="v2-action-footer">
        <span>
          {item.verification_status === "DETERMINISTIC_PASS"
            ? "規則驗證通過"
            : "待驗證"}
        </span>
        <div className="v2-action-buttons">
          <button
            type="button"
            className="v2-secondary-button"
            disabled={tracked || !item.identity || !Number.isInteger(item.source_version) || !storageReady}
            onClick={() => onAddWatch(item)}
            title={!item.identity || !Number.isInteger(item.source_version) ? "缺少可驗證的來源版本" : undefined}
          >
            {tracked ? "已加入追蹤" : "加入追蹤"}
          </button>
          <a href={item.official_url} target="_blank" rel="noreferrer">
            開啟官方來源
          </a>
        </div>
      </div>
    </article>
  );
}

function SourceHealthSummary({ sourceStatus, canReassure }) {
  const sources = Array.isArray(sourceStatus?.sources) ? sourceStatus.sources : [];
  if (!sources.length) return null;

  return (
    <details className="v2-source-health">
      <summary>查看 {sources.length} 個官方來源的快照健康狀態</summary>
      <div className="v2-source-health-grid">
        {sources.map((source) => (
          <article key={source.source_id}>
            <div>
              <span
                className={`v2-source-dot ${canReassure && source.source_health === "PASS" ? "pass" : "fail"}`}
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

function collectPublishedItems(publication) {
  const views = [
    ...(Array.isArray(publication?.profile_views) ? publication.profile_views : []),
    publication,
  ];
  const rows = [];
  for (const view of views) {
    for (const key of ["priority_items", "tracking_items", "other_changes"]) {
      if (Array.isArray(view?.[key])) rows.push(...view[key]);
    }
  }
  const unique = new Map();
  for (const item of rows) {
    if (item?.identity && !unique.has(item.identity)) unique.set(item.identity, item);
  }
  return [...unique.values()];
}

export default function V2DailyDashboard() {
  const [publication, setPublication] = useState(null);
  const [archive, setArchive] = useState(null);
  const [sourceStatus, setSourceStatus] = useState(null);
  const [systemHealth, setSystemHealth] = useState(null);
  const [candidate, setCandidate] = useState(null);
  const [loadState, setLoadState] = useState("loading");
  const [loadError, setLoadError] = useState("");
  const [archiveQuery, setArchiveQuery] = useState("");
  const [selectedProfileId, setSelectedProfileId] = useState("general");
  const [nowMs, setNowMs] = useState(null);
  const [localHandoff, setLocalHandoff] = useState(null);
  const [handoffNotice, setHandoffNotice] = useState("");
  const [handoffError, setHandoffError] = useState("");
  const [localReview, setLocalReview] = useState(null);
  const [reviewNotice, setReviewNotice] = useState("");
  const [reviewError, setReviewError] = useState("");

  useEffect(() => {
    const updateClock = () => setNowMs(Date.now());
    updateClock();
    const timer = setInterval(updateClock, 60_000);
    window.addEventListener("focus", updateClock);
    return () => {
      clearInterval(timer);
      window.removeEventListener("focus", updateClock);
    };
  }, []);

  useEffect(() => {
    if (!publication || loadState !== "ready") return;
    try {
      const loaded = loadLocalHandoff();
      const synced = syncLocalHandoff(
        loaded,
        collectPublishedItems(publication),
        publication,
        publication.generated_at,
      );
      saveLocalHandoff(synced);
      setLocalHandoff(synced);
      setHandoffError("");
    } catch (error) {
      setLocalHandoff(null);
      setHandoffError(error.message);
    }
  }, [publication, loadState]);

  useEffect(() => {
    if (!systemHealth || !Array.isArray(systemHealth.review_inbox)) return;
    try {
      const loaded = loadLocalReview();
      const synced = syncLocalReview(
        loaded,
        systemHealth.review_inbox,
        systemHealth.generated_at || systemHealth.observed_at || new Date(),
      );
      saveLocalReview(synced);
      setLocalReview(synced);
      setReviewError("");
    } catch (error) {
      setLocalReview(null);
      setReviewError(error.message);
    }
  }, [systemHealth]);

  const assessment = assessPublication(publication, sourceStatus, nowMs);

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
        const profileIds = Array.isArray(data.profile_views)
          ? data.profile_views.map((view) => view?.profile?.profile_id).filter(Boolean)
          : [];
        const requestedProfile = new URLSearchParams(window.location.search).get("profile");
        setSelectedProfileId(
          profileIds.includes(requestedProfile)
            ? requestedProfile
            : data.profile?.profile_id || profileIds[0] || "general",
        );
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

    fetchJson(SYSTEM_HEALTH_URL)
      .then((data) => {
        if (!cancelled && data?.schema_version === 1) setSystemHealth(data);
      })
      .catch(() => {});

    fetchJson(`${BASE_PATH}/data/candidate.json`)
      .then((data) => {
        if (!cancelled && data?.schema_version === 1 && data?.kind === "GOVINTEL_CANDIDATE_MANIFEST") {
          setCandidate(data);
        }
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

  const profileViews = Array.isArray(publication?.profile_views) ? publication.profile_views : [];
  const activeView = profileViews.find((view) => view?.profile?.profile_id === selectedProfileId) || publication || {};
  const activeProfile = activeView.profile || publication?.profile;
  const overview = { ...(publication?.overview || {}), ...(activeView.overview || {}) };
  const currentItems = useMemo(
    () => collectPublishedItems(publication),
    [publication],
  );
  const priorityItems = Array.isArray(activeView.priority_items)
    ? activeView.priority_items.slice(0, 3)
    : [];
  const serverTrackingItems = Array.isArray(activeView.tracking_items)
    ? activeView.tracking_items.slice(0, 5)
    : [];
  const localTrackingItems = localHandoff
    ? projectLocalTracking(localHandoff, currentItems)
    : [];
  const localByWatchId = new Map(localTrackingItems.map((item) => [item.watch_id, item]));
  const trackingItems = [
    ...serverTrackingItems
      .filter((item) => !localHandoff || !localHandoff.watch_items[item.watch_id]
        || ["WATCHING", "NEEDS_REVIEW"].includes(localHandoff.watch_items[item.watch_id].status))
      .map((item) => localByWatchId.get(item.watch_id) || item),
    ...localTrackingItems.filter((item) => !serverTrackingItems.some((row) => row.watch_id === item.watch_id)),
  ].slice(0, 5);
  const otherChanges = Array.isArray(activeView.other_changes)
    ? activeView.other_changes
    : [];
  const activeWatchIds = new Set(
    Object.values(localHandoff?.watch_items || {})
      .filter((watch) => ["WATCHING", "NEEDS_REVIEW"].includes(watch.status))
      .map((watch) => watch.watch_id),
  );
  const activeWatchIdentities = new Set(
    Object.values(localHandoff?.watch_items || {})
      .filter((watch) => ["WATCHING", "NEEDS_REVIEW"].includes(watch.status))
      .map((watch) => watch.identity),
  );

  const persistHandoff = (next, notice) => {
    try {
      saveLocalHandoff(next);
      setLocalHandoff(next);
      setHandoffError("");
      setHandoffNotice(notice);
    } catch (error) {
      setHandoffError(error.message);
    }
  };

  const handleAddWatch = (item) => {
    if (!localHandoff) {
      setHandoffError("本機追蹤尚未載入，未覆寫既有資料。");
      return;
    }
    try {
      persistHandoff(
        addLocalWatch(localHandoff, item),
        `已加入本機追蹤：${item.headline}`,
      );
    } catch (error) {
      setHandoffError(error.message);
    }
  };

  const handleConfirmHandoff = () => {
    try {
      const next = confirmLocalHandoff(localHandoff || emptyLocalHandoff(), currentItems, publication);
      persistHandoff(next, `已確認本機交班版本 v${latestHandoff(next).brief_version}。`);
    } catch (error) {
      setHandoffError(error.message);
    }
  };

  const handleWatchStatus = (watchId, status) => {
    try {
      persistHandoff(
        setLocalWatchStatus(localHandoff || emptyLocalHandoff(), watchId, status),
        status === "RESOLVED" ? "已標記為已處理。" : "已標記為不再追蹤。",
      );
    } catch (error) {
      setHandoffError(error.message);
    }
  };

  const handleExport = (format) => {
    try {
      const artifact = exportLocalHandoff(localHandoff || emptyLocalHandoff(), format);
      const url = URL.createObjectURL(new Blob([artifact.content], { type: artifact.mime }));
      const link = document.createElement("a");
      link.href = url;
      link.download = artifact.filename;
      link.click();
      URL.revokeObjectURL(url);
      setHandoffNotice(`已匯出本機交班版本：${artifact.filename}`);
      setHandoffError("");
    } catch (error) {
      setHandoffError(error.message);
    }
  };

  const handleReviewDecision = (reviewId, decision) => {
    if (!localReview) {
      setReviewError("本機覆核尚未載入，未覆寫既有資料。");
      return;
    }
    try {
      const next = setLocalReviewDecision(localReview, reviewId, decision);
      saveLocalReview(next);
      setLocalReview(next);
      setReviewNotice(`已在本機標記：${REVIEW_STATUS_LABELS[decision] || decision}。`);
      setReviewError("");
    } catch (error) {
      setReviewError(error.message);
    }
  };

  const handleReviewExport = () => {
    try {
      const artifact = exportLocalReview(localReview || emptyLocalReview(), "markdown");
      const url = URL.createObjectURL(new Blob([artifact.content], { type: artifact.mime }));
      const link = document.createElement("a");
      link.href = url;
      link.download = artifact.filename;
      link.click();
      URL.revokeObjectURL(url);
      setReviewNotice(`已匯出本機覆核紀錄：${artifact.filename}`);
      setReviewError("");
    } catch (error) {
      setReviewError(error.message);
    }
  };
  const generationMixed = Boolean(
    publication && archive && sourceStatus
      && !samePublicationGeneration(publication, archive, sourceStatus),
  );

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
            <span>快照產生時間（非官方發布時間）</span>
            <strong>{formatDateTime(publication.generated_at)}</strong>
            <small>Asia/Taipei</small>
            {profileViews.length > 1 && (
              <label className="v2-profile-selector" htmlFor="v2-profile-select">
                <span>工作角色</span>
                <select
                  id="v2-profile-select"
                  data-testid="role-profile-selector"
                  value={activeProfile?.profile_id || selectedProfileId}
                  onChange={(event) => {
                    const nextProfile = event.target.value;
                    setSelectedProfileId(nextProfile);
                    const url = new URL(window.location.href);
                    url.searchParams.set("profile", nextProfile);
                    window.history.replaceState({}, "", url);
                  }}
                >
                  {profileViews.map((view) => (
                    <option key={view.profile.profile_id} value={view.profile.profile_id}>
                      {view.profile.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        )}
      </header>

      <QueryGatewayPanel />

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
          {generationMixed && (
            <section className="v2-system-message error" role="alert" data-testid="generation-mixed">
              <strong>資料世代不一致，已拒絕混版呈現</strong>
              <p>簡報、歷史資料庫與來源狀態不是同一批發布；本期彙整已停止，歷史索引仍可單獨查閱。</p>
            </section>
          )}
          {!generationMixed && (
            <>
          <PublicationStatus publication={publication} assessment={assessment} />
          <section className={`v2-system-message ${assessment.canReassure ? "" : "error"}`}
            role={assessment.canReassure ? "status" : "alert"} data-testid="publication-freshness">
            <strong>{assessment.reason}</strong>
            <p>依裝置時間估算快照年齡，每分鐘及返回視窗時重新判斷；此頁不會自動執行新的官方蒐集。</p>
            <button type="button" onClick={() => window.location.reload()}>重新載入資料</button>
          </section>

          <section className="v2-metrics" aria-label="本期情報摘要">
            <Metric value={overview.current_change_count || 0} label="快照記錄變更" emphasis />
            <Metric value={overview.priority_count || 0} label="快照重點" />
            <Metric value={overview.tracking_count || 0} label="持續追蹤" />
            <Metric value={overview.archive_total || 0} label="歷史資料" />
          </section>

          <section className="v2-priority-section" aria-labelledby="v2-priority-title">
            <div className="v2-section-heading">
              <div>
                <p className="v2-eyebrow">10 秒掌握</p>
                <h2 id="v2-priority-title">快照重點</h2>
              </div>
              <span>{activeProfile?.label || "綜合視圖"} · 最多 3 件</span>
            </div>

            {priorityItems.length === 0 ? (
              <div className="v2-empty-priority" data-testid="v2-empty-priority" role="status">
                <strong>{assessment.canReassure
                  ? "本期沒有需要處理的重要變更（僅限此快照範圍）"
                  : "快照未列出重點，但目前是否有新異動仍待確認"}</strong>
                {assessment.canReassure && <p>{publication.status_message}</p>}
                <p>
                  此快照曾檢查 {publication.source_health?.pass_count || 0} 個正常官方來源；
                  {overview.archive_total || 0} 筆既有資料保留於歷史區，不列為今日情資。
                </p>
              </div>
            ) : (
              <div className="v2-action-list">
                {priorityItems.map((item, index) => (
                  <ActionCard
                    key={item.event_id}
                    item={item}
                    index={index}
                    tracked={activeWatchIds.has(item.watch_id) || activeWatchIdentities.has(item.identity)}
                    onAddWatch={handleAddWatch}
                    storageReady={Boolean(localHandoff)}
                  />
                ))}
              </div>
            )}
          </section>

          {(trackingItems.length > 0 || localHandoff) && (
            <section className="v2-tracking-section" aria-labelledby="v2-tracking-title">
              <div className="v2-section-heading">
                <div>
                  <p className="v2-eyebrow">近期節點</p>
                  <h2 id="v2-tracking-title">持續追蹤</h2>
                </div>
                <span>最多 5 件</span>
              </div>
              <div className="v2-handoff-toolbar" data-testid="local-handoff-toolbar">
                <span>本機保存 · 不會寫入公開網站</span>
                <div>
                  <button type="button" onClick={handleConfirmHandoff} disabled={!localTrackingItems.length}>
                    確認／更新交班版本
                  </button>
                  <button type="button" onClick={() => handleExport("markdown")} disabled={!localHandoff?.handoffs?.length}>
                    匯出 Markdown
                  </button>
                  <button type="button" onClick={() => handleExport("json")} disabled={!localHandoff?.handoffs?.length}>
                    匯出 JSON
                  </button>
                </div>
                {handoffNotice && <small role="status">{handoffNotice}</small>}
                {handoffError && <small className="error" role="alert">{handoffError}</small>}
              </div>
              <div className="v2-tracking-list" data-testid="v2-tracking-list">
                {trackingItems.length > 0 ? (
                  trackingItems.map((item) => (
                  <article key={item.tracking_id || item.watch_id || item.event_id}>
                    <a href={item.official_url} target="_blank" rel="noreferrer">
                      <strong>{item.headline}</strong>
                      <span>
                        {item.watch_status === "NEEDS_REVIEW" ? "需重新核對" : "持續追蹤"}
                        {item.source_health && item.source_health !== "PASS" ? ` · 來源 ${item.source_health}` : ""}
                      </span>
                      <small>{item.recommended_action}</small>
                    </a>
                    {item.watch_id && activeWatchIds.has(item.watch_id) && (
                      <div className="v2-tracking-actions">
                        <button type="button" onClick={() => handleWatchStatus(item.watch_id, "RESOLVED")}>標記已處理</button>
                        <button type="button" onClick={() => handleWatchStatus(item.watch_id, "DISMISSED")}>停止追蹤</button>
                      </div>
                    )}
                  </article>
                  ))
                ) : <p className="v2-handoff-empty">尚未加入本機追蹤項目；可從快照重點加入。</p>}
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

            </>
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
              <p className="v2-archive-unavailable">歷史索引暫時無法載入；已載入的 V2 快照須依上方時效與缺口提示使用。</p>
            )}
          </section>

          <SystemHealthSummary
            health={systemHealth}
            localReview={localReview}
            onDecision={handleReviewDecision}
            onExport={handleReviewExport}
            notice={reviewNotice}
            error={reviewError}
          />
          {!generationMixed && <SourceHealthSummary sourceStatus={sourceStatus} canReassure={assessment.canReassure} />}
          <PublicationProvenance
            publication={publication}
            archive={archive}
            sourceStatus={sourceStatus}
            candidate={candidate}
            generationMixed={generationMixed}
          />
        </>
      )}
    </main>
  );
}
