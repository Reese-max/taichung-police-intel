# GovIntel source reliability review — 2026-10-05

This is a read-only source audit and local code verification. It does not record
a production deployment, source promotion, or competition organizer acceptance.
Times below are Asia/Taipei unless an ISO timestamp includes another offset.

## Public production snapshot

Anonymous `data/source-status.json` readback exposed a snapshot generated at
**2026-10-04 23:26:28 +08:00**, with collection status **PARTIAL** and the existing
five active sources. The newer snapshot supersedes the previously discussed
10:22 publication for current source health; neither collection time proves the
official content is current.

| Source | Observed state | Interpretation |
| --- | --- | --- |
| S-004 | PASS; COMPLETE_WITH_ITEMS; official date 2026-10-02 | Dated council agenda content exists. |
| S-006 | Public snapshot says FRESH and COMPLETE_ZERO using collection time | Incorrect date/window inference. A separate live official HTML read found seven current-session attachment titles, all without a parseable date. |
| S-007 | PASS; COMPLETE_ZERO; data_as_of 2026-05-27 01:12; STALE | The live official police-keyword API probe returned HTTP 200, 20,906 total hits, and the same latest record date. The stale official date must remain visible. |
| S-009 | PASS; COMPLETE_ZERO; API-confirmed collection timestamp | This is the collector's current legislative-state confirmation convention, not an official proposal publication date. |
| S-029 | FAILED; CONNECTIONERROR; PARTIAL; last_success 2026-09-24 23:18:12 | Last-known-good official date is 2026-07-24. A bounded official index GET returned HTTP 503 with upstream connection timeout. No recovery is established. |

## Local fixes and verification

- Candidate observations preserve the managed runtime proxy and CA settings.
  HTTP adapter retries are disabled for both default and injected Requests
  sessions, so the six-call budget counts actual attempts and redirects.
- S-029 stores the response bytes already validated by its canary instead of
  downloading every list page and attachment twice. Attachment hashes and
  snapshots now describe the same response; PDF validation remains required.
- Undated or mixed dated/undated council attachment lists report PARTIAL, never
  COMPLETE_ZERO. Successful undated content no longer assigns collection time
  to `data_as_of` or carries a previous fabricated collection-time date forward.
  `last_checked_at` remains the observation clock; source health remains PASS,
  freshness is unknown (`NO_DATA`), and gaps include `NO_DATA_AS_OF` and
  `WINDOW_PARTIAL`. Partial-window feed items remain ineligible.
- Focused verification passed candidate canary transport 11 tests, live canary /
  council date / population parser 14 tests, news collectors 19 tests, candidate
  observation window 6 tests, and candidate publication wiring 4 tests.
  Source ingestion passed 10 tests; its PostgreSQL integration test was skipped
  because `TEST_DATABASE_URL` was not set. These checks do not establish external
  uptime or a published deployment.

At **2026-10-05 01:15:54 +08:00**, a single real bounded candidate observation
through the managed proxy returned HTTP 503 for S-001 and S-019. Each used one
request, retained null counts and CANDIDATE status, and had no promotion receipt.

## Candidate workflow and existing PRs

On `main@562141e`, scheduled observation run
[37189085475](https://github.com/Reese-max/taichung-police-intel/actions/runs/37189085475)
failed S-001; S-032, S-033, and S-031 jobs succeeded. Job success alone does not
prove complete date-window coverage. The workflow observed four sources while
the catalog promotion plan contained five: **S-019 was omitted**.

- PR #102 addresses the missing S-019 workflow entry and makes the seven-day
  observation validator fail closed for every promotion-plan source, including
  one never present in a receipt. This closes a validation omission; it cannot
  manufacture seven real days or promote a source.
- PR #112 adds bounded pagination, partial-window metadata and a candidate
  publication lane. Its retry fix still retained `trust_env=False`; integration
  must preserve the runtime proxy fix from this review. Candidate visibility is
  distinct from active-source qualification.
- PR #104 / #101 overlap on incremental candidate collection and provenance.
  Their fixtures and code should not be combined as independent live evidence.
- PR #99 prevents a first historical source baseline from being counted as NEW.
  It is relevant to any future activation and to avoiding inflated daily growth
  claims.

The source catalog and approved policy were not changed by this work. No daily
100–200 new-item throughput claim is supported by these observations.

## D1 / D2 competition background data

[The public-safe D1 sample](segis-112Y12M-taichung.verified.json) contains all 29
Taichung districts from the actual official **112Y12M / 2023-12** CSV. The CSV was
downloaded through the official fixed-period page's native download flow. The
parser recognized the English header and only the exact Chinese description
row, then verified 368 national district records: no missing values, duplicate
town/period identities, negative/noninteger counts or male/female sum mismatch.
Taichung's historical population sum is **2,845,909**.

The 9,932-byte ZIP SHA-256 is
`d2648b7ab2649b1257099937d246d9e0639c86614ac2ad1accd355c53882d870`;
the original 28,502-byte CSV SHA-256 is
`95500e06310098d4c194a26e4aeba00e7142d5c9e441a62fe18f71250d65496b`.
The sample includes official source/terms links, attribution and a notice that
the data was filtered and sorted. It contains no personal contacts.

The same fixed-period page's JSON open-service link returned **114Y12M / 2025-12**
instead. That newer service response must not overwrite or be labelled as the
112Y12M historical CSV. Historical population is background context, not current
population, event attendance, affected-person estimates or live crowd movement.

[The background observation receipt](background-source-observations-2026-10-05.json)
also retains D2 dataset 5958's metadata success and resource failure. Metadata
declares UTF-8 CSV fields and license code 1 / free, modified on 2026-10-01, but
the official TGOS ZIP and metadata page returned **HTTP 403**. A legitimate
official-catalog referrer and native browser read did not recover access. Both
connected Twinkle accounts require reauthentication. No D2 CSV rows or
coordinate reference system were verified; POINT_X / POINT_Y are insufficient
to assume WGS84. Address proximity must not imply jurisdiction or available
police resources.

Remaining external evidence is genuine source recovery, a continuous qualified
observation window, explicit source-policy transition receipts, actual D2
resource bytes/CRS verification, and publication readback after integration.
