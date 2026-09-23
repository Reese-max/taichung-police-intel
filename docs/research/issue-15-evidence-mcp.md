# Issue #15 — Read-only Evidence MCP（研究設計）

Research deliverable for `Reese-max/taichung-police-intel#15`。
讓外部 AI 直接查詢 canonical public intelligence，但**嚴格 read-only、
public-only**。

## 1. 產品契約

| # | 契約 |
|---|---|
| C1 | server 只讀 **已通過 promotion gate 的 public artifact**；private/operational source 永不進入 |
| C2 | public/private field **allowlist 有 schema test**；禁止欄位 fixture fail closed |
| C3 | 每個回應帶 provenance（source id、fetch 時間、revision hash） |
| C4 | 「沒有資料」≠「沒有事件」——stale/missing/conflict 是顯式狀態 |

## 2. `EvidenceQuery` / `EvidenceResult`

```jsonc
// query
{ "topics": ["traffic","public-safety"], "since": "...", "limit": 20 }

// result item
{
  "artifactId": "...",
  "revision": "hash",
  "source": { "id": "S-001", "url": "official-url", "fetchedAt": "..." },
  "lifecycle": "current | stale | conflicting | unknown",
  "fields": { "title": "...", "summary": "...", "publishedAt": "..." }
  // 只有 allowlist 欄位出現；其他一律不輸出
}
```

## 3. Tool surface（read-only，少而精）

| Tool | 行為 |
|---|---|
| `evidence_search` | topic/time/source 查詢 → EvidenceResult[] |
| `evidence_get` | artifactId → 單筆 + provenance |
| `evidence_freshness` | 回各 source 的 last-successful-fetch |

**不暴露**：raw HTML、內部 pipeline state、非 allowlist 欄位、
任何 private source。

## 4. Fixtures

| Fixture | 情境 |
|---|---|
| `public-only` | 查詢只回 allowlist 欄位 |
| `forbidden-field` | 嘗試取禁止欄位 → schema fail closed |
| `stale-source` | source 過期 → lifecycle=stale，不假裝最新 |
| `conflict` | 兩 source 衝突 → lifecycle=conflicting，兩筆都回 |
| `no-data-not-no-event` | 查無結果 → 明確空陣列 + freshness receipt |

## 5. 不做什麼

- 不接寫入/annotation/feedback 工具。
- 不把 private/operational source 混入同 server。
- 不讓 MCP 變成第二套 source truth（它是 projection）。
