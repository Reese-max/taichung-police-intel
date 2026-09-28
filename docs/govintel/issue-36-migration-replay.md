# Issue #36：Schema Migration / Replay / Backfill

`scripts/migration_replay.py` 提供最小可重播流程：

- `PublicEvent`、`ChangeEvent`、`EvidenceEnvelope` 使用 `schema_version` 1 → 2 → 3 migration registry。
- `dry-run` 回報 input/output hash、affected/error count、object counts、版本 binding 與人工 state hash；任一 object collection 缺少、未知或有錯誤時 fail closed。
- `apply` 只在整批無錯誤後 atomic write；既有 output 不會被部分 migration 覆蓋。
- `replay` 以相同 feed、previous state、parser/semantics/projection version 重建 deterministic state/event IDs，保留 `FIRST_SEEN` 語意。
- `manual_state`、watch、handoff、fusion/merge/split 與 review state 以獨立 payload 和 hash 保留；receipt 明確標記 `PRESERVE_SEPARATELY_NO_AUTOMATIC_MUTATION`，replay 不偷偷改寫人工決策。
- `scripts/query-store.py build` 從指定的 feed/status/brief canonical artifact 完整重建 version-bound Query Store；不把 Query Store 當 truth store。

驗證：

```bash
python -X utf8 -m unittest discover -s tests -p 'test_migration_replay.py' -v
python -X utf8 scripts/migration_replay.py self-check
```

這個流程不會替缺少 raw/source snapshot 的歷史資料猜造 evidence，也不會自動把保留的人工 merge/split decision 套進新的 canonical event；需要人工確認的 state 仍須由對應 writer 重新套用。
