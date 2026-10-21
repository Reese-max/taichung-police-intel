# #166 既有作品、增量與權利覆核清冊

核對基準：`c629f82a8b16825d5d89c204c7035dcba500a9ac`，2026-10-14。
[機器可讀清冊](work-rights-review.v1.json)是公開證據索引，`status=REVIEW_REQUIRED`、`issue_closable=false`；#166 仍須保留 open。沒有替團隊、貢獻者、來源或主辦核准權利，也沒有新增作品 LICENSE。

## 固定版本與實際驗收

| 版本 | 固定內容與可回查證據 | 可主張範圍 |
|---|---|---|
| 既有原型 | [`562141e…` 程式樹](https://github.com/Reese-max/taichung-police-intel/tree/562141e396c693e115b3fce10594c434c401a58e)、[歷史送件／展示入口](https://github.com/Reese-max/taichung-police-intel/blob/c629f82a8b16825d5d89c204c7035dcba500a9ac/docs/govintel/historical/README.md) | 既有文件與展示基準，不推定曾獲獎或有再利用權。 |
| 已保存驗收的新增版本 | [`5e2298c…` 程式樹](https://github.com/Reese-max/taichung-police-intel/tree/5e2298c8db12ec2631dbd8dac11c14599133dcc6)、[功能差異](https://github.com/Reese-max/taichung-police-intel/blob/c629f82a8b16825d5d89c204c7035dcba500a9ac/docs/govintel/competition-2026/OLD_VS_NEW.md)、[逐檔差異](closure-20261006/old-new-file-delta.json) | 查詢、條件追蹤、版本依據、研究／AI 與評測逐項區分既有、新增及未驗收。 |
| 本輪盤點基準 | [`c629f82…` 程式樹](https://github.com/Reese-max/taichung-police-intel/tree/c629f82a8b16825d5d89c204c7035dcba500a9ac) | 清冊固定此版內容；沒有把較早的部署收據套用到本版，未驗收能力不計成完成增量。 |

清冊的 `evidence` 逐筆保存盤點版本出處、路徑及 **Git blob 原始 bytes 的 SHA-256**，避免 Windows CRLF checkout 造成假差異。測試核對本次 checkout 的同一路徑仍與盤點 bytes 相同，CI 不需要下載深層歷史；歷史 commit URL 保留實際出處。[CI tree](closure-20261006/ci-tree-binding.json)、[正式 browser](closure-20261006/production-browser.json)及 [Gateway 收據](closure-20261006/production-query.json)只證明其內記錄的版本與範圍；正式來源權利受限、模型效果與真人試驗仍分開。清冊另存三個版本的 Git tree SHA，本輪以實際 Git objects 核對，例行測試以既有 CI tree 收據核對已驗收版本。

## 權利與實際許可

| 類別 | 已固定的盤點依據 | 仍缺的實際證據 |
|---|---|---|
| 專案程式／貢獻 | 固定 Git 歷史及原型／增量差異；repo 沒有作品 LICENSE | 實際權利人、貢獻／職務及第三方義務、可用範圍與同意。公開 repo 或 commit 作者不等於權利人同意。 |
| 外部依賴 | [歷史宣告清單](closure-20261006/dependency-license-inventory.json)：70 個 npm lock entries、7 個 Python 直接／binary extra；清冊亦綁定兩份 lockfile 及 requirements | 實際交付環境的全部 Python 間接依賴、optional binary 選用、實際附帶 licence／NOTICE 原文與歸屬。10/6 安裝觀察及 hash 不是本輪安裝結果，也不是完整交付 notices。 |
| 資料／全文／片段／模型傳輸 | [#39 逐來源覆核包](../source-rights-review-packet-20261007.json)、[retention policy](../retention-rights-policy.v1.json)、[source policy](../source-policy.approved.json)；同一組 20 個來源 | 真實條款／覆核人／日期／逐用途決定。測試重用既有 `compile_governance()` 與 `formal_admission()` 比對，不建立第二份授權政策；未啟用全文、片段或模型傳輸。 |
| 模型輸出 | [合成診斷索引](model-diagnostic-20261006/diagnostic-index.json)及兩次 `predictions.jsonl` 均有固定 hash | 實際適用供應商條款、輸出使用／歸屬及展示許可。執行收據與評分不構成授權或正式 AI 效益。 |
| 展示圖像／影音／文件 | 逐一列 1 個既有 `demo-video.mp4` 與 14 個測試 PDF，固定內容 hash；本基準無獨立 raster 圖檔 | 影片內圖像、音樂、字型、畫面、肖像／聲音及外部連結／嵌入媒體的實際許可與歸屬。測試 PDF 列入來源盤點，不推定作為參賽素材；私有簡報與未追蹤素材不在此掃描範圍。 |

所有類別維持 `rights_holder=null`、`status=NOT_VERIFIED`、`consent_receipts=[]`。依賴宣告已知的授權名稱，不會被轉成作品、來源或所有權核准。

[官方辦理計畫](https://ws.moi.gov.tw/Download.ashx?n=MjAyNuOAjOWFp%2BaUv%2Bm7keWuouadvuOAjea0u%2BWLlei%2BpueQhuioiOeVqy5wZGY%3D&u=LzAwMS9VcGxvYWQvNDAwL3JlbGZpbGUvOTAwOS8zNDA1ODEvMzYyMTcwNWMtZTkyNi00Zjc2LWE5YzktODhjZjk5OWYxOWUxLnBkZg%3D%3D)第 9 頁於本輪讀取：既有產品須說明新增內容；曾獲獎作品另有修改程度及主辦認定要求，並要求參賽者有權同意推廣／結案用途。這項文件核對不表示團隊已取得同意。

獲獎史維持 `UNKNOWN`；請以團隊實際參賽／獲獎紀錄確認是否適用，適用時向主辦提出固定版本與功能／驗收差異。`official_modification_percentage=null`、`organizer_determination=PENDING`，不從檔案數、行數或測試數宣稱符合 50%。參賽、官方推廣及計畫結案三種用途均為 `NOT_VERIFIED`；簽署原件、姓名及個資留在私有保管處，公開清冊只接受經授權的遮蔽收據識別碼、範圍與核對結果，目前沒有此類收據。

## 驗收對照與關閉界線

| #166 條件 | 本輪結果 |
|---|---|
| 固定原型、文件、畫面與新增功能／程式／實際驗收差異 | 已建立版本／hash 索引，沿用歷史功能及實際 browser 收據；本輪未重新驗收產品或部署。 |
| 以真實紀錄確認獲獎史及必要主辦認定 | 未完成；所需紀錄及主辦結論均待取得。 |
| 程式、貢獻、依賴、資料、模型輸出及圖像／影片權利 | 已列六類依據與逐項缺口；全部實際權利人同意與完整交付 notices 未完成。 |
| 與 #39 的逐來源／用途權利一致 | 20 個來源及既有 governance／formal admission 由測試比對；不代填同意、不修改 canonical policy。 |
| 推廣／結案用途的真實權利及同意 | 未完成；只列所需同意，私人原件不入 repo。 |
| #25 引用、新舊差異及 #62 gate 固定可點擊 | [#25 頁次對照](https://github.com/Reese-max/taichung-police-intel/blob/c629f82a8b16825d5d89c204c7035dcba500a9ac/docs/govintel/competition-2026/PROPOSAL_MAPPING.md)、[#62 交付 gate](https://github.com/Reese-max/taichung-police-intel/blob/c629f82a8b16825d5d89c204c7035dcba500a9ac/docs/govintel/issue-62-release-closure.md)及清冊 `closure_blockers`；未驗收能力保留待辦。 |

重播（Git Bash；唯讀、不聯網）：

```bash
python -X utf8 -m unittest discover -s tests -p test_work_rights.py -v
```

5 項測試驗證固定版本／hash、六類權利與官方用途、獲獎史未知、逐來源 canonical 一致性、完整受追蹤素材及模型輸出枚舉；已納入 `npm test`／`npm run check`。綠燈只表示清冊一致，不能關閉缺少真實同意的 #166。後續新增版本、素材或權利證據時須更新清冊、重新核對並保留歷史證據。

本輪工程驗證：

| 指令／檢查 | 結果 |
|---|---|
| 上述 unittest 指令，從 Git Bash 執行 | 實作前 exit 1、`FAILED (errors=5)`（清冊不存在）；實作後 exit 0、5 passed／0 failed。 |
| 同一測試在本機 depth=1、LF checkout 重播 | exit 0、5 passed；不需要歷史 Git objects，不受 checkout 換行影響。 |
| `npm run check` | exit 1，10 個驗證群組失敗；Web 為 797 passed／3 failed，內含 PDF parser 與 runtime 環境失敗。Next.js production build 通過；不能宣稱 full suite 綠燈。 |
| admitted base 的既有失敗最小重現 | 未修改的 `c629f82…` 重現 CSV／JSON audit 的 38 項測試中 2 failures／1 error／2 skipped（Windows 模式與 symlink），另 3 個測試重現目錄同步、Unix deadline 及 `O_NONBLOCK` 錯誤。本輪沒有擴大修正這些既有跨平台問題。 |
| `node --check scripts/verify-project.mjs`、`git diff --check` | 各 exit 0；獨立唯讀覆核的兩個跨 checkout 問題已修正，剩餘 findings 為 0。 |

專案沒有另設 lint、coverage ratchet、CRAP 或 mutation gate，本輪未新增工具。未推送、開 PR、部署或取得任何真人權利核准；driver 重播指令及紅燈摘錄保存在 repo 根目錄 `.issue-loop-evidence.json`。
