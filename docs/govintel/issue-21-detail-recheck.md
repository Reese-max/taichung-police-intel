# Issue #21 — detail recheck core

本地已加入 `intel_v2.detail_recheck` 的 transport-neutral B 核心，以及受限的 HTTPS transport adapter：

- `plan_recheck()` 以可設定 TTL 判斷是否到期，並在到期時保留 `If-None-Match` 與 `If-Modified-Since`。
- `classify_observation()` 保留 hash-only document version，區分 `UNCHANGED`、`PRESENTATION_ONLY`、`MATERIAL_CHANGE`、`ATTACHMENT_CHANGED`、`NOT_MODIFIED`、`DEFERRED` 與 `UNAVAILABLE`。
- 304 不宣稱附件未變更；暫時失敗保留 last-known-good，且不自動取消事件。
- 實質或附件變更標記 `review_required`，並保留 before/after 版本供既有 Review Inbox 與 handoff 層接續處理；附件只接受 approved host allowlist。
- `intel_v2.detail_recheck_http.recheck_detail()` 僅允許核准 hostname、HTTPS、443、手動受限 redirect、條件式 request header 與 2 MiB response；304、429/5xx、超大回應與未核准 redirect 都 fail closed。
- `migrations/0003_detail_recheck.sql` 與 DB collector 已接上 opt-in `detail_recheck_state`：只有 list-first collector 實際抓到的 detail 會註冊，排程每輪最多處理一筆到期 target，保存 snapshot blob、hash-only before/after classification、下次核對時間與 review flag；未註冊項目不會觸發全站重爬。

驗證：`tests/test_detail_recheck.py` 14 tests，以及 `tests/test_detail_recheck_database.py` 3 tests pass。

刻意保留的界線：尚未在 `TEST_DATABASE_URL` 或正式 DB 執行 migration/排程，也沒有 7-day canary；因此不能宣稱 production recheck、公開部署或真實來源收件已完成。
