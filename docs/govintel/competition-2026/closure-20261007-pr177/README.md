# 2026-10-07 PR #177 固定正式站驗收紀錄

固定程式碼 `b6a602580fddf8b4a634dbdf64fc255678495412`；[發布 run 37615111747](https://github.com/Reese-max/taichung-police-intel/actions/runs/37615111747)。這是 PR #177 的當次證據，後續合併／蒐集另留收據，不以新 SHA 或 generation 重標本包。

| 證據 | 已核對範圍 |
|---|---|
| [驗收總收據](publication-acceptance.json) | main CI、發布成功、精確 code／tree／release／generation；只驗技術部署與治理拒絕。 |
| [必要 CI 樹綁定](ci-tree-binding.json) | 三個 CI job 通過；checkout tree 與合併樹相同。 |
| [公開五檔](production-bytes.json) | 匿名實際 bytes/hash、PUBLISHED checkpoint、日期角色與 generation 一致。 |
| [Gateway](production-query.json) | 部署綁定通過；正式查詢 RIGHTS_BLOCKED、production_verified=false。 |
| [正式 Chromium](production-browser.json) | 15 項實際技術／受限操作；D1、來源日期、追蹤與研究拒絕，非真人任務或 AI 效益。 |
| [部署時間來源](deployment-context.json) | 11:40:32 UTC 是成功的 GitHub Actions deploy job 完成時間；未取得 Pages deployment status 的精確時間。 |
| [Claude 登入](claude-cli-status-safe.json)、[實際程式覆核](claude-review-receipt.json) | 官方登入、公開程式碼覆核完成；供應商報告 claude-opus-5-5。修復由 Codex 代理實作，沒有官方原件／登入材料傳送。 |

本次 CODE_ONLY 發布保留 `CR-DEMO-20261007-MORNING-MANUAL`；不宣稱重新蒐集、增加自然日、來源准入或權利核准。正式產品語意 AI／真人試用仍 NOT_RUN。原始 10/6 紀錄保留在 [日期化歷史包](../closure-20261006/README.md)。
