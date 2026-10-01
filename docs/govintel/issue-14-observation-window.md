# Issue #14：候選來源觀測窗口必須涵蓋整個 promotion plan

## 問題

`scripts/verify-candidate-observation-window.py` 原本只檢查「有出現在 receipt 裡的來源」的連續 7 日窗口；`promotion_plan` 中的來源如果從未被觀測，報告會直接 PASS，等於繞過「每個新來源至少 7 日 canary」的驗收條件。同時 `.github/workflows/candidate-source-observation.yml` 的每日 matrix 少了 S-019（promotion order 1），該來源永遠無法累積窗口收據。

## 變更

- `validate_reports(..., required_source_ids=None)`：預設從 `docs/govintel/source-catalog.v2.json` 讀取 `promotion_plan`，名單內來源若沒有任何有效觀測日，會以 `only 0 valid observed days` 計入 `reasons` 並輸出 `window_complete=false`；結果為 `BLOCKED`。`promotion_plan` 參照不存在的 `source_id` 會直接報錯（fail closed）。
- `--self-check` 改為對全部 `promotion_plan` 來源產生 7 日 receipt。
- `candidate-source-observation.yml` matrix 補上 `S-019`，五個候選來源全部進入每日觀測。
- `tests/test_candidate_observation_window.py`：新增「未觀測的 promotion 來源會 BLOCK」與「workflow matrix 涵蓋 promotion_plan」回歸測試；既有 fixture 涵蓋全部候選來源。

## 界線

- 這個變更不觀測來源、不產生收據，也不晉升任何來源；`promotion_eligible` 仍一律為 `false`。
- 七日 receipt 仍需由排程 workflow 逐日產生後才可驗證。
