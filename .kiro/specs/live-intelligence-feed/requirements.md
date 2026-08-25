# Live Intelligence Feed — Requirements

## Must have

1. THE SYSTEM SHALL produce a versioned `intelligence-feed.json` alongside `source-status.json` during each `build_demo_status` run, containing qualified items from existing P0 collectors.
2. THE SYSTEM SHALL assign each feed item a stable item ID derived from source_id + stable_key, ensuring deduplication across runs.
3. THE SYSTEM SHALL include for each item: stable_id, source_id, source_role, title (safe summary), official_url (HTTPS), published_at, fetched_at, data_as_of, change_type, reason_codes, item_value_score, eligibility status, evidence_count, next_milestone (or explicit absence), source_health, window_completeness.
4. THE SYSTEM SHALL enforce freshness and change-type rules: items with VERY_STALE or NO_DATA freshness status SHALL NOT be marked as current priority; items from failed sources SHALL retain LKG with explicit staleness indicators; UNCHANGED items (same content_sha256 as prior feed) SHALL be marked INELIGIBLE_UNCHANGED; items from PARTIAL window_completeness sources SHALL be marked INELIGIBLE_PARTIAL.
5. THE SYSTEM SHALL NOT expose raw response payloads, personal data, case-level criminal data, or operational command data in the feed.
6. THE SYSTEM SHALL feed the homepage from `intelligence-feed.json` via the existing `buildHomepageResponse()` eligibility pipeline. The hardcoded `PRIORITY_ITEM` SHALL only serve as a council-evidence-journey fallback (evidence drawer), NOT as a homepage priority card once the feed has loaded.
7. THE SYSTEM SHALL display at most 10 items on the homepage, at most 2 central-policy items.
8. THE SYSTEM SHALL show an explicit "no publishable items this period" state with source health summary when no items qualify, rather than falling back to stale data pretending to be current. An evidence-demo button may appear but MUST be clearly labelled as historical/not-live.
9. THE SYSTEM SHALL preserve the existing source-status.json contract (schema_version 1, five sources, health/gaps/LKG).
10. THE SYSTEM SHALL, on collector failure, preserve prior feed items for the failed source as LKG projections with change_type="LKG", source_health="FAILED", eligibility="INELIGIBLE_SOURCE_FAILED", and honest freshness_status. If no prior feed exists, the source produces zero items and source_summary shows FAILED.

## Must not break

1. Existing homepage evidence drawer and council-prep journey (PRIORITY_ITEM remains as evidence-drawer fallback).
2. Existing bilingual copy contract (COPY.en / COPY.zh).
3. Existing source-monitor UI and source-status.json schema.
4. Existing test suite: homepage.test.mjs, council-prep.test.mjs, evidence.test.mjs.
5. Gate0, specs, and full verification gate contracts.
6. The i18n, accessibility, and limitation/safety labelling in the UI.

## Out of scope

1. Integration of sources beyond the 5 existing P0 collectors.
2. PostgreSQL persistence (the demo path uses JSON files only).
3. AI-generated wording or producer/verifier pipeline for feed items.
4. Live ASR or Groq calls.
5. Deployment, commits, or external writes.

## Fast Gate

```bash
cd apps/web && node --test tests/*.test.mjs
python -X utf8 online_collect.py --self-check
node apps/web/tests/feed-projection.test.mjs
```

## Full Gate

```bash
npm run check
```

Expected: `VERIFY_OK mode=full ... secrets=0`

## Reviewer criteria

1. Feed items have stable IDs, official HTTPS URLs, and no raw payloads.
2. Freshness rules are testable: FRESH/STALE items can appear; VERY_STALE/NO_DATA cannot be presented as current.
3. Failed source preserves LKG with visible staleness.
4. Empty state is honest: "no publishable items" + source health, not fake data.
5. Page.js no longer relies solely on PRIORITY_ITEM for all homepage content.
6. All existing tests pass unchanged.
