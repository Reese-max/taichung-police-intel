# #20 發布修復：本輪邊界與下一步

基準：`e1d081bd04824c062c7ee99e7d74f9e478240743`。已知排程在資料 push 至受保護 main 被拒；不能為了展示關閉 PR／verify／分支保護。

## 2026-10-03 production recheck（修正已合併並跑過真實排程）

- 修正版 workflow 已在 `main@562141e396c693e115b3fce10594c434c401a58e`；`publication-state` checkpoint、跨 job `publication_outcome` finalizer、`if: always()` 證據保留皆在正式排程實際執行。
- 晨／晚排程成功憑據（皆在現行 main）：EVENING `36744214186`（09-30，`PUBLIC_DATA_VERIFIED`）、`36896609394`（10-01）；MORNING `36801047512`（10-01）、`36952506907`（10-02）、`37085607707`（10-03）。
- 真實失敗演練 `37033001280`（run 建立於 10-02T16:18Z，屬 `30 10 * * *` UTC cron 的 evening slot 延遲執行；實際蒐集於 10-03T00:18+08:00，故 run ID 為 `CR-DEMO-20261003-EVENING-SCHEDULE`）：build 成功、deploy 的公開位元組／hash 核對步驟失敗（`public_verify=failure`）；finalizer 以 `PUBLICATION_NOT_CONFIRMED` 回報實際失敗階段、保留成功上傳的證據 artifact 連結、明確警告不得當成零新事件或部署成功，run 結論為 failure。保留了 last-known-good：state 留在 `PENDING_PUBLICATION` checkpoint `e5114e63`。
- 重試安全：下一輪排程 `37085607707`（建立於 10-03T01:18Z，`30 22 * * *` UTC cron 的 morning slot）還原 `pending_recovery=true`，原樣重放 pending bundle（未重抓、未推進 diff，故供應中的 `collection_run_id` 維持原批 `CR-DEMO-20261003-EVENING-SCHEDULE`），部署後核對公開位元組並於 `2026-10-03T01:39:24Z` 署名 `PUBLISHED`。
- 匿名 HTTPS 核對（2026-10-03 執行）：`https://reese-max.github.io/taichung-police-intel/` 五個公開資料檔的 SHA-256 與已署名 checkpoint 的 `verified_files` 完全一致；feed 內 `collection_run_id=CR-DEMO-20261003-EVENING-SCHEDULE`、`generated_at=2026-10-03T00:18:54+08:00`，與重放批次相符。
- 尚未在自然排程觀測到 Pages **artifact-upload** 本身失敗的一輪；該路徑目前由決定性測試覆蓋（PR #100）。

## 2026-09-22 external-state recheck

- GitHub Pages is public and workflow-backed, but the latest scheduled run
  `35674266081` (2026-09-22 09:02 Asia/Taipei) still ran
  `main@e1d081bd04824c062c7ee99e7d74f9e478240743` and failed at the
  protected-main push with `GH006` (`Changes must be made through a pull
  request`, required `verify` expected); build verification had
  passed before that failure, so no new Pages deployment was produced.
- The public `/api/status.json` currently returns HTTP 200 but reports
  `generated_at=2026-09-11T08:23:26+08:00`, mode `COMPETITION_DEMO`, and the
  observed response UTF-8 SHA-256 is
  `741e569dee959c068f5833b2f210e0941a96a1c158bcb0ad100b39746b766d0b`.
  `/api/health.json` also returns HTTP 200 with `status=ok`, but the same old
  generated time and demo mode; these are reachability receipts, not current
  publication freshness.
- The effective `main` protection still requires one approving review,
  code-owner review, resolved conversations, and the `verify` check; force
  pushes and administrator bypass are disabled. The local
  `publication-state` candidate therefore remains unmerged and unverified in
  production.
- The remote `publication-state` branch currently still points to
  `e1d081bd04824c062c7ee99e7d74f9e478240743` and has no
  `state/publication-checkpoint.json`; the remote `pages.yml` blob is still
  `a5750eee7fdc87f56422c9dd484f54475eab5d4c`. The state branch's existing
  legacy files therefore do not prove that the candidate checkpoint workflow
  has run.

## 本地候選已改，仍須合併／正式執行驗證（2026-09 合併前記錄；已合併，見 2026-10-03 recheck）

1. `.github/workflows/pages.yml` 不再直接 push 受保護 `main`；V1/V2 durable state 改保存到專用 `publication-state` 分支，並以 restore baseline、pending checkpoint 與非快轉競態檢查保護未發布資料。
2. build 的主要步驟有 outcome 輸出，留證步驟涵蓋 state persist、Pages artifact 與下游 deploy 失敗；artifact URL 只在上傳成功且有實際回執時顯示。
3. deploy 後逐一核對五個公開資料檔的 HTTP status／bytes／SHA-256，只有 exact generation 通過才 ACK `PUBLISHED`；未通過不偽造發布憑據。
4. 最終 `publication_outcome` 等待 build/deploy，指出各階段 success/failure/skipped/unknown；本地 full gate 在隔離 PostgreSQL service 下為 `VERIFY_OK required=102`。
5. 前端以最舊的本輪核對時間與快照時間判斷逾期；16 小時是現行早晚更新的暫定 UI 年齡上限（12 小時間隔＋4 小時容忍），不是服務 SLA。背景統計的舊期別仍另外顯示，不混成抓取故障。

## 正式環境仍未完成（2026-09 合併前記錄，已被上方 2026-10-03 recheck 取代）

**（保留為歷史記錄：修正當時尚未合併到 `main`。）**遠端排程仍執行舊 workflow；人工取消、runner 損壞、連線中斷也可能讓 `always()` 無法取得產物，報告不得保證一定存在 artifact。正式關閉仍需有效 review／merge、晨晚自然 run、匿名公開 hash 驗證與 failure drill。

## 下一個必要外部決策（2026-09 合併前記錄；修正已合併，晨晚 run 與匿名 hash receipt 已取得）

核准並合併本地候選的受保護分支修正，沿用既有 review／verify 規則；不得 force push、添加廣泛 bypass 或另建任意自動合併權限。合併後再取得晨晚自然 run 與匿名公開版本／hash receipt。

確認事項：
- Actions 是否被允許建立 PR；未允許時回報設定阻塞，不默默增加權限。
- GITHUB_TOKEN 建立／更新 PR 的 CI 可能需人工 Approve workflows；不能假設已自動執行。
- 不使用 [skip ci] 或 path filter 讓 required verify 永久等待。
- 基線必須對應最後已確認發布版本。失敗重試、併發、另一個 code PR 推進不能吞掉未發布異動。
- 需要的 state/bundle/hash 原子關聯、恢復與跨晨晚驗收，沿用 #20，先用合成 fixture 測負向路徑。
- 不在尚未選定並驗證資料持久化方式前，先提高輪詢頻率。

若採資料分支或物件儲存而非 PR，另寫最小決策紀錄，明確界定資料寫入驗證、最後成功版本指標、deploy 失敗的恢復與禁止載入該分支程式碼。不能以「資料而已」為由繞過驗證。

## 官方介面參考（2026-09-16 閱讀）

- https://docs.github.com/en/actions/concepts/security/github_token
- https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax
- https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository

以上是平台行為說明，不是已確認本 repository 的管理設定。正式環境權限與最終行為仍須實際驗收。
