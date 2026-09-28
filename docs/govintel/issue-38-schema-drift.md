# Issue #38：Source Schema Drift Monitor

`scripts/schema_drift.py` 以 source contract 驗證 HTML、RSS、JSON API、data.gov JSON/CSV 與即時 HTML：

- required path/field、型別、pagination marker、resource ID、HTML selector/identity 與 CSV header 變更會產生 fingerprint 與 bounded receipt。
- `CONTENT_SHAPE_UNKNOWN` 不再被當成可忽略狀態；空 resource、HTTP 200 錯誤 content type、無法解析 JSON/CSV 都需要人工覆核。
- `BREAKING_DRIFT`、`SOURCE_UNAVAILABLE` 與 `CONTENT_SHAPE_UNKNOWN` 不會產生完整窗口成功；`update_state` 保留 last-known-good 與 fingerprint history。
- receipt 的 `review_inbox` 由既有 `intel_v2.review` 投影到 system health，不自動改 parser 或 production canonical data。

驗證：

```bash
python -X utf8 -m unittest discover -s tests -p 'test_schema_drift.py' -v
python -X utf8 scripts/schema_drift.py --self-check
```

目前 live observation 仍須透過 bounded collector 執行；沒有當次 observation 時只能標示 `CONTENT_SHAPE_UNKNOWN`，不能推論來源健康。
