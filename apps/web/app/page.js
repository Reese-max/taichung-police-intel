"use client";

import Hls from "hls.js";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { findActiveIndex, formatTime, groupWordsBySegment, validateEvidence } from "../lib/evidence.js";
import { validateCouncilDrawerPayload } from "../lib/council-prep.js";
import {
  COPY,
  PRIORITY_ITEM,
  CENTRAL_POLICY_CARDS,
  SOURCE_NAMES_EN,
  formatPlaybackStatus,
  limitCentralPolicyCards,
  requiresOfficialFallback,
} from "../lib/homepage-data.js";
import { buildHomepageResponse } from "../lib/homepage-eligibility.js";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const DATA_URL = `${BASE_PATH}/data/groq-asr-canary-2026-08-14.json`;
const CER_URL = `${BASE_PATH}/data/groq-asr-cer-2026-08-14.json`;
const REFERENCE_URL = `${BASE_PATH}/data/groq-asr-reference-2026-08-14.json`;
const FEED_URL = `${BASE_PATH}/data/intelligence-feed.json`;
const SUMMARY_URL = `${BASE_PATH}/data/intelligence-summary.json`;
const HLS_LOAD_TIMEOUT_MS = 10_000;

// Static fallback: council evidence journey PRIORITY_ITEM always available
const FALLBACK_RESPONSE = buildHomepageResponse([PRIORITY_ITEM]);

/**
 * Project feed items into homepage candidates compatible with buildHomepageResponse().
 * Only HOME_CANDIDATE items are projected.
 * NOTE: This is the client-side version of feed-projection.js (which uses node:fs
 * and cannot be imported in a "use client" component). Logic must stay in sync.
 */
function projectFeedToHomepageCandidates(feed) {
  if (!feed || !Array.isArray(feed.items)) return [];
  return feed.items
    .filter((item) => item.eligibility === "HOME_CANDIDATE")
    .map((item) => ({
      item_id: item.stable_id,
      stable_id: item.stable_id,
      verification_status: "AUTO_PASS",
      content_disposition: "HOME_CANDIDATE",
      evidence_ids: [item.stable_id],
      official_url: item.official_url,
      reason_codes: item.reason_codes || ["HIGH_VALUE"],
      item_value_score: item.item_value_score || 50,
      source_id: item.source_id,
      source_role: item.source_role,
      title: item.title,
      title_zh: item.title_zh || item.title,
      published_at: item.published_at,
      fetched_at: item.fetched_at,
      data_as_of: item.data_as_of,
      change_type: item.change_type,
      freshness_status: item.freshness_status,
      source_health: item.source_health,
      window_completeness: item.window_completeness,
      evidence_count: item.evidence_count || 1,
      next_milestone: item.next_milestone || null,
      content_sha256: item.content_sha256,
    }));
}

export default function Home() {
  // ── Feed state ──────────────────────────────────────────────────────────────
  const [feedResponse, setFeedResponse] = useState(FALLBACK_RESPONSE);
  const [feedLoaded, setFeedLoaded] = useState(false);
  const [feedMeta, setFeedMeta] = useState(null);

  // Load intelligence feed at mount time
  useEffect(() => {
    fetch(FEED_URL, { cache: "no-store" })
      .then((res) => res.ok ? res.json() : null)
      .then((feed) => {
        if (!feed || feed.schema_version !== 1) {
          setFeedLoaded(true);
          return;
        }
        setFeedMeta(feed);
        const candidates = projectFeedToHomepageCandidates(feed);
        if (candidates.length > 0) {
          // Live response uses only feed candidates — PRIORITY_ITEM is NOT mixed in
          // to avoid occupying the 10-item limit or altering deterministic sort order.
          setFeedResponse(buildHomepageResponse(candidates));
        }
        // If no feed candidates qualify, keep FALLBACK_RESPONSE (PRIORITY_ITEM)
        setFeedLoaded(true);
      })
      .catch(() => setFeedLoaded(true));
  }, []);

  // The council evidence journey item — always available for the drawer
  const priorityItem = FALLBACK_RESPONSE.items[0] || null;
  // Feed-sourced items for the main homepage display (may be empty)
  const feedItems = feedResponse.items;

  // ── Intelligence summary state ──────────────────────────────────────────
  const [summaryData, setSummaryData] = useState(null);

  useEffect(() => {
    fetch(SUMMARY_URL, { cache: "no-store" })
      .then((res) => res.ok ? res.json() : null)
      .then((data) => {
        if (data && data.schema_version === 1) setSummaryData(data);
      })
      .catch(() => {});
  }, []);

  // ── Language toggle ──────────────────────────────────────────────────────
  const [lang, setLang] = useState("zh");
  const t = COPY[lang];

  // Update the document language attribute when lang changes.
  useEffect(() => {
    document.documentElement.lang = lang === "en" ? "en" : "zh-Hant-TW";
  }, [lang]);

  // ── Evidence drawer state ────────────────────────────────────────────────
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [data, setData] = useState(null);
  const [cer, setCer] = useState(null);
  const [error, setError] = useState("");
  const [sourceStatus, setSourceStatus] = useState(null);
  const [query, setQuery] = useState("");
  const [activeSegment, setActiveSegment] = useState(0);
  const [expandedSegment, setExpandedSegment] = useState(0);
  const [activeWord, setActiveWord] = useState(-1);
  const [currentSeconds, setCurrentSeconds] = useState(Number(priorityItem?.clip_start_seconds || 0));
  const [followPlayback, setFollowPlayback] = useState(true);
  const [playbackStatus, setPlaybackStatus] = useState({ kind: "loading" });
  const videoRef = useRef(null);
  const segmentRefs = useRef([]);
  const pendingSeekRef = useRef(null);

  useEffect(() => {
    Promise.all([
      fetch(DATA_URL).then((response) => {
        if (!response.ok) throw new Error(`ASR JSON HTTP ${response.status}`);
        return response.json();
      }),
      fetch(CER_URL).then((response) => {
        if (!response.ok) throw new Error(`CER JSON HTTP ${response.status}`);
        return response.json();
      }),
    ])
      .then(([payload, cerReport]) => {
        const validatedPayload = validateEvidence(payload);
        const councilResult = validateCouncilDrawerPayload(validatedPayload);
        if (!councilResult.valid) {
          throw new Error(`Council evidence contract ${councilResult.reason}.`);
        }
        setData(validatedPayload);
        setCer(cerReport);
        setCurrentSeconds(Number(priorityItem?.clip_start_seconds || 0));
      })
      .catch((reason) => setError(reason.message));
  }, [priorityItem]);

  useEffect(() => {
    fetch(`${BASE_PATH}/api/status.json`, { cache: "no-store" })
      .then((response) => response.ok ? response.json() : null)
      .then(setSourceStatus)
      .catch(() => setSourceStatus(null));
  }, []);

  useEffect(() => {
    if (!drawerOpen || !data || !videoRef.current) return undefined;
    const video = videoRef.current;
    const clipStart = Number(priorityItem?.clip_start_seconds || 0);
    let hls;
    let failed = false;
    let loadTimer;
    setPlaybackStatus({ kind: "loading" });

    const failPlayback = () => {
      if (failed) return;
      failed = true;
      window.clearTimeout(loadTimer);
      pendingSeekRef.current = null;
      hls?.destroy();
      hls = undefined;
      video.removeAttribute("src");
      video.load();
      setPlaybackStatus({ kind: "hls_error" });
    };
    const ready = () => {
      if (failed) return;
      window.clearTimeout(loadTimer);
      const target = pendingSeekRef.current ?? clipStart;
      video.currentTime = target;
      setPlaybackStatus(
        pendingSeekRef.current === null
          ? { kind: "ready" }
          : { kind: "seeked", timestamp: formatTime(target, true) },
      );
      pendingSeekRef.current = null;
    };
    video.addEventListener("loadedmetadata", ready);
    video.addEventListener("error", failPlayback);

    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = data.source.media_url;
      loadTimer = window.setTimeout(failPlayback, HLS_LOAD_TIMEOUT_MS);
    } else if (Hls.isSupported()) {
      hls = new Hls({ enableWorker: true, lowLatencyMode: false });
      hls.loadSource(data.source.media_url);
      hls.attachMedia(video);
      hls.on(Hls.Events.ERROR, (_, details) => {
        if (details?.fatal) failPlayback();
      });
      loadTimer = window.setTimeout(failPlayback, HLS_LOAD_TIMEOUT_MS);
    } else {
      setPlaybackStatus({ kind: "hls_unsupported" });
    }

    return () => {
      window.clearTimeout(loadTimer);
      video.removeEventListener("loadedmetadata", ready);
      video.removeEventListener("error", failPlayback);
      hls?.destroy();
    };
  }, [data, drawerOpen, priorityItem]);

  const clipStart = Number(priorityItem?.clip_start_seconds || 0);
  const clipDuration = Number(priorityItem?.clip_duration_seconds || 0);
  const segments = data?.asr?.segments || [];
  const words = data?.asr?.words || [];
  const wordsBySegment = useMemo(() => groupWordsBySegment(segments, words), [segments, words]);
  const visibleSegments = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase("zh-Hant");
    return segments.map((segment, index) => ({ segment, index })).filter(({ segment }) =>
      !needle || String(segment.text || "").toLocaleLowerCase("zh-Hant").includes(needle),
    );
  }, [query, segments]);

  useEffect(() => {
    if (!followPlayback || activeSegment < 0) return;
    const row = segmentRefs.current[activeSegment];
    row?.scrollIntoView({ block: "nearest", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }, [activeSegment, followPlayback]);

  const seek = useCallback((relativeSeconds, autoplay = true, segmentIndex = null) => {
    const video = videoRef.current;
    if (!video || !data) return;
    const absolute = clipStart + Number(relativeSeconds);
    setCurrentSeconds(absolute);
    if (segmentIndex !== null) {
      setActiveSegment(segmentIndex);
      setExpandedSegment(segmentIndex);
    }
    if (requiresOfficialFallback(playbackStatus)) return;
    if (video.readyState === 0) pendingSeekRef.current = absolute;
    else video.currentTime = absolute;
    setPlaybackStatus({ kind: "seeked", timestamp: formatTime(absolute, true) });
    if (autoplay) {
      video.play().catch(() =>
        setPlaybackStatus({ kind: "autoplay_blocked", timestamp: formatTime(absolute, true) }),
      );
    }
  }, [clipStart, data, playbackStatus]);

  const updatePlayback = () => {
    if (!data || !videoRef.current) return;
    const absolute = Number(videoRef.current.currentTime);
    const relative = absolute - clipStart;
    const segmentIndex = findActiveIndex(segments, relative);
    setCurrentSeconds(absolute);
    setActiveWord(findActiveIndex(words, relative, 0.01));
    if (segmentIndex >= 0) {
      setActiveSegment(segmentIndex);
      if (followPlayback) setExpandedSegment(segmentIndex);
    }
  };

  const chooseTerm = (term) => {
    setQuery(term);
    const index = segments.findIndex((segment) => String(segment.text || "").includes(term));
    if (index >= 0) seek(segments[index].start, false, index);
  };

  // R4: enforced via limitCentralPolicyCards
  const visiblePolicyCards = limitCentralPolicyCards(CENTRAL_POLICY_CARDS);

  return (
    <div className={`app-frame ${drawerOpen ? "drawer-open" : ""}`}>
      <main className="workspace">

        {/* ── Language toggle ────────────────────────────────────────────── */}
        <div className="lang-toggle-row">
          <button
            type="button"
            className="lang-toggle"
            aria-pressed={lang === "en"}
            onClick={() => setLang((l) => (l === "en" ? "zh" : "en"))}
          >
            {t.lang_toggle_label}
          </button>
          <span className="lang-current" aria-hidden="true">{t.lang_toggle_current}</span>
        </div>

        {/* ── Site header ────────────────────────────────────────────────── */}
        <header className="site-header">
          <div>
            <p className="kicker">{t.site_kicker}</p>
            <h1>{t.site_title}</h1>
          </div>
          <span className="freshness">
            {sourceStatus?.generated_at
              ? `${t.freshness_live}${new Date(sourceStatus.generated_at).toLocaleString(lang === "en" ? "en-GB" : "zh-TW")}`
              : t.freshness_static}
          </span>
        </header>

        {/* ── Intelligence summary card ─────────────────────────────────── */}
        {summaryData && (
          <section className="summary-card" aria-labelledby="summary-title" data-testid="summary-card">
            <p className="section-label">{t.summary_heading}</p>
            <h2 id="summary-title">{t.summary_heading}</h2>
            <div className="summary-stats">
              <span className="summary-stat">
                <strong>{summaryData.total_items}</strong> {lang === "en" ? "items monitored" : "筆監測"}
              </span>
              <span className="summary-stat">
                <strong>{summaryData.eligible_items}</strong> {lang === "en" ? "eligible" : "筆符合條件"}
              </span>
            </div>
            <div className="summary-sources">
              {summaryData.source_breakdown.map((src) => (
                <span key={src.source_id} className="source-badge" data-source-id={src.source_id}>
                  {src.source_id} · {src.item_count}
                </span>
              ))}
            </div>
            <p className="summary-brief">
              {lang === "en" ? summaryData.daily_brief_en : summaryData.daily_brief}
            </p>
            {summaryData.key_topics.length > 0 && (
              <div className="summary-topics">
                <span className="summary-topics-label">{t.summary_topics}:</span>
                {summaryData.key_topics.slice(0, 3).map((topic) => (
                  <span key={topic.topic} className="topic-badge">
                    {topic.topic} ({topic.item_count})
                  </span>
                ))}
              </div>
            )}
          </section>
        )}

        {/* ── Judge quick guide (always English per spec) ────────────────── */}
        <section
          className="judge-guide"
          aria-labelledby="judge-guide-title"
          lang="en"
          data-testid="judge-guide-english"
        >
          <p className="section-label">{t.guide_label}</p>
          <h2 id="judge-guide-title">{t.guide_heading}</h2>
          <p>{t.guide_body}</p>
          <ol>
            <li>{t.guide_step1}</li>
            <li>{t.guide_step2}</li>
            <li>{t.guide_step3}</li>
          </ol>
          {/* Evidence provenance — same IDs and URLs for both language paths */}
          <p className="evidence-note">
            Evidence:{" "}
            <a
              href={priorityItem?.official_page_url}
              target="_blank"
              rel="noreferrer"
              data-evidence-id={priorityItem?.evidence_source_id}
            >
              Official video ({priorityItem?.evidence_source_id})
            </a>
            {" · "}
            <a href={priorityItem?.meeting_records_url} target="_blank" rel="noreferrer">
              Meeting records
            </a>
            {" · "}
            <span className="limitation-label">
              ORAL_OFFICIAL · UNVERIFIED_AFTER_MEETING · Navigation text is GROQ_ASR; formal
              citation returns to official source.
            </span>
          </p>
        </section>

        {/* ── Priority brief ─────────────────────────────────────────────── */}
        <section className="brief-intro" aria-labelledby="brief-title">
          <p className="section-label">{t.brief_label}</p>
          <h2 id="brief-title">{t.brief_heading}</h2>
          <p>{t.brief_body}</p>
        </section>

        {/* ── Priority card: only shown BEFORE feed loads (loading fallback) ── */}
        {!feedLoaded && priorityItem && (
          <article
            className="priority-card"
            data-testid="priority-card"
            data-evidence-id={priorityItem.evidence_source_id}
            data-official-url={priorityItem.official_page_url}
          >
            <div className="priority-meta">
              <span className="tag synthesis">{t.card_tag}</span>
              <span>{t.card_session}</span>
              <span>{priorityItem.session_date}</span>
            </div>
            <h3>{t.card_prep_heading}</h3>
            <ul>
              <li>{t.card_point1}</li>
              <li>{t.card_point2}</li>
              <li>{t.card_point3}</li>
            </ul>
            <div className="card-actions">
              <button
                className="primary-action"
                type="button"
                aria-expanded={drawerOpen}
                aria-controls="evidence-drawer"
                onClick={() => setDrawerOpen(true)}
              >
                {t.card_action_drawer}
              </button>
              <a href={priorityItem.meeting_records_url} target="_blank" rel="noreferrer">
                {t.card_action_records}
              </a>
            </div>
          </article>
        )}

        {/* ── Interpretation / limitation note ───────────────────────────── */}
        <section className="workflow-note">
          <strong>{t.workflow_heading}</strong>
          <p>{t.workflow_body}</p>
        </section>

        {/* ── Live intelligence feed items ───────────────────────────────── */}
        {feedLoaded && (
          <section
            className="feed-section"
            aria-labelledby="feed-section-title"
            data-testid="feed-section"
          >
            <p className="section-label">{lang === "en" ? "Live intelligence feed" : "即時情報動態"}</p>
            <h2 id="feed-section-title">
              {lang === "en" ? "Official source updates" : "官方來源更新"}
            </h2>
            {feedItems.filter((item) => item.stable_id && item.stable_id !== priorityItem?.item_id).length === 0 ? (
              <div className="feed-empty-state" data-testid="feed-empty-state" role="status">
                <p>
                  {lang === "en"
                    ? "No publishable intelligence items this period."
                    : "本時段無可發布重點情報。"}
                </p>
                <p className="feed-empty-reason">
                  {lang === "en"
                    ? "All source items are either stale, missing publication dates, or unchanged. Source health is shown below."
                    : "所有來源項目均為過時、缺少發布日期，或無變更。來源健康狀態如下。"}
                </p>
                {feedMeta?.source_summary && (
                  <ul className="feed-source-status">
                    {Object.entries(feedMeta.source_summary).map(([sid, info]) => (
                      <li key={sid}>
                        <strong>{sid}</strong>: {info.health} · {info.freshness} · {info.item_count} {lang === "en" ? "items" : "筆"}
                      </li>
                    ))}
                  </ul>
                )}
                {/* Evidence-demo fallback: clearly labelled, not posing as live priority */}
                {priorityItem && (
                  <div className="evidence-demo-fallback" data-testid="evidence-demo-fallback">
                    <p className="evidence-demo-label">
                      {lang === "en"
                        ? "Historical council evidence demo (not live):"
                        : "議會證據示範（非即時）："}
                    </p>
                    <button
                      className="secondary-action"
                      type="button"
                      aria-expanded={drawerOpen}
                      aria-controls="evidence-drawer"
                      onClick={() => setDrawerOpen(true)}
                    >
                      {lang === "en" ? "Open council evidence demo" : "開啟議會證據示範"}
                    </button>
                  </div>
                )}
              </div>
            ) : (
              <div className="feed-items" data-testid="feed-items">
                {feedItems
                  .filter((item) => item.stable_id && item.stable_id !== priorityItem?.item_id)
                  .map((item) => (
                    <article
                      key={item.stable_id || item.item_id}
                      className="feed-card"
                      data-testid="feed-card"
                      data-source-id={item.source_id}
                    >
                      <div className="feed-card-meta">
                        <span className="tag">{item.source_id}</span>
                        <span className={`freshness-badge ${(item.freshness_status || "").toLowerCase()}`}>
                          {item.freshness_status}
                        </span>
                        {item.change_type === "NEW" && <span className="tag new">NEW</span>}
                      </div>
                      <h3>{item.title_zh || item.title || (lang === "en" ? "(untitled)" : "（無標題）")}</h3>
                      <p className="feed-card-detail">
                        {item.published_at
                          ? `${lang === "en" ? "Published" : "發布"}：${new Date(item.published_at).toLocaleDateString(lang === "en" ? "en-GB" : "zh-TW")}`
                          : (lang === "en" ? "No publication date" : "無發布日期")}
                        {" · "}
                        {lang === "en" ? "Health" : "來源"}：{item.source_health}
                      </p>
                      <a href={item.official_url} target="_blank" rel="noreferrer">
                        {lang === "en" ? "Official source" : "回到官方來源"} ↗
                      </a>
                    </article>
                  ))}
              </div>
            )}
          </section>
        )}

        {/* ── Central-policy section (R4: at most CENTRAL_POLICY_LIMIT) ──── */}
        {visiblePolicyCards.length > 0 && (
          <section
            data-testid="central-policy-section"
            aria-label={lang === "en" ? "Central policy items" : "中央政策事項"}
          >
            {visiblePolicyCards.map((card) => (
              <article key={card.evidence_source_id} className="central-policy-card">
                <p>{card.official_stage}</p>
                {card.inferred_local_impact && (
                  <p className="local-impact">{card.inferred_local_impact}</p>
                )}
              </article>
            ))}
          </section>
        )}

        {/* ── Source monitor ─────────────────────────────────────────────── */}
        <section className="source-monitor" aria-labelledby="source-monitor-title">
          <div className="source-monitor-heading">
            <div>
              <p className="section-label">{t.source_monitor_label}</p>
              <h2 id="source-monitor-title">{t.source_monitor_heading}</h2>
            </div>
            <span>
              {sourceStatus?.next_update_at
                ? `${t.source_next_update} ${new Date(sourceStatus.next_update_at).toLocaleString(lang === "en" ? "en-GB" : "zh-TW")}`
                : t.source_snapshot_loading}
            </span>
          </div>
          <div className="data-export" data-testid="data-export">
            <span className="export-label">{t.download_heading}</span>
            <a href={`${BASE_PATH}/data/feed-export.csv`} download className="export-btn">
              {t.download_csv}
            </a>
            <a href={`${BASE_PATH}/data/intelligence-feed.json`} download className="export-btn">
              {t.download_json}
            </a>
          </div>
          <div className="source-grid">
            {(sourceStatus?.sources || []).map((source) => (
              <article className="source-card" key={source.source_id}>
                <div>
                  <strong>{source.source_id}</strong>
                  <span className={`health ${source.source_health.toLowerCase()}`}>
                    {source.source_health}
                  </span>
                </div>
                <h3>{lang === "en" ? SOURCE_NAMES_EN[source.source_id] || source.source_name : source.source_name}</h3>
                {lang === "en" && (
                  <small lang="zh-Hant">{t.source_official_name} {source.source_name}</small>
                )}
                <p>{source.result} · {source.freshness_status}</p>
                <small>
                  {t.source_data_as_of}
                  {source.data_as_of
                    ? new Date(source.data_as_of).toLocaleDateString(lang === "en" ? "en-GB" : "zh-TW")
                    : t.source_no_date}
                  {" · "}{t.source_lkg}
                  {source.last_known_good?.source_run_id || t.source_no_lkg}
                </small>
                <small>
                  {source.intelligence_gaps.length
                    ? source.intelligence_gaps.join(lang === "en" ? ", " : "、")
                    : t.source_no_gaps}
                </small>
                {source.source_url && (
                  <a href={source.source_url} target="_blank" rel="noreferrer">
                    {t.source_back_to_official}
                  </a>
                )}
              </article>
            ))}
          </div>
        </section>
      </main>

      {/* ── Evidence drawer ───────────────────────────────────────────────── */}
      {drawerOpen && (
        <aside className="evidence-drawer" id="evidence-drawer" aria-labelledby="drawer-title">
          <header className="drawer-header">
            <div>
              <p className="kicker">{t.drawer_kicker}</p>
              <h2 id="drawer-title">{t.drawer_heading}</h2>
            </div>
            <button
              className="close-button"
              type="button"
              onClick={() => setDrawerOpen(false)}
              aria-label={t.drawer_close_aria}
            >
              {t.drawer_close}
            </button>
          </header>

          {error ? (
            <div className="error-state" role="alert">
              {t.drawer_error_prefix}{error}
            </div>
          ) : !data || !cer ? (
            <div className="loading-state">{t.drawer_loading}</div>
          ) : (
            <div className="drawer-body">
              <section className="player-panel" aria-label={t.player_aria}>
                <div className="video-frame">
                  <video
                    ref={videoRef}
                    controls
                    playsInline
                    preload="metadata"
                    onTimeUpdate={updatePlayback}
                    aria-label={t.video_aria}
                  />
                </div>
                <div className="transport-row">
                  <div>
                    <strong className="current-clock">{formatTime(currentSeconds, true)}</strong>
                    <span>{formatTime(clipStart)}～{formatTime(clipStart + clipDuration)}</span>
                  </div>
                  <button type="button" onClick={() => seek(0, false, 0)}>
                    {t.transport_back}
                  </button>
                </div>
                <div className="clip-track" aria-label={lang === "en" ? "Evidence clip playback progress" : "證據片段播放進度"}>
                  <span
                    style={{
                      width: `${Math.max(0, Math.min(100, ((currentSeconds - clipStart) / clipDuration) * 100))}%`,
                    }}
                  />
                </div>
                <p className="playback-status" role="status">{formatPlaybackStatus(playbackStatus, t)}</p>
                {requiresOfficialFallback(playbackStatus) && (
                  <div className="playback-fallback" role="alert">
                    <a href={priorityItem?.official_page_url} target="_blank" rel="noreferrer">
                      {t.playback_open_official}
                    </a>
                  </div>
                )}

                <div className="provenance-grid">
                  <div>
                    <span>{t.provenance_source}</span>
                    <strong>{t.provenance_source_value}</strong>
                  </div>
                  <div>
                    <span>{t.provenance_derived}</span>
                    <strong>Groq / {data.asr.model}</strong>
                  </div>
                  <div>
                    <span>{t.provenance_timestamps}</span>
                    <strong>
                      {data.validation.segment_count} {lang === "en" ? "segs" : "段"} ·{" "}
                      {data.validation.word_count} {lang === "en" ? "words" : "字詞"}
                    </strong>
                  </div>
                  <div>
                    <span>{t.provenance_cer}</span>
                    <strong className={cer.status === "PASS" ? "pass" : "fail"}>
                      {cer.cer_percent}% ≤ {cer.threshold_percent}%
                    </strong>
                  </div>
                </div>

                <div className="source-actions">
                  <a href={priorityItem?.official_page_url} target="_blank" rel="noreferrer">
                    {t.source_action_official}
                  </a>
                  <a href={REFERENCE_URL} target="_blank" rel="noreferrer">
                    {t.source_action_reference}
                  </a>
                  <span>
                    {cer.reference.independent_human_signoff ? t.signoff_done : t.signoff_pending}
                  </span>
                </div>
              </section>

              {/* ── Transcript panel ─────────────────────────────────────── */}
              <section className="transcript-panel" aria-labelledby="transcript-title">
                <div className="transcript-toolbar">
                  <div className="toolbar-title">
                    <div>
                      <p className="section-label">{t.transcript_label}</p>
                      <h3 id="transcript-title">{t.transcript_heading}</h3>
                    </div>
                    <span>
                      {query
                        ? `${visibleSegments.length}／${segments.length}`
                        : segments.length}{" "}
                      {lang === "en" ? "segs" : "段"}
                    </span>
                  </div>
                  {/* Transcript limitation label — Chinese text is official-source navigation only */}
                  <p className="transcript-note" lang={lang === "en" ? "en" : "zh-Hant"}>
                    {t.transcript_note}
                  </p>
                  <div className="search-controls">
                    <input
                      type="search"
                      value={query}
                      onChange={(event) => setQuery(event.target.value)}
                      aria-label={t.search_aria}
                      placeholder={t.search_placeholder}
                    />
                    <button
                      type="button"
                      aria-pressed={followPlayback}
                      onClick={() => setFollowPlayback((v) => !v)}
                    >
                      {followPlayback ? t.follow_on : t.follow_off}
                    </button>
                  </div>
                  <div className="term-list" aria-label={t.terms_aria}>
                    {data.validation.detected_police_terms.map((term) => (
                      <button type="button" key={term} onClick={() => chooseTerm(term)}>
                        {term}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Chinese transcript text is preserved verbatim — official source alignment */}
                <ol className="segment-list">
                  {visibleSegments.map(({ segment, index }) => {
                    const expanded = expandedSegment === index;
                    return (
                      <li
                        className={activeSegment === index ? "segment active" : "segment"}
                        key={`${segment.start}-${index}`}
                        ref={(node) => { segmentRefs.current[index] = node; }}
                        lang="zh-Hant"
                      >
                        <button
                          className="segment-time"
                          type="button"
                          onClick={() => seek(segment.start, true, index)}
                          aria-label={`${t.segment_jump_prefix}${formatTime(clipStart + Number(segment.start), true)}`}
                        >
                          {formatTime(clipStart + Number(segment.start))}
                        </button>
                        <div className="segment-content">
                          <button
                            className="segment-copy"
                            type="button"
                            onClick={() => {
                              setExpandedSegment(index);
                              seek(segment.start, true, index);
                            }}
                            aria-expanded={expanded}
                          >
                            {segment.text}
                          </button>
                          {expanded && (
                            <div
                              className="word-rail"
                              aria-label={`${formatTime(clipStart + Number(segment.start))} ${lang === "en" ? "word timestamps" : "逐字時間點"}`}
                            >
                              {wordsBySegment[index].map((word) => (
                                <button
                                  className={activeWord === word.wordIndex ? "word active" : "word"}
                                  type="button"
                                  key={`${word.wordIndex}-${word.start}`}
                                  title={formatTime(clipStart + Number(word.start), true)}
                                  aria-label={`${t.segment_jump_prefix}${formatTime(clipStart + Number(word.start), true)}，${String(word.word).trim() || t.word_blank}`}
                                  onClick={() => seek(word.start, true, index)}
                                >
                                  {String(word.word).trim() || "·"}
                                </button>
                              ))}
                            </div>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ol>
                {!visibleSegments.length && (
                  <p className="empty-state">{t.empty_state}</p>
                )}
              </section>
            </div>
          )}
        </aside>
      )}
    </div>
  );
}
