# GovIntel AI｜2026 評審可重播入口

查核日：2026-09-30。程式基準為 [main `639697c815fe6fa48fca5022a0d410662d9a59c7`](https://github.com/Reese-max/taichung-police-intel/commit/639697c815fe6fa48fca5022a0d410662d9a59c7)。本文是有時間戳的產品狀態與 fixture 展示路徑，不是當屆資格、截止日、評分權重、正式報名、得獎或機關採用證明；這些事項仍須以主辦官方原件及團隊私有回執核對。

## 任務與目前狀態

承辦人要從公開的議會、市府、警政與交通公告找出同一事件的實質更正，回查官方原文，再判斷哪份舊交班稿需要重核。現行公開產品是五個臺中議會／市政來源的備詢簡報、來源狀態及官方影音證據導覽；跨機關事件融合與交班仍是可離線重播的候選工作流程，尚非正式服務。所有內容只使用公開來源與標示清楚的 fixture，不納入內部勤務、派遣或個案資料。

| 固定狀態 | 本次可支持的主張 | 證據邊界 |
|---|---|---|
| `PRODUCTION_ACTIVE` | 五來源靜態展示與官方影音導覽已在公開 Pages 提供。 | 2026-09-30 匿名讀取首頁與[公開 source status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)均為 HTTP 200。公開 JSON 記錄 `generated_at=2026-09-30T09:26:28+08:00`、MORNING `PARTIAL`；HTTP 200 不代表完整或新鮮。 |
| `IMPLEMENTED_NOT_PRODUCTION` | [唯讀 Query Gateway](../issue-30-runtime-boundaries.md)、[官方文件 locator](../issue-48-official-document-replay.md)、[PublicEvent fixture](../issue-24-public-event-fusion.md)及[本地交班版本](../issue-23-handoff-flow.md)有程式與離線重播。 | 未證明正式跨機關整合、多人持久服務、公開部署或真人驗收。 |
| `CANDIDATE_CANARY` | S-001／019／031／032／033 擴源候選。 | [觀測紀錄](../issue-22-publication-wiring.md)與仍開啟的 [PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16)；沒有 promotion 或正式發布收據。 |
| `DESIGN_ONLY` | [Twinkle＋官方直連來源策略](../TWINKLE_HYBRID_SOURCES.md)及[正文複查服務規劃](../issue-21-detail-recheck.md)。 | 沒有可用的 Twinkle client、完整正文覆蓋或正式跨機關服務。 |
| `BLOCKED` | [Issue #20 排程發布驗收](https://github.com/Reese-max/taichung-police-intel/issues/20)。 | 最新自然排程 [36655070187](https://github.com/Reese-max/taichung-police-intel/actions/runs/36655070187)完成 build、Pages artifact upload 與 deploy；其後公開資料驗證失敗，錯誤為 `public data hash/status mismatch: data/source-status.json`，Query Gateway 公開驗證跳過，沒有新的已驗證發布收據。 |

**當前公開來源讀數：**最新可讀 JSON 的 collection run 是 `CR-DEMO-20260930-MORNING-SCHEDULE`、`status=PARTIAL`。S-006／S-009 當次為 `FRESH`；S-004／S-007 為 `STALE`；S-029 為 `FAILED`／`STALE`（`CONNECTIONERROR`）。[主幹 verify run 36537639155](https://github.com/Reese-max/taichung-police-intel/actions/runs/36537639155)對 main commit 成功；它驗證程式，不替代失敗的公開資料 hash 核對或自然晨晚收據。倉庫內的 [`source-status.json`](../../../apps/web/public/data/source-status.json) 是更早的 2026-09-11 snapshot，不可當成目前線上資料。

## 五來源矩陣與證據來源

下列網址取自版本化的[來源 catalog](../source-catalog.v2.json)。S-007／S-009 是 API endpoint，瀏覽器直接開啟未必顯示人類可讀頁面。資料新鮮度依公開狀態中的 `data_as_of` 判斷；來源健康、時間窗完整性與整批發布驗證是不同訊號。

| 正式來源 | 官方入口 | 2026-09-30 公開狀態 |
|---|---|---|
| S-004 臺中市議會議事日程 | [臺中市議會](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=49) | `source_health=PASS`，但 `data_as_of=2026-07-27`、`freshness_status=STALE`。 |
| S-006 臺中市議會質詢順序表 | [臺中市議會](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=50) | 當次 `source_health=PASS`、`freshness_status=FRESH`；整批 run 仍是 `PARTIAL`。 |
| S-007 議事錄 | [臺中市議會議事資訊系統](https://yishi.tccc.gov.tw/api/ProceedingsBackWeb/FrontList) | `source_health=PASS`，但 `data_as_of=2026-05-27`、`freshness_status=STALE`。 |
| S-009 各項提案 | [臺中市議會議事資訊系統](https://yishi.tccc.gov.tw/api/Proposal/FrontList) | 當次 `source_health=PASS`、`freshness_status=FRESH`；整批 run 仍是 `PARTIAL`。 |
| S-029 臺中市政府議會專案報告 | [臺中市政府研考會](https://www.rdec.taichung.gov.tw/12047/12142/12145) | 當次 `CONNECTIONERROR`／`FAILED`；保留上次 2026-09-24 的 last-known-good 並標記 `STALE`。 |

```text
官方五來源 → bounded collector → source-status、V2 brief 與來源缺口
           → publication-state checkpoint → 靜態 Pages 與官方影音證據導覽
PublicEvent／handoff fixtures → 離線重播；不自動進入正式來源或正式發布
候選來源／Twinkle → canary 與人工 promotion gate；未升格前不算正式來源
```

## 四分鐘 fixture 展示路徑

在 main checkout 使用 Python 3.11+ 執行下列自我檢查；不需要 API key、付費服務、登入或資料寫入。展示新跨機關流程時只用 repository fixture。

| 時間 | 操作 | 評審可核對的結果 |
|---|---|---|
| 0:00–0:45 | 閱讀[根 README 的本次狀態](../../../README.md)，再開[公開 source status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)。 | 比較生成時間、MORNING `PARTIAL` 與五個來源各自的 freshness；不要把 HTTP 200 說成 hash 已驗證或所有來源新鮮。 |
| 0:45–1:40 | 執行 `python -X utf8 scripts/public-event-fusion.py --self-check`。 | 離線事件合併 fixture 與衝突檢查通過；資料是示例，不代表現場事件。 |
| 1:40–2:25 | 執行 `python -X utf8 scripts/handoff-state.py self-check`。 | 本地交班版本可重播；舊版本待重核不是正式多人服務或自動核准。 |
| 2:25–3:10 | 執行 `python -X utf8 scripts/located-facts.py self-check`。 | 以固定官方文件 fixture 驗證定位事實流程；不能推論已接入完整 live collector。 |
| 3:10–4:00 | 開啟 [#20 自然排程 run 36655070187](https://github.com/Reese-max/taichung-police-intel/actions/runs/36655070187)及[評測範本](evaluation-manifest.template.json)。 | 指出 public data hash/status mismatch 與未完成真人評測；所有未測分數保持 null／`NOT_RUN`。 |

完整工程 gate 為 `npm run check`。本展示只涵蓋固定 fixture，不展示 live 多機關事件融合、正式交班、排程驗收或真人績效。既有 [2026-08 Kiro 影片](https://reese-max.github.io/taichung-police-intel/demo-video.mp4)屬歷史原型，不能當成這些新增能力的 demo。

## 舊版與本次功能差異

| 2026-08 Kiro 原型（歷史） | GovIntel current main 已有 | 尚未驗收 |
|---|---|---|
| 五來源備詢首頁、來源健康與議會影音證據導覽 | 同一公開 council brief 主線；最近 source status 會分開顯示 freshness、缺口與 last-known-good | 自然晨晚發布完整成功，以及公開 bytes／hash 對照 |
| 單筆議會資料與影音回查 | 官方文件 locator、PublicEvent fixture、版本衝突與本地 handoff replay | 真實多機關公告關聯、正式事件 store 與持久化交班 |
| 沒有正式跨日多人交班服務 | local-first 交班 state 與待重核流程 | 權限、多人服務、真人採用與任務成效 |

以上是功能差異，不代表主辦方已認定新穎性、資格或可參賽性。舊 Kiro 使用紀錄及 2026-08 截止資料保留在[根 README 歷史區](../../../README.md#historical-kiro-competition-package)、[Kiro 紀錄](../../KIRO_USAGE.md)與[舊提交草稿](../../../SUBMISSION.md)。不得將其當成 2026 新賽事規則或新產品證據。

## 評測目標、結果與限制

| 指標 | 計畫目標／方法 | 目前結果 |
|---|---|---|
| 重要異動 precision／recall | 目標 precision ≥0.85、recall ≥0.90；依事件分組的保留集 | `NOT_RUN`；無有效樣本數與結果。 |
| 事件關聯 F1 | 暫定目標 ≥0.90；以人工裁決標準答案比較 | `NOT_RUN`。 |
| 引用支持 | 人工核對重要主張的文件版本與段落 | `NOT_RUN`；沒有支持率分數。 |
| 承辦任務時間與漏件 | 同資料截止點比較人工 A 與 GovIntel C；目標中位時間下降 ≥30%，且重要漏件不增加 | `NOT_RUN`；沒有真人試用結果。 |
| 資格、權利與報名 | 以當屆官方原件和私有回執核對 | `UNVERIFIED`；未聲稱已正式報名。 |

`evaluation-manifest.template.json` 預設結果為 null。分母為零或未測不得填 100%。公開資料以來源授權範圍、來源時間與 evidence locator 為界；模型只能提出候選，不下勤務指令、不自動改寫交班稿或替人發布。舊競賽截止日、影片與 Kiro 紀錄一律是歷史佐證。
