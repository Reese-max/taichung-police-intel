# GovIntel AI－現行實作與證據狀態

文件更新：2026-10-05（Asia/Taipei）。已部署程式基準：`main@562141e396c693e115b3fce10594c434c401a58e`；下列 PR 是另行驗收的候選，不能把其功能算入這份 production 基準。公開資料會隨排程更新，引用前須讀取 receipt 本身的時間、版本與 hash。

本輪本地整合已包含公開查詢、追蹤、來源頁、固定 D1 背景與 release-aware fail-closed 修正。**已驗證程式版本 `3a06754a2428023bf316d8cb180f239f9c3b971d`：完整 gate PASS，Web 406/406、同 checkout runtime 26/26、既有 browser 13/13／v6 browser 22/22。** Served `BUILD_ONLY` release 綁定該 code SHA 與三檔 hash；這些是本地工程驗收，沒有正式部署、AI／真人成效或來源 promotion。詳細界線見 [VERIFICATION.md](VERIFICATION.md)。

提案名稱固定為 **GovIntel AI－公共資訊查詢與個人化追蹤平台**。v6 修訂日為 2026-10-01，首期服務民眾與公開資訊研究／業務人員；公開查詢、同一瀏覽器追蹤與可重播驗證包是主交付。交班、研究匯出與多人協作不是首期主流程。

## 狀態語意

- `PRODUCTION_ACTIVE`：已在目前公開發布路徑使用；不保證每次資料完整、新鮮或涵蓋所有事件。
- `IMPLEMENTED_NOT_PRODUCTION`：程式、fixture 或本地 receipt 可重播；正式部署、資料 promotion 與真人驗收須另有證據。
- `CANDIDATE_CANARY`：候選來源正在觀察，未進入核准 production 集合。
- `DESIGN_ONLY`：規劃、規格或評測方法存在，尚無可用性證明。
- `BLOCKED`：明確驗收缺口未解決。

`PARTIAL`、`STALE`、`FAILED`、`NOT_RUN`、`UNVERIFIED` 描述資料或評測 receipt，不能直接替代能力狀態。

## Claim / evidence table

| 能力／主張 | 狀態 | 可核對的證據與限制 |
|---|---|---|
| 五個來源 publication baseline、來源健康與官方影音導覽 | `PRODUCTION_ACTIVE` | [核准 policy](../source-policy.approved.json)：S-004／S-006／S-007／S-009／S-029。[公開 source status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)仍須逐項讀取日期、完整性與 LKG。 |
| 受限 production Query Gateway | `PRODUCTION_ACTIVE` | [production verifier](../../../scripts/verify-query-gateway-production.py)與 Oct 4 run 的 Gateway check；只證明 generation 綁定、受限查詢與失敗處理，不證明語意 AI、v6 查詢 UX 或 domain-store 全域涵蓋。 |
| v6 公開查詢原型 | `IMPLEMENTED_NOT_PRODUCTION` | [PR #124](https://github.com/Reese-max/taichung-police-intel/pull/124)與修正已本地整合到 `/public-query/`，本輪 runtime／browser 通過；結果附原文、時間與限制，release／generation 變更使舊詳情失效。正式 production 部署仍須另驗。 |
| v6 個人追蹤／重開站更新 | `IMPLEMENTED_NOT_PRODUCTION` | [PR #125](https://github.com/Reese-max/taichung-police-intel/pull/125)與修正已本地整合到 `/tracking/`；保留每日時段、延長、已讀、取消、純排版與明文解除邊界。同瀏覽器保存，不含背景推播／跨裝置。 |
| v6 可重播評測工具 | `IMPLEMENTED_NOT_PRODUCTION` | [PR #126](https://github.com/Reese-max/taichung-police-intel/pull/126)與修正已本地整合；工程範圍為 `SYNTHETIC_DETERMINISTIC_POLICY_REPLAY`。[評測協定](EVALUATION.md)與 [manifest](evaluation-manifest.template.json)中 A／B／C／語意消融的完整方法執行仍 `NOT_RUN`。 |
| Source Policy、located facts、角色排序、local-first handoff、Review Inbox | `IMPLEMENTED_NOT_PRODUCTION` | [#49](../issue-49-source-policy-integration.md)、[#48](../issue-48-official-document-replay.md)、[#23](../issue-23-handoff-flow.md)及 self-check；不是正式多人簽核或機關採用。 |
| PublicEvent 融合 | `IMPLEMENTED_NOT_PRODUCTION` | [JSON](../../../apps/web/public/data/public-event-demo.json)明示 `FIXTURE_ONLY`；三份測試文件與衝突不是真實即時事件或準確率。 |
| 四類首期候選 S-001／S-032／S-033／S-031 | `CANDIDATE_CANARY` | [獨立觀察 workflow](../../../.github/workflows/candidate-source-observation.yml)、[窗口驗證器](../../../scripts/verify-candidate-observation-window.py)；七個有效觀察日也不能自動取代權利、完整性、敏感內容與 promotion review。 |
| S-019、其他 reference／Twinkle／Taiwan Intel 擴充 | `CANDIDATE_CANARY`／`DESIGN_ONLY` | S-019 不是 v6 首期四類；本地 workflow 已補入 S-019，窗口驗證器對 promotion-plan 五來源都 fail closed，但尚未取得七日自然觀察／核准啟用。Twinkle 兩個既有連線需重新認證；媒體 live／十四日 shadow 尚未驗收。 |
| D1 固定人口／戶數背景查詢 | `IMPLEMENTED_NOT_PRODUCTION` | [官方 112Y12M 樣本](segis-112Y12M-taichung.verified.json)已核對 368 國內記錄，涵蓋臺中 29 區；`/public-query/` 提供固定期別選單、人口／戶數／代碼、來源與 hash。`production_active=false`；不是現況人口，真人用途效益未測。 |
| D2 警察機關查詢、語意 AI | `DESIGN_ONLY` | [D2 receipt](background-source-observations-2026-10-05.json)：dataset 5958 metadata 200，但官方 ZIP／native TGOS 403，列數與 CRS 未驗證。deterministic keyword／fixture 不能冒充語意模型與 AI 增益。 |
| 真人試用、A／B／C 效益、採用 | `DESIGN_ONLY` | 結果 `NOT_RUN`／null；約 20 事件／60 文件、3–5 位使用者與 85%／90%／30% 都是規劃。 |
| #20 完整排程／failure-recovery 驗收 | `BLOCKED` | [Issue #20](https://github.com/Reese-max/taichung-police-intel/issues/20)的未完成條件須逐項留證；不能把整條已成功發布流程繼續稱為未合併，也不能用單次成功取代全部 failure drill。 |

## 已有的日期化 production 證據

- [Run 37169331249](https://github.com/Reese-max/taichung-police-intel/actions/runs/37169331249)，2026-10-04 10:22 Asia/Taipei 開始，執行了實際蒐集，五個公開檔案 hash 與 Gateway 檢查通過。
- 同日稍後的 [run 37212963039](https://github.com/Reese-max/taichung-police-intel/actions/runs/37212963039)也通過部署、五檔公開 bytes/hash 與 legacy Gateway 檢查。2026-10-05 01:13 Asia/Taipei 的匿名觀察對應 `CR-DEMO-20261004-EVENING-SCHEDULE`，`generated_at=2026-10-04T23:26:28+08:00`，publication hash 為 `1c9e8087121e513776dc1da0379af80615945f53dfaa68bb46641689de3471bd`。這些是各自 generation 的證據，不把較早快照寫成永遠的 latest。
- 本輪已核對的來源缺口仍為 `PARTIAL`：S-007 陳舊、S-029 蒐集失敗。失敗與 LKG 不能解讀成零事件或來源解除。
- 核准 active set 仍只有五個來源；沒有四類候選已啟用、全臺完整涵蓋或每日新增 100–200 筆的證據。
- `publication-state` lifecycle 已在 main；[PUBLICATION_RECOVERY.md](PUBLICATION_RECOVERY.md)的 9 月「尚未 merge」記錄屬歷史根因，不是目前合併狀態。
- [Gateway health](https://govintel-query-gateway.irisx-tracker.workers.dev/health)在上述觀察為 HTTP 200，coverage `PARTIAL`、missing S-029、stale S-007／S-029。`search_events`、`get_event`、`compare_event_versions`、`query_statistics` 未提供；`data/release.json` 回傳 404，release-bound [PR #103](https://github.com/Reese-max/taichung-police-intel/pull/103)仍是候選，不能宣稱已具完整 release contract。完整安全快照見[production observation](production-observation-20261005.json)。
- [本輪來源稽核](SOURCE_RELIABILITY_REVIEW_2026-10-05.md)另發現 S-006 公開快照把擷取時鐘推成 `FRESH`／`COMPLETE_ZERO`。本地已修正無日期／混合日期列表為 `PARTIAL`、未知內容日期；修正尚未部署，不能把舊公開數值視為已更正。S-029 的 bounded 官方 index GET 仍 503，尚無恢復證據。

## 送件與外部確認

v6 已完成；補件已於 2026-10-01 22:00 Asia/Taipei 寄出。已寄出不等於主辦已受理或已同意更名；目前沒有受理／更名回覆證據。官方資格、團隊真實分工、權利與提交格式須由官方原件及私有紀錄核對。公開 repo 不收錄參賽者個資、收件人、信件識別碼、原始信件或私人附件。

## 更新規則

功能改為 `PRODUCTION_ACTIVE` 前，須綁定已合併 code SHA、部署產物、匿名 readback 與該功能的真實瀏覽器／runtime 操作。本地驗收通過只可提升本地驗收狀態；資料、模型、真人與主辦結果各自留證，不能互相代替。未完成項目見 [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md)。
