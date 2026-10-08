# Issue #36：Schema Migration / Replay / Backfill

`scripts/migration_replay.py` 提供最小可重播流程：

- `PublicEvent`、`ChangeEvent`、`EvidenceEnvelope` 使用 `schema_version` 1 → 2 → 3 migration registry。每個 registry entry 都必須有對應實作（`MIGRATION_STEPS`），只註冊未實作的 step 會 fail closed，不會被靜默跳過。
- Bundle、每個 object 與 migration target 的版本宣告都必須是 registry 已登錄的正整數；boolean、浮點數、字串、null、非正數與未登錄版本會在遷移／寫入前拒絕，不以 `int()` 或 Python 的數值相等規則偷偷改寫版本。明確缺少宣告的 legacy bundle 仍預設版本 1，缺少 object 宣告仍沿用 bundle 版本；這個相容路徑不包含明確宣告 null 或錯誤型別。拒絕 apply／rehash 時既有 output 與 in-place input 保留原 bytes；合法的 migration、人工 state、重播及版本說明行為保持原契約。
- `dry-run` 回報 input/output hash、affected/error count、object counts、實際執行的 registry path、版本 binding 與人工 state hash；任一 object collection 缺少、未知或有錯誤時 fail closed。`dry-run` 只寫報告，不寫 migrated bundle。
- `apply` 只在整批無錯誤後寫入，並把 migration receipt 放進與 migrated data **同一個 atomic write**，所以 apply 中途 crash 不會留下「有資料但不知道是哪個版本產出」的狀態；既有 output 也不會被部分 migration 覆蓋。receipt 是 derived metadata，再跑一次 `apply` 時會先剝掉（`apply_receipt`、`body_hash_migration_receipt` 為保留 key），所以 migrated data 與 `output_sha256` 兩次一致；`input_sha256` 仍然記錄該次執行真正讀到的 bytes，因此是刻意不同。
- `replay` 以相同 feed、previous state、parser/semantics/projection version 重建 deterministic state/event IDs，保留 `FIRST_SEEN` 語意。
- `manual_state`、watch、handoff、fusion/merge/split 與 review state 以獨立 payload 保留，receipt 標記 `PRESERVE_SEPARATELY_NO_AUTOMATIC_MUTATION`，並輸出 `manual_state_reapply_plan`：逐項列出 hash、依各 owner 真實欄位（`watch_items` / `handoffs` / `items` / `manual_history`）算出的 decision 數量、`shape_recognised`，以及真正能寫入該 state 的 writer 指令。無法辨識的 shape 回報 0 而不是假裝有 1 筆決策。replay 不偷偷改寫或重套人工決策。
- `rebuild-query-store` 重用 `scripts/query-store.py`，從指定的 feed/status/brief canonical generation 完整重建 Query Store。每個 artifact 只讀一次，receipt 綁定 `generation_id`、三個 artifact hash 與同一份已讀文件的 version explanation。`--expect-generation` 給定且不符時拒絕寫入；未給定時 receipt 記為 `expected_generation_verified: false`（此時只是 generation-derived，不是 generation-pinned）。store 是權威且自我驗證的 artifact（query-store 的 `atomic_write_json` 會先 `validate_store`，而 `validate_store` 重算涵蓋 `generation_id` 的 projection hash），所以先寫 store、後寫 receipt：中途 crash 只會留下缺少或過期的 derived receipt，比對它的 `generation_id` 即可發現；反過來先寫 receipt 就會留下一個宣稱「某個 generation 已存在」但磁碟上根本沒有的 receipt。`--receipt-output` 未指定時，receipt 不可落在 `apps/web/public` 內（那是發佈表面，會外洩內部 hash 並留下未追蹤檔案）。
- `explain-receipt` 對任何舊 publication receipt／bundle 說明當時的 schema / body-hash / parser / semantics / projection / derivation / model version。值只接受 artifact（或其 `version_binding`）宣告且通過型別/註冊檢查的值，或所有保存物件一致宣告的值；其餘一律標記 unresolved（`UNKNOWN_UNDECLARED`）。保存物件互相衝突、或 top-level 值與物件矛盾，都報 `conflicting_object_declarations`，不仲裁、不補。沒有 `migration_history` 的 artifact 回報 `migration_path: []` 與 `absent_never_migrated`，不會從目前的 registry 反推它被遷移過。本工具自己 replay 路徑使用的版本另外放在 `replay_tool_versions`，不與 artifact 的版本混淆。
- `rehash` 是 body-hash semantics 的獨立升版軸。`content_sha256` 換語意後舊 hash 不可讀，必須從保留的 raw/canonical snapshot 重新推導；因為 hash 改變，stable identity 也會重新推導，並且這個 rename 會 cascade 到引用它的 `ChangeEvent.stable_id`，留下 dangling reference 時整批 fail closed。retention 已丟棄 raw bytes 時報 `UNDETERMINABLE_NO_RAW_SNAPSHOT` 並在 error 上標 `reason: missing_retained_evidence`（成功的報告不會帶這個欄位），data path 完全不動，報告寫在旁邊的 `.body-hash-report.json`。三個 object collection 都必須存在且為陣列，否則整批 fail closed（不會只驗證 hashed 的那兩類就宣稱成功）；重新推導後若兩個物件落到同一 identity，整批拒絕（它們原本 hash 不同，是被同一次 rehash 合併的）。v1→v3 的 schema ladder 對未宣告 body-hash 語意的物件標 `legacy-unversioned-v1`，不猜是哪一種：本專案同時存在 `sha256(response.content)`（raw bytes）與 `canonical_sha256(payload)` 兩種生產者。`rehash` 不改動已宣告目標語意的物件，但會把 bundle 層級的宣告對齊到物件（沒有 hashed 物件時不動）。

驗證：

```bash
python -X utf8 -m unittest discover -s tests -p 'test_migration_replay.py' -v
python -X utf8 scripts/migration_replay.py self-check
```

這個流程不會替缺少 raw/source snapshot 的歷史資料猜造 evidence，也不會自動把保留的人工 merge/split decision 套進新的 canonical event；需要人工確認的 state 仍須由對應 writer 重新套用。目前 `model_version` 這條軸在本專案的 publication 管線中不存在，`explain-receipt` 會誠實回報 unresolved。
