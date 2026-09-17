# CURRENT_CHECKOUT release candidate verification (issue #47)

One command builds, starts, and verifies the M1 integration candidate
**from a single checkout** — every enabled module is imported from this
tree, never fetched from other feature branches or pinned SHAs.

M1 flow under test:

```
canonical publication → Query Store → HTTP/site search → open source & version
→ freshness / data-gap / version display
```

## Command

```bash
pip install -r requirements.txt
npm ci --prefix apps/web
npm install                       # root devDependency: playwright-core
python -X utf8 scripts/verify-current-checkout.py [--output DIR]
```

Modes and flags:

| Flag | Effect |
|------|--------|
| `--mode full` (default) | build → serve → HTTP checks → real-browser checks → sabotage |
| `--mode core` | module binding + publication verifiers + policy + store + sabotage only |
| `--skip-browser` | run HTTP lane but skip the browser lane |
| `--skip-sabotage` | skip the sabotage-copy check (used internally by the sabotage lane itself) |
| `--chrome-path PATH` | explicit Chrome/Chromium executable for the browser lane |

Verification runs on a temporary serve directory and loopback HTTP only:
it writes no production data and persists no credentials.

The browser lane uses `playwright-core` with the system `chrome` channel
(Google Chrome/Chromium on the host). On hosts without a Chrome install,
run `npx playwright install chrome` or pass `--chrome-path` /
`GOVINTEL_CHROME_PATH`.

## Enabled modules (all from this checkout)

| Capability | Source | Reuse |
|---|---|---|
| Canonical publication verify | `scripts/verify-publication-bundle.py`, `scripts/verify-v2-publication.py` | #20 persistence/hash checks |
| Canonical Query Store | `scripts/query-store.py` | #30 (PR #40), generation-bound, atomic writes |
| Source policy | `scripts/source-policy.py` | #49 (PR #52), `policy_version`/`policy_hash` |
| Lane-aware health | `scripts/system-health.py` | #35 (PR #45) |
| Static site + loopback query API | `apps/web` build + in-process server | existing product surface |

`enabled_capabilities` lists what the candidate supports;
`unavailable_capabilities` records `CAPABILITY_NOT_AVAILABLE` for
`event_fusion`, `entity_registry`, `answer_evidence_gate`,
`gold_evaluation`, `chat_mcp`, `live_collection`, `persistent_handoff`
and any policy capability whose required sources are not active.

## Receipt

`runtime-evidence/current-checkout/<ts>/receipt.json`:

| Field | Meaning |
|---|---|
| `candidate_id` | `rc-<short-sha>-<utc ts>` |
| `code_sha` | `git rev-parse HEAD` of the tested checkout |
| `worktree_dirty` | uncommitted tracked changes present |
| `dependency_lock_hash` | sha256 over `requirements.txt`, `package-lock.json`, `apps/web/package-lock.json` |
| `enabled_capabilities` / `unavailable_capabilities` | capability ledger (see above) |
| `publication` | `collection_run_id`, `generated_at`, per-artifact sha256, `publication_status`, `snapshot_complete`, `PRESERVED_SNAPSHOT_NOT_LIVE` |
| `query_store` | `generation_id`, `projection_sha256`, versions, counts |
| `source_policy` | `policy_version`, `policy_hash`, `catalog_hash`, `active_source_ids` |
| `test_mode` | `CURRENT_CHECKOUT_LOOPBACK_HTTP` or `CURRENT_CHECKOUT_CORE` |
| `checks` / `not_run` | per-check PASS/FAIL with detail; NOT_RUN items never count as PASS |
| `health_receipt` | lane-aware health over measured stage outcomes |
| `historical_pinned_replay` | `SEPARATE_LANE` pointer — pinned replay never substitutes |
| `verified_at`, `started_at`, `production_verified=false`, `limitations` | timing and scope |

Overall `status` is `PASS` only when at least one check ran, no check
failed, and at least one check passed. Zero or all-`NOT_RUN` → `FAIL`.

## Negative cases exercised

| Case | Mechanism |
|---|---|
| Query pinned to an old generation after atomic cutover | `expected_generation` mismatch → HTTP 409 `GENERATION_MISMATCH`; old store still readable at `data/query-store.archive.json` |
| HTTP 200 serving a stale-generation store | `served_store_consistency` compares served vs built generation → recorded FAIL, not accepted as publish |
| Query service down | `/api/query` → 503 `QUERY_UNAVAILABLE`; static page + feed still 200; `/api/health` query lane `BLOCKED` while publication lane stays unblocked (deployment `SUCCESS`, public-HTTP verification `UNKNOWN`) |
| Stale/partial snapshot | `data_status` (`STALE`/`PARTIAL`/…) and `source_gaps` are typed; never collapsed into "no events" |
| Source outside approved set | `data_status=SOURCE_NOT_AVAILABLE`, distinct from valid empty result |
| Capability not enabled | `/api/capability?id=…` → `CAPABILITY_NOT_AVAILABLE` |
| Module sabotage in a temp copy | `run_sabotage_check` corrupts the copy's `query-store.py`, reruns the verifier there, and requires it to FAIL — proving checks bind to checkout code |
| Browser refresh/back | browser lane re-reads the provenance block and compares the displayed query-index generation |

The browser lane (`scripts/e2e-browser.mjs`, playwright-core + system
Chrome) actually types into the archive search, counts filtered results,
asserts official `https` source links with `target="_blank"`, opens the
source-health block, reads the version/provenance surface, exercises a
valid-empty query, and reloads — it does not merely read HTML strings.

## Historical pinned replay is a separate lane

`scripts/verify-backbone-runtime.py` + `config/backbone-runtime.v1.json`
(PR #46) replay **pinned SHAs** in isolated worktrees — preserved here for
the historical evidence lane and CI
(`.github/workflows/backbone-runtime.yml`). Its PASS never substitutes for
current-checkout checks, and the receipts are reported separately.

## Scope and limitations

- Loopback HTTP deployment only; the public Pages deployment is not
  verified here (`production_verified=false`).
- Checked-in preserved snapshot, not live data; external source failures
  do not affect this run.
- The candidate.json manifest is served only in the candidate serve dir;
  the dashboard always renders the brief-derived provenance block, and
  the candidate-specific query-index/policy/capability subsections appear
  only when the manifest exists.
- No credentials are read or persisted.
