# Issue #108 — 通勤道路重播與評測（v6）

Fingerprint：`govintel/v6/commute-reopen-replay-evaluation/v1`
基準：`main@562141e396c693e115b3fce10594c434c401a58e`

本單是 #33 的 v6 情境與評測擴充。評分沿用 `scripts/evaluate-govintel.py`（同一套指標實作），
重播引擎是 `intel_v2/commute_replay.py`；本單不另造評測平台，也不複製第二套指標算法。

## 單一命令

```bash
python -X utf8 scripts/replay-commute-reopen.py --self-check
# 或
npm run replay:commute
```

輸出 `COMMUTE_REOPEN_SELF_CHECK_OK` 與每個 arm 的分數。receipt 內含
code/data/policy/parser/model/prompt 版本、資料 hash、重播時鐘與 publication receipt。
`--output <path>` 會寫出完整 receipt；`--predictions <path>` 會寫出可交給
`scripts/evaluate-govintel.py` 的 prediction JSONL。

## 情境（全部合成）

`eval/gold/v6/commute-reopen-v1/session.json` 是一則「測試路 A 至 B」的通勤追蹤，
另加一條區域條件 `C-DISTRICT-XITUN` 與一條測試路 C 至 D 的工程，用來驗證
「同事件多條件命中」與「解除後仍可見」。

| 開站 | 時點 | 提示 | 狀態 | 重點 |
| --- | --- | --- | --- | --- |
| R1 | 10/05 10:00 | UPD-001 | NEW_UPDATES | v1 施工，保存條件後第一次重開 |
| R2 | 10/06 17:20 | — | NO_NEW_ITEMS | 同源轉載、重複取回、純排版版本都不提示 |
| R3 | 10/08 08:50 | UPD-005 | NEW_UPDATES | v2 只改結束日；追蹤結束日同步往後移，條件不解除 |
| R4 | 10/08 18:10 | — | SOURCE_GAP | S-001 連線失敗 |
| R5 | 10/09 09:10 | — | SOURCE_GAP | S-031 PARTIAL＋資料消失 |
| R6 | 10/09 17:10 | — | STALE_TRACKED_END | 預定日期已到但沒有明文解除 |
| R7 | 10/09 17:40 | UPD-006 | NEW_UPDATES | v3 明文解除（只解除道路條件） |
| R8 | 10/09 18:00 | UPD-007 | NEW_UPDATES | 另一工程仍可見 |
| R9 | 10/10 08:30 | UPD-008 | NEW_UPDATES | 取消道路條件後停止該條件提示，原文仍可查 |

每個開站時點的預期集合是「當時已取得、符合啟用條件、未讀的實質更新」。
`沒有新項目 / 來源失聯 / 預定日期已到` 是四個不同的狀態，不合併。
資料消失、預定日期已到與取不到資料都**不會**自動解除條件；只有明文解除或使用者取消會。

## 公平比較：分開的方法組

| arm | method ID | 狀態 | 本輪結果 |
| --- | --- | --- | --- |
| A | `A_v1_manual_same_sources_same_cutoff` | NOT_RUN | 未執行真人對照 |
| B | `B_v6_same_scope_search_generic_summary` | REPLAYED（無讀取追蹤） | precision 0.227（17 次已讀重複提示） |
| C | `C_v6_full_query_and_tracking` | REPLAYED | precision 1.0、recall 1.0（tp 5／fp 0／fn 0） |
| C_RULES_ONLY | `C_RULES_ONLY_v6_rules_only_no_semantic` | REPLAYED | precision 0.714（2 次誤報） |

`B_v6`（同範圍搜尋＋一般摘要）與 v1 模板的 `B`（`same_new_workflow_without_semantic_AI`）
**不是同一組**。v1 的定義與歷史結果在
`docs/govintel/competition-2026/evaluation-manifest.template.json` 保持凍結，v6 以
`v6_arms` 另立明確 method ID。`C_RULES_ONLY` 保留 C 的介面、資料與站內提示規則，
只關閉語意處理，因此與 B 分開計算。

## 評分口徑

- 以穩定更新 ID 計 TP／FP／FN；同一事件命中多條條件只算一次 TP。
  receipt 的 `counters.prompted_condition_pairs`（8）與 `multi_condition_hit`（3）
  並列顯示，`tp` 仍是 5。
- 失敗模式分開計數，不混成一個總數：
  `ALREADY_READ`／`FORMAT_ONLY`／`REPOST_MIRROR`／`POST_CANCEL`／`other_false_alert`，
  漏報另分 `distinct_correction_wrongly_deduped`／`missed_while_source_gap`／
  `missed_without_recorded_reason`。
- 來源取得缺漏（`source_acquisition_gap`）另列，不併入精確率／召回率分子分母。
- 零分母為 `null`；缺口 reopen 的預期集合是空集合，因此取得缺漏不會變成漏報。
- 目標值（P≥85%、R≥90%、相對 A 中位總時間降低≥30%）寫在 manifest 的 `targets`，
  與實測值分列；本輪未達標與未執行分開記錄。

## 驗收讀取當前實作

`--self-check` 除了確認完整 v6 arm 完全符合 gold 標註，還要求每個受控探針都讓分數改變：

| 探針 | 改動 | 必須產生的缺陷 |
| --- | --- | --- |
| `C_RULES_ONLY` | 關閉語意處理 | 2 次誤報（範圍外、日期無法驗證） |
| `B_v6` | 關閉已讀狀態 | 17 次已讀重複提示 |
| `PROBE_MIRROR_AS_EVIDENCE` | 同源鏡像視為獨立證據 | 1 次轉載誤報 |
| `PROBE_FORMATTING_AS_SUBSTANTIVE` | 純排版視為實質 | 1 次排版誤報 |
| `PROBE_DEDUPE_OFF` | 關閉重複取回去重 | 1 次重複取回誤報 |
| `PROBE_ONE_ALERT_PER_DOCUMENT` | 每份官方文件只提示一次 | 3 次實質更正被誤去重（recall 0.4） |
| `PROBE_AUTO_LIFT_ON_TRACKED_END` | 到期自動解除 | R6 狀態被誤報，缺口被隱藏 |
| `PROBE_CANCEL_MOVED_LATER` | 把取消時間往後移 | R9 多提示一次，證明取消判斷讀取現況 |

## 標註與切分

`annotations.json`：先讀原文與 before/after 差異標準答案，再寫預期集合，最後才執行重播。
模型輸出不得自任 gold；未解決爭議另列。dev = R1–R3、holdout = R4–R9，同一事件版本與其
轉載不跨組（以「被當成提示對象的更新 ID」檢查，兩組不相交）。

## 成本

`cost-ledger.json` 分列每次完成查詢、每個有效更新、每個追蹤條件，以及模型請求與人工工時。
本輪為離線重播：`model_request = 0` 且狀態是 `NOT_APPLICABLE_NO_MODEL_CALL`，不是「零成本」宣稱；
`human_hour` 一律 `NOT_RUN`。自檢另外把條件數從 2 增加到 5、有效更新增加到 8，
`model_requests` 仍是 0 —— 共同公告不會因使用者或條件數逐份重跑模型。

## 本輪未執行（NOT_RUN）

- 真實瀏覽器執行查詢／保存／關閉重開／已讀／解除／取消的操作 trace 與截圖。
  沒有瀏覽器 run 就不能算 PASS，維持 NOT_RUN。
- 3–5 位同意參與者的真人任務與匿名彙總。本單不授權招募或外傳參與者資料。
- 真實官方案例、人工修改的真實文件副本與真實來源（第一輪 20 事件／60 文件）。
  實際 n、版本數與缺樣原因寫在 manifest 的 `planned_scale`。
- A arm 的人工中位總時間與「相對 A 降低≥30%」的目標比較。
- D1（112 年 12 月期別、人口／戶數）與 D2（地址／電話、機關參考）理解度量測：
  屬於來源與背景資料範圍，不併入本單的修訂辨識效果。
- #106／#107 的公開查詢與站內條件 UI 尚未進 main；本單量測的是重播與評測管線，
  不宣稱端到端 UI 已驗收。#25 現行展示入口連結由該單維護。

## 重播與評測邊界

- 合成路名、日期與原文不代表真實路況或道路安全；每筆 update 的 `rights` 都是
  `SYNTHETIC_NO_REAL_ROAD`。
- 來源未通過時，只能交付有資料截止的離線查詢與追蹤驗證包；本單不聲稱完整即時涵蓋。
- 不評背景推播投遞、不建立通知管線，也不把帳號、跨裝置同步或更多縣市納入本輪。