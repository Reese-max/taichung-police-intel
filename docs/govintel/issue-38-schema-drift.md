# Issue #38：Source Schema Drift Monitor

`scripts/schema_drift.py` 以 source contract 驗證 HTML、RSS、JSON API、data.gov JSON/CSV 與即時 HTML：

- required path/field、型別、pagination marker、resource ID、HTML selector/identity 與 CSV header 變更會產生 fingerprint 與 bounded receipt。
- `CONTENT_SHAPE_UNKNOWN` 不再被當成可忽略狀態；空 resource、HTTP 200 錯誤 content type、無法解析 JSON/CSV 都需要人工覆核。
- `BREAKING_DRIFT`、`SOURCE_UNAVAILABLE` 與 `CONTENT_SHAPE_UNKNOWN` 不會產生完整窗口成功；`update_state` 保留 last-known-good 與 fingerprint history。
- receipt 的 `review_inbox` 由既有 `intel_v2.review` 投影到 system health，不自動改 parser 或 production canonical data。

## 只看「形狀」的契約指紋

`observed_schema_fingerprint` 只描述觀察到的 schema，不描述資料量，因此來源正常新增資料時指紋不會漂移：

- JSON/API 的 `pagination` 記錄的是 marker 的型別，不是 `totalPages` / `totalCount` 的數值。
- `record_types` / `field_types` 記錄的是所有 row 實際觀察到的型別集合，不再只取第一筆。
- HTML / RSS 以 `date_coverage` 分級（`ALL` / `PARTIAL` / `NONE` / `EMPTY`）取代 `n/m` 計數。
- receipt 的每筆 source 另存 `fingerprint_signature`，讓 review 可以直接看到「觀察到什麼形狀」。這會隨 receipt 一起發佈到 `apps/web/public/data/schema-drift.json`；內容只有公開政府來源的欄位名稱、CSV header 與 content type，沒有查詢參數或內容本文。
- `contract_version` 已推進到 `1.1`。1.0 以前持久化的 fingerprint 是舊 signature（含 pagination 數值、`n/m` 計數與 `entry_count`），與 1.1 之後的值不可直接比較，需要靠 version 判讀。

## 逐列的 required field 與欄位數

欄位集合只取所有 row 的聯集，原本會讓「部分 row 少一個 required 欄位」看起來正常：

- JSON/API 與 data.gov JSON 只要有任何一列缺少 required 欄位，就是 `REQUIRED_FIELD_MISSING_IN_SOME_ROWS` / `MISSING_IN_SOME_ROWS_<field>` 的 `BREAKING_DRIFT`，不會退化成假零資料。同時缺少欄位又改型別時，兩類 reason 會一起回報。
- data.gov CSV 除了 header，還驗證每筆資料列的欄位數等於 header 欄位數；截斷列或未跳脫的引號造成欄位位移會產生 `ROW_COLUMN_COUNT_MISMATCH` 的 `BREAKING_DRIFT`。`ROW_<n>_COLUMNS_<w>` 的 `<n>` 是原始資料列序號，reason 數量以 `MAX_ROW_REASONS` 為上限，總數記在 `MISMATCHED_ROW_COUNT_<n>`。
- 空白行不是資料列：不算欄位位移，也不會讓只有 header 的檔案看起來像完整窗口（那會是 `NO_DATA_ROWS` 的 `CONTENT_SHAPE_UNKNOWN`）。
- 這個欄位數檢查刻意比 production parser 嚴格。`canary-s028-165.py` 用 `csv.DictReader`，多出來的欄位會被 `restkey` 吞掉；monitor 必須先把這種不一致叫出來，而不是讓 parser 的靜默吸收決定結果。

## 窗口覆蓋與 replay 證據

- JSON/API 的 contract 另宣告 `record_count_path`。當觀察到的筆數小於宣告總數時（例如 bounded collector 只取第一頁），狀態仍是契約正常的 `NO_DRIFT`，但 `window_completeness` 會降為 `PARTIAL` 並加上 `PAGINATION_WINDOW_NOT_COVERED`；`observed_record_count` 與 `declared_total_count` 留在 result 裡。這是「不假設窗口完整」，不是把分頁當成 schema drift。
- `--input` replay 的每筆 observation 會先驗證：欄位名稱必須是已知參數、型別必須正確（`resource_id` / `requested_url` / `final_url` 允許 null）、`observed_at` 必須是可解析的 ISO-8601，`source_id` 必須在 contract 內且不重複。任何違規都在寫入 state 或 receipt 之前就拒絕。

驗證：

```bash
python -X utf8 -m unittest discover -s tests -p 'test_schema_drift.py' -v
python -X utf8 scripts/schema_drift.py --self-check
```

目前 live observation 仍須透過 bounded collector 執行；沒有當次 observation 時只能標示 `CONTENT_SHAPE_UNKNOWN`，不能推論來源健康。
