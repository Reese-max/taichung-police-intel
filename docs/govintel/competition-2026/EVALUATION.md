# GovIntel AI｜評估計畫與結果

查核日：2026-10-02。這是評估 protocol 與目前結果邊界，不是成效報告。尚未實測的數字不填入結果欄；`evaluation-manifest.template.json` 的 `status` 保持 `NOT_RUN`，結果欄保持 `null`。

## 比較設計

所有方法使用相同資料 cutoff、事件切分、可取得來源與任務條件：

- **A：人工基線**，承辦人直接查相同官方資料並完成交班。
- **B0：舊 V2 原型**，固定舊 commit，用來量產品差異；不可冒充只差模型的消融組。
- **B：同一新版工作流程、關閉 semantic AI**，保留相同來源、版本、UI 與規則。
- **C：完整 GovIntel**，啟用受控候選抽取／關聯／修訂判讀。
- **C−AI：語意能力消融**，只在規則、來源、版本與 UI 不變時使用。
- **C−D：移除選定 background dataset**，對應既有 template 的 `C_minus_D` 欄位；它衡量背景資料是否真的改善任務，不等同 AI 消融。

先以約 20 個事件、60 份原始文件建立開發與保留集；同事件的版本、轉載與未來文件不得跨集或洩漏到歷史重播。兩位標註者先建立標準答案，分歧保留裁決紀錄；模型自評不能取代人工答案。

## 目標與結果

| 指標 | 計畫目標／定義 | 目前結果 |
|---|---|---|
| 重要異動 precision／recall | 事前定義時間、地點、範圍、狀態等影響交班的變更；暫定 precision ≥ 0.85、recall ≥ 0.90 | `NOT_RUN`；無 TP／FP／FN 分母。 |
| 事件配對 | false merge、missed merge 與 event precision／recall／F1；暫定 F1 ≥ 0.90 | `NOT_RUN`；fixture 不是標註集。 |
| 引用支持 | 重要主張是否由正確 document version／evidence locator 支持；unsupported claim、abstention 分開報 | `NOT_RUN`；沒有支持率或零錯誤證明。 |
| 任務效率 | 從閱讀到可交付，包含人工核對與修正；比較 A、B、C，不把拒答當成功 | `NOT_RUN`；沒有真人 participant 或分鐘數。 |
| 漏件／誤提醒 | 重要漏件不增加；分別列 false alert、missed change、conflict 與 unknown | `NOT_RUN`。 |
| 內政背景增益 | 比較含／不含背景資料的查找時間、欄位正確率與限制理解 | `NOT_RUN`；歷史統計不等於即時人流。 |
| 成本／延遲 | 分列 provider、token、運算、儲存、人力；官方修改時間未知不計假延遲 | `NOT_RUN`；不預設免費或 SLA。 |
| 真人使用／採用 | 取得同意後以匿名 participant code、受控任務與方法順序記錄 | `NOT_RUN`；不宣稱機關採用、得獎或全臺泛化。 |

## 可重播的工程證據（不是產品結果）

```bash
npm run check
python3 -X utf8 scripts/public-event-fusion.py --self-check
python3 -X utf8 scripts/verify-source-policy-integration.py --self-check
```

這些命令證明程式契約、fixture 或離線核心可執行；不能轉述為真人成效、live provider accuracy、source coverage 或 deployment freshness。每次正式評估另需保存 commit、dataset manifest hash、information cutoff、測試命令與退出碼、分母、失敗案例及時間。

## 結果填寫規則

- 零分母填「不可評估」或 `null`，不填 100%。
- 目標與 observed result 分欄；目標達成前不能寫成結果。
- 歷史／合成／預先生成資料要標記 fixture；不把預錄操作說成即時服務。
- 原始 participant data、consent、私人回饋、credentials 與內部勤務資料留在私有受控位置，不提交公開 repo。
- 只有通過 evidence／safety／release gate 的資料才可進公開成果包。

Template：[evaluation-manifest.template.json](./evaluation-manifest.template.json)。產品流程、安全邊界與樣本限制詳見 [GOVINTEL_PLAN.md](../GOVINTEL_PLAN.md)。
