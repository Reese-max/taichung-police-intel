# 附原文出處的變更摘要（AI 候選＋人工覆核）

狀態：`IMPLEMENTED_NOT_PRODUCTION`。程式與合成 fixture 可離線重播；沒有接入公開站、沒有處理任何正式來源內容、沒有精確度或效益結果。provider 預設 `DISABLED`。

## 目的

官方公告版本改變時（延長日期、地點更動、狀態改變），讓人快速看到「改了什麼」，且每一句都能回到原文逐字核對。這是 [LIMITATIONS_AND_SAFETY](competition-2026/LIMITATIONS_AND_SAFETY.md) 安全契約第 3、4 點的具體實作：模型只產生候選，規則決定能不能留下，人決定能不能發布。

## 流程

```text
before/after 官方文字 ──(權利 gate)──> provider 草稿(JSON) ──> 逐句規則驗證 ──> PENDING_REVIEW
                                                                      │
                                                  Review Inbox NEEDS_REVIEW ──人工 CONFIRM──> release()
```

1. **權利 gate**：`rights_status` 不是 `PUBLIC_DERIVED` 且不是合成資料，直接 `RIGHTS_NOT_ADMITTED`，不呼叫 provider。目前五個來源仍 `RIGHTS_BLOCKED`，所以正式內容不會被送出。
2. **Provider**：預設 `DisabledProvider`。只有 `GOVINTEL_SUMMARY_PROVIDER=anthropic` 且有 `ANTHROPIC_API_KEY` 才啟用；測試用 `ScriptedProvider` 不連網。官方文字放在 `<before>`／`<after>` 界線內，系統提示聲明其為不可信資料。
3. **逐句驗證**（`verify_statement`），任一失敗該句丟棄並記錄代碼，不修補：
   - 輸出必須是只含 `statements` 的嚴格 JSON；欄位多一個就拒絕。
   - `claim_type` 僅 TIME／LOCATION／STATUS／AGENCY／TEXT_CHANGE；**CAUSE 一律拒絕**（原因須寫「官方來源未說明原因」）。
   - 每個 `quote` 必須逐字出現在對應側的原文（NFC＋空白正規化），並寫入位置 `start/end` 與 `quote_sha256`。
   - 至少一個 quote 只存在於一側，否則視為只引用沒變的文字。
   - 句中每個數字必須出現在所引 quote；句中不得有網址。
4. 全部被拒絕時回 `REFUSED / NO_SUPPORTED_STATEMENTS`，不顯示任何摘要。
5. **人工覆核**：`review_candidate()` 產生 Review Inbox 的 `NEEDS_REVIEW`，`source_version` 綁定 `summary_sha256`。`release()` 只在該項為 `RESOLVED`＋`CONFIRM` 且版本收據與目前摘要 hash 相同時回傳可發布紀錄；摘要內容一變，舊核可即失效。發布紀錄帶 `ai_disclosure`（provider、model、prompt 版本、角色＝僅候選草稿）與「AI 產生、經人工核對；請以官方原文為準」標示。

## 重播

```bash
python3 scripts/summarize-change.py            # 合成公告，離線，不送出任何資料
python3 -m unittest tests.test_change_summary -v
```

## 公開站 Demo 頁

靜態頁 `/change-summary/`（尚未加入主導覽，直接用網址開啟）顯示合成公告的新舊原文、AI 候選摘要、逐句引文與其位置，並標明 `FIXTURE_ONLY`／待人工覆核／模型為 `SCRIPTED_FIXTURE`。頁面在瀏覽器內重新核對每段引文都等於所顯示原文的對應位置，不符或 `published`／狀態不對就不顯示摘要。資料檔由下列指令重產：

```bash
python3 scripts/summarize-change.py --web-demo apps/web/public/data/change-summary-demo.json
```

只接受合成 fixture；不接任何正式來源或 feed。

## 診斷評測（尚未對真實模型執行）

`scripts/evaluate-change-summary.py` 對 `eval/change-summary/v1/` 的 8 組**合成**前後文跑摘要器，計算：變更案例是否涵蓋應有的新值、應拒答案例（只改標點／空白）是否拒答、錯誤拒答、禁止出現的值（如被注入指令誘導的「已取消」、未寫出的原因）以及各驗證拒絕代碼的次數。

```bash
python3 scripts/evaluate-change-summary.py                       # provider 停用 → evaluation_status=NOT_RUN，不填任何指標
python3 scripts/evaluate-change-summary.py --responses eval/change-summary/v1/scripted-responses.json   # 離線自我測試
GOVINTEL_SUMMARY_PROVIDER=anthropic ANTHROPIC_API_KEY=... python3 scripts/evaluate-change-summary.py --output <新檔案>
```

- `scripted-responses.json` 是專案自寫的腳本回應，只用來驗證 harness 本身，**不是模型結果**，其 8/8 不能引用為成效。
- 範圍固定為 `SYNTHETIC_DIAGNOSTIC_NOT_HOLDOUT`：只送合成案例、不送任何官方紀錄；不是獨立保留集，也不是 `EVALUATION.md` 的 C 對 C_semantic_off 比較。
- 輸出檔不覆寫舊結果，每次嘗試另存新檔。

## 不代表什麼

- 驗證證明「引文存在、數字有出處、有引用變更處」，**不證明**摘要語意完全正確或沒有遺漏；所以人工覆核是必要的。
- 目前沒有正確率、精確度／召回率或節省時間的量測；`EVALUATION.md` 的保留集與真人比較仍 `NOT_RUN`。
- 合成 fixture 的結果不能當作正式來源上的 AI 成效。
- 簡章要求揭露 AI 工具：本功能使用的模型名稱與用途以每筆摘要的 `ai_disclosure` 為準。
