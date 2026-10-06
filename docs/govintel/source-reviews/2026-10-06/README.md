# 2026-10-06 來源審查與實際觀察補充

本目錄為**非生效審查材料**。`source-review.pending.json` 的人工角色、時間與決策保持空白；正式 retention policy、approved source policy 與五個 active IDs 沒有變更。取得條款不等於已審完個資、第三方內容、附件或模型傳輸。

交通局與消防局原始條款 HTTP 200，正文指向政府資料開放授權條款第 1 版；市府原始宣告 HTTP 200，允許著作權範圍內的重製與改作並要求出處。本次保存 requested/final URL、實際抓取時間、原始 bytes hash 與正文定位。原始 HTML 私有保存，不在公開 repo 重刊。市府宣告未明示 OGDL 版本，不能替它補上版本。

新增 `retention-rights-policy.v1.json` 的可選 `source_reviews` 機制：每筆需真實人工角色、時間、明確 `APPROVE_METADATA_ONLY` 決策、200 原始條款 capture、來源與 catalog 精確綁定、逐欄位子集合、例外核對與出處。每筆參與 policy hash；未審同類來源保持原政策。不得藉此授權全文、摘要、模型傳輸、新網域或放寬保留期限。待審模板不能直接當核准記錄使用。

## 七日資料

核對 30 份原始 GitHub Actions ZIP，archive digest 全部吻合。`candidate-history-verified-ledger.json` 區分 archive SHA-256、原始 JSON SHA-256 與來源 manifest；window receipt 的 `report_sha256` 是 canonical JSON hash。9/30–10/6 每個臺北日選最新 workflow attempt，選擇在查閱來源結果之前完成；10/5 另有較早手動流程，不取代已選失敗紀錄。

| 來源 | 連線成功日 | 有效觀察日 | 尚缺條件 |
| --- | ---: | ---: | --- |
| S-001 | 0/7 | 0/7 | 連線、完整性、權利 |
| S-019 | 0/2 | 0/2 | 先前五日未排程、連線、權利 |
| S-031 | 7/7 | 1/7 | 前六日無告知／保留類別；即時快照為 PARTIAL |
| S-032 | 7/7 | 7/7 | 最近兩日 PARTIAL；權利與獨立完整性 |
| S-033 | 7/7 | 7/7 | 權利與獨立完整性 |

七個有效**觀察日**不等於正式來源資格。`promotion_eligible` 與 `coverage_independently_verified` 全部保持 false；完整 promotion plan 的驗證仍 BLOCKED。驗證器新增未來時間、逐來源／報表時鐘一致性檢查，並分列連線成功日、有效日、PARTIAL 日與缺告知日。

## 來源連線與日期

`official-source-access.json` 只代表本次 runtime 路徑：WWW 研考會與警察局連線失敗；不能宣稱機關全球停站。替代官方頁面未取得原始 bytes；索引快取與其他來源也不能代替 S-029 的專案報告。保留 LKG 內容與原始日期、標明 FAILED，未把重抓時間填成發布日期。

同日較早的原始日期核對保存在 `verification.json`：S-006 的 PDF 修訂日期不能當成首次發布日；S-007 的有限 API 觀察不能當成全庫最新日期。S-009 的列表與詳細 API 沒有官方發布日期，108 筆是清單存量，不是每日新增量；未知日期保持 null。

獨立 `source-egress-probe.yml` 只在 main 手動執行：建立短效秘密保護的臨時 Worker，最多讀三個固定 WWW 官方入口，每次 15 秒／2 MiB；回傳運輸 metadata/hash，不公開原始內容、不接收任意 URL、不跟隨重新導向。helper 在 finally 刪除自身臨時 Worker，失敗也記錄 cleanup；驗證未執行前不得宣稱新路徑修復了來源。現有正式 Worker 不會被此探測修改。

`source-egress-probe-actual.json` 記錄實際流程 37410450212：S-029、S-001、S-019 都在 15 秒逾時；臨時 Worker 已刪除。流程成功只表示探測與清理完成。`official-shared-cms-probes.json` 的市府同路徑入口均重新導向 `/404.html`，不能當作相同來源的鏡像或「沒有資料」。

發布用合約探測另外限制單次 connect/read 為 5/15 秒、每個來源整體 60 秒，移除 adapter 與外層疊加重試。逾時來源明確記為 `LIVE_SOURCE_DEADLINE_EXCEEDED`、`SOURCE_UNAVAILABLE`、`PARTIAL`；保留 last-known-good，繼續檢查其他來源。外層 12 分鐘保護仍會留下 interrupted receipt 並使流程失敗。此預算不修改正式蒐集器、来源准入或日期含義。
