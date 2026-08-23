# Source ingestion and provenance design

## Current baseline

The repository has audit scripts, 30 catalog IDs, S-026/S-029 canaries, static snapshots, and a source-value contract. These are inputs to the implementation, not formal collectors.

## Flow

```text
06:30 / 18:30 Asia/Taipei slot
  -> collection_run and source_run
  -> official response
  -> raw snapshot and SHA-256
  -> deterministic parse/schema/window checks
  -> source health / freshness / intelligence gaps / last-known-good
  -> stable item and version keys
  -> typed change event
  -> Generator: candidate claims
  -> Verifier: original-evidence review
  -> AUTO_PASS or quarantine
```

## Run contract

- `collection_run` is idempotent for each scheduled date and `MORNING`／`EVENING` slot; manual runs do not satisfy the twice-daily target.
- `source_run` records each source independently, including attempt/completion time, health, window completeness, result, manifest hash, and prior successful run.
- `item_count` is the number observed in the requested window; `change_count` is the number in that same window whose stable content changed. Older items may establish raw baseline versions without being reported as current-window updates. `NO_NEW_ITEM` requires `change_count=0` and either a complete zero window or a complete window whose observed items are unchanged.
- `FAILED` and `NOT_RUN` keep both counts null and cannot overwrite the last successful snapshot.
- `PARTIAL` requires a partial window, a manifest, and a non-null observed item count; it is neither a complete zero nor a failed endpoint.
- D2 materializes a per-source status projection. Last-known-good advances after a structurally valid `PASS`／`DEGRADED` run with a manifest, including a `PARTIAL` date window; `WINDOW_PARTIAL` remains visible. `FAILED` and `NOT_RUN` keep the prior pointer.
- Operational intelligence gaps use `SOURCE_FAILED`, `WINDOW_PARTIAL`, `NO_LAST_KNOWN_GOOD`, `STALE_DATA`, and `VERY_STALE_DATA`. They do not replace evidence-domain gap records such as `GAP-001`.
- D1 defines the contract. D2 owns one fixture command and one production command. In competition mode, `online_collect.py --demo-output` uses per-source failure isolation and writes the provenance summary to `source-status.json`; `.github/workflows/pages.yml` schedules it twice daily and commits only that file. Database mode retains PostgreSQL advisory locking, raw snapshots, and stable versions for the post-competition durable-history path.

## Production topology

```text
GitHub Actions 06:30 / 18:30 Asia/Taipei
  -> online_collect.py --slot <morning|evening> --demo-output .../source-status.json
  -> committed, versioned source-status.json
  -> Next.js static export
  -> GitHub Pages /api/health.json + /api/status.json
  -> anonymous HTTPS source-health panel
```

GitHub Actions schedules are UTC (`30 22 * * *` and `30 10 * * *`). Each scheduled run refreshes and commits only `source-status.json` before rebuilding and deploying the static export. PostgreSQL migrations under `migrations/` remain available for the post-competition durable-history path. Actual Pages deployment, two distinct scheduled workflow runs, and the anonymous public canary remain separate acceptance evidence.

## Deterministic checks

- HTTP/fetch status, non-empty body, expected fields, date-window semantics, SHA-256, parser version, stable IDs, exact duplicates, manifest equality, and fixture capture metadata.
- `source_health` and `window_completeness` remain independent.
- Freshness is measured from `data_as_of`, not from the time an old fixture is replayed.
- A missing date produces `UNVERIFIED_DATE`, not a guessed timestamp.
- Scheduled slot uniqueness, stable item/version uniqueness, current-version uniqueness, and producer/verifier independence are database constraints.

## Generator

The generator receives only explicitly bound evidence and emits typed candidate claims with evidence locators and a unique producer run ID.

## Verifier

The verifier uses a separate run ID, reads the original evidence, checks claim type and scope, and cannot approve missing or broken locators.

## Failure states

`FETCH_FAILED`, `PARSE_QUARANTINED`, `VALIDATION_PENDING`, `AI_DISAGREEMENT`, `CLAIM_REJECTED`, and `QUARANTINED` remain visible and source-specific. A failed source run updates current health and intelligence-gap reasons but never erases its last-known-good snapshot.

## Retry limit

One automatic retry is allowed after a validation disagreement. A second disagreement is quarantined and cannot overwrite the last `AUTO_PASS` snapshot.

## Acceptance command

```bash
python -m unittest discover -s tests -p "test_source_ingestion.py" -v
npm run check
python online_collect.py --canary
```

The first command covers the D1 schema, cross-record references, failure/zero/partial distinction, D2 double-run equality, source status, intelligence gaps, last-known-good preservation, failure isolation, and PostgreSQL migration.

Set `TEST_DATABASE_URL` to a disposable PostgreSQL database to enable the migration apply test; without it, only that post-competition-path test is reported as skipped. Competition production acceptance requires anonymous `GET /api/health.json` and `GET /api/status.json` from GitHub Pages plus two successful scheduled workflow runs, one `MORNING` and one `EVENING`, whose committed states remain visible in Git history. Local checks cannot satisfy that public-URL and scheduler gate.
