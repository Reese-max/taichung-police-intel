# GovIntel AI－資料來源與取得狀態

文件更新 2026-10-05。active 來源以[核准 policy](../source-policy.approved.json)為準，catalog、入口可取得與 fixture 不代表正式啟用。最新具日期的 production 觀察見[CURRENT_STATUS](CURRENT_STATUS.md)。

## 已核准基線

| ID | 官方入口／用途 | 目前邊界 |
|---|---|---|
| S-004 | [臺中市議會議事日程](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=49) | `PRODUCTION_ACTIVE`；health、日期窗口與 freshness 分開。 |
| S-006 | [臺中市議會質詢順序](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=50) | `PRODUCTION_ACTIVE`；單源結果不能代表所有公共公告。 |
| S-007 | [議事錄 API](https://yishi.tccc.gov.tw/api/ProceedingsBackWeb/FrontList) | `PRODUCTION_ACTIVE`；這是需參數的 API endpoint，直接無參數 GET 回 400，不是可點開的文件；參數化 collector receipt 為`STALE`，不因 HTTP 成功改成新鮮。 |
| S-009 | [各項提案 API](https://yishi.tccc.gov.tw/api/Proposal/FrontList) | `PRODUCTION_ACTIVE`；這是需參數的 API endpoint，直接無參數 GET 回 400；保留缺日期、窗口與查詢範圍限制。 |
| S-029 | [市府議會專案報告](https://www.rdec.taichung.gov.tw/12047/12142/12145) | `PRODUCTION_ACTIVE`；本輪`FAILED`／`STALE`，LKG 不是最新內容，不能宣稱零新事件。 |
| S-010 | [市議會官方影音](https://www.tccc.gov.tw/) | 既有 evidence navigation，不是第六個定期 collector；transcript 只作導航。 |

五來源的 publication 仍`PARTIAL`。2026-10-05 01:13 Asia/Taipei 的公開觀察對應 generation `CR-DEMO-20261004-EVENING-SCHEDULE`，generated `2026-10-04T23:26:28+08:00`。這是時間化快照，不是完整涵蓋證明。

2026-10-05 22:18 Asia/Taipei 的只讀連結核對：上述議會首頁／日程／順序與兩個 data.gov.tw dataset 入口為 HTTP 200；S-029 官方報告 index 為 503，故保留 `FAILED`／LKG 缺口，不移除其原始官方 URL 或換來源冒充恢復。API 無參數 400 不等於 collector 已失效；需依其正確請求與資料 receipt 分別判斷。

## v6 首期四類候選

| ID／類別 | 證據 | 狀態與獨立驗收要求 |
|---|---|---|
| S-001 警政 | [catalog](../source-catalog.v2.json)、[observation workflow](../../../.github/workflows/candidate-source-observation.yml) | `CANDIDATE_CANARY`；列表／詳細內容、日期、完整性、失敗與權利。 |
| S-032 交通 | 同上 | `CANDIDATE_CANARY`；路段、日期、例外與更正依原機關公告。 |
| S-033 市政 | 同上；[市政新聞 83551](https://data.gov.tw/dataset/83551) | `CANDIDATE_CANARY`；83551 與同源市政公告不增加獨立佐證。 |
| S-031 消防 | 同上 | `CANDIDATE_CANARY`；短生命週期 snapshot 附觀測時間／涵蓋；消失不等於解除，列數不等於完整事件數。 |

部署基準 `main@562141e` 的獨立每日 workflow 觀察 S-001／S-032／S-033／S-031，漏了 catalog promotion-plan 的 S-019。本地整合已補入 S-019，驗證器要求五個計畫來源各有有效觀察日，避免從未出現在 receipt 的來源被漏驗；這沒有製造七日證據，也沒有把 v6 首期四類改成五類。完整性、權利／敏感內容、版本、核准 promotion 與公開 readback 另需通過。S-019 及四類候選均未啟用，沒有每日新增 100–200 筆的證據。

## v6 指定背景資料

| 資料 | 送件／現況 | 驗收與限制 |
|---|---|---|
| D1 [SEGIS](https://segis.moi.gov.tw/STATCloud/Index)112 年 12 月鄉鎮市區人口／戶數 | `IMPLEMENTED_NOT_PRODUCTION`；送件時未下載，本輪已取得固定 CSV、[驗證樣本](segis-112Y12M-taichung.verified.json)及本地 UI | 全國 368 記錄／臺中 29 區，代碼、人口／戶數、合計與 hash 已核對；`production_active=false`。不是目前人口、人潮或影響人數，真人用途效益未測。 |
| D2 [dataset5958](https://data.gov.tw/dataset/5958)警察機關地址名錄 | `DESIGN_ONLY`；metadata 200，官方 TGOS ZIP／原生頁面 403，資料列未取得 | [receipt](background-source-observations-2026-10-05.json)保留失敗；POINT_X/Y 不足以證明 CRS。地址／電話／座標內容未驗證，不能推論管轄／派遣／警力。 |
| S-026／S-028／其他 reference | catalog 與既有 canary／fixture | 各來源另需驗收，不因列名而變 production，背景資料不作即時事件。 |
| Taiwan Intel 媒體 | [discovery replay](../../../scripts/discovery-adapter.py)、[設計](../issue-27-discovery-adapter.md) | 固定 fixture 可重播；正式 live 與 14 日 shadow 尚需證據。72 小時、200 項、256KB 為交換上限，非每日新增。 |
| Twinkle | [混合策略](../TWINKLE_HYBRID_SOURCES.md)、[overlay](../twinkle-source-overlay.v1.json) | `DESIGN_ONLY`；與 direct-official 是取得路徑，不算兩個原始佐證。不是 v6 首期必備 runtime。 |

每筆結果保留官方 URL、文件版本／hash、發布／觀測／適用時間與未知欄位。健康、過期、部分涵蓋、來源失敗與有效零筆各自呈現；資料取得限制不得被高 fixture 分數掩蓋。

D1 由官方固定期別頁的原生 CSV 下載流程取得；同頁 JSON open service 回傳的是 **114Y12M**，不是 112Y12M，未用它覆寫歷史樣本。原始 CSV SHA256 `95500e06310098d4c194a26e4aeba00e7142d5c9e441a62fe18f71250d65496b`；變更聲明與授權／來源均在樣本。其他人口 dataset 也不能替代本期。D2 的原生瀏覽器與合法官方 referrer 仍未恢復存取，兩個 Twinkle 既有連線需重新認證；這些失敗不會被當成成功取得。
