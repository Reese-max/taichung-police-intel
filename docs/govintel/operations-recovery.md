# 發布告警與恢復驗收

關聯 #20、#35、#39、#62。發布、來源可讀性、正式准入與真人實測各自驗收。

## 現行流程

`pages.yml` 的 `publication_outcome` 在 build、Worker、Pages job 結束後，以實際 `needs`／step outcomes 產生 health receipt。告警交 `publication-alert.py`：固定目的地為本 repository 的 #20，不接收任意收件人、issue 或 URL。只有 reporting job 增加 `issues: write`；不新增 token、bypass、管理權限或外部通知服務。

實際 failure／cancelled 階段及 source-contract `BLOCKED` 產生故障告警。`RIGHTS_BLOCKED` 本身維持資料准入待辦，不冒充已發布版本故障。正常恢復必須具公開資料 hash 與 Query Gateway 部署驗證，仍不批准來源、全文、模型傳輸或真人成果。

每次 POST 後 GET 同一 comment，body 必須逐字一致才能記 `DELIVERED_READBACK_VERIFIED`。同 run／attempt 重試回讀原 comment，不重複發表；恢復也可冪等重試。API 權限不足、transport 失敗或回讀不符時留下 `ALERT_DELIVERY_FAILED` 且退出失敗，不隱瞞告警漏接。

公開 comment 只含 scope、階段、run、經核對的證據連結與版本，不轉發 health 原件中的私人 query、token、正文或上游日誌。health 與送達收據分別作為 14 天 artifact 留存。`human_acknowledged` 保持 null；API 送達不等於有人已讀或值班機制已驗證。

告警也列出格式有效的 `state_commit`（40 位十六進位）與 `generation_id`（64 位十六進位），供定位該批 checkpoint；它們不單獨證明發布成功。`publication-outcome.py` 將成功上傳的 evidence artifact URL 同時交給摘要與 health receipt，再由告警核對 repository、實際 run ID 與數字 artifact ID。帳密、port、query、fragment、其他 repository／run 或任意文字都不能成為公開下載連結；未成功上傳時明示連結未取得，仍可列出已知 checkpoint。舊 health 或隔離演練沒有這些欄位時維持相容，不補造版本。

本機回歸（#20，2026-10-16）：

```bash
python -X utf8 -m unittest discover -s tests -p 'test_publication_alert.py' -v && python -X utf8 -m unittest discover -s tests -p 'test_publication_outcome.py' -v
```

| #20 驗收 | 程式與本機證據 | 尚缺的正式證據 |
|---|---|---|
| state push 拒絕後保留證據 | 既有 state regression；新增 PRESERVE 告警的 artifact／版本定位 | 真實隔離 state server 拒絕與完整恢復 |
| upload／deploy 失敗及 LKG | 既有 checkpoint 保留；新增 PAGES_UPLOAD／PAGES_DEPLOY／PUBLIC_VERIFY 告警定位，上傳失敗不造假連結 | 真實 Pages 部署拒絕及 LKG／pending 恢復回讀 |
| 併發／重試無假 NEW／覆寫 | 既有 state regression；新增含證據告警的重送去重與精確 API 回讀測試 | 受控端到端恢復演練 |
| 保留 PR／verify 與保護 | 本次僅修改 reporting、測試與文件；未調整權限或 workflow | 未重新盤點遠端 protection／token |
| 自然 MORNING／EVENING | 本機測試不替代自然排程 | 日期化正式 run 證據由 driver 驗收 |
| 匿名 HTTPS／hash／版本 | 既有 ACK 路徑保留；本次 FakeGithub 測試不連網 | 未重新驗證正式站 |
| 完整檢查及失敗演練 | 本次依 TDD 先取得缺漏告警證據的失敗，再驗證修正 | 本機檢查不構成 issue 關閉或正式故障／恢復證據 |

本輪關聯測試由 37 項增至 40 項，40 PASS／0 FAIL、exit 0；`npm run check:gate0` 為 exit 0（`required=164 specs=4 secrets=0`）。完整 `npm run check` 為 exit 1，10 組檢查未通過；其中 web 為 797 PASS／3 FAIL，靜態站 build 成功。已直接載入 admitted base `c629f82a8b16825d5d89c204c7035dcba500a9ac` 重現 Windows 缺少 `os.O_NONBLOCK`，另觀察到既有 symlink 權限、PDF 子程序、目錄檔案操作及提交前 dirty-checkout 限制；未藉此擴張本單修改範圍，也未宣告完整 gate 或正式驗收通過。專案未配置獨立 lint／coverage ratchet／CRAP／mutation gate，本輪未新增工具。

## 隔離的實際上傳演練

手動執行 `publication-alert-drill.yml`：

1. 實際呼叫 `upload-pages-artifact`，指定不存在的隔離目錄，檢查真實 step outcome 為 failure。
2. 發布一條 `ISOLATED_ARTIFACT_UPLOAD_DRILL` 告警，並精確回讀。
3. 在 runner temp 建立獨立 HTML，再實際上傳不同名稱的 Pages artifact，檢查 step outcome 為 success。
4. 發布並回讀該 scope 的恢復紀錄，保留 health／delivery receipts。

該流程不呼叫 Pages deploy 或 Cloudflare、不讀正式 publication-state、不蒐集來源。正常 workflow 無 `continue-on-error`；演練只在預期失敗步驟使用它，並隨後強制核對實際失敗。上傳演練不證明真實 Pages／Worker 部署故障已發生或恢復，不代替正式匿名 readback。

執行前狀態為 NOT_RUN；完成後以實際 run、artifact digest、comment IDs 與 GET 原文 hash 留日期化收據，不預填成功。該 scope 和 `PRODUCTION_PUBLICATION` 的告警互不關閉。

## 現有受控狀態／HTTP 故障

`tests/test_publication_state_branch.py` 使用隔離的真實 bare Git remote、CLI 與 loopback HTTP；push 被拒案例以 `module.git` 例外注入，並非 remote hook 或 GitHub server 拒絕：

- 缺檔／hash／generation 不符及舊 bytes 的 HTTP 200 不 ACK。
- 模擬被拒 push 保留 LKG／生成證據；遠端推進拒絕舊基準覆蓋。
- Pending replay 不推進蒐集時鐘，不覆蓋未發布異動；成功回讀後才 ACK。

這些是實際執行的隔離工程路徑，非真人成效或正式 upstream 修訂。正式 deployment 拒絕演練、長期告警送達／真人確認、官方正文到期刪除仍按 #20／#35／#39 保持待驗；不刻意破壞正式站來填驗收。

## 來源審查 hash 一致性

Python retention compiler 將非空 `source_reviews` 納入 hash，空 map／不存在時保留歷史 hash。Worker 驗證同一材料；逐來源審查的歸屬等欄位變更也必須拒絕舊治理綁定。回歸使用明確標為 `FICTIONAL_OFFLINE_ONLY` 的隔離資料，不替真人填寫 review 或改 production policy。

## 資料更正入口與處置

正式公開查詢頁連到 repository 的「公開資料問題與更正」表單，僅固定 template URL；不夾帶查詢、追蹤、原始使用者問題或瀏覽器儲存。提交前由使用者檢查公開內容並自行送出，需 GitHub 登入。

待維護角色確認後，依原文／時間／版本回查，將錯誤分類到來源、日期、投影或權利；修復透過 PR 與驗證，附新版本與匿名回讀連結回覆原 issue。未知日期與來源消失不自行推定取消；涉及內容權利或個資時依 #39 處置。表單及處置流程已備妥，不宣稱實際維護工時或值班人力已承諾。

## 2026-10-06 真實驗收與 ACK 重跑

[隔離 drill](competition-2026/closure-20261006/alert-drill.json)確有實際 upload failure→success、#20 送達與 API hash 回讀；真人閱讀仍未知。[晚間正常 workflow](competition-2026/closure-20261006/evening-collection.json)已保存實際 Query Gateway 503 故障與告警，以及其後綁定一致的匿名回讀。這不是受控 Pages/Worker deploy 拒絕演練。

下游 query 失敗發生在資料已 ACK 後時，deploy job 重跑可以重新驗相同公開 bytes；只容許原 pending commit 的直接 ACK child，且只有 checkpoint manifest 改變。其他 generation、producer、metadata、state 路徑或並行推進仍拒絕；HTTP 舊 bytes／hash 不符也拒絕。verifier 三次完整檢查間隔 20 秒，各次失敗保留，不放寬任何 release/hash/admission 條件。
