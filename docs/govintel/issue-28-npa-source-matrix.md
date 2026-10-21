# Issue #28：警政署官方資料矩陣

`npa-source-inventory.v1.json` 是警政署 OPEN DATA／警政統計的單一盤點入口，將來源分成 `PRIMARY_EVENT`、`PRIMARY_REFERENCE`、`ENRICHMENT` 與 `EXCLUDE_OR_AGGREGATE_ONLY`，並保存資料期別、更新語意、欄位、來源條款、重疊關係與接入狀態。

目前 checkout 已完成：

- Batch 1 的集會遊行、重要統計、反詐執行成效與 165 三類資料 fixture adapter；其中 172159 依官方 UTF-8 CSV schema 解析民國年／月、整數指標與來源版本 provenance，仍維持 `FIXTURE_ONLY`。
- `S-036` 集會遊行與 `S-037` 警察機關地址的 catalog 綁定；兩者仍是 candidate，沒有 production collector 或部署宣稱。
- 集會遊行的時間／路線／主管機關 identity 與 semantic-change receipt，並可和臺中交通公告 fixture 融合成一個 `PublicEvent`。
- 重要統計保留 `period`、`table_id`、`value`、官方註記、附件版本與 `attachment_sha256`；週統計、月報、年報與專題資料都標成 reference，不是即時事件。
- 165 舊資料與 `CTX-165` 共用 `NPA-165-FAMILY`，同網域只計一次並保留 superseded 關係。
- A1 CSV／JSON 共用 `NPA-A1-ACCIDENTS`；臺中 `S-034` 與全國 reference 以穩定事故 identity 合併，不新增第二個事件。
- 個別失蹤人口、失竊車／車牌與失物個案使用同一 guard，在事件融合、直接建立、快照 reconciliation 與背景附加入口拒絕；dataset／inventory／source／independent-source／source-key 身分與明示禁止公開旗標均受檢查。reference／enrichment 與禁止即時事件的紀錄只能走需期別的 `BACKGROUND_ONLY` 路徑。
- self-check 實際執行全部六區 fixture 的既有 parser，再將合格集會文件送入既有 PublicEvent fusion；重複文件不重複計為事件。缺少、空白、非陣列或 parser 拒絕的區段記入 `failed_sections`；`parse_failures` 與 `schema_drift` 都以未通過 parser 契約的區段數計算，並非完整官方 schema 差異掃描。`records` 保留輸入列數，`parsed_records` 只計合格區段；部分失敗為 `PARTIAL`，全部失敗為 `FAILED`，self-check 皆以非零碼結束。事件數為實際離線融合候選數，交班未執行所以保持零；這些都不是 live receipt。
- `--live-metadata` 會對 inventory 中有 `dataset_id` 的來源讀取 data.gov.tw metadata，保存 raw hash、dataset identity、resource URL／format／欄位數與逐來源 PASS/PARTIAL/FAILED；不下載 resource bytes。

驗證：

```bash
python -X utf8 scripts/npa-source-inventory.py --self-check
python -X utf8 -m unittest discover -s tests -p "test_npa_source_inventory.py" -v
# live metadata/resource receipt（不寫 canonical；失敗來源不折算為零筆）
python -X utf8 scripts/npa-source-inventory.py --live-metadata --output runtime-evidence/npa-metadata-receipt.json
```

尚未宣稱完成的外部 gate：resource bytes／parser compatibility、production collector、資料庫持久化、公開 UI 寫入、部署與真實使用者收件。metadata 來源故障仍遵循既有 `FAILED/PARTIAL` 不覆蓋 last-known-good 規則。

本輪 TDD 的 NPA 測試從 24 項增至 32 項，連同既有 28 項融合測試共 60 項通過，兩個 self-check 亦通過；evidence 原命令經 Git Bash 重跑為 exit 0。完整 `npm run check` 使用乾淨的隔離 Linux 候選副本，前端 800 項、current-checkout runtime 5/5 與 Next.js build 通過，但新聞 checkpoint 出現三個 2 秒 CLI 逾時與一次觀測時間序錯誤，整體 exit 1；同一組 CLI 逾時已在 admitted base `c629f82` 重現，未擴大此單修補範圍。Windows 的 Unix deadline／檔案 API 限制及既有 PDF parser 版本差異亦未在此單移植。專案未配置獨立 lint、coverage ratchet、CRAP 或 mutation gate，本輪不新增工具鏈；未設定 `TEST_DATABASE_URL` 的 PostgreSQL 整合測試仍屬未驗證。

本次離線驗收對照：

| #28 驗收 | 程式／可重播證據 | 邊界 |
| --- | --- | --- |
| Batch 1–3 角色與接入狀態 | inventory 30 列、validator／catalog 綁定測試 | 保留既有 candidate／fixture 狀態 |
| Batch 1 parser／fixture | 六個區段實際呼叫既有 adapter，缺區與壞列回歸 | 不代表正式原件相容 |
| 集會與臺中交通融合 | 既有共同 named-event identity fixture 融合測試 | 不新增自動身分推論 |
| 重要統計期別／表號／值／註記 | 既有統計 adapter 與附件版本／hash 回歸 | 官方維度與可加性仍待覆核 |
| 165 同族去重與 superseded | 既有 domain dedupe／related source 測試 | 不重標為臺中案件量 |
| 個案不進公開 canonical | guard、fuse、build、reconcile、background 全入口負面測試 | 不啟用個案來源 |
| 臺中／全國同事故不重複 | 既有共用穩定事故 ID fixture 測試 | 無正式原件逐筆對帳宣稱 |
| reference 保留期別且不冒充事件 | 事件入口拒絕 reference／enrichment，背景附加仍強制 period | 不變更公開 UI 或來源准入 |
| 故障不覆蓋 LKG | 既有 partial snapshot／source-ingestion 回歸 | 沒有啟用 NPA production collector |
| canary 非固定成功數字 | 壞時間、缺期別、缺區／錯誤型別與重複文件回歸 | 更新頻率為 inventory 類別；未執行真人交班 |

本次僅修補離線契約與公開投影的程式缺口；issue 歷史紀錄中的來源原件品質、權利、全矩陣資源完整性、production 與真人驗收仍未完成，不以本機測試宣稱整張總單已結案。
