# GovIntel 全國資料層：只直連機關官方系統

## W4 政府採購

`N-PCC-WEB` 與 `N-PCC-GPA` 已實作，由 `github-actions` 執行；本機受 PCC WAF 阻擋。本次 worker 僅使用主對話 2026-11-18 runner 留下的回應做離線驗收，尚未完成 runner 的兩次正式同步。以下 W4 說明取代早期章節的 PCC 待實作狀態及 GPA JSON 下載規劃。

- `pcc_gpa` 使用官方 `https://web.pcc.gov.tw/tps/tp/OpenData/showList` 所列 XML，授權來源同頁。官方說明每月 5 日產出兩個月前資料；檔名為 `tender_YYYYMM01/02.xml`、`award_YYYYMM01/02.xml`，分別涵蓋上、下半月。從今天往前兩個月的下半月起，讀取 `scope.months=24`；最新空檔或 404 最多向前試兩檔，空缺記入 warnings。游標使用不含副檔名的檔名；恢復時重建快照，維持跨檔去重結果。同一識別碼保留讀取順序最後一筆。
- XML 欄位與 GPA JSON 相同，推定為 GPA 適用標案子集，不能代表全量採購。近半年 GPA JSON 未另行實作。XML 單檔上限 5 MiB，解析前拒絕 DOCTYPE／ENTITY；輸出只含類型、名稱、案號、機關、日期及官方列表連結，捨棄地址、電話、聯絡人、廠商及金額。招標日期依指定欄位 `TENDER_SPDT`（截標日），決標使用 `AWARD_NOTICE_DATE`。
- `pcc_web` 的招標依警察、消防、交通局、臺中市政府逐一查詢，將窗口切為單一曆月，累計原始列數必須等於頁面總筆數。主對話實測 1／3 個月可用，1 年被拒；缺少結果表或查詢區間過長均回報錯誤，不視為零筆。決標以警察局、消防局、交通局、臺中市政府及窗口涵蓋的民國年查詢，依 `AWARD_NOTICE_DATE` 排序，從頁面讀取下一頁參數，使用第 6 欄（cells[5]）決標或無法決標公告日期，取開頭的民國日期；日期空白、格式錯誤或無效日期均報錯。第 5 欄招標公告日期不參與解析或早停；決標公告日早於窗口時停止。「無法決標」仍保留 kind=award，另記 PCC_NO_AWARD <pk> 警告。兩者均再次套用登記檔機關範圍，解析民國日期、標案名稱與更正公告警告，只保留 `tpam`／`atm`／`nonAtm` 的 pk 官方連結。
- 轄區（`jurisdiction`）依機關名稱開頭推定：以 22 個縣市名稱之一開頭（「台」視同「臺」）就填該縣市，其餘（中央機關、國營事業、國立學校）填「全國」。只看名稱前綴，不查機關隸屬。
- 網頁預設 `scope.daily_months=2`（當月及前一個曆月）；`national-sync.py --backfill` 改用 `scope.months=24`。XML 固定讀取 24 個月，其他來源不受此參數影響。工作流程只有手動 `workflow_dispatch` 的布林 `backfill=true` 才傳入此參數；排程不傳入。手動 backfill=true 的 job 上限為 210 分鐘，其餘為 150 分鐘；同步在第 150／90 分鐘後不再開始新來源（`--stop-after-minutes`），且剩餘每日寫入額度不足一整批（`MIN_SOURCE_BUDGET`＝100 筆 × 每筆預留列數）時，後續來源直接記 `SKIPPED` 而不抓取（2026-11-18 首次遠端同步：臺中 45 分、臺南 30 分、高雄 20 分後額度用盡，其後各來源仍整批抓取，於 120 分鐘被取消）（schedule 沒有 inputs.backfill，空值為 false）。24 個月回填粗估數十分鐘至 2 小時，尚非實測：僅請求間隔就約為 3 × 請求數 ÷ 60 分鐘，例如 1,000 次約 50 分鐘，另加 HTTP 回應、重試及資料庫寫入時間；實際筆數與耗時仍須由 runner 驗收。
- 兩個來源均至少間隔 3 秒。內容請求限於列表／下載路徑，連轉址也不可進入 pk 詳情頁。只存中繼資料與連結，不存全文、廠商或個資。
- robots 的特定例外有 2026-11-18 `github-actions` 證據：僅 301／302 且 `Location` 完全等於 `http://web.pcc.gov.tw/pis` 視為沒有 robots 資源，不跟隨該轉址。不同 Location 或其他轉址狀態均禁止；200 的 robots 規則仍須遵守。網路失敗保持禁止，5xx 沿用既有 `documented_open_api` 判定。登記自檢要求例外為同 host 的 HTTP／HTTPS URL、非 `/robots.txt`，且附觀察日期與 runner。

離線 fixture 位於 `tests/fixtures/national/pcc/`；來源與縮減／合成欄位列於該目錄 README。原始 award XML 沒有符合機關範圍的資料，因此測試的兩筆 scoped 機關為明確標註的合成值，不是額外的真實採購證據。測試涵蓋例外邊界、分頁計數、窗口／游標、XML 安全與欄位限制，以及 SqliteStore 同步兩次後 `changed=0`。preflight 仍是有界探測，固定探測已觀察的 `tender_20260902.xml`，不代表完整同步或最新資料驗收。

## 2026-11-17 方向修訂與定位

依使用者 2026-11-17 決定，資料層只直連各機關官方系統。data.gov.tw 是官方平台，可以作資料集目錄及官方資源發現入口。22 個地方議會全部登記，但 robots 明文禁止者不得抓取，列入待取得同意項目。任何 token 都不構成繞過 robots 的許可。

來源登記獨立放在 `docs/govintel/national-sources.v1.json`，目前共 29 筆。它是參考與發現層，不能作為現行治理回答的唯一證據；現行 catalog、policy、publication、`online_collect.py` 與 `schema_drift.py` 不改動。全國規模及不同議會格式需要獨立儲存與查詢，避免擴充既有 `S-###`、單一 origin 及發布容量限制時連動治理 hash 鏈。

公開輸出限中繼資料（標題、案號、日期、轄區、機關、授權）與官方連結，不公開全文、摘錄或逐字稿。內部保留 `official_url`、`provider_url`（官方提供端）、`fetched_at`、`content_sha256`、`license`、`rights_class`。本輪僅 W0：沒有建庫、部署、匯入或建立雲端資源。

## 官方來源

議會的官方／議事系統網址、資料種類、取得方式與網站條款照錄 #247 分支 `origin/docs/national-council-plan-20261115` 的 `docs/govintel/NATIONAL_COUNCIL_EXPANSION.md` 第二節（2026-11-15 盤點）；robots 狀態依 2026-11-17／18 的 CI 與使用者提供之台灣網路實測更新。本輪未重新連網查證。`rights_note` 保留該表網站文字；原表沒有提供宣告頁精確網址者，`license_source_url` 留 null，不把入口頁冒充授權證據。

- 22 個地方議會各一筆 `N-COUNCIL-{ISO 縣市縮寫}`。有 API 的臺中、基隆與嘉義縣 API 使用 `OFFICIAL_JSON_API`；新北使用 `HTML_HEADLESS`；其餘使用 `HTML` 或 `HTML_FILES`。完整狀態、資料種類與來源網址以登記檔為準。
- 嘉義縣保留單一 `N-COUNCIL-CYQ`：主來源是 `PLANNED_API`，`www_access` 另外標成 `BLOCKED_BY_ROBOTS_NEEDS_CONSENT` 且 endpoints 為空。API 根網址只標示官方 origin；CI 的 API robots 回 500，沒有 documented open API 旗標，維持禁止內容請求。W2a 不實作，待書面同意或確認公開文件化 API 並重新審核登記。preflight 仍只探測其 robots。
- `N-LY-ID20-BILLS`：官方 data.ly.gov.tw ID20 議案，政府資料開放授權條款第 1 版。preflight 只請求一頁；遠端是否採用分頁參數、資料 schema 與完整筆數仍待驗證。
- `N-LY-ID6-INTERP`：官方 ID6 委員質詢；已知缺口為「第 11 屆第 3–6 會期官方回空（2026-11-17 實測）」。這是指定會期的觀察，不推定所有會期永久停更，也不以第三方資料補齊。
- `N-PCC-WEB`：直接抓 web.pcc.gov.tw 官方公告檢索 HTML；每次請求間隔至少 3 秒，近 2 年只收機關名稱含「警察」「消防」「交通局」或以「臺中市政府」開頭的公告。不用廣泛的「交通」條件。容量預留 40MB，檢索表單欄位與正式路徑留待 W4；preflight 僅抓首頁。
- `N-PCC-GPA`：data.gov.tw 172020／172023，政府資料開放授權條款第 1 版。下載模板為 `https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile?fileName=GPAtender_YYYYMMDD.json` 與 `GPAaward_YYYYMMDD.json`，日期以 YYYYMMDD 代入。僅 GPA 案，不能代表全量採購；與公告網頁去重後共用 40MB。preflight 固定探測 20261117 招標檔，若檔案不存在只回報 HTTP 結果，不改抓多天。
- `N-ODP-CATALOG`：data.gov.tw 資料集 6564，`https://data.gov.tw/datasets/export/csv`。只篩臺北、新北、桃園、臺中、臺南、高雄的警察、消防、交通、治安資料集。W6 儲存目錄中繼資料；需要資料本身時只從各機關的官方資源網址取得，逐一核准 host、授權及 robots，不爬被禁的市府入口或透過轉址繞過限制。
- `N-TDX-EVENTS`：保留六都即時路況事件，30 天保留期；未設定 `TDX_CLIENT_ID`／`TDX_CLIENT_SECRET` 時記錄 `NOT_CONFIGURED`。W0 僅探測官方根網址，不申請憑證。
- `N-TWINKLE-MANUAL`：保留作開發時人工交叉驗證工具，不納入自動資料層或 Actions；與官方路徑算同一來源，不能當獨立佐證。帳號、授權與額度依既有紀錄仍待確認。

## 執行位置

來源登記的 `runner` 僅接受 `github-actions` 與 `xisx-pc`。臺北、新北、桃園、屏東、宜蘭議會指定由 Windows 主機 **xisx-pc**（台灣 IP）排程擷取；其他來源維持 GitHub Actions。

2026-11-19 起，xisx-pc 以 GitHub 自架 runner（Windows 服務，`NT AUTHORITY\NETWORK SERVICE`，標籤 `xisx-pc`）執行 `.github/workflows/national-sync-xisx.yml`：
- 每日 00:20 UTC（臺北 08:20）同步 `runner=xisx-pc` 的來源，在每日額度重設後、02:17 UTC 的 GitHub Actions 同步之前執行，讓只能由 xisx-pc 抓取的臺北議會先取得額度（2026-11-20 起；原為 06:30 UTC 排在 GitHub 之後，回填期間分不到額度）。
- 使用獨立的 concurrency group，runner 離線時不會卡住 GitHub Actions 的同步。
- 手動執行可勾選 `dry_run`，只抓取、不寫入。

repo 是公開的，因此只由 schedule／workflow_dispatch 觸發，並把外部貢獻者 PR 的 workflow 設為一律需要核准。

2026-11-17／18 實測：GitHub Actions（美國）對下列五站皆發生 `URLError`，Oracle 日本主機也連不到；使用者提供的本機、lab5090、lab301、lab6000、xisx-pc 台灣網路測試皆可連線，因此按使用者決定視為國外 IP 存取限制，改由台灣節點執行。

| 議會 | 官方 host | 台灣網路 robots 回應 |
|---|---|---|
| 臺北 | `ifddoc2.tcc.gov.tw` | 404 |
| 新北 | `bms1.ntp.gov.tw` | 404 |
| 桃園 | `www.tycc.gov.tw` | 302 至 `/TC/404.html`；終點 HTTP 狀態待驗證 |
| 屏東 | `www.ptcc.gov.tw` | 404 |
| 宜蘭 | `www.ilcc.gov.tw` | 404 |

preflight 依環境變數 `NATIONAL_RUNNER` 選取該 runner 的來源，預設 `github-actions`，並在報告頂層記錄 `runner`。GitHub Actions 規劃 22 個 robots 與 22 個頁面目標；`NATIONAL_RUNNER=xisx-pc` 規劃 5 個 robots 與 5 個頁面目標。URLError／timeout 記為 `UNREACHABLE_FROM_RUNNER`；robots 無法連線時，其依賴頁面也記此狀態及 `blocked_by=robots_fetch`，不聲稱曾請求頁面，也不當成明文禁止。

CI 證據來自 `national-preflight-44096423260/egress.json`；台灣與 Oracle 日本對照來自本次使用者提供的實測結果。本輪只讀取既有收據及執行離線驗收。

## robots 與同意

| 對象 | 登記狀態與處理方式 |
|---|---|
| 嘉義市議會整站 | `BLOCKED_BY_ROBOTS_NEEDS_CONSENT`；endpoints 空，只抓 robots；對主要搜尋引擎的例外不適用本工具。 |
| 嘉義縣議會 www | `www_access.status = BLOCKED_BY_ROBOTS_NEEDS_CONSENT`；endpoints 空，只抓 robots。API 子網域獨立查核，不沿用 www 結論。 |
| 澎湖縣議會附件目錄 | `PARTIAL_ROBOTS`；可檢查首頁，禁抓附件。原總表未列精確目錄，先停用所有附件與 SharePoint 資源。 |
| 立法院 ppg.ly.gov.tw | robots 全禁，不抓；改用 data.ly.gov.tw 官方 API。 |
| data.kcg.gov.tw、data.tainan.gov.tw | robots 全禁，不爬入口；若資源仍在相同被禁 host，也不抓，待同意或其他官方資源位置。 |

上述被擋資源須向主管機關發函取得書面同意後，再審核同意的 host、路徑、用途與速率，更新登記及存取策略後才加入。即使最新 robots 暫時回空或開放，也不自動解除已登記的禁止狀態。

preflight 與 `intel_v2/http_fetch.py` 的 `RobotsPolicy` 共用判定。HTTP 狀態與轉址依本輪指定的 RFC 9309 處理方式，另有明確限定來源的 5xx 例外：

- 2xx 空內容（含純空白）全部允許，記 `EMPTY_ROBOTS`。臺中 `yishi.tccc.gov.tw` 的 CI 回應為 200、僅 CRLF，修正先前誤判。
- 3xx 最多跟隨 5 次轉址，可跨 host，但每一跳都必須是 HTTPS，拒絕帳密 URL 與非標準 port。終點 2xx 解析內容，終點 4xx 全部允許。報告保存轉址鏈與 `final_url`；缺少 Location、超過上限或不安全轉址皆停止並禁止內容擷取。
- 所有 4xx（含 401／403／404／410／429）視為 robots 資源不可用而允許，記 `NO_ROBOTS_RESOURCE`。
- 5xx 預設全部禁止，記 `ROBOTS_SERVER_ERROR`；連線失敗也全部禁止，preflight 另記 `UNREACHABLE_FROM_RUNNER`，避免與 robots 明文禁止混淆。
- 僅 `documented_open_api: true` 的來源遇到 5xx 時允許，記 `ROBOTS_SERVER_ERROR_OPEN_API_ALLOWED`。旗標只設於 `N-LY-ID20-BILLS`、`N-LY-ID6-INTERP`、`N-PCC-GPA`、`N-ODP-CATALOG`：理由為機關以公開文件化開放資料宣告授權程式存取，而 5xx 並非明文禁止。這是本專案明定的例外，不宣稱為 RFC 的 5xx 預設行為，也不適用於連線失敗。
- 明文 Disallow 一律遵守，open API 旗標不能覆蓋。例外按來源判斷：同在 `web.pcc.gov.tw` 的 GPA 下載與一般採購 HTML 不共用例外。登記自檢拒絕不合法 runner、非布林旗標及 open API 來源標為 `BLOCKED_BY_ROBOTS_NEEDS_CONSENT` 的矛盾設定。

移除採購網「轉到首頁就允許」的特例；必須實際取得轉址終點回應。台南、彰化、桃園及採購網既有 302 收據均保留為 `REDIRECT_UNVERIFIED`，不把未驗證終點當成 404。`data.ly.gov.tw` 與 `data.gov.tw` 的 CI robots 500 適用指定來源的例外；`api.cyscc.gov.tw` 的 500 不適用。

共用判定使用 stdlib robots parser；含萬用字元、結尾錨點或混合 Allow／Disallow 的複雜規則，以及過大、HTML 或無法判讀的內容，保守視為未驗證並跳過頁面（`SKIPPED_ROBOTS_UNVERIFIED`）。因此這不是完整 RFC 規則解析器；不能把跳過視為成功或空 robots。解析成功時也尊重 Crawl-delay，且 preflight 每個 host 的請求間隔不低於 3 秒（包含 robots 轉址）。

## D1 權限待使用者 wrangler login

CI `national-preflight-44096423260/d1-permissions.json` 實測 `wrangler d1 list` 回 `LIST_FAILED`、`can_list: false`、結束碼 1。依本次確認，現有 token 沒有可用的 D1 權限；尚無列出、寫入或遠端 FTS5 的成功驗收。

D1 後續移至 W1，由使用者在本機執行 `wrangler login` 後處理。本輪不登入、不修改憑證、不建立雲端資源；既有 workflow 的唯讀列出探測不構成建立或寫入授權。

## 網站條款風險

robots 允許不代表網站已授權重製或再利用。#247 記錄高雄為「僅供個人、非商業用途，不可重製、改作、散布」，基隆為「未經同意不得重製、散佈」；多個議會為 All Rights Reserved／版權所有，部分宣告頁查不到。新竹市有網站資料開放宣告；桃園及金門區分著作權法第 9 條標的與其他內容，但不能因此概括所有議事內容的法律性質。

公開只留中繼資料與官方連結是既定資料界線，不代表已排除所有權利風險。相關重製、內部索引與再利用範圍仍須取得機關書面同意或法律意見。採購網 HTML 受採購網著作權聲明限制（非開放授權），聲明精確頁址與逐條原文待核；GPA 開放授權只適用該資料集，不能外推到全部公告網頁。TDX 條款仍待核。

## 容量預算

| 項目 | 已知議案數 | D1 估算／預留 MB |
|---|---:|---:|
| 臺中 | 15,496 | 12.40 |
| 基隆 | 約 4,998 | 4.00 |
| 嘉義縣 API | 1,246 | 1.00 |
| 高雄本會期 | 1,636（1,537＋99） | 1.31 |
| 其餘 18 個議會議案 | null，W2 盤點 | null；全域預留 140 |
| 會議等其他議會中繼資料 | W2 盤點 | 全域預留 30 |
| 立法院 ID20／ID6 | W3 盤點 | 50／5 |
| 採購近 2 年（含 GPA 去重） | W4 盤點 | 40 |
| 六都目錄中繼資料 | 約 2,300（歷史紀錄） | 2 |
| TDX | W5 盤點 | 1 |
| Twinkle 人工工具 | 不匯入 | 0 |

合計 **286.71MB**，含全域預留 `capacity_reserve_mb = 170`，低於 400MB 守門。議會已知值按每筆約 0.8KB（含索引）估算；基隆是否有約 5,000 筆回傳上限、高雄歷史筆數都未確認。未知值維持 null，不能當成零筆或已完整估算；W2 盤點後重算，超出預算即縮範圍或停止匯入。逐字稿與議事錄段落不計入 D1。

## 分批實作

### W1 儲存與載入

`national/schema.sql` 與治理 catalog 分離：`sources` 保存來源登記雜湊與授權，
`records` 以 `(source_id, external_id)` 去重，`records_fts` 使用 FTS5 trigram 索引標題及案號，
insert／update／delete trigger 保持同步；`runs` 保存狀態、寫入收據與可重播的頁面游標。
schema 可重複套用。fixture 是人工測試資料，不代表官方實際資料。

每日 UTC 寫入上限 **99,000 列**（2026-11-23 由 90,000 調高：D1 帳目已和本地 `runs` 對上，只差 8 列；免費上限 100,000，保留 1,000 列給稽核列與人工查詢），容量超過登記檔的 **400MB（400,000,000 bytes）** 就拒絕資料寫入。
每筆邏輯資料約 3 列尚未含完整索引成本，W1 暫以 **64 列／筆**保守預留（2026-11-19 依 D1 實測每筆新增 6.0 列，改為 **16 列／筆**，保留更新時改寫索引與 FTS 的餘裕；每日上限當時仍為 9 萬列，因 D1 後台 24 小時寫入量比本地帳高約 27%；2026-11-23 重查已一致，改為 99,000 列），來源更新預留 2 列，
另保留每個 run 的稽核列與主鍵索引 2 列。資料寫入以 D1 `meta.rows_written` 回填；
SQLite 用 `total_changes`，Wrangler 本機版本省略寫入 meta 時，在同一批次前後讀 `total_changes()`。
本機計數不代表遠端 D1 計費數字。實際成本仍須在 W2 大量匯入前依遠端收據校正；
schema 初始化成本另列於報告，不冒充來源匯入列數。

未變更資料不產生 upsert，因此重跑 `changed=0`、`rows_written=0`；稽核列另計入每日預算。
若連稽核預留列都不足且尚未寫資料，只輸出停止報告（`audit_recorded=false`），避免反覆排程耗盡預算。
公開內容依各來源 `public_fields` 白名單驗證，來源識別、種類與權利分類也納入內容雜湊，
`fetched_at` 不參與雜湊。禁止全文、摘錄、非 HTTPS 或非官方 host；SQL 用 UTF-8 hex literal
保留單引號、反斜線、NUL 與換行。批次含本機計數語句仍小於 100KB。

預算不足記 `BUDGET_STOPPED`、exit 0；容量超限記 `CAPACITY_BLOCKED`、exit 1。
只有整頁成功才前進游標；部分批次已提交時重播該頁，以雜湊跳過已完成資料。
完成一輪後下次從頭檢查變更。W1 採單一寫入者：workflow concurrency 不取消進行中的 run；
跨 runner 同時寫入、程序被強制終止或遠端已提交但回覆遺失時，尚無分散式預算保留與復原機制，
跨 UTC 日的批次尚按開始日計入；跨日前會停止新批次，部署排程應避開午夜，
正式啟用另一個 writer 前需補上協調，不能把本機收據當成完整帳戶用量。

本機執行（需已安裝 Wrangler；不新增專案套件依賴）：

```sh
# 專案根目錄；config 明確指定 D1 binding。
wrangler d1 execute govintel-national --local --config workers/query-gateway/wrangler.jsonc --file national/schema.sql
python -X utf8 scripts/national-sync.py --source N-COUNCIL-TXG --adapter fixture --fixture tests/fixtures/national/txg.json --store d1-local
# 完全使用 stdlib SQLite，連跑兩次可驗證冪等。
python -X utf8 scripts/national-sync.py --source N-COUNCIL-TXG --adapter fixture --fixture tests/fixtures/national/txg.json --store sqlite:nat.db --apply-schema
python -X utf8 scripts/national-sync.py --store d1-local --check-guards
# stdout 只有 SQL；JSON 報告送 stderr；不開啟資料庫、不執行 SQL。
python -X utf8 scripts/national-sync.py --source N-COUNCIL-TXG --adapter fixture --fixture tests/fixtures/national/txg.json --dry-run --apply-schema
```

`NATIONAL_RUNNER` 預設 `github-actions`，不相符的來源在報告標記 `RUNNER_MISMATCH`。
W1 唯一 adapter 是明確指定的 fixture；W2a 起未指定 adapter 時會同步已註冊且 runner 相符的來源。
`.github/workflows/national-sync.yml` 每日排程及手動執行皆須先設定 repository variable
`NATIONAL_SYNC_ENABLED=true`；未開啟前整個 job 跳過。token 需 **Account → D1 → Edit**，
使用既有 `CLOUDFLARE_API_TOKEN`、account ID 與 Wrangler 4.149.0。
workflow 依序套 schema、檢查守門、同步已有 adapter 的來源，報告以 `always()` 上傳。
本次實作未操作雲端；schema 與 workflow 啟用由主對話驗收後處理。

Worker 增加 `GET /national/search` 與 `GET /national/record/{id}`，沿用 CORS、rate limit 與 JSON 回覆。

關鍵字 3 個字以上（走全文索引）而且是第一頁時，`/national/search` 會另外回 `total` 與 `total_capped`：命中總數最多算到 1,000，超過時 `total_capped` 為 true。2 個字的關鍵字走 `LIKE` 比對，算總數要掃整張表（2026-11-23 實測讀 81,991 列，免費額度每天 500 萬列），所以不算總數。全文計數實測每命中一筆約讀 3 列，最多約 3,000 列。
搜尋預設 20 筆、最多 50 筆，3 字以上使用已跳脫的 FTS phrase，1–2 字使用已跳脫的 LIKE；
日期 DESC／id DESC keyset 游標支援缺日期資料，錯誤游標回 400。資料庫缺失或查詢失敗回
503 `NATIONAL_STORE_UNAVAILABLE`。只投影公開白名單欄位，不回傳內部 hash 或全文。
`/capabilities` 以額外 `national` 物件描述路徑及 binding 是否存在，既有 capabilities 語意不變；
binding 存在不代表資料新鮮度或資料庫健康驗證。

| 批次 | 範圍與驗收重點 |
|---|---|
| W0 | 官方登記、robots gate、preflight、共用 transport；本輪範圍。 |
| W1 | D1 schema、匯入與查詢骨架；每日寫入 9 萬列（2026-11-23 起 99,000 列）、容量 400MB 守門；驗證冪等性。 |
| W2a | 臺中、基隆議案 API；完整分頁驗證與中繼資料匯入。嘉義縣因 robots 500 且非文件化開放 API 而延後。 |
| W2b | 六都其餘一般網頁：臺北、桃園、臺南、高雄；新北另列 W2d。 |
| W2c | 其他縣市；blocked 來源先取得書面同意，澎湖附件維持停用，盤點未知筆數。 |
| W2d | 新北 Blazor Server 無頭瀏覽器；確認會話與欄位，不繞過 robots。 |
| W3 | 立法院 ID20、ID6，明示官方空白會期與資料完整性。 |
| W4 | 官方採購公告檢索 HTML、GPA 下載、篩選與去重；每次間隔至少 3 秒。 |
| W5 | TDX 憑證、事件及未設定時的跳過狀態。 |
| W6 | data.gov.tw 六都相關目錄；各機關官方資源另行查核。 |
| W7 | 從各議會官方系統取得逐字稿／議事錄，建立本機 SQLite FTS5；目前 #247 僅確認臺中有議事錄 API，其餘依 W2 盤點。全文不進 D1、CI 發布或公開頁面，受 robots 與網站條款限制。 |
| W8 | 全國查詢前端與來源說明；只公開核准中繼資料與官方連結。 |

### W2a 臺中、基隆官方議案 API

`txg`、`kee` 已註冊，登記狀態為 `IMPLEMENTED_API`（實作完成，不代表真實連網驗收完成）。
依主對話提供的 2026-11-18 官方回應結構製作離線 fixture；本 worker 不連網、不部署。
未指定 `--source` 時只同步已註冊且符合 `NATIONAL_RUNNER` 的來源；指定來源可自動選擇 adapter，
亦可明確使用 `--adapter txg` 或 `--adapter kee`，必須與來源相符。
`--adapter fixture` 保留離線用途；網路 adapter 的 `--dry-run` 仍會讀取 API，但不寫資料庫。
單獨 `--apply-schema` 維持只建 schema，不觸發網路同步。

| 欄位 | 臺中 TXG | 基隆 KEE |
|---|---|---|
| 入口 | GET `https://yishi.tccc.gov.tw/api/Proposal/FrontList?pageNumber=N&pageSize=200` | POST `https://www.kmc.gov.tw/kmcwebapi/motion/getlist`，JSON `{}` |
| `kind` | `bill` | `bill` |
| `external_id` | `billId`（GUID） | `kmc-` + UTF-8 `sha256(expkd\|seqkd\|seqno\|3c\|1c)[:24]`，欄位均來自 `sm1_` 前綴 |
| `title` | `proposalRationale.strip()[:200]`；空白時改用 `billType + billNumber`，同樣最多 200 字 | `sm1_3c.strip()[:300]` |
| `case_number` | `session + " " + billNumber` | `sm1_title` |
| `event_date` | null，官方回應沒有日期 | null，官方回應沒有日期 |
| `official_url` | `https://yishi.tccc.gov.tw/proposals/{billId}` | `https://www.kmc.gov.tw/kmcweb/#/motion`（只有列表頁） |

兩者共用 `http_fetch.get`，從登記讀取白名單、robots 與至少 3 秒間隔；
每個 adapter 使用 `GovIntelNationalBot/1.0` 識別與獨立 RobotsPolicy，
`documented_open_api` 沿用來源設定，不放寬 robots 禁止。
臺中每頁上限 2MiB、基隆單次 8MiB；POST 使用 JSON Content-Type，任何 3xx 皆拒絕且不跟隨。
既有 GET 預設與 retry 規則維持不變。HTML 全文 `proposalRationaleSort`、議事錄、
決議、連署人與其他原始欄位不進 record；只保留以上投影。

臺中先讀完最多 100 頁，檢查頁碼、穩定的總頁數／總筆數、跨頁重複 ID 與實際 `totalCount`，
全部通過才交給 loader 寫入。錯誤或 robots 禁止時不寫 sources／records，仍保留失敗 run 稽核。
記憶體只保留經投影的中繼資料頁；預算中止的游標是下一個未完成頁碼，部分頁批次可重播。
新程序續跑會重新讀完並驗證全部頁面，再從游標頁寫入；完成後下一輪從頭比對雜湊。
頁碼不是官方快照版本：若兩次執行間頁面排序改動，續跑可能漏掉前頁新增資料，需下一次完整輪巡補齊。
相同筆數但擷取期間持續變動的資料也無法證明快照一致性。

基隆沒有官方 ID；完全相同組合依出現順序加 `-2`、`-3` 等後綴。
案由或提案人等雜湊輸入一改就成為新的一筆，舊資料仍保留；不推定官方刪除，也不自動清除。
以 `|` 串接是本次指定規格，欄位本身含分隔符時存在歧義，雜湊也不是官方唯一鍵。
回傳至少 4,990 筆時，JSON run 報告附 `warnings: ["POSSIBLE_ROW_CAP"]`；
既有 runs 表不新增 warnings 欄位，須保存 CLI `--report` 產物取得警告。
主對話觀察到 4,998 筆，是否存在 5,000 上限及屆別查詢參數均未驗證；本輪不讀官方 JS、不猜參數或繞過上限。

基隆網站條款「未經同意不得重製、散佈」仍有再利用風險；只存中繼資料與官方連結是使用者指定界線，
並非完整權利授權。標題最多 300 字，短案由可能全部落入標題；不另存完整案由欄位。
嘉義縣 API robots 回 500，且 `documented_open_api=false`，W2a 不寫擷取器；
須先取得議會書面同意，或確認為公開文件化 API 並重新審核來源設定後再做。

離線驗收使用注入的假 session，驗證欄位、筆數守門、POST 轉址拒絕、robots 拒絕、
重複 ID、警告、頁碼續跑，以及 SqliteStore 完整同步兩次後第二次 `changed=0`。
真實 API 筆數與兩次 `d1-local` 同步由主對話另外驗收；仍受既有每日預算與容量守門約束，
`BUDGET_STOPPED` 不代表全量匯入完成。

### W2b 臺南、高雄、臺北官方議案列表

`tnn`、`khh`、`tpe` 已註冊；狀態 `IMPLEMENTED_API`／`IMPLEMENTED_HTML` 只表示離線實作，
不代表真實全量匯入驗收。fixture 縮減自 2026-11-18 台灣 IP 的官方原始回應，檔頭記錄來源；
HTML 保留 hidden 狀態與列表結構，計數與選單縮至測試資料範圍。沒有抓取任何詳情頁。
臺北維持 `runner=xisx-pc`；在 github-actions 指定臺北時回 `NOT_CONFIGURED`／`RUNNER_MISMATCH`。

| 欄位 | 臺南 TNN | 高雄 KHH | 臺北 TPE |
|---|---|---|---|
| `external_id` | `tnn:` + ProNo GUID | `khh:` + hidProposalSN；不以加密網址為 ID | `tpe:` + SYSN |
| `kind` | KidTitle 前半：市府提案 → bill_gov、議員提案 → bill，其餘 bill_other | 分頁名：市政府提案 → bill_gov、議員提案 → bill，其餘 bill_other | 案別去除所有空白：市府提案 → bill_gov、議員提案 → bill，其餘 bill_other |
| `title` | CaseCause，最多 300 字 | 案由，最多 300 字；分組標題不是資料列 | MainTable 的 text 欄 |
| `case_number` | GradeTime + KidTitle + 第 CaseNumber 案 | 分組標題 + 第編號號 | 已結案：`第{屆}屆結案 案號{acno}`；未結案：`未結案 案號{acno}` |
| `authority` | OrgTitle，最多 200 字 | 主辦單位／提案議員，最多 200 字，可空白 | 沿用來源機關 |
| `event_date` | DoneDate 的 yyyy/mm/dd 轉 ISO；空值為 null | null | null |
| `official_url` | `https://www.tncc.gov.tw/motionpage1.asp?ProNo=...` | onclick 的 Detail_RWD.aspx 連結；無法解析時用入口 | `https://ifddoc2.tcc.gov.tw/TCCMIS_Front/` |
| 游標 | `grdno-topage` | 會期明碼 | 兩位數屆別（如 `12`），最後為 `open` |

三站共用既有 UA、robots、請求間隔、白名單及有界 transport，每次回應最多 8MiB。
`http_fetch.get(form_body=...)` 用 requests 的表單編碼，與 `json_body` 互斥，限 POST；
所有 POST 3xx 仍直接失敗，cookie 沿用同一 requests.Session 的預設 cookie jar。
先驗證整輪有界中繼資料，再提供 loader 寫入頁面；不快取 HTML 或詳情全文。
新程序續跑重新查詢整輪、驗證後跳至保存游標；來源排序或會期集合變動仍可能使續跑缺漏，
完整輪巡才能補齊。既有每日寫入上限（2026-11-23 起 99,000 列）／400MB 守門不變，`BUDGET_STOPPED` 並非全量完成。

臺南先查官方屆別清單，再逐屆以 pagerow=1 的 PagesAll 取得確切總數，列表每頁 1,500 筆。
檢查每頁實際筆數、PagesAll 與跨頁 ID，不將最後不足一頁錯算為整頁；不符即失敗。
每屆最多 100 頁、屆數最多 100。JSON 用 utf-8-sig 去 BOM，DoneDate 空值不推定日期。
raw count_g1..g4 的總數為 2,437／2,143／5,999／7,861，合計 18,440；
這是提供的歷史探查值，本 worker 未重新連網驗證。逐屆 count probe 會比純列表多一個請求。
www robots 轉向 blank.asp 的終點為 HTML；bill 主機 robots 回 404；執行時仍重新檢查各請求主機。

高雄 Statistics 的會期連結是加密值，依會期名稱對照選屆 postback 回應的 ddlMeeting 明碼。
先取非空會期，再依 hidCount01..09（及出現時的 010）逐分頁以 500 筆翻頁，每分頁最多 100 頁。
各分頁實際筆數必須等於 hidCount，跨頁重複 SN 或計數變動也失敗；不把分組標題列當 record。
每次 POST 都帶緊接上一個回應的 VIEWSTATE、VIEWSTATEGENERATOR、VIEWSTATEENCRYPTED、
EVENTVALIDATION 與同一 Session cookie；缺 VIEWSTATE／EVENTVALIDATION 回 `KHH_POSTBACK_CHAIN_BROKEN`。
WebForms 欄位名稱、狀態簽章、選單及伺服器 Session 改動皆可能使鏈中斷，不能假裝空結果成功。
四份 raw Statistics 離線加總為 32,430；登記原有 1,636 與 1.31MB 仍是舊本會期估算，
歷史新增量由全域預留承接，實際資料庫容量須全量驗收後重估。
高雄條款「僅供個人、非商業用途，不可重製、改作、散布」仍有索引及再利用風險；
robots 允許與只存中繼資料，不等同完整授權。

臺北 GET `Adv_SearchInput.aspx`，從 `sele_DETR` 讀取兩位數屆別；POST 同頁先查未結案全部，再逐屆整批查已結案。
未結案要先查：實測 59 筆未結案中有 58 筆也列在第 14 屆的結案屆次清單，先查才能保留「未結案」標示。已結案送 `useisFi=on`、`isFini=F`、`useCOTE=useCOTE`、`sele_DETR=屆`；
未結案送 `useunFi=on`、`isFini=U`、`NotZ99All=NotZ99All`，不帶屆；兩者均送 `btnSubmit=查詢`。
其他欄位僅沿用上一回應 form1 的 hidden 狀態，不送 printForm 或其他篩選控制項。
缺 VIEWSTATE／EVENTVALIDATION 回 `TPE_POSTBACK_CHAIN_BROKEN`；不再請求首頁 OMCount 或 searchlist。
MainTable 依 data-field 表頭映射，每頁先按 `OutputWhereStr` 的「共N筆」核對實際列數必須完全相等，
缺計數回 `TPE_COUNT_MISSING`，不符回 `TPE_COUNT_MISMATCH` 並使整輪失敗，沒有 1% 容差。
沒有固定 500 列上限，但出現「超過 500 筆」仍回 `TPE_ROW_CAP term=XX` 或 `TPE_ROW_CAP open`。
核對後才跨屆與已結／未結去重，SYSN 保留首筆（未結案優先）並回 `TPE_DUPLICATE_SYSN <id>` 警告；
請保存 CLI `--report`，既有 runs 表未新增 warnings 欄位。

舊屆＋次篩選會漏掉結案屆次符合、卻不屬於任何一次大會的案件，也未抓未結案件。
主對話 2026-11-18 在 xisx-pc 實測第12屆整屆1,579筆，臨時大會236＋定期大會675＝911，
各次加總912；第5屆整屆1,994、各次加總1,972。逐次結果即使與該次 OMCount 相等，仍不能證明整屆完整。
進階檢索第1屆3,898列／2,029,868 bytes、第14屆1,716列／1,088,155 bytes、第12屆1,579列；
未結案59列／73,810 bytes。
主對話 2026-11-18 在 xisx-pc 以新做法同步：第10～14屆寫入筆數 992／1,200／1,579／1,727／1,716，與官方各屆總數完全相同；
寫到第9屆時碰到每日寫入上限（`BUDGET_STOPPED`），重跑 changed=0。
日期切片備案是 `REDTChk=on`、`REDTstartdate`／`REDTenddate` 使用民國日期（如 `103/01/01`）；
第12屆逐年切片加總亦為1,579。此次不實作日期切片，單次回應仍限8MiB。
DetailPage.aspx 直接 GET 回403；不請求 DetailPage.aspx／PrintDetailPage.aspx，不補 Referer、不換 UA，
只儲存入口連結。第1～9屆因每日寫入上限分日續跑，全量匯入尚未完成。

桃園登記為「無結構化議案」，不實作 adapter、endpoints 留空。可替代的
`schedule.aspx?mid=40`、`meeting.aspx?mid=41`、`file.aspx?mid=44` 屬議事日程與 PDF 清單，
不能當成結構化提案檢索，也不在本輪抓取。

離線驗收包含 BOM、日期空值、計數失敗、跨頁游標、hidden 狀態鏈、分組標題排除、穩定 SN、
臺北逐屆及未結案筆數完全核對、超過500列正常回應、截斷訊息失敗、去重警告與詳情請求禁制；三站各以 SqliteStore 同步兩次，第二次 changed=0。
真實網路、臺南／高雄 d1-local 全量及臺北 xisx-pc 匯入由主對話另驗收；本 worker 不連網、不碰雲端。

### W3 立法院

`ly20`（`N-LY-ID20-BILLS`）與 `ly6`（`N-LY-ID6-INTERP`）已註冊，狀態為
`IMPLEMENTED_API`。本輪以主對話提供的 2026-11-18 結構製作離線 fixture，尚未實際連網驗收。
共用 W2a `HttpAdapter`／`http_fetch.get`，依登記檔每次請求間隔 3 秒、每次最多 8MiB。
JSON 前置大量空白與換行先 strip 再解析。`sessionPeriod` 必須補零（`01`，不能是 `1`）；
分頁參數無效，每會期只請求一次 `page=1&limit=20000&fileType=json`。

| 欄位 | ID20 議案 | ID6 質詢 |
|---|---|---|
| `kind` | `ly_bill` | `ly_interp` |
| `external_id` | `billNo`；全範圍取 `sessionTimes` 數值最大者，同值取最後出現者 | `ly6-` + UTF-8 `sha256(term\|sessionPeriod\|sessionTimes\|item)[:24]`；完全重複列依順序加 `-2`、`-3` |
| `title` | `billName.strip()[:300]` | `item.strip()[:300]`；只有 `甲、`／`乙、` 開頭且短於 20 字的列才當標題列排除 |
| `case_number` | `第{term}屆第{int(sessionPeriod)}會期 {billNo}` | `第{term}屆第{int(sessionPeriod)}會期第{int(sessionTimes)}次會議` |
| `authority` | `billOrg.strip()[:200]` | 沿用登記的官方機關 |
| `official_url` | 安全的 HTTPS `ppg.ly.gov.tw` `pdfUrl`；否則 `https://data.ly.gov.tw/getds.action?id=20` | `https://data.ly.gov.tw/getds.action?id=6` |
| `event_date` | null（無官方日期欄位） | null（無官方日期欄位） |

ppg 只作官方連結、不連線；只加入 `official_url_hosts`，不加入 `allowed_hosts`。
登記自檢拒絕將 `blocked_origins` 放入 `allowed_hosts`，內容與 robots 轉址至 ppg 也在送出前拒絕。
不擷取 PDF／DOC，也不儲存 `billCosignatory`、`docUrl` 或其他未核准原始欄位。

從 `scope.from_term`（目前 10）第 01 會期開始，逐期至 08，再換下一屆；
每一屆都讀完 01–08 會期，屆中的空期是官方缺口、不停止；某屆 01 與 02 都為空才視為該屆尚未開議並停止，保護上限為第 12 屆第 08 會期。
標題列過濾或議案去重後為空，不算官方空期。先讀完有界範圍，驗證並保留公開投影，
完成跨會期議案去重後才交給 loader 寫入；游標 `term-sessionPeriod` 指向尚未完成的會期。
預算停止後新程序會重新讀取範圍，以重建全域去重，再從游標續寫；
部分批次重播會以既有內容雜湊跳過。這會重讀已完成會期，並非免網路成本的續跑。
官方內容若在續跑間改變、使議案移至游標前方，須下一次完整掃描才能補齊。

完整掃描只在 UTC 週一或手動 `--backfill` 執行；其他日子先由第 12 屆往回找最新已開議屆別（01 或 02 會期有資料），
只掃該屆，舊屆視為已結束、極少變動（2026-11-23）。續跑游標若落在舊屆（中斷的完整掃描），自動改回完整掃描。
已知限制：週一那次排程若被 GitHub 漏發，完整掃描順延一週；議案若同時出現在舊屆且舊屆 `sessionTimes` 較大，
平日與週一的去重結果可能不同，造成少量重寫。

比對既有內容雜湊時，D1 查詢改為每次約 8,000 字元的 `IN` 清單（原為固定 10 筆）。
遠端每次查詢約 1.3–2.3 秒、與筆數無關：2026-11-23 實測 ID20 全部 14,790 筆由 1,479 次查詢（約 45 分）降為 91 次、212 秒。

ID6 有官方缺口：主對話提供第 11 屆第 01 會期 74 筆、03／05／06 會期為 0。
每個實際讀取的空會期在 JSON run 報告加 `EMPTY_PERIOD term=11 sessionPeriod=03` 等警告；
不以第三方補資料。2026-11-18 主對話實測：舊的「連續兩個空期即停」會在第 11 屆 03／04 停下，之後會期補上資料就永遠掃不到，已改為整屆掃描；
未開議屆別的空期不產生警告。這仍不代表已證明全歷史完整性。警告須保存 CLI `--report`，既有 runs 表未新增 warnings 欄位。
ID6 雜湊使用完整原始 `item`，不使用截短標題；內容改動會產生新 ID，舊列不自動刪除。
`|` 串接及 24 字元雜湊依本次規格，不等同官方識別碼。

離線驗收涵蓋去重、補零、跨屆與上限、空期停止、預算續跑、標題列與穩定 ID、警告、
ppg 僅連結、欄位限制及 SqliteStore 兩次完整同步第二次 `changed=0`。
實際連網及 `--store d1-local` 兩次同步留由主對話驗收，`BUDGET_STOPPED` 不算全量完成。

### W5 TDX

`tdx`（`N-TDX-EVENTS`）已註冊為 `IMPLEMENTED_API`，表示離線實作通過，尚非帶金鑰的實網驗收。
缺 `TDX_CLIENT_ID` 或 `TDX_CLIENT_SECRET` 時，CLI 回報 `NOT_CONFIGURED`／`MISSING_SECRET`、exit 0；
該來源不建立 adapter、不請求 robots／token／事件，單獨同步它時也不建立資料庫連線。
workflow 僅在 sync 步驟傳入這兩個 secrets，未建立或申請任何憑證。

每次 run 透過官方 OAuth2 token endpoint POST `grant_type=client_credentials` 表單一次，
驗證非空字串 token，再依臺北、新北、桃園、臺中、臺南、高雄順序請求
`/api/basic/v1/Traffic/RoadEvent/LiveEvent/City/{City}?$format=JSON&$top=1000`。
每市一頁，檢查 Count 等於 Items 長度且不超過 1,000；缺欄、重複 ID、截斷或異常資料皆失敗。
游標為下一個尚未完成的城市名，預算停止後重新取得 token 並從該城市重播。
城市間並非同一時間快照；先前已完成城市的變動要等下一輪完整同步補齊。
共用既有白名單、robots、3 秒間隔與重試規則；token 回應上限 64KiB、事件回應上限 8MiB。
認證及事件錯誤只回報固定訊息，不保存可能反射 secret／token 的底層例外或回應內容。

僅保存 `traffic_event`、`tdx-{City}-{EventID}`、最多 300 字標題、中文事件類型（未知類型用 `類型{n}`）、
EffectiveTime 日期（缺值改用 PublishTime）、AuthorityCode＋Source、城市中文名及 TDX 首頁連結。
不保存 Description、Location、Positions、Geometry、AttachmentURLs，不下載附件。
因官方沒有逐筆網址，`official_url` 固定為 `https://tdx.transportdata.tw/`。

loader 對設定 `scope.retention_days` 的來源，在所有頁面成功後才清除該來源
`event_date < UTC 當天日期減 N 天` 的資料；TDX 設 30 天，邊界當天及日期不明者保留。
每批最多刪除 100 筆，刪除前按既有每筆預留列數（`RECORD_WRITE_ESTIMATE`）檢查每日預算，容量及 UTC 換日守門同樣適用；不足就
`BUDGET_STOPPED`，不執行該批，保留已完成批次的計帳。SQL delete trigger 同步 FTS，實際含 trigger 寫入量計入
`rows_written` 與每日預算；JSON 報告另列 `deleted`。dry-run 僅輸出刪除 SQL。
未設定保留期的來源行為不變；擷取 ERROR 或尚未完成全部頁面時不執行清除。

2026-11-18 主對話提供的實測：匿名 LiveEvent 回 401 純文字；robots 回 HTML SPA，
依既有規則登記 `HTML_NOT_ROBOTS`，執行時仍重新檢查。v2 News 缺高雄，因此不用。
token 成功回應仍未實測。條款頁 `https://tdx.transportdata.tw/term` 要求標示來源，
亦寫有償提供。2026-11-18 查收費要點（https://tdx.transportdata.tw/pricing ，113-03-22 修正）：
註冊後預設「基礎會員」免費、每月 3 點、不需綁卡，點數用完只停用到月底，不會自動扣款；
本端點屬基礎服務（1,500 次/點），每日一輪六都約每月 0.12 點。基礎會員速率上限 5 次/分/金鑰，
超過回 429，所以登記檔把請求間隔設為 13 秒（token 與六市請求同一 host，一輪約 80 秒）。

### W6 資料集目錄

`odp`（`N-ODP-CATALOG`）已註冊為 `IMPLEMENTED_API`，只下載 data.gov.tw 官方全量 CSV，
一個 run 為單頁，游標僅 `None`。保留 6564 metadata API 作登記參考，adapter 不額外呼叫它。
提供機關須以六都市政府開頭，接受「台／臺」並將轄區統一為「臺」；資料集名稱須含
`scope.keywords` 任一詞（警察、消防、交通、治安）。不依 CSV 列順序或 ID 大小篩選。

`http_fetch.get(output_file=...)` 逐塊寫入可 seek 的二進位暫存檔，最多 100MiB，
無 Content-Length 也逐塊計數；重試清空前次片段，失敗清空檔案，呼叫端負責關閉。
未指定 output_file 的既有行為不變。adapter 用 `io.TextIOWrapper(encoding="utf-8-sig", newline="")`
配合 `csv.DictReader` 逐列讀取，不將約 69MB 的原始 CSV 整份載入記憶體。
僅保留過濾後的中繼資料清單；CSV 欄位缺少、列長度錯誤、ID 非數字或格式錯誤均失敗。
必要欄位為資料集識別碼、資料集名稱、提供機關、更新頻率、授權方式、資料下載網址、
檔案格式、詮釋資料更新時間。保留官方其他欄名（含「資資料集上架方式」），但不匯入。

保存 `dataset`、`odp-{id}`、名稱、機關、轄區、詮釋資料更新日期、最多 100 字更新頻率、
授權方式及 `https://data.gov.tw/dataset/{id}`。空白授權明記「未標示（不推定開放）」。
不存聯絡人、電話、描述及下載網址，不連資源網址、不爬各市入口。
原始 CSV 的換行欄位、BOM、資源欄以 `;` 串接及結尾分號均按 CSV 處理；
不依錯標的 Content-Type 判定內容。沒有可匿名使用的列表／增量 API，每輪仍需全量下載。
data.gov.tw robots 500 沿用已登記的 documented open API 例外，不放寬其他 robots 限制。

`tests/test_national_tdx_odp.py` 全部使用假 session，意外真實請求會立即失敗。
TDX fixture 依提供的 LiveEvent schema 自造兩市各兩筆，其餘四市回空頁；
ODP fixture 從提供的 `export-csv.head300k.csv` 留下 136493、135546、135997、139224 四筆，
另以樣本欄位衍生六筆人工邊界案例（900005–900010），聯絡資訊及描述均改為測試字串。
驗證六都、臺／台、換行、BOM、分號、排除條件、授權空白、串流上限、重試片段替換、
憑證錯誤遮蔽、城市續跑、保留期與 FTS／預算，以及兩來源各同步兩次後第二次 `changed=0`。
實網 ODP 兩次同步、TDX 憑證及遠端 D1 的成本與效能均留由主對話另行驗收。

### W7 逐字稿本機索引

`scripts/national-transcript-index.py` 只供內部研究：**不進 D1、不進 CI、不公開全文**。
不接入 loader、公開 gateway 或任何 workflow；全文、搜尋片段與資料庫留在本機。
沿用 `N-COUNCIL-TXG` 登記的主機、robots 與 3 秒請求間隔，以及既有
`HttpAdapter`／`http_fetch.get`。執行時仍檢查 robots；列表最多 8MiB，detail 最多 4MiB。

預設資料庫 `~/.cache/govintel/transcripts.sqlite`；所有子指令拒絕 repo 內路徑，
包含解析後的符號連結／junction 及其他 Git checkout。search、stats 唯讀開庫，
不存在就報錯，不建立空庫；`.gitignore` 另排除逐字稿資料庫與 journal／WAL 側檔。

```powershell
python -X utf8 scripts/national-transcript-index.py sync --since 2026-08-01 --max-meetings 3
python -X utf8 scripts/national-transcript-index.py sync --since 2026-08-01 --until 2026-08-31 --page-size 1000
python -X utf8 scripts/national-transcript-index.py search "交通安全" --limit 20 --json
python -X utf8 scripts/national-transcript-index.py search "議員" --speaker "主席(張議長清照)" --from 2026-08-01 --to 2026-08-31
python -X utf8 scripts/national-transcript-index.py stats
# --db 放在子指令後，位置須在所有 Git checkout 外
python -X utf8 scripts/national-transcript-index.py stats --db C:\Users\Administrator\.cache\govintel\transcripts.sqlite
```

空庫且未給 `--since` 時全量回填；完成後未給 `--since`，從最新會議日減 14 天增量掃描。
先完整掃描日期範圍的列表，核對頁碼、pageSize、穩定總數與每頁
`min(pageSize, totalCount 剩餘)`，成功後才抓全文。pageSize 接受 1–1000，預設 1000。
只抓未索引或段數不同的會議；`--max-meetings` 限制 detail 嘗試數（含段數不符的嘗試），
不限制列表掃描。JSON 摘要含掃描列數、新增／更新數、跳過原因、警告與 complete。
段數不符時 `complete=false` 且保留舊會議；結束碼 0 不代表全量完整。

`sync_state` 保留未完成的 since／until，包括全量的空 since；受限額或中斷後，
重跑相同指令，或不帶日期參數續跑，仍掃描原範圍並跳過已完成且段數相同的會議，
避免切成 14 天增量而漏掉舊資料。明確傳日期可選擇新範圍；重新掃描會重複列表請求。
完整處理後清除進度，有段數警告則保留待重試。每場以一個 transaction 刪除舊段落再重寫；
寫入失敗復原該場與 FTS，較早提交的會議仍保留。insert／update／delete trigger
同步 external-content FTS5 trigram。stats 顯示會議數、段落數、最早／最新日期與主檔 bytes。

API 陷阱與處理：

- 列表 content 截成 40 字，不能當全文；內容只取 FrontDetail。
- `proceedingContentId` 可為 null、`seq` 全為 0，同場有完全相同發言。
  鍵用 `(proceedingsId, 原始陣列 ordinal)`（0 起），不依內容去重，deleted 留下序位缺口。
- 排除 `isDeleted=true` 後段數必須等於完整列表的該場列數，否則整場警告、跳過、不寫入。
- `sessionType` 列表是字串、detail 是整數；不靠此欄判斷，保存 `sessionDisplay`。
- 列表日期為無時區 ISO、detail 為民國中文字串；採列表的 ISO 日，不猜時區或解析民國顯示文字。
- `term` 參數需要 ID，不是屆次數字；不傳此參數。
- 官方連結只到 `/meeting-records/{proceedingsId}`，不捏造段落永久網址。
- 相同段數的內容修訂不觸發重抓，14 天以前的變動也可能漏掉；歷史重查需明確給 `--since`。
  若要重抓段數未變的內容，可用 repo 外的新資料庫重建，目前沒有強制刷新選項。
- 列表無快照 token；即使總數穩定，等量增刪或排序變更仍可能漏列。
  段數核對只能攔部分問題，不能據此宣稱歷史完整。

搜尋 3 字以上用 FTS5 literal phrase（加引號、跳脫雙引號）；1–2 字用 LIKE，
跳脫 `\`、`%`、`_`，與 W1 gateway 一致。發言人精確比對完整名稱，日期含起訖當日。
每筆列出日期、會期、會議類型、名稱、發言人、命中前後約 40 字與官方連結。

依主對話提供的 2026-11-18 總數 971,552、pageSize=1000，列表約 **972 次**；
僅 3 秒節流約 **48.6 分鐘**，每場 detail 再加一次。若 M 場需抓，約 `972 + M` 次 API
請求、`3 × (972 + M)` 秒，另加 robots、網路、重試與寫入時間；每多 1,000 場約加 50 分鐘。
會議總數尚未實測。max-meetings 不免除完整列表成本；pageSize=1000 是否給足列數待主對話
連網驗收，不足即報錯，可改較小 pageSize 重跑。

離線 fixture 縮減自 `proc-detail-83bac000.json` 與 `proc-p2-s100.json`，保留兩筆實際重複發言，
另人工標一段 deleted；第二場沒有 raw detail，以三筆 raw 列表摘錄合成 detail 外殼，
不冒充第二場完整逐字稿收據。測試僅用 fixture、假 session、記憶體 SQLite，攔截意外真實請求。
實際連網 `--since 2026-08-01 --max-meetings 3` 與搜尋由主對話另行驗收，本 worker 未連網。

## 評估過但不採用（使用者決定只直連官方）

以下保留第一輪提供的 **2026-11-17 實測紀錄**作規模參考，本輪沒有重新連網驗證；不是本輪官方來源的筆數或驗收收據，也不作後續擷取入口。

- OpenFun HF `openfun/tw-cc-data`：CC-BY-4.0，49 subsets，lastModified 2026-10-28；逐字稿約 545 萬筆。
- CCAPI：bills 177,657、meets 22,268、councilors 14,113；臺中 bills 15,430（官方 yishi 15,496），會議最新 2026-09-10、逐字稿最新 2026-08-10。`*.cc.govapi.tw/robots.txt` 為 `Disallow: /api/`、`Crawl-delay: 3`。
- LYAPI `ly.govapi.tw/v2`：bills total 149,339，第 9 屆 30,806、第 10 屆 43,029、第 11 屆 24,781；最新進度 2026-11-18（保留當時紀錄原值，未解釋日期差異）。interpellations 11,754，第 10 屆 1,614、第 11 屆 276。robots 僅 `Crawl-delay: 3`；頁尾程式 BSD-3、資料 CC-BY-4.0（當時未逐條確認），每筆附 ppg 官方 URL。
- `pcc-api.openfun.app`（原 pcc.g0v.ronny.tw）：getinfo 最新 2026-11-17、listbydate 可用；先前篩選量約 3–4 萬筆／年。官方連結樣式為 `https://web.pcc.gov.tw/prkms/tender/common/noticeDate/redirectPublic?ds={date}&fn={filename}`，當時觀察 303 轉官方；不表示官方 HTML 已完成擷取驗收。

上述 HF、CCAPI、LYAPI 及第三方採購 API 已移出來源清單及 preflight，不再配置其 token、不下載 Parquet、不以第三方補欄位或補官方缺口。

## 其他保留觀察與 W0 驗收界線

- 2026-11-17 提供的 data.gov.tw 紀錄：6564 CSV 約 68.9MB、52,609 筆、每日更新；六都關鍵字命中北 234、新北 266、桃 178、中 610、南 228、高 792。各市 CKAN 匿名 API 當時回 401／404；本輪未重測。採購 172020／172023 僅 GPA，6155 前 5 大、6573 優先採購也不能替代全量公告。
- 第一輪紀錄：wrangler 4.131.2 本機 D1 的 `fts5(title, tokenize='trigram')` 能以「警察局」查到中文標題。這不是遠端驗收；本輪未重新測試 D1。
- 兩種 runner 合計規劃 27 個 robots URL、27 個頁面目標（22 議會＋ID20＋採購首頁＋一個 GPA 檔＋6564 metadata＋TDX），嘉義縣 API 另含在 robots URL 中。兩個已知全禁議會頁面必定跳過；沒有轉址時最多 52 次 GET。robots 每條鏈最多 6 次 GET（初始請求加 5 次轉址），頁面最多一次 GET、不跟隨轉址；全部無重試、每次 10 秒 timeout、256KiB 讀取上限（多讀 1 byte 偵測超限）。頁面不保存正文，robots 保存有界內容。
- `--dry-run` 完全不連網；HTTP 可達不代表資料筆數、內容 schema、授權或匯入正確。報告中的跳過列沒有 HTTP 回應，不冒充成功。
- D1 workflow 保留唯讀 `d1 list --json`，CI 結果為失敗；不能證明寫入權限或遠端 FTS5。此輪沒有重新執行 Actions 或任何遠端驗收。
- `intel_v2/http_fetch.py` 的 robots 判定已更新；一般 `get` transport 的重試、內容轉址與 canary 行為維持原規則。preflight 以 stdlib 探測並共用 robots 判定，不新增安裝依賴；正式匯入器留待各批次實作與驗收。

## 排程與每日同步控制（2026-11-19）

- `scope.refresh_days` 是來源的 UTC 日期刷新間隔；未設定時為 1。臺北、臺中、臺南、高雄、基隆議會設為 7 天。只有最新一筆 run 是 `OK`、有 `finished_at` 且 `cursor_after` 為 null 時才算新鮮。`--ignore-fresh` 可手動強制重跑；dry-run 不套用新鮮度略過。
- 未指定 `--source` 時，CLI 保留 registry 內相對順序，先跑非 `N-COUNCIL-*` 來源，再跑議會來源。指定來源則維持該來源單獨執行。
- `--reserve-rows N` 把可用每日寫入上限降為 `DAILY_WRITE_LIMIT - N`，同時套用於 guard 檢查及 loader；預設 0。xisx-pc 改為先跑後，兩個 workflow 皆不使用此參數（保留供手動調整）。
- `--skip-if-ran-today` 按 UTC 日期檢查目前 runner 所管理來源的 `runs.started_at`；命中時所有候選來源回報 `SKIPPED / ALREADY_RAN`，不建構 adapter，stderr 輸出 `SKIP ALL ALREADY_RAN`。dry-run 不套用此開關。兩個 workflow 各自在排定重試時啟用，人工 dispatch 不啟用。
- xisx-pc workflow 的排程為 00:20 與 08:30 UTC；啟動 checkout/setup 前先用 curl 檢查 Cloudflare API 與臺北議會實際 registry host `ifddoc2.tcc.gov.tw`，連線失敗即以 `::error::` 終止。
- 2026-11-20 新增 GitHub Actions 看門狗，檢查兩個同步 workflow。2026-11-22 修正：實測此 repo 的 schedule 固定延遲 7–8 小時（11/20–22：00:20 的 xisx 約 07:00 才執行，02:17 的 hosted 約 09:45 才執行），原本 01:30／03:30 的最晚開始時間會在原排程還在排隊時誤判並重複補發；改為 xisx 12:00 UTC、hosted 15:00 UTC，看門狗 cron 改為 04:30、07:30、10:30、13:30 UTC（延遲後約在 12:00–21:00 UTC 執行）。若當日沒有成功或執行中的 run，逾時後透過 `GITHUB_TOKEN` 補發一次；補發後仍失敗或未出現 run 時發出 warning，同日不再重試。看門狗沿用 `NATIONAL_SYNC_ENABLED` 開關，不需 Cloudflare secret。
