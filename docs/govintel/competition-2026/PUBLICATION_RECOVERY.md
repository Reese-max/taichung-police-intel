# #20 發布修復：本輪邊界與下一步

基準：`e1d081bd04824c062c7ee99e7d74f9e478240743`。已知排程在資料 push 至受保護 main 被拒；不能為了展示關閉 PR／verify／分支保護。

## 本輪已改，仍須 CI／執行驗證

1. build 的主要步驟有 outcome 輸出，留證步驟放在 push 及 Pages artifact 上傳之後，採 always()。
2. 成功 build 也保存原有的發布檔案與 state，因此後續 deploy job 失敗時可查該批資料；留存 14 日。沒有扩大到整個工作目錄、原始來源資料或憑證。
3. 最終 `publication_outcome` 等待 build/deploy，指出各階段 success/failure/skipped/unknown。artifact URL 只在上傳成功且有實際回執時顯示。
4. deploy action 成功仍標 `DEPLOY_ACTION_SUCCEEDED_UNVERIFIED_HTTP`；尚未加入匿名 HTTP／版本／hash 自動驗收，不偽造發布憑據。
5. 前端以最舊的本輪核對時間與快照時間判斷逾期；16 小時是現行早晚更新的暫定 UI 年齡上限（12 小時間隔＋4 小時容忍），不是服務 SLA。背景統計的舊期別仍另外顯示，不混成抓取故障。

## 本輪沒有解決

**直接 push 仍保留，故 protected-branch 根因尚未修復，#20 必須保持 open。**留證不等於成功發布；人工取消、runner 損壞、連線中斷也可能讓 always() 無法取得產物，報告不得保證一定存在 artifact。

## 下一個最小決策

先核對實際生效 ruleset 與 token 身分。優先評估有界資料更新分支＋PR，保留單一 pending update，通過既有 verify 後由原有審核流程處理；不得 force push、添加廣泛 bypass 或另建任意自動合併權限。

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
