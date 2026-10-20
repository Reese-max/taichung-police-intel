# Issue #24 — conservative PublicEvent fusion

`scripts/public-event-fusion.py` 提供 deterministic-first 融合與人工決策重播；首頁使用明確標示 `FIXTURE_ONLY` 的合成事件示範，不宣稱真實官方事件或 production 驗收。

- 只有明確 `authority=official`、HTTPS URL 的 normalized document 可進入融合；`public_event_id` 獨立於 `document_id/document_version_id`，不改變文件 `ChangeEvent.event_id` 的語意。
- 穩定 named/cross-reference ID 加日期才自動聚類；缺身分或日期保持 `CANDIDATE`，同名異日不直接合併。同角色時間／行政區／狀態矛盾為 `CONFLICT`；等價時區值與不同 `fact_scope` 不製造假衝突。
- 更新保留原文件 identity 與前後 version links；partial snapshot 以 `PARTIAL_LKG` 保留最後已知事件，不推論取消。每份 link 保存來源、HTTPS URL、locator 與 `acquisition_paths`。
- `source_provenance` 依 `independent_source_id` 聚合取得路徑與版本；同一資料的 direct + Twinkle 不增加獨立佐證數。未宣告途徑保留 `UNKNOWN_UNDECLARED`，不由官方身分猜測取得方式。
- 傳入 `entity_registry` 時，agency/location labels 只接受 exact resolve，事件保留 registry version/hash。有明確 `occurrence_id` 時日期變更維持事件 ID；`RESCHEDULES` 還需官方文件／版本、locator、理由、人工 `CONFIRMED` review 與相同 registry receipt，`PENDING_REVIEW` 不自動合併。

## 重播 CLI 與人工決策

在 repo root 執行下列命令，可由 domain API 重建 checked-in UI snapshot：

```bash
python -X utf8 scripts/public-event-fusion.py --demo --output apps/web/public/data/public-event-demo.json
python -X utf8 scripts/public-event-fusion.py --self-check
```

`--demo` 產生三個官方來源角色的合成文件、前一個已確認事件、其中一份更新後的衝突、候選，以及經 `split_public_event`／`replay_manual_actions` 產生的 split/merge 預覽與 ledger。它使用 fixture URL／locator，不是實際官方文件證據。UI 的 confirm／merge／split 僅切換本機預覽，不寫入 canonical state；下載連結提供示範文件、版本與人工決策紀錄。

自備 normalized documents 時，使用不同的輸入與輸出路徑並保留原始輸入：

```bash
python -X utf8 scripts/public-event-fusion.py --input input.json --output replay.json
```

輸入至少包含 `documents`；以下為可接受的欄位結構：

```json
{
  "documents": [],
  "previous_events": [],
  "snapshot_complete": true,
  "manual_actions": []
}
```

`previous_events` 可省略；提供時必須同時提供明確 boolean `snapshot_complete`。先由 `reconcile_public_events` 處理完整／partial snapshot 與文件版本，再依 `manual_actions` 的陣列順序重播；無前一 snapshot 時由 `fuse_documents` 開始。每筆 action 必須恰含 `{action, operator, at, payload}`，`at` 使用含時區的 ISO timestamp：

| action | payload |
| --- | --- |
| `CONFIRM` | `{"public_event_id": "待確認的 active ID"}`，事件須為 `CANDIDATE` |
| `MERGE` | `{"merged_event_ids": ["active ID 1", "active ID 2"]}` |
| `SPLIT` | `{"public_event_id": "active ID", "groups": [["version ID 1"], ["version ID 2"]]}` |

`previous_events` 必須是人工決策重播前的自動融合 snapshot；重新建置時要明確提供完整 ledger，不把上次已決策的 `public_events` 當成自動 snapshot，也不只補送增量 actions。split groups 必須以非空組完整分割目前全部 linked version IDs；新證據使舊 partition 不符時拒絕重播，不自動把舊決策套用到新版本。不存在、重複或已退休的 target ID 也會拒絕。

輸出包含 `schema_version`、active `public_events`、每次操作前的 `archived_events` 與正規化 `manual_actions`。原事件、舊 IDs 與文件 links 均可回查；archive 是操作前 snapshot，同一 ID 可以有多筆。重播不修改傳入的事件與 ledger；merge 合併各事件的 manual history，split 子事件保留既有 history 並複製 evidence links，避免修改子事件時連帶修改原始證據。

## 一條固定期別背景資料路徑

`populationBackgroundForEvent()` 沿用 `validatePopulationSnapshot()` 核對既有 `population-112Y12M.json` 的固定 JSON hash、原始 CSV hash、行政區與合計，只支援前一個 `CONFIRMED` 事件的精確對應 `location:tc-xitun → 66000060`。目前 `CONFLICT` 的事件不能藉此取得已確認背景。

背景卡顯示 SEGIS／內政部統計處的固定歷史樣本：`112Y12M`（2023 年 12 月），西屯區 `235441` 人。保留官方查詢 URL 的 `COL` 值作為 `dataset_id`、原始 CSV filename 作為 `resource_id`，以及 `source_id=S-035`、期別、值、單位、`source_url` 與完整 `source_receipt`；卡片可展開資料集／資源 ID 與 CSV SHA256。此資料保留 `BACKGROUND_ONLY`、`DIRECT_OFFICIAL` 與歷史樣本說明，不表示即時人潮、出席人數或活動影響；`production_active=false`，沒有升格為正式定期來源。其他／不明行政區及期別或 hash 不符均拒絕。

## 九個驗收情境與證據

以下分母為固定的 9 個合成驗收情境；測試中的 false/missed merge、conflict 與 traceability assertions 是回歸檢查，不是人工標註 heldout accuracy，也不宣稱準確率。

| Issue 驗收 fixture | 機制與相關測試 |
| --- | --- |
| ✅ 同活動三來源合併，保留三份文件 identity | `test_three_official_sources_merge_into_one_confirmed_event`；demo 展開三份前後版本 |
| ✅ 同名不同日期不誤合併 | `test_same_named_event_on_different_dates_does_not_merge`；日期參與自動聚類 key |
| ✅ 同日但地點矛盾不自動確認 | `test_same_identity_with_district_conflict_stays_conflict`；`district_id=null` 並保留 conflict |
| ✅ 一來源更新，其餘文件版本不被改寫 | `test_complete_snapshot_preserves_prior_document_versions_when_one_source_updates`；CLI reconcile 及 demo 保留四個 version links |
| ✅ 來源消失但 snapshot partial 不取消 | `test_partial_snapshot_carries_forward_last_known_good_event`；`PARTIAL_LKG` |
| ✅ 官方取消與未更新官方來源產生衝突 | `test_official_cancellation_conflicts_with_an_unupdated_official_source`；`conflict_fields=["status"]` |
| ✅ 同資料 direct + Twinkle 雙路徑不增加獨立佐證 | `test_same_independent_source_keeps_acquisition_paths_in_provenance`；一個 independent source、兩條 paths |
| ✅ 背景具明確期別且不冒充即時影響 | Web `confirmed fixture geography selects only verified historical D1 background`，另有既有人口樣本的期別／hash 防護測試 |
| ✅ manual split/merge 可重播且保留原文件／舊 IDs | `test_manual_actions_replay_with_archived_ids_and_complete_history`；另測空組、輸入 alias、失效 target／partition 與 checked-in demo 重建一致性 |

定向驗證命令：

```bash
python -X utf8 -B -m unittest discover -s tests -p 'test_public_event_fusion.py' -v
node --test apps/web/tests/public-event-fusion.test.mjs apps/web/tests/public-event-demo.test.mjs apps/web/tests/population-background.test.mjs
```

Python 基準 28 tests，本次新增 5 項後為 33 tests 通過；PublicEvent Web 基準 4 項，新增背景 bridge 後為 5 項，加上既有人口測試 2 項，共 7 項通過。Node 其中一項會執行相同 Python suite，不重複計算為新增測試。定向驗證合計 40 tests、0 failures、exit 0；TDD red 與可供 Git Bash 重跑的命令記錄於 repo root `.issue-loop-evidence.json`。

本機驗證結果（Windows、Python 3.11.9、Node 26.7.0、lockfile 的 Next 16.3.8）：

| 驗證 | 結果與限制 |
| --- | --- |
| `npm run check:specs` | exit 0；`VERIFY_OK mode=specs required=164 specs=4 secrets=0` |
| `npm --prefix apps/web run build` | exit 0；靜態頁面 11/11 產生成功 |
| Playwright／Chrome 本機靜態站 smoke | 四個 version IDs、三來源、`112Y12M`／235,441 人／CSV hash 可見；split、merge、confirm 預覽可操作且 canonical ID 未變；不代表公開部署驗收 |
| `npm run check` | exit 1；Web 801 tests 中 796 pass、5 fail；完整 verifier 共 10 個 failed groups，未宣稱全套通過 |
| 完整檢查的 Windows 限制 | `O_NONBLOCK`／`mkfifo` 缺失、Unix deadline、symlink 權限、POSIX mode 與 directory open；涵蓋 schema drift、checkpoint、scope、reference audit 與 transport suites |
| Web 與 checkout 失敗 | PDF 兩項 `PDF_PARSER_UNAVAILABLE`、Query Gateway／Store wrapper 60 秒逾時；current-checkout receipt 的 5 個 checks 均 PASS，但未提交的 dirty worktree 使整體 FAIL |

在 admitted base `c629f82a8b16825d5d89c204c7035dcba500a9ac` 的獨立 detached worktree，已重現 `O_NONBLOCK`、Unix deadline、reference CSV 的 `438 != 384` mode assertion，以及 PDF 的兩項失敗；逾時未據此宣稱為已證實的 baseline failure。這些失敗未擴大修正至本 issue 以外。專案未提供 lint、coverage ratchet、CRAP 或 mutation gate 設定，本次未另加工具。獨立結構審查未留下待修正發現。

## 未完成的外部整合

本次限於 normalized contract、合成 fixture、可重播 CLI 與本機 UI 預覽；未接入真實大型活動的三來源 collector、公開寫入型 merge/split、跨服務持久化、production deployment 或 live 來源收件驗收，也未新增地區／資料集或 production promotion。這些仍需 #14/#27/#47 的實際整合收據；合成文件的 exact locator 只能回查 fixture，不替代真實官方事件證據。
