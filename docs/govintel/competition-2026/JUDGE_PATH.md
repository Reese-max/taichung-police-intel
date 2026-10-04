# GovIntel AI－評審重播入口

先讀[CURRENT_STATUS](CURRENT_STATUS.md)；v6 產品路徑與說法依[DEMO_SCRIPT](DEMO_SCRIPT.md)。下列命令重播已存在的工程核心，不取代查詢／追蹤 PR 的固定 head、瀏覽器操作、正式部署或真人驗收。

## 環境與完整 gate

```bash
npm ci
npm ci --prefix apps/web
python3 -m pip install -r requirements.txt
npm run check
```

Node20+、Python3.11+；離線基礎不需 APIkey、付費模型或資料庫。某些完整 runtime 檢查另有依賴時，以其實際退出碼／receipt 為準。

## 可重播的核心

```bash
python3 -X utf8 scripts/verify-source-policy-integration.py --self-check
python3 -X utf8 scripts/located-facts.py self-check
python3 -X utf8 scripts/public-event-fusion.py --self-check
python3 -X utf8 scripts/handoff-state.py self-check
```

SourcePolicy、locator、PublicEvent fixture 與 handoff 各有自己的資料與失敗契約。PublicEvent 明示`FIXTURE_ONLY`，handoff 是 local-first；它們不是 v6 真人追蹤成效或已部署 multi-agency truth。

## Web 與公開版本核對

```bash
npm --prefix apps/web run dev
```

本地`http://localhost:3000`展示此 checkout 的已驗收能力；v6 candidate 路徑為 `/public-query/`、`/tracking/`、`/sources/`，首頁主導覽提供入口，既有議會 dashboard 保留。確認它與 production 是不同版本，不把未部署 PR 寫成公開可用。公開[demo](https://reese-max.github.io/taichung-police-intel/)保留來源 health／gap 與官方影音 path。

在 `/public-query/` 的 D1 卡片切換臺中 29 區，核對人口／戶數／行政區碼、**112 年 12 月（2023-12）**、官方來源、CSV hash 與取得收據。這是實際官方固定期別樣本，不是 synthetic 道路資料；仍為未 promotion 的歷史背景，不能拿 JSON service 的 114Y12M 或擷取日期當同一期／現況。D2 尚缺官方 resource bytes／CRS；不用模擬名錄補成已取得。

讀[公開 source-status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)的 generation、generated_at、source health、freshness／window 與 LKG。日期化成功證據是[37169331249](https://github.com/Reese-max/taichung-police-intel/actions/runs/37169331249)與[37212963039](https://github.com/Reese-max/taichung-police-intel/actions/runs/37212963039)；最新公開 readback 詳見[狀態頁](CURRENT_STATUS.md)。HTTP200 不能推出所有來源新鮮或世界沒有事件。

## v6 候選驗收

[PR124 查詢](https://github.com/Reese-max/taichung-police-intel/pull/124)、[125 追蹤](https://github.com/Reese-max/taichung-police-intel/pull/125)、[126 評測](https://github.com/Reese-max/taichung-police-intel/pull/126)須各在固定 code SHA 重播。query→保存→重開站→工期延長→已讀→明文解除→取消為主流程；排版、轉載、失敗、過期、同名不同日與過度去重是負向情境。根據[ACCEPTANCE_CHECKLIST](ACCEPTANCE_CHECKLIST.md)記錄結果，不能因 PR 可合併或 unit pass 而自行宣稱已部署。

本輪已本地整合並修正 release／詳情失效、每日時段與延期追蹤；最終整合測試／瀏覽器狀態見 [VERIFICATION](VERIFICATION.md)。合成道路用 09:00–17:00 每日時段，結束日 10/7→10/9，解除要另有 10/9 17:00 的明文；來源失敗、公告消失與到期均不可推算解除。

## 判讀成果

[PROPOSAL_MAPPING](PROPOSAL_MAPPING.md)列 v6 頁次。[EVALUATION](EVALUATION.md)區分 A 官方人工、B 搜尋摘要、C 完整產品與 semantic-off 消融；未跑的方法`NOT_RUN`。真人原始資料與送件附件私有保存，對外只提供批准的匿名結果、合法 sample 與 hash／receipt。
