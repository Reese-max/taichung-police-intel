# Issue #23 — persistent watch and versioned handoff

本地 first version 已接通：

- `state/v2-handoff-state.json` 保存跨日 watch、異動 invalidation 與 confirmed handoff history。
- confirmed handoff item 另保存 deterministic `claim_id`、source document version 與 evidence locator；來源更新的 invalidation 只列出真正引用舊版本的 brief/claim。
- `scripts/build-v2-shadow-brief.py` 從 persistent state 投影 `tracking_items`；零新異動不會清空 active watch，首頁最多顯示 5 件但 `tracking_total` 保留完整數量。
- `scripts/handoff-state.py` 提供 idempotent `watch`、`confirm`、`resolve`、`dismiss`、`status`、`export`，confirmed v1 不會被來源更新靜默覆寫。
- CLI 與瀏覽器的 `export` 都在匯出當下推導 stale／needs-review 警告：仍帶著未重新確認之來源異動的 claim 會標示 `NEEDS_REVIEW`、已確認 source version、最新版本（publication sync 給 source v，detail recheck 給 document version）與 evidence locator；已確認版本內容本身不被改寫。Markdown 另列出 `brief_sha256`、source document version 與 source hash，JSON 包成 `{handoff, review_warnings}`，讓 artifact 可回到 exact publication／source／evidence version。兩者匯出形狀相同，但 CLI state 與瀏覽器 state 仍各自保存，未做同步。
- 確認時會檢查已知來源版本、雜湊與 document reference；重新載入舊 publication 不得解除較新版本的 `NEEDS_REVIEW`。已核對的 document revision 仍保留於歷史紀錄，舊文件不能建立假的已核對版本；合法後續來源版本可另建下一版。原 invalidation 的 resolution receipt 不被後續確認覆寫。
- 已核對修正後，較早觀察時間的 publication／detail receipt 不會反向覆蓋已知版本；相同觀察時間中，明確記錄過的 before 版本仍視為舊資料。以實際觀察時間判斷重播，不把 hash 或 document ID 排成時間順序。較晚觀察到的合法同版本修正／回復仍可另建 `NEEDS_REVIEW` 並人工確認，原交班 history 與 resolution receipt 保留。
- 每個 watch 另保存 `last_source_observed_at`；即使來源 reference 未改變，也會保留已接納 publication／綁定 detail observation 的最新時間。相同時間的舊 before reference 在待核對與已核對狀態都不會回寫。合法後續 OLD → NEW → OLD → NEW 會產生獨立異動紀錄，重複觀察同一個待核對 reference 則合併，既有確認與 resolution receipt 均不可改寫。
- 變更事件現在投影 canonical identity、來源版本、內容雜湊與 deterministic `watch_id`；V2 Web 可由重點卡加入本機追蹤，跨頁載入時重播版本變更並回開 `NEEDS_REVIEW`。
- 靜態 Web 以 `localStorage` 保存單一使用者的 watch／handoff 草稿，提供確認 v1/v2、已處理／停止追蹤與 Markdown/JSON 匯出；不寫入公開檔案或伺服器。
- 來源 `FAILED`／`PARTIAL` 時，本機追蹤仍保留，卡片顯示該來源的擷取缺口。
- 瀏覽器從實際收到的 brief response bytes 計算 SHA-256，保存為 handoff 的 `brief_sha256`；payload 自稱的 hash 不取代此 binding。`RESOLVED`／`DISMISSED` 不因後續來源更新自動回到首頁追蹤，異動與已確認版本仍保留於 audit history。
- DB detail recheck 的 hash-only 結果可先送入 `scripts/review-inbox.py reconcile --input`；對已追蹤且已確認的 claim，明確執行 `scripts/handoff-state.py recheck --detail-rechecks <path>` 才會建立 idempotent invalidation、回開 `NEEDS_REVIEW` 並保留 affected claim。
- `python scripts/handoff-state.py self-check` 重播「加入追蹤 → 跨日保留 → 來源 v2 → NEEDS_REVIEW → 確認 v2 → exact locator」流程。
- 執行 `npm run check` 建立靜態頁面後，`node scripts/handoff-browser-replay.mjs /tmp/govintel-handoff-replay` 用實際 Chromium 操作同一個瀏覽器的加入追蹤、跨日／零異動保留、claim invalidation、JSON／Markdown 匯出、拒絕舊版本確認、來源缺口、確認 v2、重新載入歷史與終止追蹤。輸出 receipt、截圖、下載 artifact 與合成來源 fixture；兩版 evidence popup 的實際 response bytes 需符合匯出的雜湊。fixture 清楚標示為合成歷史／local-first replay，不呼叫外部來源或付費 provider，不代表現行官方公告、真實使用者或 production 驗收。

驗證結果：`HANDOFF_DEMO_OK cross_day=true invalidation=true versions=1->2 exact_locator=true export_warning=true`；handoff state tests 另驗證 claim 依賴回指。

邊界：GitHub Pages 仍是 static export；瀏覽器的 watch／確認／匯出只存在使用者本機，CLI state 與瀏覽器 state 尚未做同步。尚未宣稱 production deployment、多人協作、通知或離線檔案自動更新。
