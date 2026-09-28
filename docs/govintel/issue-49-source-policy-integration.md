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
