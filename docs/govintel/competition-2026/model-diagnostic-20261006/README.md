# 實際免費模型合成診斷

使用官方 OpenCode CLI 1.18.33 與 `opencode/mimo-v2.6-flash-free`；只送既有、專案自撰的合成案例 input，未送 expected 標籤、正式政府資料或真人紀錄。模型完成步驟的 CLI 費用為 0；這不是獨立帳務總額核實。

| CLI attempt | 結果 | 意義 |
| --- | --- | --- |
| 1、2 | 供應商 403 | 停用工具的自訂模式遭免費層拒絕；沒有可評分答案 |
| 3 | 11/12 exact | 提示漏列 `STALE` 這個既有合法類別 |
| 4 | 12/12 exact | 使用修正的提示 v2；同一份開發用 seed 再跑 |

12 個案例包含事件配對、重要修改與主張支持；另三個 query 案例沒有輸入 corpus，明確排除。兩次模型回答與評分都保存，沒有改寫第一次失敗。這是同一組 synthetic development seed 的提示修正，**不是保留集準確率、真人成效或完整 A/B/C/C_semantic_off 產品評測**。

重跑需新 output 目錄，避免覆蓋失敗紀錄：

```sh
python scripts/evaluate-free-model-synthetic.py --output /tmp/govintel-free-model-diagnostic-new
```

此命令會呼叫免費供應商，不能放進一般 CI。CI 只執行 `test_free_model_synthetic.py` 的 offline guard checks；非合成資料、供應商錯誤、工具呼叫與截斷回答均拒絕計分。實際 workflow 的 provider network request 總數沒有獨立計量；raw CLI events 私有保存。

真人 protocol 見 `../human-study-protocol.v1.json`：3–5 位經同意受測者、約 20 events／60 documents、事件／時間保留集、相同 cutoff／回查、counterbalanced 四方法、核對與修正時間。此輪 actual participants/tasks 仍為 0；正式資料權利與產品准入未完成前不啟動正式真人比較。
