# S019 ODT 正文會議次數與日期角色

2026-10-07 14:02 UTC，以正常 `BoundedSession` 分四次執行，對[既有取得收據](source-loop-s019-documents-20261007.json)的四個原始 ODT URL 各做一次 GET，全部 HTTP 200，內容 SHA-256 全部與先前相同。每次 `max_calls=1`、無重試、未跟重新導向；未重爬列表、詳頁，也未取得 PDF、影音或其他來源。原件只在記憶體處理，沒有寫入檔案或傳至模型。先前收據的 `body_saved=false` 保留；這不是解析先前已保存的原件。

新 parser 從 ODT 正文開頭的明確市政會議標題與「時間」欄位得到以下 metadata，沒有使用檔名、ODT 建立時間或列表日期推定會議日。

| 原官方列表日期 | 正文會議次數 | 明確時間欄位的會議日 |
|---|---:|---|
| 2026-09-23 | 733 | 2026-09-15 |
| 2026-09-16 | 732 | 2026-09-08 |
| 2026-09-10 | 731 | 2026-09-01 |
| 2026-09-04 | 730 | 2026-08-25 |

因此，這四個列表日期在 9/1–10/7 的文件只有三個正文會議日期在該期間。這不是全期間只有三場會議的證據，也不把列表日期改寫成會議日期。會議次數是正文序號，尚未核對官方 meeting registry identity；內容 hash 僅識別 byte version，「定稿」不產生官方版次或修訂歷史宣告。

[安全收據](source-s019-odt-meeting-metadata-20261007.json)保存原 URL、實際 UTC 時間、HTTP／byte／hash、與先前 hash 的關係及最小 normalized metadata；沒有正文、題名、段落索引或檔案內部內容。實際 parser commit 為 `4a7a968fd1d77b980ddcdf27087bf233d4501a86`，module SHA-256 為 `02db66a3e7b326ea8ac6b8c4ec5fd99c62b2af44f22c557fbf1bfb8d4a729c8e`。這些 hash 是本地一致性收據，不是官方簽章或獨立覆蓋驗證。

`intel_v2/s019_meeting_metadata.py` 只接受 ODT bytes。限額為原 ZIP 2 MiB、128 members、宣告總解壓 8 MiB、XML 4 MiB、壓縮比例 100、50,000 nodes、128 層及有界文字；不解出檔案。選用的 `mimetype`／`content.xml` 檢查 local header、compressed boundary、實際 bounded deflate output、EOF、無尾隨 stream、實際 size／CRC，不能只相信 ZIP 宣告大小。XML 僅 UTF-8，拒絕 DTD／entity declarations。只識別首 32 個 text-flow blocks、議程前的明確標題／日期欄位，不宣稱文件 rendering 已驗收。

缺少標籤、日期未定、修訂／參考日期、縮寫日期範圍、無效日曆或衝突皆保留 UNKNOWN；標題／日期來源字串不公開。初版 24 項虛構 ODT tests 包含七個日期誤判 red→green、偽造 ZIP size／CRC 的隱藏尾巴、有界解壓、UTF-16 entity、深 XML、隱藏變更及 raw projection 回歸；獨立 code-only review 的日期、截斷／尾隨 deflate probes 也通過。

後續僅以虛構資料修正明確民國紀年與西元年衝突、以及正文星期與日曆不符的兩個邊界，新增兩項回歸使 suite 為 26 項；明確民國搭配 `2026` 不再被當成西元 `2026`，星期衝突保留 UNKNOWN。修正後 module SHA-256 為 `9e86f878243ad8d03ba3c5de9b662f995d707bef1431e66ef5f678350df4ba07`。以上四份實際 ODT 收據仍綁先前 module `02db66a3...`，未重標、重新取件或用新 parser 解析原件；body 沒有保存，收據也沒有保存正文星期 token。四個已存 ISO 日期可離線算出都是星期二，但這不能證明原正文星期與日期一致，也不能將虛構回歸稱為四份原件的新驗證。

來源及指定期間的 business scope、完整附件、PDF／ODT 跨格式內容一致性、修訂歷史與獨立完整性仍未驗證；`rights_approved`／`promotion_eligible`／`model_transmission_allowed` false，真人 review null，窗口 `PARTIAL`、全歷史 `UNKNOWN`。本輪不增加候選資格日或來源准入，也不改寫 S032 歷史 receipt。
