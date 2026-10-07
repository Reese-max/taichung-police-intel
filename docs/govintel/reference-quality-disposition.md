# 原件品質分隔與資源集合驗證

本地工具現在能把有結構問題的資料列分隔成可重跑的 quarantine index，並列出精確重複群。原件保持不變；來源權利、業務識別、正式查詢准入及模型傳輸仍未核准。這項修復提供可追查的覆核位置，不會自行更正官方日期、刪除真實活動，或把重複紀錄合計成案件／人口。

## 2026-10-07 實際重跑

[彙總與 26 個原件 before/after SHA256 證據](reference-quality-disposition-20261007.json) 是既有下載原件的離線重跑，沒有新的官方下載。四組工具均因品質問題 exit 2；`original_hashes_unchanged=true`。原始資料及逐列 hash index 保存在 private scratch，公開文件只有 schema、期間、筆數、原件 hash 與品質彙總。

| 原件組 | 全部資料列 | 分隔待處理列 | 候選未覆核列（保留 multiplicity） | 精確重複額外列 | 尚待釐清 |
|---|---:|---:|---:|---:|---|
| S036 集會遊行 CSV | 77,624 | 17 | 77,607 | 468／353 群 | 9 列共有 10 空值；8 列結束早於開始 |
| CTX165 涉詐網站 CSV | 93,839 | 0 | 93,839 | 7,183／7,163 群 | 網域與法律處理事件的業務識別、版本語意 |
| S028 刑案統計，18 原件 | 27,040 | 0 | 27,040 | 0 | 觀察到的集合缺 2026-05，不能當成該月零案件 |
| CTXPOP 年資料，6 原件 | 1,566 | 0 | 1,566 | 261／261 群 | 2023 年兩個資源原始 bytes 相同，版本與發布用途尚待官方對帳 |

`candidate_unreviewed_rows` 只表示已通過本地已知結構檢查，仍保留重複列。`candidate_unique_full_rows` 是精確解碼後整列 hash 的種類數，不能稱為獨立活動、涉詐事件、案件或人口數。非空且日期格式正確也不證明內容正確。

S036 時間沒有 timezone；只比較來源本地時間字串，維持 `source_timezone=UNKNOWN`。JSON 日期欄的月／年邊界檢查只對照當下已下載資源集合；`period_semantics=NOT_VERIFIED`。缺期計算只在已觀察最早至最晚期別之間，不證明之前、之後或指定業務期間完整。

## S028／CTXPOP 候選粒度對帳

[新增離線結構對帳](reference-grain-reconciliation-20261007.json) 使用同一組 24 個 JSON 原件及既有下載收據；沒有重新下載，24 個 before/after SHA256 均相同。原有品質收據及逐列索引沒有改寫。兩組實際 CLI 仍 exit 2／`QUALITY_ISSUES`，下載宣告、原件 byte/schema 驗證及原先缺期／重複判斷均保留。

S028 的 3,120 與 1,040 列差異已能由原件結構解釋：14 個已觀察月份各含 1 個「項目」與 1,040 個「欄位名稱」；2026-01、2026-02、2026-06、2026-07 各含 3 個「項目」，每個項目各有相同的 1,040 個欄位名稱，因此各 3,120 列。18 個月的欄位名稱集合完全相同，項目集合有兩種。27,040 列在 `資料時間日期＋地區＋項目＋欄位名稱` 的候選鍵上皆唯一，沒有同鍵數值字串衝突。這只解釋資料列結構，不證明三個項目可以相加、為何某月只提供一個項目，或哪些項目代表發生／破獲等官方統計口徑。2026-05 仍是已觀察集合缺期，不能填零。

CTXPOP 的五個已觀察年度均有 29 個地區、2 個項目、9 個欄位名稱；兩個項目各包含 3 與 6 個欄位名稱。2023 年保留兩個 byte-identical 原件的全部 522 列，候選鍵有 261 組、每組 multiplicity 2，同鍵數值字串無衝突。其餘年度各 261 列、候選鍵皆唯一。這不授權刪除 2023 年原件或自行選定正式版本，也不代表 261 種統計欄位可合計成人口數。

JSON audit 現在輸出 `candidate_grain_profile`，只含上述特定 schema 欄位的 distinct counts、multiplicity histograms、欄位／項目集合是否相同與同鍵數值字串衝突數；不輸出實際分類標籤、數值、row hashes 或位置。profile 只包含已通過本地結構檢查的 `CANDIDATE_UNREVIEWED` 列。候選鍵不是已確認的官方統計粒度；數值只做精確解碼字串比較，不把 `42` 和 `42.0` 自動視為同值。同鍵有不同數值字串會標記 `QUALITY_ISSUES`，原件仍保持 byte/schema 已驗證狀態。

工具同時回傳 `aggregation_guard.status=NOT_ALLOWED`，期內加總、跨期加總及去重皆為 false；此欄位記錄 audit 的使用限制，不會對其他程式提供權利或改動正式來源准入。`business_identity=UNKNOWN`、官方期間語意／量值可加性尚未核對、`rights_review=PENDING`、`production_active=false`、`model_transmission_allowed=false` 持續保留。

候選鍵會保留完整、未轉換的 `資料時間日期` 字串；月／年 period 只用於報告分組，不取代鍵中的日期欄位。預期 ISO 日期契約限定 ASCII 數字；Unicode 數字日期列標記 `INVALID_COLLECTION_PERIOD` 並分隔，不能自動轉成同月份或同鍵。[完整日期鍵與 ASCII 契約的追加離線收據](reference-grain-reconciliation-v2-20261007.json) 保留前一份收據的 SHA256；原收據沒有改寫，實際 24 個原件的結構彙總與品質判斷沒有改變，原件 SHA256 仍全部相同。

## CSV 單一解析路徑

`scripts/reference-csv-audit.py` 保持原有 schema、SHA、64 MiB、UTF-8 與 CSV width 檢查，新增逐列 reason codes：

- `EMPTY_REQUIRED_VALUE`：原件 schema 中任何欄值空白。
- `INVALID_LOCAL_TIME`／`END_BEFORE_START`：S036 日期格式、曆日或區間問題。
- `INVALID_ROC_PERIOD`：CTX165 民國年月格式問題。

同一列可以有多個理由，分隔列數仍只計一次。精確中文欄位說明只跳過 S036 的第一個資料位置。列號是含 header 的 one-based CSV record ordinal，CSV 引號中的換行不增加 record ordinal；不是檔案實體行數。

```sh
python scripts/reference-csv-audit.py \
  --source-id S-036 --resource /private/raw/S-036-original.csv \
  --expected-sha256 8719423307dfa67931cf0d6d5884d7cb4a777bbf9a67e891e4057ffa82834314 \
  --out /private/review/S036-summary.json \
  --private-index-out /private/review/S036-dispositions.json
```

CTX165 同理使用其已核對原件 hash。省略 `--private-index-out` 時，報告不含逐列索引。索引每列只有 record ordinal、row SHA256、處理狀態及 reason codes；重複群保留所有位置和 multiplicity。row hash 只表示精確解碼欄位相同，無法判斷兩筆是否為同一業務事件。

## JSON 原件集合契約

`scripts/reference-json-collection-audit.py` 在離線環境把 S028／CTXPOP 當下 metadata declaration 與下載收據、原檔連結：

- 宣告數量必須非空、有界且一致，resource ID 必須唯一；使用現有 inventory parser 從原 URL 取得唯一 ID，並拒絕宣告 ID／URL 衝突。舊收據的 null ID 可由未改動且無歧義的原 URL 解析，不修改原 metadata 收據。
- 每個下載收據必須綁相同原 URL、HTTP 200、完整 byte count 和 SHA256；不接受 redirect／替代 URL。下載收據缺少、原件遺失、bytes/hash 不符會使集合 `RESOURCE_CONTRACT_BLOCKED`。
- 每個原件最多 4 MiB，JSON 必須是非空 array，每列精確 13 個既有字串欄位；拒絕 HTML、空集合、重複 JSON keys、非有限數、schema/type drift 及原件 symlink 逃出 private directory。
- 彙總列出跨原件重複、完全相同的原件組、觀察期間缺口。這是 `LOCAL_CURRENT_METADATA_RESOURCE_SET_ONLY`，不是指定官方期間的應有資源完整證明。

```sh
python scripts/reference-json-collection-audit.py \
  --source-id S-028 \
  --metadata-receipts /private/raw/reference-metadata-20261007.json \
  --download-receipts /private/raw/reference-original-json-resources.json \
  --resource-root /private/raw \
  --out /private/review/S028-summary.json \
  --private-index-out /private/review/S028-dispositions.json
```

CTXPOP 同理。JSON index 列號是 one-based JSON array position，以 resource ID、原件 hash 和整列 hash 綁定。跨資源重複額外列定義為群中總 multiplicity 減去單一原件最大 multiplicity；原件內重複另列，不重複計算。原件 schema 含聯絡欄，但值不寫入報告或索引。

兩個 CLI 以 0600 atomic write 寫報告／索引，避免修改原件 inode；輸出不得覆蓋原輸入，JSON 輸出必須位於 raw directory 外。共用的有界讀取以 nonblocking open 後檢查 regular-file 類型，拒絕 FIFO／特殊檔案，避免在套用 byte 上限前無限等待。沒有任何工具請求網路、發送原件給模型、產生傳輸准入，或修改正式站來源政策。

## 覆核與剩餘驗收

逐列 hash index 仍應留在 private review 環境。它可和原件反查，**不構成匿名化批准**；工具維持 `row_hash_index_anonymization_approved=false`、`model_transmission_allowed=false`、`rights_review=PENDING`、`business_identity=UNKNOWN`、`production_active=false`。

後續須取得官方區間／空值說明、精確重複與版本規則、S028 應有月份清單、CTXPOP 2023 重複發布原因、來源 timezone／期間語意及 source/item-level 權利覆核。這些答案沒有由工具推填。驗收前不會把候選集合導入正式查詢或模型，也不會把 S028 缺月份報為零。

新增測試覆蓋輸出保護、原件 hash 不變、空值與倒置重疊理由、quoted newline record ordinal、duplicate multiplicity、缺收據、錯 hash/bytes、schema drift、重複 JSON keys、null-ID 舊收據、ID 衝突、集合缺期、跨原件年度重複與 private index 權限。新 JSON suite 與既有 CSV suite 已接 `scripts/verify-project.mjs` 持續驗證 gate。測試資料是 fictional offline fixtures，不能當成官方原件、權利或真人驗收。
