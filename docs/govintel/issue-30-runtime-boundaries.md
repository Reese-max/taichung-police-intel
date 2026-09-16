# #30: Query projection runtime boundary follow-up

Implemented in the existing PR #40, not a second query service. No production wiring/deployment is implied.

## Corrections

- Reject mixed collection IDs or generation timestamps across feed/status/brief. Reject malformed rows instead of silently dropping them.
- Require the current exact five-source P0 coverage, known source refs, strict count/hash/time types and credential-free HTTPS locators. Candidate-source promotion requires a reviewed contract update.
- Query response includes canonical hashes, current query time, source health/gaps, data age and an explicit metadata-only scope. Zero matching titles with failed/stale/unknown sources is not reassuring.
- `publication_deployment_verified=false` remains explicit: these checked-in artifacts have no authenticated served-publication receipt in this adapter. The #20 data checkpoint acknowledgement must be wired separately.
- Pagination cursor binds generation and filters; old cursors cannot silently page into a new snapshot. Date sorting compares instants, not timezone-bearing strings.
- Projection hash detects accidental corruption. This is not authentication: only a server-controlled canonical artifact source may populate the index. Clients cannot supply their own evidence catalog or write truth through the query API.
- Byte/row/input/result limits are explicit. Failed atomic swap preserves the previous complete index and cleans the temporary file.
- Schema 2 is a disposable projection upgrade: rebuild from canonical files, do not mutate old canonical artifacts. No database migration is introduced.

## Actual local execution

The existing 8 tests are unchanged, with 20 new boundary/CLI regressions. `python -m unittest discover -s tests -p 'test_query_store*.py' -v` passed **28 tests**. The portable Node bridge and the real CLI self-check passed too; bridge runs are not counted as independent validation.

```sh
python scripts/query-store.py build --output /tmp/query-store.json
python scripts/query-store.py query --store /tmp/query-store.json --q 警察 --limit 2
```

Using the exact archived publication artifacts: 118 indexed rows, 5 sources; the title query matched 110 rows and returned 2 with pagination/truncation. The response correctly reported STALE at execution time. This is a query over the recorded 2026-08-27 snapshot, not today's official event count.

Tests also inject source failure, mixed generations, malformed rows, cursor drift, credential-bearing URLs, timezone ordering and an atomic replace failure. No live government requests or production writes occur.

Container source came from the exact Actions replay artifact for PR #16 plus fetched PR #40 files; local full-repository build is not asserted. The exact pushed head must also pass the existing required GitHub CI.

## Still needed

This remains a bounded linear metadata index, not full document-text search, an implemented PublicEvent store, statistics adapter, Web Chat, MCP endpoint or measured performance improvement. Those downstream integration criteria remain open under #30/#29/#15/#24/#28. The 16-hour age threshold is an explicit local policy, not a data-provider SLA.
