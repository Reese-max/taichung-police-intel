# GovIntel AI－首期驗收清單

核對日：2026-10-07。每項完成須有固定 code/data/policy／版本、實際操作與 receipt；[PR #177 固定驗收包](closure-20261007-pr177/README.md)與 [10/6 歷史包](closure-20261006/README.md)各自保留原始版本。本地、CI、匿名正式回讀、合成與真人證據分開；下列工程勾選不自動通過完整產品／參賽 gate。

## 已完成的工程與受限正式驗收

- [x] PR #167／#168／#169 依必要 CI 與 Chromium 通過的程式樹合併；沒有繞過 main 保護或 force-push。
- [x] PR #177 已發布 `b6a6025…`；三個必要 CI job、合併後 CI、公開五檔與精確 checkpoint、Gateway、15 項正式 Chromium 通過。CODE_ONLY 不增加資料觀察日；後續修復另驗 CI／發布。
- [x] Claude 官方登入及公開程式碼覆核完成；Python 3.14 相容性與程式碼讀取上限已修復。這些結果不當成產品模型效益、來源權利或真人實測。
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
- [ ] #14 四類候選依各自日期化窗口核對七個有效日、獨立完整性、PARTIAL／LKG、敏感欄位、policy promotion；保留既有 S032／S033 七日資格歷史，新批次同日不增加自然日，警政原入口仍 503，未達標者不補造未來／歷史 guards。
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

## #161 本機混版拒答補強（2026-10-17）

`issue-loop/issue-161` 以 `c629f82a8b16825d5d89c204c7035dcba500a9ac` 為基準，只補瀏覽器端的發布綁定核對。沿用 Worker `createReleaseManifest` 與正式驗證器的欄位契約：query／research 共用完整 release 比對；query 另核對頂層 publication／generation／policy 與 receipt，research 核對自身 generation。缺漏或混版回應以 `PENDING_UPDATE` 拒答；不完整 manifest 在 POST 前阻擋。Worker 部署資訊與 artifact 欄位順序不造成誤拒。

TDD 先得到 47 個測試中 6 個失敗（`Missing expected rejection`，以及未攔下 POST 的 `2 !== 1`），修正後同批 47/47 通過。重跑指令：

```bash
node --test --test-concurrency=1 apps/web/tests/query-release-client.test.mjs apps/web/tests/research-client.test.mjs apps/web/tests/controlled-answer-client.test.mjs
```

加入既有實際 Worker pipeline 回歸後為 63/63、exit 0；鎖定依賴與 UTF-8 的完整 Web suite 為 807/807、build 成功，離線 Chrome 腳本 12 項通過且 live provider calls 為 0。Node 原生 coverage 在兩個變更模組的行／分支覆蓋率為 99.74%／94.12%，未新增工具或門檻。完整 `npm run check` 為 exit 1：八組既有 Python gate 涉及 Windows 缺少 `O_NONBLOCK`、Unix deadline、目錄 fsync、symlink／0600 權限語意；三個最小案例在乾淨 admitted base 同樣得到 2 errors／1 failure。本次不將完整 gate 記為通過。

| #161 驗收條件 | 本次對應與剩餘 gate |
|---|---|
| 正式核准結果、canonical IDs、原文與 receipt | 待逐來源准入及正式正值回讀；未以離線 fixture 代替。 |
| 文件／版本／locator／日期／缺口與 metadata 界線 | 保留既有契約；正式原件與版本核對待辦。 |
| 加入／預覽／修改／保存及真正關閉重開、baseline | 保留既有本機追蹤；正式瀏覽器重開驗收待辦。 |
| 真實官方新版本未讀更新 | 待自然修訂；受控副本仍只能列 replay。 |
| 受影響欄位、排版去重、快取不混版 | 補強 transport 發布標籤與 generation／hash／receipt 一致性；真實修訂差異與內容驗收待辦。 |
| 展示版本已讀、後續修訂與取消 | 沿用既有精確版本已讀與取消；正式完整流程待辦。 |
| 明文解除、失敗／消失／到期不能推定解除 | 保留既有追蹤語意；正式明文解除待辦。 |
| 故障、混版、LKG／storage 回饋與不外傳 | 本次測試補混版拒答及送出前 manifest gate；既有故障／storage 回歸另跑，正式故障 trace 待辦。 |
| 桌面／390px／鍵盤及固定版本證據 | 離線 browser 腳本新增同 release 的 policy／generation 篡改；桌面／390px 通過，正式鍵盤與成功流程待辦。 |
| 同步 #25／#62、與 #33 分列 | 本頁提供 driver 可引用的工程紀錄；未對外留言或更新 issue，真人成效仍獨立待辦。 |

完整 #161 保持 **BLOCKED_BY_SOURCE_ADMISSION**。此 checkout 的核准 policy 仍是沒有 governance binding 的 legacy schema 1；工程測試沒有授予權利、promotion、部署或正式驗收，也未改寫歷史 receipt。
