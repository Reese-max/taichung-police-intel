# Issue #21 — detail recheck core

本地已加入 `intel_v2.detail_recheck` 的 transport-neutral B 核心：

- `plan_recheck()` 以可設定 TTL 判斷是否到期，並在到期時保留 `If-None-Match` 與 `If-Modified-Since`。
- `classify_observation()` 保留 hash-only document version，區分 `UNCHANGED`、`PRESENTATION_ONLY`、`MATERIAL_CHANGE`、`ATTACHMENT_CHANGED`、`NOT_MODIFIED`、`DEFERRED` 與 `UNAVAILABLE`。
- 304 不宣稱附件未變更；暫時失敗保留 last-known-good，且不自動取消事件。
- 實質或附件變更標記 `review_required`，並保留 before/after 版本供既有 Review Inbox 與 handoff 層接續處理。

驗證：`tests/test_detail_recheck.py`，7 tests pass。

刻意保留的界線：目前只有共用判定核心，尚未接上 live HTTP collector、排程、資料庫寫入或 7-day canary，因此不能宣稱 production recheck、公開部署或真實來源收件已完成。
