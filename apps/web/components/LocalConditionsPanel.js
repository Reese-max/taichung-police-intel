"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CONDITION_STORAGE_KEY,
  addLocalCondition,
  cancelLocalCondition,
  clearLocalConditions,
  collectConditionItems,
  conditionDatasetStatus,
  conditionDateGaps,
  emptyLocalConditions,
  exportLocalConditions,
  initializeConditionBaselines,
  loadLocalConditions,
  markDisplayedUpdatesRead,
  projectLocalConditions,
  saveLocalConditions,
  syncConditionTimeWindows,
  updateLocalCondition,
} from "../lib/local-conditions.js";
import { loadConditionDatasets } from "../lib/condition-datasets.js";
import { safeHttpsUrl } from "../lib/public-query.js";
import { assessPublication } from "../lib/publication-freshness.mjs";

const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH || "";
const EMPTY_FORM = { keywords: "", district: "", road: "", topic: "", category: "", source_id: "", agency: "", time_from: "", time_to: "", daily_from: "", daily_to: "" };
const LABELS = { NEW: "新收錄實質更新", REVISED: "實質修訂", STATUS_CHANGED: "官方狀態更正", DEADLINE_CHANGED: "時程更正", RELEASED: "官方明文解除", CANCELLED: "官方明文取消" };

function filtersFromForm(form) {
  const filters = {};
  for (const [key, value] of Object.entries(form)) {
    if (!value.trim()) continue;
    filters[key] = ["keywords", "source_id"].includes(key) ? [...new Set(value.split(/[，,]/).map((word) => word.trim()).filter(Boolean))] : value.trim();
  }
  if (filters.time_from || filters.time_to) filters.time_semantics = "event_overlap";
  return filters;
}

function dateLabel(value) {
  if (!value) return "未提供";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "時間未知" : date.toLocaleString("zh-TW", { timeZone: "Asia/Taipei", hour12: false });
}

function UpdateCard({ item, state, onRead }) {
  const officialUrl = item.namespace === "demo:commute" ? null : safeHttpsUrl(item.official_url || item.primary_official_url || item.evidence?.[0]?.official_url);
  const comparison = item.comparison || item.material_diff || item.diff;
  const reasons = item.hit_condition_ids.map((id) => Object.entries(state.conditions[id]?.filters || {}).map(([key, value]) => `${key}：${Array.isArray(value) ? value.join("、") : value}`).join("；"));
  return (
    <article className="lc-update" data-testid="condition-update" data-kind={item.update_kind}>
      <div className="lc-row"><strong>{item.headline || item.title || item.event_id}</strong><span>{item.update_kind === "INITIAL" ? "初始清單" : item.update_kind === "HISTORICAL" ? "歷史／未確認實質變更" : item.read ? "已讀" : "未讀"}</span></div>
      <p>{item.what_changed || item.summary || LABELS[item.change_type] || "保留已收錄的公開資料，未推論目前現況。"}</p>
      <small>{LABELS[item.change_type] || item.change_type || "變更類型未知"} · 原文版本 {item.source_version || "未提供"} · 官方日期 {dateLabel(item.official_published_at || item.published_at)}</small>
      <p className="lc-reasons">命中條件：{reasons.join(" ／ ")}</p>
      {comparison && <details><summary>查看已發布的差異依據</summary><pre>{JSON.stringify(comparison, null, 2)}</pre></details>}
      {item.daily_schedule && <p>每日時段：{typeof item.daily_schedule === "string" ? item.daily_schedule : JSON.stringify(item.daily_schedule)}</p>}
      {item.road_segments?.length > 0 && <p>路段：{item.road_segments.map((row) => typeof row === "string" ? row : [row.road_name, row.from, row.to].filter(Boolean).join(" ")).join("、")}</p>}
      <div className="lc-actions">
        {officialUrl ? <a href={officialUrl} target="_blank" rel="noreferrer">核對原文／證據</a> : <span>{item.namespace === "demo:commute" ? `合成證據定位：${item.documents?.at(-1)?.evidence_locator || item.change_key}（非官方資料）` : "尚無可核對的官方 HTTPS 原文"}</span>}
        {item.update_kind === "UPDATE" && !item.read && <button type="button" onClick={() => onRead([item])}>標記這次更新已讀</button>}
      </div>
    </article>
  );
}

export default function LocalConditionsPanel() {
  const [state, setState] = useState(null);
  const [storageReady, setStorageReady] = useState(false);
  const [datasets, setDatasets] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [form, setForm] = useState(EMPTY_FORM);
  const [editingId, setEditingId] = useState(null);
  const [namespace, setNamespace] = useState("published");
  const [snapshotId, setSnapshotId] = useState("R1");
  const [nowMs, setNowMs] = useState(null);

  useEffect(() => {
    const updateClock = () => setNowMs(Date.now());
    updateClock();
    const timer = window.setInterval(updateClock, 60_000);
    const load = () => {
      try { setState(loadLocalConditions()); setStorageReady(true); setError(""); }
      catch (failure) { setStorageReady(false); setError(`本機資料未載入：${failure.message}。未覆寫原有資料，可清除後重試。`); }
    };
    load();
    const onStorage = (event) => {
      if (event.key === CONDITION_STORAGE_KEY || event.key === null) {
        load(); setNotice("另一分頁已變更追蹤資料，清單已重新載入。");
      }
    };
    window.addEventListener("storage", onStorage);
    window.addEventListener("focus", updateClock);
    return () => { window.clearInterval(timer); window.removeEventListener("storage", onStorage); window.removeEventListener("focus", updateClock); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    loadConditionDatasets(BASE_PATH).then(({ datasets: loaded, replayError }) => {
      if (cancelled) return;
      setDatasets(loaded);
      if (replayError) setError(`合成重播未啟用：${replayError}`);
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, []);

  const replay = datasets["public-query-replay"];
  const snapshots = replay?.snapshots || {};
  const replaySnapshot = snapshots[snapshotId];
  const publishedStatus = conditionDatasetStatus(datasets["v2-daily-brief"], datasets["intelligence-feed"], datasets["source-status"]);
  const age = assessPublication(datasets["v2-daily-brief"], datasets["source-status"], nowMs);
  const items = useMemo(() => namespace === "demo:commute" ? replaySnapshot?.tracking_items || replaySnapshot?.events || [] : collectConditionItems(datasets["v2-daily-brief"], datasets["intelligence-feed"]), [namespace, replaySnapshot, datasets]);
  const dataReady = namespace === "demo:commute" ? Boolean(replaySnapshot) : publishedStatus.present && !publishedStatus.mixed;
  const publication = namespace === "demo:commute" ? { generation: `demo:commute:${snapshotId}`, generated_at: replaySnapshot?.as_of, snapshot_complete: replaySnapshot?.snapshot_complete === true && !(replaySnapshot?.source_gaps || []).length } : { ...datasets["v2-daily-brief"], snapshot_complete: publishedStatus.complete && age.canReassure, generation_mixed: publishedStatus.mixed };

  useEffect(() => {
    if (!state || !storageReady || !dataReady) return;
    try {
      const latest = loadLocalConditions();
      const synced = syncConditionTimeWindows(latest, items, namespace);
      const next = initializeConditionBaselines(synced, items, publication, namespace);
      if (JSON.stringify(next) === JSON.stringify(latest)) return;
      saveLocalConditions(next); setState(next);
    } catch (failure) { setError(failure.message); }
  }, [state, storageReady, dataReady, items, namespace, snapshotId]);

  const activeState = state || emptyLocalConditions();
  const conditions = Object.values(activeState.conditions).filter((row) => (row.namespace || "published") === namespace);
  const matches = dataReady ? projectLocalConditions(activeState, items, { namespace }) : [];
  const dateGaps = dataReady ? conditionDateGaps(activeState, items, namespace) : [];
  const unread = matches.filter((item) => item.update_kind === "UPDATE" && !item.read);
  const preview = useMemo(() => {
    try { return projectLocalConditions(addLocalCondition(emptyLocalConditions(), { filters: filtersFromForm(form), namespace }), items, { namespace }).length; } catch { return null; }
  }, [form, items, namespace]);

  // Reload latest persisted state before each mutation to avoid overwriting another tab.
  const persist = (change, success) => {
    try {
      const next = change(loadLocalConditions());
      saveLocalConditions(next); setState(next); setNotice(success); setError("");
      return true;
    } catch (failure) { setError(`尚未保存：${failure.message}`); return false; }
  };
  const save = (event) => {
    event.preventDefault();
    const filters = filtersFromForm(form);
    if (!Object.keys(filters).length) { setError("請至少選擇一個追蹤條件。"); return; }
    if (persist((latest) => {
      const next = editingId ? updateLocalCondition(latest, editingId, { filters }) : addLocalCondition(latest, { filters, namespace });
      return dataReady ? initializeConditionBaselines(syncConditionTimeWindows(next, items, namespace), items, publication, namespace) : next;
    }, dataReady ? "已保存於此瀏覽器；目前已收錄資料列為初始清單。" : "已保存條件；待取得可核對的公開資料後建立初始清單。")) { setEditingId(null); setForm(EMPTY_FORM); }
  };
  const read = (displayed) => persist((latest) => markDisplayedUpdatesRead(latest, displayed), "已標記實際展示的更新為已讀；追蹤條件仍啟用，未改變官方事件狀態。");
  const exportState = () => {
    try {
      const artifact = exportLocalConditions(loadLocalConditions());
      const url = URL.createObjectURL(new Blob([artifact.content], { type: artifact.mime }));
      const link = document.createElement("a"); link.href = url; link.download = artifact.filename; link.click(); URL.revokeObjectURL(url);
      setNotice("已匯出本機條件與已讀 IDs；請自行保管。");
    } catch (failure) { setError(failure.message); }
  };

  return (
    <main className="lc-panel" id="govintel-local-conditions">
      <header><p className="v2-eyebrow">免登入 · 保存你的查詢條件</p><h1>個人條件追蹤</h1><p>條件與已讀紀錄僅存在同一瀏覽器，不會上傳。清除瀏覽器資料可能使清單消失；關站不推播，開站或手動重新整理才比對共用公開快照。</p></header>
      <div className="lc-actions"><a href={`${BASE_PATH}/public-query/`}>公開查詢</a><a href={`${BASE_PATH}/`}>返回發布快照</a><button type="button" onClick={() => window.location.reload()}>重新載入公開資料</button></div>
      <label className="lc-scope">資料範圍<select value={namespace} onChange={(event) => { setNamespace(event.target.value); setEditingId(null); setForm(EMPTY_FORM); }}><option value="published">公開發布快照</option>{Object.keys(snapshots).length > 0 && <option value="demo:commute">合成通勤重播（與真實清單隔離）</option>}</select></label>
      {namespace === "demo:commute" && <label className="lc-scope">重播資料截止<select value={snapshotId} onChange={(event) => setSnapshotId(event.target.value)}>{Object.entries(snapshots).map(([id, snapshot]) => <option key={id} value={id}>{id} · {dateLabel(snapshot.as_of)} · {snapshot.status}</option>)}</select><small>合成驗收資料；不代表真實官方事件或候選來源已啟用。</small></label>}
      <p className="lc-warning" role="status">{loading ? "正在載入共用快照，尚未完成比對。" : namespace === "demo:commute" ? `合成重播 ${snapshotId}；${publication.snapshot_complete ? "僅限此快照的已收錄範圍。" : "此快照有資料缺口，未推進為全部檢查。"}` : `${publishedStatus.reason} ${age.reason}`}</p>
      {namespace === "published" && publishedStatus.gaps.length > 0 && <ul className="lc-gaps">{publishedStatus.gaps.map((row) => <li key={row.source_id}>{row.source_id} · {row.source_name || "來源"}：{row.source_health}／{row.freshness_status} {(row.intelligence_gaps || []).join("、")}</li>)}</ul>}
      {dateGaps.length > 0 && <p className="lc-warning" data-testid="condition-date-gaps" role="status">{dateGaps.length} 件條件匹配資料的官方生效時間未知，未納入時段判斷，不能推定無更新：{dateGaps.map((row) => row.title || row.event_id).join("、")}</p>}
      {error && <p className="lc-warning error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
      <form className="lc-form" onSubmit={save}>
        <h2>{editingId ? "修改追蹤條件" : "新增追蹤條件"}</h2><p>請只填地區、議題與道路等公共條件，不填精確住址、私人筆記或憑證。不同欄位需同時符合；多個關鍵字以逗號分隔，符合其中一個即可。</p>
        {[["keywords", "關鍵字"], ["district", "地區 ID／地區"], ["road", "道路"], ["topic", "議題 ID／議題"], ["category", "事件類型"], ["source_id", "來源 ID（以逗號分隔）"], ["agency", "機關 ID"], ["time_from", "事件重疊時段：開始（含時區）"], ["time_to", "事件重疊時段：結束（含時區）"], ["daily_from", "每日關注開始（HH:MM，臺灣時間）"], ["daily_to", "每日關注結束（HH:MM，臺灣時間）"]].map(([key, label]) => <label key={key}>{label}<input name={key} value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} maxLength={300} /></label>)}
        <p>目前已載入資料的預覽命中：{dataReady ? preview ?? "條件格式無效" : "資料未完整取得"}；預覽不代表完整監測。</p>
        <div className="lc-actions"><button type="submit" disabled={!storageReady}>{editingId ? "保存修改並建立新初始清單" : "保存條件"}</button>{editingId && <button type="button" onClick={() => { setEditingId(null); setForm(EMPTY_FORM); }}>取消修改</button>}</div>
      </form>
      <section><h2>已保存條件 · {conditions.length}</h2>{conditions.length ? conditions.map((row) => <article className="lc-condition" key={row.condition_id}><strong>{row.enabled ? "啟用" : "已停用"} · 條件版本 {row.condition_version || 1}</strong><p>{Object.entries(row.filters).map(([key, value]) => `${key}：${Array.isArray(value) ? value.join("、") : value}`).join("；")}</p><small>{row.baseline_initialized ? `初始資料截止 ${dateLabel(row.baseline_at)}${row.baseline_complete ? "" : " · 僅涵蓋已取得資料"}` : "初始清單尚待可核對資料"}</small>{row.tracked_time_to && <p>已核對的延期追蹤至：{dateLabel(row.tracked_time_to)}；原始日期條件保留，截止不會自動解除。</p>}<div className="lc-actions"><button type="button" onClick={() => { setEditingId(row.condition_id); setForm(Object.fromEntries(Object.keys(EMPTY_FORM).map((key) => [key, Array.isArray(row.filters[key]) ? row.filters[key].join(", ") : row.filters[key] || ""]))); }}>修改</button><button type="button" onClick={() => persist((latest) => row.enabled ? cancelLocalCondition(latest, row.condition_id) : (dataReady ? initializeConditionBaselines(updateLocalCondition(latest, row.condition_id, { enabled: true }), items, publication, namespace) : updateLocalCondition(latest, row.condition_id, { enabled: true })), row.enabled ? "已停止此條件的提示，公開資料仍可查詢。" : "已重新啟用並建立初始清單。")}>{row.enabled ? "取消追蹤條件" : "重新啟用"}</button></div></article>) : <p>尚未保存此資料範圍的條件；可先從公開查詢加入。</p>}</section>
      <section><div className="lc-row"><h2>已收錄的未讀實質更新 · {unread.length}</h2><button type="button" disabled={!storageReady || !unread.length} onClick={() => read(unread)}>將目前展示的 {unread.length} 筆標為已讀</button></div>{unread.length ? unread.map((item) => <UpdateCard key={item.change_key} item={item} state={activeState} onRead={read} />) : <p>{dataReady ? "本次已載入資料中沒有未讀實質更新。仍須留意資料截止與來源缺口，不能推論現實沒有變化。" : "未取得可核對資料，尚未完成未讀比對；不是零筆更新。"}</p>}</section>
      <details><summary>初始、歷史與已讀資料 · {matches.length - unread.length}</summary>{matches.filter((item) => !unread.includes(item)).map((item) => <UpdateCard key={item.change_key} item={item} state={activeState} onRead={read} />)}</details>
      <footer className="lc-actions"><button type="button" onClick={exportState} disabled={!storageReady}>匯出本機紀錄 JSON</button><button type="button" onClick={() => { if (!window.confirm("清除這個瀏覽器的所有追蹤條件與已讀紀錄（含重播）？官方資料不會刪除。")) return; try { setState(clearLocalConditions()); setStorageReady(true); setError(""); setNotice("已清除本機追蹤與已讀紀錄。"); } catch (failure) { setError(failure.message); } }}>清除本機追蹤與已讀資料</button></footer>
    </main>
  );
}
