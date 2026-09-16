# 本輪驗證紀錄

日期：2026-09-16。基準 main：`e1d081bd04824c062c7ee99e7d74f9e478240743`。

## 證據取得與變更邊界

透過已連接 GitHub 讀取固定版本 workflow、V2DailyDashboard 與既有測試。供本輪編輯的兩份原檔在本機重建後，Git blob SHA 分別等於讀取結果：

- `.github/workflows/pages.yml`：`a5750eee7fdc87f56422c9dd484f54475eab5d4c`
- `apps/web/components/V2DailyDashboard.js`：`8cb08ae7dc04309ac51c2fff50e5e2d60b2eaeca`

只改前端時效呈現、workflow 留證／結果報告，新增對應測試及參賽交付文件。未修改 collector、資料 snapshot、state、ruleset、權限設定、模型供應商或 PR #16。

## 實際執行

環境：Node.js 22.16.0、Python 3.13.5。

| 檢查 | 結果 | 證據邊界 |
|---|---|---|
| `node --test apps/web/tests/publication-freshness.test.mjs apps/web/tests/publication-outcome.test.mjs` | 25 pass / 0 fail | 24 項時效／接線測試＋1 項 Python suite bridge；非全站測試 |
| `python -m unittest discover -s tests -p test_publication_outcome.py -v` | 7 pass / 0 fail | 已測推送、上傳、部署失敗、取消、未知結果與 artifact 連結語意；也是上列 bridge 呼叫的同一套，不重複當額外產品驗收 |
| TypeScript `transpileModule` 解析 JSX | 0 syntax errors | 只做語法轉譯，不是 React／Next.js build 或瀏覽器測試 |
| YAML／JSON 解析 | 通過 | 只證明可解析，不代表 GitHub Actions 執行環境已驗證 |

首次時效測試在 component 尚未寫入時為 21 pass / 1 fail（檔案不存在），完成實際接線後全部通過。沒有刪除或跳過失敗測試。

## 尚未驗證

- 本機 GitHub clone 因無法解析 github.com 失敗，raw URL 也未能取得；因此沒有完整 repository checkout／套件依賴，不宣稱執行過 `npm run check` 或全站 build。
- 沒有執行真人評測、模型／Twinkle 呼叫、七日 canary 或新的正式部署。
- PR 的既有 CI 須另看實際執行結果。未取得結果前保持 `NEEDS_RUNTIME_VERIFICATION`。
- 此輪不修正 protected-main direct push；#20 保持 open。
- 沒有匿名 HTTP／版本／hash 驗收，不能以 deploy action 結果或本地測試代替。

## 合併前與合併後必補

1. 既有 `verify`／`npm run check` 對本 PR 實際 head 通過。
2. 以固定時間、來源缺失、混合 run IDs、停留頁面超時等案例執行真正瀏覽器測試。
3. 在隔離 workflow 演練 preserve、artifact、deploy 失敗，核對 finalizer 與 artifact 實際連結；不刻意破壞 production。
4. #20 真正修復後取得晨／晚發布與匿名版本核對證據；此輪不得提前關閉。
