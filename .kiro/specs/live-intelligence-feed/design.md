# Live Intelligence Feed — Design

## Architecture

```text
P0 Collectors (online_collect.py)
        |
        v
build_demo_status() — already collects items per source
        |
        ├─> source-status.json (existing — health/LKG/gaps)
        └─> intelligence-feed.json (NEW — qualified items for homepage)
                |
                v
feed-projection.js (Node.js, apps/web/lib/)
        |
        v
buildHomepageResponse() (existing eligibility pipeline)
        |
        v
page.js — renders feed items + PRIORITY_ITEM as evidence fallback
```

## Data flow

1. **Python collector output** (`build_demo_status`): Each source returns `collected["items"]` — array of `{stable_key, source_url, published_at, content_sha256, payload}`.
2. **Feed projection** (Python, in `build_demo_status`): Transforms raw items into safe feed items, strips payload, assigns stable_id, applies freshness/eligibility, writes `intelligence-feed.json`.
3. **Frontend projection** (`feed-projection.js`): Reads `intelligence-feed.json` at build/runtime, maps to `buildHomepageResponse()`-compatible candidates.
4. **Homepage** (`page.js`): Loads feed via `feed-projection.js`, passes to `buildHomepageResponse()`. Falls back to PRIORITY_ITEM for council evidence journey if feed is empty.

## Feed item schema (intelligence-feed.json)

```json
{
  "schema_version": 1,
  "generated_at": "ISO8601",
  "collection_run_id": "string",
  "items": [
    {
      "stable_id": "FEED-{source_id}-{stable_key_hash8}",
      "source_id": "S-004",
      "source_role": "PRIMARY_OFFICIAL",
      "title": "safe summary from payload title",
      "official_url": "https://...",
      "published_at": "ISO8601 | null",
      "fetched_at": "ISO8601",
      "data_as_of": "ISO8601 | null",
      "change_type": "NEW | UNCHANGED | UPDATED",
      "freshness_status": "FRESH | STALE | VERY_STALE | NO_DATA",
      "source_health": "PASS | DEGRADED | FAILED",
      "window_completeness": "COMPLETE_WITH_ITEMS | COMPLETE_ZERO | PARTIAL",
      "reason_codes": ["COUNCIL_ATTENTION"],
      "item_value_score": 70,
      "eligibility": "HOME_CANDIDATE | INELIGIBLE_STALE | INELIGIBLE_NO_DATE",
      "evidence_count": 1,
      "next_milestone": null,
      "content_sha256": "hex64"
    }
  ],
  "source_summary": {
    "S-004": { "health": "PASS", "freshness": "VERY_STALE", "item_count": 1 }
  }
}
```

## Deterministic checks

- Feed items must have unique `stable_id` (dedup by source_id + stable_key).
- UNCHANGED items (same content_sha256 as prior feed) get `eligibility: "INELIGIBLE_UNCHANGED"` — never promoted to homepage.
- Items from FAILED sources get `eligibility: "INELIGIBLE_SOURCE_FAILED"`.
- Items from PARTIAL window_completeness sources get `eligibility: "INELIGIBLE_PARTIAL"`.
- Items from VERY_STALE sources get `eligibility: "INELIGIBLE_STALE"`.
- Items without `published_at` or NO_DATA freshness get `eligibility: "INELIGIBLE_NO_DATE"`.
- Only `HOME_CANDIDATE` items pass to `buildHomepageResponse()`.
- SHA-256 content hash ensures unchanged items are suppressed across runs.
- Eligibility priority order: UNCHANGED > FAILED > PARTIAL > VERY_STALE > NO_DATE > HOME_CANDIDATE.

## Generator

- Python `build_demo_status` generates the feed as a side-effect of collection.
- No AI model involved — pure deterministic projection from collector output.

## Verifier

- Node.js tests verify schema, freshness rules, dedup, and eligibility assignment.
- `verify-project.mjs` gate checks feed file presence and cross-references with source-status.

## Failure states

- Source FAILED: prior feed items copied as LKG projections (change_type="LKG", eligibility="INELIGIBLE_SOURCE_FAILED"); source_summary shows FAILED health.
- All sources FAILED: feed has LKG items from prior + zero new items; homepage shows empty state with source health.
- Partial source (window_completeness=PARTIAL): items marked INELIGIBLE_PARTIAL, not promoted to homepage.
- Stale but healthy: items appear with INELIGIBLE_STALE or INELIGIBLE_UNCHANGED, not promoted to homepage priority.
- No prior feed + source failure: zero items for that source; source_summary shows FAILED.
- PRIORITY_ITEM: shown only before feed loads (loading state); after feed loads, replaced by feed-driven content or explicit empty state with evidence-demo fallback button.

## Retry limit

- Collectors already have 2 retries (HTTPAdapter). No additional retry in projection.
- If feed file is unreadable at frontend, fall back to PRIORITY_ITEM (council fixture).

## Acceptance command

```bash
npm run check
```

Expected output: `VERIFY_OK mode=full ... secrets=0`
