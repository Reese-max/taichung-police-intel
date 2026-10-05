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

## 治理 schema 2 候選與目前正式查詢

新增 `--governed-candidate` 只編譯非生效候選；不得以此模式覆寫核准快照、歷史核准目錄或 Web 生效 projection。實際 catalog、核准 v1／archive、#39 rights matrix 與五個 active IDs／capabilities 保持原樣。沒有額外的人工來源清單；compiler 從同一 catalog、既有 freshness 規則與 #39 matrix 衍生 original source identity、HTTPS approved origins、geographic scope、temporal semantics、freshness policy，以及 rights／retention／public field refs。未有證據的地理與 valid-time 語意明示 `UNKNOWN`，fetched-at 只代表觀測時間。原有 freshness 門檻不變。

schema 2 的 governance hash 同時綁定所有來源的衍生設定、#39 canonical policy hash 與原檔 SHA、freshness 原檔 SHA，以及 derived-summary 權利／field policy。policy version／parent hash 保留轉換脈絡；重新計算自身 hash 不能代替 catalog／reviewed-input binding。所有 consumers 使用相同 policy／catalog／governance／rights hashes；schema 2 的 feed、status、brief、summary、Query Store、Health 與 UI 必須有相同 binding，混版或缺少 binding 拒絕。

目前核准 v1 缺少這些治理 binding，不能再授權**目前正式 publication query／VERIFIED 資料列**。目前 Query Store、Python HTTP／MCP／stdio 與 Worker 正式 metadata 查詢回傳零筆並明示 `UNKNOWN`／`APPROVED_GOVERNANCE_MISSING`；schema 2 若 rights 是 `UNKNOWN` 或 review 未完成則 `RIGHTS_BLOCKED`。這種零筆不可描述為本地没有事件，也沒有 bounded-no-match 授權。required sources 仍是原五個 IDs；collection coverage、FAILED／PARTIAL／STALE gaps 與 rights admission 分開呈現，來源 filter 不會隱藏 required gap。

目前 Query Store 的建立與讀取都驗證來源 health 的 scalar／enum／時間型別，拒絕把巢狀 payload 帶進 health 或 gaps。讀取 v4 projection 時，每筆 metadata 也重新驗證 active source 與 rights admission、精確 public-field whitelist、封閉且與 canonical hash／ID 一致的 backlink，以及 source-approved HTTPS origin；自行重算 projection hash 不會授權未知來源、私有欄位或替換 backlink。Worker 在 canonical publication 建立邊界採用相同來源 scalar 限制。這些目前使用的限制不套用到明確的原 v3 歷史重播。

目前 API 與 CLI 還會由實際 feed／status／brief 的完整 UTF8 bytes 重建索引並比對完整 projection；只修改允許的標題、重新封裝索引但沿用舊 canonical hashes 也會拒絕。預設使用目前設定的 canonical 檔案；由其他明確發布檔建立的 CLI 索引，查詢時須同時指定 `--feed`、`--status`、`--brief`。API 可使用明確的三檔 raw-byte bundle，但仍完整 parse、hash、policy-bound rederive，不接受 trust boolean 或局部 bundle。Gateway 的私有 snapshot 保存並驗證這些原始 bytes，備援重啟使用同一 captured generation，保留 STALE／no-bounded-zero 語意；HTTP／MCP tool arguments 不能提供 artifacts 或 policy。原先實際 configured Gateway／cache loader 已會重建索引，這個補強處理的是一般 current API／CLI 的 provenance 邊界，不宣稱曾有 live exploit。

來源 health 與 restricted metadata／link collection 仍保留五個 IDs，公開 health 明示各來源 `rights_status`／`review_required`。#39 的可選 UNKNOWN metadata/link 例外不等於 #49 正式 VERIFIED/public-query 授權。event／statistics 與 located-fact 答案需要 active source 的獨立 summary permission、source identity 與 approved origin；domain projection 另遵守 source 的 public-field whitelist。metadata 權利不能授權任意 typed facts。正式 brief 還需要獨立 reviewed derived-summary policy、完整 whitelist、來源／origin binding；Python 與 Worker 都使用封閉巢狀 projection，未知 nested field 或 scalar 型別漂移拒絕。rights/hash PASS 不證明語句的事實正確性或真實模型驗收。

歷史 v1 不適用新的目前正式 admission。`replay` 必須指定核准 archive hash 與固定含時區時間，維持原有完整 118 筆 metadata projection 與原查詢結果，只讀且 `may_answer_current: false`。`tests/fixtures/source-policy/publication-metadata-v3.json` 保存變更前的原始 projection（包含當時的 generation／canonical hashes），用來避免把新的空索引誤當作舊歷史基準。歷史結果不會回寫目前索引。

正向測試使用獨立暫存 checkout、明確標示 `FICTIONAL_OFFLINE_ONLY` 的 rights／summary review 與 fixture promotion receipts，由實際 compiler 產生 schema 2 並實跑 consumers、HTTP／MCP／stdio／Worker；沒有修改本 repository 的核准檔、rights、public data 或啟用來源。這只驗證 source/runtime 邊界；目前 schema 2 尚未生效、真實權利與來源審查尚未補齊，因此不宣稱完整 #49 已驗收。
