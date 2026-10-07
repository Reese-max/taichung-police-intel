# 2026-10-07 Claude Code 實際覆核與後續修復

Claude Code 2.1.292 已完成官方登入，並於 2026-10-07 10:25:03–10:26:35 UTC 實際完成一次公開程式碼覆核。供應商回應報告模型 `claude-opus-5-5`，exit code 為 0、`provider_error=false`，耗時 92.23 秒。覆核對象為程式版本 `85260b938b51e9a24ec3aabbb851e2263bd88356` 的 S032 checkpoint 模組；輸入只有公開程式碼，沒有官方來源原件、登入憑證或授權連結，模型沒有啟用工具。

第一個 `--bare` 呼叫仍保留為失敗紀錄：當時官方登入狀態已成功，但該模式不讀取 OAuth/keychain，呼叫 exit code 為 1，未報告任何實際模型。第二次移除 `--bare`，同時明確停用 hooks、tools、plugins、MCP、automatic memory 與 session persistence，才完成真正覆核。[結構化證據](claude-code-followup-20261007.json)分開保存兩次結果，不將第一次失敗當成成功。

## Findings 與獨立重播

以下重播使用虛構 HTML 和 FakeSession。Claude 未看到實際 navigation helper 全部實作，因此涉及該 helper 的推論必須再依真實程式行為判斷。

| 項目 | 實際基線行為 | 本文件的狀態 |
| --- | --- | --- |
| Runtime wrapper 改變 row parser | `functools.wraps` 可令 source fingerprint 保持原值，兩列卻變成一列；獨立代理重播成立 | 已修復，虛構回歸通過 |
| 先 import 再改磁碟程式，舊 bytecode 使用新 source hash | Claude 指出 loaded bytecode / disk source 不一致風險 | 同長度磁碟變更／loaded bytecode 不一致回歸已拒絕，來源不執行 |
| Image-alt Next→2／Last→9 可能誤封末頁 | 現有 helper 已回傳 unresolved，collector 拒絕封存 | 這個反例已被基線拒絕 |
| Next→2 與 Next→3 的多重目標 | 現有 helper 已回傳 unresolved，collector 在下一頁 GET 前拒絕 | 這個反例已被基線拒絕 |
| Page 3 的 Previous→1／Previous→2 衝突 | Normalizer 接受同角色衝突，helper 可把這頁視為 terminal；獨立代理重播成立 | 已修復，虛構回歸通過 |
| 同頁同 stable ID 的 title/date 衝突 | Parser 先依 ID 去重，第二列的 metadata 衝突消失；為獨立代理另發現 | 已修復，虛構回歸通過 |

已同步主分支 `1d55c059`，保留 PR #175 的不安全 pager／非 CSV archive 修復。這次將 callable binding 改為實際 bytecode、遞迴 constants、literal defaults、Python runtime 等指紋，並以當前磁碟 compile 結果比對 loaded function；只 compile／parse，不執行來源。明確註冊所有 checkpoint helper、row/date/navigation helpers 與 checker helper。獨立整合覆核另外重現「替換 aggregate checker 返回舊 hash」缺口，已加 import-time 原函式 identity／code／defaults 防護，於 dispatch 前拒絕替換，該 guard 本身也在註冊檢查中。

同角色不同導航目標、非相鄰 Previous 與同頁 stable ID 的 metadata 衝突均停止；首頁合法 Previous=1、相同控制／metadata 重複仍接受。CP 明確啟用 strict 同頁檢查，其他一般 collector 保留原預設。

170 項互不重疊重點測試通過：checker23、runtime整合7、checkpoint40、Parser13、terminal11、collector30、budget8、candidate19、transport19。原主分支上的3項root wrapper整合反例確實失敗；另外省略 bootstrap guard 的隔離重播3項反例也失敗，修復後7項整合測試通過。代理測試與這170項重疊，不另加總。完整 gate/build、必要 exact-tree CI／Chromium、合併與正式站驗收須由後續獨立收據記錄，本文固定在建 PR 前的實際狀態。

最终功能程式 `04e9df84fc740dd3368449a75471429d9fc3b9f9` 實際用5次正常官方GET：原入口4頁／39IDs，加一個單獨第82頁末頁檢查。控制器後續文檔 commit 不改該功能模組 bytes。[相容收據](s032-runtime-binding-compatibility-20261007.json)只保存hash／時間／聚合，沒有原body、正文或附件。較早bootstrap修復前的開發版另有5次相容請求，保留為歷史，不當最终程式驗收；本輪實際總共10次GET，都在同一自然日，沒有增加觀察日。這不是82頁新版全鏈或獨立完整性。

Runtime binding 是明確註冊程式／設定的本地一致性檢查，不是對任意 interpreter、第三方依賴或未註冊 global 改寫的安全邊界。相同跨頁重複／來源漂移仍不提供獨立完整性；未驗證的第三方 dependency／例外類型等觀察未虛稱已全部修復。

## 驗收範圍

這次是真實 Claude 的公開程式碼覆核，與正式產品的來源查詢／模型測試是不同驗收。正式產品 `semantic_ai_trial=NOT_RUN`、`human_trial=NOT_RUN`，來源權利仍未核准，`new_source_admissions=0`、`model_transmission_allowed=false`、正式查詢仍 `RIGHTS_BLOCKED`，provider 仍 `DISABLED`。

來源與 checkpoint 的 `window_completeness=PARTIAL`、獨立完整性與正文／附件驗證均未成立，15 項來源的完整業務範圍也未全數驗收。修復後若 runtime binding 改變，必須從原核准官方入口新建 checkpoint；既有 V1 與舊 V2 觀察保留原 code／hash／時間，不改 hash、遷移或宣稱為新版完整鏈。發布程式碼本身不增加自然觀察日。
