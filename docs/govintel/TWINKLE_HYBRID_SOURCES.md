# GovIntel AI｜Twinkle + 政府官方直連混合資料架構

版本：v1.0  
更新日：2026-09-09

GovIntel 採「官方即時／權威來源 + Twinkle 統一政府資料查詢」雙路徑。Twinkle 負責跨資料集搜尋、歷史統計、採購、立法院、法規、交通、人口/GIS 等背景查詢；官方網站/API負責即時事件、異動偵測與最終證據。

## 路由規則
- `OFFICIAL_FIRST`：警政新聞、消防災情、交通管制、議會議程等即時事件，直接抓官方來源。
- `TWINKLE_FIRST`：人口、GIS、歷史統計、採購歷史等，先用 Twinkle 查詢，必要時回官方完整資料。
- `DUAL_VERIFY`：重大法規、採購、法案狀態、重要統計，同時比對 Twinkle 與官方來源；不一致時顯示衝突。

## 第一批要接
1. 165 涉詐網站：Twinkle `public_safety` / dataset `176455` + data.gov.tw/警政署 direct。
2. 165 闢謠：Twinkle dataset `38262` + data.gov.tw direct。
3. 政府採購：Twinkle `pcc-tender` + PCC Open Data `web.pcc.gov.tw/tps/tp/OpenData`。
4. 立法院：Twinkle legislature + `data.ly.gov.tw` JSON/XML/CSV API。
5. 法規：Twinkle statute time machine + 全國法規資料庫／臺中法規官方來源。
6. 人口/GIS：Twinkle social/geo + SEGIS 官方網路服務。

## 第二批
- 交通：Twinkle transport + 臺中交通局／警政署／TDX 官方來源。
- 預算：Twinkle public finance + data.gov.tw／臺中主計處／警察局預算資料。
- 氣象環境：Twinkle 做歷史背景；即時狀態使用中央氣象署／環境部官方 API。
- 司法：Twinkle 先做研究背景；另建司法院 official canary 後才 production。

## 適時更新
- 消防／公共災情：官方 5–15 分鐘。
- 交通管制：官方 15–30 分鐘。
- 警政新聞：官方 30 分鐘。
- 市府／新聞局：30–60 分鐘。
- 議會會期資料：1–6 小時。
- 市政會議／法規草案：6–12 小時。
- 立法院：6–24 小時，會期提高頻率。
- 政府採購：每日一次 + 任務時 Twinkle 查詢。
- 人口／GIS／統計：依官方 metadata 統計期更新，每日只做 metadata diff。

Twinkle 不拿來做 5–15 分鐘高頻 polling，以免聚合延遲與 API 額度浪費。

## Provenance 契約
每筆資料至少保存：
```json
{
  "provider_path": "TWINKLE|DIRECT_OFFICIAL",
  "source_id": "...",
  "dataset_id": "...",
  "publisher": "...",
  "official_source_url": "...",
  "twinkle_dataset_id": "...",
  "license": "...",
  "published_at": "...",
  "effective_at": "...",
  "observed_at": "...",
  "statistical_period": "...",
  "content_sha256": "...",
  "freshness_status": "FRESH|STALE|VERY_STALE|UNKNOWN",
  "verification_status": "SINGLE_OFFICIAL|DUAL_MATCH|CONFLICT|UNVERIFIED"
}
```

## Fallback
- Twinkle 可用、官方失敗：只能顯示最近背景資料，標明官方目前無法驗證；不得宣稱「沒有新事件」。
- 官方可用、Twinkle 延遲：事件以官方為準，Twinkle 只做歷史比較，記錄 `TWINKLE_LAGGING`。
- 兩邊衝突：保留兩個版本，標 `CONFLICT`，重要事件需人工確認。

## Promotion gate
Twinkle-backed source 只有在以下全部完成後才能標 `PRODUCTION_ACTIVE`：
- 已讀 dataset metadata/schema。
- 保留 `source_url`、publisher、license、統計期。
- 有 direct official fallback，或清楚記錄沒有 fallback 的原因。
- 至少做過一次 Twinkle vs 官方樣本一致性測試。
- 有 stale / missing / conflict 狀態。
- 不用 collector 抓取時間冒充官方發布時間。
- 不把歷史／模擬資料冒充即時狀態。
- 通過敏感資料 disclosure gate。

核心定位：**需要即時異動時直連官方；需要跨資料集與歷史背景時用 Twinkle；重要結論永遠保留官方證據與可追溯 provenance。**
