# Issue #24 — conservative PublicEvent fusion

本地已加入 `scripts/public-event-fusion.py`：

- 只有明確 `authority=official`、HTTPS URL 的 normalized document 可進入 canonical fusion；media/discovery 不能直接成為事件證據。
- `public_event_id` 與文件 `document_id/document_version_id` 分離；穩定 named/cross-reference ID 加日期才自動聚類，缺身分或日期保持 `CANDIDATE`。
- 同角色時間／地點／狀態矛盾為 `CONFLICT`；時區等價值先正規化，不同 `fact_scope` 不製造假衝突；同源 direct + Dashboard 不增加 independent source count。
- partial snapshot 以 `PARTIAL_LKG` 保留既有事件與文件版本，不推論取消；版本更新維持同一 `public_event_id`。
- `confirm_candidate`、`merge_public_events`、`split_public_event` 保留 manual history 與舊 ID；背景資料只有 confirmed、精確行政區、明確期別／值／單位／HTTPS source 才能附加，並標示 `BACKGROUND_ONLY`。

驗證：`tests/test_public_event_fusion.py` 13 tests pass；`node --test apps/web/tests/public-event-fusion.test.mjs` 2 tests pass；CLI self-check 會重播三來源、版本衝突與背景資料防護。

尚未宣稱完成的外部／產品 gate：現有正式 collector 尚未把真實來源輸出轉成此 normalized document contract；公開頁尚未提供寫入型 event merge/split UI；未做 production deployment、真實來源收件或跨服務持久化。這些仍需以 #14/#27/#47 的整合驗收接續，不以合成 fixture 代替。
