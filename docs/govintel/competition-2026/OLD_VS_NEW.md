# GovIntel AI｜舊原型與 2026 參賽新增差異

這張表讓評審分辨「原有 Kiro／議會原型」與「本次 GovIntel 方向」。新增欄位若不是 production，明確保留固定狀態；功能差異不等於主辦方已認定創新、資格或參賽成立。

| 能力 | 舊競賽／Kiro 原型（歷史） | 2026 GovIntel 新增方向 | 目前固定狀態與證據 |
|---|---|---|---|
| 產品入口 | `Taichung Police Public Intelligence`，一個議會備詢首頁 | `GovIntel AI｜跨機關公共事件整合、異動辨識與交班支援平台`，先聚焦公告更正到交班核對 | `PRODUCTION_ACTIVE` 的公開 baseline；名稱與歷史資料見 [Historical](../historical/README.md) |
| 任務 | 看 priority council issue、來源健康、官方影片與 timestamp | 由跨機關公告找出真正版本／時間變更，指出受影響主張，再回原文核對 | `IMPLEMENTED_NOT_PRODUCTION` 的 fixture／core；正式 production workflow 未完成 |
| 來源 | 五個議會／市政 publication source 與 S-010 影音證據 | source catalog v2、candidate police／city／traffic／fire sources、Twinkle + direct official routing | baseline `PRODUCTION_ACTIVE`；擴源 `CANDIDATE_CANARY`；Twinkle `DESIGN_ONLY` |
| 事件模型 | 文件／來源各自顯示，沒有 production public-event truth | 以獨立 `public_event_id` 保留多份 document identity、版本與衝突 | `IMPLEMENTED_NOT_PRODUCTION`；[fixture](../../../apps/web/public/data/public-event-demo.json) 明示 `FIXTURE_ONLY` |
| 更正與交班 | 歷史原型有來源 gap／LKG 與證據導航 | 17:00→16:00 版本更正、受影響 handoff claim、人工確認與版本化輸出 | core replay `IMPLEMENTED_NOT_PRODUCTION`；完整 #21／#23 acceptance `DESIGN_ONLY` |
| 背景資料 | 不把人口／統計作為主要產品證據 | period-bound population、agency、statistics enrichment；Twinkle 不增加獨立證據 | `DESIGN_ONLY`／`CANDIDATE_CANARY`；不代表即時人流或管轄 |
| 評估 | 舊 package 有本地工程／browser／Kiro receipts | A 人工、B/B0 基線、C GovIntel、C−AI／C−D，事件切分與真人任務 | `DESIGN_ONLY`；[evaluation manifest](./evaluation-manifest.template.json) `NOT_RUN` |
| 發布 | 舊紀錄有 2026-08 的 Pages／schedule／demo video receipt | publication-state、source freshness、failure outcome、匿名 bytes／hash 的嚴格 acceptance | `BLOCKED` 直到 [#20](https://github.com/Reese-max/taichung-police-intel/issues/20) 完成全部證據 |
| 安全邊界 | 公開資料、transcript navigation-only、無內部勤務資料 | 加入 provenance、locator、candidate promotion、conflict／unknown／rights guardrails | 目前規則與限制見 [LIMITATIONS_AND_SAFETY](./LIMITATIONS_AND_SAFETY.md) |

## 避免混淆

- 舊 `2026-08-23` deadline、舊 submission package、舊 2:43 video、Kiro usage 與舊 source snapshot 全部是 [Historical competition / prior prototype evidence](../historical/README.md)。
- 本次新增的 candidate、fixture、Issue、Open PR 與設計文件不能寫成已上線功能。
- 目前公開 snapshot 的 freshness 與 #20 的 publication acceptance 仍需按[現行狀態](./CURRENT_STATUS.md)的日期和 run URL 重查。
