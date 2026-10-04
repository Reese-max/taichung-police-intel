# Issue #107 — 個人化條件追蹤：本機保存與重開站未讀更新

本地 condition tracking 已接通：

- `apps/web/lib/local-conditions.js` 提供本地優先條件狀態：`emptyLocalConditions`、`validateLocalConditions`、`loadLocalConditions`、`saveLocalConditions`、`addLocalCondition`（idempotent）、`updateLocalCondition`、`cancelLocalCondition`、`matchPublishedItems`、`markLocalRead`、`getUnreadUpdates`、`projectLocalConditions`、`exportLocalConditions`。
- 條件格式支援 `source_id`、`keywords`、`district`、`category` 四種過濾欄位；同一事件同次變更命中多條件只呈現一次，並記錄 `hit_condition_ids`。
- 已讀紀錄以 `event_id#v{version}` 為 key，不同版本獨立追蹤；`markLocalRead` 支援多條件合併，不重複記錄 condition id。
- 狀態儲存在 `localStorage` key `govintel.v2.conditions.v1`；schema_version=1、mode=LOCAL_CONDITION_TRACKING，驗證失敗時停止載入，不覆蓋未知格式。
- 關站／重新整理後，瀏覽器端比對已儲存條件與目前 publication，只顯示命中條件且未讀的更新；純排版、重複文件與相同資料重跑不新增提示。
- 儲存禁用／quota／格式損壞／不支援 schema 有明確錯誤回饋，不假裝保存成功。
- `apps/web/tests/local-conditions.test.mjs` 涵蓋空狀態、驗證、idempotent 新增、更新、取消、條件匹配、多條件命中、已讀紀錄、未讀過濾、去重、儲存輪替、錯誤處理。

驗證結果：`apps/web/tests/local-conditions.test.mjs` 24/24 通過；`npm --prefix apps/web test` 328/328 通過。

邊界：本單只完成資料契約與本機比對層，UI 條件管理面板與 gateway 條件查詢工具為後續增量；不涉及 service worker、推播、帳號、雲端同步或網路請求。


## v6 接通與驗收修正（2026-10-05）

新增 `/tracking/` 可操作頁面，提供條件預覽、新增、修改、停用／重新啟用、逐次更新已讀、本機匯出及明確清除。條件、初始清單 IDs 與已讀版本保存在獨立 key，不覆寫交班資料；變更前重新載入現有 state，並處理跨分頁 storage 事件。寫入失敗不顯示保存成功；未知 schema 保留原資料。

修正等價條件重複新增、多條件已讀覆盖、停用後已讀保留與 material version 身分。初次保存與修改／重新啟用各建立初始清單；舊公告與已結束事件的補抓列歷史。純排版與任意 content hash 改變不產生重要提示。只標記實際展示的版本，後續版本與未載項目不被一起標記。

發布快照須簡報／歷史／來源狀態同世代才比對；PARTIAL、資料缺失、來源陳舊或過期不能推進成完整檢查。提供資料截止、来源警示與保留條件提示。候選來源未宣稱正式启用；沒有未讀只表示已載入範圍，沒有推論現實無變化。發布 feed 缺少 material version 的項目仍可列初始／歷史，但不能冒稱具備 live 實質更新辨識。

合成通勤重播使用 `demo:commute` scope，與 `published` 條件及已讀版本隔離。透過共用 `/data/public-query-replay.json` 展示版本、差異、每日時段與合成 locator；合成內容不冒用官方原文。`saveLocalConditionRequest` 作為公開查詢條件加入追蹤的共用保存 API；不建立追蹤網路服務。

驗證：Node local-conditions 35/35 通過；Chromium 實機驗證初始清單、R3 實質修訂、v2 精確已讀、reload 保留、v3 明文解除後條件仍啟用、取消停止提示以及最小 local state，沒有 pageerror。共享瀏覽器完整驗收由整合分支提供；本地證據 `/workspace/scratch/tracking-browser-check.json` 僅為此次工作區執行紀錄。
