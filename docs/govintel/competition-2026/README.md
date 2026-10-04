# GovIntel AI－公共資訊查詢與個人化追蹤平台

2026 內政黑客松的目前文件入口。送件 v6 修訂於 2026-10-01；本頁不是主辦受理、資格認定、成效或機關採用證明。日期、部署與待驗收功能以 [CURRENT_STATUS.md](CURRENT_STATUS.md)為準。

**首期任務：查公告 → 保存自己選擇的地區／議題／路段條件 → 再次開站看有原文依據的變更。**

既有公開基線是五來源的議會／市政查證與影音導覽。v6 的公開查詢、個人追蹤與評測候選 PR 仍須驗收，不把計畫書全文當作已上線功能。個人追蹤在同一瀏覽器保存；開站或重新整理才比對，不含關站背景推播或跨裝置帳號。

## 3–5 分鐘檢視

1. 讀[現況](CURRENT_STATUS.md)與[資料來源矩陣](DATA_SOURCE_MATRIX.md)：published 不等於 fresh，四類候選不等於 active。
2. 依[展示腳本](DEMO_SCRIPT.md)看「道路工程 v1 → 延長 v2 → 明文解除 v3」，固定測試資料明示 synthetic／offline。
3. 看[v6 頁次對照](PROPOSAL_MAPPING.md)與[驗收清單](ACCEPTANCE_CHECKLIST.md)，確認還缺哪些能力與證據。
4. 看[評測協定](EVALUATION.md)：A 官方人工、B 同範圍搜尋加一般摘要、C GovIntel、相同介面語意 AI 關閉消融，目標與結果分開。
5. 需要工程深入重播時看[JUDGE_PATH.md](JUDGE_PATH.md)、[架構](ARCHITECTURE.md)與[限制](LIMITATIONS_AND_SAFETY.md)。

## 目前可說的結論

- Oct 4 [run 37169331249](https://github.com/Reese-max/taichung-police-intel/actions/runs/37169331249)實際重新蒐集，五個公開檔案 hash 與 Gateway check 通過；稍後 [37212963039](https://github.com/Reese-max/taichung-police-intel/actions/runs/37212963039)也成功。
- 資料仍為 `PARTIAL`，S-007 陳舊、S-029 失敗；候選來源尚未 promotion，没有每日新增 100–200 筆的證據。
- v6 首期功能的 candidate／local acceptance、public deployment、真人成效各自驗收，不互相替代。
- D1／D2 取得與用途驗收、語意 AI、實際人員 A／B／C 研究仍未由目前 receipts 證明。
- v6 與補件寄送完成；主辦受理／更名回覆仍未確認。

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
| 舊版本與本次新增？ | [OLD_VS_NEW.md](OLD_VS_NEW.md)、[Historical](../historical/README.md) |

## 歷史規劃與證據

[GOVINTEL_PLAN.md](../GOVINTEL_PLAN.md)、[DATA_SOURCE_STRATEGY.md](../DATA_SOURCE_STRATEGY.md)保留原有技術規劃；首期定位與評測方法以送件 v6 對照為準。[PUBLICATION_RECOVERY.md](PUBLICATION_RECOVERY.md)與[VERIFICATION.md](VERIFICATION.md)是日期化歷史紀錄，不替代現在的 Actions／公開 receipts。Kiro deadline、影片與送件包位於 [Historical](../historical/README.md)。
