# Issue #21 — detail recheck core

本地已加入 `intel_v2.detail_recheck` 的 transport-neutral B 核心，以及受限的 HTTPS transport adapter：

- `plan_recheck()` 以可設定 TTL 判斷是否到期，並在到期時保留 `If-None-Match` 與 `If-Modified-Since`。
- `classify_observation()` 保留 hash-only document version，區分 `UNCHANGED`、`PRESENTATION_ONLY`、`MATERIAL_CHANGE`、`ATTACHMENT_CHANGED`、`NOT_MODIFIED`、`DEFERRED` 與 `UNAVAILABLE`。
- 304 不宣稱附件未變更；暫時失敗保留 last-known-good，且不自動取消事件。
- 實質或附件變更標記 `review_required`，並保留 before/after 版本供既有 Review Inbox 與 handoff 層接續處理；附件只接受 approved host allowlist。
- `intel_v2.detail_recheck_http.recheck_detail()` 僅允許核准 hostname、HTTPS、443、手動受限 redirect、條件式 request header 與 2 MiB response；304、429/5xx、超大回應與未核准 redirect 都 fail closed。
- `migrations/0003_detail_recheck.sql` 與 DB collector 已接上 opt-in `detail_recheck_state`：只有 list-first collector 實際抓到的 detail 會註冊，排程每輪最多處理一筆到期 target，保存 snapshot blob、hash-only before/after classification、下次核對時間與 review flag；未註冊項目不會觸發全站重爬。
- DB collector 的回傳結果保留 hash-only `classification`；可直接以 `scripts/review-inbox.py reconcile --input` 轉成 `NEEDS_REVIEW`，不會自動改寫 handoff 或 canonical truth。

## 請求預算與退避（同一輪補上）

Issue #21 B 段還要求「每來源請求上限、主機併發上限、退避、退避截止與待重試狀態可查；429 尊重來源限制，不立即全站重爬」。額度與退避的規則全部由 `intel_v2/detail_recheck_budget.py` 決定，collector 只呼叫它並採用它回傳的游標：

- `plan_recheck_budget()` 對一批到期 target 做 admission 決定，逐筆核對（`INVALID_BUDGET_STATE` → `RETRY_DEADLINE_EXCEEDED` → `BACKOFF_ACTIVE` → `SOURCE_BUDGET_EXHAUSTED` → `HOST_BUDGET_EXHAUSTED` → `RUN_LIMIT_REACHED` → `ALLOWED`）。每一筆被放行的請求都會在該批次內扣掉同一來源、同一主機與本輪的額度；候選批次取 `max(limit, per_source_limit, per_host_limit)` 列，否則 SQL 的 `LIMIT` 會讓額度永遠沒有仲裁對象。
- 是否到期由既有的 `plan_recheck()` 決定一次，且在額度計算之前：未到期的列完全不進入額度計畫，因此不會佔用本輪唯一的名額，也永遠不會被寫成「已核對」；這一輪會把該列的 `budget_state.last_decision` 記為 `NOT_DUE`，而 `NOT_DUE` 與其他拒絕原因一樣必須能通過同一個驗證器，否則下一個到期週期會把健康的列誤判成 `INVALID_BUDGET_STATE`。
- 存在資料庫的 `budget_state` 若無法解碼，該列以 `INVALID_BUDGET_STATE` 拒絕一個 window，並被改寫成乾淨、可被 `pending_retry_rows` 列出、也可被 `acknowledge_recheck_budget` 接受的拒絕狀態（拒絕原因本身必須能通過同一個驗證器，否則該列會永久卡住且無法復原）；`budget_policy` 在開啟 row-lock transaction 之前先驗證。
- `record_recheck_budget()` 在每次實際請求後寫回 ledger：`DEFERRED`（429／5xx）累加 `failure_streak`，退避時間取本地指數退避與來源 `Retry-After` 的較大者，並以 `backoff_deadline_hours` 設下截止時間；確認過的成功狀態才會清掉退避，`UNAVAILABLE`／傳輸錯誤仍佔用額度但不會清掉既有 streak，否則 429／404 交錯的來源可以永遠把自己的截止時間往後推。
- `pending_retry_rows()` 輸出可查的待重試視圖（`deferred_until`、`deadline_at`、`seconds_until_retry`、`retry_deadline_exceeded`、`failure_streak`）；超過截止時間的項目不會自動再發請求，必須明確確認後才恢復。`scripts/detail-recheck-budget.py list|acknowledge` 是讀寫這個 ledger 的唯一受支援入口（需 `DATABASE_URL`），`acknowledge` 會一併把 `next_check_at` 拉回當下並回報新游標；`--self-check` 為離線自我檢查（涵蓋 SQL 欄位綁定與 ledger 往返），實際 DB 讀寫不在離線套件覆蓋範圍內。
- 儲存位置是 `migrations/0004_detail_recheck_budget.sql` 新增的 `detail_recheck_state.budget_state`（JSONB，只放 admission 決策與時間戳，不放任何內文，並以 CHECK 限制為 object）。被額度擋下的列只更新 `next_check_at` 與 `budget_state`，不動 `last_checked_at`、`document_version_id` 與 `status`，且 `next_check_at` 只會單調前進（`GREATEST`），因此「尚未核對」不會被偽裝成「已核對無變更」，也不會因時鐘校正而倒退重複複查。collector 對這些列回報 `status: "SKIPPED"` 加上 `reason`，Review Inbox 與 handoff 層會直接略過沒有文件證據的 SKIPPED 列。

刻意界線：候選批次仍依 `next_check_at` 最舊優先排序，所以單一來源若積壓很深，會佔滿本輪候選名額，其他來源要等積壓消化後才輪到（每輪最多消化 `candidate_limit` 列，不是永久 starvation）。改成 per-source round-robin 需要 window function，而該 repo 的候選查詢必須維持 `FOR UPDATE SKIP LOCKED`，兩者不相容，因此這次不做，只記錄。額度帳本以每個 target 的 `attempts` 時間戳為準，跨批次只以本輪候選集合彙總，跨輪失真上限為一個 window。這個上限是「序列化 collector 的每輪請求速率上限」，不是並行 worker 的 semaphore。整批複查（含 HTTP）仍在同一個 `FOR UPDATE SKIP LOCKED` transaction 內，這是既有 collector 的既有邊界：若整批在中途失敗，已送出的請求其帳務會跟著 rollback，下一輪會重試，額度在跨輪的失真上限為一批。

驗證：`tests/test_detail_recheck.py` 28 tests（含 `DetailRecheckBudgetTests` 14 tests）、`tests/test_detail_recheck_database.py` 15 tests、`tests/test_review_inbox.py` 與 `tests/test_handoff_state.py` 的 SKIPPED 回歸測試 pass。2026-09-22 另以隔離、短命的 PostgreSQL 17 Docker 容器執行 `python -X utf8 -m unittest discover -s tests -p "test_source_ingestion.py" -v`，包含 migration case 在內共 11 tests pass；容器於測試後清理，未使用既有資料庫。`migrations/0004_detail_recheck_budget.sql` 只做過離線自我檢查（`node apps/web/scripts/migrate.mjs --self-check` 統計到 4 個 migration），尚未在持久／正式 DB 套用。

刻意保留的界線：目前只有一次隔離 ephemeral DB migration 驗證，尚未在持久／正式 DB 執行排程，也沒有 7-day canary；因此不能宣稱 production recheck、公開部署或真實來源收件已完成。

正式資料庫以 transaction advisory lock 序列化預算准入與 ledger 更新；rolling window 同時計入未到期與其他來源的已保存請求，不只當次到期批次。無法解讀的歷史額度會拒絕同來源／host 的新請求。
