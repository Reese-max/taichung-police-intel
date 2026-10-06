# Issue #162 — 驗收 D1 歷史人口與 D2 警察機關名錄、座標系及背景用途

驗收基準（本單工作樹基底）：`main@2c660cef6f1b1fbf86b141306e54d06e0511c9f9`

本單為 v6 D1 歷史人口與 D2 警察機關名錄的驗收子項；沿用已有 D1 核對結果，不重建事件融合，亦不以其他人口期別替換計畫範圍。驗收結果分列 D1（已完成）與 D2（資源未取得）兩節。

## D1：固定期別 SEGIS 人口統計（已完成驗收）

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
