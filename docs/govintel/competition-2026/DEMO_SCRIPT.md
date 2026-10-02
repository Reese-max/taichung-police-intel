# GovIntel AI｜現行 4 分鐘評審展示腳本

這是 2026 GovIntel judge path；舊 Kiro 2:43 影片已移到 [Historical](../historical/README.md)，不能拿來證明本次新增能力。

## 計時前準備

在乾淨 checkout 安裝既有依賴並確認完整工程 gate：

```bash
npm ci
npm ci --prefix apps/web
python3 -m pip install -r requirements.txt
npm run check
```

展示不需要 API key、登入、資料庫或付費服務。將終端、README、公開 source status 和 localhost 瀏覽器並排。

## 0:00–0:40｜任務與現況

開 [README](../../../README.md) 的第一屏與 [CURRENT_STATUS](./CURRENT_STATUS.md)。說明 JTBD：承辦人從公開公告找出實質更正，回原文核對，再決定舊交班內容是否需要重核。

明示：目前 `PRODUCTION_ACTIVE` 是五個議會／市政來源的公開備詢與影音證據 path；候選 collector、Twinkle、persistent tracking、production event fusion 與 #20 完整發布 acceptance 各有自己的狀態，沒有混稱。

## 0:40–1:15｜資料時間與來源健康

開 [公開 source-status](https://reese-max.github.io/taichung-police-intel/data/source-status.json)，指出 `generated_at`、collection run、每個 source 的 `source_health`、`freshness_status`、`window_completeness` 與 LKG。最新查核 snapshot 為 `2026-10-02T09:46:41+08:00`、overall `PARTIAL`、S-029 `FAILED`／`STALE`。

不要把 endpoint HTTP 200、單一 source `PASS` 或「沒有新項目」說成全部來源新鮮或世界沒有事件。

## 1:15–2:00｜目前 build 的官方證據流程

```bash
npm --prefix apps/web run dev
```

開 `http://localhost:3000`：看 priority brief、來源卡與 gap；開 evidence drawer；搜尋 `警察局`；點 transcript segment／word 回到官方影音時間戳。說明 transcript 是 navigation-only，官方頁面與影片才是 authoritative evidence。若 HLS 失效，展示正式來源連結和 fallback，不替換成產品 demo video。

## 2:00–2:45｜跨機關 fixture（明示不是 production）

```bash
python3 -X utf8 scripts/public-event-fusion.py --self-check
```

打開 [`public-event-demo.json`](../../../apps/web/public/data/public-event-demo.json)，指出 `FIXTURE_ONLY`、三個來源、文件版本、時間 `17:00 → 16:00` 和 `CONFLICT`。解釋這證明的是 deterministic replay／保守衝突模型，不是 live collector、production event store 或 accuracy score。

## 2:45–3:25｜交班與 provenance 核心

```bash
python3 -X utf8 scripts/handoff-state.py self-check
python3 -X utf8 scripts/located-facts.py self-check
python3 -X utf8 scripts/verify-source-policy-integration.py --self-check
```

展示版本化 handoff、evidence locator、hash 與 source policy；說明 local-first、candidate 與 design 的邊界，並指出正式多人權限、真人驗收與 production promotion 尚未由 self-check 證明。

## 3:25–4:00｜新增／歷史差異與限制

開 [OLD_VS_NEW](./OLD_VS_NEW.md)、[EVALUATION](./EVALUATION.md) 與 [LIMITATIONS_AND_SAFETY](./LIMITATIONS_AND_SAFETY.md)。

收尾固定說法：目標數字不是結果；評估 manifest 仍 `NOT_RUN`／null；[Issue #20](https://github.com/Reese-max/taichung-police-intel/issues/20) 仍是完整發布 acceptance blocker；舊 Kiro package、舊 deadline 與舊影片是歷史 provenance，不是本次比賽規則或新增功能證據。
