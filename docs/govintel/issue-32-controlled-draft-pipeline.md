# #32：逐句草稿與必要回答核對

Web 的非空 metadata 查詢現在先完成 `search_evidence → structured draft → claim extraction → validate_answer`，才顯示回答。共用閘門仍由伺服器的官方 evidence catalog 決定支持；瀏覽器不傳入 evidence／trust fields，也不展示 adapter 的原始草稿。查詢結果的官方連結屬於索引；回答另以「索引摘要」標示，核對按鈕只重試已完成的必要核對。合法零結果不創造草稿，也不取代 query coverage 的限制。

`runControlledAnswerPipeline` 可接入明確的 structured draft adapter。預設使用 publication title 的 metadata template，**沒有呼叫 LLM**；adapter 測試也只是離線受控 fixture，不能當成模型、實際官方時間／地點／統計或部署的驗收證據。

草稿格式是 `schema_version: 1` 與 1–32 個 `statements`。每句必須有 factual `claim_type`（TIME、LOCATION、STATUS、CAUSE、STATISTIC、AGENCY）、CURRENT／HISTORICAL scope、一個 subject/value proposition 與 citation IDs。`text` 必須完整等於該命題值的受控文字；統計值還須同時具備精確 decimal 字串、period、geography、unit。自由文章、命題值以外未對應的額外文字、OTHER、空引用或混入 evidence 都會拒絕，不能偷偷退回標題摘要。這個文字相等檢查不能證明任意 value 在語意上只有一個事實，也不能提取並核對複合 value 內全部事件事實；adapter 仍須把不同事實分句，完整語意抽取尚待驗收。

`publication:*:title` 與 `publication:*:source_id` 在與共用閘門一致的 NFC／去空白正規化後，只能使用 STATUS。TIME、LOCATION、CAUSE、STATISTIC、AGENCY 不能借用索引命題取得其他事實類型的支持。官方標題即使同時提到時間、原因或數量，STATUS 收據只證明索引標題文字，沒有證明其中每一個事件敘述。

Client 比對查詢 generation、publication hash、每句的 receipt ID／type／text／normalized proposition、renderer version 與實際 answer hash。少核對一句、替換命題、使用別版收據、或修改 rendered answer 都不能釋出。只顯示伺服器的受控 renderer 結果；未定位的原因明示「官方來源未說明原因」，其他未支持事實移除，過期或衝突保持 qualification。

實際 Worker 的離線 HTTP／release fixture 已驗證 metadata positive、四類未定位事實的拒絕／qualification、自由文章拒絕、零結果與 metadata 型別／正規化邊界。現有 Worker catalog 僅支持 publication title/source 的 metadata assertions；本輪沒有新增可定位且已核准的官方事實 artifact，沒有把 client facts 當作證據。Python gateway 既有的受控 located-facts/statistics admission 仍需各自有效的 canonical source、review、document/version/hash/locator binding。

這輪修復不代表 #32 全部字面驗收完成：實際 LLM draft adapter、完整官方 typed-fact 發布／Worker admission、真人與 production release 驗證仍待獨立收據。
