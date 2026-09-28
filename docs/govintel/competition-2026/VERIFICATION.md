# 本輪驗證紀錄

日期：2026-09-16。基準 main：`e1d081bd04824c062c7ee99e7d74f9e478240743`。

## 證據取得與變更邊界

透過已連接 GitHub 讀取固定版本 workflow、V2DailyDashboard 與既有測試。供本輪編輯的兩份原檔在本機重建後，Git blob SHA 分別等於讀取結果：

- `.github/workflows/pages.yml`：`a5750eee7fdc87f56422c9dd484f54475eab5d4c`
- `apps/web/components/V2DailyDashboard.js`：`8cb08ae7dc04309ac51c2fff50e5e2d60b2eaeca`
- 後續讀取的 `.github/workflows/ci.yml` 原件：`69e0105880de16632980478f9087973c3505fad3`，重建後 SHA 相符。

只改前端時效呈現、workflow 留證／結果報告及其 CI 驗證，新增對應測試及參賽交付文件。未修改 collector、資料 snapshot、state、ruleset、權限設定、模型供應商或 PR #16。

## 本機實際執行

環境：Node.js 22.16.0、Python 3.13.5。

| 檢查 | 結果 | 證據邊界 |
|---|---|---|
| `node --test apps/web/tests/publication-freshness.test.mjs apps/web/tests/publication-outcome.test.mjs` | 25 pass / 0 fail | 24 項時效／接線測試＋1 項 Python suite bridge；非全站測試 |
| `python -m unittest discover -s tests -p test_publication_outcome.py -v` | 9 pass / 0 fail | 已測推送、上傳、部署失敗、取消、未知結果與 artifact 連結語意；另直接執行 CLI 檢查摘要寫入、退出碼及 Shell 特殊字元不執行；也是上列 bridge 呼叫的同一套，不重複當額外產品驗收 |
| TypeScript `transpileModule` 解析 JSX | 0 syntax errors | 只做語法轉譯，不是 React／Next.js build 或瀏覽器測試 |
| YAML／JSON 解析 | 通過 | 只證明可解析，不代表 GitHub Actions 執行環境已驗證 |

首次時效測試在 component 尚未寫入時為 21 pass / 1 fail（檔案不存在），完成實際接線後全部通過。沒有刪除或跳過失敗測試。

## 首次 GitHub CI 與後續修正

PR #26 首次 head：`4b3854fef4810d9ebecc7a52f49a82e8dd36dfeb`。

- CI：https://github.com/Reese-max/taichung-police-intel/actions/runs/35055942092
- verify job：`104666042124`。
- 已實際通過 `npm run check`（`VERIFY_OK mode=full required=43 specs=4 secrets=0`），含 201 項 web tests 與 Next.js 靜態建置。
- V1 驗證、V2 語意回歸、V2 baseline 與 24 個異動／顯示 23 個的回歸均通過。
- **整個 CI 仍是 failure**：最後的 `Verify scheduled-failure summary quoting` 仍要求 `.github/workflows/pages.yml` 內包含舊 Shell `printf` 寫法。報告已移至 Python，所以該字串斷言不再對應實際實作。

本次 follow-up 保留安全驗證目的，將 `.github/workflows/ci.yml` 最後一項改為執行 9 項 publication outcome 測試；新增直接執行報告 CLI 的兩項測試，檢查摘要文字、失敗退出碼，以及反引號／命令替換字元只當資料而不執行。沒有略過測試、增加 `continue-on-error` 或弱化發布閘門。

follow-up 的本機 25 項 Node 測試及 9 項 Python 測試已通過；CI YAML 裡的新摘要驗證步驟也已在本機直接執行成功。這些是同一組測試的不同入口，不重複算真人／部署成果。後續 head 的完整 GitHub CI 結果以 PR Checks 為準，不能沿用首次 head 的通過步驟宣稱新 head 全部通過。

## 尚未驗證

- 本機 GitHub clone 因無法解析 github.com 失敗，raw URL 也未能取得；因此沒有完整 repository checkout／套件依賴，不宣稱在本機執行過 `npm run check` 或全站 build；上述完整建置證據來自 GitHub CI。
- 沒有執行真人評測、模型／Twinkle 呼叫、七日 canary 或新的正式部署。
- 後續 head 的完整 CI、瀏覽器與發布驗收須各自核對。尚未完成的 runtime 範圍保持 `NEEDS_RUNTIME_VERIFICATION`。
- 此輪不修正 protected-main direct push；#20 保持 open。
- 沒有匿名 HTTP／版本／hash 驗收，不能以 deploy action 結果或本地測試代替。

## 合併前與合併後必補

1. 既有 `verify`／`npm run check` 對本 PR 實際 head 通過。
2. 以固定時間、來源缺失、混合 run IDs、停留頁面超時等案例執行真正瀏覽器測試。
3. 在隔離 workflow 演練 preserve、artifact、deploy 失敗，核對 finalizer 與 artifact 實際連結；不刻意破壞 production。
4. #20 真正修復後取得晨／晚發布與匿名版本核對證據；此輪不得提前關閉。
