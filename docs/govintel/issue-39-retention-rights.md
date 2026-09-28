# Issue #39：Retention / Rights / Archive Policy

`docs/govintel/retention-rights-policy.v1.json` 與 `scripts/retention-policy.py` 提供 versioned source class、rights status、public projection、raw/canonical retention window 與唯讀 expiry receipt：

- rights unknown 一律需要 review，且不能開放全文／excerpt；公開查詢只投影 metadata/link 或 evidence-bound summary。
- raw／normalized document 到期可規劃 purge，但沒有 audit linkage 時 fail closed。
- canonical event、publication、query index 到期也必須有 `audit_refs` 才能標記 `KEEP_AUDIT_LINKAGE`；沒有 linkage 會是 `BLOCKED`，不得假稱可安全清理。
- receipt 綁定 policy version/hash，不執行刪除；實際 purge 仍須由受控 writer 依 receipt 執行。

驗證：

```bash
python -X utf8 -m unittest discover -s tests -p 'test_retention_policy.py' -v
python -X utf8 scripts/retention-policy.py --self-check
```
