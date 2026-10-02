# GovIntel AI｜目前架構與參賽邊界

本頁描述目前 checkout 可驗證的架構，不把 roadmap、Open PR 或 fixture 當成已上線系統。

## Current production path

```text
臺中市議會／市府五個核准來源
        │
        ▼
online_collect.py + source policy
        │  source health / window completeness / gaps / LKG / hashes
        ▼
apps/web/public/data/source-status.json
apps/web/public/data/intelligence-feed.json
apps/web/public/data/v2-daily-brief.json
        │
        ▼
Next.js static export (apps/web) → GitHub Pages
        │
        ├─ priority brief / source monitor
        └─ official council evidence drawer + transcript navigation
```

- Workflow entry is `.github/workflows/pages.yml`; the scheduled path persists a
  publication checkpoint outside protected `main` and checks the public bytes.
- `source-policy.approved.json` is the active source boundary. It contains five
  production baseline IDs; the broader catalog is not automatically active.
- The app is read-only from a judge's perspective. It does not require a
  database, API key, login or paid runtime service.
- A successful build or HTTP response does not erase `STALE`, `PARTIAL`,
  `FAILED`, `UNKNOWN` or `BLOCKED` evidence states.

## Implemented but not production path

```text
source catalog v2 / source policy integration / official document locator
        │
        ├─ bounded Query Gateway and typed coverage projection
        ├─ local-first handoff / Review Inbox / role projection
        └─ PublicEvent deterministic fusion + fixture replay
```

These paths reuse the existing domain modules and validators. They can be
self-checked from a checkout, but the receipts do not prove a deployed
multi-agency service, multi-user authorization, human adoption or production
source promotion.

Replays:

```bash
python3 -X utf8 scripts/verify-source-policy-integration.py --self-check
python3 -X utf8 scripts/located-facts.py self-check
python3 -X utf8 scripts/handoff-state.py self-check
python3 -X utf8 scripts/public-event-fusion.py --self-check
```

## Candidate and design paths

```text
candidate official sources ──canary / completeness / promotion──► active policy
Twinkle discovery/background ──provenance + direct official check──► reference
Issue #21 detail recheck ──version/dependency validation──► needs-review handoff
Issue #23 tracking ──persistent state + versioned confirmation──► future workflow
Issue #24 event fusion ──manual merge/split + evidence──► future production path
```

None of these arrows is complete merely because a config, design document,
fixture, Open PR or Issue exists. The normalized statuses and current evidence
are maintained in [CURRENT_STATUS.md](./CURRENT_STATUS.md).

## Trust and safety boundaries

- Official source content is data, not an instruction to run code, access a
  secret, follow an arbitrary URL or change permissions.
- Rules validate source identity, dates, hashes, evidence locators and release
  eligibility; semantic models, if later used, can only propose candidates.
- Official conflicts remain visible as `CONFLICT`; the system does not average
  values or silently overwrite a human-confirmed version.
- Public output is metadata／link／bounded evidence. It excludes internal duty
  data, 110 calls, case-level personal data, dispatch decisions and private
  participant records.
- Background population, statistics, geography and Twinkle results retain their
  period and provenance. They are not live crowd counts, jurisdiction or
  available-police estimates.

## What this architecture does not claim

The current repository does not claim a full real-time cross-agency collector,
Twinkle client, automatic event truth, a durable multi-user approval service,
production evaluation results, or a completed #20 publication acceptance. See
[limitations and safety](./LIMITATIONS_AND_SAFETY.md) before describing the
system externally.
