"use client";

import { useEffect, useState } from "react";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const DATA_URL = `${BASE_PATH}/data/public-event-demo.json`;
const STATUS_LABELS = {
  CONFIRMED: "已確認",
  CANDIDATE: "待人工確認",
  CONFLICT: "來源衝突",
  SPLIT_REQUIRED: "需要拆分",
};

function statusLabel(status) {
  return STATUS_LABELS[status] || status || "未知";
}

function statusClass(status) {
  return `v2-fusion-status ${String(status || "unknown").toLowerCase()}`;
}

function dateLabel(value) {
  return value ? String(value).replace("T", " ").replace("+08:00", "") : "未提供";
}

function sourceLabel(document) {
  return `${document.source_id} · ${document.source_name}`;
}

function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isHttpsUrl(value) {
  return typeof value === "string" && value.startsWith("https://");
}

function isValidDemo(data) {
  const event = data?.current_event;
  const candidate = data?.candidate_event;
  const background = data?.background_preview;
  const documents = data?.documents;
  if (
    data?.schema_version !== 1
    || data?.kind !== "GOVINTEL_PUBLIC_EVENT_DEMO"
    || data?.status !== "FIXTURE_ONLY"
    || data?.production_verified !== false
    || !isObject(event)
    || !isObject(candidate)
    || !isObject(background)
    || !Array.isArray(documents)
    || documents.length !== 3
  ) return false;
  if (
    typeof event.public_event_id !== "string"
    || typeof event.canonical_title !== "string"
    || !["CONFIRMED", "CANDIDATE", "CONFLICT", "SPLIT_REQUIRED"].includes(event.fusion_status)
    || !Array.isArray(event.location_candidates)
    || !event.location_candidates.every((item) => typeof item === "string")
    || !Array.isArray(event.link_reasons)
    || !event.link_reasons.every((item) => typeof item === "string")
    || !Number.isInteger(event.independent_source_count)
    || !Array.isArray(event.independent_source_ids)
    || event.independent_source_count !== event.independent_source_ids.length
  ) return false;
  if (!documents.every((document) => (
    isObject(document)
    && typeof document.source_id === "string"
    && typeof document.source_name === "string"
    && typeof document.document_id === "string"
    && typeof document.evidence_locator === "string"
    && isHttpsUrl(document.official_url)
    && isObject(document.previous)
    && isObject(document.current)
    && typeof document.previous.document_version_id === "string"
    && typeof document.current.document_version_id === "string"
    && typeof document.previous.title === "string"
    && typeof document.current.title === "string"
  ))) return false;
  return (
    typeof background.dataset_name === "string"
    && typeof background.period === "string"
    && Number.isFinite(background.value)
    && typeof background.unit === "string"
    && typeof background.district === "string"
    && isHttpsUrl(background.source_url)
    && isObject(data.split_preview)
    && Array.isArray(data.split_preview.children)
    && isObject(data.merge_preview)
    && Array.isArray(data.merge_preview.merged_from_public_event_ids)
  );
}

export default function PublicEventFusionDemo() {
  const [payload, setPayload] = useState(null);
  const [loadState, setLoadState] = useState("loading");
  const [loadError, setLoadError] = useState("");
  const [preview, setPreview] = useState(null);
  const [candidateConfirmed, setCandidateConfirmed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetch(DATA_URL, { cache: "no-store" })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (cancelled) return;
        if (!isValidDemo(data)) {
          throw new Error("事件融合示範格式不相容，已停止呈現。");
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
    return <section className="v2-fusion-demo" data-testid="public-event-fusion-demo"><p>正在載入公共事件融合示範……</p></section>;
  }
  if (loadState !== "ready") {
    return (
      <section className="v2-fusion-demo error" data-testid="public-event-fusion-demo" role="alert">
        <strong>公共事件融合示範暫時無法安全呈現</strong>
        <p>{loadError}</p>
      </section>
    );
  }

  const event = payload.current_event;
  const candidate = payload.candidate_event;
  const split = preview === "split" ? payload.split_preview : null;
  const merge = preview === "merge" ? payload.merge_preview : null;
  const candidateStatus = candidateConfirmed ? "CONFIRMED" : candidate.fusion_status;

  return (
    <section className="v2-fusion-demo" data-testid="public-event-fusion-demo" aria-labelledby="public-event-fusion-title">
      <div className="v2-section-heading">
        <div>
          <p className="v2-eyebrow">公共事件融合 · 可重播示範</p>
          <h2 id="public-event-fusion-title">三份官方文件 → 一個 PublicEvent</h2>
        </div>
        <span className="v2-demo-badge">FIXTURE_ONLY · 未接 production</span>
      </div>
      <p className="v2-fusion-note">
        這張卡重播 deterministic fusion、版本差異與保守衝突狀態；不把文件異動 ID 當成事件 ID，也不寫入公開 canonical state。
      </p>

      <article className="v2-fusion-card">
        <div className="v2-fusion-card-head">
          <div>
            <span className={statusClass(event.fusion_status)}>{statusLabel(event.fusion_status)}</span>
            <span className="v2-fusion-source-count">{event.independent_source_count} 個獨立官方來源</span>
          </div>
          <code>{event.public_event_id}</code>
        </div>
        <h3>{event.canonical_title}</h3>
        <dl className="v2-fusion-facts">
          <div><dt>事件日期</dt><dd>{event.event_date}</dd></div>
          <div><dt>行政區候選</dt><dd>西屯區（{event.location_candidates.join("、")}）</dd></div>
          <div><dt>融合理由</dt><dd>{event.link_reasons.join("、")}</dd></div>
        </dl>

        <details className="v2-fusion-documents" open>
          <summary>展開 {payload.documents.length} 份官方文件與版本</summary>
          <div className="v2-fusion-document-list">
            {payload.documents.map((document) => {
              const changed = document.previous.document_version_id !== document.current.document_version_id;
              return (
                <article key={document.document_id} className="v2-fusion-document">
                  <div className="v2-fusion-document-head">
                    <strong>{sourceLabel(document)}</strong>
                    <a href={document.official_url} target="_blank" rel="noreferrer">開啟官方證據</a>
                  </div>
                  <small>{document.document_id} · locator: {document.evidence_locator}</small>
                  <div className="v2-fusion-versions">
                    <div>
                      <span>前一版本 · {document.previous.document_version_id}</span>
                      <strong>{document.previous.title}</strong>
                      <small>{dateLabel(document.previous.event_start_at)} → {dateLabel(document.previous.event_end_at)}</small>
                    </div>
                    <div className={changed ? "changed" : ""}>
                      <span>目前版本 · {document.current.document_version_id}{changed ? " · 有變更" : " · 未變更"}</span>
                      <strong>{document.current.title}</strong>
                      <small>{dateLabel(document.current.event_start_at)} → {dateLabel(document.current.event_end_at)}</small>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>
        </details>

        <div className="v2-fusion-conflict" role="status">
          <strong>版本差異／衝突：event_start_at</strong>
          <p>警政新聞由 18:00 改為 16:00；其他兩份官方文件尚未同步，因此目前保留 CONFLICT，不自動覆寫共同事件時間。</p>
        </div>

        <div className="v2-fusion-background">
          <strong>背景資料：{payload.background_preview.dataset_name}</strong>
          <p>
            {payload.background_preview.period} · {payload.background_preview.district} · {payload.background_preview.value.toLocaleString()}{payload.background_preview.unit}
          </p>
          <small>{payload.background_preview.note} <a href={payload.background_preview.source_url} target="_blank" rel="noreferrer">官方資料入口</a></small>
        </div>

        <div className="v2-fusion-actions" aria-label="本機人工決策預覽">
          <button type="button" onClick={() => setPreview("split")}>預覽人工拆分</button>
          <button type="button" onClick={() => setPreview("merge")}>預覽人工合併</button>
          {preview && <button type="button" onClick={() => setPreview(null)}>清除預覽</button>}
        </div>
        {split && (
          <div className="v2-fusion-preview" data-testid="public-event-split-preview">
            <strong>拆分預覽 · {split.original_status}</strong>
            <div>{split.children.map((child) => <span key={child.public_event_id}>{child.public_event_id} · {child.sources.join("、")} · {statusLabel(child.fusion_status)}</span>)}</div>
          </div>
        )}
        {merge && (
          <div className="v2-fusion-preview" data-testid="public-event-merge-preview">
            <strong>合併預覽 · {merge.independent_source_count} 個獨立來源</strong>
            <p>{merge.merged_from_public_event_ids.join(" + ")}；保留 {merge.public_event_id}，退休 {merge.retired_public_event_ids.join("、")}。</p>
          </div>
        )}
      </article>

      <article className="v2-fusion-candidate">
        <div>
          <span className={statusClass(candidateStatus)}>{statusLabel(candidateStatus)}</span>
          <strong>{candidate.canonical_title}</strong>
        </div>
        <p>{candidate.link_reason}；符合人工確認前的保守候選規則。</p>
        <code>{candidate.public_event_id}</code>
        <button
          type="button"
          disabled={candidateConfirmed}
          onClick={() => setCandidateConfirmed(true)}
        >
          {candidateConfirmed ? "已建立本機確認預覽" : "預覽人工確認"}
        </button>
        {candidateConfirmed && <small>CONFIRMED 僅存在於此瀏覽器預覽，未改寫 canonical state。</small>}
      </article>
    </section>
  );
}
