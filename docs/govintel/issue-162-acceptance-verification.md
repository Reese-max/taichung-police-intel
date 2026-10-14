# Issue #162 — 驗收 D1 歷史人口與 D2 警察機關名錄、座標系及背景用途

本次工作樹基底：`c629f82a8b16825d5d89c204c7035dcba500a9ac`（2026-10-14）；下列官方取件與部署紀錄保留其原觀察時間，本次沒有重新取件或部署。

本單為 v6 D1 歷史人口與 D2 警察機關名錄的驗收子項；沿用已有 D1 核對結果，不重建事件融合，亦不以其他人口期別替換計畫範圍。D1 固定樣本的技術驗證已具備；來源權利、用途核准與真人效益仍需獨立驗收，整張 issue 尚未完成。D2 原始資源未取得，仍無法驗收。

## 本次修正：驗證收據不得覆寫原件

`reference-resource-validator.py --out` 原可指向 `--plan` 或 `--resource`，驗證成功後將輸入覆寫成 JSON。新增 CLI 回歸測試先重現 7 個失敗案例，再重用 `source-scope-contracts.py` 的 alias 檢查及 `reference-csv-audit.py` 的原子 JSON 寫入，拒絕同路徑、`..`、symlink 與 hardlink 輸入別名，保留原始 bytes／hash；其他檔案連到舊收據時亦不被連帶修改。

| 本次驗收項目 | 機制與界限 |
|---|---|
| 保存原件與取得驗證計畫 | 輸入 alias 回傳 2、stdout 無收據、兩份輸入 bytes 不變；distinct output 原子替換。 |
| D2 失敗收據與來源缺口一致 | metadata 200／原件 BLOCKED 403 的離線案例仍輸出 `resource_rows=null`、`completeness=false`、CRS `UNKNOWN`，回傳 2。 |
| D1 固定歷史身分 | 沿用既有 112Y12M／29 區／西屯人戶數與期別、hash、防 promotion 測試，沒有修改人口資料或公開收據。 |
| D2 真實機關資料與座標、權利與真人用途 | 尚未完成；離線 fictional fixture 不構成取得、CRS、用途或正式站驗收證據。 |
| #25／#62 同步 | 本地文件保留成功與 blocked 清單；外部 issue 更新由 driver 處理，本次未對外寫入。 |

TDD 指令：`python -X utf8 -m unittest discover -s tests -p test_reference_resource_validator_review.py -v`。修正前：`Ran 22 tests`、`FAILED (failures=7, skipped=3)`；修正後：`Ran 22 tests`、`OK (skipped=3)`。跳過的是 Windows 無建立 symlink 權限的 3 個子案例（WinError 1314）；同路徑、相對路徑與 hardlink 已實際執行。D1 web 2 項亦通過；完整驗證結果見本次 `.issue-loop-evidence.json`。

相關測試以 evidence 的 Git Bash 指令實際重跑，exit 0：validator 22 項、transport／人口 19 項、web 人口 2 項，0 failures，另有上述 3 個 symlink 子案例跳過。validator 測試數由 19 增至 22。`git diff --check` 與兩個變更 Python 檔案的 `py_compile` 均 exit 0；獨立 diff review 無阻擋發現。

完整 Python 測試指令 `python -X utf8 -m unittest discover -s tests -p "test_*.py" -v` 為 exit 1：1539 項、17 failures、86 errors、15 skips。失敗包含既有 Windows 缺少 `os.O_NONBLOCK`／symlink 權限／POSIX mode、子程序 cp950 編碼、安裝的 pypdf 6.18.0 與專案固定 6.19.0 不符，以及尚未提交的工作樹被 current-checkout gate 拒絕；未宣稱每項失敗均已在基底完整重跑。`test_binding_helpers_themselves_match_their_compiled_current_source` 在 admitted base 的獨立 `git archive` 副本亦重現缺少 `os.O_NONBLOCK`（exit 1），未更動任何其他分支。這些全套驗證限制不以相關測試的 exit 0 掩蓋，亦未擴大本單修改其他模組或共用 Python 環境。

`npm run check` 已完整執行並以 exit 1 結束：web 800 項中 797 通過、3 失敗（current-checkout 與兩個 PDF parser 案例）；其餘 Python 子 gate 的錯誤保留於 `runtime-evidence/issue-162/full-gate.log`。其中 Next.js production build 成功完成靜態頁面產生；不將 build 成功當成完整 gate 通過。`npm run check:gate0` 為 exit 0：`VERIFY_OK mode=gate0 required=164 specs=4 secrets=0`。專案沒有提供的 lint／coverage ratchet／CRAP／mutation 工具未另行安裝。

## D1：固定期別 SEGIS 人口統計（固定樣本技術驗證）

- 來源 ID：S-035，內政部統計處 SEGIS 112 年 12 月行政區人口統計（鄉鎮市區）
- 期別：112Y12M（2023 年 12 月），官方固定期別查詢頁的原生 CSV 下載按鈕取得
- 範圍：國內 368 筆／臺中市 29 區
- 原始 CSV hash：`95500e06310098d4c194a26e4aeba00e7142d5c9e441a62fe18f71250d65496b`（zip `d2648b7ab2649b1257099937d246d9e0639c86614ac2ad1accd355c53882d870`）
- 欄位／單位：COUNTY_ID／縣市代碼、TOWN_ID／鄉鎮市區代碼、TOWN／鄉鎮市區名稱、H_CNT／戶數、P_CNT／人口數、M_CNT／男性人口數、F_CNT／女性人口數、INFO_TIME／資料時間
- 彙總：人口 2,845,909、戶數 1,059,625、男性 1,391,681、女性 1,454,228（男 + 女 = 總人口；各區加總等於彙總，已核對）
- 驗收結果：`VERIFIED_HISTORICAL_BACKGROUND_SAMPLE`，`production_active=false`（非正式定期來源 promotion）
- 顯示：PublicQuery 背景頁（`PopulationBackground`）提供臺中市 29 區選單，顯示行政區代碼、統計期別、人口、戶數與授權原文；頁首與 `usage_limits` 明確標示為歷史背景，不稱目前人口、現場人潮、活動出席人數或受影響人數，亦不推論機關管轄或警力
- 期別防切：官方查詢頁 JSON open service 回傳的是 114Y12M，與 112Y12M 不符，不以此覆蓋固定歷史 CSV（`alternate_service_warning`）；validator 對 `period !== "112Y12M"`、總和變動、`production_active` 提升皆fail closed
- 測試：`apps/web/tests/population-background.test.mjs`（2 項通過）、`tests/test_live_canary_transport.py::PopulationBackgroundTests`（3 項通過）

## D2：各縣(市)警察(分)局暨所屬分駐(派出)所地址資料（資源未取得，無法驗收）

- 來源 ID：dataset 5958，`各縣(市)警察(分)局暨所屬分駐(派出)所地址資料`
- 中繼資料：HTTP 200（metadata sha256 `0c08d96a8613dc794780822e6ecb07c574916e27f2ddf1658ab0b44c56b74d7a`；更新時間 `2026-10-01 23:32:05`；授權代碼 `1`／免費）
- 原始資源：官方 TGOS ZIP／原生頁面 HTTP 403；未以 metadata 200 當取得成功，亦未繞過已明確拒絕的資源
- 欄位（僅 metadata 聲稱）：中文單位名稱、英文名稱、郵遞區號、地址、電話、POINT_X、POINT_Y（UTF-8）
- CRS：UNKNOWN。官方 metadata／schema 未聲明座標系，`POINT_X`／`POINT_Y` 欄位名稱本身不是 CRS 證據；座標驗證未執行（`coordinate_validation: NOT_RUN`）
- 資料列：未取得（`resource_rows: null`）；不偽造零列查無機關
- 狀態：`RESOURCE_UNAVAILABLE`，`production_active=false`，`coordinate_reference_system: UNKNOWN`

## 驗收清單逐項

| 驗收項目 | 狀態 | 說明 |
|---|---|---|
| D1 保存官方 dataset/resource ID、原始 CSV hash、欄位／單位、行政區代碼與 112Y12M 期別，核對臺中 29 區及彙總 | ✅ 完成 | `population-112Y12M.json` 含完整收據；原始 CSV hash 與 validator 核對一致；29 區代碼、人戶數與彙總已核對 |
| 正式公開範圍、來源權利與用途由 #39 核准 | ⏸ 外部 | 由 issue #39 核准，本單不替代 |
| D1 站上顯示正確人口／戶數與原文，明確為歷史統計 | ✅ 完成 | `PopulationBackground` 顯示固定期別人口／戶數與授權原文 |
| 不稱目前人口、即時人潮、受影響人數或危險度 | ✅ 完成 | 頁文與 `usage_limits` 明文化 |
| 不換成 114Y12M 服務後沿用舊身分 | ✅ 完成 | 114Y12M JSON service 因期別不符被拒絕，validator fail closed |
| D2 取得合法可讀官方資料、保存原始 bytes/hash、取得時間、欄位 | ❌ blocked | 原始資源 403，僅 metadata 200；資料列未取得 |
| D2 核對機關名稱、類型、行政區、公開地址／電話、缺值／重複、資料有效性及更新期別 | ❌ blocked | 資料列未取得，無法核對 |
| D2 從官方 metadata／schema 查明 CRS、單位、軸順序 | ❌ blocked | metadata 無 CRS 聲明；僅欄位名稱不足以證明座標系 |
| D2 機關／行政區查詢與背景卡連到原始名錄並顯示「機關參考，管轄另行確認」 | ❌ blocked | D2 尚未接入 |
| 距離不推論管轄、警力或派遣；缺可靠 CRS 時不提供假精準附近結果 | ✅ 完成 | 無 D2 查詢與距離計算，無此風險 |
| 取得／解析失敗保留 UNKNOWN/PARTIAL／原文與缺口，不以零列假裝查無機關 | ✅ 完成 | `validation_status: RESOURCE_UNAVAILABLE`，`resource_rows: null`，與 receipt 一致 |
| #33 另測 D1／D2 查找耗時、欄位正確性、期別／管轄限制理解 | ❌ 分開 | 由 issue #33 驗收；背景效益不併入異動辨識／AI 成效 |
| 成功與 blocked 項同步 #25／#62 | ⏸ 待執行 | D1 成功、D2 blocked 已紀錄；同步至 #25/#62 |

## 證據

- [D1／D2 日期化取得紀錄](competition-2026/background-source-observations-2026-10-05.json)
- [D1 驗證樣本](competition-2026/segis-112Y12M-taichung.verified.json)
- [D1 公開資料與收據](../../apps/web/public/data/population-112Y12M.json)
- [v6 p5 對照](competition-2026/PROPOSAL_MAPPING.md)、[資料來源矩陣](competition-2026/DATA_SOURCE_MATRIX.md)

## 待辦（受外部因素阻擋）

- D2：官方 TGOS 資源 HTTP 403 未取得可讀原始資料；需 #163 以原件確認行政要求並恢復資料存取後，方可繼續核對資料列、地址電話與 CRS。未取得前不宣稱已接入（不依賴 metadata 200、fixture 或座標欄名）。
