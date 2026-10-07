# 2026-10-07 待辦修復與有界驗證

本輪基線為已发布 PR #177 的 `b6a602580fddf8b4a634dbdf64fc255678495412`。此文件記錄可重現的修復與來源樣本 metadata，不宣稱尚未執行的新發布成功。發布後的實際 CI、部署、public bytes、Gateway 與 Chromium 證據另由精確 commit/run 收據記錄。

- 模型診斷：部分回覆不再以成功退出；未齊全的結果保留為 `INCOMPLETE`。輸入 ID 不合規或重複時，在 transport 與 output 建立前拒絕。這是離線虛構回歸，不是產品模型實測。
- 來源健康：取得缺口優先呈現，同時保留日期陳舊／未知。缺少核准來源收據與時區不明的取得時間不再產生完整或可比較的成功時間。正式站新增逐來源待辦展示。
- 統計粒度：候選 key 保留完整 ASCII 日期；非 ASCII 日期隔離而不合併。S028、CTXPOP 實際原始 JSON 的 hash 未變，結構診斷仍為 `QUALITY_ISSUES`，未允許彙整、相加或刪除重複資料。[最新安全結構收據](../reference-grain-reconciliation-v2-20261007.json)保留前一版 hash。
- S019：四個原始 ODT 各一次正常 GET，均 200 且 hash 與先前一致。正文序號 733／732／731／730，明確會議日 9/15、9/8、9/1、8/25；四份列表期內文件只有三個樣本會議日在期間內。[日期角色證據](../source-s019-odt-meeting-metadata-20261007.md)保留列表日期，不推定全期間只有三場。
- S001：13:46 UTC 原入口單次 GET 仍 503，未取得 body。[安全收據](../s001-original-availability-20261007T1346.json)未猜測原因或建立其他入口。

[本地回歸收據](focused-validation-safe.json)的五個不重疊 suite 共 123 tests，全部通過、零 skip。完整專案 gate 與 CI 必須另行通過；本文件不把獨立 probes 再加進總數。最初獨立 core review 的四個問題及修正後覆核均保留，沒有覆寫失敗歷史。

四個 ODT 正文未保存或送入模型；新 parser 的 ZIP／XML／字數限額與不明日期拒絕只驗證有限 metadata extraction，沒有完成文件 rendering、PDF 對照或官方 registry identity。原四份 ODT 收據固定較早 parser hash；後續民國年與星期衝突修復只經虛構 ODT 回歸，未重標原件為新版實測。全部 15 個來源的業務完整範圍仍為 `NOT_VERIFIED`。使用權利、候選准入、真人與產品語意 AI 實測、完整會議期間及修訂歷史仍待真實證據；自然日與正式 provider 狀態不因本輪修復增加。

Claude 實際公開 code-only 覆核固定 PR #178 的較早 head `55c118a901d083e7333392abd14f24c3a7bd0844`，[安全 invocation 收據](claude-original-public-review-invocation.json)不宣稱較後 head 再由 Claude 審查。提出的健康時鐘優先順序、隱藏 ODT／獨立時間欄位／XML tail 限額及來源身分待辦，都以虛構離線案例重現與修復。原 115 項收據另留，[最新五 suite](focused-validation-safe.json)共 123 項不重疊 tests；新修正仍需精確樹 CI 與發布後實際驗收。

[最終獨立重播](claude-findings-replay-safe.json)核對六個現行公開模組的精確 bytes，確認五項 Claude finding 的修正與有界相容性。39 個獨立 probes 不是上述 123 項 suite 的附加產品指標；未呼叫 provider、未重新解析原四份 ODT，也未核准來源完整性或權利。
