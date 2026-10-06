# 2026-10-06 修復、發布與驗收證據

本包保存固定版本的實際觀察。文件之後的合併、排程或蒐集不會改寫這些 receipt；最新正式版本另以公開 `data/release.json` 與 `publication-state` checkpoint 核對。

## 已完成

- [PR #167](https://github.com/Reese-max/taichung-police-intel/pull/167)：D1 固定樣本與 D2 取得限制的驗收記錄。
- [PR #168](https://github.com/Reese-max/taichung-police-intel/pull/168)：補件收妥、更名未接受及資格尚待核對，並修正相互矛盾的舊文字。
- [PR #169](https://github.com/Reese-max/taichung-police-intel/pull/169)：Worker 對非空 `source_reviews` 的 retention hash 與 Python compiler 一致；真實通知送達／回讀、隔離 upload 失敗／恢復流程、資料問題回報入口，以及 source review 證據引用修復。沒有核准任何來源或模擬真人簽核。
- [發布 run 37472606240](https://github.com/Reese-max/taichung-police-intel/actions/runs/37472606240)：固定 `5e2298c8db12ec2631dbd8dac11c14599133dcc6` 的 Pages／Worker 成功發布。
- [production-bytes.json](production-bytes.json)：匿名讀取五個公開檔案，逐一與固定 checkpoint bytes/hash 比對；保留官方修訂日、首次發布日未知、陳舊記錄與 LKG 的界線。
- [production-query.json](production-query.json)：真實 Gateway／Pages release、hash 與受限查詢一致；`deployment_verified=true`，正式准入仍 `RIGHTS_BLOCKED`、`production_verified=false`。
- [production-browser.json](production-browser.json)：15 項實際 Chromium 正式站觀察全部通過，含 D1 歷史背景、回報入口、來源日期與缺口、同瀏覽器保存／修改／刷新／取消／清除，以及研究流程在傳送模型前拒答。沒有真人或語意 AI 成效測量。
- [alert-drill.json](alert-drill.json)：實際 artifact 打包失敗後重新 upload 成功；失敗及恢復通知都送達既有 #20，並由獨立 API GET 比對通知 body hash。這裡的「獨立」指不同的回讀請求，不是另一位審查人。
- [ci-tree-binding.json](ci-tree-binding.json)：PR #169 的必要 CI／Chromium 與已合併樹相同；loopback runtime、合成 UI 與 provider mocks 各自留界線。

## 仍需證據

隔離 drill 沒有部署失敗的正式 Pages／Cloudflare 產物，也沒有恢復 S-029；通知不等於真人已閱讀。實際 raw purge、負載與尖峰量、發票／核准預算、正式正值查詢／模型／真人對照仍未完成。

[依賴授權清單](dependency-license-inventory.json)列出 npm lockfile 與 Python 直接依賴／binary extra 的宣告授權、已安裝版本及可讀 licence 檔 hash，不是作品所有權或法務核准。Python 全部間接依賴與實際對外交付的 notices 尚未逐項驗收；repo 尚無作品 LICENSE，須由權利人決定，不能自行授權。

[新舊檔案差異](old-new-file-delta.json)固定 2026-09-30 的 `562141e…` 與 `5e2298c…`。檔案數與行數不能當官方創新百分比；曾否獲獎、真實權利人及必要同意均待確認。

來源與真人 gate 見 [CURRENT_STATUS](../CURRENT_STATUS.md)、[ACCEPTANCE_CHECKLIST](../ACCEPTANCE_CHECKLIST.md)與 [#62](https://github.com/Reese-max/taichung-police-intel/issues/62)。原始送件 v6 保留，不重交、不冒稱主辦核准。

## 晚間實際蒐集與後續故障

[run 37474304213](https://github.com/Reese-max/taichung-police-intel/actions/runs/37474304213)是 10/6 已到期的晚間手動蒐集，窗口 18:30，實際完成 21:54:19 Asia/Taipei，沒有未來日期。Pages／Worker 部署及五檔 hash 已通過，但初次 Gateway verifier 的三次短重試回 HTTP 503，整個 run 保留 **failure**；#20 的真實告警送達／hash 回讀已核對。稍後匿名完整 verifier 通過，另存 receipt，沒有改寫初次失敗。

[晚間證據](evening-collection.json)、[五檔 bytes](evening-production-bytes.json)及 [Gateway 回讀](evening-production-query.json)：S-029 原允許 WWW 路徑本次連線成功，`source_health=PASS`、窗口 `COMPLETE_ZERO`，官方資料日期仍 2026-07-24／STALE；不是新的零事件世界結論。S-019 的 contract probe HTTP 200／NO_DRIFT 不代替七日 canary。S-001 仍 unavailable，整體 schema drift BLOCKED。

為處理已出現的恢復問題，新增 ACK 重跑只接受同 pending commit 的直接、只修改 manifest 的已發布 child；需重新驗五檔 HTTP，不接受新 producer、generation、metadata 或其他檔案變更。verifier 維持三次完整 hash／release／權利檢查，延長間隔為 20 秒，使觀察窗越過 Worker 的 30 秒 artifact cache；各次失敗也保留。這是工程修復，後續 PR／CI／發布仍需自己的 exact SHA receipt。

[晚間 canary](current-candidate-observation.json)實際取得 S-019 的第一個有效觀察日；消防同日重跑仍只有 1 日。這不是把早上失敗刪掉，也沒有製造未來六日。只公開 counts、結果與 artifact／report hashes，不複製原始 incident 明細。
