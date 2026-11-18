# GovIntel AI｜資料來源優化策略

**2026-11-17 退役：** 擁有者決定移除 S-019、S-029、S-033、S-034：前兩者憑證 hostname mismatch 且只剩公告頁，S-033 回傳公告 HTML 而非 RSS，S-034 網域 NXDOMAIN、替代網域同路徑 502。現行 active 為 S-004／S-006／S-007／S-009；候選觀察為 S-001／S-032／S-031，Pages 候選為 S-001／S-032。這次只重建現行發布投影，保留原收集時間；下列日期化歷史收據不改寫，未重新連網收集、未部署驗證。

> 本文原為 2026-09-09 的來源架構規劃，表中建議頻率與歷史 canary PASS 不是目前正式啟用或新鮮度證明。2026-11-17 已退役 S-019／S-029／S-033／S-034，現行候選觀察為 S-001／S-032／S-031。現行核准集合、D1／D2 缺口與每來源驗收界線見[資料來源矩陣](competition-2026/DATA_SOURCE_MATRIX.md)。沒有每日新增 100–200 筆的實測承諾。

版本：v0.2  
更新日：2026-11-17（退役更新；原始規劃 2026-09-09）
目標：把現有「議會證據導覽」擴充成「跨機關公共事件整合、重要異動偵測與交班支援」所需的來源架構。

## 全國資料層

全國資料層獨立於治理 catalog、policy 與 publication，僅直連機關官方系統。
`data.gov.tw` 可作官方資料集目錄與資源發現入口；資料本身仍須從核准的官方資源網址取得。
`/national/` 只呈現中繼資料與官方連結，作為參考與發現入口，不能當作治理回答的唯一證據。
逐字稿、議事錄段落只建本機索引，不進 D1、不公開全文或摘錄。

以下取得方式依本機 [national-sources.v1.json](national-sources.v1.json) 與
[NATIONAL_DATA_LAYER.md](NATIONAL_DATA_LAYER.md)，不是本次連網或正式匯入驗收；
登記中的規劃來源可能尚無 adapter 或資料。

| 來源 | 取得方式與界線 |
|---|---|
| 臺中、臺南、基隆議會 | 官方 JSON API；以各來源狀態及已驗收範圍為準。 |
| 嘉義縣議會 | 官方 API 規劃中；API robots 500 且未標為文件化開放 API，禁止內容請求；www 另有明文禁止。 |
| 新北市議會 | 規劃以官方網頁 headless 取得，不繞過存取控制。 |
| 桃園、苗栗、澎湖、連江議會 | 官方 HTML／檔案；桃園未提供結構化議案；澎湖附件及 SharePoint 資源停用。 |
| 臺北、高雄、新竹市、嘉義市、新竹縣、彰化、南投、雲林、屏東、宜蘭、花蓮、臺東、金門議會 | 官方 HTML；仍逐一遵守 robots、條款與登記狀態；嘉義市禁止內容抓取。 |
| 立法院 ID20／ID6 | `data.ly.gov.tw` 官方開放資料 JSON API，分別為議案與委員質詢；不抓 robots 全禁的 `ppg.ly.gov.tw`。ID6 指定會期回空的缺口保留，不以第三方補齊。 |
| 政府電子採購網 | `web.pcc.gov.tw` 官方公告檢索 HTML；每次請求至少間隔 3 秒，近 2 年限機關名稱含警察、消防、交通局或以臺中市政府開頭。 |
| GPA 招標／決標 | 官方 `GPAtender_YYYYMMDD.json`／`GPAaward_YYYYMMDD.json`，對應 data.gov.tw 172020／172023；僅 GPA，與公告頁去重，不冒充全量採購。 |
| 六都開放資料目錄 | data.gov.tw 資料集 6564 的官方 HTTPS CSV，篩選六都警察、消防、交通、治安中繼資料；不爬被禁市府入口。 |
| TDX 六都即時路況 | 官方 HTTPS OAuth2；未設定金鑰時為 `NOT_CONFIGURED`，不要求使用者在前端提供密鑰；事件保留 30 天。 |
| Twinkle | 僅人工交叉驗證，不納入自動資料層，也不當成另一份獨立官方佐證。 |

資料每日更新，部分來源受每日寫入上限約束，首次回填需要數天；空資料、服務不可用與來源未啟用分開處理。
公開欄位限標題、案號、日期、縣市、機關、授權及官方連結等中繼資料。
網站條款、授權不因 robots 允許或已有 token 而被推定允許；登記禁止也不因 robots 暫時回空自動解除。
明文 Disallow 必須遵守。robots 無法連線時停止內容請求；5xx 預設禁止，僅登記明確標示
`documented_open_api: true` 的立法院兩來源、GPA 與官方目錄有專案限定例外，不能外推其他來源。
轉址安全、複雜規則與 4xx／5xx 的完整處理沿用全國資料層文件與既有實作，本次不修改政策。

需要議會同意／權利確認的來源：

- **嘉義市議會整站、嘉義縣議會 www、澎湖縣議會附件與 SharePoint**：既有 robots 限制，取得書面同意並核對 host、路徑、用途、速率後才可重新審核開啟。
- **嘉義縣議會 API**：目前禁止內容請求，須取得書面同意，或確認公開文件化 API 並重新審核登記，不能沿用 www 的結論或自行解禁。
- **高雄、基隆議會**：既有文件記錄限制重製／散布等條款；相關內部索引及再利用範圍仍需機關書面同意或法律意見，不把中繼資料界線當成完整權利許可。
- **其他標為版權所有、All Rights Reserved 或宣告頁未確認的議會**：逐來源確認用途與權利，不推定已獲同意；名單及原條款觀察見登記檔 `rights_note`。新竹市、桃園、金門的開放宣告或特定法定標的也不概括授權所有議事內容。

非議會的 `ppg.ly.gov.tw`、`data.kcg.gov.tw`、`data.tainan.gov.tw` 被禁資源同樣須取得主管機關同意，
或改用已核准的其他官方資源位置；禁止以轉址、token 或第三方代理繞過。以上是本專案既有處理界線，
不是新的法律認定，也不宣稱本次核對了網站最新條款。

## 1. 現況判斷

目前正式執行的 `P0_SOURCES` 只有 4 個：

- S-004 臺中市議會議事日程
- S-006 臺中市議會質詢順序表
- S-007 臺中市議會議事資訊系統－議事錄
- S-009 臺中市議會議事資訊系統－各項提案

這 4 個來源對「議會備詢」很強，但對 GovIntel AI 想解決的「今天發生什麼、哪些事情剛改變、哪些資訊需要交班」仍不夠完整。

倉庫既有稽核與 canary 已經證明不少來源值得升級：S-001 臺中市政府警察局警政新聞、S-005 內政部警政署警政新聞、S-026 臺中市政府主管法規共用系統、S-028 警政統計＋人口分母，以及 165 涉詐網站公開資料。這些不應重新當成「新發現」，而應納入新的優先級與驗收制度；已退役的 S-019 不再納入升級候選。

## 2. 來源不再只用「有沒有抓到」分類

每個來源都必須標記以下角色：

| 類型 | 用途 | 能不能單獨形成已確認結論 |
|---|---|---|
| `PRIMARY_EVENT` | 官方事件、公告、會議、新聞、狀態變更 | 可以，前提是時間與正文可驗證 |
| `PRIMARY_REFERENCE` | 法規、統計、計畫、預算、機關資料 | 可以作背景或制度依據，不等同即時事件 |
| `ENRICHMENT` | SEGIS、人口、地理與歷史統計 | 只能補充背景，不能冒充即時人流或現場狀態 |
| `DISCOVERY_ONLY` | 新聞聚合、社群、搜尋結果 | 只能用來找到事件，不能成為唯一證據 |

原則：**官方原始資料優先；結構化 API／CSV／JSON 優先；可回查版本與日期者優先；媒體與社群只作發現，不作單一事實來源。**

## 3. 第一優先來源矩陣

### A. 即時／高頻公共事件

| ID | 來源 | 角色 | 建議頻率 | 狀態 | 用途 |
|---|---|---|---|---|---|
| S-001 | 臺中市政府警察局警政新聞 | PRIMARY_EVENT | 30 分鐘 | 既有稽核，曾 DEGRADED | 治安、交通、重大活動、宣導與警政措施 |
| S-031 | 臺中市政府消防局「即時災情」 | PRIMARY_EVENT | 10 分鐘 | 新候選，官方頁已查核 | 公開救災／救護事件感知；僅供公共資訊，不作派遣依據 |
| S-032 | 臺中市政府交通局最新消息 | PRIMARY_EVENT | 30 分鐘 | 新候選，官方頁已查核 | 交通管制、公車／YouBike 異動、重大交通事件 |
| S-033 | 臺中市政府市政新聞 | PRIMARY_EVENT | 不再收集 | 2026-11-17 退役：RSS 回傳公告 HTML | 已移出 promotion plan |

### B. 政策、議會與制度異動

| ID | 來源 | 角色 | 建議頻率 | 狀態 | 用途 |
|---|---|---|---|---|---|
| S-004 | 臺中市議會議事日程 | PRIMARY_EVENT | 每日 2 次；會期可提高 | 正式 collector | 議程新增／修正 |
| S-006 | 臺中市議會質詢順序表 | PRIMARY_EVENT | 每日 2 次；會期可提高 | 正式 collector | 備詢順序與時程異動 |
| S-007 | 臺中市議會議事錄 API | PRIMARY_EVENT | 每日 2 次 | 正式 collector | 質詢內容與會議紀錄 |
| S-009 | 臺中市議會各項提案 API | PRIMARY_EVENT | 每日 2 次 | 正式 collector | 提案及狀態內容 |
| S-010 | 臺中市議會議員質詢影音 | PRIMARY_EVENT | 會期內每日 | 已稽核 | 原始影音證據與時間戳導航 |
| S-011 | 臺中市議會議事影音 | PRIMARY_EVENT | 會期內每日 | 已稽核 | 會議原始影音 |
| S-019 | 臺中市政府市政會議紀錄與專案報告 | PRIMARY_EVENT | 不再收集 | 2026-11-17 退役：憑證不符、只剩公告頁 | 已移出 promotion plan |
| S-026 | 臺中市政府主管法規共用系統 | PRIMARY_REFERENCE | 每日 | canary PASS | 法規草案、期限及制度變更 |
| S-029 | 臺中市政府議會專案報告 | PRIMARY_REFERENCE | 不再收集 | 2026-11-17 退役：憑證不符、只剩公告頁 | 已移出 active policy |

### C. 統計與內政資料背景

| ID | 來源 | 角色 | 建議頻率 | 狀態 | 用途 |
|---|---|---|---|---|---|
| S-028 | 臺中市受（處）理刑事案件－分局別（data.gov.tw 88147） | PRIMARY_REFERENCE | 每日檢查 metadata；新檔才下載 | canary PASS | 犯罪趨勢背景與分局層級統計 |
| CTX-POP | 臺中市各區戶數／人口（data.gov.tw 103703） | ENRICHMENT | 每週 | canary PASS | 人口分母與行政區背景 |
| CTX-165 | 165 遭停止解析涉詐網站（data.gov.tw 176455） | PRIMARY_REFERENCE | 每日 | canary PASS | 詐欺型態與全國外部脈絡；不得與臺中案件數直接相加 |
| S-034 | 臺中市政府警察局交通事故逐案公開資料／OAS | PRIMARY_REFERENCE | 不再收集 | 2026-11-17 退役：網域 NXDOMAIN、替代入口 502 | 已移出 promotion plan |
| S-035 | SEGIS 統計／內政大數據入口 | ENRICHMENT | 按資料期別或任務載入 | 新候選，官方入口 | 人口、社經、統計區等背景；模擬資料不得宣稱為即時實況 |

## 4. 新來源的正式入口

以下保留 2026-09-09 的入口紀錄；S-019／S-029／S-033／S-034 已於 2026-11-17 退役，其 URL 僅供歷史追溯。其餘來源實作前仍要執行 canary、確認 HTML／API 結構與使用條件。

- 臺中市政府警察局警政新聞：`https://www.police.taichung.gov.tw/ch/home.jsp?id=1&parentpath=0&mcustomize=news_list.jsp`
- 臺中市政府消防局：`https://www.fire.taichung.gov.tw/`（首頁含公開「即時災情」）
- 臺中市政府交通局最新消息：`https://www.traffic.taichung.gov.tw/`
- 臺中市政府市政新聞 RSS：`https://www.taichung.gov.tw/10179/564770/rss?nodeId=9962`
- 臺中市政府市政會議紀錄：`https://www.rdec.taichung.gov.tw/12047/12142/12186`
- 臺中市政府主管法規共用系統：`https://law.taichung.gov.tw/DraftForum.aspx`
- 臺中市政府議會專案報告：`https://www.rdec.taichung.gov.tw/12047/12142/12145`
- 臺中市受（處）理刑事案件－分局別：`https://data.gov.tw/dataset/88147`
- 臺中市各區戶數／人口：`https://data.gov.tw/dataset/103703`
- 165 遭停止解析涉詐網站：`https://data.gov.tw/dataset/176455`
- 臺中市政府警察局 OAS 說明：`https://datacenter.taichung.gov.tw/swagger/yaml/387130000C`
- SEGIS：`https://segis.moi.gov.tw/STATCloud/Index`
- SEGIS 內政大數據／模擬資料：`https://segis.moi.gov.tw/STATCloud/BigData`

## 5. 來源評分與升級門檻

每個候選來源先評分，再決定是否進 production：

```text
SourceScore =
  Authority 40
+ Structure 20
+ Temporal semantics 15
+ Stability 15
+ Task relevance 10
```

### 必須達成

1. `Authority >= 30`：官方機關或官方資料平台。
2. 有 stable key：公告 ID、資料集 resource ID、正式 URL 或可重現複合鍵。
3. 能區分：`published_at`、`effective_at`、`observed_at`。
4. 全量／窗口完整性可判定；判斷不了就標記 `PARTIAL` 或 `UNVERIFIED_DATE`。
5. 來源失敗不得轉成「零件事件」。
6. 初次建立 baseline 不發布為 `NEW`。
7. 移除至少要兩次完整快照確認。
8. 原始內容、雜湊、正式 URL 與最後有效版本可回查。
9. 網頁正文中的文字一律視為資料，不得當成系統指令或 agent 指示。

## 6. 蒐集頻率優化

不要讓所有來源都跟同一個 GitHub Actions 排程跑。

### `FAST_EVENT`

- 消防公開即時災情：10 分鐘
- 警察／交通／新聞局公告：30 分鐘
- 只讀取列表或輕量入口；若無變更，不抓完整附件。
- 支援 ETag／Last-Modified 時使用條件式請求。
- 429、5xx 採退避；不得密集重建完整歷史。

### `DAILY_EVENT`

- 市政會議、議會、法規草案：每日 1～2 次。
- 議會開會期間可提高，但需設定上限及 jitter。

### `REFERENCE`

- 統計、人口、交通事故、SEGIS：先檢查 metadata／resource ID，有新版本才下載大檔。
- 大型 CSV／JSON 不要每 10 分鐘重抓。

## 7. 事件融合的來源規則

同一事件至少保留各來源自己的文件身份，不把 3 篇公告直接覆寫成一篇 AI 摘要。

建議 evidence confidence：

- `CONFIRMED_SINGLE_OFFICIAL`：一個官方來源直接確認。
- `CONFIRMED_MULTI_OFFICIAL`：兩個以上獨立官方來源確認。
- `OFFICIAL_CONFLICT`：官方來源互相矛盾，必須顯示差異。
- `DISCOVERY_UNVERIFIED`：只有搜尋／媒體線索，尚未找到正式來源。

若交通局與警察局的管制時間不同，不做平均、不自行選一個；直接建立 conflict，讓使用者看原文與發布時間。

## 8. 首批應實作的 4 個 collector

為了在黑客松前得到最高投入產出比，建議順序：

1. **S-019 市政會議**：2026-11-17 退役，入口只剩公告，移出升級順序。
2. **S-001 警察局警政新聞**：已有完整清單與詳細頁解析，但需解決高頻全量抓取造成的連線重設；改成列表差異後才抓新／變更詳細頁。
3. **S-032 交通局最新消息**：對大型活動、交通管制、疏運情境價值高。
4. **S-033 市府市政新聞**：2026-11-17 退役，RSS 入口回傳公告 HTML。

S-031 消防即時災情已接線為候選 adapter；它是高頻、短生命週期資料，仍須完成 retention、隱私遮蔽與「不是派遣資料」提示的 live／七日驗收。

## 9. 黑客松展示用資料組合

首個完整案例不要追求最多來源，建議固定使用：

```text
警察局公告
+ 交通局公告
+ 新聞局市政新聞
+ 市政會議／議會資料
+ S-028 / 交通事故統計背景
+ SEGIS 人口／行政區背景
        ↓
跨來源事件融合
        ↓
重要異動
        ↓
官方證據回查
        ↓
人工確認的交班摘要
```

評審要看到的不是「我們接了 30 個網站」，而是：**同一件事情跨 3～5 個官方來源時，系統可以判斷它們的關係、指出真正改變的欄位、保留衝突，並讓每個結論回到正式證據。**

## 10. 不納入首期 production 的來源

- 未確認授權或需要登入的資料。
- 含個人層級敏感資料且對交班任務沒有必要的資料。
- 純媒體新聞、Facebook／Threads／社群貼文：只作 discovery candidate。
- 需要繞過驗證、反爬、存取控制才拿得到的內容。
- 沒有日期、沒有 stable key、無法判斷完整性的來源：只能保留為 reference，不能驅動 `NEW`／`REMOVED`。

## 11. 驗收

新來源進 production 前至少完成：

- 連續 7 日 canary。
- 成功／失敗／PARTIAL／零筆情境測試。
- 同一筆資料更新時 stable key 不變、version 增加。
- 無實質內容變更時不產生假警示。
- 來源故障時 last-known-good 保留。
- 回查連結可匿名開啟。
- 對外展示不含內部勤務、110 案件、個資或未公開資料。

`promotion_plan` 中每個來源都必須在觀測窗口內逐日累積有效 canary receipt；`scripts/verify-candidate-observation-window.py` 會把「來源完全沒有出現在任何 receipt」視為未完成窗口（BLOCKED），而不是略過。每日觀測 workflow（`.github/workflows/candidate-source-observation.yml`）的 matrix 必須涵蓋 `promotion_plan` 全部來源（S-001、S-032、S-031），缺漏來源無法累積七日收據。

### 2026-10-16：候選觀察增量快取修正（#14）

`scripts/candidate-runtime-canary.py` 的 `item_state` 保存下次列表差異比對所需的 stable ID／內容 hash。來源失敗、schema 拒絕或詳頁預算檢查失敗時保留先前快取；所有檢查通過後才更新。新聞列表的 `PARTIAL` 觀察合併已見項目並保留未見 ID，完整觀察（含 `COMPLETE_ZERO`）則替換快取。S-031 成功時只保存當次快照的 hashes，避免累積已消失的即時災情 ID。

| #14 驗收項目 | 程式／離線驗證與剩餘界線 |
|---|---|
| 列表先行、僅新／變更 ID 取得詳頁 | 沿用 `online_collect.collect_news_list`；`test_news_list_collector.py` 驗證 unchanged skip、變更與請求上限。 |
| 失敗／PARTIAL 不破壞既有資料 | `test_candidate_runtime_canary.py` 新增失敗報告重載、schema／健康／詳頁上限拒絕、PARTIAL 保留未見 ID；快取與輸入彼此獨立。正式 LKG 仍由原有 collector／publication 流程管理。 |
| success／FAILED／PARTIAL／COMPLETE_ZERO | 候選 runtime 回歸涵蓋各狀態；完整觀察依實際 items 替換快取，零筆窗口仍可保留窗口外列表項目，空快照才清空；失敗仍保留 null 計數。 |
| stable key、版本、無實質變更不警示、首次 baseline、兩次完整快照才移除 | 沿用 `intel_v2.semantics.compare_snapshot` 與 `test_v2_semantics.py`；快取更新不產生事件。 |
| S-031 隱私、短期保存與用途提示 | 沿用 coarse metadata、retention policy 與 public usage notice；新增成功快照不累積舊 ID 的回歸。實際來源／UI 與七日條件仍需真實驗收。 |
| 不破壞既有五個 P0 source | collector、P0 集合與核准 policy 均沿用；建置通過，但本機完整測試仍有下列阻擋，未宣稱全專案回歸通過。 |
| 發布來源角色／integration status、未核准不得 production | 沿用 source policy、bundle validator 與候選觀察窗口檢查；本次未升級來源。 |
| 每來源七日 canary、匿名回查、獨立完整性與權利 | 本次僅離線程式修補，未新增真實觀察日、匿名來源查核或權利證據，這些條件仍待完成。 |

本次未執行線上 collector、部署、push 或 PR；#14 維持未結案。既有候選／production 狀態及歷史收據保留。

本機驗證（完整紀錄保留在忽略的 `.runtime/issue-14-*.log`）：

| 命令 | 實際結果 |
|---|---|
| `python -X utf8 -m unittest discover -s tests -p test_candidate_runtime_canary.py -v` | 原有 19 項通過；新增回歸後 24 項出現 4 failures／1 error（exit 1），修正後 24 項通過（exit 0）。 |
| `python -X utf8 -m unittest discover -s tests -v` | 1,541 項，17 failures／86 errors／12 skipped，exit 1。包含 Windows 缺少 `os.O_NONBLOCK`／FIFO、symlink 權限／Unix mode、PDF parser pin、子程序 CP950，以及提交前乾淨工作目錄檢查。 |
| `npm --prefix apps/web test` | 800 項，795 pass／5 fail，exit 1；含 PDF parser pin、子程序 CP950、提交前工作目錄檢查與一次 Python fixture 逾時。該逾時案例單獨重跑 1/1 pass（exit 0）。 |
| `npm run check:gate0`、`npm run check:specs` | 各 exit 0，`required=164 specs=4 secrets=0`。 |
| `npm --prefix apps/web run build` | exit 0，Next 16.3.8 編譯與 11/11 靜態頁面產生成功，source policy `changed=false`。 |

以 `git show c629f82a8b16825d5d89c204c7035dcba500a9ac:intel_v2/checkpoint_code_binding.py` 的原件獨立重現缺少 `O_NONBLOCK`；相關原件與目前工作目錄內容一致。現有 Python 的 `pypdf=6.18.0` 與專案 pin `6.19.0` 不符。這些完整測試阻擋未在 #14 擴大修補，也未以跳過測試或更改全域環境掩蓋。

機器可讀清冊：`docs/govintel/source-catalog.v2.json`。
