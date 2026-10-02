# GovIntel AI｜資料來源矩陣

查核日：2026-10-02。下表的 `status` 是給評審看的五種固定狀態；[source-catalog.v2.json](../source-catalog.v2.json) 仍保留較細的 machine lifecycle label，兩者不要混讀。

## Current production baseline

| ID | 來源／角色 | 固定狀態 | 官方入口／可重播證據 | 邊界 |
|---|---|---|---|---|
| S-004 | 臺中市議會議事日程／`PRIMARY_EVENT` | `PRODUCTION_ACTIVE` | [official entry](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=49)、[source status](../../../apps/web/public/data/source-status.json) | endpoint health、date-window completeness、freshness 分開解讀。 |
| S-006 | 臺中市議會質詢順序表／`PRIMARY_EVENT` | `PRODUCTION_ACTIVE` | [official entry](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=50)、[source status](../../../apps/web/public/data/source-status.json) | 目前公開 snapshot 曾為 `FRESH`，不代表其他來源或未來 run 都新鮮。 |
| S-007 | 臺中市議會議事錄 API／`PRIMARY_EVENT` | `PRODUCTION_ACTIVE` | [official API](https://yishi.tccc.gov.tw/api/ProceedingsBackWeb/FrontList)、[source status](../../../apps/web/public/data/source-status.json) | API 成功不等於涵蓋所有日期；官方內容是證據，derived transcript 只作導航。 |
| S-009 | 臺中市議會各項提案 API／`PRIMARY_EVENT` | `PRODUCTION_ACTIVE` | [official API](https://yishi.tccc.gov.tw/api/Proposal/FrontList)、[source status](../../../apps/web/public/data/source-status.json) | 缺日期／不完整結果保持 unknown 或 gap，不補造「今天沒有」。 |
| S-029 | 臺中市政府議會專案報告／`PRIMARY_REFERENCE` | `PRODUCTION_ACTIVE` | [official entry](https://www.rdec.taichung.gov.tw/12047/12142/12145)、[source status](../../../apps/web/public/data/source-status.json) | 最新公開 snapshot 的 source health 為 `FAILED`／freshness `STALE`；LKG 不等於 current。 |
| S-010 | 臺中市議會質詢影音／`PRIMARY_EVENT` evidence path | `PRODUCTION_ACTIVE` | [official council site](https://www.tccc.gov.tw/)、[evidence drawer](../../../apps/web/app/page.js) | 官方影片時間軸優先；影片失效時不拿產品 demo video 替代。 |

`source-policy.approved.json` 的 active set 只有 S-004、S-006、S-007、S-009、S-029。公開 workflow 最近一次查核為 [run 36952506907](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907)；公開 source status 在 2026-10-02 09:46:41 +08:00 為 `PARTIAL`，其中 S-029 failed。這是資料快照，不是全域完整性保證。

## Candidate and reference expansion

| ID／群組 | 來源／角色 | 固定狀態 | Evidence | Promotion boundary |
|---|---|---|---|---|
| S-001 | 臺中市政府警政新聞／`PRIMARY_EVENT` | `CANDIDATE_CANARY` | [catalog entry](../source-catalog.v2.json)、[PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16)、[Issue #22](https://github.com/Reese-max/taichung-police-intel/issues/22) | list/detail、日期、分頁完整性、canary 與 Pages wiring 需各自驗收。 |
| S-019 | 臺中市政府市政會議／`PRIMARY_EVENT` | `CANDIDATE_CANARY` | [catalog entry](../source-catalog.v2.json)、[PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16) | audited existing 不等於已進入 current active source policy。 |
| S-031／S-032／S-033 | 消防即時災情、交通局最新消息、市府市政新聞／`PRIMARY_EVENT` | `CANDIDATE_CANARY` | [source strategy](../DATA_SOURCE_STRATEGY.md)、[Issue #22](https://github.com/Reese-max/taichung-police-intel/issues/22) | 候選觀察、隱私遮蔽、來源完整性與 promotion receipt 尚未完成。 |
| S-026／S-028／CTX-POP／S-034／S-035 | 法規、統計、人口、交通事故、SEGIS background／`PRIMARY_REFERENCE` or `ENRICHMENT` | `CANDIDATE_CANARY` | [source catalog](../source-catalog.v2.json)、[NPA matrix](../issue-28-npa-source-matrix.md) | reference／enrichment 不能冒充即時事件、即時人流或管轄／派遣依據。 |
| Twinkle overlay | cross-dataset discovery／history path | `DESIGN_ONLY` | [hybrid strategy](../TWINKLE_HYBRID_SOURCES.md)、[overlay config](../twinkle-source-overlay.v1.json) | 必須保留 direct official fallback、dataset ID、license、period、stale/conflict；Twinkle + direct official 不算兩個獨立佐證。 |

## Reading rules

1. `PRODUCTION_ACTIVE` 只表示已進入目前公開 path；它不承諾 freshness、完整 coverage 或事件真相。
2. `CANDIDATE_CANARY`、Open PR、Issue、catalog entry、fixture 和單次 live observation 都不能單獨升級成 production。
3. 每個重要主張保留 official URL、source/document version、observed time、content hash 或 locator；不以 collector time 冒充 official publish time。
4. source failure、partial coverage、stale data 與 valid zero-result 是不同狀態；失敗不可轉成零事件。
5. 新來源 promotion 前至少需要 bounded canary、完整性、重播、權利／敏感資料檢查與 current publication path evidence。
