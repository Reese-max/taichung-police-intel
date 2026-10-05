# Issue #49：Source Policy 與 Query Coverage 整合收據

`docs/govintel/source-catalog.v2.json` 是唯一人工來源設定；`scripts/source-policy.py` 由 catalog 的 `PRODUCTION_ACTIVE` rows deterministic compile versioned policy。`scripts/verify-source-policy-integration.py --self-check` 會驗證：

`docs/govintel/source-policy.approved.json` 是由 catalog 編譯並納入版本控制的**核准快照**，不是第二份手工來源清單。正常 CLI、Query Store、Health 與 publication validator 會讀這份快照，重建並比對 catalog 衍生的 active set、capabilities 與 hash；缺少快照或比對失敗即停止，不能把修改後的 catalog 當作全新 v1。policy hash 只檢查完整性；信任邊界是受審查的 catalog 與核准快照。

未變更 catalog 時執行 `python -X utf8 scripts/source-policy.py` 會輸出已核准快照。要評估來源狀態轉換，先在隔離候選 checkout 準備 catalog 與 JSON receipt arrays，再以 `--catalog <candidate-catalog> --promotions <promotion-receipts.json> --retirements <retirement-receipts.json> --output <proposed-policy.json>` 產生候選版；沒有新增／移除的 receipt 參數可省略。缺少或多出的 exact receipt 都會被拒絕。候選 policy 與來源審查證據須一併 review 才能更新核准快照；此流程本身不核准來源。

- collector、online collector、publication validator、Query Store、Health 與 Web policy projection 的 active set／policy hash／catalog hash 一致。
- Query Store／Web／MCP response 另外回傳同一 policy hash 下的 `supported_capabilities`、`required_sources`、`covered_sources`、`missing_required_sources`、`collection_completeness`、`coverage_limitations` 與 `can_state_bounded_no_match`；索引結果不再單獨冒充問題覆蓋。
- candidate `S-032` 只有帶 exact promotion receipt 才能產生新 policy version；不會由 policy 自己批准自己。
- 舊 policy 建出的 publication/query projection 可 deterministic replay；新 policy 不回寫舊 generation。
- 混用新舊 policy 的 query artifact fail closed；required source 缺失保留 `PARTIAL` gap；未支援能力回報 `CAPABILITY_NOT_AVAILABLE`。

這是 code-only／fixture integration receipt，不是 source promotion、live canary、部署或公開可達性證據。候選來源仍由 #14/#22/#28 的獨立 fixture/canary/approval 流程控制。

2026-09-22 self-check 實際回報 `consumers=7`：`query_store`、`system_health`、`ui`、`query_gateway`、`collector`、`online_collector`、`publication_validator`；這個數字只代表目前被檢查的 policy bindings，不代表已部署或已啟用候選來源。

## 政策切換後的歷史重播

collector 與 online collector 現在也必須讀取 catalog-bound 核准快照；單改 catalog 或刪除核准快照會停止載入，不會默默啟用新來源。Query Store 驗證 projection hash 之外，也比對 `generated_from.policy_hash`，並從 canonical artifact hashes 與政策 binding 重算 generation，拒絕重新封裝的混版索引。零結果只有在 collection scope 與 query coverage 都通過時才標示可描述本範圍未發現；不支援的能力不會取得這個標記。

`docs/govintel/source-policy-history/<policy_hash>.json` 保存**當時已核准且已 review 的編譯快照**，第一份由目前 v1 核准檔原樣產生。這是受版本控制的歷史輸出，不是另一份人工清單。未來更新核准快照前，先在該核准版本執行 `python scripts/source-policy.py --output docs/govintel/source-policy-history/<該版本 hash>.json` 並納入審查；不得把未核准的候選輸出當作歷史核准檔。archive 的 schema、自身 hash 與檔名必須一致，但 hash 本身不代表授權；信任仍來自 repository 的核准與審查。

一般 `query`、Web 與 MCP 維持只接受目前 catalog-bound policy。歷史 projection 必須明確使用只讀離線命令：

```sh
python scripts/query-store.py replay --store <old-query-store.json> \
  --policy-hash <當時核准政策的 64 位小寫 hash> --as-of <當時含時區的查詢時間>
```

回應外層明示 `APPROVED_HISTORICAL_REPLAY`、`read_only: true`、`may_answer_current: false`；內層保留原查詢的 policy、generation、canonical hashes 與固定時間下的結果。命令不接受任意 policy 檔、不回寫舊 publication／receipt，也不將歷史資料變成目前可回答的資料。未知 hash、變造 archive、混版 projection 或缺少明確時區的時間都會拒絕。

integration self-check 現在另外建立隔離暫存 checkout，用明確標為 fixture 的 promotion receipt 切換至 v2，實際執行七個 consumers，驗證新的完整零筆交通公告快照、required source FAILED/PARTIAL/STALE、filter 無法隱藏 gap、新 generation，以及切換後的原 v1 publication 重播相同結果。UI 使用實際 `validateSourceStatus`，publication 使用實際 bundle validator。此驗證不呼叫 provider、不執行 collector 網路抓取、不更新本 repository 的 catalog、核准政策或 public data，也不證明候選來源的 canary／rights／部署已獲驗收。
