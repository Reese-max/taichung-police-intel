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

## 2026-10-20：完整正文複查與摘要依賴修正

本輪沿用既有 collector、版本分類器、snapshot blobs 與 handoff 流程，補上 B→C 的正確性缺口，沒有新增來源或改動排程。這是 Issue #21 可獨立驗收的一段，並非整份需求或正式部署的完成證明。

- `plan_recheck()`、`recheck_detail()`、`run_detail_rechecks()` 支援 `full_recheck_interval_hours`（預設 168 小時，不得短於一般 `interval_hours`）。一般核對到期時，最後成功取得正文的 `observed_at` 若已超過完整複查期限，或該時間未知，就移除條件式 headers 重新取得正文；仍遵守既有每輪／來源／主機額度與退避。這是第一個到期核對的策略，並非七天內一定成功的 SLA。
- collector 從既有 `last_result.classification.after`／`before` 恢復正文時間與語意欄位，不新增 migration。304、429 與失敗可更新核對時間，但不會把最後成功取得正文的時間往後推；舊資料沒有這項時間時，下次到期採完整複查。
- 標題、地點、狀態、範圍與官方時間欄位一併保存在前後版本及 handoff invalidation。修正會標記正確的舊 claim；相同資料重跑不再遺失欄位或重複要求核對。未提供的官方時間仍保持 null。
- 額外保存 `material_text_sha256`，只忽略空白前或句尾的 `.!?。！？` 差異。原始正文 hash、既有正規化 hash 與文件版本仍保留差異；`17:00 → 16:00`、`1.5 → 15` 仍要求核對。其他標點或語意不明的修改採保守待核對，未加入 LLM 或通用語意解析器。
- HTTP adapter 在初次或正文有變時列舉附件連結；正文不變時的實際附件核對已於 2026-10-28 補上，見下節。附件未全部成功核對時仍回報 `attachment_recheck_required=true`。

### 本輪驗收對照

| Issue 情境 | 證據與界線 |
|---|---|
| 同網址／標題／列表，正文更正 | HTTP fixture 的時間更正為 `MATERIAL_CHANGE`；定期移除 validators，避免永久依賴來源的 304。一般正文仍只提供 hash 異動，沒有自動抽取「管制開始時間」的欄位差異文案。 |
| 排版或標點變更 | 既有 `PRESENTATION_ONLY` 與新增句末標點 fixture 通過；其他標點仍可能要求人工核對。 |
| 正文不變、附件內容更正 | 2026-10-28 補上離線 fixture：正文 304 或同 hash 的 2xx 後，在剩餘預算內下載同網址附件，bytes SHA-256 異動為 `ATTACHMENT_CHANGED`，並要求複查。部分核對、限流與傳輸失敗明確保留未核對狀態；未做真實來源驗收。 |
| 首次看到上月文件 | 沿用官方日期／擷取日期分離，不以本輪時間補造官方時間；參見 `test_official_source_dates.py`、`test_date_metadata_projection.py`。 |
| 首次完整快照、相同資料重跑 | `BASELINE` 不產生 NEW；新增語意欄位重播檢查為 `UNCHANGED`。 |
| partial／失敗／單筆消失 | 沿用 last-known-good 與 `event_cancelled=false`；未新增任何自動取消或解除語意。 |
| 更正缺乏可確認關聯 | 依確切 source/stable key 與已確認文件版本關聯；不按主題改寫其他摘要。 |
| 靜態舊版前推時鐘 | 既有 `publication-freshness.test.mjs` 驗證不用重建即可過期、未知時鐘不保證時效；未新增 deployment receipt。 |
| Twinkle 索引較新、原始期別較舊 | 本輪未接入或變更 Twinkle；原始 dataset/resource 對應及 live canary 仍由 #14 驗收。 |
| 同源雙路徑 | 沿用 `test_public_event_fusion.py` 的獨立來源去重；未擴充來源數。 |
| 併發、重試與 delayed job | 沿用 advisory lock、rolling budget 與 handoff 來源時鐘；新增重啟／304／完整核對往返測試。真實 PostgreSQL 併發測試仍需要隔離的 `TEST_DATABASE_URL`。 |
| 公告→異動→舊摘要待核對→人工確認→新版匯出 | `test_semantic_correction_rechecks_only_dependent_handoff_and_exports_new_version` 為明確標示的合成公告重播：保留前後地點、僅影響相關摘要、舊版不變、新版另有編號及引用。既有 `scripts/handoff-state.py self-check` 提供可操作的跨日重播。 |

可由 Git Bash 重跑本輪相關驗收：

```bash
python -X utf8 -B -m unittest discover -s tests -p 'test_detail_recheck*.py' && python -X utf8 -B -m unittest discover -s tests -p 'test_handoff_state.py' && python -X utf8 -B -m unittest discover -s tests -p 'test_review_inbox.py' && node --test apps/web/tests/publication-freshness.test.mjs
```

未做：附件 bytes 持久化與獨立排程複查、一般正文的重要主張自動抽取與差異文案、完整 A 段 deployment/publication receipt 整合、Twinkle／新來源接入、實際部署／canary／延遲量測。本輪不啟動正式資料庫、不宣稱正式公告漏件已重現，也不把合成重播當成 production 證據。專案目前沒有設定獨立 lint、coverage ratchet、CRAP 或 mutation gate；沿用既有驗證器與測試，未安裝額外工具。

廣域驗證：已完整執行 `npm test`，原生 Windows 未全綠；既有 checkpoint／來源稽核測試使用 POSIX 的 `O_NONBLOCK`、檔案權限、directory fsync 與 Unix signal，另有需乾淨提交的 current-checkout gate。這些平台限制不藉本輪修改繞過；相關驗收命令與 Linux 全套補驗分開回報，不以局部綠燈冒充全套通過。

## 2026-10-28：正文不變時核對附件 bytes

- 保存 approved 附件 `url`、`content_sha256`、自身 validators、成功核對時間 `last_checked_at` 與最近嘗試時間 `last_attempted_at`，沿用既有 attachments JSONB 與 before/after，不新增 migration。新欄位對舊資料皆有才收；附件 URL／validators／核對與嘗試時間不參與文件版本 hash，避免僅 ETag 或時間更新造成誤報。既有附件首次取得 hash 只建立基準，以 `attachment_baseline_ids` 列出 ID，保留 `NOT_MODIFIED`／`UNCHANGED` 與原文件版本，不要求複查；往後已知 hash 改變、附件新增或移除仍會要求複查。
- 正文 304 或 2xx 且 body hash 相同時，優先核對尚無 `last_attempted_at` 的附件，再依最近嘗試時間由舊到新下載 bytes；同時間依 `attachment_id` 穩定排序。成功、404、429／5xx、逾時、URL／redirect 拒絕與超限均記錄嘗試時間，失敗不更新成功時間 `last_checked_at`、hash 或 validators。預算不足、`SOURCE_BACKOFF` 跳過與缺少 URL 不記為嘗試。時間隨分類結果保存並在下輪恢復，因此持續失敗的附件也會輪替，不再搶走每輪唯一附件額度；429／5xx 仍沿用來源退避。
- 共用 `_approved_url`、`_read_body`、手動 redirect 迴圈與 `request_count`。正文與附件的 redirect 均逐跳檢查 approved host allowlist，兩個已核准 host 之間允許 redirect；任何一跳未核准、非 HTTPS、非 443、超大宣告或串流回應均拒絕。collector 目前只傳單一 host，所以其實際下載仍限制於該 host。附件 304 保留已知 hash；沒有已知 hash 時採無條件下載，不以單獨 304 補造內容證據。
- 每次 GET（含正文、附件、redirect 與失敗嘗試）扣除同一輪／來源／host 的剩餘額度，沒有額外附件預算。collector 每筆完成後重新做既有 budget admission；同秒多次請求以 `budget_state.request_counts` 計數，舊 ledger 缺少此欄時每個既有時間戳視為一次，既有去重與 rolling window 保留。待重試視圖同樣顯示實際請求數。下載期間停用並於離開 GET 時還原 adapter 自動 retries，避免隱藏重試繞過額度；此流程沿用序列化 collector，不支援共用 session 的並行呼叫。
- 逐筆重新准入時，尚待處理的到期候選列仍視為同一批次：壞帳本只拒絕該列，拒絕後將乾淨的 ledger 同步回本輪 history，不會把健康列誤判成預算用盡；批次外無法解讀的歷史帳本仍拒絕同來源／host 的新請求。
- `attachment_checks` 逐筆列出已核／未核、HTTP status 與原因；預算不足為 `REQUEST_BUDGET_EXHAUSTED`。升級過渡期：main 時期存下、沒有 `url` 的附件列會標為 `MISSING_URL`，不發附件請求；持續收到正文 304 時，須等正文改版重新解析、補上 URL 後才開始核對。既有 2xx 路徑會重新解析正文，因此若較早收到完整 2xx，即使 hash 相同，也可能先補回 URL；本輪不新增舊資料補 URL 的程式。只有全部成功核對才設定 `attachments_checked=true`；已確認無附件時為空集合全數核對。
- 429／5xx 保存 `Retry-After` 秒數、保留該附件 LKG，停止後續附件並標為 `SOURCE_BACKOFF`。沒有已確認變更時分類為 `DEFERRED`；已有附件變更時保留 `ATTACHMENT_CHANGED`／`review_required`，同時由既有 ledger 套用退避。正文 304 的附件變更不會推進最後取得完整正文的 `observed_at`。
- 邊界：首次正文／正文變更仍只列舉附件，下載在正文不變時進行；不保存附件 bytes、不新增獨立排程、不調高既有額度。`DETAIL_RECHECK_MAX_PER_RUN` 預設為 3（2026-10-28 由 1 調高）：一輪可核一份正文加至多兩個附件，仍受 `per_source_limit = 3` 約束；額度不足時附件以 `REQUEST_BUDGET_EXHAUSTED`／`attachments_checked=false` 保持未核。要核更多附件，呼叫端可提高 `run_detail_rechecks(..., limit=...)`，同時確保 `budget_policy` 的 `per_source_limit`／`per_host_limit` 在 rolling window 內仍有餘額；每跳 redirect 也會消耗一次。現有 CLI 未提供此額度的參數或環境變數，設定同名環境變數不會生效。

例如正文加三個附件、沒有 redirect 或歷史用量時，呼叫端可明確設定如下；這是設定說明，本輪未變更排程呼叫端或預設值：

```python
run_detail_rechecks(
    connection, session, source_run_ids, observed_at,
    limit=4,
    budget_policy={"per_source_limit": 4, "per_host_limit": 5},
)
```

只提高為 `limit=2` 且來源／host 還有餘額時，每輪正文之外最多核對一個附件；三個可成功下載的附件會在三個到期輪次內各核對一次。不是三次立即重試，也不是在預設額度 1 下可完成附件核對的保證。

本輪僅執行下列離線驗收；未呼叫 provider、官方網站或 PostgreSQL。`online_collect.py` 的預算整合有使用實際函式 AST 與 fake connection/session 的離線測試；完整匯入與資料庫測試仍待 Linux 補驗（本機缺 psycopg）。

```powershell
C:/Users/Administrator/anaconda3/python.exe -X utf8 -B -m unittest discover -s tests -p "test_detail_recheck.py" -v
```
