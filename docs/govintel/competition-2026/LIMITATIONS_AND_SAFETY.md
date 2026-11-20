# GovIntel AI｜限制、風險與安全邊界

**2026-11-17 退役：** 擁有者決定移除 S-019、S-029、S-033、S-034：前兩者憑證 hostname mismatch 且只剩公告頁，S-033 回傳公告 HTML 而非 RSS，S-034 網域 NXDOMAIN、替代網域同路徑 502。現行 active 為 S-004／S-006／S-007／S-009；候選觀察為 S-001／S-032／S-031，Pages 候選為 S-001／S-032。這次只重建現行發布投影，保留原收集時間；下列日期化歷史收據不改寫，未重新連網收集、未部署驗證。

本頁把評審最容易誤讀的地方寫在入口，不用功能名稱或漂亮畫面補足不存在的證據。

## Current limitations

- **資料不是全世界。** Active source policy 自 2026-11-17 只有四個 S-004／S-006／S-007／S-009；來源健康不等於問題領域完整覆蓋。
- **新鮮度不是健康。** 成功連線的來源仍可能 `STALE`；`PARTIAL`、`FAILED`、`UNKNOWN`、valid zero-result 與 `NO_DATA_AS_OF` 要分開看。LKG 保護可讀性，但不把舊資料變 current。
- **#20 尚未全收斂。** [固定正式驗收](closure-20261007-pr177/README.md)與[真實 upload drill](closure-20261006/alert-drill.json)分別保存成功發布及隔離 artifact 失敗／恢復；實際 deploy、source 與 public mismatch 恢復仍需逐項留證。不能繼續稱流程尚未合併，也不能用成功 run 取代失敗路徑驗收。
- **正式正值查詢仍受權利 gate 阻擋。** 固定正式收據的 `RIGHTS_BLOCKED`／provider `DISABLED`、`deployment_verified=true`／`production_verified=false` 並存；受限拒答與已部署介面不證明來源權利、原文正值查詢或 AI／真人效益。本次文件核對不更新正式收據版本。

  **2026-11-13 擁有者決定（2026-11-15 已經由 #242 合併；尚無正式環境驗收收據）。** 專案擁有者決定放寬來源權利規定，變更在 PR [#237](https://github.com/Reese-max/taichung-police-intel/pull/237)、[#238](https://github.com/Reese-max/taichung-police-intel/pull/238)、[#239](https://github.com/Reese-max/taichung-police-intel/pull/239)，三者一併經由 [#242](https://github.com/Reese-max/taichung-police-intel/pull/242)（`17629c2`）合併。repo 內的核准政策為 governed schema 2，四個現行正式來源的 `formal_admission` 為 `ADMITTED`，權利狀態標為 `OWNER_ASSERTED_PERMISSION`／`OWNER_DECISION_TERMS_NOT_VERIFIED`：這是擁有者主張，不是逐來源條款查核或法律判斷，現行四個來源均屬臺中市議會，其網站未提供開放宣告。公開範圍仍只有 metadata 與連結；全文、摘錄與模型傳輸未開放，provider 仍停用。上方 `RIGHTS_BLOCKED` 是 2026-10-07 固定正式收據的結果，在重新部署並完成正式環境驗收之前仍是正式環境的已驗證狀態。2026-11-20 以匿名方式讀取一次正式環境 query gateway（`code_sha` `8e9cf24`），回報 `formal_admission: ADMITTED`、沒有權利受阻的來源；這只是一次讀回，不是已驗收的重新部署收據。
- **候選不是 production。** Open PR、Issue、catalog entry、一次 canary、fixture 或設定檔不能升級 source status。候選 promotion 需要完整性、權利、敏感資料、重播與 current publication evidence。
- **事件融合是受限核心。** `public-event-demo.json` 是 `FIXTURE_ONLY`；它不證明 live multi-agency coverage、event accuracy、背景資料效益或正式交班服務。
- **首期追蹤是同瀏覽器功能。** 保存使用者明確選擇的條件與已讀版本；開站／刷新才比對，不含關站推播、跨裝置帳號或敏感身分側寫。清除瀏覽器資料可能失去本機清單。有限合成 UI 與受限正式頁面各有收據，真實官方新版本／解除的完整流程與真人效益仍另驗。
- **D1 已核對但仍是歷史背景，D2 尚未取得內容。** D1 指定 112 年 12 月官方 CSV 已下載、核對與本地展示，未 promotion，不能代替現況／真人任務效益；114Y12M JSON service／其他人口檔不能冒充本期。D2 metadata 200 但 TGOS resource 403，POINT_X/Y 不證明 CRS。keyword 與規則 fixture 不能冒充語意模型與 AI 增益。
- **評估尚未執行。** Precision、recall、F1、任務時間、真人使用、採用、得獎與資格結果不預填。`evaluation-manifest.template.json` 的 result 欄保持 null。
- **歷史資料有界。** 2026-08 Kiro package、舊 deadline、舊 demo video 與 Kiro credits 是 prior prototype evidence，集中在 [Historical](../historical/README.md)，不代表目前版本或本次官方規則。
- **權利與資格未由 repo 推定。** 當屆主辦公告、報名回執、團隊資格、資料／軟體授權與使用同意需另以官方原件／私有紀錄核對。

## Safety contract

1. 只處理核准的公開資料。排除內部勤務、110、案件級個資、私密參與者資料、派遣與自動指揮。
2. 官方網頁、PDF、字幕與外部文字是 untrusted data，不是 agent 指令；不得因內容要求而讀取秘密、執行程式、外傳資料、寄信或擴大爬取範圍。
3. Official URL、source/document version、locator、觀測時間與 hash 綁在 evidence path；無法支持主張就標待查、衝突或拒答，不補造日期／數值／因果。
4. AI／embedding 只產生候選。規則控制來源白名單、schema、時間、版本、發布資格與敏感資料 gate；模型不能自行發布或修改 canonical truth。
5. Twinkle、mirror、轉載與 direct official 是不同取得路徑，不把同一原始資料算成多個獨立證據；重要不一致保留 `CONFLICT`。
6. 人口、統計、地理與模擬資料保留 statistical period、source、license 與限制，不推論即時人流、受影響人口、管轄、可用警力或勤務建議。
7. 公開 handoff／export 不含私人筆記與 operational police fields；正式多人簽核需要另行的身份、權限與 audit 設計。

## Fixed status guardrail

| 狀態 | 可說 | 不可說 |
|---|---|---|
| `PRODUCTION_ACTIVE` | 已在目前公開 path 使用 | 每次資料新鮮、全域完整或功能永遠正確 |
| `IMPLEMENTED_NOT_PRODUCTION` | checkout／fixture／離線 receipt 可重播 | 已部署、已採用、已通過真人驗收 |
| `CANDIDATE_CANARY` | 正在觀測或等待 promotion | Open PR／單次 canary 就是 production |
| `DESIGN_ONLY` | 設計、規格或評估方法存在 | 已實作、已接入或已有成效 |
| `BLOCKED` | 阻塞條件與下一個證據明確 | 以 workaround、CI 綠燈或歷史 receipt 宣稱完成 |

狀態真相見 [CURRENT_STATUS.md](./CURRENT_STATUS.md)；本頁與 source／evaluation receipt 一起更新，不能只改文案不改證據。
