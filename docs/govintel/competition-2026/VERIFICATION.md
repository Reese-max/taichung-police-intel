# GovIntel AI－驗證紀錄與未完成閘門

## 2026-10-05 本地整合與來源查核

部署基準仍為 `main@562141e`；本輪正式執行完整 gate／同 checkout runtime 的已提交、乾淨程式版本為 **`3a06754a2428023bf316d8cb180f239f9c3b971d`**。2026-10-05 01:47–01:48 Asia/Taipei 執行 loopback runtime；文件後續修訂不改寫這份 tested SHA。正式部署與其後匿名 readback 尚未完成，不能把 `BUILD_ONLY` release 當 production receipt。

| 範圍 | 目前可留存的結論 | 不能由此宣稱 |
|---|---|---|
| 本地 query／tracking／sources | 已整合 candidate，入口為 `/public-query/`、`/tracking/`、`/sources/`；release mismatch、未知與來源失敗各自處理 | 已正式部署、跨裝置／關站推播、真實資料完整涵蓋 |
| D1 固定期別官方取得 | [原始取得與驗證樣本](segis-112Y12M-taichung.verified.json)：112Y12M／2023-12，全國 368 記錄、臺中 29 區；本地 query 頁可選區查看人口／戶數與收據 | 目前人口、人潮、受影響人數或已實測任務價值 |
| D2／Twinkle | [D2 metadata 200／官方 resource 403](background-source-observations-2026-10-05.json)，資料列／CRS 未驗證；Twinkle 既有連線需重新認證 | 已取得可用名錄、已驗證座標或同源取得路徑增加獨立證據 |
| Source reliability | [日期化稽核與 focused checks](SOURCE_RELIABILITY_REVIEW_2026-10-05.md)；本地修正 proxy、request budget、無日期列表與 S-019 漏驗 | 外部來源已恢復、七個合格觀察日、已核准 promotion |
| PostgreSQL migration | 已在 PostgreSQL 16 實際執行 `test_migration_applies_to_postgresql`，1/1 PASS；最終完整 gate 另提供 `TEST_DATABASE_URL` | 不能把資料庫 schema 通過當來源／UI／部署或真人驗收；既有無 DB 的 skipped 紀錄仍保留日期 |
| 合成政策 replay | [v6 工程協定](EVALUATION.md)、[資料 manifest](../../../eval/gold/v6/commute-reopen-v1/manifest.json)；政策代理與完整方法執行分開 | 已跑 B 搜尋摘要、C 語意 AI、真人 A/B/C、獨立保留集品質 |
| 實際 Chromium UI 觀察 | [2026-10-05 01:44 有限 UI receipt](browser-ui-observation-20261005.json)22/22 PASS：保存／重開／UPD-005 延長／已讀／UPD-006 明文解除／取消、錯誤／範圍／手機與 D1 正值／wrong-period／hash mutation | served release binding 為 `NOT_RUN`（該 loopback 的 release.json 404）；driver SHA 不是 server SHA，不能當最終同 checkout 或正式部署成功 |
| 最終同 checkout runtime | [本輪 compact receipt](current-checkout-verification-20261005.json)：26/26 PASS、無未執行 runtime checks、code SHA／lockfile／publication generation 綁定；HTTP／STDIO、mixed-generation、失敗退回與 sabotage 均核對 | loopback 與 preserved snapshot 不能代表正式公開部署、即時來源或領域完整涵蓋 |
| 嚴格同 checkout 瀏覽器 | [本輪 browser receipt](browser-verification-20261005.json)：既有 journey 13/13、v6 22/22；served code SHA=`3a06754…`、`BUILD_ONLY` release ID=`b9ea34d04868e4aabe7b965550705d8d6e39fa4e83db51c2a8b4113067a97ad3`、feed/status/brief 三檔 hash 綁定 PASS | 有限 UI 情境不是模型／真人／保留集效益；不能沿用較早未綁版本 observation 的 driver SHA |
| 完整工程 gate | 同一 `3a06754…` 執行 `npm run check`：`VERIFY_OK mode=full required=126 specs=4 secrets=0`；Web 406/406、0 fail，PostgreSQL 16 actual migration 有執行 | Web bridge 與其 Python suites 不重複加成獨立產品／真人案例數；成功 gate 不等於資料新鮮 |

道路合成序列固定為 10 月 5–7 日、每日 **09:00–17:00**；延長只把結束日從 **10/7 改為 10/9**；v3 明文解除時間為 **10/9 17:00**。事件期程、每日時段、觀測／發布時點各自保存，不能推算道路現場安全，也不能從到期／消失推論解除。

瀏覽器操作可核對原文連結與官方影音導航，但「點得開連結／導覽正確」不等於來源回應 HTTP 200、內容新鮮或答案準確；匿名 HTTP／bytes、資料涵蓋與人工答案判定另驗。

尚待完成：受保護流程 review／merge 與真實部署 readback、七日來源資格／promotion、release supersession audit、D2 取得／CRS、獨立事件保留集、實際語意 AI 與經同意真人 A/B/C。v6 的 85%／90%／30% 仍為目標；結果空白不填成功。主辦受理／更名仍待回覆。[驗收清單](ACCEPTANCE_CHECKLIST.md)逐項保存證據。

重播本輪工程驗證：

```bash
# 完整 gate 的資料庫 integration lane 使用可用的 TEST_DATABASE_URL；不公開其值。
npm run check
python3 -X utf8 scripts/verify-current-checkout.py --mode full --output runtime-evidence/current-checkout/replay
```

命令在新的 checkout 會產生該版本自己的 receipt，不會自動變成上列固定 `3a06754…` 的證據。歷史保存快照與合成道路都保留日期／分類，latency、真人與模型指標仍 null／NOT_RUN。

## 2026-09-16 歷史驗證紀錄

日期：2026-09-16。基準 main：`e1d081bd04824c062c7ee99e7d74f9e478240743`。

以下保留當日 PR／candidate 的驗證邊界，不是目前 main、目前公開資料或本次 judge path 的最新 receipt。請以本頁上方與 [CURRENT_STATUS.md](./CURRENT_STATUS.md) 的日期化證據為準。

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
