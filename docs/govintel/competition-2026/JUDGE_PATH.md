# GovIntel AI｜2026 評審可重播入口

這是本次內政黑客松文件的 3–5 分鐘 judge path。它展示目前真的能從 checkout／fixture 重播的內容，並把候選、設計與阻塞項目留在邊界外。它不是當屆資格、正式報名、得獎、真人成效或機關採用證明。

先讀[現行狀態與 Claim/evidence table](./CURRENT_STATUS.md)，再按下列步驟。能力狀態只採 `PRODUCTION_ACTIVE`、`IMPLEMENTED_NOT_PRODUCTION`、`CANDIDATE_CANARY`、`DESIGN_ONLY`、`BLOCKED`。

## 0. 準備（計時前）

需要 Node.js 20+、Python 3.11+，不需要 API key、登入、資料庫或付費服務：

```bash
npm ci
npm ci --prefix apps/web
python3 -m pip install -r requirements.txt
npm run check
```

`npm run check` 是完整工程 gate；它不是真人評測或部署證據。若只要重播本頁的離線 receipt，可直接進入第 1 步。

## 1. 0:00–0:45｜確認問題、狀態與資料時間

開啟[根目錄 README](../../../README.md)與[現行狀態](./CURRENT_STATUS.md)，再開[公開 demo](https://reese-max.github.io/taichung-police-intel/)和[公開 source-status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)。

請說清楚：目前公開主線是五個議會／市政來源的備詢與官方影音證據導覽；跨機關事件與交班是離線／候選路徑。閱讀 `generated_at`、`source_health`、`freshness_status`、`window_completeness` 與 `last_known_good`，不要把 HTTP 200 或 `PASS` 說成所有資料新鮮。

最新查核的公開 snapshot 是 `2026-10-02T09:46:41+08:00`、`CR-DEMO-20261002-MORNING-SCHEDULE`、`PARTIAL`；[run 36952506907](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907) 是對應的成功 scheduled workflow。這是有時間界線的證據。

## 2. 0:45–1:35｜重播事件融合 fixture

```bash
python3 -X utf8 scripts/public-event-fusion.py --self-check
```

確認 repository 的 [`public-event-demo.json`](../../../apps/web/public/data/public-event-demo.json) 明示 `status=FIXTURE_ONLY`、三個來源、前後版本與 `fusion_status=CONFLICT`。這一步可展示「同一事件／版本差異／官方衝突」的保守資料模型，但不能說成 live collector、production event store 或準確率結果。

## 3. 1:35–2:20｜重播交班與文件證據核心

```bash
python3 -X utf8 scripts/handoff-state.py self-check
python3 -X utf8 scripts/located-facts.py self-check
```

前者是 local-first handoff replay；後者從固定官方文件 fixture 產生可定位事實。兩者都應保留版本、locator、hash 與未知狀態的界線，不把 self-check 當真人驗收或正式多人簽核。

## 4. 2:20–3:05｜重播來源政策與覆蓋語意

```bash
python3 -X utf8 scripts/verify-source-policy-integration.py --self-check
```

指出 `source-policy.approved.json` 只啟用五個 baseline source IDs；candidate catalog、Twinkle overlay 與未完成來源不會因被列在設定／設計中就自動成為 production source。

## 5. 3:05–4:30｜看目前可用的 Web judge path

```bash
npm --prefix apps/web run dev
```

開啟 `http://localhost:3000`：

1. 看 priority brief 與來源健康／缺口；
2. 開 evidence drawer；
3. 搜尋 `警察局`，選取 transcript segment 或 word；
4. 核對它回到官方臺中市議會影片 URL／時間戳，並閱讀 transcript 的 navigation-only 標示；
5. 回到 README 的 current status，確認 candidate／design／blocked 功能沒有被 UI 或文件寫成 production。

這條路徑只展示目前 build 與 checked-in data；若官方 HLS 不可播放，應使用正式來源連結與明示 fallback，不以歷史產品影片冒充同一證據。

## 6. 收尾：評估與限制

開[評估計畫與結果](./EVALUATION.md)、[功能差異](./OLD_VS_NEW.md)和[限制／安全](./LIMITATIONS_AND_SAFETY.md)。`evaluation-manifest.template.json` 保持 `NOT_RUN`／null；目標數字不是結果。最後指出 [#20 publication blocker](https://github.com/Reese-max/taichung-police-intel/issues/20) 的未完成 acceptance，不用成功 CI 或成功 fixture 掩蓋它。

## 可核對連結

- Repository：[Reese-max/taichung-police-intel](https://github.com/Reese-max/taichung-police-intel)
- Public demo：[reese-max.github.io/taichung-police-intel](https://reese-max.github.io/taichung-police-intel/)
- Latest scheduled run：[36952506907](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907)
- Candidate collector：[PR #16](https://github.com/Reese-max/taichung-police-intel/pull/16)、[Issue #22](https://github.com/Reese-max/taichung-police-intel/issues/22)
- Prior prototype video（僅歷史）：[demo-video.mp4](https://reese-max.github.io/taichung-police-intel/demo-video.mp4)
