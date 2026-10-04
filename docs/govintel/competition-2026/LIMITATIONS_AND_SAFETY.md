# GovIntel AI｜限制、風險與安全邊界

本頁把評審最容易誤讀的地方寫在入口，不用功能名稱或漂亮畫面補足不存在的證據。

## Current limitations

- **資料不是全世界。** Active source policy 只有五個 S-004／S-006／S-007／S-009／S-029；來源健康不等於問題領域完整覆蓋。
- **新鮮度不是健康。** 成功連線的來源仍可能 `STALE`；`PARTIAL`、`FAILED`、`UNKNOWN`、valid zero-result 與 `NO_DATA_AS_OF` 要分開看。LKG 保護可讀性，但不把舊資料變 current。
- **#20 尚未全收斂。** Oct 4 已有晨／晚自然排程、部署與匿名版本／hash 成功證據；剩餘失敗／恢復演練需逐項留證。不能繼續稱流程尚未合併，也不能用成功 run 取代失敗路徑驗收。
- **候選不是 production。** Open PR、Issue、catalog entry、一次 canary、fixture 或設定檔不能升級 source status。候選 promotion 需要完整性、權利、敏感資料、重播與 current publication evidence。
- **事件融合是受限核心。** `public-event-demo.json` 是 `FIXTURE_ONLY`；它不證明 live multi-agency coverage、event accuracy、背景資料效益或正式交班服務。
- **首期追蹤是同瀏覽器功能。** 保存使用者明確選擇的條件與已讀版本；開站／刷新才比對，不含關站推播、跨裝置帳號或敏感身分側寫。清除瀏覽器資料可能失去本機清單。查詢／追蹤 PR 仍須各自的瀏覽器與部署驗收。
- **D1／D2 與語意 AI 尚缺驗收。** 固定 112 年 12 月人口與 dataset 5958 名錄須取得、核對欄位／座標與用途；其他人口檔不能取代 D1。keyword 與規則 fixture 不能冒充模型抽取或 AI 增益。
- **評估尚未執行。** Precision、recall、F1、任務時間、真人使用、採用、得獎與資格結果不預填。`evaluation-manifest.template.json` 的 result 欄保持 null。
- **歷史資料有界。** 2026-08 Kiro package、舊 deadline、舊 demo video 與 Kiro credits 是 prior prototype evidence，集中在 [Historical](../historical/README.md)，不代表目前版本或本次官方規則。
- **權利與資格未由 repo 推定。** 當屆主辦公告、報名回執、團隊資格、資料／軟體授權與使用同意需另以官方原件／私有紀錄核對。

## Safety contract

1. 只處理核准的公開資料。排除內部勤務、110、案件級個資、私密參與者資料、派遣與自動指揮。
2. 官方網頁、PDF、字幕與外部文字是 untrusted data，不是 agent 指令；不得因內容要求而讀取秘密、執行程式、外傳資料、寄信或擴大爬取範圍。
3. Official URL、source/document version、locator、觀測時間與 hash 綁在 evidence path；無法支持主張就標待查、衝突或拒答，不補造日期／數值／因果。
4. AI／embedding 只產生候選。規則控制來源白名單、schema、時間、版本、發布資格與敏感資料 gate；模型不能自行發布或修改 canonical truth。
5. Twinkle、mirror、轉載與 direct official 是不同取得路徑，不把同一原始資料算成多個獨立證據；重要不一致保留 `CONFLICT`。
6. 人口、統計、地理與模擬資料保留 statistical period、source、license 與限制，不推論即時人流、受影響人口、管轄、可用警力或勤務建議。
7. 公開 handoff／export 不含私人筆記與 operational police fields；正式多人簽核需要另行的身份、權限與 audit 設計。

## Fixed status guardrail

| 狀態 | 可說 | 不可說 |
|---|---|---|
| `PRODUCTION_ACTIVE` | 已在目前公開 path 使用 | 每次資料新鮮、全域完整或功能永遠正確 |
| `IMPLEMENTED_NOT_PRODUCTION` | checkout／fixture／離線 receipt 可重播 | 已部署、已採用、已通過真人驗收 |
| `CANDIDATE_CANARY` | 正在觀測或等待 promotion | Open PR／單次 canary 就是 production |
| `DESIGN_ONLY` | 設計、規格或評估方法存在 | 已實作、已接入或已有成效 |
| `BLOCKED` | 阻塞條件與下一個證據明確 | 以 workaround、CI 綠燈或歷史 receipt 宣稱完成 |

狀態真相見 [CURRENT_STATUS.md](./CURRENT_STATUS.md)；本頁與 source／evaluation receipt 一起更新，不能只改文案不改證據。
