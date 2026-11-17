# GovIntel 全國資料層：只直連機關官方系統

## 2026-11-17 方向修訂與定位

依使用者 2026-11-17 決定，資料層只直連各機關官方系統。data.gov.tw 是官方平台，可以作資料集目錄及官方資源發現入口。22 個地方議會全部登記，但 robots 明文禁止者不得抓取，列入待取得同意項目。任何 token 都不構成繞過 robots 的許可。

來源登記獨立放在 `docs/govintel/national-sources.v1.json`，目前共 29 筆。它是參考與發現層，不能作為現行治理回答的唯一證據；現行 catalog、policy、publication、`online_collect.py` 與 `schema_drift.py` 不改動。全國規模及不同議會格式需要獨立儲存與查詢，避免擴充既有 `S-###`、單一 origin 及發布容量限制時連動治理 hash 鏈。

公開輸出限中繼資料（標題、案號、日期、轄區、機關、授權）與官方連結，不公開全文、摘錄或逐字稿。內部保留 `official_url`、`provider_url`（官方提供端）、`fetched_at`、`content_sha256`、`license`、`rights_class`。本輪僅 W0：沒有建庫、部署、匯入或建立雲端資源。

## 官方來源

議會的官方／議事系統網址、資料種類、取得方式、網站條款與 robots 觀察均照錄 #247 分支 `origin/docs/national-council-plan-20261115` 的 `docs/govintel/NATIONAL_COUNCIL_EXPANSION.md` 第二節（2026-11-15 盤點）。本輪未重新連網查證。`rights_note` 保留該表網站文字；原表沒有提供宣告頁精確網址者，`license_source_url` 留 null，不把入口頁冒充授權證據。

- 22 個地方議會各一筆 `N-COUNCIL-{ISO 縣市縮寫}`。有 API 的臺中、基隆與嘉義縣 API 使用 `OFFICIAL_JSON_API`；新北使用 `HTML_HEADLESS`；其餘使用 `HTML` 或 `HTML_FILES`。完整狀態、資料種類與來源網址以登記檔為準。
- 嘉義縣保留單一 `N-COUNCIL-CYQ`：主來源是 `PLANNED_API`，`www_access` 另外標成 `BLOCKED_BY_ROBOTS_NEEDS_CONSENT` 且 endpoints 為空。API 根網址只標示官方 origin，精確路徑／分頁待 W2a；它的 robots 未查，preflight 另行取得，不發出 API 內容請求。
- `N-LY-ID20-BILLS`：官方 data.ly.gov.tw ID20 議案，政府資料開放授權條款第 1 版。preflight 只請求一頁；遠端是否採用分頁參數、資料 schema 與完整筆數仍待驗證。
- `N-LY-ID6-INTERP`：官方 ID6 委員質詢；已知缺口為「第 11 屆第 3–6 會期官方回空（2026-11-17 實測）」。這是指定會期的觀察，不推定所有會期永久停更，也不以第三方資料補齊。
- `N-PCC-WEB`：直接抓 web.pcc.gov.tw 官方公告檢索 HTML；每次請求間隔至少 3 秒，近 2 年只收機關名稱含「警察」「消防」「交通局」或以「臺中市政府」開頭的公告。不用廣泛的「交通」條件。容量預留 40MB，檢索表單欄位與正式路徑留待 W4；preflight 僅抓首頁。
- `N-PCC-GPA`：data.gov.tw 172020／172023，政府資料開放授權條款第 1 版。下載模板為 `https://web.pcc.gov.tw/tps/tp/OpenData/downloadFile?fileName=GPAtender_YYYYMMDD.json` 與 `GPAaward_YYYYMMDD.json`，日期以 YYYYMMDD 代入。僅 GPA 案，不能代表全量採購；與公告網頁去重後共用 40MB。preflight 固定探測 20261117 招標檔，若檔案不存在只回報 HTTP 結果，不改抓多天。
- `N-ODP-CATALOG`：data.gov.tw 資料集 6564，`https://data.gov.tw/datasets/export/csv`。只篩臺北、新北、桃園、臺中、臺南、高雄的警察、消防、交通、治安資料集。W6 儲存目錄中繼資料；需要資料本身時只從各機關的官方資源網址取得，逐一核准 host、授權及 robots，不爬被禁的市府入口或透過轉址繞過限制。
- `N-TDX-EVENTS`：保留六都即時路況事件，30 天保留期；未設定 `TDX_CLIENT_ID`／`TDX_CLIENT_SECRET` 時記錄 `NOT_CONFIGURED`。W0 僅探測官方根網址，不申請憑證。
- `N-TWINKLE-MANUAL`：保留作開發時人工交叉驗證工具，不納入自動資料層或 Actions；與官方路徑算同一來源，不能當獨立佐證。帳號、授權與額度依既有紀錄仍待確認。

## robots 與同意

| 對象 | 登記狀態與處理方式 |
|---|---|
| 嘉義市議會整站 | `BLOCKED_BY_ROBOTS_NEEDS_CONSENT`；endpoints 空，只抓 robots；對主要搜尋引擎的例外不適用本工具。 |
| 嘉義縣議會 www | `www_access.status = BLOCKED_BY_ROBOTS_NEEDS_CONSENT`；endpoints 空，只抓 robots。API 子網域獨立查核，不沿用 www 結論。 |
| 澎湖縣議會附件目錄 | `PARTIAL_ROBOTS`；可檢查首頁，禁抓附件。原總表未列精確目錄，先停用所有附件與 SharePoint 資源。 |
| 立法院 ppg.ly.gov.tw | robots 全禁，不抓；改用 data.ly.gov.tw 官方 API。 |
| data.kcg.gov.tw、data.tainan.gov.tw | robots 全禁，不爬入口；若資源仍在相同被禁 host，也不抓，待同意或其他官方資源位置。 |

上述被擋資源須向主管機關發函取得書面同意後，再審核同意的 host、路徑、用途與速率，更新登記及存取策略後才加入。即使最新 robots 暫時回空或開放，也不自動解除已登記的禁止狀態。

preflight 先 GET 每個探測 origin 的 `/robots.txt` 並記錄內容，才決定是否 GET 內容頁。明文禁止者記 `BLOCKED_BY_ROBOTS_NEEDS_CONSENT`；抓取失敗、過大、HTML 或無法判讀者記 `SKIPPED_ROBOTS_UNVERIFIED` 並跳過頁面。404／410 表示沒有 robots 資源。採購網已知 `/robots.txt` 轉至同 origin 首頁且沒有觀察到限制，僅此精確轉址情況可通過；仍不跟隨轉址。

preflight 使用 stdlib robots parser；含萬用字元、結尾錨點或混合 Allow／Disallow 的複雜規則，保守視為未驗證並跳過頁面。這可能減少即時探測覆蓋，不能把跳過視為成功。解析成功時也尊重 Crawl-delay，且請求間隔不低於 3 秒。

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

| 批次 | 範圍與驗收重點 |
|---|---|
| W0 | 官方登記、robots gate、preflight、共用 transport；本輪範圍。 |
| W1 | D1 schema、匯入與查詢骨架；每日寫入 9 萬列、容量 400MB 守門；驗證冪等性。 |
| W2a | 臺中、基隆、嘉義縣 API；先確認 robots、精確端點、分頁及官方筆數。 |
| W2b | 六都其餘一般網頁：臺北、桃園、臺南、高雄；新北另列 W2d。 |
| W2c | 其他縣市；blocked 來源先取得書面同意，澎湖附件維持停用，盤點未知筆數。 |
| W2d | 新北 Blazor Server 無頭瀏覽器；確認會話與欄位，不繞過 robots。 |
| W3 | 立法院 ID20、ID6，明示官方空白會期與資料完整性。 |
| W4 | 官方採購公告檢索 HTML、GPA 下載、篩選與去重；每次間隔至少 3 秒。 |
| W5 | TDX 憑證、事件及未設定時的跳過狀態。 |
| W6 | data.gov.tw 六都相關目錄；各機關官方資源另行查核。 |
| W7 | 從各議會官方系統取得逐字稿／議事錄，建立本機 SQLite FTS5；目前 #247 僅確認臺中有議事錄 API，其餘依 W2 盤點。全文不進 D1、CI 發布或公開頁面，受 robots 與網站條款限制。 |
| W8 | 全國查詢前端與來源說明；只公開核准中繼資料與官方連結。 |

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
- preflight 計畫含 27 個 robots URL、27 個頁面目標（22 議會＋ID20＋採購首頁＋一個 GPA 檔＋6564 metadata＋TDX），嘉義縣 API 另含在 robots URL 中。兩個已知全禁議會頁面必定跳過；其餘仍依即時 robots 判斷，最多 52 次 GET。每個 URL 最多一次 GET、無重試、不跟隨 redirect、10 秒 timeout、256KiB 讀取上限（多讀 1 byte 偵測超限）。頁面不保存正文，robots 保存有界內容。
- `--dry-run` 完全不連網；HTTP 可達不代表資料筆數、內容 schema、授權或匯入正確。報告中的跳過列沒有 HTTP 回應，不冒充成功。
- D1 workflow 保留唯讀 `d1 list --json`，僅驗證列出權限，不能證明寫入權限或遠端 FTS5。Actions 真實 egress、D1 token 權限與遠端 FTS5 均未在本輪驗證。
- 第一輪 `intel_v2/http_fetch.py` 與 `canary-s028-165.py` 保持原樣。preflight 採 stdlib 一次性探測，以維持不重試、不跟隨 redirect 的界線；正式匯入器留待各批次實作與驗收。
