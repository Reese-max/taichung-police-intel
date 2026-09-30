# GovIntel AI｜2026 評審可重播入口

查核日：2026-09-29；程式基準：[main `bd9adc647a2a91b54487d96869bd2d56eacea26e`](https://github.com/Reese-max/taichung-police-intel/commit/bd9adc647a2a91b54487d96869bd2d56eacea26e)。這是產品與參賽準備說明；當屆資格、截止日、評分權重、報名回執仍須以主辦官方原件和團隊私有回執確認，本文不填未驗證的數字。

## 1. 任務、證據與版本

承辦人要從公開的議會、市府、警政與交通公告辨識同一活動的實質更正，回查每份官方原文，並知道舊交班稿是否要重核。現行可公開操作的產品主線是**五來源備詢簡報、來源健康與官方影音證據導覽**；跨機關事件與交班是可重播的候選流程，尚非正式服務。資料只取公開資訊；內部勤務、派遣、個案資料與未核准全文不進公開輸出。見[產品計畫與資料界線](../GOVINTEL_PLAN.md)及[來源權限規則](../retention-rights-policy.v1.json)。

| 固定狀態 | 目前能力 | 可核對來源與限制 |
|---|---|---|
| `PRODUCTION_ACTIVE` | S-004／006／007／009／029 的五來源政策、已部署的靜態展示與證據導覽 | [catalog](../source-catalog.v2.json)、[runtime source policy](../../../apps/web/public/data/source-policy.json)、[公開網站](https://reese-max.github.io/taichung-police-intel/)。此狀態指來源納入正式集合，不表示今日資料新鮮。 |
| `IMPLEMENTED_NOT_PRODUCTION` | [官方文件 locator](../issue-48-official-document-replay.md)、[PublicEvent 融合](../issue-24-public-event-fusion.md)、[本地交班版本](../issue-23-handoff-flow.md)、[read-only Query Gateway](../issue-30-runtime-boundaries.md) | 程式、fixture 與本地驗證可重播；正式跨來源寫入、真人使用及已部署整合仍未驗收。 |
| `CANDIDATE_CANARY` | S-001／019／031／032／033 擴源 | [bounded observations](../issue-22-publication-wiring.md)及仍開啟的 [PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16)；沒有七日 promotion／正式發布收據。 |
| `DESIGN_ONLY` | [Twinkle＋官方直連路由](../TWINKLE_HYBRID_SOURCES.md)、完整「最新資訊→正文複查→正式交班」的服務迴路 | [#21 detail-recheck core](../issue-21-detail-recheck.md)已有本地實作，**但**完整 live 複查、Twinkle client 與正式交班服務未接通。設計或 core 測試不等於上線。 |
| `BLOCKED` | 新資料的穩定排程發布與正式驗收 | [#20](https://github.com/Reese-max/taichung-police-intel/issues/20)：[PR #26](https://github.com/Reese-max/taichung-police-intel/pull/26)已修復 protected-main 直推並合併；最新 [2026-09-29 自然排程](https://github.com/Reese-max/taichung-police-intel/actions/runs/36511458406)的完整 gate 失敗，persist／upload／deploy 均跳過。仍缺成功 EVENING、新晨晚配對與 artifact-upload 失敗演練。 |

**讀數時點：**2026-09-29 匿名 HTTPS 讀取[公開 `source-status.json`](https://reese-max.github.io/taichung-police-intel/data/source-status.json)為 HTTP 200、`generated_at=2026-09-24T23:18:12+08:00`、`collection_run_id=CR-DEMO-20260924-EVENING-SCHEDULE`。當時五來源中 S-004／007／029 的 `freshness_status=STALE`；這是公開資料本身的狀態，不應從 HTTP 200 推論今日蒐集成功。此 checkout 的[保存快照](../../../apps/web/public/data/source-status.json)更早，`generated_at=2026-09-11T08:23:26+08:00`。[主幹 verify](https://github.com/Reese-max/taichung-police-intel/actions/runs/36487310563)對同一 commit 成功執行 22 步，但最新自然排程失敗的[兩份 artifact 與 job steps](https://github.com/Reese-max/taichung-police-intel/actions/runs/36511458406)只能證明失敗有留證，不能當成新部署。評審若稍後開啟，須重讀上述時間、hash／generation 與 #20 的最新結果。

## 2. 官方來源矩陣與系統邊界

下列網址來自版本化的[來源 catalog](../source-catalog.v2.json)；S-007／009 是 API endpoint，直接在瀏覽器開啟未必得到可閱讀的清單。官方入口不表示每筆 fixture 已有可驗證的個案 locator。判定今日事件時仍須檢查單筆文件版本、發布時間、來源缺口與[官方證據 gate](../../../apps/web/lib/answer-evidence-gate.js)。

| 正式 ID | 官方入口 | 本次公開資料狀態 |
|---|---|---|
| S-004 議事日程 | [臺中市議會](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=49) | 舊資料／`STALE` |
| S-006 質詢順序 | [臺中市議會](https://www.tccc.gov.tw/wb_download13.asp?uno=&cno=50) | 公開快照內 `FRESH`，但整批快照仍過期 |
| S-007 議事錄 | [議事資訊系統](https://yishi.tccc.gov.tw/api/ProceedingsBackWeb/FrontList) | 舊資料／`STALE` |
| S-009 各項提案 | [議事資訊系統](https://yishi.tccc.gov.tw/api/Proposal/FrontList) | 公開快照內 `FRESH`，但整批快照仍過期 |
| S-029 議會專案報告 | [臺中市政府研考會](https://www.rdec.taichung.gov.tw/12047/12142/12145) | 舊資料／`STALE`；新排程連線／驗證仍有阻塞 |

```text
五個核准官方來源 → bounded collector → source-status + feed + V2 brief
                  → publication-state checkpoint → 靜態 Pages + 官方證據入口
候選來源／Twinkle → canary、審查與 promotion gate；不直接加入正式五來源
文件版本／locator → 本地 PublicEvent、交班與 Query Gateway 重播；未取代正式發布
```

架構的來源、時間、hash 與 LKG 契約見[資料策略](../DATA_SOURCE_STRATEGY.md)、[發布 checkpoint](../issue-20-runtime-checkpoint.md)及[主線 workflow](../../../.github/workflows/pages.yml)。`source_health=PASS` 與 `window_completeness=COMPLETE_ZERO` 只適用於當次受限觀測，不能推論整個世界沒有事件。

## 3. 四分鐘展示（本地固定版本；非 live 新能力）

在此 commit 的乾淨 checkout，先用 Node.js 20+／Python 3.11+ 執行 `npm ci --prefix apps/web`，再依表操作；無需模型 key、付費服務或正式資料寫入。若只閱讀公開網站，請跳過 fixture 操作並直接檢查公開資料時間。

| 時間 | 實際操作 | 應看到／不可宣稱 |
|---|---|---|
| 0:00–0:40 | 開[公開展示](https://reese-max.github.io/taichung-police-intel/)或 `npm --prefix apps/web run dev` 後的 `http://localhost:3000`；讀 priority brief 與來源健康。 | 五來源、官方連結、gap、資料時間；公開與 checkout 版本可能不同。 |
| 0:40–1:20 | 開[公開 source status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)，比較本地[快照](../../../apps/web/public/data/source-status.json)的 `generated_at` 和 S-029。 | 9/24 與 9/11 的版本差；不能說「即時」。 |
| 1:20–2:35 | 本地首頁找 `FIXTURE_ONLY` PublicEvent 卡，打開三份文件與前後版本；或執行 `python scripts/public-event-fusion.py --self-check`。 | [fixture JSON](../../../apps/web/public/data/public-event-demo.json)會顯示同一事件、時間衝突與來源連結；其文件、locator 為合成，不能作真實案證據。CLI 預期 `PUBLIC_EVENT_FUSION_SELF_CHECK_OK`。 |
| 2:35–3:20 | 執行 `python scripts/handoff-state.py self-check`；指出舊稿 v1 在版本變更後要重核才可產生 v2。 | 預期 `HANDOFF_DEMO_OK`；這是本地 deterministic replay，沒有正式多使用者持久服務。 |
| 3:20–4:00 | 回到[#20 執行紀錄](https://github.com/Reese-max/taichung-police-intel/actions/runs/36511458406)與下方評測欄。 | 指出 skipped deploy、尚未測得真人效益、真人 A/B/C 結果留空。 |

完整工程驗證是 `npm run check`；[本地／CI／正式環境的驗證分層](VERIFICATION.md)需分開讀。舊的 [2:43 Kiro 影片](https://reese-max.github.io/taichung-police-intel/demo-video.mp4)只展示 2026 年 8 月原型，不展示上表的新 GovIntel 功能。

## 4. 舊版與新版的可證差異

| 2026-08 Kiro 原型（歷史） | GovIntel 在此 commit 已有 | 下一個驗收缺口 |
|---|---|---|
| 五來源備詢首頁、來源健康、官方影片／逐字稿導航；見[歷史腳本](../../DEMO_SCRIPT.md)與[Kiro 使用紀錄](../../KIRO_USAGE.md) | 保存版本、source policy、具來源 coverage 的 read-only 查詢；見[Query Gateway](../issue-30-runtime-boundaries.md) | 排程、新版公開版本與查詢服務部署須逐項核對 |
| 單筆議會資料與影音來源回查 | 官方文件 locator／版本對照、三來源 PublicEvent 融合 fixture、可追溯衝突；見[#24](../issue-24-public-event-fusion.md) | 真實多機關文件、正式事件 ID／原文 locator、人工裁決 |
| 無跨日正式交班工作庫 | local-first watch、變更後 `NEEDS_REVIEW` 與 v1→v2 重播；見[#23](../issue-23-handoff-flow.md) | 持久多使用者流程、權限與真人操作 |
| 舊受保護 main 直推造成排程中斷 | PR #26 已合併 checkpoint／失敗收據與公開 bytes 核對程式 | [#20](https://github.com/Reese-max/taichung-police-intel/issues/20) 的自然排程與失敗演練仍未完成 |

既有作品的新增比例、著作權與參賽資格要由主辦和團隊核對；以上是**功能差異**，不推論官方新穎性認定。Kiro 的 Steering／Specs／Hooks、模型揭露、舊報名草稿與歷史時限仍見[根 README 歷史區](../../../README.md#historical-kiro-competition-package)、[Kiro 紀錄](../../KIRO_USAGE.md)及[舊 submission](../../../SUBMISSION.md)。

## 5. 評測和交付缺口

| 衡量項目 | 目標／方法 | 目前結果 |
|---|---|---|
| 重要異動 P／R、事件融合 F1、引用支持 | [開發／事件保留集與 A／B0／B／C／C−D 定義](README.md)；主計畫暫定 P≥0.85、R≥0.90、F1≥0.90 | `NOT_RUN`；[機器範本](evaluation-manifest.template.json)保留 null，沒有 n／score |
| 承辦任務時間及漏件 | 同資料截止點的人工 A 與新版 C，比中位耗時、漏件與人工修正 | `NOT_RUN`；無真人／機關採用證據 |
| 來源→發布時效及維運 | 分開記錄官方修改、蒐集、驗證、公開版本與 hash | 公開 9/24 快照及 9/29 失敗排程已可查；**未通過**完整晨晚與負向發布驗收 |
| 參賽資格、資料使用與實際送件 | 當屆官方原件、團隊私有核對與回執 | `UNVERIFIED`；不沿用 2026-08 Kiro 時限 |

本路徑不可把 fixture、CI、HTTP 200、舊影片或規劃目標說成真人使用、即時來源、正式送件或得獎。後續發行時更新主幹 SHA、公開 `generated_at`／hash、自然排程及每項能力的實際狀態，再對外展示。
