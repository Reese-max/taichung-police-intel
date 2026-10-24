# Issue #39：Retention / Rights / Archive Policy

`docs/govintel/retention-rights-policy.v1.json`（`policy_version` 2）與 `scripts/retention-policy.py` 是本專案的保存、授權與公開邊界規則來源。它只給保守預設，不做法律結論。

## 已實作的範圍（以及還沒做的部分）

已接線：**對外綁定**。`scripts/query-gateway.py` 在每個帶 `retention` 欄位的 response 上引用啟動時推導出的 `RETENTION_BINDING`；`workers/query-gateway/src/index.js` 則 import 由 `retention-policy.py --binding` 產生的 `apps/web/public/data/retention-policy-binding.json`（變數名 `RETENTION_POLICY`）。兩邊不可能各自宣稱不同的權利狀態。MCP `initialize`／`tools/list` 外層封裝本來就不帶 `retention` 欄位。

已接線：**公開 archive gate**。collector 在建立 feed 前共用 `archive_decision(..., layer="publication")`，檢查原始 item 與內層 payload；active／candidate 的 last-known-good（LKG）在任何發布檔寫入前再次檢查，拒絕時保留原有發布檔。安全 LKG 重新發布時移除內容負載與 payload。`scripts/query-store.py` 在欄位投影前共用 `archive_decision(..., layer="query_index")`，因此 canonical rebuild 與 migration replay 的現行重建入口也受同一規則約束；Worker 則在等效入口依既有 retention matrix 執行相同檢查。治理標記不會因先清洗欄位而被忽略，錯誤也不回傳標記值。

尚未完成：`project_public_record()` 的撤稿呈現與 `plan_expiry()` 的到期規劃，仍不能當成 collector／正式查詢已具備完整撤回傳播或實際清除的證據。逐來源真人審查、正式准入與部署驗收也須另有實際收據；本次僅修補公開投影前的 archive gate，不核准來源或用途。

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
- 若 record **已過期**、證據齊全，但來源已撤下，`replay_status: LIMITED` 並附 `SOURCE_NO_LONGER_AVAILABLE_REPLAY_PARTIAL`，明確標示無法完整重播，而不是假裝還能重播。（未過期的 withdrawn record 一律是 `RETAINED`／`PRESERVED`：還沒到期就沒有被清除的風險。）

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

本次新增 4 項回歸測試，相關測試由 88 項增至 92 項。先執行新增測試取得 `ValueError not raised`／`Missing expected rejection`，再接上 gate；另確認 LKG 的 active／candidate 兩條路徑及巢狀 payload 都會拒絕，拒絕時不覆寫檔案，安全資料仍可投影。權利核准的正例全部使用隔離的虛構 fixture，不修改真實審查或公開資料。

`npm run check:gate0` 結束碼 0，輸出 `VERIFY_OK mode=gate0 required=164 specs=4 secrets=0`；`git diff --check` 結束碼 0。完整 `npm run check` 結束碼 1，其中 `npm --prefix apps/web run build` 成功。失敗包含本機 Windows CPython 3.11 的相容性限制：admitted base 的 `read_source_bounded()` 可獨立重現 `os.O_NONBLOCK` 缺失，其他既有測試也有 Unix deadline／目錄操作、POSIX 權限或 symlink 限制；隔離 Python（`-I`）無法匯入 pypdf，HTTP wrapper 另有逾時。current-checkout 驗證器在未提交工作樹上會拒絕，即使其五個內部檢查都通過。專案未配置獨立 lint、coverage ratchet、CRAP 或 mutation gate，本次未新增這些工具。

```bash
python -X utf8 -B -m unittest discover -s tests -p test_retention_policy.py -v && python -X utf8 -B -m unittest discover -s tests -p test_source_governance_integration.py -v && python -X utf8 -B -m unittest discover -s tests -p test_candidate_publication_wiring.py -v && node --test apps/web/tests/query-gateway-worker.test.mjs
```

| 驗收條件 | 機制與限制 |
|---|---|
| versioned matrix、catalog 引用 rights／retention | 既有 policy compiler 與 source policy；本次不修改權利狀態 |
| media link-only、unknown 不宣稱 open | 既有 allowlist／governed policy；保留全文剝除與未知權利拒絕測試 |
| 個資／敏感資料不進 public archive | 本次補上 collector、兩種 LKG、Python Query Store、Worker 的投影前 gate；非布林治理旗標與禁止欄位也拒絕 |
| raw／normalized／publication／query index 分層規則 | 既有 layer policy；本次由可信呼叫端固定 publication／query_index layer |
| 撤稿後保留 history 並標示 unavailable | 既有領域投影測試；正式管線完整傳播仍待完成 |
| expiry dry-run、audit linkage 與 replay limitation | 既有 planner／receipt 與測試；實際到期／撤回執行及不復活收據仍待完成 |
| 逐來源、逐用途真人審查與正式准入 | 待實際審查；不以虛構 fixture 或工程測試取代 |
| 正式匿名正值查詢、來源轉換及產品驗收 | 未執行；沒有新 promotion、部署或真人結果 |

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
