# S032 有界列表續跑與末頁驗證

2026-10-07 修正 S032 交通局列表的 `Parser=9,4,20,,,,,,,,82` 末頁判斷。官方第 82 頁保留「下一頁」，但該連結被限制為本頁，舊 parser 因而判斷為未結束。新 parser 只在 S032 的原官方 HTTPS origin、列表路徑及精確 Parser 語法中接受此情形，並在掃描全部控制後，要求官方 Last 指向本頁、所有 Next 指向本頁、First／Previous／數字頁碼／Last 均一致。跨站、錯路徑、錯參數、重複 query key、錯誤頁碼、衝突 Last／Next、無 Last 的自迴圈及無末頁證據仍保持 PARTIAL。

原入口 `Parser=9,4,20` 對應第一頁；顯式逗號頁碼必須恰為 11 個欄位，前三欄為 `9,4,20`、中間七欄為空、最後一欄為正整數。這個語法限於 S032。既有其他来源的分页機制和來源權利狀態沒有改動。

## 實際證據

以下單頁與兩批檔案是 V1 時期的實際歷史觀察，保留當時的版本與 hash；不是 V2 續跑鏈。V2 修復沒有新增 HTTP 請求，也沒有把較早的 82 頁／814 項列表 metadata 或 V1 續跑收據重新標成 V2 實測。V1 checkpoint 現在須由官方原入口重新開始，不能用舊第 9 頁游標直接接上 V2。

08:02:22 UTC 對原官方第 82 頁進行一次 HTTP GET，取得 86 個導航控制：Last=82、Next=82、First=1、Previous=81，所有數字控制一致。新 parser 判定該頁導航已到末頁，控制 metadata replay 得到相同結果；7 項列表日期均可辨識。原 body 未保存，詳情與附件未蒐集。完整 metadata 與原 bytes hash 見 [單頁收據](s032-terminal-navigation-observation-20261007.json)。這次單頁觀察不等於重新蒐集 82 頁。

另實際跑兩個最終版本的預設批次，每批 4 頁、4 次 HTTP（每批 HTTP 上限仍為 6），累積 8 頁、79 個唯一 ID、36 個指定日期窗口的列表項，續跑游標為第 9 頁。只保存 hash、導航、ID、日期及聚合收據，不保存 HTML、文章標題、正文或附件。聚合證據見 [兩批實際收據](s032-resume-live-batches-20261007.json)。先前開發版本的兩批 8 次請求另外列為 superseded，並未混入最終續跑鏈。

## 續跑契約

`scripts/news-list-resume.py` 限定 S032，每次最多 40 頁，預設 4 頁；沿用有界 canary transport，每次預設最多 6 次 HTTP，每個 redirect 也計數、停用隱藏重試、每個 body 上限 2 MiB。需要更大批次時必須明確設定頁數與足夠的 HTTP 預算。每次有 120 秒批次期限，頁間至少 0.3 秒。一次工具執行只處理一個批次。

目前是 `schema_version=2`、`S032_COMMA_PARSER_V2`。首批必須由官方原入口開始。續跑 checkpoint 綁定來源、入口、窗口及完整相關程式模組：`online_collect.py`、`collect.py`、checkpoint 模組、續跑 CLI 及有界 transport；這涵蓋 stable key／日期的 row extraction、其 helpers 與 `list_sha256` metadata 產生方式。新增 `intel_v2/checkpoint_code_binding.py`，使用實際 loaded bytecode、遞迴 constants、literal defaults、Python runtime 版本等指紋，不依 `inspect.getsource()` unwrap。對明確註冊的 row/date/navigation、checkpoint 全部 helper 及 code-binding helper，比對當前檔案 compile 結果；只 compile／parse，不執行來源。Wrapper、陳舊 import、來源不一致與 unsupported callable 拒絕於下一次 GET 之前。另綁 S032 row config／ID pattern、角色規則與本地上限。這是註冊程式／設定的一致性檢查，不涵蓋任意第三方 library 或未註冊 global 改寫。任何綁定變更都要求由官方原入口重新蒐集，沒有 V1 遷移或自動重算 hash 的捷徑。

每頁含原 bytes SHA256、時間及列表 ID／日期／list hash。CP 啟用 `strict_same_page_ids=True`，在去重前拒絕同頁 ID 的不同 title／日期／detail URL；相同 metadata 重複仍接受。其他一般 collector 保持原預設去重行為。控制投影前先要求精確導航角色及合法原官方列表 cursor，只保存 `role` 與有界整數 `target_page`；可用角色是 FIRST、PREVIOUS、NEXT、LAST、NUMBERED。官方 URL 從既定入口與頁碼重建，不保存來源 label、title、aria-label、raw class 或 raw href。`next-story` 等文章連結不會因 class 或文字包含 next 而落入 checkpoint；矛盾角色、同一非 NUMBERED 角色的不同目標、導航目標帶額外 query／錯 origin 或含糊的 pager 都會停止。FIRST 必須為 1，PREVIOUS 必須為相鄰前頁（首頁 clamp=1 可接受），LAST 不可早於本頁。相同角色／目標的重複控制可接受。

批次由前一批 hash 串接，整個 checkpoint 另計 hash，續跑 CLI 要求另外提供前次保留的 expected hash。驗證拒絕中段冒充首批、缺頁、重複頁、跨站游標、redirect 游標、末頁後追加、Last 宣告改變、跨批相同 ID 的內容改變及 source／window／parser 變更。`next_url=null` 且 `has_next=true` 是 unresolved pagination，collect 與 validator 都拒絕；CLI 不回成功、不寫新 checkpoint、不覆寫既有輸出。只有有末頁控制證據的 `has_next=false` 才能結束本地頁鏈。

Checkpoint 採 compact JSON，writer 與 reader 使用相同的 2 MiB 限制；整個續跑鏈最多 256 頁／20,000 項列觀察。超過任何限制時停止，不能靠提高預設上限或跳過檢查取得完成宣告。

```sh
python scripts/news-list-resume.py --start 2026-09-01 --end 2026-10-07 \
  --output-checkpoint /private/s032-batch1.json

python scripts/news-list-resume.py --start 2026-09-01 --end 2026-10-07 \
  --resume-checkpoint /private/s032-batch1.json \
  --expected-checkpoint-sha256 PREVIOUSLY_RETAINED_HASH \
  --output-checkpoint /private/s032-batch2.json
```

所有 checkpoint 結果始終是 `LOCAL_TRAVERSAL_RECEIPT_ONLY`。自算 hash 只檢查所提供 metadata 的一致性，沒有官方簽章或獨立認證能力。即使一份本地鏈涵蓋入口至宣告末頁，甚至是重新計算 hash 的虛構全鏈，`window_completeness` 仍為 `PARTIAL`、`whole_history_completeness` 仍為 `UNKNOWN`、`coverage_independently_verified`／`rights_approved`／`promotion_eligible` 仍為 false。`prefix_to_declared_terminal_consistent` 只描述提供的頁鏈與控制是否一致，不是正式來源完整性或 admission。

## 回歸驗證

新增 Parser 控制回歸 10 項及 checkpoint 回歸 13 項，共 23 項。原末頁修正的紅測試有 17 個失敗案例，包含 Last／Next 順序、錯身分、矛盾控制与無 Last 的自迴圈。獨立 review 另找出顯式逗號第一頁無控制、`rel=LAST` 大小寫及 pretty JSON 超出讀取限制三項問題，均已修正並加入回歸。

有真實 86 控制寬度的虛構 82 頁 receipt 用來驗證 compact writer／reader 往返；該測試不是實際來源蒐集。新測試加上既有列表、canary 及有界 transport 回歸，共 110 項通過。測試中的虛構內容與固定 hash 不作正式站或來源驗收證據。

CLI另加5項有界檔案IO回歸：拒絕FIFO／非regular input與相同resolved input/output，讀取最多2MiB，mkstemp0600＋fsync／atomic replace，固定.tmp衝突與destination hardlink均不修改原inode。該followup未進行HTTP。

V2 另加 13 項回歸，重現並修正 PR #173 的三項後續 review：row parser／metadata binding 遺漏、unresolved pager 被當成功及 loose control 投影原文。第一組 8 個紅測試原有 9 個失敗案例，修正後通過；其餘測試驗證完整 module byte 變更、runtime row projection／ID pattern 替換、V1 即使重算 hash 也須 restart、原始 control 格式不能藉重新綁定轉成 V2，以及 CLI 在 unresolved 時不寫檔且關閉 transport。現在 checkpoint 31 項，連同 Parser／列表／canary／transport 相容回歸共 128 項通過，未進行新版 HTTP 實测。

Root 後續整合時另外做了 5 次 bounded 官方 HTTP 相容檢查：新版首個預設批次 4 頁／39 IDs，以及單獨官方第 82 頁末頁檢查。這段實際新版觀察見 [V2 相容收據](s032-v2-compatibility-observation-20261007.json)；前述「未進行新版 HTTP」只描述分頁修復代理交付時。它不是全 82 頁新版重蒐集，不補出權利或獨立完整性。

## Claude 實際覆核後續修復

2026-10-07 Claude Code 已完成一次真正的公開程式碼覆核，登入與供應商呼叫收據、基線重播／已拒絕反例，以及新版驗證範圍見 [後續證據](claude-code-followup-20261007.md)。舊 V1、較早 V2 收據仍保留原 binding／code／時間，不遷移或重算為本版權威。

## 程式檔 IO 與 Python 3.14 追加驗證

2026-10-07補修slice常數與有界程式檔reader，前後兩次module hash及compile來源均先限制regular file／4MiB並只read(limit+1)。10 helper的前置defaults guard採嚴格原生型別比較、不呼叫自訂equality。實際3.14.7、112項與3.12、180項測試及歷史82頁新版鏈的範圍见[追加修復證據](source-code-io-compatibility-20261007.md)。本次binding再次改變，歷史82頁2a48c4a證據不重hash或遷移；新binding僅另做原入口4頁＋單獨82末頁5GET相容檢查。
