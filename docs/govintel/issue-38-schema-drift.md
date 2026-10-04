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
- 每個 signature 都由 `_signature` 統一補上 `source_id`，連錯誤分支也是；所以兩個欄位結構相同但來源不同的 dataset（真實的 S-028 與 CTX-POP 就是這樣）不會拿到同一個 fingerprint。
- receipt 的每筆 source 另存 `fingerprint_signature`，讓 review 可以直接看到「觀察到什麼形狀」。這會隨 receipt 一起發佈到 `apps/web/public/data/schema-drift.json`；內容只有公開政府來源的欄位名稱、CSV header 與 content type，沒有查詢參數或內容本文。
- 從遠端回應帶進 signature 的名稱清單都受上限保護：欄位名稱與 CSV header 用 `MAX_SIGNATURE_NAMES`（500），`row_column_counts` 用 `MAX_ROW_REASONS`（10）。這些上限只作用在 signature 的呈現，required 欄位存在性、型別與欄位數判定都用完整清單，所以上限不會改變任何 verdict。
- 指紋反映的是「這一筆觀測看到的形狀」，不是資料量。`record_types` / `field_types` 是這一列取樣到的型別集合，`date_coverage` 也是這一頁的覆蓋分級，所以某個 optional 欄位在某一頁全部是 null、下一頁出現值時，指紋仍會變。這是刻意的取樣誠實，不是告警：目前沒有任何 alert 以指紋比較觸發，只有 `resource_id` 變更會升級成需要覆核。
- `contract_version` 已推進到 `1.1`。1.0 以前持久化的 fingerprint 是舊 signature（含 pagination 數值、`n/m` 計數與 `entry_count`），與 1.1 之後的值不可直接比較；`update_state` 會把 state 頂層的 marker 更新成當前版本，但保留每筆 `current` / `last_known_good` / `history` 各自記錄的版本。

## 逐列的 required field 與欄位數

欄位集合只取所有 row 的聯集，原本會讓「部分 row 少一個 required 欄位」看起來正常：

- JSON/API 與 data.gov JSON 只要有任何一列缺少 required 欄位，就是 `REQUIRED_FIELD_MISSING_IN_SOME_ROWS` / `MISSING_IN_SOME_ROWS_<field>` 的 `BREAKING_DRIFT`，不會退化成假零資料。同時缺少欄位又改型別時，兩類 reason 會一起回報。
- data.gov CSV 除了 header，還驗證每筆資料列的欄位數等於 header 欄位數；截斷列或未跳脫的引號造成欄位位移會產生 `ROW_COLUMN_COUNT_MISMATCH` 的 `BREAKING_DRIFT`。`ROW_<n>_COLUMNS_<w>` 的 `<n>` 是原始資料列序號，reason 數量以 `MAX_ROW_REASONS` 為上限，總數記在 `MISMATCHED_ROW_COUNT_<n>`。JSON 與 data.gov JSON 則是逐列檢查 required 欄位「是否存在」；CSV 的欄位名稱來自 header，所以對 CSV 只檢查欄位數，不做逐列欄位存在性判斷。
- 沒有分隔符號的空白行不是資料列，因此不會造成欄位位移，也不會讓只有 header 的檔案看起來像完整窗口（那是 `NO_DATA_ROWS` 的 `CONTENT_SHAPE_UNKNOWN`）。帶了分隔符號的行永遠算資料列：欄位數正確但值全空的一列是資料列，欄位數不正確但值全空的一列是 `ROW_COLUMN_COUNT_MISMATCH`，不會被當成空白行吞掉。
- 刻意不做的事：required 欄位「有欄位但值是空字串」不算 schema drift。欄位存在與欄位有值是兩件事，後者是資料品質，publication lane 的 `source-status.json` / `intelligence_gaps` 已經負責；把它放進契約層只會在真實資源出現合法稀疏值時誤擋。
- CSV 讀取本身失敗（超過 `csv` 欄位長度上限等）只會讓該來源變成 `UNPARSEABLE_CSV` 的 `CONTENT_SHAPE_UNKNOWN`，不會中止整份 receipt，也不會讓其他來源失去 last-known-good。
- 這個欄位數檢查刻意比 production parser 嚴格。`canary-s028-165.py` 用 `csv.DictReader`，多出來的欄位會被 `restkey` 吞掉；monitor 必須先把這種不一致叫出來，而不是讓 parser 的靜默吸收決定結果。

## 窗口覆蓋與 replay 證據

- JSON/API 的 contract 另宣告 `record_count_path` 與 `page_count_path`。當觀察到的筆數小於宣告總數，或來源宣告超過一頁時，狀態仍是契約正常的 `NO_DRIFT` / `ADDITIVE_COMPATIBLE`（契約本身沒有變），`review_required` 仍是 false，但 `window_completeness` 降為 `PARTIAL` 並加上 `PAGINATION_WINDOW_NOT_COVERED`；`observed_record_count`、`declared_total_count`、`declared_total_pages` 留在 result 裡。一次 observation 就是一頁，所以宣告多頁的來源不可能被單頁完整觀察到。
- 這是「不假設窗口完整」的證據欄位，不是告警。bounded collector 對議會 API 只取第一頁（`pageSize=200`），因此只要某個 keyword 的筆數超過一頁，PARTIAL 就是每次執行都會出現的預期狀態。system health 的 `source_contracts` stage 與 `overall` 只看 `status`，所以不會因此降級；要處理這種覆蓋落差需要擴充 collector 分頁，而不是擴充 monitor。
- `resource_id` 的比對基準是 `last_known_good`，不是上一筆 `current`。抓不到資源的那次執行（例如 data.gov.tw 回 503）`current.resource_id` 是 null，若拿它當基準，一次暫時故障之後資料集換版就不會再產生 `RESOURCE_ID_CHANGED`，而且永遠不會再回報。基準在確認過一次正常比較後才會前進。
- `--input` replay 的每筆 observation 會先驗證：欄位名稱必須是 `observe()` 接受的參數（`previous` 等內部參數、`body_base64` / `body_text` 這種檔案格式欄位不接受）、型別必須正確（`resource_id` / `requested_url` / `final_url` 允許 null）、metadata 字串不得超過 `MAX_OBSERVATION_TEXT`（4096，`body` 本身不算）、`observed_at` 必須是帶時區的 ISO-8601（`intel_v2.review` 拒絕 naive timestamp，會讓整個 Review Inbox 投影被丟掉），`source_id` 必須在 contract 內且不重複。任何違規都在寫入 state 或 receipt 之前就拒絕。
- 進入 Review Inbox 的每一列都帶著該來源的 `last_known_good`，所以 `intel_v2.review` 投影出的 `evidence.before` 不再是 `null` — reviewer 從 inbox 就看得到「壞掉之前的契約指紋」。
- 沒有當次 observation 的來源標成 `NO_CURRENT_OBSERVATION` 且 `review_required` 為 false：那是「這次沒看到」，不是來源有問題，沒有東西需要人工覆核；它只會把 `overall` 拉到 `UNKNOWN` 並讓 system health 的 `source_contracts` stage 變 `UNKNOWN`。
- HTML / RSS 列表 contract（S-001、S-019、S-032、S-033）沒有宣告 pagination marker，`get_news_listing` 也只取列表端點回傳的那一頁，所以它們的 `window_completeness` 是「列表端點自己回報的完整度」，不是「整站所有分頁都抓完了」。要監測分頁需要先在 contract 宣告 marker，那是比這個 monitor 更大的改動；在此之前，這些來源的完整窗口定義就是那個端點。
- 抓不到 HTTP 回應的失敗（TLS、timeout、連線錯誤）在 receipt 裡是 `http_status: 0` 加 `LIVE_FETCH_*` reason，而不是捏造一個伺服器狀態碼；真的拿到非 2xx 才會記錄該狀態碼。

## state 的寫入時機

- `--live` 與 `--input` 都會寫回 `state/schema-drift-state.json`。只有這樣 last-known-good、fingerprint history 與 `RESOURCE_ID_CHANGED` 才可能跨執行存活 — `pages.yml` 每一次都會 `publication-state-branch.py restore` 這個檔案，再於 build 結尾 `persist` 回去。寫入走 temporary file + `os.replace` + `fsync`：cron 用 `timeout --signal=TERM` 包住 live probe，直接 `write_text` 被砍斷會留下一個壞掉的 state，而 publication state 是以 blob hash 還原、不解析 JSON，壞掉之後每一次執行都會失敗且自我修復不了。
- `--live-interrupted-receipt` 不會寫 state。那是 live probe 被中斷後的 fail-closed fallback，它沒有觀察到任何東西，用它覆寫 state 只會污染歷史。
- 每次 receipt 都記錄該次觀察到的 `http_status`，所以「HTTP 200 但契約壞掉」在 receipt 裡是看得見的，不只是 log。

驗證：

```bash
python -X utf8 -m unittest discover -s tests -p 'test_schema_drift.py' -v
python -X utf8 scripts/schema_drift.py --self-check
```

目前 live observation 仍須透過 bounded collector 執行；沒有當次 observation 時只能標示 `CONTENT_SHAPE_UNKNOWN`，不能推論來源健康。
