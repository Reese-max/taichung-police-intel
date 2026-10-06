# GovIntel AI－現行實作與證據狀態

核對日：2026-10-06（Asia/Taipei）。本頁以已合併 `main@5e2298c8db12ec2631dbd8dac11c14599133dcc6` 與 [發布 run 37472606240](https://github.com/Reese-max/taichung-police-intel/actions/runs/37472606240)為固定工程基準。匿名回讀及 15 項正式 Chromium 操作通過；[日期化證據包](closure-20261006/README.md)保存 release、checkpoint、generation、Worker version、公開五檔 hash 與各自的實際查核時間。之後的程式合併或蒐集另留 receipt，不將這一份記錄宣稱永遠最新。

**程式已發布，正式來源查詢仍 `RIGHTS_BLOCKED`。** `deployment_verified=true` 與 `production_verified=false` 同時成立：發布與受限拒答通過，逐來源正式權利尚未准入；不是已能返回核准官方內容的正值查詢。原始 v6 的 85%／90%／30%、20 事件／60 文件及 3–5 位試用者仍是計畫，沒有模型／真人效益結果。

產品／構想書工作題名為 **GovIntel AI－公共資訊查詢與個人化追蹤平台**。主辦已確認收妥補件、未接受更名；原報名完整題名、代表資格與封面指示仍待確認。不能把產品工作題名當已核准的正式報名題名。

## 能力與證據

| 項目 | 現況 | 實際證據及限制 |
|---|---|---|
| Pages／Worker／metadata 查詢 | 已發布、受限拒答通過 | [query receipt](closure-20261006/production-query.json)：release／hash 綁定，正式准入 `UNKNOWN`；不回傳未核准內容，也不把空結果稱為世界沒有事件。 |
| 公開查詢／來源狀態 | 已發布並驗收受限流程 | [browser receipt](closure-20261006/production-browser.json)：可見條件、五來源狀態、修訂／未知日期、LKG 與取得失敗。正式正值結果及原文閉環仍待 [#161](https://github.com/Reese-max/taichung-police-intel/issues/161)。 |
| 同瀏覽器條件追蹤 | 保存、修改、刷新、取消、清除通過 | 真實正式頁面操作；來源不足時 `baseline_complete=false`，沒有真實官方新版本／明文解除的完整成功情境。不含關站推播、跨裝置帳號或真人成效。 |
| 合成道路 v1→v2→v3 | 可離線重播 | [必要 CI 樹綁定](closure-20261006/ci-tree-binding.json)：有限合成 UI 22 項、loopback runtime 28 項；結束日延長、時段不變、已讀、取消與明文解除不是正式即時公告。 |
| 研究／文件／摘要 | 已發布、provider 停用 | 正式 browser 核對 `RIGHTS_BLOCKED`、`provider_transmission_attempted=false`；未送未准入資料給模型。文件及 synthesis 的正值能力仍需權利與正式原件驗收。 |
| D1 歷史人口／戶數背景 | 固定樣本取得、正式 UI 值通過 | 112Y12M／2023-12、全國 368 記錄／臺中 29 區；西屯 235,441 人／94,971 戶。不是目前人口、人潮、管轄或 AI 效益；來源 promotion／真人理解另驗。 |
| D2 警察機關名錄 | 原始資源受阻 | dataset 5958 metadata 可讀，原檔／native TGOS 403；資料列、列數、CRS 未核對。不得以中繼資料成功或 POINT_X/Y 字樣代替資料取得。 |
| 四類候選來源 | 尚未 promotion | S-032／S-033 各 7 有效日；S-031 只有 1 有效日；S-001 為 0。日數不替代權利、獨立完整性與欄位核對；S-019 不是 v6 首期四類。 |
| 發布告警與恢復 | 真實 upload drill 通過 | [drill receipt](closure-20261006/alert-drill.json)：實際 artifact 失敗→成功，通知送達 #20 並核對 body hash。尚未驗真實 Pages／Worker deploy 失敗、source recovery 或真人閱讀。 |
| 資料問題回報 | 正式入口已驗收 | 固定 GitHub 表單連結，不帶查詢／追蹤資料；使用者自行提交。維運接案人員、處理時限與完成更正案例仍未確認。 |
| 語意 AI／真人比較 | 正式評測未執行 | 12 合成開發題調整 prompt 後 11/12→12/12 是診斷；沒有獨立保留集、真人 A/B/C、訪談或採用成果。 |
| 工時、負荷、成本、作品權利 | 已補可核對清冊，仍待輸入 | [資源觀察](closure-20261006/resource-observation.json)、[依賴宣告授權](closure-20261006/dependency-license-inventory.json)、[新舊差異](OLD_VS_NEW.md)。沒有發票、尖峰負載、真實工時、作品權利人同意或官方創新比例認定。 |

## 來源與日期

核准 active 集合維持 S-004／S-006／S-007／S-009／S-029。`source-policy.approved.json` 的 legacy active 與逐用途權利准入是不同條件，不能以 active 名稱推論正式查詢權利。

[五檔回讀](closure-20261006/production-bytes.json)固定 `CR-DEMO-20261006-MORNING-MANUAL`：S-004 為 FRESH；S-006 有已核對附件修訂日 2026-09-01、首次發布日未知，VERY_STALE／PARTIAL；S-007 觀察到的官方 API 記錄截至 2026-05-27，STALE；S-009 首次發布日期未知、PARTIAL；S-029 CONNECTIONERROR、PARTIAL，保留 LKG。重新取得或檢查時間沒有被填成官方日期。Schema drift 仍 BLOCKED，沒有藉發布成功抹除來源缺口。

[候選七日窗口](../source-reviews/2026-10-06/candidate-window-actual.json)使用原始日期化 receipts；[待覆核 packet](../source-reviews/2026-10-06/README.md)已修復斷掉的證據引用並保留實際條款取得資料。`reviewer`／`decision` 仍未填，沒有 Codex 代簽權利核准。沒有四類來源已正式啟用、全臺完整涵蓋或每日新增 100–200 筆的證據。

## 參賽與剩餘 gate

v6 是 2026-10-01 的原始 10 頁 A4 送件版本，保留原件；本頁不改寫當日承諾，也不自行重交。10/5 的補件收妥確認與「更名未接受」已由 [PR #168](https://github.com/Reese-max/taichung-police-intel/pull/168)統一；它不代表代表資格、資料「或／與」要求或作品權利已核准。

行政核對見 [registration-verification](registration-verification-20261006.md)；實作逐頁對照見 [PROPOSAL_MAPPING](PROPOSAL_MAPPING.md)；完成與未完成條件見 [ACCEPTANCE_CHECKLIST](ACCEPTANCE_CHECKLIST.md)及 [總清單 #62](https://github.com/Reese-max/taichung-police-intel/issues/62)。歷史 receipt 保留在 [VERIFICATION](VERIFICATION.md)，不以新 SHA 重標舊驗收。

## 10/6 晚間新觀察

[晚間手動蒐集](closure-20261006/evening-collection.json)的 S-029 已恢復可讀與 PASS，但官方 7/24 日期仍 STALE，其他日期缺口不變。Pages 五檔 hash 通過；初次 Gateway 檢查 503 令該 run failure，稍後完整匿名 verifier 已通過並另留 receipt。S-019 只有本次 contract probe 可讀，不自動成為七日資格；S-001 仍令 schema drift BLOCKED。上方晨間收據保留，正式 browser 15 項不改標為新的晚間 generation。

10/6 22:06 Asia/Taipei 的[新一輪真實 canary](closure-20261006/current-candidate-observation.json)已獨立留證：S-019 有第一個有效當地日；S-031 仍只有同一天的 1 日，重跑不增加日數。S-032／33 仍需權利及獨立完整性，S-001 失敗；全部未 promotion。較早七日窗口及其失敗不改寫，S-019 至少還需六個未來實際有效日。
