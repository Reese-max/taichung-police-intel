# GovIntel AI－v6 評測協定與結果界線

2026-10-05 更新。以送件 v6 p7–9 為準；9 月舊計畫中的 A／B0／B／C 定義保留作歷史，不混用在本次比較。結果仍為 `NOT_RUN`，目標尚無實測達成證據。

## 方法與分組

- **A 官方人工**：直接查各官方網站，自行記錄條件與回查更新。
- **B 搜尋加一般摘要**：相同來源與問題範圍，使用搜尋及一般摘要，不故意弱化基線。
- **C 完整 GovIntel**：公開查詢、原文依據、個人條件、版本與站內更新。
- **C_semantic_off 消融**：保留 C 的介面、資料、追蹤與已讀規則，只關閉語意處理。C 對此組才可解釋語意 AI 的額外貢獻；B 與 C 比較整體產品流程。

所有方法固定同一問題、可取得資料、資訊截止與回查／重開站時點。B 的摘要工具、模型與 prompt、C 的版本需記錄；若尚無語意 AI 實作，C_semantic_off 不可冒充已完成 AI 比較。可另保存 frozen V2 作歷史工程基準，但不充當 B 或消融組。

第一輪**規劃**約 20 個事件／60 份原始文件，各版另計，真人**規劃**3–5 位經同意使用者。真實修訂、合成道路序列與 fixture 工程結果分開報。按事件分開開發與保留集，同事件版本、轉載不跨集，歷史 replay 不使用截止後文件。人工讀原文與差異建立答案；分歧保留裁決，未解決案例另列，不由模型裁定。

## 指標與分母

| 指標 | 目標／計數方式 | 目前結果 |
|---|---|---|
| 有來源查詢正確性 | 答對、答錯、未回答、原文支持與可回答比例；不得有無依據／矛盾斷言 | `NOT_RUN`／null |
| 查詢完成時間 | 相對 A 中位耗時降低至少 30%，正確性不降低；包含開原文、理解條件與修正 | `NOT_RUN`／null |
| 站內重要更新 | 每次指定開站時點「已取得、符合條件、未讀、實質」更新為分母；precision≥85%，recall≥90% | `NOT_RUN`／null |
| 重複／誤列 | 已讀、純排版、同源轉載不多列重要提示；不同實質更正不被去重漏掉；取消後不提示 | `NOT_RUN`／null |
| 資料與背景限制 | D1 固定 112/12 期別；D2 名稱／地址／電話／座標可核對；限制理解與欄位正確性 | `NOT_RUN`／null；D1/D2 尚未取得驗收 |
| 來源取得缺漏 | 未取得來源／文件另報，不以已取得資料的高召回代表全網涵蓋 | 未完成 promotion；公開基線仍`PARTIAL` |
| 成本 | 每查詢、有效更新、追蹤條件的 model／運算／儲存／人工成本；有模型時保存當時費率 | `NOT_RUN`／null |
| 真人體驗 | 經同意匿名使用者同等難度任務／方法順序輪替，記錄時間、正確性、漏件與困難 | `NOT_RUN`／null；個人試用不是機關採用 |

解除需明文；資料消失、日期到期與來源失敗不可當解除。首期不評估推播投遞。零分母寫 null／無可評估案例，不能填 100%。真實與合成不混算，小樣本無錯誤不意味永久可靠。

## 工程與真人結果各自留證

[PR #126](https://github.com/Reese-max/taichung-police-intel/pull/126)是候選工具，CI、自我檢查、合成更新精確率或固定資料 replay 只能證明該 fixture／contract。真人 A/B/C 耗時、模型 quality、source coverage 與 public deployment 須另外的 receipt。

該候選的工程範圍為 `SYNTHETIC_DETERMINISTIC_POLICY_REPLAY`：6 份來源文件、2 個來源、13 個版本樣本，未提供事件 ID，因此事件數為 null。固定案例的 6 TP 是規則代理指標；候選中切換已讀規則或 deterministic interval matching，並沒有實際執行 B 的搜尋加一般摘要，也沒有 C 的語意 AI 關閉消融。四組 v6 方法執行狀態均維持 `NOT_RUN`；沒有 UI、真人、真實資料或模型成效可由此推出。

```bash
npm run check
python3 -X utf8 scripts/public-event-fusion.py --self-check
python3 -X utf8 scripts/verify-source-policy-integration.py --self-check
```

[Manifest template](evaluation-manifest.template.json)保持結果 null；每次研究填入 commit、data hash、split、cutoff、方法與工具版本、分母、失敗、時間與成本。若來源未通過，先交付明示 cutoff 的 offline query／tracking replay；不宣稱持續最新服務。

原始同意書、participant 紀錄與個人追蹤保持私有；公開成果只放已核准的匿名彙總與合法 fixture。
