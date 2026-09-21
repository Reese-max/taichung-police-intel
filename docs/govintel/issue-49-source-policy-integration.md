# Issue #49：Source Policy 與 Query Coverage 整合收據

`docs/govintel/source-catalog.v2.json` 是唯一人工來源設定；`scripts/source-policy.py` 由 catalog 的 `PRODUCTION_ACTIVE` rows deterministic compile versioned policy。`scripts/verify-source-policy-integration.py --self-check` 會驗證：

- collector、online collector、publication validator、Query Store、Health 與 Web policy projection 的 active set／policy hash／catalog hash 一致。
- Query Store／Web／MCP response 另外回傳同一 policy hash 下的 `supported_capabilities`、`required_sources`、`covered_sources`、`missing_required_sources`、`collection_completeness`、`coverage_limitations` 與 `can_state_bounded_no_match`；索引結果不再單獨冒充問題覆蓋。
- candidate `S-032` 只有帶 exact promotion receipt 才能產生新 policy version；不會由 policy 自己批准自己。
- 舊 policy 建出的 publication/query projection 可 deterministic replay；新 policy 不回寫舊 generation。
- 混用新舊 policy 的 query artifact fail closed；required source 缺失保留 `PARTIAL` gap；未支援能力回報 `CAPABILITY_NOT_AVAILABLE`。

這是 code-only／fixture integration receipt，不是 source promotion、live canary、部署或公開可達性證據。候選來源仍由 #14/#22/#28 的獨立 fixture/canary/approval 流程控制。

2026-09-22 self-check 實際回報 `consumers=6`：`query_store`、`system_health`、`ui`、`collector`、`online_collector`、`publication_validator`；這個數字只代表目前被檢查的 policy bindings，不代表已部署或已啟用候選來源。
