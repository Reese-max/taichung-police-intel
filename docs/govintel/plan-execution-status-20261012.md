# GovIntel 補齊計畫執行追蹤

更新日：2026-10-12（UTC）  
工作分支：`codex/negative-control-hardening`  
狀態：程式驗收與範圍草案已完成；正式來源用途仍受真人治理決定阻擋。

## 已完成

- [x] 修補 current-checkout 負控制的假通過：破壞測試要求模組匯入成功且只有預期的 `candidate_context` 檢查失敗。提交 `3d4aa00`。
- [x] 完成修補後的 focused E2E 與正式專案 gate。驗收記錄顯示 `VERIFY_OK mode=full required=164 specs=4 secrets=0`；PostgreSQL rolling-budget 3 項通過；同 checkout Chromium 驗收 28/28。PostgreSQL 使用臨時容器，測試產物位於本機 runtime evidence，正式發布前仍應依發布版本重跑。
- [x] 取得並存證 S-029 官方資料開放宣告的頁面觀測，保留兩次 fetch 的 hash 差異，沒有推斷條款變更或作成授權決定。觀測見 [`source-reviews/2026-10-11/s029-official-terms-observation.json`](source-reviews/2026-10-11/s029-official-terms-observation.json)。
- [x] 核對來源權利審查封存包：20 筆來源的真人 reviewer 與正式 decision 均為空，權利狀態為 `UNKNOWN`，模型傳輸未獲准。
- [x] 建立 M1 第一批範圍草案與 AI 預審狀態紀錄：[`first-batch-scope-proposal-20261012.md`](first-batch-scope-proposal-20261012.md)、[`source-reviews/2026-10-12/ai-pre-review-status.json`](source-reviews/2026-10-12/ai-pre-review-status.json)。來源 ID 與封存包 20 筆完全相符；新增檔案未改動准入或 production 設定。

## P0：真人治理決定

- [ ] 確認是否採用 M1 日期窗口 `2026-09-01` 至 `2026-10-07`（含）、Asia/Taipei，以及 S-004／006／007／009／029 的文字範圍。
- [ ] 指定實際營運單位、內部受眾與業務目的。
- [ ] 指定有權作成來源／用途決定的真人角色；逐來源及必要時逐附件決定全文、展示／下載、摘錄、摘要、索引／向量、模型傳輸用途。
- [ ] 補齊保存期限、撤回／更正／刪除、備份清除規則。
- [ ] 查明既有 AI agent 預審使用的 agent／模型、服務商、輸入文件與雜湊、處理地區、留存／訓練條件，確認是否曾傳輸來源原文或附件。
- [ ] 如需模型處理，明確決定推論、嵌入、訓練、payload、區域及留存；完成前維持「未授權」。

## P1：M1 文字資料閉環

- [ ] 固定官方會期／會議 ID 清冊及日期分母；未知日期、缺頁、缺件逐筆標記。
- [ ] S-004 議程、S-006 質詢順序、S-007 議事錄、S-009 提案與 S-029 專案報告完成全文及官方附件 manifest。
- [ ] S-007 以整個範圍內會議的官方清冊驗證完整度；「警察局」關鍵字只作主題標籤，不能作完整分母。
- [ ] 日期分離保存：會期／實際會議日／列表日／發布日／附件修訂日／決議或辦理日／擷取時間。
- [ ] 收錄來源明確提供的歷史版本；無法驗證的版本史保留未知狀態。
- [ ] 完成至少一條議程或提案 → 議事錄 → 正式報告／答覆 → 精確頁碼引用摘要的回查驗收。
- [ ] 影音 S-010／S-011 排入後續批次，獨立完成 archive 枚舉、播放與字幕／時間戳驗收。

## P2：其餘資料模組與共用層

- [ ] 依計畫分別完成 M2 警政反詐、M3 交通活動、M4 市政決策、M5 消防災情、M6 法規預告、M7 地區背景與機關服務的來源範圍、日期口徑、附件／版本、欄位字典、用途審查及完整性驗收。
- [ ] 完成 L1–L6 共用層：權利准入、原件採集、解析定位、檢索 AI、異動追蹤、驗收維運。
- [ ] 來源拒絕／暫停服務沿用原狀態處理：S-001 等待官方入口正常恢復；S-034／S-037 不繞過已拒絕路徑。
- [ ] 取得正式正值查詢、資料新鮮度、coverage、拒答及正式環境證據後，再評估 production 驗收。

## 目前閘門

- 正式准入：`UNKNOWN / APPROVED_GOVERNANCE_MISSING`，活動資料仍 `RIGHTS_BLOCKED`。
- Production：`production_verified=false`；未推送、未部署、未啟用新來源。
- 模型傳輸：沒有來源傳輸授權；既有 AI 預審的實際傳輸情形仍待稽核。
- 正式上線：保持阻擋，直到真人治理決定與同版本正式驗收都完成。
