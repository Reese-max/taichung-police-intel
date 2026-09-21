# Issue #23 — persistent watch and versioned handoff

本地 first version 已接通：

- `state/v2-handoff-state.json` 保存跨日 watch、異動 invalidation 與 confirmed handoff history。
- confirmed handoff item 另保存 deterministic `claim_id`、source document version 與 evidence locator；來源更新的 invalidation 只列出真正引用舊版本的 brief/claim。
- `scripts/build-v2-shadow-brief.py` 從 persistent state 投影 `tracking_items`；零新異動不會清空 active watch，首頁最多顯示 5 件但 `tracking_total` 保留完整數量。
- `scripts/handoff-state.py` 提供 idempotent `watch`、`confirm`、`resolve`、`dismiss`、`status`、`export`，confirmed v1 不會被來源更新靜默覆寫。
- DB detail recheck 的 hash-only 結果可先送入 `scripts/review-inbox.py reconcile --input`；對已追蹤且已確認的 claim，明確執行 `scripts/handoff-state.py recheck --detail-rechecks <path>` 才會建立 idempotent invalidation、回開 `NEEDS_REVIEW` 並保留 affected claim。
- `python scripts/handoff-state.py self-check` 重播「加入追蹤 → 跨日保留 → 來源 v2 → NEEDS_REVIEW → 確認 v2 → exact locator」流程。

驗證結果：`HANDOFF_DEMO_OK cross_day=true invalidation=true versions=1->2 exact_locator=true`；handoff state tests 另驗證 claim 依賴回指。

邊界：GitHub Pages 是 static export，公開 Web 目前唯讀；watch／確認／匯出寫入仍由 local CLI 完成。尚未宣稱 production deployment、多人協作、通知或離線檔案自動更新。
