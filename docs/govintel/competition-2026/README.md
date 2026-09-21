# GovIntel AI｜內政黑客松交付與驗證入口

更新：2026-09-22。本文是參賽準備與交付計畫，不是獲獎保證、報名回執或正式採用證明。

**價值主張：讓承辦人知道跨機關公告改了什麼、哪份舊交班稿需要重核，並能回到原文確認。**

沿用 [GovIntel 主計畫](../GOVINTEL_PLAN.md)。README 既有 Kiro 競賽證據屬歷史原型；不得把其八月截止日期、舊影片或舊試用當成本次比賽的規則與成果。當屆資格、報名期間、評分比重、既有作品認定及格式須取得官方原件確認，本頁不重複未重新驗證的數字。

## 0. Current judge path（3–5 分鐘）

1. 先看本頁的 current status 與 limitation，再看根目錄 README 的 evidence links。
2. 在 checkout 重播三個核心 receipt：

   ```powershell
   python -X utf8 scripts/discovery-adapter.py self-check
   python -X utf8 scripts/located-facts.py self-check
   python -X utf8 scripts/verify-source-policy-integration.py --self-check
   ```

3. 執行 `npm --prefix apps/web run dev`，檢查首頁、source health、evidence drawer 與官方回查連結；若需完整工程驗收，再執行 `npm run check`。

目前公開 Pages 的版本/hash 尚未在本 checkout 重新核對；本地 receipts、HTTP 200 或 CI 成功都不替代 deployment/public reachability/user validation。

## 0.1 Current status snapshot

| 能力 | 固定狀態 | 可重播證據 |
|---|---|---|
| 五個議會／市政正式來源基線 | `PRODUCTION_ACTIVE`（repository baseline） | `source-policy.json`、source status、既有 full gate |
| Source Policy 跨 collector／Query Store／Health／UI | `IMPLEMENTED_NOT_PRODUCTION` | #49 receipt、`scripts/verify-source-policy-integration.py` |
| 官方文件版本→located fact→evidence/PublicEvent input | `IMPLEMENTED_NOT_PRODUCTION` | #48 receipt、`scripts/located-facts.py` |
| Taiwan Intel Dashboard discovery feed | `IMPLEMENTED_NOT_PRODUCTION` | #27 fixture、`scripts/discovery-adapter.py` |
| 受限 Query Gateway（Web／HTTP MCP／STDIO MCP first slice） | `IMPLEMENTED_NOT_PRODUCTION` | #30 runtime boundary、#47 current-checkout `24/24` receipt |
| 端到端健康 stage receipt／operator summary | `IMPLEMENTED_NOT_PRODUCTION` | `scripts/system-health.py`、`system-health.json`、Web health tests |
| 版本化 Unit／Role Profile 與角色化 Priority Brief | `IMPLEMENTED_NOT_PRODUCTION` | #12、`role-profiles.v1.json`、role profile tests、current-checkout browser `13/13` |
| S-001/S-019/S-031/S-032/S-033 擴源 | `CANDIDATE_CANARY` | #14/#22；尚缺完整 live canary/promotion |
| 排程發布、晨晚自然 run、匿名版本/hash | `BLOCKED` | #20；需正常 review/merge 與正式環境證據 |
| 真人成效／機關採用／得獎 | `NOT_RUN` / `UNVERIFIED` | `evaluation-manifest.template.json` 保持 null |

## 1. 這輪真的交付什麼

本分支目前包含前端快照年齡判斷、發布階段留證、bounded detail-recheck／handoff／事件融合核心、受限 Query Gateway 與測試，並包含 #20 的
`publication-state` 分支修復候選，**尚未合併／部署**。因此不能把候選流程
當成正式排程已修復，也不能把 metadata-only 查詢、local-first 核心或 fixture
重播寫成 production；完整全文抽取與 Twinkle client 仍未實作。

| 能力 | 此輪狀態 | 證據／後續 |
|---|---|---|
| 舊版 V2 文件異動、Top 3、來源回查 | 已有主線程式，仍需當下部署驗證 | 基準 `e1d081bd04824c062c7ee99e7d74f9e478240743` |
| 前端快照逾期／不完整／版本不一致提示 | 本分支實作，離線測試可執行 | `apps/web/lib/publication-freshness.mjs`、#21 |
| 推送／產物／部署階段結果與留證 | 本分支實作，實際 Actions 驗收待完成 | `.github/workflows/pages.yml`、#20 |
| main 分支保護導致推送阻塞 | 候選流程已改寫入 `publication-state`，不再直接推送受保護 `main`；尚未 merge／正式排程驗收 | #20；[後續修復邊界](PUBLICATION_RECOVERY.md) |
| 五個新聞／跨機關候選來源 | `CANDIDATE_CANARY`；有 bounded live observation，尚未 promotion | #14、#22 最新 canary receipt |
| 正文更正與摘要失效 | `IMPLEMENTED_NOT_PRODUCTION`；detail recheck 與 review/invalidation core 已有測試，尚缺正式 DB／7-day canary | #21、`docs/govintel/issue-21-detail-recheck.md` |
| 跨日追蹤、確認與交班版本 | `IMPLEMENTED_NOT_PRODUCTION`；local-first handoff state 已可重播，尚缺正式部署／使用者驗證 | #23、`docs/govintel/issue-23-handoff-flow.md` |
| 版本化角色排序與 relevance reasons | `IMPLEMENTED_NOT_PRODUCTION`；本地 deterministic/schema/browser 已驗證，尚缺正式部署／角色使用者驗證 | #12、`intel_v2/role_profiles.py`、`tests/test_role_profiles.py` |
| 跨來源事件融合、背景卡 | `IMPLEMENTED_NOT_PRODUCTION`；保守融合與回歸 fixture 已有，尚缺正式來源／部署驗證 | #24、`docs/govintel/issue-24-public-event-fusion.md` |
| Twinkle / public-apis | 來源策略／候選，不等於可用 API client | #14 |
| 當屆資格與報名、真人測試、AI 成效 | 待確認／待測 | 私有回執、匿名評測；不得預填成功 |

來源新增、人工回饋或新模型不能自動把上述狀態升為 production。每項狀態須附程式版本、執行日期、測試範圍與驗證依據。

## 2. 最小競賽版本：一件活動、三份文件、一次更正

市府活動公告、警察局交通管制、交通局公車異動，關聯到同一公共事件，但各自文件 ID、版本與證據仍保留。某份公告把管制開始由 17:00 改為 16:00，網址與標題不變：

1. 定期正文複查取得新版本，保存新舊證據。
2. 規則／AI 找出時間的實質變更，不把排版更新當重大警示。
3. 引用舊時間的交班稿 v1 標為 `NEEDS_REVIEW`，不能静默改寫。
4. 使用者點原文、核對後產生 v2；v1 仍能回查。
5. 顯示一份有實際任務用途的內政資料背景卡及其資料期別。
6. 新版本真正發布；取得部署結果與匿名 HTTP／publication ID／hash 比對後才說已上線。

公開 Demo 使用歷史／合成案例就清楚標示。時間數值是測試情境，不是現場公告或實測成果。

## 3. 三條並行工作線

| 工作線 | 順序與交付 | 完成證據 |
|---|---|---|
| 工程 | #20 → #22／PR #16 → #21 → #23／#24 | 真實發布＋入口回歸＋端到端情境；不以 PR 開啟／mergeable 代替 |
| 實務與評測 | 先訪談，再標註開發／保留集；功能完成後測真人任務 | 匿名工作流程、標註分母、耗時、漏件、修改紀錄 |
| 參賽與展示 | 資格／權利／資料條件、#25 文件、演練、備援 | 當屆官方原件與私有回執、功能差異、可核对報告 |

第一週不以增加 API 數量為驗收指標。先完成一條資料與使用者工作流程；MCP、多城市、持續直播、多人簽核與新資料庫不是此版的必要依賴。

## 4. 必須先確認的送件事項

代表人及學生資格、正式組隊與真實分工、跨單位認定、既有作品／曾獲獎情形、軟體與資料權利、當屆表單欄位和截止時點，各自記錄官方來源及確認日期。未取得者保持 `UNVERIFIED`，不沿用聊天紀錄作證。

個人報名資料、合作信件、試用者資料、內部紀錄與金鑰不進公開 GitHub。個人試用不等於機關採用；没有正式授權不使用機關標誌背書。既有程式授權不可擅自替所有權人選定。

## 5. 評測協定：產品價值與 AI 增益分開量

沿用主計畫的事件／時間分組與保留集，先以約 20 個事件／60 份文件試跑標註規格，再依人力擴大至主計畫目標；樣本量不是官方門檻。加入同名異地、不同日期、來源冲突、附件修改、取消、缺日期、來源故障及無實質變更的控制組。

- **A：人工現行流程**。可取得相同資料，資訊截止時間一致。
- **B0：參賽前既有 V2 原型**。比較整體產品改進，保存固定 commit。
- **B：同一新版工作流程但關閉語意 AI**。版本、搜尋、UI、來源及資料截止不變，用於隔離 AI 增益。不得故意弱化規則。
- **C：完整新版 GovIntel**。同樣資料與限制，啟用受控 AI 功能。
- **C−D：移除選定內政背景資料**。檢查是否真正改善指定任務，並非為了湊資料。

A 對 C 衡量整體工作流程；B 對 C 衡量 AI 額外貢獻。B0 不可以冒充與 C 只有模型差異的消融組。

兩位標註者先核對標準答案；分歧保存裁決。先固定任務成功標準，再計時。調整僅使用開發集；保留集按事件切分，不能把同活動的另一篇公告洩漏到開發集。歷史重播不得使用截止時間後的文件。

| 指標 | 定義 | 主計畫暫定目標／結果 |
|---|---|---|
| 重要異動精確率 | TP / (TP + FP) | 目標 ≥0.85；結果待測 |
| 重要異動召回率 | TP / (TP + FN) | 目標 ≥0.90；結果待測 |
| 事件合併 | 錯合併、漏合併、配對 P/R/F1 | F1 目標 ≥0.90；結果待測 |
| 引用支持 | 每個重要主張的版本、段落是否支持 | 目標無錯誤支持；另報覆蓋、拒答、待核對及分母 |
| 任務時間 | 閱讀至可交付，包含人工核對與修正 | 中位耗時下降目標 ≥30%，重要漏件不增加；待測 |
| 時效 | 來源變更→發現、發現→實際發布分開 | 官方修改時間未知則不計前者；不得只看 cron |
| 成本 | 呼叫／token／運算／儲存與人工維護分開 | 待實測；本計畫不授權付費 |

分母為零填 null／無可評估案例，不能填 100%。零錯誤只能說在 n 個案例中未觀察到，不代表永遠不會錯。全部拒答不能算高品質。人工處理的模型錯誤也要計入耗時與错误數。

真人試用先邀請 3–5 位經同意的相關使用者，以不重複、難度相近任務輪替方法順序。保存匿名 participant code，不蒐集不必要姓名。小樣本只報描述結果，不宣稱全機關或全臺泛化。

結果採 [evaluation-manifest.template.json](evaluation-manifest.template.json)，所有結果預設 null。試用原始資料另存私有位置，公開報告只放已核准的匿名彙總與可公開 fixture。

## 6. AI 與資料責任

AI 僅輸出重要變更與事件關聯候選，不指揮勤務、不自行採取外部行動。規則控制版本、引用、欄位、權限與發布資格。模型失敗保留原文與規則差異。

資料必須實際參與任務：地區關聯／背景與機關資訊需標統計期、來源及限制。人口不等於人潮；鄰近不等於管轄。Twinkle、官方直連、其他聚合服務拿到同一原始資料，不增加獨立佐證數。

## 7. 四分鐘內部演練腳本（非官方簡報時限）

| 時段 | 操作 | 看得到的證據 |
|---|---|---|
| 0:00–0:30 | 說明承辦人交班前核對公告的任務 | 匿名實務流程，不用概括「大家都很忙」 |
| 0:30–1:20 | 三份公告→一個活動 | 每份官方文件與合併理由；未完成只能用明示 fixture |
| 1:20–2:30 | 17:00→16:00，v1 待重核→v2 | 新舊原文、引用、人工確認紀錄 |
| 2:30–3:15 | 來源失效／官方矛盾 | 舊資料保留、時效限制、CONFLICT，不硬猜 |
| 3:15–4:00 | 展示 A/B/C 與維運結果 | 真實 n、耗時、誤報、漏件、拒答；未測留空 |

準備可操作的固定重播、錄影及來源截取，但不得把預錄操作說成即時服務。演練者包括實務人員、技術人員與第一次看到作品的人；AI Persona 只能幫忙找問題，不是正式使用者成效。

## 8. 交付閘門

G0 資格與權利有依據 → G1 發布可用 → G2 核心情境跑通 → G3 保留集與真人結果 → G4 凍結 Demo／備援與問答。

每關記錄 PASS／FAIL／NOT_RUN、版本及證據，不能用後一關的漂亮畫面抵銷前一關未完成。#20／#22 的已知缺陷優先；若資料或模型不可靠，縮小範圍，不擴大架構掩蓋。

## 9. 技術驗證

```bash
# 新測試已納入既有 apps/web/tests/*.test.mjs 的發現路徑
node --test apps/web/tests/publication-freshness.test.mjs apps/web/tests/publication-outcome.test.mjs
python -m unittest discover -s tests -p test_publication_outcome.py -v
# 完整 checkout 與依賴具備後仍必須跑原有 gate
npm run check
```

[本輪驗證與限制](VERIFICATION.md)。文件中的規劃及測試 fixture 不等同真人／provider／正式發布驗收。
