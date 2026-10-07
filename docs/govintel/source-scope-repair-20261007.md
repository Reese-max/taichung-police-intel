# 2026-10-07：草案空態、日期角色與影音索引驗收

本輪在 `4472853e1cdce1d539026d0b6161621446d3fc9b` 基礎上處理可驗證的範圍缺口。原始 HTML／PDF 留在私人 scratch；公開 [收據](source-scope-repair-20261007.json) 只保存 URL、時間、hash、結構識別碼、日期角色及計數。沒有向 Claude、agy、Grok 或其他模型傳送原始官方內容，沒有修改正式 rights/admission。

| 項目 | 本轮實際結果 | 邊界與下一步 |
|---|---|---|
| S001 | 07:57:57 UTC 單次原址正常重試仍為503、91bytes，hash與較早收據相同 | 不推定原因；沒有換 host、代理或鏡像。待原官方正常恢復，再按正常低頻delta流程驗收 |
| S026 current | 08:00:03 UTC原址200，`#new > #ctl00_cp_content_divNoData` 為既有明確空態；只有current activepane及current導覽，無列表／詳頁衝突、維護或登入狀態 | `current_snapshot_count=0` 僅表示當次官方宣告無預告中草案。visibility是靜態HTML規則，並非瀏覽器rendering驗收；全歷史、期間、世界筆數仍未驗 |
| S026 112569 | 公告日2026-08-25；結構標籤「預告日期」區間2026-08-26至2026-09-01。詳頁兩個可見ANN附件137020／137021原PDF bytes及hash均已核對 | 137020本輪補取96,792bytes；137021保留07:22原觀察322,597bytes。只涵蓋此一詳頁的兩個可見附件，未驗官方版本歷史或所有785草案內容 |
| S010 recent | 官方`url=91` recent索引3頁，分別6／6／1項，共13個不重複item ID，第三頁官方末頁控制為3；14872詳頁標示會議日2026-10-01、一個播放器 | 第一頁沿用07:24觀察，2／3頁本輪新取，是非原子枚舉。此結果只涵蓋recent section；全部archive、指定期間全場次、所有詳頁日期角色仍未驗 |
| S011 | 594詳頁明確會議日2026-10-06、兩個播放器；另取得官方`url=11` archive第一頁，末頁宣告19 | 不把recent六項和archive第一頁十項當成相同篩選範圍。19頁未全取、發布日／播放／時間軸／字幕待驗 |
| S037／S034 | 本輪沒有重試受拒原件或OAS路徑 | 保留D2 10/5原件403和CRS UNKNOWN；S034保留10/7明確proxy tunnel403，不使用替代host／鏡像／代理／機器繞行 |

112569的預告區間在9/1與pilot 9/1–10/7重疊。因此先前完整歴史列表中的「期間內公告日期0項」，不能被解釋為「期間沒有任何徵詢／預告中的草案」。截止法律語意、版本語意及所有草案預告區間都仍待核對。

`scripts/source-scope-contracts.py` 是可重跑的離線metadata parser，不做網路下載。current契約要求原預設URL、current表單、唯一且可見的activepane、可辨認空態，並排除history／filtered selector、頁外可見草案、server／維護／登入與衝突列表。日期從可見結構標籤與有效日曆解析，不從檔名、fetch時間或播放器handle猜測。播放器ID只公開hash，不輸出任意path字串；播放器從未請求。CLI用 `O_NONBLOCK`、`fstat` 和2MiB限額拒绝FIFO、設備、目錄及過大檔案。

```bash
python -m unittest tests.test_source_scope_contracts -q
python scripts/source-scope-contracts.py --source-id S-026 --kind current \
  --url https://law.taichung.gov.tw/DraftForum.aspx --input /private/current-snapshot.html
```

27項回歸覆蓋缺表、隱藏或多個activepane、history／private selector、衝突可見草案、登入／維護、無效日期、隱藏日期cell及內層span、跨草案或跨origin附件、未知／重複query、任意播放器字串與有界檔案讀取。實際私人原件也通過同一parser；HTTP狀態與URL來自獨立hash-bound收據，不由HTML parser自行證明。

[權利review packet](source-rights-review-packet-20261007.json) 已列出15項取得目標和5項目前active query來源，共20項；所有真人reviewer、審查時間、decision及逐用途批准均保留null。它沒有被接入正式批准管線。仍需真實審查者提供適用條款及版本/hash、來源欄位／origin、metadata／summary／全文或節錄／provider傳輸的分別決策，以及保存期限、敏感欄位與撤回／purge證據。D1已有固定112Y12M授權聲明與來源證據，仍未代填正式審查決策。真人效益試驗另需真實同意參與者、兩位獨立標註者及20事件／60文件的權利清楚holdout；模型不能代簽這些資料。
