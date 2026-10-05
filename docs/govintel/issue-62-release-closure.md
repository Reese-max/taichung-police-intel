# #62 Production Closure：release binding 與驗收紀錄

本輪實作 release manifest、Worker／Web 版本核對與公開驗證器。#62 仍未完成：正式部署、長期來源觀測、PR supersession audit、human holdout、真人任務與真人瀏覽器驗收均未在本輪執行。下列「已實作（local）」只表示程式與本地驗證路徑存在，不代表 production 通過。

## Release contract

`scripts/build-release-manifest.mjs` 重用 `workers/query-gateway/src/index.js` 的 `buildSnapshot()` 與 `createReleaseManifest()`，在 Pages static build 後讀取 `apps/web/out/data`，產生 `data/release.json`。它不建立第二份 source policy、query store 或 canonical publication。

Manifest 包含 `release_id`、`code_sha`、`publication_generation`、`publication_hash`、feed/status/brief 的 `artifact_hashes`、`source_policy_hash`、`query_generation`、`evidence_catalog_hash`、`built_at`，並保留 `worker_version`、`pages_deployment`、`deployed_at`、`anonymous_http_verified_at`。建置階段的後四欄為 `null`。

Worker 以 `CF_VERSION_METADATA.tag` 比對 code SHA，以 `CF_VERSION_METADATA.id` 保存實際 Worker 版本。將 tag 設為 Git SHA 是本專案部署流程的約定；Cloudflare binding 提供 version ID、tag 與版本建立時間，詳見 [Cloudflare version metadata](https://developers.cloudflare.com/workers/runtime-apis/bindings/version-metadata/)。版本建立時間不冒充 Pages 部署完成時間。

Worker 重新核對 manifest 與 canonical artifacts 的綁定；manifest 缺失、code／generation／policy／query／evidence hash 不一致，或客戶端 `release_id` 不符時，回傳 HTTP 503 `QUERY_TEMPORARILY_UNAVAILABLE`。靜態 publication 仍保留。Evidence catalog hash 以 publication time 固定；查詢 freshness 仍用伺服器當下時間，舊資料不會因固定 hash 變成新資料。

`release.evidence_catalog_hash` 綁定固定發布版本；`answer_evidence_receipt.evidence_catalog_hash` 則雜湊該次 gate 實際使用的 catalog，包含當下 `is_current`。同一 release 過期後，後者會改變，仍可重播回答當時的證據狀態。

Web 的 `query-release-client.js` 對外部 Gateway 先以 `cache: "no-store"` 讀取 Pages manifest，核對 UI build 的 `NEXT_PUBLIC_RELEASE_CODE_SHA` 與 Dashboard 目前顯示的 publication generation，再送出 `release_id`。回應的 release 不符時不呈現答案。Worker origin 可直接設定為 Gateway URL，client 會補上 `/query`；relative `/query` 與 localhost／loopback 的 Python 開發路徑保留既有行為，不要求 production manifest。

| Evidence level | 證據範圍 | `production_verified` |
|---|---|---|
| `BUILD_ONLY` | 本地／CI 建置完成的 manifest | `false` |
| `RUNTIME_BOUND` | Worker 已核對 canonical publication，附實際 Worker version | `false` |
| `LOCAL_TEST` | verifier 使用注入的測試 client，即使輸入部署 metadata 也仍屬本地 | `false` |
| `ANONYMOUS_HTTP_ONLY` | 實際匿名 HTTP 核對通過，但缺正式部署 metadata | `false` |
| `DEPLOYMENT_BOUND_RIGHTS_BLOCKED` | 實際匿名 HTTP 與部署 metadata 綁定通過，但 formal admission 仍為 UNKNOWN，沒有可公開的正式 evidence | `false` |
| `PRODUCTION` | 實際匿名 HTTP、Pages workflow attempt、部署完成後觀測時間與預期 code SHA 均齊備並通過核對 | `true` |
| `VERIFICATION_FAILED` | 公開驗證失敗，覆寫同一 output 的舊 PASS receipt | `false` |

`PRODUCTION` 只證明該次 release 的公開 binding，不表示所有來源新鮮、真人可完成任務或整張 #62 已完成。

## 建置與部署驗證

本地重播（Git Bash）：

```bash
npm run check
node scripts/build-release-manifest.mjs --data-dir apps/web/out/data --code-sha "$(git rev-parse HEAD)"
node --test apps/web/tests/release-manifest.test.mjs apps/web/tests/query-release-client.test.mjs apps/web/tests/query-gateway-worker.test.mjs
python -X utf8 -B -m unittest discover -s tests -p 'test_release_verification.py'
```

本地 manifest 只記錄提供的 SHA；正式驗收須使用受審 commit 的 CI artifact。最終測試命令、結束碼與數量以本輪 `.issue-loop-evidence.json` 為準，此文件不預填尚未完成的 full-suite 結果。

正式操作由部署操作者執行既有 workflow：

1. `.github/workflows/query-gateway-workers.yml` 在 `main` 部署 Worker，將 `github.sha` 寫入 version tag；手動 dispatch 也限制在 `main`。
2. `.github/workflows/pages.yml` 建置 UI 時注入相同 code SHA 與 `QUERY_GATEWAY_URL`，產生 manifest 後才上傳 Pages artifact；完成 Pages deploy 後記錄 `DEPLOYED_AT`。
3. Pages workflow 執行下列 verifier，保留 `runtime-evidence/query-gateway-receipt.json`。`PAGES_DEPLOYMENT` 是實際 workflow run attempt URL；`DEPLOYED_AT` 是 deploy step 完成後的觀測時間；`GITHUB_SHA` 必須對應本次建置，不能以手填成功值補齊缺失證據。

```bash
python -X utf8 scripts/verify-query-gateway-production.py \
  --gateway-url "$QUERY_GATEWAY_URL" \
  --public-base-url "$PUBLICATION_BASE_URL" \
  --pages-deployment "$PAGES_DEPLOYMENT" \
  --deployed-at "$DEPLOYED_AT" \
  --expected-code-sha "$GITHUB_SHA" \
  --output runtime-evidence/query-gateway-receipt.json
```

Verifier 核對匿名 Pages manifest／artifact bytes、Worker `/health`、`/capabilities`、`/query` 及 `/mcp`。governed policy 分支必須與 verifier 所在 repository 的核准 snapshot 完全相同，且 formal admission 為 ADMITTED；此分支仍要求一筆 query 結果的官方 HTTPS `.gov.tw` locator 與 bound feed 一致；legacy policy 1 分支只接受與 repo 核准 snapshot 完全綁定的 UNKNOWN／APPROVED_GOVERNANCE_MISSING 拒絕結果。它不代替真人打開官方頁、確認內容、操作 Web 或 mobile 的驗收。

`deployment_verified` 是新增的部署完整性欄位：只有實際匿名 HTTP、既有 Pages workflow attempt、部署時間、預期 code SHA 與所有 release/hash/query/MCP 核對通過才為 `true`。CLI 以此欄位決定 operational exit code，成功標記為 `DEPLOYMENT_QUERY_RECEIPT_OK`。注入 client 不會取得此欄位或 production 驗證。

權限拒絕分支必須保留所有 active source identities、完整 per-source blockers、health/gaps、HTTP/MCP 一致的正式零結果、false bounded-no-match，以及 brief/answer 的明確 `RIGHTS_BLOCKED` 拒絕。一般空陣列、錯誤的 blocker/reason、混用 generation、遺失 source health、錯誤 HTTP failure 或帶有 evidence 的權限拒絕均失敗；其他尚未建立 oracle 的 empty/policy 分支也失敗。通過部署檢查時 `query_readiness=RIGHTS_BLOCKED`、`production_verified=false`、official locator 欄位為 `null`，不得升格來源權利或把零結果說成沒有事件。

驗證器會用同一 repo 的實際 Query Store builder、coverage 與 scope 函式，從公開的 feed/status/brief 原始 bytes 和核准 policy 重新計算 query generation、missing/stale sources、collection coverage、source gaps 和 freshness；回應之間互相一致不能代替 canonical 核對。查詢的 server clock 必須落在這次 HTTP 驗證區間內（最多五秒 clock skew），且與 receipt issued time 相符；scope 與 envelope 記錄間僅允許一秒邊界差異。`/health` 沒有 server timestamp，使用該次請求的前後觀測時間核對；不改寫原始資料時間。MCP 成功回應必須只有一段 JSON object 文字，並與 structuredContent 完全一致（包括型別且禁止 non-finite values）；文字藏有額外 evidence 或缺口不符均失敗。

Pages 保留完整 query receipt，並只傳遞 readiness/production 欄位到 publication outcome。`QUERY_VERIFY=success` 表示 operational binding 通過；遇到 `RIGHTS_BLOCKED`，runtime health 另外保留 `formal_query_admission=UNKNOWN`，query lane 不會變成 HEALTHY，摘要仍提示權限與資料就緒缺口。這些欄位不代表整張 #49 或 #62 完成，也不啟用 proposed policy 2。

Production verifier 拒絕 loopback／private IP 與 `.local`／`.localhost` Gateway，部署 URL 必須指向本 repo 的 workflow attempt，並要求 `built_at <= deployed_at <= 驗證時間`。這些是來源與格式檢查，不是獨立的部署簽章。

Pages 與 Worker workflow 都在每次 `main` push 執行，避免路徑篩選造成只有單端 SHA 前進；兩者仍獨立部署，過渡期間可能回傳 503，直到版本一致。應保留失敗 receipt 並核對兩端實際版本，不移除 binding 或將 fail-closed 狀態改成成功。此輪未演練正式 rollout／rollback。

## Canonical 維護路徑

| 能力 | 現行 owner path／驗證入口 |
|---|---|
| Worker release 與公開 smoke | `workers/query-gateway/src/index.js`、`scripts/build-release-manifest.mjs`、`scripts/verify-query-gateway-production.py`；`apps/web/tests/release-manifest.test.mjs`、`tests/test_release_verification.py` |
| Web query／release 核對 | `apps/web/components/QueryGatewayPanel.js`、`V2DailyDashboard.js`、`apps/web/lib/query-release-client.js`；`apps/web/tests/query-release-client.test.mjs` |
| Canonical publication／checkpoint | `online_collect.py`、`scripts/publication-state-branch.py`、`.github/workflows/pages.yml` |
| Source policy／freshness | `scripts/source-policy.py`、`docs/govintel/source-catalog.v2.json`、`collect.py` 的 `SOURCE_FRESHNESS_POLICY`／`freshness_status()`；`tests/test_source_policy.py`、`tests/test_source_ingestion.py` |
| Candidate canary | `scripts/candidate-runtime-canary.py`、`scripts/verify-candidate-observation-window.py`；`tests/test_candidate_runtime_canary.py`、`tests/test_candidate_observation_window.py` |
| Query Store／本地 Gateway | `scripts/query-store.py`、`scripts/query-gateway.py`、`scripts/query-gateway-stdio.py`；不以本輪新增另一套 store |
| Gold evaluator／真人計畫 | `scripts/evaluate-govintel.py`、`eval/gold/v1/manifest.json`、`tests/test_gold_evaluator.py`、`docs/govintel/competition-2026/evaluation-manifest.template.json` |
| Operator health | `scripts/system-health.py`、`apps/web/components/V2DailyDashboard.js`；`tests/test_system_health.py`、`apps/web/tests/system-health.test.mjs` |

此路徑表不是 open PR inventory，也未判定 #40–#55 是否已被 main 吸收；關閉重複 PR 前仍須逐份核對 diff 與測試。

## Acceptance mapping

| AC | 狀態 | 本輪證據／仍缺的驗收 |
|---|---|---|
| P0-1：PR #61 正式部署 | 未完成 | 已改部署 wiring，未執行正式部署。 |
| P0-1：匿名 HTTP 可驗 Pages／Worker | 部分 | verifier 已實作；未保存本輪 live HTTP receipt。 |
| P0-1：四個 endpoint production smoke | 部分 | `/health`、`/capabilities`、`/query`、`/mcp` 均有 verifier 與本地測試；production 未跑。 |
| P0-1：UI／API 回到官方 evidence URL | 部分 | release client 與 locator binding 已實作；未做正式 UI／官方頁回查。 |
| P0-1：manifest 檢出 mixed release | 已實作（local） | code、publication、policy、query、evidence 與 client release 不符會 fail closed。 |
| P0-1：production／local evidence 分開 | 已實作（local） | 上述 evidence levels 與 `production_verified`；注入 client 不升格 production。 |
| P0-2：active source 明確 freshness policy | 部分 | 五個 active source 已有各自 threshold；完整 policy 欄位與下列無日期缺口未修。 |
| P0-2：7-day machine-readable canary | 未完成 | 有連續 observation validator，但本輪無七日 live receipt。 |
| P0-2：穩定後才擴來源 | 未完成 | 本輪未擴來源，也未取得長期穩定證據。 |
| P0-2：source health 與 world/query coverage 分離 | 已實作（local） | 沿用 source policy、coverage 與 health contract；endpoint PASS 不等於 world coverage。 |
| P0-3：open PR inventory | 未完成 | 未查核本輪 GitHub open PR 清單。 |
| P0-3：每張舊 PR 四態結論 | 未完成 | 尚無 MERGE／REBASE／SUPERSEDED／ARCHIVE audit receipt。 |
| P0-3：關閉前確認 main 功能與測試 | 未完成 | 未執行逐 PR diff audit 或關閉任何 PR。 |
| P0-3：docs 不指向 obsolete implementation | 部分 | README 修正 #20 已有 checkpoint 路徑；全 repo 歷史文件與 PR 指向尚待審核。 |
| P1-1：主要 UI 接 production Query Gateway | 部分 | Pages build 注入 Gateway／code SHA，UI 已做 release 核對；正式部署與主流程驗收未完成。 |
| P1-1：unsupported capability 不回假零 | 已實作（local） | 沿用 `CAPABILITY_NOT_AVAILABLE`；未提供的 domain capability 不偽裝可查。 |
| P1-1：首頁到 evidence locator 合理步驟 | 部分 | 既有查詢結果與 evidence drawer 可回查；未以真人／瀏覽器量測步驟。 |
| P1-1：使用者理解 stale／partial／conflict／unknown | 部分 | 既有狀態提示保留；未量測真人理解程度。 |
| P1-1：mobile accessibility／responsive | 部分 | 本輪未做真人或實際 mobile browser 驗收。 |
| P1-2：human-labeled holdout lane | 部分 | evaluator 可讀替代 manifest、非 synthetic case 要求 reviewer；完整 holdout split／label provenance 與真人資料未交付。 |
| P1-2：第一輪真人 task evaluation | 未完成 | 無參與者任務、time-to-evidence 或實測回饋。 |
| P1-2：README／competition 只呈現實測 | 部分 | 保留 `NOT_RUN`／`UNVERIFIED`，不把 synthetic test count 當真人有效性；未取得新實測。 |
| P1-2：可重播 evaluation receipt | 未完成 | 既有 synthetic evaluator 不替代本輪 human holdout／task receipt。 |
| P1-3：定位未更新的 source／各 stage | 部分 | 既有 collection／validation／deployment／HTTP／query lanes；完整 source-specific parse／verify／publish 鏈仍待補齊。 |
| P1-3：可採取行動的 next action | 部分 | 既有 generic operator summary；source-specific 原因與 next action 未做。 |
| P1-3：Query failure 不改寫 canonical publication | 已實作（local） | 既有 health lanes 分離；本輪 release mismatch 只阻擋 Query。 |
| P1-3：Actions 綠燈不等於 production healthy | 已實作（local） | 缺部署／公開證據維持 `UNKNOWN`／未驗證；build receipt 不升格 production。 |
| P2：README、docs 分層及自動 current status | 部分 | 新增 release runbook 與 machine-readable manifest；README 大幅收斂、archive 搬移與自動 status 尚未做。 |

Freshness 未處理的 code gap：`collect.py` 已有 per-source threshold，仍缺完整 cadence／completeness／age metadata；`online_collect.py` 對部分無官方日期來源以 fetch time 填入 `data_as_of`，並可能標成 `COMPLETE_ZERO`。這不能當作官方新鮮度或沒有新事件的證據。既有 candidate observation validator 也尚未產出完整 collection success ratio、freshness age、partial/stale ratio 與 publish latency。這些應延續既有 collector／policy／canary 模組修正。

## Done Definition（九項）

| 條件 | 狀態 |
|---|---|
| Pages＋Worker＋Query＋Evidence 匿名可驗閉環 | 部分：本地實作／verifier 已有，正式部署與匿名 receipt 未驗證。 |
| Manifest 防 mixed generation／policy／query | 已實作（local）：仍待正式 rollout 驗證。 |
| 主要來源至少七日 canary＋per-source policy | 部分：threshold 已有，完整 policy 與七日證據未完成。 |
| 舊 PR／Issue supersession audit、active path 清楚 | 部分：canonical path 已列，外部 inventory／diff audit 未完成。 |
| UI 主要路徑使用真實 production query | 部分：release wiring 已有，正式 UI／主流程驗收未完成。 |
| 至少一份 human-labeled holdout evaluation | 未完成。 |
| 至少一輪真人 task evaluation／time-to-evidence | 未完成。 |
| Operator 能快速回答未更新原因 | 部分：既有 health summary，完整 source 原因與 next action 未完成。 |
| README／status 與 production evidence 一致 | 部分：本頁保留缺口，未產生本輪正式 release receipt／自動 current status。 |

本輪未推送、未開 PR、未部署、未關閉 issue／PR，未執行 7／30 日 canary、真人標註或真人任務；未完成項保留為 #62 closure gate。
