# Issue #28：警政署官方資料矩陣

`npa-source-inventory.v1.json` 是警政署 OPEN DATA／警政統計的單一盤點入口，將來源分成 `PRIMARY_EVENT`、`PRIMARY_REFERENCE`、`ENRICHMENT` 與 `EXCLUDE_OR_AGGREGATE_ONLY`，並保存資料期別、更新語意、欄位、來源條款、重疊關係與接入狀態。

目前 checkout 已完成：

- Batch 1 的集會遊行、重要統計、反詐執行成效與 165 三類資料 fixture adapter。
- `S-036` 集會遊行與 `S-037` 警察機關地址的 catalog 綁定；兩者仍是 candidate，沒有 production collector 或部署宣稱。
- 集會遊行的時間／路線／主管機關 identity 與 semantic-change receipt，並可和臺中交通公告 fixture 融合成一個 `PublicEvent`。
- 重要統計保留 `period`、`table_id`、`value` 與官方註記；週統計、月報、年報與專題資料都標成 reference，不是即時事件。
- 165 舊資料與 `CTX-165` 共用 `NPA-165-FAMILY`，同網域只計一次並保留 superseded 關係。
- A1 CSV／JSON 共用 `NPA-A1-ACCIDENTS`；臺中 `S-034` 與全國 reference 以穩定事故 identity 合併，不新增第二個事件。
- 個別失蹤人口、失竊車／車牌與失物個案明確阻擋在公開 canonical feed 外；只允許後續採用彙總統計。
- self-check 會輸出離線 fixture 的資料量、更新頻率類別、解析失敗、schema drift、實際事件數與交班數；它不是成功下載或 live receipt。

驗證：

```bash
python -X utf8 scripts/npa-source-inventory.py --self-check
python -X utf8 -m unittest discover -s tests -p "test_npa_source_inventory.py" -v
```

尚未宣稱完成的外部 gate：live metadata/resource receipt、production collector、資料庫持久化、公開 UI 寫入、部署與真實使用者收件。來源故障仍遵循既有 `FAILED/PARTIAL` 不覆蓋 last-known-good 規則。
