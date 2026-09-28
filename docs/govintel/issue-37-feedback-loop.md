# Issue #37：Feedback Loop

`intel_v2/feedback.py` 與 `scripts/feedback.py` 提供 local-first correction signal：

- 9 類 reason：`FALSE_MERGE`、`MISSED_MERGE`、`WRONG_ENTITY`、`NOT_RELEVANT`、`MISSING_EVENT`、`WRONG_CHANGE_CLASSIFICATION`、`UNSUPPORTED_ANSWER`、`WRONG_STATISTIC_SCOPE`、`BAD_SOURCE_MAPPING`。
- target 可指向 `EVENT`、`ENTITY`、`QUERY` 或 `ANSWER` 的 id/version，也可連結既有 Review Inbox item。
- 原始輸出只保存 SHA-256，不保存私人對話、prompt 或全文；evidence/reference 以最小 ID／locator 保存。
- 相同 fingerprint 去重；每筆保留 `model_version`、`parser_version`、`registry_hash`、audit 與 review status。
- 載入狀態時會驗證 feedback ID／fingerprint、audit sequence／hash binding 與 review decision 一致；被竄改或未完成 review 的記錄 fail closed。
- `ACCEPTED` 才會產生 `FEEDBACK_REGRESSION` link，且明確標成需要後續 gold promotion review；不會自動修改 entity registry、event fusion、evidence gate 或 production rule。
- `statistics` 可按 reason、review status、model/parser/registry 版本查詢錯誤分布。

操作範例：

```bash
python scripts/feedback.py add --target-type EVENT --target-id PE-demo --target-version v1 \
  --reason FALSE_MERGE --output-hash <64-char-sha256> \
  --corrected-state '{"same_event":false}' --evidence-refs '["S-036#fixture"]' \
  --review-id REVIEW-demo
python scripts/feedback.py review --feedback-id FEEDBACK-... --status ACCEPTED --reviewer-ref operator-1
python scripts/feedback.py stats
```

`state/feedback.json` 是 canonical、可重播的本地狀態；公開 Web 不直接寫入它。V2 Review Inbox 現在可建立 9 類、版本／evidence hash 綁定的瀏覽器本機 feedback draft；Ask GovIntel 的查詢結果也可建立 `QUERY` draft，若 Gateway 回傳受控答案證據收據則可建立 `ANSWER` draft。查詢／答案只保存輸出 SHA-256，不保存原始輸出；所有草稿均可匯出 Markdown/JSON，仍須人工轉入 `scripts/feedback.py` 才會進入 canonical review gate。這一版完成 feedback record、review gate、dedupe、regression link、statistics 與 bounded local UI draft，尚未宣稱已接上遠端通知或自動 online learning。
