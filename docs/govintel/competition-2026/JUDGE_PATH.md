# GovIntel AI－評審重播入口

先讀[CURRENT_STATUS](CURRENT_STATUS.md)；v6 產品路徑與說法依[DEMO_SCRIPT](DEMO_SCRIPT.md)。下列命令重播已存在的工程核心；完整版本與瀏覽器驗證可執行 `python3 -X utf8 scripts/verify-current-checkout.py --mode full --output /tmp/govintel-current-checkout`，保留產生的 code SHA、鎖檔雜湊與 receipt。這些工程結果不取代正式部署或真人驗收。

## 環境與完整 gate

```bash
npm ci
npm ci --prefix apps/web
python3 -m pip install -r requirements.txt
npm run check
```

Node20+、Python3.11+；離線基礎不需 APIkey、付費模型或資料庫。某些完整 runtime 檢查另有依賴時，以其實際退出碼／receipt 為準。

2026-10-16 的 Windows／Python 3.11 執行有 POSIX、符號連結權限及 pinned PDF parser 環境失敗；完整 gate 未通過。靜態 build、文件檢查、合成 replay 與有限 Chromium 操作各自的結果見 [VERIFICATION](VERIFICATION.md)，不以單一通過項代替完整 gate。

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

本地`http://localhost:3000`用於開發此 checkout；v6 路徑為 `/public-query/`、`/tracking/`、`/sources/`，首頁主導覽提供入口，既有議會 dashboard 保留。四分鐘演練請改用 [DEMO_SCRIPT](DEMO_SCRIPT.md)的靜態 build、僅綁 loopback 的伺服器及 R1–R9 操作表，避免把啟動 dev server 當成固定產物的重播。公開[demo](https://reese-max.github.io/taichung-police-intel/)另依固定正式 receipt 判讀，不把本地 checkout 當成公開 served 版本。

在 repo 根目錄可先獨立核對合成序列與文件契約：

```bash
python3 -X utf8 scripts/replay-commute-reopen.py --self-check
node --test apps/web/tests/competition-docs.test.mjs
```

完成靜態 build，且 `http://127.0.0.1:8000` 正在提供該產物後，可用既有實際 UI driver 留存畫面與操作收據（需要已安裝的 Chromium／Chrome 與根目錄 `npm ci`）：

```bash
CHROMIUM_PATH="/absolute/path/to/chrome" node scripts/e2e-v6-browser.mjs --base-url http://127.0.0.1:8000 --out runtime-evidence/judge-replay
```

將 `CHROMIUM_PATH` 換成實際可執行檔；Windows Git Bash 可使用 `C:/Program Files/Google/Chrome/Application/chrome.exe`。8000 已有服務時，換一個可用 port 並同步調整伺服器命令與 `--base-url`。driver 檢查保存、重載、延長、已讀、來源缺口、到期不解除、明文解除與取消；收據中的有限 UI 結果不是真人／模型分數。需要 served code SHA／鎖檔及 HTTP／STDIO 全部綁定時，另跑上方 `verify-current-checkout.py --mode full --chrome-path ...`，不得以單獨 UI driver 代替完整 receipt。

在 `/public-query/` 的 D1 卡片切換臺中 29 區，核對人口／戶數／行政區碼、**112 年 12 月（2023-12）**、官方來源、CSV hash 與取得收據。這是實際官方固定期別樣本，不是 synthetic 道路資料；仍為未 promotion 的歷史背景，不能拿 JSON service 的 114Y12M 或擷取日期當同一期／現況。D2 尚缺官方 resource bytes／CRS；不用模擬名錄補成已取得。

讀[公開 source-status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)的 generation、generated_at、source health、freshness／window 與 LKG。日期化成功證據是[37169331249](https://github.com/Reese-max/taichung-police-intel/actions/runs/37169331249)與[37212963039](https://github.com/Reese-max/taichung-police-intel/actions/runs/37212963039)；最新公開 readback 詳見[狀態頁](CURRENT_STATUS.md)。HTTP200 不能推出所有來源新鮮或世界沒有事件。

## v6 已合併程式與獨立驗收

[PR124 查詢](https://github.com/Reese-max/taichung-police-intel/pull/124)、[125 追蹤](https://github.com/Reese-max/taichung-police-intel/pull/125)、[126 評測](https://github.com/Reese-max/taichung-police-intel/pull/126)已合併，須在目前 checkout 的固定 code SHA 重播。query→保存→重開站→工期延長→已讀→明文解除→取消為主流程；排版、轉載、失敗、過期、同名不同日與過度去重是負向情境。根據[ACCEPTANCE_CHECKLIST](ACCEPTANCE_CHECKLIST.md)記錄結果，不能因 source merge 或 unit pass 而自行宣稱已部署。

本輪已本地整合並修正 release／詳情失效、每日時段與延期追蹤；最終整合測試／瀏覽器狀態見 [VERIFICATION](VERIFICATION.md)。合成道路用 09:00–17:00 每日時段，結束日 10/7→10/9，解除要另有 10/9 17:00 的明文；來源失敗、公告消失與到期均不可推算解除。

## 判讀成果

[PROPOSAL_MAPPING](PROPOSAL_MAPPING.md)列 v6 頁次。[EVALUATION](EVALUATION.md)區分 A 官方人工、B 搜尋摘要、C 完整產品與 semantic-off 消融；未跑的方法`NOT_RUN`。真人原始資料與送件附件私有保存，對外只提供批准的匿名結果、合法 sample 與 hash／receipt。
