# GovIntel AI－公共資訊查詢與個人化追蹤平台

2026 內政黑客松的目前文件入口。送件 v6 修訂於 2026-10-01；本頁不是主辦受理、資格認定、成效或機關採用證明。日期、部署與待驗收功能以 [CURRENT_STATUS.md](CURRENT_STATUS.md)為準。

**首期任務：查公告 → 保存自己選擇的地區／議題／路段條件 → 再次開站看有原文依據的變更。**

公開查詢與同瀏覽器追蹤已合併發布；2026-10-06 的匿名五檔 hash、Gateway 綁定與 15 項正式 browser 通過。正式來源正值查詢仍 RIGHTS_BLOCKED，模型／真人效果另需驗收。個人追蹤在同一瀏覽器保存；開站或重新整理才比對，不含關站背景推播或跨裝置帳號。

## 3–5 分鐘檢視

1. 讀[現況](CURRENT_STATUS.md)與[資料來源矩陣](DATA_SOURCE_MATRIX.md)：published 不等於 fresh，四類候選不等於 active。
2. 依[展示腳本](DEMO_SCRIPT.md)看「道路工程 v1 → 延長 v2 → 明文解除 v3」，固定測試資料明示 synthetic／offline。
3. 看[v6 頁次對照](PROPOSAL_MAPPING.md)與[驗收清單](ACCEPTANCE_CHECKLIST.md)，確認還缺哪些能力與證據。
4. 看[評測協定](EVALUATION.md)：A 官方人工、B 同範圍搜尋加一般摘要、C GovIntel、相同介面語意 AI 關閉消融，目標與結果分開。
5. 需要工程深入重播時看[JUDGE_PATH.md](JUDGE_PATH.md)、[架構](ARCHITECTURE.md)與[限制](LIMITATIONS_AND_SAFETY.md)。

## 目前可說的結論

- [2026-10-06 驗收包](closure-20261006/README.md)固定已發布 `5e2298c…`、release、checkpoint、資料 generation、policy／hash 與 browser；本地、CI、正式發布及真人各自留證。
- D1 固定 112Y12M 官方 CSV 與臺中 29 區已核對，正式 UI 值通過；不是目前人口或真人用途效益。D2 原檔 403、資料列／CRS 未驗證。
- 真實隔離 artifact 失敗→恢復通知已送達 #20，API 回讀 body hash 一致；正式 source/deploy recovery、raw purge、負載與成本仍需逐項留證。
- 四類候選未 promotion；交通／市政 7 有效日、消防 1 日、警政 0 日，權利及獨立完整性另驗。沒有每日新增 100–200 筆證據。
- 免費模型只完成 12 合成開發題診斷；官方保留集、語意 off 與真人 A/B/C 未執行，85%／90%／30% 仍是目標。
- v6／補件寄送完成；10/5 主辦確認補件收妥、未接受更名，原題名及代表資格仍待核對。

## 文件索引

| 讀者問題 | 文件 |
|---|---|
| 哪些 deployed，哪些只是候選？ | [CURRENT_STATUS.md](CURRENT_STATUS.md) |
| 如何對回送件 v6？ | [PROPOSAL_MAPPING.md](PROPOSAL_MAPPING.md) |
| 還缺哪些驗收？ | [ACCEPTANCE_CHECKLIST.md](ACCEPTANCE_CHECKLIST.md) |
| 如何展示與重播？ | [DEMO_SCRIPT.md](DEMO_SCRIPT.md)、[JUDGE_PATH.md](JUDGE_PATH.md) |
| 哪些來源、資料期別與限制？ | [DATA_SOURCE_MATRIX.md](DATA_SOURCE_MATRIX.md) |
| 資料流與責任邊界？ | [ARCHITECTURE.md](ARCHITECTURE.md)、[LIMITATIONS_AND_SAFETY.md](LIMITATIONS_AND_SAFETY.md) |
| 目標與成果是否混寫？ | [EVALUATION.md](EVALUATION.md)、[manifest template](evaluation-manifest.template.json) |
| 哪些本輪驗證已完成、還缺什麼？ | [VERIFICATION.md](VERIFICATION.md)、[來源稽核](SOURCE_RELIABILITY_REVIEW_2026-10-05.md) |
| 舊版本與本次新增？ | [OLD_VS_NEW.md](OLD_VS_NEW.md)、[Historical](../historical/README.md) |

## 歷史規劃與證據

[GOVINTEL_PLAN.md](../GOVINTEL_PLAN.md)、[DATA_SOURCE_STRATEGY.md](../DATA_SOURCE_STRATEGY.md)保留原有技術規劃；首期定位與評測方法以送件 v6 對照為準。[PUBLICATION_RECOVERY.md](PUBLICATION_RECOVERY.md)保留歷史根因；[VERIFICATION.md](VERIFICATION.md)分開本輪整合與舊紀錄，不替代正式 Actions／公開 receipts。Kiro deadline、影片與送件包位於 [Historical](../historical/README.md)。
