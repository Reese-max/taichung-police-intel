# 啟用 `main` 正式分支 Ruleset

程式端已具備完整 `verify` CI、CODEOWNERS、PR template 與部署前 publication 驗證。此文件完成最後一個 GitHub 設定層步驟。

## 為何仍需要 Ruleset

沒有 Ruleset 時，擁有 push 權限的人員或 Agent 仍可繞過 Pull Request，直接把未測試程式推到 `main`。Ruleset 啟用後：

- 一般程式修改必須透過 Pull Request。
- Pull Request 必須通過 GitHub Actions 的 `verify` job。
- 分支必須與最新 `main` 同步後才能合併。
- 禁止刪除 `main`。
- 禁止 force push／non-fast-forward push。
- Review conversation 必須先處理完畢。
- GitHub Actions 不需要、也不應取得 bypass：06:30／18:30 workflow 將 durable publication checkpoint 寫入專用 `publication-state` 分支，再以同一份已驗證 artifact 部署 Pages，不直接寫入受保護的 `main`。

## 匯入步驟

1. 下載 `.github/rulesets/main-production.json`。
2. 開啟此 repository 的 **Settings**。
3. 左側選擇 **Rules → Rulesets**。
4. 點選 **New ruleset → Import a ruleset**。
5. 選擇 `main-production.json`。
6. 確認名稱為 `Protect main production`，Enforcement 為 `Active`。
7. 確認 Target 為 default branch。
8. 確認 required status check 為 `verify`。
9. 點選 **Create**。

## 為何批准人數目前是 0

這是個人 repository，目前主要維護者與 CODEOWNER 是同一帳號。GitHub 不允許作者批准自己的 Pull Request；若強制一名批准者，將造成所有 PR 無法合併。

Ruleset 仍然要求：

- 必須有 Pull Request。
- `verify` 必須成功。
- 分支必須是最新狀態。
- Review conversation 必須全部解決。

未來加入另一名可信任協作者後，建議把：

```json
"required_approving_review_count": 0
```

調整為：

```json
"required_approving_review_count": 1
```

並視需要啟用 `require_code_owner_review`。

## 啟用後驗收

使用 GitHub API 或 repository Branches 頁面確認：

- `main` 顯示為受保護／有 active ruleset。
- 未通過 `verify` 的 PR 無法合併。
- 直接由一般帳號 push `main` 會被拒絕。
- 06:30／18:30 workflow 能在不寫入 `main` 的情況下更新 `publication-state` 並完成 Pages artifact/deploy；候選 workflow 合併前，遠端舊 workflow 的失敗不算通過。
- force push 與 branch deletion 被拒絕。

## 緊急回復

如果匯入後資料排程失敗：

1. 先查看 workflow 是否已使用 `.github/workflows/pages.yml` 的 `publication-state` checkpoint 路徑。
2. 讀取 build、persist、Pages deploy 與匿名公開核對的實際 receipt；不要以 bypass 或關閉 ruleset 掩蓋失敗。
3. 確認 `publication-state` 分支的受控寫入與 Pages artifact 權限已具備。
4. 不要移除 required `verify` check，也不要讓資料 workflow 直接 push `main`。
