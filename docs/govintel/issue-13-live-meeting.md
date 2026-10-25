# #13 公開會議即時導航

既有 `intel_v2/live_meeting.py` 是唯一 session／reconciliation 狀態機。
`intel_v2/live_asr.py` 接上明確啟用的本機錄製音訊分段入口，
`scripts/live-meeting.py` 負責原子保存與跨程序鎖；`/live/` 讀取相同 JSON。
這是手動、有時間上限的 MVP；不會發現直播後自動開啟 ASR。

## 操作與邊界

- `start-live-session`／`stop-live-session` 與原有 `start`／`stop` 相容。開始時必須指定 allowlist 中的官方來源及 HTTPS stream 身分。
- transport 需要 `--enable-transport --provider-authorized` 與明確的 `--budget-seconds`。實際 Groq 呼叫另需環境中的 `GROQ_API_KEY` 及每次 `--recorded-public-audio`；未具備條件就拒絕，不會送出請求。
- 音訊由操作者確認為授權的公開會議錄製內容；只接受本機、1–60 秒、小於 2 MB 的 mono PCM16／16 kHz WAV。入口不下載 HLS、不跟隨 redirect、不接受 provider 指定 URL，也不自動重試。
- session 同時限制媒體時間、累計保留的音訊秒數及實際請求 deadline。送出前先保存 PENDING reservation；失敗與 crash 仍計入使用量。下次 ingest 會先持久化 `FAILED` reservation 與 `SESSION_CRASH` gap，再判斷預算／牆鐘到期或拒絕重送同一區間（#219）。已停止的舊 session 也會修復尚未關閉的 reservation，並保留 `BUDGET_STOP`，不能重新取得預算。
- 父／子程序各有整體逾時；到期後不得透過 `resume` 重新取得預算。顯示呼叫次數、保留秒數與 mode；`estimated_cost_usd=null` 表示金額未知，不把音訊秒數誤稱為實際帳單。未驗證 unattended 串流成本，不提供自動常駐。
- CLI 對整個讀取→reservation→transport→保存交易持有 OS lock；crash 後 OS 釋放鎖。鎖檔保留，避免刪除仍被其他程序使用的鎖。

以下為有另外付費授權後的操作範例，`chunk.wav` 必須由操作者提供；範例沒有金鑰值，不會由驗收腳本執行：

```bash
python -X utf8 scripts/live-meeting.py --state .runtime/meeting.json start-live-session \
  --source-id S-010 --official-source-url https://www.tccc.gov.tw/ \
  --stream-url https://vod.tccc.gov.tw/live/operator-verified.m3u8 \
  --provider groq --model whisper-large-v3 --asr-version v1 \
  --max-duration 1800 --budget-seconds 1800 --enable-transport --provider-authorized
python -X utf8 scripts/live-meeting.py --state .runtime/meeting.json transcribe-chunk \
  --audio chunk.wav --start 0 --recorded-public-audio --profile-id traffic-policy
python -X utf8 scripts/live-meeting.py --state .runtime/meeting.json stop-live-session
```

`/live/` 不啟動 transport。明確選取本機 session JSON 後可搜尋、查看修訂、
profile 命中與缺漏，並將當時文字／revision／hash／時間保存為同瀏覽器 WATCH
sidecar 或下載 JSON。重新匯入更正文字不會取代原 WATCH。
播放器只使用 allowlisted、已確認 `STREAM_TIME` 的 URL；沒有契約、載入失敗
或實際 seek 不成功時顯示時間文字。每段始終顯示 `LIVE / AI 轉錄 / 未核對`。

## 會後核對與正式證據

`reconcile --input candidates.json` 重用既有候選與收據流程；操作者提供已取得的
官方 VOD／minutes／agenda locator、來源類型、版本及文字 SHA-256。
來源尚未取得時保留 `SOURCE_NOT_YET_AVAILABLE`，衝突／不確定時保存
`SUPERSEDED_TRANSCRIPT`／`UNRESOLVED`，也保留可用的官方 after hash。
此 MVP 不排程爬取尚未發布的議事錄，也不使用模型自行判斷官方事實。

收據保存 before/after hash、locator、ASR contract、reconciler/parser version、
verification status 與核對時間。新收據以 `superseded_by` 串接歷史；
`mark-stale` 標示來源改版，原始證據 hash 不被改寫。stale 收據不匯出正式候選。
一般已核對候選僅為 `OFFICIAL_RECONCILED`，仍需通過原 publication evidence
gate；本功能沒有新增正式 publication writer。錄製測試的 `FIXTURE_ONLY`
session 一律不匯出正式候選，確認類收據也只在 UI 顯示 `FIXTURE_ONLY`。

## 可重跑的離線 runtime 驗收

```bash
python -X utf8 scripts/live-meeting.py self-check
python -X utf8 scripts/live-meeting.py replay-recording --out runtime-evidence/live-meeting
python -X utf8 -m unittest discover -s tests -p 'test_live_meeting*.py' -v
node --test apps/web/tests/live-session-view.test.mjs apps/web/tests/live-session.test.mjs
npm --prefix apps/web run build
node scripts/verify-live-meeting-browser.mjs
npm run check:gate0
```

瀏覽器驗收使用既有 `playwright-core`，優先啟動已安裝的 Chrome；沒有 Chrome
時需有可用的 Playwright Chromium，或設定 `GOVINTEL_CHROME_PATH`。
所有非 loopback 請求均攔截，僅指定的合成 WAV fixture 會回傳本機位元組。

`recorded-stream.json` 明確標為 `OWNED_SYNTHETIC_AUDIO_FIXTURE`：程式產生
自有 30 分鐘靜音 WAV，以加速媒體時鐘經過真正分段／reservation／保存流程，
注入自行編寫的辨識回應。結果為 55 段、57 次模擬嘗試、1,710 秒保留用量、
150 秒 visible gaps（90 秒 stream 中斷、30 秒 ASR outage、30 秒 crash），
完整涵蓋 1,800 秒；到期後新增呼叫為 0，實際 provider 網路呼叫為 0。
`runtime-receipt.json` 的 latency 僅是本機 fixture 處理時間，不能作為中文 ASR
品質、真實 provider 延遲／帳單或官方影音可用性的證據。

真實 Chromium 的 10 項檢查涵蓋明確載入、搜尋與 gap、WATCH 修訂保存／下載／
重載、無效 JSON 隔離、會後 fixture 收據、真正媒體時鐘 seek、無法 seek 的
降級、320 px 版面與瀏覽器錯誤；截圖輸出於 `output/playwright/issue-13/`。
這是離線錄製流程與瀏覽器 fixture 驗收，沒有宣稱真實會議 canary 或真人任務測試。

## Acceptance mapping

| Issue 驗收條件 | 實作與證據 |
| --- | --- |
| 版本化 session／官方來源／hash／status | 沿用 canonical schema v1、`validate_session` 與 self-check |
| opt-in／key／budget／source gate | CLI 明確授權、固定 Groq contract、無條件時零 transport 測試 |
| 所有 live 文字 provisional，無 AUTO_PASS | segment schema／validator、UI 固定標示、`formal_candidates` gate |
| 穩定 ID／時間／hash／ASR／修訂 | `append_segment`、`finalized_at`、speaker drift 與 bookmark 修訂測試 |
| search／bookmark／可信 seek 或降級 | `/live/`、WATCH sidecar；Chromium 成功 seek 與不可 seek fixture |
| disconnect／outage／budget visible gap | 30 分鐘回放、既有 budget-stop fixture；不以空結果取代 gap |
| deterministic profile highlighting | 重用 `role_profiles`，不改逐字稿或產生 operational recommendation |
| 會後官方 locator／unresolved | 重用 `reconcile_session`；VOD later available／minutes contradiction fixtures |
| before/after hash／版本／stale | 收據來源類型、parser version、verification status；既有 stale 測試 |
| 正式 publication gate | 無新增 writer；未核對／stale／FIXTURE_ONLY 不匯出正式候選 |
| 十類 fixtures 與 runtime | 既有 scenario fixtures、30 分鐘 WAV 回放、39 Python／69 Node／10 瀏覽器檢查 |
| `npm run check`／no-key snapshot | 完整命令已執行但未全綠，見以下既有環境限制；未修改排程或 snapshot 流程 |

2026-10-16 此 Windows checkout 的完整 `npm run check` 結束碼為 1。
失敗包含 admitted base 的 POSIX `os.O_NONBLOCK`、目錄 `os.open` 權限、
chmod／symlink 語意，以及安裝的 pypdf 6.18.0 與 pin 6.19.0 不符。
前三類已在 `c629f82a8b16825d5d89c204c7035dcba500a9ac` 的乾淨暫存副本重現；
沒有藉此擴大修改其他 issue 的模組。相關測試、static build 與 gate0 可獨立重跑；
全套綠燈、真實 Groq／公開會議 canary、官方來源可用性及真人 usability 均未驗證。
專案沒有獨立配置 lint／coverage ratchet／CRAP／mutation gate，本次未新增工具。

## PR #51 prototype preservation

獨立 JavaScript prototype 仍保留在 `tests/support/live-session-prototype.mjs`，
其 regression suite 繼續執行；替代 storage schema 仍只是
`docs/govintel/prototypes/live-session-storage.sql` 設計參考，未套用 migration。
正式與 workspace view 均使用 canonical Python session contract 及
`live-session-view.js`，沒有增加第二個 production backend。
