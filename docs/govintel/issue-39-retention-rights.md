# Issue #39：Retention / Rights / Archive Policy

`docs/govintel/retention-rights-policy.v1.json`（`policy_version` 2）與 `scripts/retention-policy.py` 是本專案的保存、授權與公開邊界規則來源。它只給保守預設，不做法律結論。

## 已實作的範圍（以及還沒做的部分）

已接線：**對外綁定**。`scripts/query-gateway.py` 的每個 response 與 `workers/query-gateway/src/index.js` 都引用同一份由 `retention-policy.py --binding` 產生的 `RETENTION_BINDING`，因此 UI 與 gateway 不可能各自宣稱不同的權利狀態。

尚未接線（**本議題不宣稱已完成**）：archive gate 目前只由 `scripts/retention-policy.py` 的 CLI 與測試呼叫。實際的 live query 路徑是 `scripts/query-store.py` → `apps/web/public/data/*.json` → gateway／worker，它並沒有 import retention module；`scripts/query-store.py` 內另有一個同名但無關的 `policy_binding` 區域變數。collector 與 `scripts/migration_replay.py` 也仍走自己的資料管線。換句話說：`--project`／`--plan` 的規則已經是正確且測試過的守門員，但把它們插進 live query 路徑是後續議題的工作。

## 資料類型矩陣

`data_type_matrix` 把每一種資料型別對到一個 retention class（CONTENT）或一個 storage layer（LAYER）；`layer_policies` 為每一層定義 retention 來源、archive eligibility 與公開投影。compile 會 fail closed：layer 集合、layer↔data type 對應、class 引用、data type 唯一性都必須完整；公開投影為 `NONE` 的層必須是 `NOT_ARCHIVABLE`，避免出現「永不公開卻可公開封存」的空隙。

| data type | 類型 | 治理 |
|---|---|---|
| `OFFICIAL_OPEN_DATA_STRUCTURED_RECORD` | CONTENT | `REFERENCE_METADATA`，sensitive by default，只允許彙總 |
| `OFFICIAL_HTML_PDF_ATTACHMENT` | CONTENT | `OFFICIAL_METADATA_LINK` |
| `OFFICIAL_TRANSIENT_LIVE_ALERT` | CONTENT | `OFFICIAL_TRANSIENT_METADATA`，raw 7 天 |
| `MEDIA_RSS_NEWS_DISCOVERY_METADATA` | CONTENT | `OFFICIAL_MEDIA_LINK`，link-only |
| `LLM_DERIVED_SUMMARY_LABEL` | CONTENT | `DERIVED_SUMMARY`，project controlled |
| `RAW_SNAPSHOT` | LAYER | raw retention class，`NOT_ARCHIVABLE`／`NONE` |
| `NORMALIZED_DOCUMENT` | LAYER | raw retention class，`NOT_ARCHIVABLE`／`NONE` |
| `CANONICAL_GOVINTEL_EVENT` | LAYER | canonical retention class，`EVIDENCE_ARCHIVE` |
| `CANONICAL_GOVINTEL_RECEIPT` | LAYER | canonical retention class，`EVIDENCE_ARCHIVE` |
| `QUERY_INDEX` | LAYER | canonical retention class，`PROVENANCE_ONLY` |

layer 的規則會**優先於** class：`raw_snapshot`／`normalized_document` 即使來源 class 允許公開，也永遠進不了公開 archive（`archive_decision()` 先看 `layer_archive_eligibility`）。class 則決定 rights 與 allowlist，兩者分開，不互相覆蓋。

## 每個 catalog source 都會發布的事實

`compile_policy()` 為 20 個 catalog source 一對一產出 `source_policies[source_id]`：

- `terms_url` 取自 catalog `entrypoint`，且必須是 `https://`。它只是收集定位，不是授權。
- `terms_status`：`UNKNOWN` rights 一律是 `CATALOG_ENTRYPOINT_NOT_A_LICENSE`；只有 project 自有衍生物可以用 `PROJECT_OWNED_NO_THIRD_PARTY_LICENSE`。
- `rights_status`／`terms_status` 必須出現在政策宣告的詞彙內；未宣告值一律拒絕。
- `public_projection`／`public_fields` 決定可公開欄位；`full_text_allowed`、`excerpt_allowed` 在本政策一律 `false`。
- `review_required`、`archive_eligibility`、`sensitive_default`、`withdrawal_behavior`／`withdrawal_projection`。
- `raw_retention_class` 與 `canonical_retention_class` 各自對應一個 retention window；實際天數由該 window 的 `max_age_days` 決定。

class 的欄位集合是**精確比對**：多一個或少一個欄位都會讓 compile 失敗，避免未知欄位在 merge 時蓋掉 source 層事實（`terms_url`、`data_types`）。

## 撤稿與歷史留存

官方來源把頁面拿掉不代表 GovIntel 假裝它從未存在。`project_public_record()` 在 `source_available: false` 時：

- 保留 provenance（`content_sha256`、`official_url`、`published_at`、`title` 等 allowlist 欄位）；
- `source_availability` 標為 `SOURCE_NO_LONGER_AVAILABLE`、`currently_available: false`，不偽稱目前仍可查；
- `withdrawal_behavior` 為 `RETRACT_*` 的 class（project 自有衍生物）改標 `RETRACTED` 並清空投影內容，只留稽核軌跡。

`withdrawal_projection` 必須與 `withdrawal_behavior` 一致，compile 會驗證。`source_available` 未宣告時視為可用；明確傳 `null` 會被拒絕。

## 治理 class 由呼叫端決定，不由資料決定

`class_id` 是 `project_public_record()`／`archive_decision()` 的**可信參數**，不會從 record JSON 讀取。理由：一個存在資料庫裡的 payload 不該能把自己改標成權限較寬鬆的 class（例如把 `REFERENCE_METADATA` 換成 `DERIVED_SUMMARY` 來繞過 sensitive-default gate）。輸出中的 `terms_url` 另綁 `catalog_hash`，所以 catalog 改動也會被看見。要把 project 自有衍生物當成 `DERIVED_SUMMARY` 投影，必須由呼叫端明確指定。

## 個資與敏感資料

`archive_decision()` 先於任何公開投影執行，且執行在**未被清洗的原始 record** 上——先清洗再判斷會把治理違規降級成靜默成功。

- 帶 `personal_data`、`private_notes`、`operational_fields`、`credentials` 等治理標記 → `BLOCKED`；
- `contains_personal_data: true` 或 `sensitive: true` → `BLOCKED`；
- class 為 `sensitive_default`（`REFERENCE_METADATA`）而 record 未宣告 `aggregate_only: true` → `BLOCKED`（#28 的「排除或只用彙總」）；
- layer 或 class 為 `NOT_ARCHIVABLE` → `BLOCKED`。

`raw_payload`、`raw_bytes`、`body`、`full_text` 屬於內容負載（compile 會確認它們都在 `prohibited_public_fields` 內）：投影會丟棄並記在 `dropped_fields`，query-index 路徑則記在 `suppressed_fields`，所以 media 的 link-only 投影仍然成立；purge 路徑（`plan_expiry`）對任何 prohibited 欄位則直接報錯，不進入規劃。投影值若無法 JSON 序列化會回報是哪個欄位，而不是讓整個索引建置中斷。

## Expiry 與 replay 證據

`plan_expiry()` 是唯讀 dry-run，產生綁定 `policy_version`／`policy_hash` 的 receipt；receipt 的 `receipt_sha256` 綁定整份內容（含每一列），`counts` 同時列出 retained/expired/blocked 與 replay_preserved/replay_limited/replay_blocked。

- canonical／publication／query index 到期必須有 `audit_refs`，否則 `BLOCKED_NO_AUDIT_LINKAGE`；
- `replay_required: true` 的 record 不會被保留策略靜默清除：到期且原本的動作允許覆寫時，action 改為 `KEEP_AUDIT_LINKAGE`，`overridden_expired_action` 記錄被覆寫的原動作；
- `audit_refs` 為空或缺少 `replay_evidence_fields` 宣告的 provenance 時，`replay_status: BLOCKED` 且附 `REPLAY_EVIDENCE_INCOMPLETE`，不會宣稱保留了不存在的 linkage；
- 原本的 `expired_action` 是 `REVIEW_REQUIRED` 時（raw 或 canonical layer 都算），`replay_status: LIMITED` 並附 `REPLAY_REVIEW_WINDOW_PENDING`：證據被保留，但重播仍在等人審，不會標成 preserved；
- `PRESERVED` 與 limitation 互斥；planner 產生前會自我檢查這個不變式。
- 若來源已撤下且證據齊全，`replay_status: LIMITED` 並附 `SOURCE_NO_LONGER_AVAILABLE_REPLAY_PARTIAL`，明確標示無法完整重播，而不是假裝還能重播。

`replay_evidence_fields` 是 planner 實際比對的清單，缺項會逐項列在 `replay_missing_fields`；`replay_limitation_reasons` 必須涵蓋 planner 會發出的每一種原因，否則 compile 失敗。

## Query index 只是 projection

`project_query_index()` 輸出 `projection_only: true`、`extends_source_retention: false`，逐筆：

- 先對原始 record 跑 `archive_decision(record, layer="query_index")`（fail closed），再剝除 prohibited 欄位並記在 `suppressed_fields`；宣告其他 layer 的 record 直接拒絕，不會因為自稱 `canonical_event` 就取得較弱的 gate；
- 只保留 class allowlist 欄位，`full_text`／`body` 即使出現在舊 rebuild 輸入也不會被投影出去；
- 標出 `source_raw_retention_expired`，讓 UI 知道 raw 窗口已過——它不放棄該筆記錄的 metadata/link 投影（那是 canonical/provenance 規則允許的），只是不讓任何人重新取得已過期的全文；
- `projection_hash` 與 `index_hash` 都描述呼叫端實際收到的內容。

## 對外綁定不得漂移

`python3 scripts/retention-policy.py --binding` 產生 `apps/web/public/data/retention-policy-binding.json`，`workers/query-gateway/src/index.js` 直接 import 這份檔案，`scripts/query-gateway.py` 則在啟動時由編譯後的政策推導同一組欄位。`apps/web/tests/retention-policy.test.mjs` 會比對已檢入的檔案與 `--binding` 輸出，所以手寫的舊 `policy_version`／`policy_hash` 無法存活。

binding 的每個欄位都由編譯後的政策推導：`public_projection` 來自 query index layer，`full_text_allowed`／`excerpt_allowed` 以「全部 class 都允許」為準，`catalog_hash` 一併輸出。rights/terms 狀態則以**最嚴格**的 class 為準（任一 class 為 `UNKNOWN` 就報 `UNKNOWN`），因此 consumer 不可能把未驗證的權限畫成已開放授權。

## 驗證

```bash
python -X utf8 -m unittest discover -s tests -p 'test_retention_policy.py' -v
python -X utf8 scripts/retention-policy.py --self-check
python -X utf8 scripts/retention-policy.py --json
python -X utf8 scripts/retention-policy.py --binding
python -X utf8 scripts/retention-policy.py --project projection-manifest.json --at 2026-09-21T00:00:00+00:00
python -X utf8 scripts/retention-policy.py --plan expiry-manifest.json --at 2026-09-21T00:00:00+00:00 --output receipt.json
```

`--project` 與 `--plan` 的輸入 manifest 結構相同（`{"records": [...]}`），但語義不同：`--project` 只接受 archiveable 的層，`--plan` 接受全部層。

`npm test` 會跑 `retention-policy-tests` 與 `retention-policy-self-check`；`apps/web/tests/retention-policy.test.mjs` 另外驗證對外 binding、worker 匯入、CLI 的 `--plan`／`project` 行為，並確認 self-check receipt 的每個欄位都來自編譯後的政策而非字面值。