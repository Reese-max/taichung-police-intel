# GovIntel PR 收斂紀錄：2026-10-05

核對時點：2026-10-05 22:53 Asia/Taipei（#145 合併後回讀）。範圍是本輪開始時的 37 個開啟 PR；逐張比對 exact head、merge-base delta、現行實作與測試，再確認 GitHub head 沒有變更才整理。這是時間化工作清單，不是所有候選功能已上線的證明。

截至此時，15 張已關閉為已取代／封存，#143 已由獨立工作線合併，21 張保留並標記待移植。原分支均未刪除。**#85／#87／#92／#94／#100 的唯一保護／回歸已移植並隨 [#145](https://github.com/Reese-max/taichung-police-intel/pull/145) 合併至 main `678afa496fdefbb3e7a61ee68cde8424ee782ee7`，這五張已在確認原 head 不變後關閉。**

合併舊分支前，只移植仍缺的功能與測試，保留現行 release、evidence、rights、來源核准與失敗處理。主要整合入口為 #97（Python live）、#104（incremental provenance）、#112（bounded pagination／隔離候選 Pages）、#116（NPA reference）。#84 的合成比較不能替代真人／語意 AI 評測，結果仍 `NOT_RUN`。

| PR | 審核 head SHA | 截至核對時點 | 保留／收斂理由 | 整合目標 |
|---|---|---|---|---|
| [#16](https://github.com/Reese-max/taichung-police-intel/pull/16) | `acb67b3fc43ea1b6e7150a110e388885f77098aa` | 已關閉 | 舊支線無條件啟用候選，違反核准來源邊界 | 保留增量於 #104、隔離候選於 #112 |
| [#50](https://github.com/Reese-max/taichung-police-intel/pull/50) | `1dbfaf4cc6bca98ff6f39ef195de8dedbbd2865a` | 保留／待移植 | preview UX 與可及性／保存回歸尚未被正式 UI 完整取代 | 按現行 UI 挑選 delta，保留獨立 preview |
| [#51](https://github.com/Reese-max/taichung-police-intel/pull/51) | `03da387dc9becfba8980efabc4c8adb894ac5c3b` | 保留／待移植 | JS＋SQL live 架構另有 ASR／持久化保護 | 唯一保護先盤點並移植 #97 |
| [#81](https://github.com/Reese-max/taichung-police-intel/pull/81) | `56882f85aa5919b613cbd40bf338ac0a5e575e84` | 保留／待移植 | registry entity 綁定與 bounded discovery 尚缺 | 本 PR 更新基底，保留新 trust／release 保護 |
| [#82](https://github.com/Reese-max/taichung-police-intel/pull/82) | `bb436d8bfed117c87c13f514c9a2635eeda9ec37` | 已關閉 | 9/29 評審入口已由新版文件取代 | main d3d94ac／v6 文件 |
| [#83](https://github.com/Reese-max/taichung-police-intel/pull/83) | `438f45e2955a711d3ab6b23273566e640374f007` | 保留／待移植 | 尚有跨 origin next-page-before-fetch 回歸 | 先移植唯一測試至 #112 |
| [#84](https://github.com/Reese-max/taichung-police-intel/pull/84) | `a1bea19bf5e3739b621c6bd597772b4730ce3db2` | 保留／待移植 | 19-case synthetic v2／baseline gate 尚缺 | 更新基底並保留 v6 reopen；真人 NOT_RUN |
| [#85](https://github.com/Reese-max/taichung-police-intel/pull/85) | `f288bd3830e7691a2248bfb9b79f6fa07383b5d1` | 已關閉 | live 連續兩次 outage 持久化回歸已移植 | 已移植並合併 #145；原 head 不變，已關閉 |
| [#86](https://github.com/Reese-max/taichung-police-intel/pull/86) | `1e5785c480c469b59b36b61454f9c36cdb818c47` | 已關閉 | Worker 官方角色／stale／cause 保護已吸收 | main canonical query／MCP 回歸 |
| [#87](https://github.com/Reese-max/taichung-police-intel/pull/87) | `46f3922e78034dd4cda35ad8dadf58bc2cb4f078` | 已關閉 | release runtime 已吸收，raw-byte CLI 回歸已移植 | 已移植並合併 #145；原 head 不變，已關閉 |
| [#88](https://github.com/Reese-max/taichung-police-intel/pull/88) | `49351495bb6479a6d1ab55a5741b37e38ae170e3` | 已關閉 | release client／manifest／verifier 回歸已吸收 | main release／rights／Worker version gates |
| [#89](https://github.com/Reese-max/taichung-police-intel/pull/89) | `31f1d20a5686d53f583ee06dc57c188dfe1e857e` | 保留／待移植 | fraud parser 已被 #116 強化，仍有唯一 inventory 回歸 | 先移植 CSV／授權／FIXTURE_ONLY 測試至 #116 |
| [#90](https://github.com/Reese-max/taichung-police-intel/pull/90) | `de120ef8a7020448548f500d3cb45ca4f9302bd8` | 已關閉 | 9/30 評審入口已由新版文件取代 | main d3d94ac／v6 文件 |
| [#91](https://github.com/Reese-max/taichung-police-intel/pull/91) | `65e8384c2b6bab96efd3a78e888d71f5892f350c` | 保留／待移植 | catalog publication metadata 尚缺，與 #104 重疊 | 保留唯一 bundle 測試並整合 #104 |
| [#92](https://github.com/Reese-max/taichung-police-intel/pull/92) | `fa12efe17f674406387b67b027c8039214964b89` | 已關閉 | 兩種 profile 少比較三個 source status 欄位，已修復 | 已移植並合併 #145；原 head 不變，已關閉 |
| [#94](https://github.com/Reese-max/taichung-police-intel/pull/94) | `131e64137e595945978b7be6c68831d36a18c0e5` | 已關閉 | catalog role 保護已吸收，三個 distinct 回歸已移植 | 已移植並合併 #145；原 head 不變，已關閉 |
| [#97](https://github.com/Reese-max/taichung-police-intel/pull/97) | `914ef98ab6b3d1bb8f1fea735a77660c94521582` | 保留／待移植 | Python bounded live canonical 延續仍未完成 | 本 PR 更新 policy／rights，保留 provisional |
| [#99](https://github.com/Reese-max/taichung-police-intel/pull/99) | `c26fe38eef8f9851d7d9cc96e60906ec33a76419` | 保留／待移植 | 新來源 first-observation baseline suppression 尚缺 | 更新基底並保留 builder／event 回歸 |
| [#100](https://github.com/Reese-max/taichung-police-intel/pull/100) | `0e6bc09e09d3cbf0517bc2648ae6de5eb50f90a1` | 已關閉 | state push 拒絕／留證 wording 回歸已移植 | 已移植並合併 #145；原 head 不變，已關閉 |
| [#101](https://github.com/Reese-max/taichung-police-intel/pull/101) | `e49a7c64717e998dec2542d1b12b9ab8579a0041` | 已關閉 | 全部實作保留於 #104，後者另有四個 assertions | #104 仍待更新基底；不代表已在 main |
| [#102](https://github.com/Reese-max/taichung-police-intel/pull/102) | `414a76d96cd2734daa578529d247bc09b019de85` | 已關閉 | 觀察 workflow／verifier／測試與文件已吸收 | main candidate observation window |
| [#104](https://github.com/Reese-max/taichung-police-intel/pull/104) | `ce67f539e182beb92ea62669c627909a25b5357e` | 保留／待移植 | canonical incremental source provenance 尚缺 | 本 PR 更新核准 policy／日期與角色狀態 |
| [#109](https://github.com/Reese-max/taichung-police-intel/pull/109) | `a86735ceb0877e197c5509f9411cb2670cf9ba70` | 已關閉 | gate admission 與完整／不完整索引回歸已吸收 | main runner／Python／Worker evidence gates |
| [#111](https://github.com/Reese-max/taichung-police-intel/pull/111) | `1793fae7fc2fd5f7f5002cc719d482d358789c76` | 保留／待移植 | ordered pipeline／SLO model 尚缺 | 更新基底，保留正式 RIGHTS_BLOCKED 語意 |
| [#112](https://github.com/Reese-max/taichung-police-intel/pull/112) | `3691935a3ca69cbf81dff18c71b4836ddf15c45a` | 保留／待移植 | canonical bounded pagination／隔離候選 Pages 尚缺 | 本 PR 更新 publication／來源邊界，不覆蓋舊 Pages |
| [#113](https://github.com/Reese-max/taichung-police-intel/pull/113) | `965df6f2fdf85d8363413b88aaf59039b6dc5989` | 保留／待移植 | document version history／acquisition provenance 尚缺 | 本 PR 更新基底，維持 fixture-only |
| [#114](https://github.com/Reese-max/taichung-police-intel/pull/114) | `b5e4921f9d1b27b0efb99ac0ffed37aeba6e5463` | 已關閉 | 22 檔 blob 與 d3d94ac 全部相同，後續已有 v6 更新 | main d3d94ac／ec071d5／787babe |
| [#115](https://github.com/Reese-max/taichung-police-intel/pull/115) | `5bbfa26790e81674870e435c8ebd80877793038a` | 保留／待移植 | export-time needs-review／exact source version warning 尚缺 | 本 PR 更新基底，不改寫已確認 handoff v1 |
| [#116](https://github.com/Reese-max/taichung-police-intel/pull/116) | `b31de73aa3d3ffa02ad4997ed187efc3f243122b` | 保留／待移植 | canonical NPA fraud／attachment provenance contract 尚缺 | 本 PR 更新基底，維持 fixture-only reference |
| [#117](https://github.com/Reese-max/taichung-police-intel/pull/117) | `000994caeb06fa786efd93bfcfa872017b06acbc` | 保留／待移植 | located facts 未判否定／未定／改期語句 | 更新基底並核對 context／version 碰撞 |
| [#118](https://github.com/Reese-max/taichung-police-intel/pull/118) | `40fdec65d5e474209929b644a65f4c7d1c27de6b` | 保留／待移植 | persistent per-source／host budget／backoff 尚缺 | 保留實作與 DB 回歸，按現行 transport 更新 |
| [#119](https://github.com/Reese-max/taichung-police-intel/pull/119) | `1ed3a1eb3f58b3c9c15c78f7f0540d41ff11cb09` | 保留／待移植 | migration dispatch／rehash／atomic receipt 尚缺 | 本 PR 更新基底，保留人工 reapply gate |
| [#120](https://github.com/Reese-max/taichung-police-intel/pull/120) | `eaa8180f6cda16ece5396cc9765278a04594923b` | 保留／待移植 | dated NamedEvent／venue／跨市同名道路 fixture 尚缺 | 保留資料與回歸；不視為 live source promotion |
| [#121](https://github.com/Reese-max/taichung-police-intel/pull/121) | `96cee6d304edf03f842c83f3229f10fe0a02b9d8` | 保留／待移植 | reason-bound promotion／relevance-only 回饋尚缺 | 本 PR 更新基底，canonical promotion 仍人工核准 |
| [#122](https://github.com/Reese-max/taichung-police-intel/pull/122) | `d73e6caf122a4cc13e5f43aa04f2ba472850a3c9` | 已關閉 | schema drift code／tests／docs 已逐位吸收 | main #127／60c45b1 |
| [#123](https://github.com/Reese-max/taichung-police-intel/pull/123) | `299b549a9ce5b923275cd4bbed318b4672479ac7` | 保留／待移植 | retention v2 matrix／archive／query-index gates 尚缺 | 更新基底，只移植 delta，保留新 Gateway |
| [#143](https://github.com/Reese-max/taichung-police-intel/pull/143) | `b75859abd0e57481358d30777812d01fde5ac88c` | 已合併 | 來源審核待辦與 S-038 研究規劃已合併 | main cade5a78；pending 仍未核准 |

審核原始紀錄與動作回讀保存於本輪驗收附件。檔案內容 SHA256 如下，GitHub PR 連結另保留每次關閉／保留原因：

| 紀錄 | SHA256 |
|---|---|
| `pr-audit-sources.json` | `bb3ae74bab6061740577f7af28fd1fda56205cf70d89c631db1b6ee739b5c22a` |
| `pr-audit-core.json` | `9f9c6bff755c2fffd683ae5063e2051942f1f86eb759791462f613d34bba7f82` |
| `pr-audit-governance.json` | `f75cb0650c5519dc0233a1b964c9a40a7b59da39d52b4e187b514a369d0c97be` |
| `pr-organization-actions-docs-snapshot.json`（22:26 原始整理快照） | `047345e30e2946a4ac160d3e0e802693751354d45f06a8f1c332c4a920eb7eed` |
| `pr-final-readback.json`（#145 合併後） | `f41bd36c65fe8220fd979086df9db6e79d8e480c1e820625f1fdc95840ebb827` |
| `pr-organization-actions.json`（#145 合併後） | `29a7d077e512e212ffdc1e959f02c7c638955dcabb19c46567f747bc0651ffa3` |

來源日期／連線的本輪資料證據見 [source-repair-evidence](source-repair-evidence-2026-10-05.json)，已發布與尚待驗收的能力見 [CURRENT_STATUS](competition-2026/CURRENT_STATUS.md)。本輪未核准候選來源 promotion，未宣稱全臺覆蓋、每日新增量、真人效益或主辦受理。
