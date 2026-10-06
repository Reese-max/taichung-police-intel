# GovIntel AI－送件 v6 與實作對照

依據：2026-10-01 修訂、10 頁的已寄送構想書 v6。原始送件檔與個人報名資料保留在私有位置，公開 repo 只列產品要求、頁次與工程證據。原始 v6 是提案，不因後續程式進展而改寫已送件內容。

產品與已送 v6 名稱：**GovIntel AI－公共資訊查詢與個人化追蹤平台**；主辦未接受提案更名，正式競賽登記名稱待原報名紀錄確認。首頁定位依產品任務；原有議會備詢／交班可保留作基礎與後續延伸，不代替首期民眾公開查詢流程。

| v6 頁次／要求 | 實作／證據 | 驗收與目前限制 |
|---|---|---|
| p1–2 民眾與研究／業務人員的公開查詢 | [PR #124](https://github.com/Reese-max/taichung-police-intel/pull/124)、既有受限 [Gateway](../../../scripts/query-gateway.py) | 地區、議題、路段、關鍵字、期間可見；結果附原文與時間；歧義不可暗改。候選 query UX 尚無 production acceptance。 |
| p2、p4、p7 同一瀏覽器追蹤、已讀、重開站 | [PR #125](https://github.com/Reese-max/taichung-police-intel/pull/125) | 保存／修改條件、重開站、手動刷新、取消／刪除；來源缺口揭露；清除瀏覽器資料可能失去清單。沒有關站推播、跨裝置帳號或敏感身分側寫。 |
| p3 測試道路 v1 原公告、v2 延長、v3 明文解除 | [展示腳本](DEMO_SCRIPT.md)、待驗收候選及固定 replay | 10/7→10/9 只變結束日，9:00–17:00 與 A–B 範圍不變；排版／轉載不多列重要提示；日期到期、來源失敗或消失不能推論解除。全部明示合成。 |
| p4 AI 理解查詢、抽取時間與範圍、候選融合、修訂判讀 | 既有 [located facts](../../../intel_v2/located_facts.py)、[fusion](../../../scripts/public-event-fusion.py)核心 | deterministic／keyword／fixture 不是已完成 semantic AI；模型、prompt、資料 hash、失敗與成本須另留 receipt。目前沒有 provider quality 或 AI 增益證據。 |
| p4 查詢快取依來源版本待更新／重算 | 版本／hash 基礎、待驗收 query candidate | 源版本改變時不得沿用錯誤舊摘要；未受影響內容不任意改寫，歷史補抓不是今日新增。 |
| p5 D1 112 年 12 月鄉鎮市區人口／戶數 | v6 送件時尚未下載；本輪已由官方固定期別 CSV 取得[樣本](segis-112Y12M-taichung.verified.json)，並提供 `/public-query/` 的臺中 29 區選單 | 全國 368 記錄、數值／代碼／hash 已核對；`production_active=false`，真人用途效益未測。JSON service 回傳 114Y12M，未冒充指定期別；不能估計現況人口／人潮。 |
| p5 D2 各縣市警察機關地址 dataset 5958 | 送件時尚未下載；本輪 metadata 200，但官方 TGOS ZIP／原生頁面 403，[receipt](background-source-observations-2026-10-05.json) | 資料列、電話／地址、座標／CRS 尚未核對。POINT_X/Y 不能證明座標系統；顯示「機關參考，管轄另行確認」，鄰近不能推論管轄或警力。 |
| p1、p5、p9 臺中警政／交通／市政／消防四類候選 | [矩陣](DATA_SOURCE_MATRIX.md)、[獨立觀察](../../../.github/workflows/candidate-source-observation.yml) | S-001／S-032／S-033／S-031 尚未 active；各自七個有效觀察日、完整性／權利／失敗驗收與核准 promotion。83551 與同源市政資料只算一個來源。 |
| p5–6 Taiwan Intel 待查媒體發現層 | [discovery adapter](../../../scripts/discovery-adapter.py)、[設計](../issue-27-discovery-adapter.md) | 固定 fixture 可 replay，未證明正式 live 介接；十四日影子觀察另驗。72 小時／200 項／256KB 是交換上限，不是每日新增量。 |
| p7–8 人工標註與 A／B／C 比較 | [PR #126](https://github.com/Reese-max/taichung-police-intel/pull/126)、[協定](EVALUATION.md)、[template](evaluation-manifest.template.json) | A 官方人工；B 同範圍搜尋加一般摘要；C 完整產品；同介面語意 AI off 消融另列。事件切分／同 cutoff，真人與合成分開；結果仍 `NOT_RUN`。 |
| p8 目標 precision85%、recall90%、相對 A 耗時−30% | [EVALUATION.md](EVALUATION.md) | 都是目標，未測不填成功；零分母 null，來源取得缺漏另列，不等於全網召回。 |
| p9 未通過來源時交離線 query／tracking replay | [DEMO_SCRIPT.md](DEMO_SCRIPT.md)、[驗收](ACCEPTANCE_CHECKLIST.md) | 固定 cutoff／fixture 標示清楚；不能宣稱持續提供最新變更。成功網站發布不能替代來源驗收。 |
| p10 安全、隱私、外部輸入與正式公開驗收 | [LIMITATIONS_AND_SAFETY.md](LIMITATIONS_AND_SAFETY.md) | 公開 repo 不保存私人追蹤、報名信件或原始使用者資料；不做個人危險／嫌疑評分、自動派遣。 |

## 外部確認界線

v6 補件已寄出；主辦於 2026-10-05 確認收妥並拒絕更名。原報名完整名稱與是否須改回原名重送已詢問，尚待指示，見[日期化核對](ORGANIZER_STATUS_2026-10-06.md)。當屆資格、跨單位認定、權利、評分與期限需官方原件／私有回執，不從 repository 或收妥回覆推定。
