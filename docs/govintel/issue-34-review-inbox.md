# Issue #34 — Review Inbox

## Current contract

- `intel_v2/review.py` 與 `scripts/review-inbox.py` 是唯一能修改 canonical Review Inbox state 的 writer；公共 `system-health.json` 只投影 bounded IDs、狀態、來源版本與 evidence hash，不公開私有 evidence／audit。
- V2 Dashboard 會讀取該公共投影，並在瀏覽器 `localStorage` 保存獨立的 `LOCAL_REVIEW_OVERLAY`。overlay 以 `review_id + source_version + evidence_sha256` 綁定；來源版本或 evidence hash 改變時自動重開本機決策，不會自動關閉來源失敗的項目。
- 「保持追蹤」、「標記已處理」、「暫時忽略」及「重新開啟」都是本機操作；可匯出 Markdown，明確標示不修改 canonical state、原始文件或公開發布物。
- 合併、拆分、修正 mapping 尚未在 static page 假裝成可寫入操作，仍須由 canonical writer 執行。

## Verification

`apps/web` 的 `npm test`：280 tests pass；覆蓋 dedupe、local decision、版本變更重開、storage round-trip、binding tamper rejection 與 Dashboard boundary copy。
