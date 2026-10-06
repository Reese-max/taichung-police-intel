# S-038 bounded candidate collector

Local implementation only, based on `main@bd0152b0585dc888e9576892166afaff766c1fec`.
The complete source catalog still ends at S-037. S-038 is reserved in merged
planning PR #143 and is not registered or activated here. Existing open PR #116
is a distinct NPA statistics/reference adapter; it does not collect dataset 7505.
No existing review decision, rights policy, schedule, public feed or deployment
is changed by this patch.

## Source and observed evidence

[Official dataset 7505](https://data.gov.tw/dataset/7505) describes **irregular**
updates and OGDL v1. The [bounded receipt](canary-2026-10-05.json) records one
successful public CSV fetch on 2026-10-05, HTTP 200, 1,846,819 bytes, 999 records,
one Chinese description row, two shared-content-hash groups and zero publisher
names containing 臺中/台中. Publication dates span 2021-03-24–2026-09-11.
This is one observation day, not a cadence or freshness test. A matching word in
the body cannot identify the publisher or prove complete Taichung coverage.

Raw SHA-256:
`f8277912531cd75f88d5d52c917d129d3a32e98a2a7beb16d3038815bcb3d49a`.
Only aggregate/hash evidence is retained. Raw bytes were consumed in memory,
not saved. This hash identifies the observed bytes but does not provide a
replayable raw archive; archive permission and retention remain unreviewed.

Two 5-second socket attempts timed out. The final bounded canary used a
20-second socket timeout and succeeded in 13.469 seconds. All attempts were
read-only. Successful transport says nothing about publication recency.

## Local usage

The [2026-10-06 bounded readback](canary-2026-10-06.json) independently fetched
the same 1,846,819 bytes (HTTP 200 in 3.337 seconds). Its hash, 999-row count,
latest publication day and zero Taichung-publisher count match the October 5
observation. These are two observed calendar days, with unchanged bytes; they
do not establish serial stability after a change, update cadence, complete
coverage or archive/activation permission. No raw body or text was retained.

Offline tests require Python's standard library only:

```sh
python -X utf8 -m unittest discover -s tests -p test_npa_news.py -v
python scripts/npa-news-candidate.py --input /path/to/approved-local.csv
```

An explicit one-shot public fetch, without registration or publication:

```sh
python scripts/npa-news-candidate.py --live
```

Optional `--state-output /private/local/state.json` writes hash-only state
atomically after successful validation. `--previous /private/local/state.json`
compares to a prior successful observation. No state is written by default;
failed validation leaves an existing state unchanged. Preserve output/state in
an approved local location; this tool does not provide archive retention policy.

Transport uses a fixed HTTPS URL, rejects redirects before following them,
requires an allowed CSV/plain/octet-stream MIME type and identity encoding,
performs one request without automatic retry, reads at most 8 MiB + 1 sentinel
byte, and enforces a 30-second streaming deadline. An in-progress socket call
may extend that deadline by up to the 20-second socket timeout. No linked body
URL, image, attachment or article-detail page is fetched.

## Data and safety semantics

- Strict UTF-8/BOM, quoted multiline CSV, exact five-column schema, optional
  Chinese description row before or after the header, maximum 5,000 data rows
- `postDate` is publication time, never incident time. Explicit offsets are
  normalized to Asia/Taipei. Unzoned source timestamps use the documented
  Taiwan-time assumption; date-only values retain day precision without a
  fabricated timestamp. Source modification time stays null. First/last seen
  and detected change time are observation metadata, not source update time
- First import: `HISTORICAL_BASELINE`, never `NEW`. Subsequent identical records
  are `UNCHANGED`. Changed serial payloads and new serials are only revision/
  identity **candidates**, never confirmed world events
- Serial values begin at 1. Cross-snapshot identity stability is **UNKNOWN**;
  they could be positional. Do not promote `CANDIDATE_REVISION` or
  `CANDIDATE_NEW_ID` to actual updates/new events until this is resolved
- Exact duplicate serial/payload rows collapse; conflicting duplicate serials
  fail the whole snapshot. Identical content across different IDs is counted
  for review, not merged. Cross-source origin reconciliation remains unbuilt
- Missing rows are `UNKNOWN_NOT_RETRACTION`. Previously seen identities survive
  absence so reappearance is not classified as a newly seen identity
- Names, titles, body, department strings, contacts, arbitrary URLs, excerpts
  and images are omitted from receipts and persisted state. Department and
  content hashes retain comparison lineage. Raw department display and record
  text require a separate private review/view implementation; they are not
  made public by this collector
- Public projection is always empty. Rights remain `UNKNOWN`, reviewer required,
  no full text/excerpts, activated=false. OGDL metadata alone does not create a
  human approval or override privacy/publication rules

## Still no-go for production

Need verified serial stability/original identifiers, independent observations,
999-row truncation/history/completeness investigation, cross-source original
URL reconciliation, record-level field/rights/retention review, genuine human
approval, activation-baseline integration and separately authorized deployment.
No daily 100–200 item, full Taichung, recent-feed or production-quality claim is
supported. Fixture success is implementation evidence only.
