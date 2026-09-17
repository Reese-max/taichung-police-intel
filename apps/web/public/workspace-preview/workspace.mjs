import { VIEWS, STORAGE_KEY, EVENT_IDS, SCENARIOS, DEMO_EVENTS, DEMO_SOURCES, escapeHTML as e, safeOfficialURL,
  readRoute, routeHash, initialDemo, readDemo, persistDemo, demoGeneration, canConfirm, confirmDemo, demoExport,
  searchDemo, inspectSnapshot, loadSnapshot } from './model.mjs';

const root = document.querySelector('#app');
const dialog = document.querySelector('#detail-dialog');
const storage = { getItem: key => window.localStorage.getItem(key), setItem: (key, value) => window.localStorage.setItem(key, value) };
const loaded = readDemo(storage);
let demo = loaded.state, storageError = loaded.error, storageAllowed = !loaded.error;
let route = readRoute(location.hash), tab = 'compare', query = '西屯', category = '全部', followup = false;
let reviewed = null, snapshot = null, bundle = null, snapshotState = 'idle', snapshotError = '', request = null, requestId = 0;
let sourceFilter = '全部', toastTimer, lastFocus;
const labels = ['情報總覽','對話搜尋','事件與證據','追蹤與交班','資料來源','AI / MCP 連接'];
const icons = ['grid','chat','layers','file','pulse','plug'];
const paths = {
  shield:'M12 3 4 7v5c0 4 3 7 8 9 5-2 8-5 8-9V7Z M8 12l3 3 5-6',
  grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
  chat:'M21 11a9 9 0 0 1-9 9H5l-3 2 1-6a9 9 0 1 1 18-5Z',
  layers:'m12 3 10 5-10 5L2 8Z M2 12l10 5 10-5 M2 16l10 5 10-5',
  file:'M5 2h10l4 4v16H5Z M14 2v6h5 M8 12h8 M8 16h5',
  pulse:'M2 12h4l3-9 5 18 3-9h5', plug:'M8 2v5 M16 2v5 M6 7h12v4a6 6 0 0 1-12 0Z M12 17v5',
  arrow:'M5 12h14 M13 6l6 6-6 6', chevron:'m9 5 7 7-7 7',
  alert:'m12 3 10 18H2Z M12 9v5 M12 17v1', search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
  close:'m6 6 12 12 M6 18 18 6', check:'m4 12 5 5L20 6', external:'M14 3h7v7 M21 3 11 13 M10 3H3v18h18v-7',
  clock:'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18 M12 7v5l4 2', more:'M4 5h16 M4 12h16 M4 19h16',
  download:'M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5', back:'M19 12H5 M11 6l-6 6 6 6',
};
function icon(name) { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name] || paths.file}"/></svg>`; }
function badge(text, tone='neutral') { return `<span class="badge ${tone}">${e(text)}</span>`; }
function button(text, action, extra='', tone='') { return `<button class="btn ${tone}" data-action="${action}" ${extra}>${e(text)}</button>`; }
function nav(view, text, extra='') { return `<a href="${routeHash({...route, view})}" ${extra}>${text}</a>`; }
function eventLink(id, text, cls='btn secondary') { return `<a class="${cls}" href="${routeHash({...route, view:'event', event:id})}">${text}</a>`; }
function announce(text) { clearTimeout(toastTimer); document.querySelector('#announcer').textContent = text; toastTimer = setTimeout(() => document.querySelector('#announcer').textContent = '', 7000); }
function notice(text, tone='', action='') { return `<div class="notice ${tone}">${icon(tone === 'good' ? 'check' : tone === 'info' ? 'shield' : 'alert')}<p>${text}</p>${action}</div>`; }
function eventData() { return DEMO_EVENTS.find(v => v.id === route.event) || DEMO_EVENTS[0]; }
function isAligned() { return demo.scenario === 'aligned'; }
function activityStatus() { return demo.scenario === 'stale' ? badge('示例資料過期','neutral') : isAligned() ? badge('示例來源一致','verified') : badge('示例來源不一致','conflict'); }
function sourceButton(ref, title, sub='') { return `<button class="source-link" data-action="evidence" data-ref="${ref}"><span class="source-number">${ref.includes('city')?'01':ref.includes('police')?'02':'03'}</span>${e(title)}${sub?`<small>${e(sub)}</small>`:''}</button>`; }
function commitState(next) {
  if (!storageAllowed) { demo = next; announce(storageError); return false; }
  const result = persistDemo(storage, next);
  if (!result.ok) { storageError = result.error; announce(result.error); return false; }
  demo = next; return true;
}
function modeSelect(id) { return `<label class="small" for="${id}"><span class="hide">資料模式</span><select class="mode-select" id="${id}" aria-label="資料模式"><option value="demo" ${route.mode==='demo'?'selected':''}>合成互動示例</option><option value="snapshot" ${route.mode==='snapshot'?'selected':''}>本站保存快照</option></select></label>`; }
function frame(body, title, subtitle, action='') {
  const menu = VIEWS.map((v,i) => nav(v,`${icon(icons[i])}<span>${labels[i]}</span>`,`class="nav-link" ${route.view===v?'aria-current="page"':''}`)).join('');
  return `<aside class="sidebar"><div class="brand"><span class="brand-mark">${icon('shield')}</span>GovIntel</div><p class="tagline">公共情報・證據工作台</p><hr><div class="nav-label">工作空間</div><nav class="nav-list" aria-label="主要導覽">${menu}</nav><div class="sidebar-bottom"><div class="policy-card"><strong>${icon('shield')}只使用公開資料</strong>不含案件個資、110 或<br>內部勤務資訊。</div><p class="sidebar-foot">介面預覽 / FIGMA V1.1</p></div></aside>
  <div class="shell"><header class="topbar"><div class="breadcrumb"><span class="muted">臺中公開資訊</span><span class="muted">/</span><strong>${labels[VIEWS.indexOf(route.view)]}</strong></div><div class="top-actions">${badge(route.mode==='demo'?'互動原型・合成示例資料':'保存快照・非即時核對',route.mode==='demo'?'review':'neutral')}${modeSelect('desktop-mode')}<span class="avatar" aria-hidden="true">公</span></div></header>
  <header class="mobile-header"><div class="brand"><span class="brand-mark">${icon('shield')}</span>GovIntel</div>${modeSelect('mobile-mode')}</header>
  <main id="main-content" tabindex="-1" class="view-${route.view}"><div class="page-heading"><div><h1 id="page-title">${e(title)}</h1><p class="subtitle">${e(subtitle)}</p></div>${action}</div>
  ${storageError && route.mode==='demo'?notice(e(storageError),'error',`<button data-action="reset">重設示例</button>`):''}
  ${route.mode==='demo' && demo.scenario==='stale'?notice('目前為過期情境示例；可讀歷史內容，但不能確認為現在有效。','error'):''}${body}<footer class="footer"><div><span>${route.mode==='demo'?'所有事件、來源、時間與數量皆為合成示例；非官方資訊或勤務指令。':'保存快照不代表此刻政府網站狀態；文件收錄時間不等於事件發生時間。'}</span><a href="../../">返回既有情報首頁 ${icon('external')}</a></div></footer></main></div>
  <nav class="bottom-nav" aria-label="手機導覽">${VIEWS.slice(0,4).map((v,i)=>nav(v,`${icon(icons[i])}<span>${['總覽','搜尋','證據','交班'][i]}</span>`,route.view===v?'aria-current="page"':'')).join('')}<button data-action="more" ${['sources','connections'].includes(route.view)?'aria-current="page"':''}>${icon('more')}<span>更多</span></button></nav>`;
}
function metric(label,value,description) { return `<div class="metric"><span>${label}</span><div class="metric-bottom"><b>${value}</b><small>${description}</small></div></div>`; }
function overview() {
  const pending = isAligned() && demo.saved.length ? 2 : 3;
  return frame(`${notice('示例快照｜一項來源連線失敗；另一項窗口不完整。查不到資料，不代表沒有事件。')}
  <section class="metrics" aria-label="合成示例統計">${metric('需要核對',String(pending).padStart(2,'0'),'待處理示例')}${metric('持續追蹤',String(demo.watched.length).padStart(2,'0'),'保存在此瀏覽器')}${metric('外部待確認','02','不列入正式交班')}${metric('來源可取得','5/6','不代表覆蓋完整')}</section>
  <div class="columns"><section><div class="section-title"><h2>需要核對的異動</h2>${nav('handoff','查看待處理 →','class="text-btn"')}</div><div class="stack"><article class="card event-card">${activityStatus()}<h3>西屯週末活動（示例）</h3><p class="muted">道路管制開始時間有新版本，請先比對再更新交班。</p><div class="row between"><div class="diff-time"><span class="time-pill">17:00</span>${icon('arrow')}<span class="time-pill new ${isAligned()?'aligned':''}">16:00</span></div>${eventLink('demo-activity','比較公告版本','btn')}</div><p class="muted small">${isAligned()?'示例中兩份交管公告已一致；人工確認仍是另一個步驟。':'警察局 v2／交通局 v1 尚未一致；不推測調整原因。'}</p></article>
  ${DEMO_EVENTS.slice(1).map(ev=>`<article class="card compact-card"><div>${badge(ev.tag==='市政'?'示例附件新增':'示例反詐提醒',ev.tag==='市政'?'verified':'teal')}<h3>${e(ev.title)}</h3><p class="muted small">${e(ev.summary)}</p></div>${eventLink(ev.id,`${icon('chevron')}<span class="hide">查看${e(ev.title)}</span>`,'icon-btn')}</article>`).join('')}</div></section>
  <aside class="stack aside-stack"><section class="card"><div class="section-title"><h2>持續追蹤</h2>${badge(`${demo.watched.length}`)}</div>${demo.watched.length?demo.watched.map(id=>`<div class="tracking-row"><span>待核對</span>${eventLink(id,e(DEMO_EVENTS.find(v=>v.id===id).title),'')}</div>`).join(''):'<p class="muted small">尚未加入追蹤。可從事件頁加入示例。</p>'}<div class="tracking-row"><span>版本提醒</span><strong>${demo.saved.length?'已保存示例 v2；v1 仍保留':'交班稿 v1：1 個引用受影響'}</strong></div>${nav('handoff','前往追蹤與交班 →','class="text-btn"')}</section>
  <section class="card tinted"><h2>外部新聞訊號</h2><p class="small">尚待官方確認・2 筆合成示例</p><p>媒體提及活動可能調整接駁安排</p><p class="small">Taiwan Intel Dashboard → 媒體來源<br>僅作發現線索，不作官方結論。</p><button class="text-btn" data-action="discovery">查看未確認訊號 →</button></section>
  <section class="card callout"><h2>${icon('chat')} 直接問 GovIntel</h2><p class="small">先用示例資料體驗搜尋與版本追問。</p>${nav('ask','開啟對話式搜尋 →','class="text-btn"')}</section></aside></div>`, '先看真正需要你處理的事。','把跨機關公告整理成異動與待核對事項，讓每個結論都能回到證據。');
}
function ask() {
  const results = searchDemo(query, category);
  return frame(`${notice('對話示例｜僅搜尋 3 筆合成資料，未連接 LLM 或正式 Query Gateway。','info')}
  <div class="columns"><section class="card chat-card"><div class="chat-header">${badge('目前對話・本機示例')}<button class="text-btn" data-action="clear-query">清除條件</button></div>
  <div class="toolbar" aria-label="示例分類">${['全部','交通','市政','反詐'].map(c=>`<button class="chip" data-action="category" data-value="${c}" aria-pressed="${category===c}">${c}</button>`).join('')}</div>
  <div class="user-message">${query?`搜尋「${e(query)}」`:'查看全部示例資料'}</div>
  <div class="assistant-message"><div class="assistant-title">${icon('shield')} GovIntel ${badge('示例資料搜尋','teal')}</div>
  ${results.length?`<h3>找到 ${results.length} 筆示例；資料限制與來源狀態一起保留。</h3>${results.map(ev=>`<button class="answer-result" data-action="open-event" data-id="${ev.id}"><span><strong>${e(ev.title)}</strong><small>${e(ev.id==='demo-activity'?`活動 18:00｜道路管制 ${isAligned()?'16:00':'16:00 / 17:00 待核對'}`:ev.summary)}</small></span>${icon('chevron')}</button>`).join('')}`:`<div class="empty-state">${icon('search')}<h2>示例資料中沒有符合的結果</h2><p>目前只搜尋這 3 筆合成示例，不代表真實世界沒有相關事件。</p><button class="btn secondary" data-action="clear-query">清除條件並查看示例</button></div>`}
  ${results.some(v=>v.id==='demo-activity')?`<div class="row"><button class="text-btn" data-action="followup">這場活動的公告改了什麼？ →</button></div>`:''}
  ${followup && results.some(v=>v.id==='demo-activity')?`<div class="notice info"><p><strong>示例公告的管制開始時間由 17:00 改為 16:00。</strong><br>${isAligned()?'兩份示例公告目前記載一致。':'另一份示例公告仍記載 17:00，需要重新核對。'}<br>資料沒有說明原因，不自行加上「因豪雨」。</p></div>${sourceButton('demo-police:v2','查看此句的示例原文','第 4 段・版本 v2')}`:''}</div>
  <form id="search-form" class="chat-form"><label for="query-input" class="hide">搜尋示例資料的關鍵字</label><input class="search-input" id="query-input" name="q" maxlength="200" value="${e(query)}" placeholder="輸入關鍵字，例如：西屯、交通、反詐" autocomplete="off"><button class="btn" type="submit" aria-label="送出示例搜尋">${icon('arrow')}</button></form><p class="small muted">Enter 送出。文字只在此頁暫存，不上傳、不保存完整對話。</p></section>
  <aside class="stack aside-stack"><section class="card"><h2>這個示例答案的依據</h2><p class="muted small">點擊來源，查看示例版本與原文。</p>${results.some(v=>v.id==='demo-activity')?`${sourceButton('demo-city:v1','市府活動公告','活動 18:00・v1')}${sourceButton('demo-police:v2','警察局交通公告','道路管制 16:00・v2')}${sourceButton(`demo-traffic:${isAligned()?'v2':'v1'}`,'交通局交通資訊',`道路管制 ${isAligned()?'16:00・v2':'17:00・v1'}`)}`:'<p class="muted small">這批結果尚未提供正文比對；不借用另一事件的原文作為證據。</p>'}</section>
  <section class="card" aria-label="回答限制"><h2>回答範圍與限制</h2><p class="muted">這不是全市活動清單。未收錄不等於沒有臨時管制。</p><p class="muted small">沒有任意網路搜尋、案件、110 或勤務資料。範例回答不代表已完成 AI 語意查證。</p>${nav('sources','查看資料來源狀態 →','class="text-btn"')}</section><section class="card"><h3>可追溯的示例回應</h3><code>${demoGeneration(demo)}</code><p class="small muted">同一示例版本。查詢狀態不提升來源可信度。</p></section></aside></div>`, '用問題，找到可核對的答案。','先明確查詢範圍，再整理答案；未確認、過期與缺口不會被隱藏。');
}
function eventView() {
  const ev = eventData();
  if (ev.id !== 'demo-activity') return frame(`${notice('這是一筆獨立合成示例，不會跳到不相關的活動或交通時間。','info')}<div class="columns"><section class="card stack">${badge('合成示例','review')}<h2>${e(ev.title)}</h2><p>${e(ev.summary)}</p><p class="muted">${e(ev.reason)}</p><dl><dt>資料角色</dt><dd>${e(ev.tag==='反詐'?'全國反詐參考；不推論臺中案件量':'會議政策背景；不是即時勤務事件')}</dd><dt>地區</dt><dd>${e(ev.district)}</dd></dl>${button(demo.watched.includes(ev.id)?'已加入示例追蹤':'加入示例追蹤','watch',`data-id="${ev.id}"`,demo.watched.includes(ev.id)?'secondary':'')}</section><aside class="card"><h2>證據功能範圍</h2><p class="muted">此筆示例尚未製作正文或附件差異，不能借用另一場活動的證據。</p>${badge('附件比對尚未提供')}${nav('handoff','查看示例追蹤 →','class="text-btn"')}</aside></div>`,ev.title,'各事件保留自己的身分，不以相同畫面冒充不同資料。',nav('overview','返回總覽','class="btn secondary small-btn"'));
  const comparison = `<section class="card"><h2>同一份公告，前後版本比較</h2><p class="muted small">僅顯示道路管制欄位的實質異動・合成原文</p><div class="compare"><div class="quote"><small>前版 v1</small><p>周邊道路自</p><strong>17:00 起實施管制。</strong><small>原文第 4 段・保存的前一版</small><button class="text-btn" data-action="evidence" data-ref="demo-police:v1">查看示例原文</button></div><div class="quote new"><small>新版 v2</small><p>周邊道路自</p><strong>16:00 起實施管制。</strong><small>原文第 4 段・本次保存版本</small><button class="text-btn" data-action="evidence" data-ref="demo-police:v2">查看示例原文</button></div></div><p class="small"><strong>變更：</strong>提前 1 小時。<strong>原因：</strong>示例原文未說明。</p></section>`;
  const evidence = `<section class="card"><h2>示例文件與原文定位</h2>${sourceButton('demo-city:v1','市府活動公告','第 2 段｜活動開始 18:00')}${sourceButton('demo-police:v2','警察局交通公告','第 4 段｜道路管制 16:00')}${sourceButton(`demo-traffic:${isAligned()?'v2':'v1'}`,'交通局交通資訊','第 3 段｜請核對來源版本')}<p class="muted small">這些為合成文件，沒有偽造官方頁面連結。</p></section>`;
  const summary = `<section class="card stack"><h2>事件概覽</h2><p>活動於示例日期 09/20 18:00–22:00 舉行；道路管制是另一項資訊。</p><p>${isAligned()?'兩份合成交管公告記載 16:00，但仍需人工確認交班文字。':'兩份交管公告記載不同，不選擇其中一份作確定結論。'}</p>${badge('地理精度：行政區','teal')}<p class="muted">沒有精確活動位置，不提供虛假的周邊距離或人流推估。</p></section>`;
  return frame(`${demo.scenario==='stale'?notice('過期示例：仍可查看歷史原文，但不能確認為目前有效。','error'):''}<section class="card facts"><div><small>活動時間</small><strong>09/20（日）18:00–22:00</strong><small>示例場次；與道路管制分開</small></div><div><small>道路管制開始</small><strong class="${isAligned()?'teal-text':'danger-text'}">${isAligned()?'16:00・示例來源一致':'16:00 / 17:00 尚未一致'}</strong><small>不把活動與管制時間混為衝突</small></div><div><small>涵蓋範圍</small><strong>臺中市・西屯區</strong><small>行政區層級，未推測精確位置</small></div></section>
  <div class="tabs" role="tablist" aria-label="事件資訊">${[['summary','事件概覽'],['compare','版本比較'],['evidence','示例證據']].map(([id,name])=>`<button class="tab" id="tab-${id}" role="tab" data-action="tab" data-tab="${id}" aria-selected="${tab===id}" aria-controls="event-panel" tabindex="${tab===id?'0':'-1'}">${name}</button>`).join('')}</div>
  <div class="toolbar"><label for="scenario">原型情境</label><select id="scenario"><option value="conflict" ${demo.scenario==='conflict'?'selected':''}>來源衝突</option><option value="aligned" ${isAligned()?'selected':''}>來源一致後</option><option value="stale" ${demo.scenario==='stale'?'selected':''}>資料過期</option></select><span class="muted small">只切換合成示例，不修改政府資料。</span></div>
  <div class="columns"><div class="stack"><div role="tabpanel" id="event-panel" aria-labelledby="tab-${tab}">${tab==='compare'?comparison:tab==='evidence'?evidence:summary}</div>
  <section class="card"><h2>各來源目前記載（示例）</h2><div class="table-wrap"><table class="source-table facts-table"><caption class="hide">活動與交通管制分開比較</caption><thead><tr><th>來源</th><th>事實角色</th><th>時間</th><th>版本</th></tr></thead><tbody><tr><td>市府</td><td>活動開始</td><td>18:00</td><td>v1</td></tr><tr><td>警察局</td><td>道路管制開始</td><td>16:00</td><td>v2</td></tr><tr><td>交通局</td><td>道路管制開始</td><td class="${isAligned()?'teal-text':'danger-text'}">${isAligned()?'16:00':'17:00'}</td><td>${isAligned()?'v2':'v1'}</td></tr></tbody></table></div></section></div>
  <aside class="stack aside-stack"><section class="card"><h2>示例證據時間軸</h2><ol class="timeline"><li><small>09/16 18:20</small>兩份交管公告皆記載 17:00<br><span class="muted">保存交班稿 v1</span></li><li><small>09/17 08:10</small>警察局公告更新為 16:00<br><span class="muted">來源版本 v2</span></li><li><small>09/17 08:45</small>${isAligned()?'交通局公告也改為 16:00':'交通局仍記載 17:00'}<br>${activityStatus()}</li></ol></section><section class="card"><h3>1 個交班引用需要核對</h3><p class="muted small">交班稿 v1 第 2 項引用舊時間。舊版保留，不會自動改寫。</p>${nav('handoff','查看受影響交班','class="btn secondary full"')}</section>${button(demo.watched.includes(ev.id)?'已加入追蹤・再次點擊不重複':'加入示例追蹤','watch',`data-id="${ev.id}"`,'secondary')}</aside></div>`,ev.title,'示例事件 PE-DEMO-001 ／三份來源文件，各自保留原文與版本。',nav('overview','返回總覽','class="btn secondary small-btn"'));
}
function handoff() {
  const saved = demo.saved.length > 0;
  return frame(`${notice(isAligned()?'示例來源已一致。請核對原文與文字，再保存示例交班版本。':'來源仍有衝突或已過期。保留 v1，不能直接確認成目前有效。',isAligned()?'good':'')}
  <div class="handoff-columns"><aside class="card review-column"><h2>待處理事項</h2><div class="row">${badge('核對示例','review')}${badge(`追蹤中 ${demo.watched.length}`)}</div><div class="review-list">${DEMO_EVENTS.map((ev,i)=>`<button class="review-item ${i===0?'selected':''}" data-action="${i===0?'review-info':'open-event'}" data-id="${ev.id}">${i===0?badge(isAligned()?'待人工確認':'引用需重新核對','review'):''}<h3>${e(ev.title)}</h3><small>${e(i===0?'交班稿 v1 第 2 項｜道路管制時間出現新版本':ev.reason)}</small></button>`).join('')}</div><p class="small muted">沒有新公告時，尚未完成的示例追蹤仍會保留在此瀏覽器。</p></aside>
  <section class="card document">${badge(saved?'v2 已保存在此瀏覽器・示例':'v2 草稿・示例',saved?'verified':'teal')}<h2>臺中公開資訊交班</h2><p class="small muted">合成示例日期 2026/09/17 ／ ${demoGeneration(demo)}</p><hr><h3>01 活動與交通</h3><p><strong>西屯週末活動預定於 09/20 18:00 開始。</strong></p><p class="muted small">依據：合成市府活動公告 v1。活動與交通管制分開列示。</p>
  <div class="quoted-fact ${isAligned()?'ready':''}">${badge('第 2 項・引用受影響',isAligned()?'verified':'review')}<h3>${isAligned()?'道路管制於 16:00 開始；活動於 18:00 開始。':'道路管制：警察局記載 16:00，交通局記載 17:00。'}</h3><p class="small">${isAligned()?'兩份示例來源一致，不推測調整原因。':'目前無法確認一致時間。不得直接沿用 v1 作最新資訊。'}</p></div>
  <section><h3>版本紀錄</h3><div class="history-row"><span><strong>v1</strong> 示範用歷史版本<br><small class="muted">09/16 18:20・保持不變</small></span><button class="text-btn" data-action="history" data-version="1">查看 v1</button></div>${saved?`<div class="history-row"><span><strong>v2</strong> 使用者已確認的示例<br><small class="muted">${e(new Date(demo.saved[0].confirmed_at).toLocaleString('zh-TW',{timeZone:'Asia/Taipei'}))}（臺灣時間）</small></span><button class="text-btn" data-action="history" data-version="2">查看 v2</button></div>`:'<p class="small muted">v2 尚未保存。僅可在來源一致且核對後確認。</p>'}</section>
  ${saved && !isAligned()?notice('情境已變更。先前保存的 v2 只代表當時示例版本，不會自動跟著修改。'):''}
  <div class="document-actions">${eventLink('demo-activity','查看原文並核對','btn secondary')}${saved?button('匯出已保存的示例 v2','export','data-version="2"'):button(isAligned()?'核對並保存示例 v2':'仍有引用待核對','confirm-open',isAligned()?'':'disabled')}</div><p class="small muted">只保存合成示例的版本與來源 ID；不保存私人備註，不送到伺服器。${button('重設示例','reset','','secondary small-btn')}</p></section></div>`, '交班有版本，確認才算完成。','已確認內容不會被覆寫；來源有變，只標記真正受影響的引用。');
}
function sources() {
  const selected = DEMO_SOURCES.filter(s=>sourceFilter==='全部'||(sourceFilter==='需要注意'?s.tone!=='verified':s.tone==='verified'));
  return frame(`${notice('這是來源狀態示例。正常取得不等於查詢覆蓋完整，也不代表正式來源已啟用。','info')}
  <section class="metrics">${metric('已設計的來源','06','合成示例')}${metric('可取得','05','含一項部分取得')}${metric('窗口不完整','01','不能推論無事件')}${metric('連線失敗','01','保留最後有效版本')}</section>
  <div class="toolbar"><label for="source-filter">來源狀態</label><select id="source-filter">${['全部','需要注意','正常'].map(v=>`<option ${v===sourceFilter?'selected':''}>${v}</option>`).join('')}</select><span class="small muted">顯示 ${selected.length}／6 個示例來源</span></div>
  <section class="card"><div class="table-wrap" tabindex="0" aria-label="來源清單，可橫向捲動"><table class="source-table"><thead><tr><th>來源</th><th>取得狀態</th><th>資料角色</th><th>涵蓋範圍</th><th>最後核對（示例）</th><th>詳情</th></tr></thead><tbody>${selected.map(s=>`<tr><td>${e(s.name)}</td><td>${badge(s.status,s.tone)}</td><td>${e(s.role)}</td><td>${e(s.coverage)}</td><td>${e(s.time)}</td><td><button class="icon-btn" data-action="source-detail" data-id="${s.id}" aria-label="查看${e(s.name)}詳情">${icon('chevron')}</button></td></tr>`).join('')}</tbody></table></div></section>
  <div class="columns"><section class="card stack"><h2>取得完整，不代表世界完整</h2><p>即使議會資料全數正常，也不足以回答「今天西屯所有臨時交通管制是否為零」。</p><p class="muted">查詢需同時說明時間、地區、資料種類及未涵蓋事項。</p></section><section class="card"><h2>查看實際保存快照</h2><p class="muted small">可唯讀載入本站既有的來源狀態與收錄文件；不重抓政府網站。</p><button class="text-btn" data-action="snapshot-mode">切換到本站保存快照 →</button></section></div>`, '資料從哪裡來，現在能回答什麼。','取得狀態、時效、查詢覆蓋與正式啟用是四件不同的事。');
}
function connections() {
  return frame(`${notice('正式 AI／MCP 端點尚未提供。本頁是介面與資料邊界說明，不會連接帳號。')}
  <div class="columns"><section class="card stack"><div class="row between"><h2>GovIntel Evidence MCP</h2>${badge('尚未提供')}</div><p>未來由 Web 對話與 MCP 共用 Query Gateway，避免兩個入口產生不同資料版本。</p><div class="code-card">Web 對話 ─┐\n          ├─ 共用查詢核心 ─ 公開資料／證據／來源狀態\nMCP 工具 ─┘\n\n模式：唯讀・公開資料\n端點：尚未部署（不產生假的連線網址）</div><h3>第一階段工具範圍（規劃）</h3>${['搜尋已收錄資料','取得已發布摘要','取得來源健康狀態'].map(t=>`<div class="row between"><span>${t}</span>${badge('規劃中')}</div>`).join('')}${button('連接端點尚未提供','unavailable','disabled')}<p class="small muted">不要求輸入 API key；不提供事件確認、發布、刪除或勤務指令。</p></section><aside class="stack aside-stack"><section class="card"><h2>回答始終攜帶</h2><p class="muted">資料版本、來源定位、最後核對時間、未確認與衝突狀態、查詢涵蓋限制。</p></section><section class="card"><h2>連線失敗也能查證</h2><p class="muted">對話服務中止，不會讓既有靜態情報頁失效。</p><a class="text-btn" href="../../">返回原有情報頁 →</a></section><section class="card"><h2>設計與實作分開</h2><p class="muted small">本預覽不是已部署 MCP，也沒有執行 AI 語意驗證。外部 AI 如何編排答案，不能由此頁保證。</p></section></aside></div>`, '讓資料進入你的 AI 工作流程。','共用一份資料與證據；不同入口，不建立不同事實。');
}
function unavailable() {
  return frame(`${notice('保存快照模式不使用合成事件，不會在讀取失敗時自動切成示例。','info')}<section class="empty-state">${icon('plug')}<h2>此能力尚未接入保存快照</h2><p>目前可查看收錄文件與來源狀態；正式對話、事件融合、交班寫入及 MCP 端點尚未接線。這不是查詢後的零筆結果。</p><div class="row between">${nav('overview','查看保存快照','class="btn secondary"')}<button class="btn" data-action="demo-mode">改用合成互動示例</button></div></section>`,labels[VIEWS.indexOf(route.view)],'能力尚未提供，與來源沒有資料是不同狀態。');
}
function snapshotView() {
  if (!['overview','sources','connections'].includes(route.view)) return unavailable();
  if (route.view==='connections') return connections();
  if (snapshotState==='loading'||snapshotState==='idle') return frame(`${notice('正在讀取本站保存的三份資料檔，不會連線政府網站。','info')}<section class="card" role="status" aria-busy="true"><h2>讀取保存快照中</h2><div class="skeleton wide"></div><div class="skeleton wide"></div><div class="skeleton short"></div></section>`,'查看本站保存快照。','介面預覽・唯讀資料入口');
  if (!snapshot || snapshotState==='error') return frame(`${notice(`讀取失敗：${e(snapshotError)}。這不是「零筆情報」，也沒有以合成資料代替。`,'error')}<section class="empty-state">${icon('alert')}<h2>目前無法核對保存快照</h2><p>請重試本站資料，或回到既有情報首頁。此操作不會重抓官方來源。</p>${button('重新讀取本站快照','reload')} <a class="btn secondary" href="../../">返回既有首頁</a></section>`,'資料暫時無法讀取。','保留失敗原因，不以空清單當成正常結果。');
  const s = inspectSnapshot(...bundle); snapshot = s;
  const sourceRows = s.sources.map(v=>`<tr><td>${e(v.source_name)}</td><td>${badge(v.source_health==='PASS'?'快照內取得成功':'快照內來源異常',v.source_health==='PASS'?'verified':'conflict')}</td><td>${e(v.window_completeness)}</td><td>${e(v.last_checked_at)}</td><td>${safeOfficialURL(v.source_url)?`<a class="icon-btn" href="${e(safeOfficialURL(v.source_url))}" target="_blank" rel="noopener noreferrer" aria-label="開啟${e(v.source_name)}官方頁面">${icon('external')}</a>`:'連結未通過檢查'}</td></tr>`).join('');
  const body = route.view==='sources'?`<section class="card"><h2>保存快照中的來源狀態</h2><div class="table-wrap" tabindex="0" aria-label="保存快照來源表，可橫向捲動"><table class="source-table"><thead><tr><th>來源</th><th>當時的取得狀態</th><th>窗口</th><th>最後核對時間</th><th>原始頁面</th></tr></thead><tbody>${sourceRows}</tbody></table></div></section>`:`<section class="metrics">${metric('收錄文件',s.items.length,'不是今日新增')}${metric('快照來源',s.sources.length,'目前固定五來源')}${metric('即時來源核對','未執行','本頁不重抓政府網站')}${metric('部署版本核對','未執行','不冒充端到端驗收')}</section><section class="card"><div class="section-title"><h2>保存文件列表</h2>${nav('sources','查看來源狀態 →','class="text-btn"')}</div><p class="muted small">僅展示前 20 筆／共 ${s.items.length} 筆。不是依重要程度排序，也不是完整搜尋服務。</p><div class="table-wrap" tabindex="0" aria-label="歷史文件清單，可橫向捲動"><table class="source-table snapshot-table"><thead><tr><th>原始文件標題</th><th>來源</th><th>官方資料日期</th><th>來源連結</th></tr></thead><tbody>${s.items.slice(0,20).map(i=>`<tr><td>${e(i.title)}</td><td>${e(i.source_id)}</td><td>${e(i.published_at||'官方未提供')}</td><td>${safeOfficialURL(i.official_url)?`<a class="icon-btn" href="${e(safeOfficialURL(i.official_url))}" target="_blank" rel="noopener noreferrer" aria-label="開啟${e(i.title)}官方來源（新視窗）">${icon('external')}</a>`:'不提供不安全連結'}</td></tr>`).join('')}</tbody></table></div></section>`;
  return frame(`${notice(`<strong>${e(s.status==='STALE'?'資料已過期':s.status==='PARTIAL'?'快照不完整':s.status==='UNKNOWN'?'時效無法確認':'快照核對時間在本地期限內')}</strong>｜保存時間 ${e(s.generated_at)}。${e(s.limitation)}`)}<div class="row between toolbar"><code>Collection ${e(s.run)}</code>${button('重新讀取本站快照','reload','','secondary small-btn')}</div>${body}`, '保存資料仍可查，但不冒充即時資訊。','下列內容來自本站既有快照；介面未替文件重新作官方驗證。');
}
function render(focus=false) {
  const pages = {overview,ask,event:eventView,handoff,sources,connections};
  root.innerHTML = route.mode==='snapshot'?snapshotView():pages[route.view]();
  document.title = `${labels[VIEWS.indexOf(route.view)]}｜GovIntel 互動預覽`;
  if (focus) document.querySelector('#main-content').focus({preventScroll:true});
}
async function refreshSnapshot() {
  request?.abort(); request = new AbortController(); const id = ++requestId;
  snapshotState='loading'; render();
  try { const result = await loadSnapshot(fetch, request.signal); if (id!==requestId || route.mode!=='snapshot') return;
    bundle=result.bundle; snapshot=result.view; snapshotState='ready'; snapshotError=''; render(); announce('本站保存快照已讀取，請注意資料時間與範圍。');
  } catch(error) { if (id!==requestId || error.name==='AbortError') return; snapshotState='error'; snapshotError=error.message; render(); announce('保存快照讀取失敗，並非零筆情報。'); }
}
function modal(title, content) {
  if (dialog.open) dialog.close();
  const trigger = document.activeElement;
  if (trigger && !trigger.id && trigger !== document.body) trigger.id = 'last-dialog-trigger';
  lastFocus=trigger?.id;
  dialog.innerHTML=`<div class="dialog-heading"><h2 id="dialog-title">${e(title)}</h2><button class="icon-btn" data-action="close-dialog" aria-label="關閉對話框" autofocus>${icon('close')}</button></div>${content}`;
  dialog.showModal();
}
function evidenceModal(ref, returnToReview = false) {
  if (!/^demo-(city|police|traffic):v[12]$/.test(ref)) return;
  const city = ref.includes('city'), hour = city?'18:00':ref.endsWith('v1')?'17:00':'16:00';
  modal('示例原文與版本', `${badge('合成文件・非真實官方公告','review')}<div class="code-card">${e(ref)}\n${city?'第 2 段・活動時間':ref.includes('police')?'第 4 段・道路管制':'第 3 段・道路管制'}</div><div class="quote new"><p>${city?'活動預定於 2026 年 9 月 20 日':'周邊道路自'}</p><strong>${hour} ${city?'開始。':'起實施管制。'}</strong></div><p class="muted small">片段只描述時間，沒有說明調整原因；不補造因果。</p>${notice('這裡沒有偽造官方網址。正式文件版本、雜湊與定位，仍需 #48 的資料流程接入。','info')}<div class="modal-actions">${button(returnToReview?'返回交班確認':'返回查證',returnToReview?'confirm-open':'close-dialog','','secondary')}</div>`);
}
function confirmModal() {
  if (!isAligned()) { announce('仍有衝突或已過期，不能保存成已確認。'); return; }
  reviewed=null;
  modal('核對並保存示例交班 v2',`${badge('只保存本機合成示例','review')}<p>請核對以下事實與版本。保存後不會改寫 v1，也不會發布到任何政府系統。</p>${sourceButton('demo-police:v2','警察局合成公告 v2','道路管制開始 16:00')}${sourceButton('demo-traffic:v2','交通局合成公告 v2','道路管制開始 16:00')}<p class="muted small">原文可先在事件頁查看；開啟文件不等於完成核對。</p><label class="check-row"><input type="checkbox" id="review-checkbox"><span>我已核對此示例的活動 18:00、管制 16:00 及兩份 v2 引用；不加入未被支持的調整原因。</span></label><div class="modal-actions">${button('取消','close-dialog','','secondary')}${button('確認並保存示例 v2','confirm-save','id="save-confirmation" disabled')}</div>`);
}
function resetModal() { modal('重設合成示例？','<p>只清除這個瀏覽器的示例追蹤與示例交班 v2。正式資料、其他網站與已下載檔案都不會修改。</p><div class="modal-actions">'+button('取消','close-dialog','','secondary')+button('重設示例','reset-confirm')+'</div>'); }
function setMode(mode) { location.hash=routeHash({...route,mode,view:'overview'}); }
root.addEventListener('change',ev=>{
  if (ev.target.id.endsWith('-mode')) setMode(ev.target.value);
  if (ev.target.id==='scenario' && SCENARIOS.includes(ev.target.value)) { reviewed=null; const next={...demo,scenario:ev.target.value}; if(!commitState(next)) demo=next; render(); document.querySelector('#scenario')?.focus(); announce('已切換合成情境，不會修改官方來源。'); }
  if (ev.target.id==='source-filter') { sourceFilter=ev.target.value;render();document.querySelector('#source-filter')?.focus(); }
});
root.addEventListener('submit',ev=>{if(ev.target.id!=='search-form')return;ev.preventDefault(); query=new FormData(ev.target).get('q').slice(0,200);followup=false;render();document.querySelector('#query-input')?.focus();announce(`示例資料符合 ${searchDemo(query,category).length} 筆，並非真實事件搜尋。`);});
root.addEventListener('keydown',ev=>{if(ev.target.getAttribute('role')!=='tab'||!['ArrowLeft','ArrowRight','Home','End'].includes(ev.key))return;ev.preventDefault();const values=['summary','compare','evidence'];const i=values.indexOf(tab);tab=values[ev.key==='Home'?0:ev.key==='End'?2:(i+(ev.key==='ArrowRight'?1:2))%3];render();document.querySelector(`#tab-${tab}`).focus();});
dialog.addEventListener('change',ev=>{if(ev.target.id==='review-checkbox'){reviewed=ev.target.checked?demoGeneration(demo):null;document.querySelector('#save-confirmation').disabled=!canConfirm(demo,reviewed);}});
dialog.addEventListener('close',()=>{reviewed=null;if(lastFocus)document.getElementById(lastFocus)?.focus();});
document.addEventListener('click',ev=>{
  if(ev.target.closest('.skip-link')){ev.preventDefault();document.querySelector('#main-content')?.focus();return;}
  const el=ev.target.closest('[data-action]');if(!el||el.disabled)return;const a=el.dataset.action;
  if(a==='close-dialog'){dialog.close();return;}
  if(a==='more'){modal('更多頁面',`<nav class="stack" aria-label="更多頁面">${nav('sources',`${icon('pulse')} 資料來源`,'class="btn secondary"')}${nav('connections',`${icon('plug')} AI / MCP 連接`,'class="btn secondary"')}<a class="btn secondary" href="../../">原有情報首頁</a></nav>`);return;}
  if(a==='snapshot-mode'){setMode('snapshot');return;}if(a==='demo-mode'){setMode('demo');return;}
  if(a==='reload'){refreshSnapshot();return;}
  if(route.mode!=='demo')return;
  if(a==='open-event' && EVENT_IDS.includes(el.dataset.id)){route.event=el.dataset.id;location.hash=routeHash({...route,view:'event'});return;}
  if(a==='evidence'){evidenceModal(el.dataset.ref, Boolean(dialog.querySelector('#review-checkbox')));return;}
  if(a==='tab'){tab=el.dataset.tab;render();document.querySelector(`#tab-${tab}`)?.focus();return;}
  if(a==='category'){category=el.dataset.value;followup=false;render();announce(`已篩選${category}示例。`);return;}
  if(a==='clear-query'){query='';category='全部';followup=false;render();document.querySelector('#query-input')?.focus();return;}
  if(a==='followup'){followup=true;render();announce('已顯示示例版本差異及來源限制。');return;}
  if(a==='watch') { const id=el.dataset.id;if(!EVENT_IDS.includes(id))return;if(demo.watched.includes(id)){announce('已在示例追蹤中，不會重複加入。');return;}if(commitState({...demo,watched:[...demo.watched,id]})){render();announce('已加入此瀏覽器的示例追蹤。');}return; }
  if(a==='confirm-open'){confirmModal();return;}
  if(a==='confirm-save'){try {const next=confirmDemo(demo,reviewed);if(commitState(next)){dialog.close();render(true);announce('示例交班 v2 已保存在此瀏覽器；v1 保留，沒有對外發布。');}else{dialog.close();render();}}catch{announce('來源版本或核對狀態已變更，請重新核對。');}return;}
  if(a==='history'){const version=Number(el.dataset.version);try{const payload=demoExport(demo,version);modal(`交班 v${version}・示例歷史`,`${badge('合成示例・版本不覆寫','review')}<pre class="code-card">${e(JSON.stringify(payload,null,2))}</pre>${button(`匯出示例 v${version}`,'export',`data-version="${version}"`,'secondary')}`);}catch{announce('此示例版本尚未保存。');}return;}
  if(a==='export'){try{const version=Number(el.dataset.version);const payload=demoExport(demo,version);const url=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)+'\n'],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`govintel-DEMO-handoff-v${version}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);announce('已匯出合成示例；檔案不會隨來源自動修改。');}catch{announce('沒有已保存的示例版本可匯出。');}return;}
  if(a==='reset'){resetModal();return;}
  if(a==='reset-confirm'){try{window.localStorage.removeItem(STORAGE_KEY);demo=initialDemo();storageError=null;storageAllowed=true;reviewed=null;dialog.close();render();announce('已重設此瀏覽器的合成示例。');}catch{announce('無法清除儲存空間，沒有宣稱重設成功。');}return;}
  if(a==='review-info'){announce('請核對右側示例草稿；來源仍有衝突時不能確認。');return;}
  if(a==='source-detail'){const s=DEMO_SOURCES.find(v=>v.id===el.dataset.id);if(!s)return;modal(s.name,`${badge('合成來源狀態','review')}<dl><dt>取得狀態</dt><dd>${e(s.status)}</dd><dt>涵蓋範圍</dt><dd>${e(s.coverage)}</dd><dt>最後核對（示例）</dt><dd>${e(s.time)}</dd></dl>${notice(s.tone==='verified'?'成功取得不等於資料涵蓋所有問題。':s.tone==='review'?'沒有讀完窗口，不可將零筆回答成沒有事件。':'連線失敗代表狀態未知，不可推論官方沒有新資料。')}`);return;}
  if(a==='discovery'){modal('外部待確認訊號',`${badge('2 筆合成示例・不可作官方結論','discovery')}<div class="card"><h3>活動接駁安排可能調整</h3><p class="muted small">尚未取得官方確認。</p></div><div class="card"><h3>媒體提及週末道路壅塞</h3><p class="muted small">未定位至官方事件，也不能推論現場人數。</p></div><p class="muted small">Taiwan Intel Dashboard 僅作發現來源；本預覽沒有呼叫其 API。</p>`);}
});
window.addEventListener('hashchange',()=>{if(dialog.open)dialog.close();route=readRoute(location.hash);request?.abort();++requestId;render(true);window.scrollTo(0,0);if(route.mode==='snapshot'&&(!snapshot||snapshotState!=='ready'))refreshSnapshot();});
window.addEventListener('storage',ev=>{if(ev.key!==STORAGE_KEY&&ev.key!==null)return;const latest=readDemo(storage);demo=latest.state;storageError=latest.error;storageAllowed=!latest.error;reviewed=null;if(dialog.open)dialog.close();render();announce('另一分頁已變更示例狀態，請重新核對。');});
setInterval(()=>{if(route.mode==='snapshot'&&bundle&&snapshotState==='ready'){const next=inspectSnapshot(...bundle);if(next.status!==snapshot.status){snapshot=next;render();announce('保存快照時效已改變。');}}},60000);
render();if(route.mode==='snapshot')refreshSnapshot();
