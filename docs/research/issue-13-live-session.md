# Issue #13 — 直播會議 Provisional Intelligence Session＋會後對帳

Research deliverable for `Reese-max/taichung-police-intel#13`。
官方公開直播目前是「會後才進 evidence」；本設計定義 bounded
provisional live-session 與會後官方對帳 lifecycle。

## 1. `LiveMeetingSession`（versioned）

```jsonc
{
  "sessionId": "live_<ulid>",
  "officialSource": { "channel": "council-yt | gov-stream", "url": "...", "title": "..." },
  "asrContract": { "engine": "...", "interimRevisions": true, "finalRevisions": true },
  "status": "provisional | reconciled | gap | failed",
  "maxDurationMinutes": 180,
  "gapIntervals": [{"from":"...","to":"..."}],   // stream 斷線區間
  "startedAt": "...", "endedAt": "...",
  "contentHash": "sha256(final transcript)",
  "versionHashes": ["interim-1-hash", "..."]
}
```

## 2. 規則（issue 驗收對應）

- 所有 live text **必須標示未核對**（provisional），真人使用前不得去掉。
- stream gap/reconnect 記 `gapIntervals`；gap 區間內容不假裝存在。
- interim/final revision 分開：interim 是 provisional，final 才可對帳。
- 會後官方 VOD/minutes 出來 → `ReconciliationReport`：
  ```jsonc
  { "sessionId": "...", "officialRef": "vod-url | minutes-doc",
    "matchedSegments": 42, "mismatchedSegments": 3,
    "status": "reconciled | partial | conflicted" }
  ```
- mismatch → `conflicted`，不自動覆寫 provisional（保留兩版）。

## 3. Rolling latency / seek locator

- 每個 interim segment 記 `streamOffset`（秒）→ 對帳時可 seek 回 VOD
  對應時間點。
- rolling latency = `now - segment.streamOffset` 的 p95 進 receipt。

## 4. Fixtures

| Fixture | 情境 |
|---|---|
| `clean-live` | 全程連線 → provisional → reconciled |
| `gap-reconnect` | 中斷 90 秒 → gapIntervals 記錄，不補假內容 |
| `mismatch` | ASR 與官方 minutes 不同 → conflicted，保留兩版 |
| `cost-cap` | maxDurationMinutes 超過 → status=failed，停止收費 ASR |
| `still-provisional` | 官方 VOD 未出 → 永遠標未核對，不自動升級 |

## 5. 驗收（NEEDS_RUNTIME_VERIFICATION）

需一個公開/授權長時段 stream fixture 或真實公開會議 canary，驗證
rolling latency、interim/final、gap/reconnect、cost、seek locator、
post-event VOD/minutes 對帳。Schema/unit test 不足以關閉本 issue。

## 6. 不做什麼

- 不做即時「自動摘要推播」給真人——provisional 只進 ledger。
- 不在 VOD 對帳前把 live text 當 official evidence。
