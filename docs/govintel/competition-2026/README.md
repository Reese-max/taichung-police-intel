# GovIntel AI｜2026 內政黑客松參賽入口

這是目前評審應使用的文件入口，不是獲獎保證、正式報名回執、官方資格認定或機關採用證明。查核日與證據版本在[現行實作與證據狀態](./CURRENT_STATUS.md)維護。

> **一句話 JTBD：**讓承辦人知道跨機關公開公告改了什麼、哪份舊交班稿需要重核，並能回到官方原文確認。

## 先走這條 judge path（3–5 分鐘）

1. 開[現行狀態與 Claim/evidence table](./CURRENT_STATUS.md)。先分清 `PRODUCTION_ACTIVE`、`IMPLEMENTED_NOT_PRODUCTION`、`CANDIDATE_CANARY`、`DESIGN_ONLY`、`BLOCKED`。
2. 依[現行 4 分鐘展示腳本](./JUDGE_PATH.md)重播目前 build 與 `FIXTURE_ONLY` PublicEvent／handoff／locator receipts。
3. 用[資料來源矩陣](./DATA_SOURCE_MATRIX.md)核對五個 active source、candidate collectors、Twinkle route 與每個官方入口。
4. 用[架構說明](./ARCHITECTURE.md)看 current production path、離線實作與未完成 promotion 之間的邊界。
5. 最後讀[評估計畫與結果](./EVALUATION.md)、[舊版／新增差異](./OLD_VS_NEW.md)與[限制／安全](./LIMITATIONS_AND_SAFETY.md)。

不需要 API key、登入、資料庫或付費 runtime service。完整工程 gate：

```bash
npm run check
```

## 文件索引

| 評審問題 | 文件 |
|---|---|
| GovIntel 是什麼、現在真的做到哪些？ | [CURRENT_STATUS.md](./CURRENT_STATUS.md) |
| 3–5 分鐘如何重播？ | [JUDGE_PATH.md](./JUDGE_PATH.md)、[DEMO_SCRIPT.md](./DEMO_SCRIPT.md) |
| 新聞／市政／交通／Twinkle 到底是什麼狀態？ | [DATA_SOURCE_MATRIX.md](./DATA_SOURCE_MATRIX.md) |
| 提案如何對到程式與證據？ | [PROPOSAL_MAPPING.md](./PROPOSAL_MAPPING.md) |
| 現在的資料流與安全邊界？ | [ARCHITECTURE.md](./ARCHITECTURE.md)、[LIMITATIONS_AND_SAFETY.md](./LIMITATIONS_AND_SAFETY.md) |
| 評估目標和實測結果是否分開？ | [EVALUATION.md](./EVALUATION.md)、[evaluation-manifest.template.json](./evaluation-manifest.template.json) |
| 舊 Kiro／競賽和本次新增差在哪？ | [OLD_VS_NEW.md](./OLD_VS_NEW.md)、[Historical package](../historical/README.md) |
| #20 為何還是 blocker？ | [CURRENT_STATUS.md](./CURRENT_STATUS.md)、[Issue #20](https://github.com/Reese-max/taichung-police-intel/issues/20)、[publication recovery record](./PUBLICATION_RECOVERY.md) |

## Current headline

- 目前 production baseline 是五個議會／市政來源的備詢、來源狀態、缺口與官方影音證據導覽。
- PublicEvent、official document locator、Query Gateway、local-first handoff 等有可重播核心，但正式跨機關服務／真人驗收未由此證明。
- 新聞／市政／交通／消防候選維持 `CANDIDATE_CANARY`；Twinkle hybrid、latest-information loop 與完整跨日／事件 production flow 維持 `DESIGN_ONLY` 或依 current status 標示。
- 最新查核的 scheduled run [36952506907](https://github.com/Reese-max/taichung-police-intel/actions/runs/36952506907) 成功，但公開 source status 為有時間界線的 `PARTIAL` snapshot，且 S-029 失敗／過期；#20 的完整 acceptance 仍是 `BLOCKED`。
- `evaluation-manifest.template.json` 保持 `NOT_RUN`／null；目標不冒充實測結果。

## Historical boundary

舊的 Kiro competition deadline、submission checklist、舊 demo video、Kiro usage 與 prior prototype receipts 已集中到[Historical competition / prior prototype evidence](../historical/README.md)。根目錄的 compatibility pointers 只是為既有 verifier 保留的入口，不是 current judge path。

當屆資格、報名期間、評分比重、既有作品認定與官方提交格式必須以主辦官方原件和私有回執確認；本 repo 不自行填入未核實的規則或結果。

## Related planning records

- [GovIntel development and acceptance plan](../GOVINTEL_PLAN.md)
- [Data-source strategy](../DATA_SOURCE_STRATEGY.md)
- [Twinkle + official hybrid strategy](../TWINKLE_HYBRID_SOURCES.md)
- [Issue #20 publication recovery record](./PUBLICATION_RECOVERY.md)（歷史 checkpoint，現況以 CURRENT_STATUS 為準）
- [Earlier verification record](./VERIFICATION.md)（有日期的歷史驗證，不替代 current run）
