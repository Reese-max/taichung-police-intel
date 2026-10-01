# #30: Query projection runtime boundary follow-up

## Current local integration status (2026-09-21)

The first read-only query slice is now wired through the shared Query Gateway:

- `scripts/query-gateway.py` serves HTTP `/query` and `/mcp`.
- `scripts/query-gateway-stdio.py` serves the same MCP contract over bounded line-delimited stdio.
- The Web `Ask GovIntel` panel uses the same `search_evidence`, `get_current_brief`, and `get_source_health` primitives; `get_publication_receipt` exposes the bound artifact hashes, and `validate_answer` uses the server-controlled Answer Evidence Gate.
- The controlled answer renderer uses only the Gate's normalized, evaluated propositions; caller claim text and raw proposition whitespace are not rendered as verified output.
- The current code and checked-in artifacts pass `npm run check`, including the HTTP/MCP parity suite and the stdio transport tests; the gateway also exposes the bounded publication receipt projection.
- The gateway now has validated, read-only adapters for optional `PublicEvent` and typed-statistics stores. They are advertised only when an explicitly supplied store passes hash/schema validation; the checked-in publication snapshot does not contain either store, so the domain tools remain unavailable by default.
- Optional domain-store responses do not inherit the publication snapshot's freshness or no-match guarantee: without a bound source-health receipt they report `freshness=UNKNOWN`, `domain_store_coverage_status=UNVERIFIED`, and keep bounded no-match disabled.
- `scripts/build-query-domain.py build` now provides the bounded rebuild boundary for canonical domain inputs. It validates and atomically writes deterministic PublicEvent/statistics projections; the fixture-only dashboard demo is rejected and no domain store is enabled by default.

This is still a local/code-only receipt. It is not a production deployment, anonymous public reachability proof, or completion of the later event, comparison, and statistics slices.

Implemented in the existing PR #40, not a second query service. No production wiring/deployment is implied.

## Corrections

- Reject mixed collection IDs or generation timestamps across feed/status/brief. Reject malformed rows instead of silently dropping them.
- Require the current exact five-source P0 coverage, known source refs, strict count/hash/time types and credential-free HTTPS locators. Candidate-source promotion requires a reviewed contract update.
- Query response includes canonical hashes, current query time, source health/gaps, data age and an explicit metadata-only scope. Zero matching titles with failed/stale/unknown sources is not reassuring. A source counts as incomplete on both runtimes when its health is not `PASS`, its window is not `COMPLETE_ZERO`/`COMPLETE_WITH_ITEMS`, or its run reported anything other than `NEW_ITEMS`/`NO_NEW_ITEM`, so a partially collected source can never license a bounded no-match on either surface.
- The metadata projection supports exact `canonical_id` lookup as well as bounded text/source filters; the lookup is included in the cursor binding so a page cannot be reused for a different identity query. Each publication item carries deterministic evidence and document-version projection locators back to its canonical artifact: `evidence_id = PUB-<stable_id>` identifies the item, while `document_version_id = DOCV-<content hash prefix>` is content-addressed, so two publication items carrying byte-identical content deliberately share one document version. Item freshness is resolved once, at projection time: the item's own `freshness_status`, falling back to its source row's, and `verification_status` is `VERIFIED` only for `FRESH`/`RECENT` and `STALE` otherwise. The trusted evidence catalog publishes that projected status on each evidence record, and the gate's current/not-current decision follows it, so a result label and the answer decision cannot drift apart; a stale item is never handed over as confirmed-official evidence.
- The projection version is part of the store contract (`publication-metadata-v3`): a saved store written by an older projection is refused and must be rebuilt from canonical artifacts, because its rows cannot carry the current document/evidence locators. `query_coverage.requested_scope` reports only the filters a caller actually set, identically on the Python gateway and the Worker.
- PublicEvent query results preserve explicit trust tiers: `VERIFIED`, `DISCOVERY_UNVERIFIED`, `CONFLICT`, and `STALE`; Web/HTTP-MCP/STDIO-MCP envelopes report discovery candidates separately instead of promoting them into verified results. `discovery_unverified_count` counts the whole matched set, not just the returned page (the single-event tools report 0/1 for the returned entity), and `trust_tier_counts`, `conflict_count` and `stale_count` aggregate the same full match set so a zero discovery count never reads on its own as "everything is verified". An `lkg`-flagged event projects as `STALE` rather than verified. The tier mapping fails closed: a non-empty `verification_status` outside the known verified set (`VERIFIED`, `VERIFIED_OFFICIAL`, `OFFICIAL_RECONCILED`, `DETERMINISTIC_PASS`, `CONFIRMED_OFFICIAL`) projects as `DISCOVERY_UNVERIFIED`, so discovery-pipeline values such as `OFFICIAL_CANDIDATE`, `NO_OFFICIAL_MATCH`, `EXPIRED`, or `NEEDS_REVIEW` can never surface as verified.
- A failed rebuild keeps the last usable index instead of dropping it: the Python CLI's atomic replace already preserves the previous complete store, and the Worker serves its last good snapshot only when the *upstream* is unreachable. That degraded projection is labelled, not silent: every response built from it carries an `INDEX_REBUILD_FAILED` source gap (latched at the first failure and cleared by the next successful rebuild), reports `freshness=STALE` instead of `RECENT`, downgrades `query_coverage.status` to `PARTIAL` with `can_state_bounded_no_match=false`, disables bounded no-match, reports `status: "degraded"` on `/health`, and logs the failure. Artifacts that are present but mutually inconsistent (mixed collection run, bad content hash, non-HTTPS or credential-bearing locator, duplicate canonical id, policy/coverage mismatch) are an *integrity* failure, not an outage: the Worker fails closed with `UPSTREAM_UNAVAILABLE` instead of answering from the superseded generation. A worker-side cursor whose bound offset exceeds the filtered set is rejected with `INVALID_ARGUMENTS`, matching the Python gateway.
- `publication_deployment_verified=false` remains explicit: these checked-in artifacts have no authenticated served-publication receipt in this adapter. The #20 data checkpoint acknowledgement must be wired separately.
- Pagination cursor binds generation and filters; old cursors cannot silently page into a new snapshot. Date sorting compares instants, not timezone-bearing strings. `search_events` date-only bounds use the requested IANA `time_zone` (default `Asia/Taipei`), return the resolved UTC interval, and never use caller time as the freshness clock.
- Projection hash detects accidental corruption. This is not authentication: only a server-controlled canonical artifact source may populate the index. Clients cannot supply their own evidence catalog or write truth through the query API.
- Byte/row/input/result limits are explicit. Failed atomic swap preserves the previous complete index and cleans the temporary file.
- HTTP `/mcp` rejects an explicit `Origin` unless it exactly matches the configured allow-origin; requests without `Origin` remain available for non-browser clients, and `*` never authorizes a browser origin.
- Schema 2 is a disposable projection upgrade: rebuild from canonical files, do not mutate old canonical artifacts. No database migration is introduced.

## Actual local execution

The bounded query-store suite currently passes **36 tests** with `python -m unittest discover -s tests -p 'test_query_store*.py' -v`. The portable Node bridge and the real CLI self-check passed too; bridge runs are not counted as independent validation.

```sh
python scripts/query-store.py build --output /tmp/query-store.json
python scripts/query-store.py query --store /tmp/query-store.json --q 警察 --limit 2
```

Using the exact archived publication artifacts: 118 indexed rows, 5 sources; the title query matched 110 rows and returned 2 with pagination/truncation. The response correctly reported STALE at execution time. This is a query over the recorded 2026-08-27 snapshot, not today's official event count.

Tests also inject source failure, mixed generations, malformed rows, cursor drift, credential-bearing URLs, timezone ordering and an atomic replace failure. No live government requests or production writes occur.

Container source came from the exact Actions replay artifact for PR #16 plus fetched PR #40 files; local full-repository build is not asserted. The exact pushed head must also pass the existing required GitHub CI.

## Still needed

This remains a bounded linear metadata index, not full document-text search or a production PublicEvent/statistics store. Canonical domain-store generation now has a local rebuild boundary, but live collector wiring and production/runtime acceptance remain open under #29/#15/#24/#28. The 16-hour age threshold is an explicit local policy, not a data-provider SLA.
