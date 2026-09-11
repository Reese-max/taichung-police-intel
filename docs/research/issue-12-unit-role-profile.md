# Issue #12 — 可版本化 Unit／Role Intelligence Profile 與角色化 Priority Brief

Research deliverable for `Reese-max/taichung-police-intel#12`。
`affected_roles` 已存在於 event；缺的是 versioned reusable
UnitProfile/RoleProfile 讓同一事件依 profile deterministically rerank
並說明 why-for-this-role。

## 1. Schema

### 1.1 `UnitProfile` / `RoleProfile`

```jsonc
{
  "profileId": "prof_<ulid>", "profileVersion": 1,
  "unitName": "交通大隊 | 刑事警察大隊 | ...",
  "roleTerms": ["traffic","dui","accident"],       // 非敏感關鍵詞
  "topics": ["public-safety","traffic"],
  "sourceScope": ["S-001","S-019"],                // 只列 public source id
  "metadata": { "note": "非敏感描述" }
}
```

**Schema validation 對敏感欄位 fail closed**：人名、案件 ID、私人聯絡
資訊、內部勤務、operational deployment field → 直接拒絕。

### 1.2 `ProfiledBrief`

```jsonc
{
  "briefId": "brief_<ulid>",
  "profileId": "prof_...", "profileVersion": 1,
  "generatedAt": "...",
  "items": [
    {
      "eventId": "...", "rank": 1,
      "relevanceScore": 0.87,
      "whyForThisRole": ["topic:traffic 命中 roleTerms","affected_roles 含交通"],
      "lifecycle": "current | stale"
    }
  ]
}
```

## 2. 規則

- rerank 是 **deterministic**：同 event set + 同 profile → 同 brief。
  score = topic 命中 + roleTerms 命中 + affected_roles 命中 + freshness。
- `whyForThisRole` 必須列出具體命中項，不只給分數。
- profile config 放敏感欄位 → schema reject（fail closed）。
- profile version 變更 → brief 記 `profileVersion`；可回放「舊 profile
  會怎麼排」。

## 3. Fixtures

| Fixture | 情境 |
|---|---|
| `deterministic-rerank` | 同 input + 同 profile → 相同排序 |
| `why-for-role` | 每個 item 的 whyForThisRole 非空 |
| `sensitive-field-rejected` | profile 含人名/案件 ID → schema fail closed |
| `profile-version-replay` | v1 vs v2 profile 產不同 brief，各自帶版本 |
| `no-role-match` | 無命中 → 事件仍列出但 relevanceScore 低，不隱藏 |

## 4. 不做什麼

- 不做 ML 推薦器——deterministic scoring only。
- 不在 profile 裡存任何 operational/個資欄位。
- 不隱藏「與此角色無關」的事件——只降排序，仍可查。
