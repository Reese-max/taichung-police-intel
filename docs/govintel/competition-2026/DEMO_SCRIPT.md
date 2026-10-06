# GovIntel AI－v6 四分鐘展示與備援

提案：**GovIntel AI－公共資訊查詢與個人化追蹤平台**。以下時限是內部演練安排，不是主辦官方簡報規定。主要流程對應 v6 p2–4 的民眾道路查詢與站內追蹤；交班與議會影音留作既有基礎補充。

## 計時前固定展示範圍

準備一份已驗收的乾淨 checkout、版本／lockfile、測試 receipt 與資料 cutoff。公開已部署 baseline 與 PR 候選本地畫面分開。若候選尚未通過，僅展示明示合成／offline 的資料與腳本，不用預錄或舊畫面冒充 live 功能。

```bash
npm ci
npm ci --prefix apps/web
python3 -m pip install -r requirements.txt
npm run check
npm --prefix apps/web run dev
```

付費模型不是離線 replay 的必要條件；沒有 provider receipt 時不宣稱已運行語意 AI。資料、模型、真人與 deployment 各有驗收範圍。

## 0:00–0:35 任務與現況

說明使用者想知道「測試路 A 到 B 路口的工程何時結束」，並希望下次開站看見工期是否延長。這是合成道路及公告，不代表真實道路安全或已取得的正式四類來源。

讀[CURRENT_STATUS](CURRENT_STATUS.md)：核准 baseline 為五來源；v6 query／tracking／eval PR 已合併到 main，公開部署與真人成效仍待各自驗收。新功能演示若來自本地，螢幕先標版本與`SYNTHETIC`／`OFFLINE_REPLAY`。

## 0:35–1:25 查詢與保存條件

在已驗收的本地 candidate 開 `/public-query/`，查詢臺中、測試路 A–B 工程。v1 資料顯示 10 月 5–7 日、每日 9:00–17:00，開原文與資料時間。檢查查詢條件可見、缺值與來源限制可讀，不暗改地區／期間。

開 `/tracking/` 保存地區、路段、工程關鍵字；條件可預覽／修改，已讀版本存同一瀏覽器，不要求住址、登入或敏感身分。首次資料列為初始清單，不叫「今日新增」。來源頁 `/sources/` 可核對已部署資料的時間與限制。

## 1:25–2:30 重開站看延長與去重

關閉後重新開啟或刷新固定 replay。v2 把結束日 10 月 7 日延到 9 日；每日時段與 A–B 範圍不變。展開 v1／v2 原文與差異，再標已讀、再次開站核對不重複。

重播同源轉載與純排版版本，應保留來源／版本但不新增重要提示。另一次實質更正仍可列出，不被過度去重吞掉。來源失敗／過期時保留 LKG 與缺口，不推論道路已恢復。

## 2:30–3:15 明文解除與取消

v3 另有 10 月 9 日 17:00 起解除的明文；讀取後不重複提示該次工程。缺 v3、到期、列表消失或來源失聯均不能標解除。使用者取消追蹤後停止該條件站內提示，原公告仍能公開查詢。

說明只有開站／刷新才比對；關站不背景推播。本機 storage 被清除可能失去清單，跨裝置及帳號不是首期承諾。

## 3:15–4:00 證據與缺口

展示[最新日期化 receipt](CURRENT_STATUS.md)與[驗收清單](ACCEPTANCE_CHECKLIST.md)。Oct4 production collection、五檔 hash／Gateway 通過，但仍`PARTIAL`，S-007 陳舊／S-029 失敗，四類候選未正式啟用。

在 query 的 D1 卡片切換臺中 29 區，核對 112 年 12 月（2023-12）人口／戶數、原始 CSV hash 與官方來源；這是已核對歷史樣本，未 promotion，不能算現況人口／人潮。D2 官方 resource 403，不能以未核對名錄或座標補畫成功。

語意 AI、獨立保留集與真人比較仍未完成；85%／90%／30% 只說目標。工程 fixture 的命中與用時不能當真人或 AI 效益。最終整合驗收狀態看 [VERIFICATION](VERIFICATION.md)；主辦已確認補件收妥，但未接受更名，原報名完整名稱與重送要求仍待指示，見[核對紀錄](ORGANIZER_STATUS_2026-10-06.md)。

## 既有基礎與故障備援

需要補充時再開議會 evidence drawer、官方影片 timestamp 和來源 health。官方 HLS 失效用原機關入口與明示 fallback；舊 Kiro 影片僅[歷史證據](../historical/README.md)。依 v6 p9，未通過線上來源可交截止時間明確的離線查詢／追蹤驗證包，不宣稱持續最新服務。
