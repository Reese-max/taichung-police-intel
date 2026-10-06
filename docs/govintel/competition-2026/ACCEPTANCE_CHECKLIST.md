# GovIntel AI－首期驗收清單

核對日：2026-10-06。每項完成須有固定 code/data/policy／版本、實際操作與 receipt；[驗收包](closure-20261006/README.md)分開本地、CI、匿名正式回讀、合成與真人證據。下列工程勾選不自動通過完整產品／參賽 gate。

## 已完成的工程與受限正式驗收

- [x] PR #167／#168／#169 依必要 CI 與 Chromium 通過的程式樹合併；沒有繞過 main 保護或 force-push。
- [x] 已發布 `5e2298c…` Pages／Worker；匿名五檔 bytes/hash 與精確 checkpoint 相符，Gateway release／generation／policy／hash 綁定通過。
- [x] 正式受限查詢如實 `RIGHTS_BLOCKED`／UNKNOWN，不把零結果推成世界沒有事件；研究／文件／synthesis 未將未准入內容送模型。
- [x] 正式 Chromium 15 項操作：日期／來源缺口、D1、資料回報入口、條件保存／修改／刷新／取消／清除、手機與 JavaScript 無錯誤。
- [x] 同樹 CI 的 28 項 loopback runtime、22 項有限合成 UI 與 10 項 research mocks；不是真人／模型指標。
- [x] 合成道路延長只改 10/7→10/9，09:00–17:00／A–B 範圍不變，明文 10/9 17:00 解除；已讀、取消、純排版、hash mutation 及 storage 錯誤另有有限 fixture 證據。
- [x] D1 固定 112Y12M 原始 CSV hash、368 筆／臺中 29 區、數值／代碼／合計與 UI 值通過；不是正式來源 promotion 或現況人口。
- [x] 真實隔離 artifact 打包失敗→upload 成功；兩則 #20 通知送達並回讀 body hash，回復通知指向實際失敗 comment。
- [x] Source review 引用沒有 dangling IDs；原始七日日期與條款 capture 留證，未代填任何 reviewer／decision。
- [x] 已產生固定新舊檔案差異、依賴宣告 licence 與公開 bytes／單次蒐集時間清冊；未知權利與成本仍明示。

## 完整交付尚需完成

- [ ] #39 逐來源、欄位、用途與模型傳輸有真實覆核准入；legacy active 不等於 rights approval。
- [ ] #161 正式核准官方原文正值查詢、真正關閉／重開瀏覽器、自然修訂／已讀後再修訂／明文解除的完整流程；受控副本分列 replay。
- [ ] #14 四類候選每來源七個有效日、獨立完整性、PARTIAL／LKG、敏感欄位、policy promotion；消防 1 日、警政 0 日不得補造未來／歷史 guards。
- [ ] #162 D2 原始官方資源、列數、欄位、CRS／單位／軸順序與真實用途；D1/D2 真人理解效益另測。
- [ ] #48 最小真實官方文件／版本／exact locator、被准許的正值 research 及故障證據。
- [ ] #33 兩名獨立實際人工覆核的事件保留集，約 20 事件／60 原件；正式模型／語意 off、經同意真人 A/B/C 計時與修正。85%／90%／30% 仍為目標。
- [ ] #164 真實需求訪談、至少一條任務基線及實際角色；不能以 AI persona 或 browser 操作代替。
- [ ] #20 自然晨／晚排程、實際 state persistence／deploy／public mismatch 恢復；通知真人閱讀另有證據。手動晨／晚不冒稱自然 schedule。
- [ ] #35 負載、p95、有效查詢／更新成本、raw purge／撤回與接案人員、處理時限及真實資料更正案例。
- [ ] #165 真實每週工時、六週起訖、里程碑、人員、單價／預算上限與容量假設；沒有費用或同意不自行購買／招募。
- [ ] #166 獲獎史、作品／貢獻／圖片／模型輸出權利、完整 third-party notices 與權利人同意；沒有 LICENSE 不自行授權。
- [ ] #163 原報名完整題名、代表資格／人數、資料「或／與」、更名／封面／重交指示；補件收妥不代表全部核准。
- [ ] #25 維持最多 10 頁 A4 的新版工作稿、可點擊官方引用與核對過的字型／連結；原件保留，正式重交依主辦實際指示。
- [ ] 媒體 live 授權與 14 日 shadow；交換上限不是每日新增量。

若准入／原件／真人仍未取得，可交明示版本、截止與真實／合成分類的離線公開查詢及追蹤重播包；不得包裝成持續最新、全部完整或已測效益。未完成 gate 保留 open，詳見 [#62](https://github.com/Reese-max/taichung-police-intel/issues/62)。

10/6 22:06 Asia/Taipei 的[新一輪真實 canary](closure-20261006/current-candidate-observation.json)已獨立留證：S-019 有第一個有效當地日；S-031 仍只有同一天的 1 日，重跑不增加日數。S-032／33 仍需權利及獨立完整性，S-001 失敗；全部未 promotion。較早七日窗口及其失敗不改寫，S-019 至少還需六個未來實際有效日。
