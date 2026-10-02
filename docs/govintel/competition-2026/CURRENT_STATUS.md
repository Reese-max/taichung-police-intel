# GovIntel AI｜現行實作與證據狀態

查核日：**2026-10-02**。程式基準：`main@562141e396c693e115b3fce10594c434c401a58e`。
本頁是評審入口的狀態真相；每次狀態更新都應重新填入查核日期、版本與證據，不把舊 receipt 重新標成 current。

## 固定狀態語意

能力表只使用以下五種狀態：

- `PRODUCTION_ACTIVE`：目前公開產品／發布路徑正在使用的能力；不代表每次資料都新鮮或完整。
- `IMPLEMENTED_NOT_PRODUCTION`：程式、fixture 或離線 receipt 可重播，但尚未證明正式公開服務、promotion 或真人使用。
- `CANDIDATE_CANARY`：候選來源或流程正在觀測／等待 promotion；Open PR 不是 production 證據。
- `DESIGN_ONLY`：有設計、規格或評估方法，尚未宣稱可用。
- `BLOCKED`：有明確阻塞條件；在證據補齊前不得說已完成。

`NOT_RUN`、`UNVERIFIED`、`STALE`、`PARTIAL` 等字樣只描述某一份評估或資料 receipt，不是能力狀態，不能取代上面五種狀態。

## 能力狀態

| 能力 | 固定狀態 | 評審可重播／核對的證據 | 不可由此推出的結論 |
|---|---|---|---|
| 五個議會／市政來源的 publication baseline（S-004、S-006、S-007、S-009、S-029） | `PRODUCTION_ACTIVE` | [核准 source policy](../source-policy.approved.json)、[source-status contract](../../../scripts/source-status-contract.mjs)、[目前公開資料](https://reese-max.github.io/taichung-police-intel/data/source-status.json) | `PASS` 不等於新鮮、完整或涵蓋所有公共事件。 |
| Council brief、來源健康／缺口、官方影音與 transcript navigation | `PRODUCTION_ACTIVE` | [現行 Web app](../../../apps/web/app/page.js)、[current-checkout verifier](../../../scripts/verify-current-checkout.py)、[公開 demo](https://reese-max.github.io/taichung-police-intel/) | transcript 是導航文字，不是人工核准的逐字證據；影片播放受官方 CDN 狀態影響。 |
| Source Policy、query coverage、官方文件定位、受限 Query Gateway、角色排序、local-first handoff、Review Inbox | `IMPLEMENTED_NOT_PRODUCTION` | [Source Policy integration](../issue-49-source-policy-integration.md)、[official document replay](../issue-48-official-document-replay.md)、[runtime boundaries](../issue-30-runtime-boundaries.md)、[handoff flow](../issue-23-handoff-flow.md) 與各自 self-check | 尚未因此宣稱正式跨機關部署、多人簽核、機關採用或 production domain-store coverage。 |
| PublicEvent 事件融合 | `IMPLEMENTED_NOT_PRODUCTION` | [fixture-only JSON](../../../apps/web/public/data/public-event-demo.json)、`python -X utf8 scripts/public-event-fusion.py --self-check`、[Issue #24 design](../issue-24-public-event-fusion.md) | fixture 的三個來源、衝突與時間更正不是 live collector 或準確率結果。 |
| 新聞／市政／交通／消防候選 collector（S-001、S-019、S-031、S-032、S-033） | `CANDIDATE_CANARY` | [source catalog v2](../source-catalog.v2.json)、[PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16)、[Issue #22](https://github.com/Reese-max/taichung-police-intel/issues/22) | Open PR、list adapter、單次觀察或 fixture 都不等於 promotion 或正式 Pages source。 |
| Twinkle + direct official hybrid、latest-information loop、完整跨機關即時覆蓋 | `DESIGN_ONLY` | [Twinkle strategy](../TWINKLE_HYBRID_SOURCES.md)、[source strategy](../DATA_SOURCE_STRATEGY.md)、[Issue #21](https://github.com/Reese-max/taichung-police-intel/issues/21) | 沒有把設計文件寫成已存在的 Twinkle client、完整正文複查或即時 SLA。 |
| 完整跨日 persistent tracking／版本化交班 acceptance | `DESIGN_ONLY` | [Issue #23 design](../issue-23-handoff-flow.md)、local-first handoff 的離線 self-check | local replay 不能代替正式持久服務、權限、通知或真人驗收。 |
| Production event fusion、背景資料單一路徑 promotion | `DESIGN_ONLY` | [Issue #24 design](../issue-24-public-event-fusion.md)、[NPA source matrix](../issue-28-npa-source-matrix.md) | PublicEvent fixture 不能宣稱 live multi-agency fusion 或背景資料效益。 |
| 排程發布的完整晨／晚、失敗演練與匿名版本／hash 完成定義 | `BLOCKED` | [Issue #20](https://github.com/Reese-max/taichung-police-intel/issues/20)、[latest scheduled run 36952506907](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907) | 這次 run 成功不等於 #20 全部 acceptance 已完成；仍須補齊 issue 定義的證據。 |

## Claim / evidence table

| Claim | Status | Evidence |
|---|---|---|
| 五個原始來源 publication | `PRODUCTION_ACTIVE` — production baseline | [source policy](../source-policy.approved.json)、[public source status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)、[latest scheduled run](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907) |
| 新聞／市政／交通 collector | `CANDIDATE_CANARY` — open PR / candidate | [PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16) 與追蹤 promotion 缺口的 [Issue #22](https://github.com/Reese-max/taichung-police-intel/issues/22) |
| Twinkle hybrid | `DESIGN_ONLY` — design/config | [TWINKLE_HYBRID_SOURCES.md](../TWINKLE_HYBRID_SOURCES.md)、[twinkle-source-overlay.v1.json](../twinkle-source-overlay.v1.json) |
| latest-information loop | `DESIGN_ONLY` — design / issue | [Issue #21](https://github.com/Reese-max/taichung-police-intel/issues/21)、[detail-recheck design](../issue-21-detail-recheck.md) |
| persistent tracking | `DESIGN_ONLY` — planned / issue | [Issue #23](https://github.com/Reese-max/taichung-police-intel/issues/23)、[handoff design](../issue-23-handoff-flow.md) |
| production event fusion | `DESIGN_ONLY` — planned / issue | [Issue #24](https://github.com/Reese-max/taichung-police-intel/issues/24)、fixture boundary in [public-event-demo.json](../../../apps/web/public/data/public-event-demo.json) |
| scheduled publishing | `BLOCKED` — #20 + latest Actions evidence | [Issue #20](https://github.com/Reese-max/taichung-police-intel/issues/20)、[run 36952506907](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907) |

## Latest dated evidence

- The latest scheduled workflow checked during this update was [run 36952506907](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907), `success`, `head=562141e`, created `2026-10-02T01:46:06Z` and completed `2026-10-02T02:16:21Z`.
- The anonymous public `source-status.json` read during this update reported `generated_at=2026-10-02T09:46:41+08:00`, collection run `CR-DEMO-20261002-MORNING-SCHEDULE`, overall `PARTIAL`, five sources and one `FAILED` source. S-004 and S-007 were `STALE`; S-006 and S-009 were `FRESH`; S-029 was `FAILED`/`STALE`.
- The same public read proves reachability and a time-bound snapshot, not complete freshness, full source coverage, a human evaluation, eligibility, or a submission receipt.
- The checked-in [`apps/web/public/data/source-status.json`](../../../apps/web/public/data/source-status.json) is an older `2026-09-11` snapshot. Do not compare it with the public endpoint as if it were the latest deployment.

## Evidence update rule

When a source, feature, deployment, evaluation, or competition claim changes, update this page and the linked receipt together. Keep the old receipt dated and labelled historical; never promote a design file, Issue, Open PR, CI pass, HTTP 200, or fixture to a production claim by wording alone.
