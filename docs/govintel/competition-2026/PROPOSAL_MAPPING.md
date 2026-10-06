# GovIntel AI－送件 v6 與實作對照

依據：2026-10-01 的 10 頁送件 v6；現況核對：2026-10-06。原件、姓名及私有報名資料保留，公開文件只列產品要求、頁次與可回查證據。產品工作題名為 GovIntel AI－公共資訊查詢與個人化追蹤平台；主辦未接受更名，原報名完整題名仍須核對。

| v6 頁次／要求 | 現有實作與證據 | 未完成與限制 |
|---|---|---|
| p1–2 公開資訊查詢 | 頁面已發布；[15 項正式 browser](closure-20261006/production-browser.json)核對條件、日期與缺口，Gateway release/hash 一致 | 正式准入 `RIGHTS_BLOCKED`；沒有核准官方內容正值查詢，不將空結果稱為沒有事件。 |
| p2／p4／p7 同瀏覽器追蹤 | 保存、修改、刷新、取消、清除在正式頁面通過 | baseline 不完整；真實官方新版本、已讀後再修訂及解除的完整情境待 #161；沒有推播／跨裝置。 |
| p3 道路 v1→v2→v3 | [固定 CI 樹](closure-20261006/ci-tree-binding.json)的 22 項合成 browser 可重播 | 10/7→10/9 只變結束日，09:00–17:00 與 A–B 範圍不變；10/9 17:00 明文解除。合成不是當日真實公告。 |
| p4 AI 查詢／抽取／修訂 | 研究、文件及 synthesis 已合併發布；正式停用 provider 並在傳輸前拒答；另有 [免費模型診斷](model-diagnostic-20261006/README.md) | 12 題同一合成開發集 11/12→12/12，非獨立保留集、非真人／官方語意 AI 成效。正式實測與成本待 #33／#48。 |
| p4 來源版本與快取 | runtime 拒絕混合 generation/hash，正式 Pages／Worker 綁定與 hash 核對 | 原文正值、真實後續版本及現況內容仍需准入來源；不洗新首次發布日。 |
| p5 D1 112 年 12 月人口／戶數 | 原送件時未下載；現在已取得固定 CSV、368 筆／臺中 29 區，正式 UI 值通過 | 歷史背景，不是目前人口、人潮或受影響人數；正式 promotion／真人理解效益另驗。 |
| p5 D2 警察機關名錄 5958 | metadata 可讀，取得限制已由 [PR #167](https://github.com/Reese-max/taichung-police-intel/pull/167)留證 | 原檔 403、資料列及 CRS 未驗；POINT_X/Y 與距離不證明管轄，不能稱已接入。 |
| p1／p5／p9 四類候選 | [來源覆核包](../source-reviews/2026-10-06/README.md)：S-032／33 各 7 有效日、S-031 1 日、S-001 0 日 | 全部未 promotion，權利與獨立完整性未核對；同源轉載不增加佐證。 |
| p5–6 媒體發現 | 固定 fixture 可 replay；正式畫面清楚標示示例 | Taiwan Intel live、授權、14 日 shadow 未驗。72h／200 項／256KB 是交換上限，非每日新增量。 |
| p7–8 人工標註、A／B／C 及語意 off | [評測協定](EVALUATION.md)與 [真人 protocol](human-study-protocol.v1.json)已備妥 | 實際事件／原件／兩名獨立覆核及同意試用人數為 0；訪談、答案與計時未執行。 |
| p8 85%／90%／30% | 仍是目標，零分母以 null 表示 | 不以工程測試數或 prompt 調整填成達成。 |
| p9 六週、人力、負荷、成本與備援 | [資源觀察](closure-20261006/resource-observation.json)、[固定新舊差異](OLD_VS_NEW.md)、離線 replay | 真實成員工時、起訖、預算、60 原件處理、負載與單位成本待輸入；備援不是持續最新服務。 |
| p9–10 告警、恢復與更正 | [真實 upload drill](closure-20261006/alert-drill.json)、送達／hash 回讀與靜態資料回報入口已驗 | 實際 deploy 故障、raw purge、來源恢復及維運接案人員仍待驗；通知送達不等於真人閱讀。 |
| p6／p10 作品、引用、資格與權利 | [依賴清單](closure-20261006/dependency-license-inventory.json)、[報名核對](registration-verification-20261006.md)、[新舊差異](closure-20261006/old-new-file-delta.json) | 原題名、代表資格、資料「或／與」、獲獎史、權利人同意待確認。原始 PDF 沒有 hyperlink annotations，新工作稿可補，未獲指示不重交。 |

10/5 已確認補件收妥、未接受更名；這與報名／資格核准分開。[CURRENT_STATUS](CURRENT_STATUS.md)、[驗收](ACCEPTANCE_CHECKLIST.md)與 [#62](https://github.com/Reese-max/taichung-police-intel/issues/62)列完成與外部 gate。原始 v6 的當日狀態保留，不以新功能改寫歷史送件內容。
