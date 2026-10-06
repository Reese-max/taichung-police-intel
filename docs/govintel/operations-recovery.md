# 發布告警與恢復驗收

關聯 #20、#35、#39、#62。發布、來源可讀性、正式准入與真人實測各自驗收。

## 現行流程

`pages.yml` 的 `publication_outcome` 在 build、Worker、Pages job 結束後，以實際 `needs`／step outcomes 產生 health receipt。告警交 `publication-alert.py`：固定目的地為本 repository 的 #20，不接收任意收件人、issue 或 URL。只有 reporting job 增加 `issues: write`；不新增 token、bypass、管理權限或外部通知服務。

實際 failure／cancelled 階段及 source-contract `BLOCKED` 產生故障告警。`RIGHTS_BLOCKED` 本身維持資料准入待辦，不冒充已發布版本故障。正常恢復必須具公開資料 hash 與 Query Gateway 部署驗證，仍不批准來源、全文、模型傳輸或真人成果。

每次 POST 後 GET 同一 comment，body 必須逐字一致才能記 `DELIVERED_READBACK_VERIFIED`。同 run／attempt 重試回讀原 comment，不重複發表；恢復也可冪等重試。API 權限不足、transport 失敗或回讀不符時留下 `ALERT_DELIVERY_FAILED` 且退出失敗，不隱瞞告警漏接。

公開 comment 只含 scope、階段與 run 連結，不轉發 health 原件中的私人 query、token、正文或上游日誌。health 與送達收據分別作為 14 天 artifact 留存。`human_acknowledged` 保持 null；API 送達不等於有人已讀或值班機制已驗證。

## 隔離的實際上傳演練

手動執行 `publication-alert-drill.yml`：

1. 實際呼叫 `upload-pages-artifact`，指定不存在的隔離目錄，檢查真實 step outcome 為 failure。
2. 發布一條 `ISOLATED_ARTIFACT_UPLOAD_DRILL` 告警，並精確回讀。
3. 在 runner temp 建立獨立 HTML，再實際上傳不同名稱的 Pages artifact，檢查 step outcome 為 success。
4. 發布並回讀該 scope 的恢復紀錄，保留 health／delivery receipts。

該流程不呼叫 Pages deploy 或 Cloudflare、不讀正式 publication-state、不蒐集來源。正常 workflow 無 `continue-on-error`；演練只在預期失敗步驟使用它，並隨後強制核對實際失敗。上傳演練不證明真實 Pages／Worker 部署故障已發生或恢復，不代替正式匿名 readback。

執行前狀態為 NOT_RUN；完成後以實際 run、artifact digest、comment IDs 與 GET 原文 hash 留日期化收據，不預填成功。該 scope 和 `PRODUCTION_PUBLICATION` 的告警互不關閉。

## 現有受控狀態／HTTP 故障

`tests/test_publication_state_branch.py` 使用隔離的真實 bare Git remote、CLI、push rejection hook 與 loopback HTTP：

- 缺檔／hash／generation 不符及舊 bytes 的 HTTP 200 不 ACK。
- 被拒 push 保留 LKG／生成證據；遠端推進拒絕舊基準覆蓋。
- Pending replay 不推進蒐集時鐘，不覆蓋未發布異動；成功回讀後才 ACK。

這些是實際執行的隔離工程路徑，非真人成效或正式 upstream 修訂。正式 deployment 拒絕演練、長期告警送達／真人確認、官方正文到期刪除仍按 #20／#35／#39 保持待驗；不刻意破壞正式站來填驗收。

## 來源審查 hash 一致性

Python retention compiler 將非空 `source_reviews` 納入 hash，空 map／不存在時保留歷史 hash。Worker 驗證同一材料；逐來源審查的歸屬等欄位變更也必須拒絕舊治理綁定。回歸使用明確標為 `FICTIONAL_OFFLINE_ONLY` 的隔離資料，不替真人填寫 review 或改 production policy。

## 資料更正入口與處置

正式公開查詢頁連到 repository 的「公開資料問題與更正」表單，僅固定 template URL；不夾帶查詢、追蹤、原始使用者問題或瀏覽器儲存。提交前由使用者檢查公開內容並自行送出，需 GitHub 登入。

待維護角色確認後，依原文／時間／版本回查，將錯誤分類到來源、日期、投影或權利；修復透過 PR 與驗證，附新版本與匿名回讀連結回覆原 issue。未知日期與來源消失不自行推定取消；涉及內容權利或個資時依 #39 處置。表單及處置流程已備妥，不宣稱實際維護工時或值班人力已承諾。
