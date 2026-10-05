# 文件入口核對：2026-10-05

本次核對基於 `cade5a78c7b384604284b9e926b3269aa01fcd22`。只修正文件與證據的解讀；#25 的補充留言所列資料取得、完整展示與真人驗收尚未全部完成，Issue 保持 open。

| 文件要求 | 核對證據與範圍 |
|---|---|
| 現行首頁 | 根 README 第一屏為 GovIntel plan v6、首期任務與固定狀態表。 |
| 歷史競賽 | [歷史入口](../historical/README.md)保留 Kiro 資料與 2026-08 日期，表單現已關閉；不作現行報名入口。 |
| 功能狀態 | [CURRENT_STATUS](CURRENT_STATUS.md)區分五種固定狀態；PR #103／#124／#125／#126 已合併程式，但不等於 production。 |
| 發布阻塞 | #20 的未完成驗收仍見 README、狀態頁及限制頁；舊成功 run 只作日期化證據。 |
| 展示重播 | [DEMO_SCRIPT](DEMO_SCRIPT.md)提供四分鐘路徑；[JUDGE_PATH](JUDGE_PATH.md)列環境、完整 gate、核心 self-check 與 same-checkout runtime 命令。合成道路與官方背景資料分開標示。 |
| 新舊差異 | [OLD_VS_NEW](OLD_VS_NEW.md)區分舊議會證據旅程與 v6 查詢／個人追蹤。 |
| 評測 | [EVALUATION](EVALUATION.md)結果與目標分開；真人、語意 AI 與獨立保留集仍為 NOT_RUN/null。 |
| 連結 | 基準版本 20 份 Markdown 的 141 個相對連結皆有目標；修正後 README／參賽專區／歷史入口共 17 份 Markdown 的 147 個相對連結再次核對皆有目標；12 個公開 HTTPS 位址實際唯讀查核，狀態與雜湊見[讀回紀錄](document-link-readback-20261005.json)。S-029 本次 503 明列缺口；兩個議會 API 裸 GET 為 400，已標示需參數的 API 端點。其餘 GitHub 證據以授權 API 核對。 |
| 依賴／安全契約 | 改動只含文件與唯讀紀錄，未變更 runtime dependency、workflow、來源政策或 evidence/safety contract。 |

本地完整 `npm run check` 返回 `VERIFY_OK mode=full required=134 specs=4 secrets=0`。四個核心命令 source-policy integration、located-facts、public-event-fusion、handoff-state 均執行成功。使用既有 Python requirements 環境與 Node 22，沒有 API key／provider 呼叫。CI 與本次 same-checkout runtime 的結果依 PR 實際 receipt，不在本文件預填成功。

12 個公開連結於 2026-10-05 22:18 Asia/Taipei 核對；HTTP 狀態不推出資料新鮮、現場安全、來源 promotion 或機關採用。原始網頁內容未保存，歷史來源 URL 保留供追溯。
