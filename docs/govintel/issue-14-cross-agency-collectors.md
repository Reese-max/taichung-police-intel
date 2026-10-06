# #14 跨機關資料來源與增量 collector

Five cross-agency sources ride the existing list-first collector under a daily
bounded canary. None of them is production yet; promotion requires a seven-day
observation receipt plus an explicit policy transition in
`docs/govintel/source-policy.approved.json`.

## Scope and status

| Source | Catalog status | Adapter | Cadence |
| --- | --- | --- | --- |
| S-019 市政會議紀錄與專案報告 | `AUDITED_EXISTING` | `collect_news_list` (HTML list + detail) | 30 分鐘 |
| S-001 警察局警政新聞 | `AUDITED_EXISTING` | `collect_news_list` (HTML list + detail, pagination fallback) | 30 分鐘 |
| S-032 交通局最新消息 | `VERIFIED_CANDIDATE` | `collect_news_list` (HTML list + detail) | 30 分鐘 |
| S-033 新聞局市政新聞 | `VERIFIED_CANDIDATE` | `collect_news_list` (RSS list + detail) | 30 分鐘 |
| S-031 消防局即時災情 | `VERIFIED_CANDIDATE` | `collect_fire_live` (live transient snapshot) | 10 分鐘 |

All five remain `CANDIDATE` in the observation report and in the published
`candidate_sources` block — never labelled production before the canary window
passes.

## Incremental collection contract

- Every adapter fetches the cheap list/RSS page first and computes a stable key
  plus a list-row content hash per item.
- `collect_news_list(existing=...)` skips the detail fetch for unchanged rows
  (`unchanged-skipped`) and caps detail fetches (`skipped-detail-cap`), so a
  wide list cannot trigger a full-history rebuild.
- `run_database_slot` seeds `existing` from durable `raw_items` rows keyed by
  `stable_key`, making the production path incremental for any list-first
  source once promoted.
- `scripts/candidate-runtime-canary.py` accepts `--existing <prior report>` and
  emits `item_state` (`stable_key → content_sha256`), so the daily observation
  workflow diffs against its previous run instead of a cold start.
- `published_at` comes from the official page, `observed_at`/`fetched_at` is the
  collector read time, and `effective_at` stays unset unless the source exposes
  a separate effective date — the three timestamps are never collapsed.
- A failed or partial run never counts as zero events: `FAILED`/`PARTIAL` keep
  the last-known-good record and manifest.
- `REMOVED` requires two complete snapshots (see `intel_v2/semantics.py`);
  the first snapshot only establishes the baseline and emits no `NEW` events.

## S-031 public-safety guardrails

- `collect_fire_live` performs one bounded snapshot and returns `PARTIAL` —
  transient rows can never claim a complete window.
- Retained location text is redacted to district level; `observed_at` is the
  incident receipt time.
- The catalog row carries `public_usage_notice` and
  `retention_class: OFFICIAL_TRANSIENT_METADATA`; the canary report and the
  published `candidate_sources` entry must echo them verbatim.
- `verify-candidate-observation-window.py` blocks any S-031 window missing the
  notice — the disclaimer cannot be dropped silently.

## Observation and promotion

- `.github/workflows/candidate-source-observation.yml` runs all five candidates
  daily (`15 2 * * *` UTC) with the bounded session and uploads each report as
  an artifact. `actions/cache` carries `item_state` between runs.
- `verify-candidate-observation-window.py --input <report.json>` (repeatable)
  confirms seven continuous days per source; `promotion_eligible` stays
  `false` in every receipt — promotion is a separate explicit catalog change
  with receipts.
- `verify-publication-bundle.py` now binds published labels to the catalog:
  every status source and feed item must carry the catalog `role`, the
  `integration_status` must equal the catalog `status`, `candidate_sources`
  entries must stay non-production with `promotion_eligible: false`, and all
  recheck URLs must be credential-free HTTPS.

## Replay

```powershell
python -X utf8 -m unittest discover -s tests -p test_news_list_collector.py
python -X utf8 -m unittest discover -s tests -p test_candidate_runtime_canary.py
python -X utf8 -m unittest discover -s tests -p test_candidate_observation_window.py
python -X utf8 -m unittest discover -s tests -p test_publication_bundle.py
python -X utf8 scripts/verify-publication-bundle.py
python -X utf8 scripts/verify-candidate-observation-window.py --self-check
```
