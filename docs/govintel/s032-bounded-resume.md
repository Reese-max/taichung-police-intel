# S032 有界列表續跑與末頁驗證

2026-10-07 修正 S032 交通局列表的 `Parser=9,4,20,,,,,,,,82` 末頁判斷。官方第 82 頁保留「下一頁」，但該連結被限制為本頁，舊 parser 因而判斷為未結束。新 parser 只在 S032 的原官方 HTTPS origin、列表路徑及精確 Parser 語法中接受此情形，並在掃描全部控制後，要求官方 Last 指向本頁、所有 Next 指向本頁、First／Previous／數字頁碼／Last 均一致。跨站、錯路徑、錯參數、重複 query key、錯誤頁碼、衝突 Last／Next、無 Last 的自迴圈及無末頁證據仍保持 PARTIAL。

原入口 `Parser=9,4,20` 對應第一頁；顯式逗號頁碼必須恰為 11 個欄位，前三欄為 `9,4,20`、中間七欄為空、最後一欄為正整數。這個語法限於 S032。既有其他来源的分页機制和來源權利狀態沒有改動。

## 實際證據

08:02:22 UTC 對原官方第 82 頁進行一次 HTTP GET，取得 86 個導航控制：Last=82、Next=82、First=1、Previous=81，所有數字控制一致。新 parser 判定該頁導航已到末頁，控制 metadata replay 得到相同結果；7 項列表日期均可辨識。原 body 未保存，詳情與附件未蒐集。完整 metadata 與原 bytes hash 見 [單頁收據](s032-terminal-navigation-observation-20261007.json)。這次單頁觀察不等於重新蒐集 82 頁。

另實際跑兩個最終版本的預設批次，每批 4 頁、4 次 HTTP（每批 HTTP 上限仍為 6），累積 8 頁、79 個唯一 ID、36 個指定日期窗口的列表項，續跑游標為第 9 頁。只保存 hash、導航、ID、日期及聚合收據，不保存 HTML、文章標題、正文或附件。聚合證據見 [兩批實際收據](s032-resume-live-batches-20261007.json)。先前開發版本的兩批 8 次請求另外列為 superseded，並未混入最終續跑鏈。

## 續跑契約

`scripts/news-list-resume.py` 限定 S032，每次最多 40 頁，預設 4 頁；沿用有界 canary transport，每次預設最多 6 次 HTTP，每個 redirect 也計數、停用隱藏重試、每個 body 上限 2 MiB。需要更大批次時必須明確設定頁數與足夠的 HTTP 預算。每次有 120 秒批次期限，頁間至少 0.3 秒。一次工具執行只處理一個批次。

首批必須由官方原入口開始。續跑 checkpoint 綁定來源、入口、窗口、Parser 合約與程式 hash；每頁含原 bytes SHA256、時間、控制 metadata 及列表 ID／日期／list hash。批次由前一批 hash 串接，整個 checkpoint 另計 hash，續跑 CLI 要求另外提供前次保留的 expected hash。驗證拒絕中段冒充首批、缺頁、重複頁、跨站游標、redirect 游標、末頁後追加、Last 宣告改變、跨批相同 ID 的內容改變及 source／window／parser 變更。

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
