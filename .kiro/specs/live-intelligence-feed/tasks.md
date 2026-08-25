# Live Intelligence Feed — Tasks

## Task 1: Add feed output to build_demo_status (Python)

Modify `build_demo_status()` in `online_collect.py` to:
- Retain `collected["items"]` per source alongside existing status metadata.
- Project each raw item into a safe feed item (strip payload, assign stable_id).
- Apply freshness/eligibility rules per item.
- Write `intelligence-feed.json` to the same output directory as source-status.json.

Acceptance: `python -X utf8 online_collect.py --self-check` passes; feed schema is valid.

## Task 2: Create feed-projection.js (Node.js)

Create `apps/web/lib/feed-projection.js`:
- `loadFeed()`: reads intelligence-feed.json, returns typed feed object.
- `projectToHomepageCandidates(feed)`: maps eligible feed items to the shape expected by `buildHomepageResponse()`.
- Handles missing file, empty items, and partial failures gracefully.

Acceptance: `node --test apps/web/tests/feed-projection.test.mjs` passes.

## Task 3: Modify page.js to use feed

Update `apps/web/app/page.js`:
- Import and use `projectToHomepageCandidates()` to build the homepage response.
- Keep PRIORITY_ITEM as fallback for the council evidence journey (drawer still works).
- Show empty state when no feed items qualify.

Acceptance: `node --test apps/web/tests/homepage.test.mjs` passes; page renders feed items.

## Task 4: Add feed-projection tests

Create `apps/web/tests/feed-projection.test.mjs` covering:
- Current NEW item qualifies for homepage.
- Unchanged item suppression (same content_sha256 across runs).
- VERY_STALE item is ineligible.
- Missing published_at (NO_DATE) is ineligible.
- PARTIAL/failed source with LKG shows staleness.
- Stable dedup/hash correctness.
- Official URL gate (only HTTPS).
- Homepage ≤ 10 items cap.
- Empty state (no items → explicit message).

Acceptance: All test assertions pass with `node --test`.

## Task 5: Update verify-project.mjs for feed

Add to gate0 checks:
- `apps/web/public/data/intelligence-feed.json` must exist.
- Feed schema_version must be 1.
- Feed items must have valid stable_id and official_url.

Acceptance: `npm run check:gate0` passes.

## Task 6: Run gates and fix regressions

- Run Fast Gate: `cd apps/web && node --test tests/*.test.mjs`
- Run Full Gate: `npm run check`
- Fix any failures found.

Acceptance: Both gates pass with exit code 0.
