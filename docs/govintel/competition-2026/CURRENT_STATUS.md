# GovIntel AI－現行實作與證據狀態

文件更新：2026-10-05（Asia/Taipei）。日期化已發布程式基準：`main@edb7148c9565c7d59ac222689c35bff41376d1cb`，對應成功的 [run 37303622240](https://github.com/Reese-max/taichung-police-intel/actions/runs/37303622240)。這是本頁核對時點的證據；公開資料與版本會隨後續排程更新，引用前須重讀 receipt 的時間、code SHA、generation 與 hash。PR #103／#124／#125／#126 已合併；本輪來源／日期修復另待合併、發布與正式回讀。

公開查詢與同瀏覽器追蹤已合併並發布。2026-10-05 的 `31d93f52bb6df37ff9b4e94c13bfdfdad7109699` 正式驗收保存了 Gateway 7 項、公開五檔 bytes/hash、原生瀏覽器 6 項 PASS；瀏覽器範圍包含實際受限查詢、官方原文卡、條件保存／刷新／重開，以及明示資料缺口。較早 `3a06754a2428023bf316d8cb180f239f9c3b971d` 的完整 gate、406 項 Web 與本地 runtime／browser 記錄仍屬歷史本地工程證據。以上均不證明 AI／真人成效、完整事件覆蓋或來源 promotion；詳細界線見 [VERIFICATION.md](VERIFICATION.md)。

產品與已送 v6 使用 **GovIntel AI－公共資訊查詢與個人化追蹤平台**；主辦未接受更名，正式競賽登記名稱另待確認。v6 修訂日為 2026-10-01，首期服務民眾與公開資訊研究／業務人員；公開查詢、同一瀏覽器追蹤與可重播驗證包是主交付。交班、研究匯出與多人協作不是首期主流程。

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
| 受限 production Query Gateway | `PRODUCTION_ACTIVE` | [production verifier](../../../scripts/verify-query-gateway-production.py)與下列日期化正式驗收；release／generation／hash 綁定、受限查詢及官方證據通過，不代表語意 AI 或 domain-store 全域涵蓋。 |
| v6 公開查詢 | `PRODUCTION_ACTIVE` | [PR #124](https://github.com/Reese-max/taichung-police-intel/pull/124)與後續修正已整合；正式 `/public-query/` 的實際 metadata 查詢、原文卡與缺口提示通過日期化瀏覽器驗收。未宣稱完整跨機關事件或語意模型能力。 |
| v6 個人追蹤／重開站更新 | `PRODUCTION_ACTIVE` | [PR #125](https://github.com/Reese-max/taichung-police-intel/pull/125)與後續修正已整合；正式 `/tracking/` 的條件保存、刷新及同瀏覽器重開通過。每日時段、延長、已讀、取消與明文解除另有本地 replay；完整真人跨日情境仍待驗，不含背景推播／跨裝置。 |
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
- [較早 production observation](production-observation-20261005.json)綁定 `562141e`：當時 release.json 404 與能力缺口是該時點的記錄。後續 PR #103 合併與以下正式 release 驗收不改寫這份歷史收據。
- 2026-10-05 11:22 Asia/Taipei，[run 37257702283](https://github.com/Reese-max/taichung-police-intel/actions/runs/37257702283)完成 `31d93f5` Pages／Worker 發布；11:49–11:50 的匿名正式驗收通過 Gateway 7 項、公開五檔 hash 與瀏覽器 6 項。驗收包 `GovIntel-secret-and-production-acceptance-20261005-31d93f5.zip` 的 `acceptance-index.json` 綁定 code SHA、release `9392d0d17a59e6fe4157b7a7f19f2a74a7d5fc143bdea5c4c0078ea2cad5af86`、Worker version 與各 receipt。`data/release.json` 已可取得；Pages manifest 的 `BUILD_ONLY`、Worker 的 `RUNTIME_BOUND` 與匿名 verifier 的 `PRODUCTION` 是不同證據層，不能自行把 manifest 的空驗收欄位填成成功。
- [run 37303622240](https://github.com/Reese-max/taichung-police-intel/actions/runs/37303622240)於 2026-10-05 19:54 Asia/Taipei 成功完成 `edb7148` 發布。此 run 的成功與較早 `31d93f5` 的完整瀏覽器 receipt 分開留證；不把早期 6 項操作改標為新 SHA 的瀏覽器驗收。
- [本輪來源稽核](SOURCE_RELIABILITY_REVIEW_2026-10-05.md)與 [metadata-only 修復證據](../source-repair-evidence-2026-10-05.json)保留四種時間：擷取／檢查時鐘、API 記錄日期、文件修訂日、首次發布日。S-006 附件明示的修訂日期以 `document_revision_at`／`OFFICIAL_DOCUMENT_REVISION_DATE` 記錄，不能冒充 `published_at`，未取得證據仍未知。S-007 的 2026-05-28 至 2026-10-05 受限官方窗口回傳零筆，已觀察 API 頁面的舊記錄仍陳舊；不能捏造新文件或把零筆推成來源已更新。S-009 的受查 FrontList 記錄與相符 FrontDetail 未提供可核對發布日期，內容日期維持 `UNKNOWN`，擷取時鐘不得補成日期。這批修復尚待正式發布與回讀。
- 2026-10-05 22:19 Asia/Taipei 的受限連線診斷中，S-029／S-019 研考會與 S-001 警察局入口仍回 HTTP 503，訊息為 upstream connection timeout；只證明本執行環境的當次連線失敗，不能由此斷定 GitHub runner 的 ConnectionError 根因。保留 FAILED／PARTIAL 與 LKG，來源恢復仍待外部連線成功及正式蒐集證據。

## 送件與外部確認

2026-10-06 另核對既有往返：v6 補件於 2026-10-01 22:00 Asia/Taipei 寄出；主辦於 10-05 11:39 確認收妥，但未接受提案更名。參賽者已於當日 17:01 詢問原報名完整名稱及是否須改名重送，尚未查到後續指示。收妥不等於最終資格或名稱核准，詳見[日期化核對](ORGANIZER_STATUS_2026-10-06.md)。官方資格、團隊真實分工、權利與提交格式仍須由官方原件及私有紀錄核對。公開 repo 不收錄參賽者個資、收件人、信件識別碼、原始信件或私人附件。

## 更新規則

功能改為 `PRODUCTION_ACTIVE` 前，須綁定已合併 code SHA、部署產物、匿名 readback 與該功能的真實瀏覽器／runtime 操作。本地驗收通過只可提升本地驗收狀態；資料、模型、真人與主辦結果各自留證，不能互相代替。未完成項目見 [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md)；37 張舊 PR 的逐 head 判定與整合目標見 [PR 收斂紀錄](../pr-convergence-2026-10-05.md)。
