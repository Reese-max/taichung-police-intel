# Issue #22 — candidate publication wiring

本地候選接線已完成最小修正，仍不代表候選來源已升格為 production：

- `online_collect.py` 的 Pages demo path 仍由 source policy 的 `PRODUCTION_ACTIVE` 集合驅動；明確 promotion 後，S-001／S-019／S-032 才會進入同一個 `source-status.json`、feed 與 CSV 產生流程。
- feed projection 保留 `source_name` 與 HTTPS `official_url`，避免新來源進入後只剩裸 `source_id`。
- V2 daily brief、首頁中英文來源名稱與 V2 dashboard source-health 已補上第一批 promotion candidates 的 context。
- `tests/test_candidate_publication_wiring.py` 以升格後的 S-032 合成觀測驗證 status → feed → V2 locator 鏈；它不改動正式 catalog，也不冒充 live canary。

目前仍未完成的外部 gate：

- S-001／S-019／S-032 的連續觀察窗口與 promotion receipt；
- S-001 穩定 live 取得與完整分頁證據；
- #20 protected-main-safe workflow 的合併、實際排程、Pages deploy 與匿名 HTTP/hash 驗證。
