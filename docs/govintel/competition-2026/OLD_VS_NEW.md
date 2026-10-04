# GovIntel AI－既有基礎與 v6 新增範圍

新增方向不等於已部署；主辦創新／資格認定另待官方確認。

| 項目 | 既有／歷史基礎 | v6 首期與目前邊界 |
|---|---|---|
| 名稱／使用者 | Taichung Police Public Intelligence，議會備詢／交班技術方向 | GovIntel AI－公共資訊查詢與個人化追蹤平台；民眾及公開資訊研究／業務人員。 |
| 主流程 | priority brief、health／gap、官方影音導航 | query→保存地區／議題／路段條件→重開站查看更新；query／tracking candidate 待驗收與部署。 |
| 來源 | 五個核准議會／市政來源與 S-010 影音 | 首期四類 S-001／S-032／S-033／S-031 仍未 promotion；S-019 不是首期四類；同源市政與轉載不重複算佐證。 |
| 版本 | identity、hash、LKG、source status 與異動核心 | 未讀重要更正、純排版去重、明文解除、取消條件；合成道路 v1/v2/v3 與真實 sample 分開。 |
| 追蹤 | local-first handoff／ReviewInbox | 同一瀏覽器條件與已讀，開站／刷新比對；無關站 push 或跨裝置帳號。handoff／研究匯出列後續。 |
| AI | fixture／規則與 locatedfacts／fusion 核心 | 語意查詢／抽取／修訂規劃；keyword 與 deterministic replay 不是 model 結果，尚缺 AI 驗收。 |
| 背景 | 候選人口／法規／統計／機關清冊 | D1 112Y12M 官方 CSV 已核對／本地臺中 29 區背景選單已實作，未 promotion；D2 dataset5958 metadata 可取得但 resource 403，CRS 未驗證。不能推現況人流或管轄。 |
| 評測 | 舊 A/B0/B/C 技術計畫與工程 receipts | v6 A 官方人工、B 同 scope 搜尋摘要、C 產品、同介面 semantic-off；真人與模型執行仍 NOT_RUN，fixture policy 另外報。 |
| 發布 | 歷史 protected-main 推送失敗 | publication-state 已合併；Oct4 collection／五檔 hash／Gateway 通過，但 PARTIAL 資料、來源 promotion 與#20 剩餘 failure/recovery 另驗。 |

舊 Kirodeadline、credits 與 2:43 影片位於[Historical](../historical/README.md)。查看[CURRENT_STATUS](CURRENT_STATUS.md)與[v6 對照](PROPOSAL_MAPPING.md)，避免把歷史 receipt、OpenPR 或 synthetic 示例寫成現在公開功能或實測效益。
