# Issue #39：Retention / Rights / Archive Policy

`docs/govintel/retention-rights-policy.v1.json`（`policy_version` 2）與 `scripts/retention-policy.py` 是單一治理規則來源，讓 collector／publication／query／replay 共用同一套保存、授權與公開邊界。政策只給保守預設，不做法律結論。

## 資料類型矩陣

`data_type_matrix` 把每一種資料型別對到一個 retention class 或一個 storage layer；`layer_policies` 為每一層定義各自的保留來源、archive eligibility 與公開投影。compile 會 fail closed：layer 集合、layer↔data type 對應、class 引用都必須完整且唯一。

| data type | 類型 | 治理 |
|---|---|---|
| `OFFICIAL_OPEN_DATA_STRUCTURED_RECORD` | CONTENT | `REFERENCE_METADATA`，sensitive by default，只允許彙總 |
| `OFFICIAL_HTML_PDF_ATTACHMENT` | CONTENT | `OFFICIAL_METADATA_LINK` |
| `OFFICIAL_TRANSIENT_LIVE_ALERT` | CONTENT | `OFFICIAL_TRANSIENT_METADATA`，raw 7 天 |
| `MEDIA_RSS_NEWS_DISCOVERY_METADATA` | CONTENT | `OFFICIAL_MEDIA_LINK`，link-only |
| `LLM_DERIVED_SUMMARY_LABEL` | CONTENT | `DERIVED_SUMMARY`，project controlled |
| `RAW_SNAPSHOT` | LAYER | raw retention class，`NOT_ARCHIVABLE` |
| `NORMALIZED_DOCUMENT` | LAYER | raw retention class，`NOT_ARCHIVABLE` |
| `CANONICAL_GOVINTEL_EVENT` | LAYER | canonical retention class，`EVIDENCE_ARCHIVE` |
| `CANONICAL_GOVINTEL_RECEIPT` | LAYER | canonical retention class，`EVIDENCE_ARCHIVE` |
| `QUERY_INDEX` | LAYER | canonical retention class，`PROVENANCE_ONLY` |

## 每個 catalog source 都會發布的事實

`compile_policy()` 為 20 個 catalog source 一對一產出 `source_policies[source_id]`：

- `terms_url` 取自 catalog `entrypoint`，且必須是 `https://`。它只是收集定位，不是授權。
- `terms_status`：`UNKNOWN` rights 一律是 `CATALOG_ENTRYPOINT_NOT_A_LICENSE`；只有 project 自有衍生物可以用 `PROJECT_OWNED_NO_THIRD_PARTY_LICENSE`。
- `rights_status` 必須出現在 `rights_status_values` 宣告詞彙內；未宣告值一律拒絕。
- `public_projection`／`public_fields` 決定可公開欄位；`full_text_allowed`、`excerpt_allowed` 在本政策一律 `false`。
- `review_required`、`archive_eligibility`、`sensitive_default`、`withdrawal_behavior`／`withdrawal_projection`。
- `raw_retention_class` 與 `canonical_retention_class` 各自對應一個 retention window。

## 撤稿與歷史留存

官方來源把頁面拿掉不代表 GovIntel 假裝它從未存在。`project_public_record()` 在 `source_available: false` 時：

- 保留 provenance（`content_sha256`、`official_url`、`published_at`、`title` 等 allowlist 欄位）；
- `source_availability` 標為 `SOURCE_NO_LONGER_AVAILABLE`、`currently_available: false`，不偽稱目前仍可查；
- `withdrawal_behavior` 為 `RETRACT_*` 的 class（project 自有衍生物）改標 `RETRACTED` 並清空投影內容，只留稽核軌跡。

`withdrawal_projection` 必須與 `withdrawal_behavior` 一致，compile 會驗證。

## 個資與敏感資料

`archive_decision()` 先於任何公開投影執行，fail closed：

- 帶 `personal_data`、`private_notes`、`operational_fields`、`credentials` 等治理標記 → `BLOCKED`；
- `contains_personal_data: true` 或 `sensitive: true` → `BLOCKED`；
- class 為 `sensitive_default`（`REFERENCE_METADATA`）而 record 未宣告 `aggregate_only: true` → `BLOCKED`（#28 的「排除或只用彙總」）；
- `archive_eligibility: NOT_ARCHIVABLE` → `BLOCKED`。

`raw_payload`、`raw_bytes`、`body`、`full_text` 屬於內容負載：投影會丟棄並記在 `dropped_fields`，所以 media 的 link-only 投影仍然成立；purge 路徑（`plan_expiry`）對任何 prohibited 欄位則直接報錯，不進入規劃。

## Expiry 與 replay 證據

`plan_expiry()` 是唯讀 dry-run，產生綁定 `policy_version`／`policy_hash` 的 receipt：

- canonical／publication／query index 到期必須有 `audit_refs`，否則 `BLOCKED_NO_AUDIT_LINKAGE`；
- `replay_required: true` 的 record 不會被保留策略靜默清除：action 強制改為 `KEEP_AUDIT_LINKAGE`，`replay_status` 為 `PRESERVED`；
- 若該來源已撤下，`replay_status: LIMITED` 並附 `replay_limitation: SOURCE_NO_LONGER_AVAILABLE_REPLAY_PARTIAL`，明確標示無法完整重播，而不是假裝還能重播；
- 缺 `audit_refs` 時 `replay_status: BLOCKED`。

`replay_evidence_fields` 宣告 #36 replay 所需的 provenance 欄位，`replay_limitation_reasons` 必須宣告 withdrawn-source 原因。

## Query index 只是 projection

`project_query_index()` 輸出 `projection_only: true`、`extends_source_retention: false`，逐筆：

- 先剝除 prohibited 欄位並記在 `suppressed_fields`；
- 只保留 class allowlist 欄位，`full_text`／`body` 即使出現在舊 rebuild 輸入也不會被投影出去；
- 標出 `source_raw_retention_expired`，讓 UI 知道 raw 窗口已過。

重建 query index 因此不能延長或復活原資料不允許的 retention。

## 驗證

```bash
python -X utf8 -m unittest discover -s tests -p 'test_retention_policy.py' -v
python -X utf8 scripts/retention-policy.py --self-check
python -X utf8 scripts/retention-policy.py --json
python -X utf8 scripts/retention-policy.py --project manifest.json --at 2026-09-21T00:00:00+00:00
python -X utf8 scripts/retention-policy.py --plan manifest.json --at 2026-09-21T00:00:00+00:00 --output receipt.json
```

`npm test` 會跑 `retention-policy-tests` 與 `retention-policy-self-check`，`apps/web/tests/retention-policy.test.mjs` 另外驗證 UI 讀到的治理 receipt 不會把 unknown rights 表現為 open permission。