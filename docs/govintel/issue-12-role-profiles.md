# Issue #12：Versioned Unit／Role Intelligence Profile 與角色化 Priority Brief

`intel_v2/role_profiles.py` 與 `docs/govintel/role-profiles.v1.json` 提供可版本化的公開職能檔（profile），對既有已驗證事件做 deterministic relevance projection：

- profile schema 為 allowlist：`profile_id`（kebab-case）、`version`、`label`、`responsibilities`、`topics`、`source_ids`、`role_terms`、`deadline_weight`、`status`；未知欄位一律拒絕，人名、案件、私人聯絡、內部勤務等明顯敏感欄位 fail closed。
- repository-defined profiles：`general`（局本部綜合）、`council-liaison`（議會聯絡）、`traffic-policy`（交通／公共安全業管），內容僅來自公開、非敏感工作職能。
- `rank_items` 以 reason code 權重加總排序，reason codes 包含 `affected_role_match`、`topic_match`、`source_priority`、`deadline_within_72h`、`status_changed`、`explicit_follow_up`；無命中時明確標 `default`。排序 key 為 score → change-type 優先序 → source_id → event_id，相同 event/profile/policy 產生相同結果，可重播。
- `project_profile` 產生 Top 3（`priority_items`）、最多 5 件 `tracking_items` 與 `other_changes` 的 profile view；每個 item 帶 `profile_relevance`（`profile_id`、`profile_version`、`profile_hash`、`ranking_policy_version`、`score`、`reason_codes`）。
- `scripts/build-v2-shadow-brief.py` 的 `enrich_for_police_users` 對所有 active profile 各產生一份 view 寫入 `brief["profile_views"]`，並以 `--profile` 選定的 view 作為 brief 頂層 projection；profile 只複製排序，不改寫 `what_changed`、temporal basis、evidence、source health 或 gap state。
- `scripts/verify-v2-publication.py` 強制檢查：每份 view 的 profile metadata、`profile_relevance` 綁定、Top 3 / tracking 上限，以及 `validate_profile_view_consistency` 確保同一 canonical item 在各 profile view 間的 event identity、what_changed、temporal basis、official evidence、source locator、source_health、source_freshness 與 source_gaps 完全一致——profile view 不得靠刪改缺口狀態美化排序結果。
- 首頁 `V2DailyDashboard.js` 提供角色選單與 `?profile=<id>` URL query；切換只改變卡片排序與 relevance reason chips，來源健康、stale／partial 缺口警告與官方證據連結維持不變。

操作範例：

```bash
python3 scripts/build-v2-shadow-brief.py --profile council-liaison
python3 -X utf8 -m unittest discover -s tests -p test_role_profiles.py -v
python3 -X utf8 -m unittest discover -s tests -p test_v2_publication_contract.py -v
python3 -X utf8 scripts/verify-v2-publication.py
```

每份 publication 保存 `profile_id + profile_version + profile_hash + ranking_policy_version`；profile 改版後舊 publication 的排序仍可用原 hash 回溯解釋，不把新設定倒灌到歷史。本版只做 deterministic 排序與選擇，不產生新事實、不做通知渠道、不放入個人或 operational deployment 資料；stale/failed 來源在 profile view 下仍以缺口呈現，不會被解讀成零事件。
