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
