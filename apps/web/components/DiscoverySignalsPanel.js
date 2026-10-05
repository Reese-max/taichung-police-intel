"use client";

import { useEffect, useState } from "react";
import { isValidDiscoverySignals } from "../lib/discovery-signals.js";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const DATA_URL = `${BASE_PATH}/data/discovery-signals.json`;

const UPSTREAM_MODE_NOTES = {
  HISTORICAL_REPLAY_ONLY: "上游暫停：本區僅為歷史重播，不作為目前情資訊號。",
  CANARY_ONLY: "上游還原中：僅為 canary 觀測，未列為正式訊號。",
  GAP_VISIBLE: "上游降級：候選保留，但資料涵蓋可能有缺口。",
};

const PENDING_STATUS_LABELS = {
  DISCOVERY_UNVERIFIED: "未完成官方比對",
  OFFICIAL_CANDIDATE: "官方候選",
  NO_OFFICIAL_MATCH: "待官方確認",
  CONFLICT: "來源衝突",
};

// Pending rows never reuse wording that would read as official confirmation.
const PENDING_NOTES = {
  DISCOVERY_UNVERIFIED: "尚未完成官方比對",
  OFFICIAL_CANDIDATE: "官方候選，待回查原始來源",
  NO_OFFICIAL_MATCH: "尚未找到官方確認",
  CONFLICT: "官方來源不一致，待人工判定",
};

const AUTHORITY_LABELS = {
  media: "媒體線索",
  official: "官方訊號",
};

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

function ConfirmedRow({ row }) {
  return (
    <article className="v2-discovery-row">
      <strong>{row.headline}</strong>
      <small>
        {AUTHORITY_LABELS[row.authority] || row.authority} · {row.publisher || "未知發佈者"} · {formatDateTime(row.event_time)}
      </small>
      <div className="v2-discovery-evidence">
        官方文件版本：{row.official_document_versions.join("、")}
      </div>
      {row.public_event_ids.length > 0 && (
        <small>
          關聯事件：{row.public_event_ids.join("、")}{row.matched_existing ? "（既有事件）" : "（新建事件）"}
        </small>
      )}
      {row.official_urls.length > 0 && (
        <a href={row.official_urls[0]} target="_blank" rel="noreferrer">開啟官方證據</a>
      )}
    </article>
  );
}

function PendingRow({ row }) {
  return (
    <article className="v2-discovery-row">
      <strong>{row.headline}</strong>
      <small>
        {PENDING_STATUS_LABELS[row.verification_status] || row.verification_status}
        {" · "}{AUTHORITY_LABELS[row.authority] || row.authority}
        {" · "}{row.publisher || "未知發佈者"}
        {" · "}發現於 {formatDateTime(row.discovered_at)}
      </small>
      <p>{PENDING_NOTES[row.verification_status] || row.note}</p>
      <small>候選保存至 {formatDateTime(row.expires_at)}</small>
    </article>
  );
}

export default function DiscoverySignalsPanel() {
  const [payload, setPayload] = useState(null);
  const [loadState, setLoadState] = useState("loading");
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let cancelled = false;
    fetch(DATA_URL, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (cancelled) return;
        if (!isValidDiscoverySignals(data)) {
          throw new Error("Discovery 訊號格式不相容，已停止呈現。");
        }
        setPayload(data);
        setLoadState("ready");
      })
      .catch((error) => {
        if (!cancelled) {
          setLoadState("failed");
          setLoadError(error.message);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (loadState === "loading") {
    return <section className="v2-discovery" data-testid="discovery-signals"><p>正在載入外部事件發現層……</p></section>;
  }
  if (loadState !== "ready") {
    return (
      <section className="v2-discovery error" data-testid="discovery-signals" role="alert">
        <strong>外部發現層暫時無法安全呈現</strong>
        <p>{loadError}</p>
      </section>
    );
  }

  const upstreamNote = UPSTREAM_MODE_NOTES[payload.upstream.signal_mode];
  const counts = payload.counts;

  return (
    <section className="v2-discovery" data-testid="discovery-signals" aria-labelledby="discovery-signals-title">
      <div className="v2-section-heading">
        <div>
          <p className="v2-eyebrow">外部事件發現層 · Taiwan Intel Dashboard</p>
          <h2 id="discovery-signals-title">Discovery 雷達（唯讀）</h2>
        </div>
        <span className="v2-demo-badge">
          {payload.status === "FIXTURE_ONLY" ? "FIXTURE_ONLY · 未接 production" : "唯讀 · 不寫入正式事件"}
        </span>
      </div>
      <p className="v2-fusion-note">
        媒體與外部訊號僅作為發現線索；回到原始官方文件驗證後才列入「已確認」。此區不寫入正式事件，也不進交班或快照重點。
      </p>
      {upstreamNote && (
        <p className="v2-discovery-upstream" role="status" data-testid="discovery-upstream-state">{upstreamNote}</p>
      )}

      <div className="v2-discovery-grid">
        <section className="v2-discovery-confirmed" aria-label="已確認">
          <h3>已確認（{counts.confirmed_count}）</h3>
          {payload.confirmed.length > 0 ? (
            payload.confirmed.map((row) => <ConfirmedRow key={row.candidate_id} row={row} />)
          ) : (
            <p>本期沒有已確認的外部發現。</p>
          )}
        </section>

        <section className="v2-discovery-pending" aria-label="待官方確認">
          <h3>待官方確認（{counts.pending_total}）</h3>
          {payload.pending.length > 0 ? (
            payload.pending.map((row) => <PendingRow key={row.candidate_id} row={row} />)
          ) : (
            <p>目前沒有待官方確認的候選。</p>
          )}
          {counts.pending_total > payload.pending.length && (
            <small>另有 {counts.pending_total - payload.pending.length} 筆候選於 TTL 內保存，未在此顯示。</small>
          )}
        </section>
      </div>

      <p className="v2-discovery-counts">
        本輪餵入 {counts.feed_item_count} 筆 · 通過相關性 {counts.relevant_count} · 官方文件確認 {counts.confirmed_count} ·
        待確認 {counts.pending_total} · 來源衝突 {counts.conflict_count} · 已過期 {counts.expired_count}
      </p>
    </section>
  );
}
