# #20 發布修復：本輪邊界與下一步

基準：`e1d081bd04824c062c7ee99e7d74f9e478240743`。已知排程在資料 push 至受保護 main 被拒；不能為了展示關閉 PR／verify／分支保護。

## 2026-09-22 external-state recheck

- GitHub Pages is public and workflow-backed, but the latest scheduled run
  `35626064887` still ran `main@e1d081bd04824c062c7ee99e7d74f9e478240743`
  and failed at the protected-main push with `GH006`; build verification had
  passed before that failure, so no new Pages deployment was produced.
- The public `/api/status.json` currently returns HTTP 200 but reports
  `generated_at=2026-09-11T08:23:26+08:00`; this is reachability, not current
  publication freshness. The public `/api/health.json` likewise reports the
  old `COMPETITION_DEMO` snapshot.
- The effective `main` protection still requires one approving review,
  code-owner review, resolved conversations, and the `verify` check; force
  pushes and administrator bypass are disabled. The local
  `publication-state` candidate therefore remains unmerged and unverified in
  production.

## 本地候選已改，仍須合併／正式執行驗證

1. `.github/workflows/pages.yml` 不再直接 push 受保護 `main`；V1/V2 durable state 改保存到專用 `publication-state` 分支，並以 restore baseline、pending checkpoint 與非快轉競態檢查保護未發布資料。
2. build 的主要步驟有 outcome 輸出，留證步驟涵蓋 state persist、Pages artifact 與下游 deploy 失敗；artifact URL 只在上傳成功且有實際回執時顯示。
3. deploy 後逐一核對五個公開資料檔的 HTTP status／bytes／SHA-256，只有 exact generation 通過才 ACK `PUBLISHED`；未通過不偽造發布憑據。
4. 最終 `publication_outcome` 等待 build/deploy，指出各階段 success/failure/skipped/unknown；本地 full gate 在隔離 PostgreSQL service 下為 `VERIFY_OK required=102`。
5. 前端以最舊的本輪核對時間與快照時間判斷逾期；16 小時是現行早晚更新的暫定 UI 年齡上限（12 小時間隔＋4 小時容忍），不是服務 SLA。背景統計的舊期別仍另外顯示，不混成抓取故障。

## 正式環境仍未完成

**本地 protected-main 根因修正已完成，但尚未合併到 `main`，所以 #20 仍不能關閉。**遠端排程仍執行舊 workflow；人工取消、runner 損壞、連線中斷也可能讓 `always()` 無法取得產物，報告不得保證一定存在 artifact。正式關閉仍需有效 review／merge、晨晚自然 run、匿名公開 hash 驗證與 failure drill。

## 下一個必要外部決策

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
