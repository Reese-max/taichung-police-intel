## 變更摘要

- 

## 警政使用價值

- 本次變更如何讓使用者更快找到需要處理的資訊：
- 是否改變「今日變更／歷史資料／來源健康」的語意：

## 證據與資料契約

- [ ] 不以 `fetched_at` 冒充 `published_at`、`effective_at` 或 `data_as_of`
- [ ] 來源失敗不會被呈現為零筆資料
- [ ] 歷史未變更項目不會冒充今日情報
- [ ] 每個公開項目保留 HTTPS 官方來源與可追溯識別
- [ ] `source-status.json`、feed、summary、CSV 屬於同一 collection run

## 驗證

- [ ] `npm run check`
- [ ] `python -X utf8 scripts/verify-publication-bundle.py`
- [ ] 手機版主要路徑檢查
- [ ] 空資料、來源失敗與 stale 狀態檢查

## 風險與回滾

- 風險：
- 回滾方式：
