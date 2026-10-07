# 候選 HTML 列表的範圍與有限遍歷

本次修復僅處理來源取得與計數，不准入來源、不替代權利或真人覆核。

## 修復

- 列表解析排除 `nav`、`header`、`footer`、`aside` 中的文章形狀連結。2026-10-07 S019 官方首頁的結構觀察顯示，真正日期化條目位於 `main#center > section.list`，站台導覽亦含 `/ID/post`，且可能先於同 ID 的真正條目出現。保留根相對文章 URL；不捏造分類路徑或日期。真正內容中的未知日期仍保留並造成 `PARTIAL`。
- 針對已含明確 `Page` 的列表 URL，只有官方「最後一頁」連結與目前頁數相同、同 HTTPS origin／路徑／其餘查詢參數，且其他頁碼控制不矛盾、沒有 Next 控制，才可證明末頁。未知總頁數、重複參數、不同路徑／參數／origin 或消失的分頁控制仍不能證明完整。未分頁原始 URL 的既有判定不在本次改寫範圍。
- 官方 Page25 的 `section.function` 另含「回上一頁／javascript:history.back()」瀏覽器工具，不是 `section.page` 的真正上一頁。只排除這個已觀察到的精確工具與位置；真正 pager 內的相同 JavaScript、未知 JavaScript 或矛盾頁码不被忽略。
- `collect_news_list`／`collect_source` 可明確指定 `max_list_pages`（1–40）；預設仍為 4。每次請求仍通過來源 URL 與重新導向限制。完整遍歷判定必須到達可解析的末頁；遇到頁數上限、循環或無法解析的分頁仍為 `PARTIAL`。
- 候選 CLI 可指定 `--list-page-limit` 與 `--http-call-limit`（1–64，預設 6），實際 HTTP 嘗試包含重新導向，禁止隱藏重試。這是一次執行的明確預算，不自動增加正式排程預算。
- 新增 `list_traversal` 收據：要求起訖、停下原因、觀察日期上下界、日期是否完整、順序觀察、詳頁是否全取得。`whole_history_completeness` 一律 `UNKNOWN`，`attachments_downloaded` 一律 false；RSS 是快照，不是全部歷史。

## 使用

例如針對 S019 明確允許較深、仍有限的遍歷：

```sh
python scripts/candidate-runtime-canary.py --source S-019 \
  --list-page-limit 30 --http-call-limit 40 --output /tmp/s019-observation.json
```

指令不是成功收據；只有實際執行產出的日期、HTTP 結果及停止原因才是觀察證據。預算消耗完即停止，不能繞過 403、改用未核准鏡像或推定缺漏為零。S032 已觀察到的完整列表可能超過 40 頁，因此本次上限不保證取得全部列表。

「較舊條目已出現」不證明後續頁面沒有釘選或較新文章。收集器不以該條件提早停止；既有日期完整、逆序及日期邊界檢查維持。即使要求期間的收集器計數通過，`coverage_independently_verified` 與 `promotion_eligible` 仍 false；詳頁、附件、全歷史及權利是另外的驗收條件。

## CLI 委派結果

實際 agy 首次 headless `accept-edits` 執行因 command 權限無法詢問而被拒絕；一次使用既有 YOLO 授權的有限重試，於 210 秒 print 上限回傳空白 partial response，未留下 tracked edits。CLI JSON 的 `SUCCESS` 不作為工作完成證據。以上修復由執行代理另行完成並以離線紅／綠回歸驗證，不宣稱 agy 完成修復。原始 CLI 輸出與測試紀錄保存於私人驗收包，不放入公開來源資料。
