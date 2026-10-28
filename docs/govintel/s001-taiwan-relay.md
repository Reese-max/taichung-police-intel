# S-001 台灣轉交（source relay）

## 為什麼

S-001「臺中市政府警察局警政新聞」拒絕非台灣位址：2026-10-28 實測，台灣兩台主機（本機、實驗室電腦）抓列表頁都是 HTTP 200，OCI 日本與 GitHub-hosted runner 逾時，正式 schema-drift 因此持續 `LIVE_FETCH_CONNECTIONERROR`、overall `BLOCKED`。
反過來，S-007／S-009（臺中市議會 API）從台灣回 HTTP 500、從 GitHub runner 正常，所以整條管線不能搬到台灣，只有 S-001 走轉交。

## 資料流

```
pages.yml（GitHub runner）── live 抓 S-001 ──✗ 無回應
        │
        └─ 匿名下載 raw.githubusercontent.com/Reese-max/govintel-source-relay/main/relay/S-001.json（curl，30 秒、約 7MB 上限）
               ▲
relay host（OCI，常駐）── 排程 ── SSH（forced command）──► 台灣主機：capture-s001.ps1
        └─ check_relay.py 通過才 commit／push（deploy key 只能寫 relay repo）
```

- `scripts/source-relay/capture-s001.ps1`：台灣主機上由 SSH forced command 執行，只輸出一個 JSON；URL、標頭、fallback 規則同 `online_collect.get_news_listing`。主機上不放任何 GitHub 憑證。
- `scripts/source-relay/check_relay.py`：relay host 發佈前的檢查（僅標準函式庫），15 分鐘內的擷取才發佈。
- `scripts/source-relay/relay-s001.sh`：relay host 排程腳本。
- `scripts/schema_drift.py --live --relay FILE`：`load_relay` 先限檔案大小再讀，驗 schema、型別、catalog URL、HTTP 200、時間（4 小時內、不超前 5 分鐘）、內容雜湊；格式錯誤一律轉成拒絕原因，不中止 live probe。**只有整次 probe（含 fallback、redirect）都沒收到任何 HTTP 回應時**才以轉交代替；收到過回應（例如 503 後 fallback 逾時）就保留 live 證據。

## 收據上的標示

轉交的觀察在 `schema-drift.json` 的來源列多出：

| 欄位 | 意義 |
|---|---|
| `observation_origin: "TAIWAN_RELAY"` | 這筆不是本次 build 親自抓到的 |
| `relay_captured_by` | 擷取主機標籤 |
| `live_error_reason` | 本次 live 失敗原因（例如 `LIVE_FETCH_CONNECTIONERROR`） |
| `observed_at` | 台灣主機擷取時間，不是 build 時間 |

轉交被拒時，來源維持 `SOURCE_UNAVAILABLE`，並多出 `relay_rejected`（例如 `RELAY_STALE`、`RELAY_BODY_HASH_MISMATCH`）。

這些標記也保留在 `state/schema-drift-state.json` 的 `current`／`last_known_good`／`history`、收據的 `review_inbox` 與 Review Inbox 證據。`system-health.json` 的 source_contracts 階段另有 `relayed_sources` 與 `evidence_observed_at`（最舊證據的擷取時間），與檢查完成時間 `ended_at` 分開。

## 範圍與限制

- 只涵蓋 schema-drift 觀察；S-001 的候選收集（candidate lane）仍是 live，不受影響。
- 台灣主機或 relay host 停機時，轉交過期，S-001 回到現行的 `SOURCE_UNAVAILABLE`（fail closed）。
- 台灣主機的管理員能偽造擷取內容；影響限於 S-001 的 schema-drift 判定，不進正式資料。
