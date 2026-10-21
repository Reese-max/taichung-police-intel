# GovIntel AI｜資料來源優化策略

> 本文是 2026-09-09 的來源架構規劃，表中建議頻率、canary PASS 與升級順序不是目前正式啟用或新鮮度證明。送件 v6 首期四類候選為 S-001／S-032／S-033／S-031；S-019 仍是其他候選。現行核准集合、D1／D2 缺口與每來源驗收界線見[資料來源矩陣](competition-2026/DATA_SOURCE_MATRIX.md)。沒有每日新增 100–200 筆的實測承諾。

版本：v0.2  
更新日：2026-09-09  
目標：把現有「議會證據導覽」擴充成「跨機關公共事件整合、重要異動偵測與交班支援」所需的來源架構。

## 1. 現況判斷

目前正式執行的 `P0_SOURCES` 只有 5 個：

- S-004 臺中市議會議事日程
- S-006 臺中市議會質詢順序表
- S-007 臺中市議會議事資訊系統－議事錄
- S-009 臺中市議會議事資訊系統－各項提案
- S-029 臺中市政府議會專案報告

這 5 個來源對「議會備詢」很強，但對 GovIntel AI 想解決的「今天發生什麼、哪些事情剛改變、哪些資訊需要交班」仍不夠完整。

倉庫既有稽核與 canary 已經證明不少來源值得升級：S-001 臺中市政府警察局警政新聞、S-005 內政部警政署警政新聞、S-019 市政會議紀錄與專案報告、S-026 臺中市政府主管法規共用系統、S-028 警政統計＋人口分母，以及 165 涉詐網站公開資料。這些不應重新當成「新發現」，而應納入新的優先級與驗收制度。

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
| S-033 | 臺中市政府市政新聞 | PRIMARY_EVENT | 30 分鐘 | 候選，官方市府 RSS 已查核 | 跨局處事件發現；轉載不得算獨立佐證 |

### B. 政策、議會與制度異動

| ID | 來源 | 角色 | 建議頻率 | 狀態 | 用途 |
|---|---|---|---|---|---|
| S-004 | 臺中市議會議事日程 | PRIMARY_EVENT | 每日 2 次；會期可提高 | 正式 collector | 議程新增／修正 |
| S-006 | 臺中市議會質詢順序表 | PRIMARY_EVENT | 每日 2 次；會期可提高 | 正式 collector | 備詢順序與時程異動 |
| S-007 | 臺中市議會議事錄 API | PRIMARY_EVENT | 每日 2 次 | 正式 collector | 質詢內容與會議紀錄 |
| S-009 | 臺中市議會各項提案 API | PRIMARY_EVENT | 每日 2 次 | 正式 collector | 提案及狀態內容 |
| S-010 | 臺中市議會議員質詢影音 | PRIMARY_EVENT | 會期內每日 | 已稽核 | 原始影音證據與時間戳導航 |
| S-011 | 臺中市議會議事影音 | PRIMARY_EVENT | 會期內每日 | 已稽核 | 會議原始影音 |
| S-019 | 臺中市政府市政會議紀錄與專案報告 | PRIMARY_EVENT | 每日 | 已稽核 | 市政決策、局處報告與政策方向 |
| S-026 | 臺中市政府主管法規共用系統 | PRIMARY_REFERENCE | 每日 | canary PASS | 法規草案、期限及制度變更 |
| S-029 | 臺中市政府議會專案報告 | PRIMARY_REFERENCE | 每日 2 次 | 正式 collector／canary PASS | 警政及跨局處正式報告 |

### C. 統計與內政資料背景

| ID | 來源 | 角色 | 建議頻率 | 狀態 | 用途 |
|---|---|---|---|---|---|
| S-028 | 臺中市受（處）理刑事案件－分局別（data.gov.tw 88147） | PRIMARY_REFERENCE | 每日檢查 metadata；新檔才下載 | canary PASS | 犯罪趨勢背景與分局層級統計 |
| CTX-POP | 臺中市各區戶數／人口（data.gov.tw 103703） | ENRICHMENT | 每週 | canary PASS | 人口分母與行政區背景 |
| CTX-165 | 165 遭停止解析涉詐網站（data.gov.tw 176455） | PRIMARY_REFERENCE | 每日 | canary PASS | 詐欺型態與全國外部脈絡；不得與臺中案件數直接相加 |
| S-034 | 臺中市政府警察局交通事故逐案公開資料／OAS | PRIMARY_REFERENCE | 每日檢查新月資料 | 新候選，官方資料已查核 | 事故類型、時段、道路條件、GPS，供事件背景與後續模型 |
| S-035 | SEGIS 統計／內政大數據入口 | ENRICHMENT | 按資料期別或任務載入 | 新候選，官方入口 | 人口、社經、統計區等背景；模擬資料不得宣稱為即時實況 |

## 4. 新來源的正式入口

以下入口在 2026-09-09 重新查核；實作前仍要執行 canary、確認 HTML／API 結構與使用條件。

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

1. **S-019 市政會議**：已有稽核、結構穩定，最容易快速升級成正式 collector。
2. **S-001 警察局警政新聞**：已有完整清單與詳細頁解析，但需解決高頻全量抓取造成的連線重設；改成列表差異後才抓新／變更詳細頁。
3. **S-032 交通局最新消息**：對大型活動、交通管制、疏運情境價值高。
4. **S-033 市府市政新聞**：作跨局處事件發現；轉載內容不能重複計為獨立佐證。

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

`promotion_plan` 中每個來源都必須在觀測窗口內逐日累積有效 canary receipt；`scripts/verify-candidate-observation-window.py` 會把「來源完全沒有出現在任何 receipt」視為未完成窗口（BLOCKED），而不是略過。每日觀測 workflow（`.github/workflows/candidate-source-observation.yml`）的 matrix 必須涵蓋 `promotion_plan` 全部來源（S-019、S-001、S-032、S-033、S-031），缺漏來源無法累積七日收據。

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
