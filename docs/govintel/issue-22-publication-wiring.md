# Issue #22 — candidate publication wiring

本地候選接線已完成最小修正，仍不代表候選來源已升格為 production：

- `online_collect.py` 的 Pages demo path 仍由 source policy 的 `PRODUCTION_ACTIVE` 集合驅動；明確 promotion 後，S-001／S-019／S-032 才會進入同一個 `source-status.json`、feed 與 CSV 產生流程。
- feed projection 保留 `source_name` 與 HTTPS `official_url`，避免新來源進入後只剩裸 `source_id`。
- V2 daily brief、首頁中英文來源名稱與 V2 dashboard source-health 已補上第一批 promotion candidates 的 context。
- `tests/test_candidate_publication_wiring.py` 以升格後的 S-032 合成觀測驗證 status → feed → V2 locator 鏈；它不改動正式 catalog，也不冒充 live canary。

2026-09-21 11:35:12 Asia/Taipei 的 bounded live canary 修正並驗證了 S-032 的來源路徑：`https://www.traffic.taichung.gov.tw/news/index.asp?Parser=9,4,20`，HTTP 200、9 筆列表、窗口內 4 筆、1 篇 detail、`NO_DRIFT`，manifest `d24d5b467f510b019e1f08627faa1ab715f4e081e7e627375740578e6dde0014`。原設定誤指向長者專區，已改為最新消息項目的 `index-1.asp` 連結；本觀測仍明確標記 `CANDIDATE`，不是 promotion receipt。

2026-09-21 15:34:50 Asia/Taipei 的第二次 bounded live canary（`candidate-runtime-canary.py`，`failed_count=0`）觀察到三個候選來源均 HTTP 200、`NO_DRIFT`、`COMPLETE_WITH_ITEMS`：S-001 15 筆列表／窗口 3 筆／2 snapshots，S-019 63 筆／窗口 1 筆／2 snapshots，S-032 10 筆／窗口 5 筆／2 snapshots。三者仍為 `integration_status=CANDIDATE`、`promotion_eligible=false`、`coverage_independently_verified=false`；manifest 分別為 `ecaa4864fc03677128e5f34c66abf72cebe4fa32e094380dc3e6d79d7ec85181`、`bcdbe2e2225b0ce88c8d33153c83d24fe2518747a1df2d3a5f978ff6419cd557`、`ff3690b99c1617173299002675588abf10e3157710b270c59eae6edefe4f505d`。這是單次觀測，不是七日 promotion 或正式發布證據。

2026-09-22 00:49:52 Asia/Taipei 的第三次 bounded live canary（`candidate-runtime-canary.py`，`failed_count=0`）觀察到五個候選來源皆 HTTP 200、`NO_DRIFT`：S-001 15 筆列表／窗口 2 筆／2 snapshots，S-019 63 筆／窗口 1 筆／2 snapshots，S-031 10 筆／窗口未提供（`PARTIAL`）／1 snapshot，S-032 10 筆／窗口 4 筆／2 snapshots，S-033 59 筆／窗口 2 筆／2 snapshots。五者仍為 `integration_status=CANDIDATE`、`promotion_eligible=false`、`coverage_independently_verified=false`；S-031 的 `PARTIAL` 視窗不能被解讀成完整覆蓋。manifest 分別為 `ecaa4864fc03677128e5f34c66abf72cebe4fa32e094380dc3e6d79d7ec85181`、`bcdbe2e2225b0ce88c8d33153c83d24fe2518747a1df2d3a5f978ff6419cd557`、`5b328c3a1e54ef84ee2fcb742cab42094e235201a5f737c7a0c1fb48112a7ecb`、`ff3690b99c1617173299002675588abf10e3157710b270c59eae6edefe4f505d`、`33dd072a1b6c1aa38853d4af425e6228ff4d42d02caa376f2039805a7b84b08d`。這仍是單次觀測，不是七日 promotion 或正式發布證據。

2026-09-22 05:59:28 Asia/Taipei 的第四次 bounded live canary（修正 `BoundedSession` 與 collector 的 `allow_redirects` 參數衝突後，`failed_count=0`）觀察到五個候選來源皆 HTTP 200、`NO_DRIFT`：S-001 15 筆列表／窗口 2 筆／2 snapshots，S-019 63 筆／窗口 1 筆／2 snapshots，S-031 6 筆／窗口未提供（`PARTIAL`）／1 snapshot，S-032 10 筆／窗口 4 筆／2 snapshots，S-033 59 筆／窗口 2 筆／2 snapshots。五者仍為 `integration_status=CANDIDATE`、`promotion_eligible=false`、`coverage_independently_verified=false`；S-031 的 `PARTIAL` 視窗仍不能解讀成完整覆蓋。manifest 分別為 `ecaa4864fc03677128e5f34c66abf72cebe4fa32e094380dc3e6d79d7ec85181`、`bcdbe2e2225b0ce88c8d33153c83d24fe2518747a1df2d3a5f978ff6419cd557`、`15b9d75693e10efc19da5a072a7bb2ad831e2d765c98654d09b287f47e40891e`、`ff3690b99c1617173299002675588abf10e3157710b270c59eae6edefe4f505d`、`33dd072a1b6c1aa38853d4af425e6228ff4d42d02caa376f2039805a7b84b08d`。這仍是單次觀測，不是七日 promotion 或正式發布證據。

目前仍未完成的外部 gate：

- S-001／S-019／S-031／S-032／S-033 的連續觀察窗口與 promotion receipt；
- S-001 穩定 live 取得與完整分頁證據；
- #20 protected-main-safe workflow 的合併、實際排程、Pages deploy 與匿名 HTTP/hash 驗證。
