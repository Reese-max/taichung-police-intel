# 原件保護與 checkpoint 覆核後續修復

PR #173發布完成後，四條自動code review指出範圍parser的output可能覆原件、checkpoint綁定漏了列解析語意、未解出的下一頁可能成功封存、寬鬆控制項可能保存原文。這輪以可重現fictional fixtures補上拒絕／原件hash不變／metadata最小化與版本變更的回歸。

範圍CLI拒絕相同resolved input/output或相同inode，包括symlink／hardlink alias；輸出透過fresh0600 mkstemp、fsync與atomic replace完成，錯誤清除暫存，不穿過output symlink修改其他檔案。

S032的新checkpoint合約綁定列解析、metadata產生、导航、checkpoint及有界transport相關程式。舊V1只保留歷史觀察；新版需從原官方入口建立，不能把舊鏈改hash當成新版認證。未解出的分頁必须非成功停止。控制metadata只保存已驗證的必要角色與頁碼；原label、title、class和任意href不進checkpoint。

較早V1實際續跑鏈已於08:06:21–08:30:37 UTC走完82頁814 ID，窗口36列表項；不是單一原子snapshot，[安全收據](s032-v1-terminal-resume-observation-20261007.json)記錄原code SHA和取得時間，並仍為LOCAL_TRAVERSAL_RECEIPT_ONLY／PARTIAL／coverage independently false。這些歷史取得事實沒有被重發成V2資格。

Claude Code2.1.292已安裝，官方OAuth仍等待使用者一次性授權碼；actual model invocation NOT_RUN_AUTH_REQUIRED。來源詳頁／附件／業務完整性、原受阻路徑、權利與真人條件仍須實際證據；正式查詢保留RIGHTS_BLOCKED、provider DISABLED。

整合後 root 跑過互不重疊的 122 項重點測試（news list 90／scope 32）；分頁代理相容測試 128 項與另一代理獨立覆核 14 項也通過，兩者與 root 測試重疊，不累加成新總數。Root 另以有界 5 次正常官方請求取得新版入口前 4 頁／39 IDs，並單獨驗證第 82 頁 normalized controls 可辨識末頁；[新版相容收據](s032-v2-compatibility-observation-20261007.json)保留真實 code／module fingerprints／時間與 bytes hashes，沒有再取得整個 82 頁鏈，也沒有遷移 V1。完整 gate、必要 exact-tree CI 與後續正式發布須在這次文件提交之後另行跑過，不能把較早 PR #173 的驗收代用。
