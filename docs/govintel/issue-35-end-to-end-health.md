# End-to-end health chain and measurement fields (#35)

Answers the operator question "今天為什麼沒更新？" with a named stage instead of one
red light. The receipt is `apps/web/public/data/system-health.json`, produced by
`scripts/system-health.py` and served machine-readably at `/api/system-health.json`.

## Versioned stage model

`STAGE_MODEL_VERSION = "govintel-e2e-stages.v1"` — every published receipt carries the
registry under `stage_model`. The canonical artifact reports every advertised
`stage_ids` entry; a workflow receipt (`scripts/publication-outcome.py`) may report a
subset of them, and `stage_model.required_publication_stages` names the stages whose
absence alone forces the publication lane to UNKNOWN, so a consumer never has to guess
what gates a HEALTHY verdict.

| lane | stage | what it proves |
|---|---|---|
| discovery | `source_contracts` | upstream response shape still matches the contracted schema |
| discovery | `upstream_operating_state` | upstream Dashboard operating state (`ACTIVE` / `DEGRADED` / `RESTORING` / `PAUSED`) |
| publication | `collection` | each active source was collected, with item and gap counts |
| publication | `parsing` | collected bytes became canonical records |
| publication | `fusion_verification` | cross-source fusion and official-source verification |
| publication | `canonical_validation` | the publication and the source snapshot share one generation |
| publication | `publication_build` | publication bundle / Pages artifacts were built |
| publication | `deployment` | protected state push and Pages deployment |
| publication | `public_http_verification` | the deployed bytes are readable and hash-matched publicly |
| query | `query_index` | Query Store index generation is bound to the publication |
| query | `read_only_mcp` | read-only MCP stdio lifecycle bound to the publication |
| query | `mcp_web_query` | Web Chat / MCP query runtime |

Every stage row always carries `started_at`, `ended_at`, `last_success_at`,
`generation_id`, `upstream_hash`, `downstream_hash`, `item_count`, `gap_count`,
`error_stage`, `error_class` and `receipt_ref`. Missing evidence is `null`, never
fabricated.

## Failure attribution

`FAILURE_STAGE_CLASSIFICATION` maps every known `error_class` to exactly one stage, and
`failure_stage_receipt()` fails closed on an unclassified class rather than dumping it
into a catch-all. `tests/test_system_health.py` asserts that every class the pipeline
actually emits — from `current_publication_stages()` and from
`publication-outcome.runtime_health()` across the phase combinations the workflow emits —
is a key of the registry. The regression matrix walks:

| simulated failure | stage | lane | overall |
|---|---|---|---|
| collector transport error | `collection` | publication | `BLOCKED` |
| parser partial output | `parsing` | publication | `PARTIAL` |
| fusion/verification failure | `fusion_verification` | publication | `BLOCKED` |
| protected-main push rejected | `deployment` | publication | `BLOCKED` |
| deploy action failed | `deployment` | publication | `BLOCKED` |
| public HTTP hash mismatch | `public_http_verification` | publication | `BLOCKED` |
| query index build failed | `query_index` | query | `DEGRADED` |
| MCP runtime unavailable | `mcp_web_query` | query | `DEGRADED` |
| source contract drift | `source_contracts` | discovery | `DEGRADED` |

Three invariants hold by construction:

- a publish/deploy failure never rewrites `collection` — it lands on `deployment`;
- a Query Store / MCP failure never marks the canonical publication failed, it only
  degrades the `query` lane;
- an upstream `PAUSED` or `DEGRADED` feed is reflected in the `discovery` lane with
  `upstream.overrides_govintel_health: false` — an upstream outage is not reported as a
  GovIntel collection failure.

## Measurement-only SLO fields

`slo.status` is `MEASUREMENT_ONLY` and `slo.thresholds` is `null`: this release defines
the fields, not an SLA that has not been validated.

| field | unit | source |
|---|---|---|
| `collection_success_ratio` | ratio | sources that are `PASS` with a complete window |
| `source_freshness_age_ms` | milliseconds | oldest `last_success_at` against the receipt `generated_at` |
| `stale_source_ratio` | ratio | sources whose freshness is `STALE` / `VERY_STALE` |
| `partial_source_ratio` | ratio | sources outside the complete-window set |
| `publication_mismatch_count` | count | publication-lane receipts whose error class is an artifact mismatch (`PUBLICATION_GENERATION_MISMATCH`, `PUBLIC_HTTP_HASH_MISMATCH`) — independent of the collection ratios |
| `source_to_detect_ms` | milliseconds | chain latency |
| `detect_to_verify_ms` | milliseconds | chain latency |
| `verify_to_publish_ms` | milliseconds | chain latency |
| `publish_to_public_visible_ms` | milliseconds | chain latency |
| `query_index_lag_ms` | milliseconds | query runtime receipt |
| `query_error_rate` | ratio | query runtime receipt |

Every metric is an object with `value`, `unit`, `measured` and `reason`. An unmeasured
field reports `value: null`, `measured: false` and the reason it could not be measured —
for example the checked-in snapshot has no deployment or public HTTP receipt, so
`publish_to_public_visible_ms` stays UNKNOWN.

## Latency chain

`chain_timestamps()` derives the chain from real stage `ended_at` values only.
`source_published_at` has no stage behind it, so source→detect latency stays UNKNOWN
unless a source publishes a real timestamp — a missing upstream timestamp is never
invented. Two endpoints carrying the *same* instant are equally unknowable (a reused
generation timestamp is indistinguishable from a zero-length hop), so a zero delta
reports UNKNOWN rather than `0 ms`.

## Non-goals

- No observability SaaS is required; the receipt is a checked-in JSON artifact.
- A green GitHub Actions run is not production health: `scripts/publication-outcome.py`
  still reports action success separately from anonymous HTTP verification.
- No notification service is bound; the operator surface is the receipt, the API route
  and `operator_summary`.
