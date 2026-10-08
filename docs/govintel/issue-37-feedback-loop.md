# Issue #37：Feedback Loop

`intel_v2/feedback.py` 與 `scripts/feedback.py` 提供 local-first correction signal：

- 9 類 reason：`FALSE_MERGE`、`MISSED_MERGE`、`WRONG_ENTITY`、`NOT_RELEVANT`、`MISSING_EVENT`、`WRONG_CHANGE_CLASSIFICATION`、`UNSUPPORTED_ANSWER`、`WRONG_STATISTIC_SCOPE`、`BAD_SOURCE_MAPPING`。
- target 可指向 `EVENT`、`ENTITY`、`QUERY` 或 `ANSWER` 的 id/version，也可連結既有 Review Inbox item。
- 原始輸出只保存 SHA-256，不保存私人對話、prompt 或全文；evidence/reference 以最小 ID／locator 保存。
- canonical 建立與載入都會遞迴拒絕整筆回饋 record 中的 `raw_prompt`、`conversation`、`full_text`、`private_notes` 欄位（不分大小寫），包含 `corrected_expected_state`／`evidence_refs`／`audit.payload` 的巢狀 object／array；錯誤不輸出欄位值。既有檔案含這些欄位時 fail closed，不會自動刪除、改寫或接受。最小 IDs／locators、一般 expected state 與必要的普通片段（例如 `excerpt`）仍可使用；這是明確欄位名的邊界，不是任意自由文字的敏感內容偵測，操作者仍須只提供重現錯誤所需的最小資料。
- 相同 fingerprint 去重；每筆保留 `model_version`、`parser_version`、`registry_hash`、audit 與 review status。
- 載入狀態時會驗證 feedback ID／fingerprint、fingerprint 與記錄內容一致、audit sequence／hash binding 與 review decision 一致；被竄改或未完成 review 的記錄 fail closed。
- record、target、trace、decision 與 fixture 都是封閉 schema（`RECORD_KEYS`／`TARGET_KEYS`／`TRACE_KEYS`／`DECISION_KEYS`／`FIXTURE_KEYS`），未知欄位（例如 `auto_apply`）一律不會載入；`ACCEPTED` 才會產生 `FEEDBACK_REGRESSION` link，且明確標成需要後續 gold promotion review；不會自動修改 entity registry、event fusion、evidence gate 或 production rule。`validate_record` 同時拒絕非 `ACCEPTED` 記錄掛著 regression fixture。
- `statistics` 可按 reason、review status、model/parser/registry 版本查詢錯誤分布。

## Promotion routes（AC4 / AC5）

每個 `ACCEPTED` 的 fixture 都帶 `promotion_targets`（可提案的下游系統）、`effect_scope`、`mutates_verified_truth` 與 `requires_human_approval: true`。這些欄位只描述「可以被提案到哪裡」，實際變更仍須人工核准：

| reason | promotion_targets |
| --- | --- |
| `FALSE_MERGE`、`MISSED_MERGE`、`WRONG_ENTITY` | `ENTITY_REGISTRY`（#31）、`EVENT_FUSION`（#24） |
| `MISSING_EVENT`、`WRONG_CHANGE_CLASSIFICATION` | `EVENT_FUSION`（#24）、`GOLD_DATASET`（#33） |
| `UNSUPPORTED_ANSWER`、`WRONG_STATISTIC_SCOPE` | `ANSWER_EVIDENCE_GATE`（#32）、`GOLD_DATASET`（#33） |
| `BAD_SOURCE_MAPPING` | `EVENT_FUSION`（#24）、`SOURCE_POLICY` |
| `NOT_RELEVANT` | `RELEVANCE_EVALUATION` |

`promotion_targets` 與 reason 綁定：`validate_state`／`validate_record` 在讀取狀態時會重新推導並比對 route 欄位、`effect_scope`、兩個 boolean（`1`/`0` 不算 `true`/`false`）、`fixture_kind`、`fixture_id`（`FEEDBACK_REGRESSION` 另須由 feedback ID 推導）、`reason`、`feedback_id`、`target`、`expected` 與 `requires_gold_promotion_review`。fixture schema 本身是封閉的（`FIXTURE_KEYS` 逐 kind 列舉允許欄位），因此任何未知欄位（例如 `auto_apply`）都不會載入；`decided_at`／`linked_at` 也分別鎖住兩種 fixture：兩種 kind 的欄位集不對齊，被改標成另一 kind（不論保留或刪掉對方的欄位）都會因未知或缺少欄位而 fail closed，外部 fixture 也無法夾帶 `target`／`expected`。fingerprint 另會由記錄內容重新計算比對，所以改寫 `corrected_expected_state`（連同 fixture 一起改）仍然 fail closed。威脅模型僅限「未重算雜湊的竄改」：`state/feedback.json` 沒有簽章，能同時重算 fingerprint／feedback_id／audit_id 與物件 key 的攻擊者仍可偽造一筆自洽的 `ACCEPTED` 記錄，因此人工 review 仍是唯一的最後一道關卡。`link-regression` 連結外部 fixture 時同樣寫入這組欄位，避免外部 fixture 繞過核准標記。schema_version 1 早期寫入、尚未帶 route 欄位的 fixture 會由 `normalize_state`（CLI 載入路徑；回傳正規化後的副本，不改寫傳入物件）依 reason 推導補齊（補齊值完全由 reason 決定，不會帶來額外權限）；只缺一部分欄位則直接 fail closed。純驗證的 `validate_state` 不做升級，因此會拒絕這類舊檔案——讀取舊檔案請走 `normalize_state`。

## 不會變成訓練資料或自動改 production rule

`promotion_targets` 只是「可以被提案到哪裡」的描述，這一版沒有任何 consumer：`intel_v2/feedback.py` 與 `scripts/feedback.py` 只讀寫 `state/feedback.json`，不讀 entity registry、answer evidence gate、gold dataset 或任何 rule／prompt 檔案，所以 accepted feedback 在結構上無法自行改動 production truth，也沒有任何匯出路徑把它送進模型訓練。`route_fields_for` 的呼叫點只有 fixture 產生（`build_regression_fixture`／`link_regression`）與兩條驗證路徑（`_bind_route_fields` 比對、`_backfill_legacy_route_fields` 補齊）；`statistics` 走 `promotion_targets_for`／`effect_scope_for`，不經 `route_fields_for`。

## NOT_RELEVANT 只影響 relevance／evaluation（Regression 3）

`NOT_RELEVANT` 是相關性判斷，不是事實判斷，因此 `effect_scope: "RELEVANCE_ONLY"` 且 `mutates_verified_truth: false`。`corrected_expected_state` 對 `NOT_RELEVANT` 只能使用 relevance allowlist：`relevance`、`relevant`、`relevance_reason`、`relevance_score`、`relevance_note`、`profile_relevance`、`local_police_relevance`（沿用 repo 既有的 relevance 詞彙；巢狀 dict／list 內的 key、非字串 key，以及 JSON 無法還原的容器型別如 tuple／set 都一樣檢查）。記錄層同樣沒有夾帶空間：`linked_review_id` 只能是非空字串或 null 且已納入 fingerprint，`evidence_refs` 在 `RELEVANCE_ONLY` 下只接受非空參照字串（其他 reason 仍可用結構化 locator），兩者都沒有夾帶事實主張的空間。建立或載入 `NOT_RELEVANT` 記錄時，`corrected_expected_state` 只要出現 allowlist 以外的欄位（例如 `delete_event`、`retract`、`verified`、`purge`），一律 raise `ValueError` fail closed——使用者按「不相關」永遠無法刪除或改寫已驗證事件，只能回報 relevance／評測層面的判斷。其他 reason 仍可自由描述事實修正。

`statistics` 另外輸出 `accepted`、`by_effect_scope` 與 `by_route`；route 與 effect scope 只統計 `ACCEPTED` 記錄（可回答「哪些下游系統還等人工核准」），`by_reason`／`by_status` 則保留未過濾的「最常見錯誤類型」視圖。

操作範例：

```bash
python scripts/feedback.py add --target-type EVENT --target-id PE-demo --target-version v1 \
  --reason FALSE_MERGE --output-hash <64-char-sha256> \
  --corrected-state '{"same_event":false}' --evidence-refs '["S-036#fixture"]' \
  --review-id REVIEW-demo
python scripts/feedback.py review --feedback-id FEEDBACK-... --status ACCEPTED --reviewer-ref operator-1
python scripts/feedback.py stats
```

`review --status ACCEPTED` 會印出 `routes=ENTITY_REGISTRY,EVENT_FUSION human_approval=True`，表示這筆回饋可提案到 #31／#24 且仍待人工核准。

`state/feedback.json` 是 canonical、可重播的本地狀態；公開 Web 不直接寫入它。V2 Review Inbox 現在可建立 9 類、版本／evidence hash 綁定的瀏覽器本機 feedback draft；Ask GovIntel 的查詢結果也可建立 `QUERY` draft，若 Gateway 回傳受控答案證據收據則可建立 `ANSWER` draft。查詢／答案只保存輸出 SHA-256，不保存原始輸出；所有草稿均可匯出 Markdown/JSON，仍須人工轉入 `scripts/feedback.py` 才會進入 canonical review gate。這一版完成 feedback record、review gate、dedupe、regression link、promotion routes、relevance-only 限制、statistics 與 bounded local UI draft，尚未宣稱已接上遠端通知或自動 online learning。
