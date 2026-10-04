# GovIntel AI－首期架構與責任邊界

首期定位為公開查詢與同一瀏覽器追蹤。以下把已部署 baseline、候選功能與規劃分開；能力與時間化 receipt 以[CURRENT_STATUS](CURRENT_STATUS.md)為準。

## 已部署公開基礎

```text
五個核准官方來源 → Python collector／source policy
                          │ health／coverage／LKG／版本與hash
                          ▼
              同generation publication bundle
                          │
       publication-state checkpoint → Next.js static export → Pages
                                                      │
                                      metadata/link-only Query Worker
```

Pages 驗證五個公開檔案 bytes/hash；Gateway 驗證同 publication generation 與受限能力。兩者成功不會把`PARTIAL`來源變完整。當前 Worker 的 publication metadata/sourcehealth/council 能力不是 production PublicEvent 或 statistics store。

## v6 候選本地路徑

```text
已取得的公開資料／明示合成replay
                  │
        公開query → 可見條件／原文／時間與資料狀態
                  │
        使用者明確保存條件 → 同瀏覽器localStorage
                  │
        開站／刷新 → 比對版本與已讀 → 站內未讀實質更新
                  │
        原文核對／標已讀／修改條件／取消／清除
```

PR124 query、125 tracking、126 replay 在驗收中。關閉網站時不提供個人背景推播，browser storage 清除可能失去清單。跨裝置帳號、外部通知、多人審批與 handoff／研究匯出為後續，不能當首期完成條件或現有 production 能力。

## 可重播核心與尚缺功能

SourcePolicy、locatedfacts、角色 projection、ReviewInbox 與 PublicEvent fixture 已有受限核心。deterministic keyword 或時段規則不能冒充語意模型；AI 理解／抽取／融合／修訂須另有 provider、prompt、輸出、失敗及成本 receipt。

D1 已取得固定 112Y12M 官方 CSV，核對全國 368 記錄與臺中 29 區；本地 query UI 提供行政區選單、人口／戶數／代碼、來源與 hash。這是 `VERIFIED_HISTORICAL_BACKGROUND_SAMPLE`，未 promotion，不能用 114Y12M JSON service 或取得時鐘代替其歷史期別。D2 metadata 可取得，但官方 ZIP／native TGOS 403，資料列／CRS 未驗證。真人用途效益、語意 AI 與保留集品質另驗。

四類官方候選及額外 S-019 尚需有效觀察／完整性／權利與 promotion；本地 workflow 補入 S-019 不會自動產生七日證據。媒體發現尚需 live／14 日 shadow。日期、未知與衝突保留，不從來源消失或預定日期推論解除。

## 控制與安全

官方網頁／附件與模型文字是資料，不是程式或工具指令；URL／下載量／權限受限。規則保護 identity、hash、版本、schema、來源資格與發布；AI 只能產生可回查候選。公開 output 不含私人追蹤、參賽者資料、使用者原始紀錄、內部勤務或案件級個資。D1 不是現況人流，D2 不是管轄／派遣建議。詳見[LIMITATIONS_AND_SAFETY](LIMITATIONS_AND_SAFETY.md)。
