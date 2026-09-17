/** Presentation-only model. No canonical writes, AI verification or remote MCP. */
export const VIEWS = Object.freeze(['overview', 'ask', 'event', 'handoff', 'sources', 'connections']);
export const STORAGE_KEY = 'govintel:synthetic-workspace:v1';
export const EVENT_IDS = Object.freeze(['demo-activity', 'demo-council', 'demo-fraud']);
export const SCENARIOS = Object.freeze(['conflict', 'aligned', 'stale']);
export const EXPECTED_SOURCES = Object.freeze(['S-004', 'S-006', 'S-007', 'S-009', 'S-029']);
export const MAX_AGE_MS = 16 * 60 * 60 * 1000;
export const DEMO_EVENTS = Object.freeze([
  { id: 'demo-activity', title: '西屯週末活動', tag: '交通', district: '西屯區', summary: '道路管制開始時間有新版本，請先比對再更新交班。', reason: '示例：交班稿 v1 的交通時間引用受到影響。' },
  { id: 'demo-council', title: '市政會議交通改善追蹤事項', tag: '市政', district: '臺中市', summary: '會議紀錄新增附件，需要核對列管內容。', reason: '示例：附件的期別與追蹤事項需要確認。' },
  { id: 'demo-fraud', title: '反詐公告新增假客服提醒', tag: '反詐', district: '全國', summary: '澄清內容可作備詢素材，不等同本地案件量。', reason: '示例：只提供防詐提醒，不推論臺中發生件數。' },
]);
export const DEMO_SOURCES = Object.freeze([
  { id: 'demo-city', name: '市府活動公告', status: '正常', role: '活動資訊', coverage: '單一示例活動', time: '09/17 08:45', tone: 'verified' },
  { id: 'demo-police', name: '警察局交通公告', status: '正常', role: '道路管制', coverage: '單一示例公告', time: '09/17 08:45', tone: 'verified' },
  { id: 'demo-traffic', name: '交通局公告', status: '窗口不完整', role: '交通資訊', coverage: '只讀取部分公告', time: '09/17 08:45', tone: 'review' },
  { id: 'demo-council-source', name: '市政會議紀錄', status: '正常', role: '政策背景', coverage: '已收錄會議紀錄', time: '09/17 08:40', tone: 'verified' },
  { id: 'demo-165', name: '165 公開澄清', status: '正常', role: '反詐參考', coverage: '全國；非本地案件', time: '09/17 08:40', tone: 'verified' },
  { id: 'demo-failed', name: '其他候選來源', status: '連線失敗', role: '候選／未啟用', coverage: '本輪無法確認', time: '尚未成功', tone: 'conflict' },
]);
export function escapeHTML(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
}
export function safeOfficialURL(value) {
  try {
    const u = new URL(value);
    if (u.protocol !== 'https:' || u.username || u.password || (u.port && u.port !== '443')) return null;
    if (!u.hostname.endsWith('.gov.tw')) return null;
    return u.href;
  } catch { return null; }
}
export function readRoute(hash) {
  const p = new URLSearchParams(String(hash).replace(/^#/, ''));
  return { view: VIEWS.includes(p.get('view')) ? p.get('view') : 'overview',
    mode: p.get('mode') === 'snapshot' ? 'snapshot' : 'demo',
    event: EVENT_IDS.includes(p.get('event')) ? p.get('event') : EVENT_IDS[0] };
}
export function routeHash(route) {
  return '#' + new URLSearchParams({ view: route.view, mode: route.mode, event: route.event }).toString();
}
export function initialDemo() {
  return { schema_version: 1, synthetic: true, scenario: 'conflict', watched: ['demo-activity'], saved: [] };
}
export function validateDemo(s) {
  if (!s || s.schema_version !== 1 || s.synthetic !== true || !SCENARIOS.includes(s.scenario)) throw new Error('INVALID_DEMO_STATE');
  if (!Array.isArray(s.watched) || s.watched.length > 3 || new Set(s.watched).size !== s.watched.length || s.watched.some(id => !EVENT_IDS.includes(id))) throw new Error('INVALID_WATCH_IDS');
  if (!Array.isArray(s.saved) || s.saved.length > 1) throw new Error('INVALID_DEMO_HISTORY');
  for (const item of s.saved) {
    if (!item || Object.keys(item).sort().join(',') !== 'confirmed_at,generation,version' || item.version !== 2 || item.generation !== 'DEMO-aligned-v2' || typeof item.confirmed_at !== 'string' || !/^\d{4}-\d\d-\d\dT.*Z$/.test(item.confirmed_at) || !Number.isFinite(Date.parse(item.confirmed_at))) throw new Error('INVALID_DEMO_RECEIPT');
  }
  return { schema_version: 1, synthetic: true, scenario: s.scenario, watched: [...s.watched], saved: s.saved.map(x => ({...x})) };
}
export function readDemo(storage) {
  try {
    const raw = storage.getItem(STORAGE_KEY);
    if (!raw) return { state: initialDemo(), error: null };
    if (raw.length > 8192) throw new Error('TOO_LARGE');
    return { state: validateDemo(JSON.parse(raw)), error: null };
  } catch { return { state: initialDemo(), error: '示例狀態無法讀取；目前使用暫存模式，沒有覆寫舊資料。' }; }
}
export function persistDemo(storage, state) {
  try { const clean = validateDemo(state); storage.setItem(STORAGE_KEY, JSON.stringify(clean)); return { ok: true }; }
  catch { return { ok: false, error: '瀏覽器無法保存。此操作尚未保存，請勿當成已完成。' }; }
}
export function demoGeneration(state) { return `DEMO-${state.scenario}-v2`; }
export function canConfirm(state, reviewedGeneration) {
  return state.scenario === 'aligned' && reviewedGeneration === demoGeneration(state);
}
export function confirmDemo(state, reviewedGeneration, now = new Date()) {
  if (!canConfirm(state, reviewedGeneration)) throw new Error('REVIEW_REQUIRED');
  if (state.saved.length) return validateDemo(state);
  return validateDemo({ ...state, saved: [{ version: 2, generation: demoGeneration(state), confirmed_at: now.toISOString() }] });
}
export function demoExport(state, version) {
  if (version !== 1 && version !== 2) throw new Error('INVALID_VERSION');
  if (version === 2 && !state.saved.length) throw new Error('NOT_CONFIRMED');
  return { schema_version: 1, synthetic: true, purpose: '介面示例，非正式公務交班', version,
    event_id: 'demo-activity', event_start: '2026-09-20T18:00:00+08:00',
    traffic_control_start: version === 1 ? '17:00' : '16:00',
    evidence_ids: version === 1 ? ['demo-police:v1', 'demo-traffic:v1'] : ['demo-police:v2', 'demo-traffic:v2'],
    source_generation: version === 1 ? 'DEMO-baseline-v1' : state.saved[0].generation,
    confirmed_at: version === 1 ? null : state.saved[0].confirmed_at,
    limitations: ['所有公告與事件皆為合成示例', '不表示官方已查核或正式部署', '已匯出檔案不會隨來源自動修改'] };
}
export function searchDemo(text, category = '全部') {
  if (typeof text !== 'string' || text.length > 200) throw new Error('INVALID_SEARCH');
  const terms = text.trim().replaceAll('台', '臺').split(/\s+/).filter(Boolean);
  return DEMO_EVENTS.filter(e => (category === '全部' || e.tag === category) && terms.every(term => `${e.title} ${e.tag} ${e.district} ${e.summary}`.includes(term)));
}
function time(value) {
  return typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) && Number.isFinite(Date.parse(value)) ? Date.parse(value) : NaN;
}
/** A read-only view of existing files, not a new Query Gateway or canonical index. */
export function inspectSnapshot(feed, status, brief, now = Date.now()) {
  if (![feed, status, brief].every(d => d && d.schema_version === 1)) throw new Error('SCHEMA_INVALID');
  const run = feed.collection_run_id;
  if (typeof run !== 'string' || !run || run !== status.latest_collection_run?.collection_run_id || run !== brief.source_collection_run_id || feed.generated_at !== status.generated_at || feed.generated_at !== brief.source_status_generated_at) throw new Error('GENERATION_MISMATCH');
  if (!Number.isFinite(time(feed.generated_at)) || !Number.isFinite(time(brief.generated_at))) throw new Error('TIME_INVALID');
  if (!Array.isArray(feed.items) || feed.items.length > 10000 || !Array.isArray(status.sources)) throw new Error('ARRAY_INVALID');
  const ids = status.sources.map(s => s?.source_id);
  if (ids.length !== 5 || new Set(ids).size !== 5 || ids.some(id => !EXPECTED_SOURCES.includes(id))) throw new Error('SOURCE_COVERAGE_INVALID');
  if (feed.items.some(i => !i || typeof i.stable_id !== 'string' || typeof i.title !== 'string' || !ids.includes(i.source_id)) || new Set(feed.items.map(i => i.stable_id)).size !== feed.items.length) throw new Error('ROW_INVALID');
  const sourceGap = status.sources.some(s => s.source_health !== 'PASS' || !['COMPLETE_ZERO', 'COMPLETE_WITH_ITEMS'].includes(s.window_completeness));
  const checks = [time(feed.generated_at), time(brief.generated_at), ...status.sources.map(s => time(s.last_checked_at))];
  const unknown = checks.some(t => !Number.isFinite(t) || t > now + 60000);
  const stale = !unknown && now - Math.min(...checks) > MAX_AGE_MS;
  const partial = sourceGap || brief.snapshot_complete !== true || brief.publication_status !== 'READY' || status.latest_collection_run.status !== 'SUCCEEDED';
  return { run, generated_at: feed.generated_at, status: unknown ? 'UNKNOWN' : stale ? 'STALE' : partial ? 'PARTIAL' : 'SNAPSHOT_RECENT', stale, partial, sources: status.sources,
    items: feed.items, publication_verified: false, capabilities: ['保存快照列表', '來源狀態'],
    limitation: '只讀取本站保存的公開資料；沒有即時重查政府網站，也不是全市事件完整清單。' };
}
export async function loadSnapshot(fetcher = fetch, signal) {
  const names = ['intelligence-feed.json', 'source-status.json', 'v2-daily-brief.json'];
  const results = await Promise.all(names.map(async name => {
    const r = await fetcher(new URL(`../data/${name}`, import.meta.url), { signal, cache: 'no-store', redirect: 'error' });
    if (!r.ok) throw new Error('HTTP_FAILED');
    if (!(r.headers.get('content-type') || '').includes('json')) throw new Error('CONTENT_TYPE_INVALID');
    if (Number(r.headers.get('content-length')) > 4 * 1024 * 1024) throw new Error('RESPONSE_TOO_LARGE');
    const reader = r.body.getReader(); const decoder = new TextDecoder(); let size = 0, text = '';
    try {
      for (;;) { const {done, value} = await reader.read(); if (done) break; size += value.byteLength;
        if (size > 4 * 1024 * 1024) throw new Error('RESPONSE_TOO_LARGE'); text += decoder.decode(value, {stream: true}); }
      text += decoder.decode(); return JSON.parse(text);
    } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
  }));
  return { bundle: results, view: inspectSnapshot(...results) };
}
