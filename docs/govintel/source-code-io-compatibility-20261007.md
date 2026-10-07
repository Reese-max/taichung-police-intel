# 2026-10-07 程式檔有界讀取與 Python 3.14 相容修復

PR #176 合併後的兩條追加覆核（4206050788、4206050794）已重現並修復。實際 CPython 3.14.7 會將 slice 放入相關函式 co_consts，原白名單使 binding／new_checkpoint 在 transport 前失敗；現在遞迴、確定性編碼 slice 三個欄位，保留型別、深度與循環拒絕。舊 nested-code 測試只改 co_consts，不能保證改變3.14 LOAD_SMALL_INT 的 operand；現改為編譯不同虛構函式並替換實際 nested CodeType。第一次110項fixture失敗與中間110項通過證據均保留。

本地程式檔讀取現在先用 nonblocking descriptor／fstat 要求 regular file 與大小不超過4MiB，再最多read(limit+1)，檔案在stat後增長仍有界；所有路径關閉FD。Module hash的前後兩次讀取及當前來源compile共用此reader。這是程式檔輸入byte上限，未宣稱AST／整體程序只占4MiB，也未變更官方網頁transport或CSV原件處理上限。超大sparse檔在配置讀取buffer之前拒絕，FIFO不等待writer。

獨立整合覆核另找到defaults普通相等比較把False視為int0，且可能呼叫自訂__eq__。舊完整collect仍於transport前拒絕，沒有封存偽造成功；但前置guard會先讀9次程式檔，custom hook執行2次。現採原生型別／shape比較，兩個新反例均在任何程式檔讀取前拒絕，custom hook=0／transport=0；10個helper替換與有界IO反例亦通過。

本地Python3.12共有180項互不重疊重點測試通過。實際Python3.14.7最終112項（checker31、runtime integration9、checkpoint40、Parser13、budget8、terminal11）全通過；它們與180項重疊，不相加。[實際3.14證據](python314-checkpoint-verification-20261007.json)完整保留基線、staged fixture失敗、中間通過及最終通過。CI新增Python3.14專用job，保留完整gate與Chromium兩個既有job；所有三job與exact-tree artifact須通過後才可合併，正式部署仍需後續独立收據。本文件固定在建PR前的實際狀態。

S032已在程式2a48c4a下實際完成新版21批、82頁、814個唯一ID、36個窗口列表項的本地一致性驗證；原prefix4HTTP加20個新批78HTTP，每批仍為預設4頁／6HTTP。從10:48:36UTC的prefix至11:07:19UTC終末，不是原子快照。[歷史V2終末聚合收據](s032-v2-terminal-observation-20261007.json)保留原code／binding／hash／時間，不遷移為本輪新binding。本輪新增有界reader使binding改變，已由原官方入口重新跑4頁／39IDs，再單獨查82末頁，共5正常GET；[新相容收據](s032-code-io-compatibility-observation-20261007.json)不稱為新版82頁完整鏈。

所有驗證仍是LOCAL_TRAVERSAL_RECEIPT_ONLY，PARTIAL／whole history UNKNOWN；正文／附件／獨立完整性、權利、promotion均未成立，0新來源准入，model_transmission_allowed=false。正式query仍RIGHTS_BLOCKED／provider DISABLED；Claude已實際公開程式覆核，但產品AI與真人測試均NOT_RUN，發布／同日重跑不增加自然日。來源原body與私有checkpoint／逐列索引未提供模型或公開artifact。
