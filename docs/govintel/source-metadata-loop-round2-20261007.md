# S026／S010／S011：第二輪有限原始入口核查

[安全收據](source-metadata-loop-round2-20261007.json)保留實際 HTTP、原件 bytes/hash、官方 href 的發現來源及列表 metadata。本輪 pilot 是 **2026-09-01 至 2026-10-07（Asia/Taipei）**，不是全歷史或全功能驗收。只作正常 HTTPS GET、不跟隨 redirect，每來源至多6次、每次2MiB；本轮沒有 CLI／模型轉送官方正文，沒有下載影片、CDN、字幕或草案正文公開副本。原始 HTML/PDF 留在私人 scratch，repo只存安全 metadata。

| 來源 | 實際取得 | 還不能宣稱 |
|---|---|---|
| S026 法規草案 | 6次GET全部200：current、history第1/2/79頁、一筆detail及其同host ANN附件。歷史列表宣告785筆／79頁；抽樣25筆，公告日期2011-01-14至2026-08-25。附件為PDF、322,597B，hash已核對。 | current沒有可辨識列表table，因此不是零筆；只取3/79歷史頁，未證明pilot無草案、所有附件／截止／版本完整或正式使用准入。 |
| S010 個人質詢影音 | 官方www首頁明確href指向 `vod.tccc.gov.tw/index.asp`，原始iframe列表及一筆detail均200。列表快照辨識6筆detail URL，日期token為2026-10-01；日期角色未確認。 | 沒有全窗口分頁或總場次對帳、meeting/session identity、原始影片播放或時間軸驗收；沒有字幕／模型用途授權。 |
| S011 議事影音 | 官方首頁「歷次議程」href指向 `agenda-vod.tccc.gov.tw/v2_index.asp`，原始iframe列表及一筆detail均200。列表快照辨識6筆detail URL，日期token為9/11、9/15、10/2、10/5、10/6；日期角色未確認。 | 這是目前列表快照，不是pilot／所有會期完整；未知會議身份／版次不能猜，沒有影片播放或時間軸驗收。 |

兩項影音各有4個專屬GET，加上同一個共用官方www首頁收據，各記5次；共用首頁沒有重複下載。影音子網域是首頁明確宣告的正式入口，不是拒絕後繞行；收據保留 `discovered_origin_from_official_link` 及首頁hash。只讀同origin iframe HTML，沒有讀外部counter/script/CDN，也沒有自動跟隨第三方頁面。

三項的 `window_coverage`／`all_history_coverage` 都保留 **NOT_VERIFIED**，`production_active=false`，權利待真實覆核。未讀到窗口內草案或未辨識current list都不能產生官方零事件結論。

下一步具體工作：

1. S026 先建立 current list 的真實空狀態／列表合約，再以官方分頁日期界線證明pilot涵蓋；將每個draft ID的公告、截止、版本、所有附件逐一綁定。這輪ANN附件取得只證明bytes可讀。
2. S010／S011 依已發現iframe列表枚舉pilot的分頁或官方篩選端點，保留穩定item ID、日期角色及最後頁／總場次證據；不把六筆快照當總數。
3. 確認meeting/session與原始timeline映射後，分開驗收播放、正式引用，以及保存／字幕／模型用途；這些條件需要真實證據，不能由metadata200或派生ASR替代。
