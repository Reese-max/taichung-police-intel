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

Issue #21 B 段還要求「每來源請求上限、主機併發上限、退避、退避截止與待重試狀態可查；429 尊重來源限制，不立即全站重爬」。這一組由 `intel_v2.detail_recheck_budget.py` 單獨負責，collector 只呼叫它，不自己重算：

- `plan_recheck_budget()` 對一批到期 target 做 admission 決定，逐筆核對（`RETRY_DEADLINE_EXCEEDED` → `BACKOFF_ACTIVE` → `SOURCE_BUDGET_EXHAUSTED` → `HOST_BUDGET_EXHAUSTED` → `ALLOWED`）。每一筆被放行的請求都會在該批次內扣掉同一來源與同一主機的額度，因此資料庫一次交回再多到期列，也不會超過設定上限。
- `record_recheck_budget()` 在每次實際請求後寫回 ledger：成功或暫時不可用（`UNAVAILABLE`）會清掉退避，`DEFERRED`（429／5xx）則累加 `failure_streak`，退避時間取本地指數退避與來源 `Retry-After` 的較大者，並以 `backoff_deadline_hours` 設下截止時間。
- `pending_retry_rows()` 輸出可查的待重試視圖（`deferred_until`、`deadline_at`、`seconds_until_retry`、`retry_deadline_exceeded`、`failure_streak`）；超過截止時間的項目不會自動再發請求，必須由 `acknowledge_recheck_budget()` 明確確認後才恢復。
- 儲存位置是 `migrations/0004_detail_recheck_budget.sql` 新增的 `detail_recheck_state.budget_state`（JSONB，只放 admission 決策與時間戳，不放任何內文）。被預算擋下的列只更新 `next_check_at` 與 `budget_state`，不動 `last_checked_at`、`document_version_id` 與 `status`，因此「尚未核對」不會被偽裝成「已核對無變更」；collector 對這些列回報 `status: "SKIPPED"` 加上 `reason`，而不是偽裝成一次成功的複查。

刻意界線：額度帳本以每個 target 的 `attempts` 時間戳為準，跨批次只以本輪候選集合彙總，因此跨輪的額度判斷最寬鬆只會落在一個 window 內的落差，不宣稱是全站共用同一個 host 帳本。這個上限是「序列化 collector 的每輪請求速率上限」，不是並行 worker 的 semaphore。

驗證：`tests/test_detail_recheck.py` 25 tests（含 `DetailRecheckBudgetTests` 11 tests）以及 `tests/test_detail_recheck_database.py` 8 tests pass。2026-09-22 另以隔離、短命的 PostgreSQL 17 Docker 容器執行 `python -X utf8 -m unittest discover -s tests -p "test_source_ingestion.py" -v`，包含 migration case 在內共 11 tests pass；容器於測試後清理，未使用既有資料庫。

刻意保留的界線：目前只有一次隔離 ephemeral DB migration 驗證，尚未在持久／正式 DB 執行排程，也沒有 7-day canary；因此不能宣稱 production recheck、公開部署或真實來源收件已完成。
