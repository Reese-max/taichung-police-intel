"use client";

import Hls from "hls.js";
import { useEffect, useRef, useState } from "react";
import {
  createLiveWatch, formatStreamClock, projectReceiptSummary, projectSearchResults,
  projectTimeline, validateLiveViewSession,
} from "../../lib/live-session-view.js";
import "./live.css";

const STORAGE_KEY = "govintel.live.watch.v1";

export default function LiveMeetingPage() {
  const [session, setSession] = useState(null);
  const [query, setQuery] = useState("");
  const [watches, setWatches] = useState([]);
  const [error, setError] = useState("");
  const [target, setTarget] = useState(null);
  const [playback, setPlayback] = useState("");
  const player = useRef(null);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      if (!Array.isArray(saved) || saved.some((row) => typeof row?.session_id !== "string" || !Array.isArray(row.segment_ids)
          || typeof row.text !== "string" || !Number.isInteger(row.revision) || !Number.isFinite(row.start_seconds))) throw new Error("Invalid WATCH data");
      setWatches(saved);
    }
    catch { setError("此瀏覽器的 WATCH 無法讀取，請先保留原有資料。"); }
  }, []);

  useEffect(() => {
    if (!target || !player.current) return;
    const video = player.current;
    let hls;
    const seek = () => {
      try { video.currentTime = target.time_seconds; }
      catch { setPlayback(`播放器無法定位，請依時間回看：${target.display}`); }
    };
    const failed = () => setPlayback(`影音無法定位，請依時間回看：${target.display}`);
    const deadline = setTimeout(() => { failed(); hls?.destroy(); video.removeAttribute("src"); video.load(); }, 5000);
    const confirmed = () => {
      clearTimeout(deadline);
      if (Math.abs(video.currentTime - target.time_seconds) < 1) setPlayback(`定位至 ${target.display}`);
      else failed();
    };
    const ready = () => { if (video.currentTime === target.time_seconds) confirmed(); else seek(); };
    video.addEventListener("loadedmetadata", ready);
    video.addEventListener("seeked", confirmed);
    video.addEventListener("error", failed);
    if (/\.m3u8(?:\?|$)/i.test(target.url) && !video.canPlayType("application/vnd.apple.mpegurl") && Hls.isSupported()) {
      hls = new Hls();
      hls.on(Hls.Events.ERROR, (_, data) => { if (data.fatal) { failed(); hls.destroy(); } });
      hls.loadSource(target.url);
      hls.attachMedia(video);
    } else { video.src = target.url; }
    return () => { clearTimeout(deadline); hls?.destroy(); video.removeEventListener("loadedmetadata", ready); video.removeEventListener("seeked", confirmed); video.removeEventListener("error", failed); video.removeAttribute("src"); video.load(); };
  }, [target]);

  async function load(event) {
    const file = event.target.files?.[0];
    if (!file) return;
    try {
      if (file.size > 2_000_000) throw new Error("Session 檔案超過 2 MB");
      const next = validateLiveViewSession(JSON.parse(await file.text()));
      setSession(next); setTarget(null); setQuery(""); setError(""); setPlayback("");
    } catch (problem) { setError(`未載入：${problem.message}`); }
    event.target.value = "";
  }

  function watch(segmentId) {
    if (watches.some((row) => row.session_id === session.session_id && row.segment_ids[0] === segmentId)) return;
    try {
      const selected = createLiveWatch(session, segmentId);
      const next = [...watches.filter((row) => row.session_id !== selected.session_id || row.segment_ids[0] !== segmentId), selected];
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      setWatches(next); setError("");
    } catch { setError("WATCH 未儲存，請確認此瀏覽器允許本機儲存。"); }
  }

  function download() {
    const data = { schema_version: 1, evidence_state: "LIVE_ASR_PROVISIONAL", watches: watches.filter((row) => row.session_id === session.session_id) };
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = "live-watch.json"; anchor.click(); URL.revokeObjectURL(url);
  }

  const search = session && query.trim() ? projectSearchResults(session, query) : null;
  const hits = search ? new Set(search.matches.map((hit) => hit.segment_id)) : null;
  const timeline = session ? projectTimeline(session).filter((row) => row.kind === "GAP" || !hits || hits.has(row.segment_id)) : [];
  const localWatches = session ? watches.filter((row) => row.session_id === session.session_id) : [];
  return <main className="live-meeting">
    <h1>公開會議即時導航</h1>
    <p className="live-badge">LIVE / AI 轉錄 / 未核對</p>
    <p>明確載入本機 session 才開始瀏覽；錄製音訊模式由本機 CLI 啟動。文字供搜尋與回看，正式引用請核對官方影音或議事錄。</p>
    <label>載入 session JSON <input type="file" accept="application/json,.json" onChange={load} /></label>
    {error && <p role="alert">{error}</p>}
    {session && <>
      <p>狀態：{session.status} · {session.asr_contract.provider === "fixture" ? "合成音訊回放／非真實會議" : "公開錄製音訊"}</p>
      <p>上限 {session.max_duration_seconds} 秒 · 授權 {session.budget_seconds ?? 0} 秒 · {session.provider_usage?.mode === "FIXTURE_ONLY" ? "模擬" : "provider"}呼叫 {session.provider_usage?.calls.length ?? 0} 次 · 已保留 {session.provider_usage?.reserved_seconds ?? 0} 秒 · 金額未估算</p>
      <label>搜尋暫定逐字稿 <input type="search" value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} /></label>
      <p role="status">{hits ? `${search.truncated ? "僅顯示前" : "找到"} ${hits.size} 段未核對文字；缺漏區間持續顯示。` : `共 ${session.segments.length} 段未核對文字。`}</p>
      {target && <video ref={player} controls preload="metadata" aria-label="官方影音播放器" />}
      <p role="status">{playback}</p>
      <ol className="live-timeline">{timeline.map((row) => <li key={row.segment_id || row.gap_id} data-kind={row.kind}>
        <span>{formatStreamClock(row.start_seconds)}–{formatStreamClock(row.end_seconds)}</span>
        {row.kind === "GAP" ? <p><strong>資料缺漏 GAP · {row.reason}</strong>：此區間內容未知。</p> : <>
          <small>LIVE / AI 轉錄 / 未核對 · 修訂 {row.revision} · {row.partial ? "部分音訊" : "暫定文字"}</small>
          <p>{row.text}</p>
          {row.profile_relevance?.matched_terms?.length > 0 && <p>議題命中：{row.profile_relevance.matched_terms.join("、")}</p>}
          {row.locator.reliable_seek ? <button onClick={() => { setTarget(row.locator); setPlayback(`準備定位 ${row.locator.display}`); }}>回看 {row.locator.display}</button> : <span>時間文字定位；此來源未確認 seek 支援。</span>}
          <button disabled={localWatches.some((watch) => watch.segment_ids[0] === row.segment_id)} onClick={() => watch(row.segment_id)}>加入 WATCH</button>
          <details><summary>修訂歷程</summary>{row.revision_history.map((prior) => <p key={prior.revision}>修訂 {prior.revision} · {prior.speaker_label || "未辨識講者"}：{prior.text}</p>)}</details>
        </>}
      </li>)}</ol>
      <h2>本機 WATCH（{localWatches.length}）</h2>
      <p>保存當時文字與時間範圍；重新載入同一 session 的修訂不會替換已保存內容。</p>
      <button onClick={download}>下載 WATCH</button>
      <ul>{localWatches.map((row) => <li key={row.segment_ids[0]}>{formatStreamClock(row.start_seconds)} · 修訂 {row.revision} · 未核對：{row.text}</li>)}</ul>
      <h2>會後核對收據</h2>
      <ul>{projectReceiptSummary(session).map((receipt) => <li key={receipt.receipt_id}>{receipt.outcome} · {receipt.verification_status}{receipt.current ? " · 目前" : " · 歷史"}{receipt.superseded_by ? " · 已取代" : ""}<p>{receipt.official?.locator || "等待可核對的官方來源"}</p></li>)}</ul>
    </>}
  </main>;
}
