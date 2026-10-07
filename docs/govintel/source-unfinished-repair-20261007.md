# 15 項來源後續修復與 Claude 安裝

後續四條code review的修補與V2 checkpoint契約見 [覆核後續修復](source-checkpoint-reviewfix-20261007.md)；本文件保留PR #173當時的執行與驗證範圍。

本輪從已合併 PR #171 的程式狀態開始，先覆核並合併 PR #172 的四項驗證漏洞，再整合交通局分頁續跑、四組原件品質處理，以及法規／影音日期與範圍契約。各測試使用 fictional/offline fixtures；官方內容只在本地 parser 處理，公開收據限於 URL、hash、數值與聚合 metadata。

| 修復範圍 | 已驗證的進展 | 仍缺少的證據 |
|---|---|---|
| S032 交通局 | 真實第82頁的86個控制一致，修正自指Next；新工具預設每批4頁／6 HTTP，兩批實際8頁79 IDs，可從第9頁續跑 | 82頁814項是較早 metadata 觀察；checkpoint自算hash不是官方認證，詳頁／附件／修訂與獨立窗口覆盖仍未驗 |
| S036 集會遊行 | 原77,624列保存，17列結構異常隔離，77,607列未覆核候選；468重複額外列保留 | 活動身分、timezone、官方窗口總數與用途覆核 |
| CTX165 涉詐網站 | 原93,839列保存，7,183重複額外列標記、multiplicity保留 | 網域身分、撤除／期別語意；不能當本地案件數 |
| S028／CTXPOP | 18/18與6/6原件綁宣告／下載收據／SHA/schema；26個CSV／JSON原件hash前後不變 | S028集合缺2026-05；CTXPOP2023兩原件相同、有261重複額外列。統計口徑／行政區／固定期對帳仍待核對 |
| S026 法規 | 歷史79頁785 ID已枚舉；當次官方current明確空态；112569兩個可見原附件bytes齊 | 此草案公告8/25、預告8/26–9/1與pilot首日重疊；公告窗口0不能當pilot沒有草案。785詳頁／全附件／版本未齊 |
| S010／S011 影音 | recent質詢3頁13 ID；議事archive第一頁宣告19頁。兩個詳頁分別有明確10/1、10/6會議日與1／2播放器embed | archive／窗口完整性、播放、影片版本、時間軸與字幕仍未驗；沒有請求播放器或CDN |
| S001／S037／S034 | S001正常原網址重試仍503；D2保留原件403和CRS未知；S034明確代理拒絕 | 等待正常可用的官方取件及CRS／存取條件；未繞行、未偽造完整 |
| S019／S031／S033／D1 | 保留既有市政會議列表與附件、消防快照、市政RSS及SEGIS固定期證據 | 會議正文日期／編號／所有附件版本、快照語意、RSS對官方歸檔、D1用途及真人理解未閉合 |

離線工具明確標註其本地契約範圍，不能以 caller-supplied SHA、metadata、完整假鏈或一個資料集合推定官方全量／權利批准。CSV描述列必須緊接header且只出現一次；私有逐列hash索引以0600保存，不公開，也不視為已匿名化。JSON集合品質異常、缺期或重複保留非零exit供操作人追查，不自動刪原值。

Claude Code 2.1.292 已由官方 npm package 安裝。CLI登入已啟動，等待使用者的官方一次性授權碼；在實際登入和模型呼叫完成前，Claude執行狀態是 AUTH_REQUIRED／NOT_RUN。不能把其他 agent 的覆核當成 Claude CLI 執行。

[15項台帳](source-acquisition-targets.v1.json)、[品質重跑聚合](reference-quality-disposition-20261007.json)、[S032末頁](s032-terminal-navigation-observation-20261007.json)、[有界續跑](s032-bounded-resume.md)、[歷史草案枚舉](s026-history-enumeration-20261007.json)、[範圍與日期收據](source-scope-repair-20261007.json)、[待真人權利覆核包](source-rights-review-packet-20261007.json)。

所有15項仍沒有本輪新取得的正式完整性／權利准入，model_transmission_allowed與promotion_eligible均為false。真實自然日仍按實際Asia/Taipei觀察日累積，同日重跑不增加天數；既有七日證據不因本輪兩日批次而被宣稱失效或重新完成。正式查詢仍應保留RIGHTS_BLOCKED，provider啟用與模型／真人驗收須以另行實際證據核對。
