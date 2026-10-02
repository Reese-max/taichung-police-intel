# GovIntel AI｜構想書／參賽內容對照表

本表把本次構想書的產品主張連到 repository 內可讀的設計、程式與 receipt。它不取代主辦方的官方表單、資格認定或正式格式；沒有實測的欄位保持空白或 `NOT_RUN`。

| 構想書問題 | 目前可說的內容 | Repository evidence | 固定狀態／結果邊界 |
|---|---|---|---|
| 誰遇到什麼問題 | 公開公告、議會資料、版本異動與官方證據分散；承辦人需要在交班／備詢前找出真正變更並回原文核對。 | [GovIntel plan](../GOVINTEL_PLAN.md)、[root README problem and users](../../../README.md#problem-and-users) | 產品 JTBD；不宣稱已完成真人需求驗證。 |
| 首期產品任務 | 官方公告版本 → 差異／衝突 → evidence locator → 人工核對 → 交班草稿；公開資料、非勤務指揮。 | [architecture](./ARCHITECTURE.md)、[issue #24 design](../issue-24-public-event-fusion.md)、[issue #23 design](../issue-23-handoff-flow.md) | `IMPLEMENTED_NOT_PRODUCTION`（可重播核心）／`DESIGN_ONLY`（正式服務）。 |
| 既有產品基線 | 五個議會／市政來源、source health／gap／LKG、priority brief、官方影音與 transcript navigation。 | [source policy](../source-policy.approved.json)、[public data](../../../apps/web/public/data/source-status.json)、[Web app](../../../apps/web/app/page.js) | `PRODUCTION_ACTIVE`；公開資料仍可能 `STALE`／`PARTIAL`。 |
| 本次新增：來源策略 | source catalog v2、Source Policy、candidate canary、Twinkle／direct-official routing 原則。 | [source catalog](../source-catalog.v2.json)、[source strategy](../DATA_SOURCE_STRATEGY.md)、[Twinkle overlay](../twinkle-source-overlay.v1.json) | `CANDIDATE_CANARY` 或 `DESIGN_ONLY`，不能寫成已擴源 production。 |
| 本次新增：事件與版本 | 保留各文件 identity、版本、locator，候選公共事件使用獨立 `public_event_id`，衝突不平均。 | [PublicEvent fixture](../../../apps/web/public/data/public-event-demo.json)、[fusion self-check](../../../scripts/public-event-fusion.py) | `IMPLEMENTED_NOT_PRODUCTION`；fixture-only，不是 live accuracy。 |
| 本次新增：交班／追蹤 | local-first handoff 與待重核資料模型可 replay；完整跨日 persistent tracking 仍依 #23 驗收。 | [handoff flow](../issue-23-handoff-flow.md)、[handoff script](../../../scripts/handoff-state.py) | `IMPLEMENTED_NOT_PRODUCTION`（local replay）／`DESIGN_ONLY`（完整 production claim）。 |
| 本次新增：內政背景資料 | 人口／機關／統計資料要標期別、來源與限制；Twinkle 不算第二個獨立證據。 | [GovIntel plan data boundary](../GOVINTEL_PLAN.md#3-資料清冊與取得順序)、[NPA matrix](../issue-28-npa-source-matrix.md) | `DESIGN_ONLY` 或 `CANDIDATE_CANARY`；無即時人流／管轄／派遣推論。 |
| 評估與結果 | 以相同 cutoff、事件切分、人工標註，比較 A 人工、B／B0 基線、C GovIntel；結果欄不預填。 | [evaluation plan](./EVALUATION.md)、[manifest template](./evaluation-manifest.template.json) | `DESIGN_ONLY`；結果 `NOT_RUN`／null。 |
| 發布與展示 | Pages 靜態 build、來源狀態與 evidence path 可看；完整晨晚、失敗演練與匿名 hash acceptance 仍受 #20 限制。 | [current status](./CURRENT_STATUS.md)、[Issue #20](https://github.com/Reese-max/taichung-police-intel/issues/20)、[latest run](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907) | `BLOCKED`（#20 acceptance 尚未全部完成）。 |

## Official-source boundary

當屆資格、報名期間、評分權重、既有作品認定與官方提交欄位不由本 repository 推定。請把主辦官方原件與團隊私有回執另行保存，不把本表當成報名成功證據。

## Reader path

先讀 [JUDGE_PATH.md](./JUDGE_PATH.md)，再依[資料來源矩陣](./DATA_SOURCE_MATRIX.md)、[架構](./ARCHITECTURE.md)、[評估](./EVALUATION.md)和[限制](./LIMITATIONS_AND_SAFETY.md)深入核對。
