# 結構化背景來源：2026-10-07 取得與品質循環

本輪是原始 CSV 的 local acquisition/audit，不是正式來源 promotion、業務範圍完整、授權准入或真人效益驗收。原始資料留在 private scratch；public repo 只保存 URL、hash、schema、筆數及品質彙總，不發布活動地點或涉詐網域資料列。

## 實際取得

見 [metadata 與原件收據](reference-source-loop-20261007.json)。S036 與 CTX165 都從當下官方 metadata 列出的唯一原始資源下載；不跟隨 redirect，最大 64 MiB，完整讀到 EOF。第一輪 8 MiB 上限不足，部分讀取被丟棄且未記為完整；第二輪有界 64 MiB 的完整原件核對如下：

| 來源 | 完整 bytes | 原始 CSV SHA256 | 解析結果與阻塞 |
|---|---:|---|---|
| S-036 集會遊行 | 12,126,117 | `8719423307dfa67931cf0d6d5884d7cb4a777bbf9a67e891e4057ffa82834314` | 77,625 CSV rows，其中首列是精確中文欄位說明；77,624 data rows，468 exact duplicate rows，10 empty cells，8 end-before-start intervals |
| CTX-165 停止解析涉詐網站 | 10,206,212 | `5964f56d1d89479d5b6fc163d885d2abecefcb597b0b1dc113ff8bf87ae61a54` | 93,839 data rows，7,183 exact duplicate rows，0 empty cells，ROC periods 11412–11509 |

完整接收一個資源不證明指定期間所有應有事件／網域都已涵蓋；尚無官方期間總數對帳。重複不能未經語意判定便刪除或當成獨立事件。S036 原件時間格式是 `YYYY/MM/DD HH:MM`，資料沒有 timezone；本輪只比較同一原件的 local strings，`source_timezone=UNKNOWN`，不自動推成 UTC/Taipei。未來活動起日可為 2026-12-05；它不是未來取得時間或已發生事件。

## 可重跑本地驗證

```sh
python scripts/reference-csv-audit.py \
  --source-id S-036 --resource /path/to/original.csv \
  --expected-sha256 8719423307dfa67931cf0d6d5884d7cb4a777bbf9a67e891e4057ffa82834314 \
  --out /path/to/audit.json
```

`CTX-165` 同理使用其原件 hash。此腳本不發網路請求、不修改資料、不輸出原始活動／網域資料列。SHA、schema、ragged rows、HTML/error response、空檔或超量失敗時拒絕；品質問題輸出彙總並 exit 2；contract checks 無問題 exit 0 仍不等於業務範圍完整、權利通過或 promotion。

本輪兩個實際原件都產生 `QUALITY_ISSUES`／exit 2。8 項 fictional offline unit tests 驗證錯 hash、schema/HTML/ragged、精確說明列、重複、顛倒時間、錯月、空檔、超量與不提升用途。

## Metadata identity 修復

`npa-source-inventory.py` 原本用第一個 `/resource/` segment，MOI nested route 被誤記成 `resource_id=api`，臺中 `?rid=UUID` 則成 null。本輪改用最後資源 segment 與唯一 query rid；兩種識別衝突／多 rid 拒絕，避免 hash receipt 綁錯資源。3 red regressions 已重現並修復；既有套件合計 18 tests PASS。後續已完整下載當下 metadata 列出的 S028 18/18 original JSON resources（27,040 rows）與 CTX-POP 6/6（1,566 rows）；每個原件皆核對 13-field schema/hash 並保留期間／週期彙總，沒有輸出聯絡欄值。這是當下 metadata resource list 全檔取得，不是官方指定期別的業務範圍完整／D1 112Y12M替代品；語意對帳、合計與用途仍待驗收。S028 取得日期欄為 2025-01 至 2026-07，其中 2026-05 未出現在當下 metadata resource set，不能以已取得全部18 resources就說月份連續完整。CTX-POP 是年週期、2021–2025；跨原件出現261 exact duplicate rows（2023年），未把重複合計成新增人口。

## D2 保留原件阻塞

S037／dataset 5958 metadata 在 2026-10-07T06:56:27Z 仍 200、SHA 與 10/5 相同；唯一原件仍是 TGOS `1150930.zip`，宣告格式 CSV、7 fields。10/5 原件 403 保留原始觀察時間；本輪沒有重新請求已拒絕資源或透過鏡像、替代 host、proxy/DNS/VPN 繞過限制。metadata 200 不等於原件取得，403 無法自行確定是登入要求、政策或原站限制。原件 rows=null、CRS=UNKNOWN、coordinate_validation=NOT_RUN；POINT_X/Y 不能證明 CRS。

官方正常取件途徑、CRS 聲明／units／axis order、真實資料列和後續用途覆核仍待完成。本輪沒有寄送訊息或提出第三方申請，也沒有填寫人員覆核結果。
