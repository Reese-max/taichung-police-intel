# GovIntel research gold v1

This is a **45-case synthetic, offline integration regression seed**, not a
production accuracy benchmark. No live provider, credential, document fetch,
network call, or model-generated gold label is used.

Run from the repository root:

```sh
node --test apps/web/tests/research-gold.test.mjs
```

The test runner reads `cases.jsonl` and executes the actual `executeResearch`
route for every case. Its only provider transport is an in-process MiniMax mock;
`expected.model_calls` counts mock invocations. A global network trap also rejects
accidental use of the default fetch transport. Producer and critic responses are
separate explicit fixtures, including deliberately invalid responses and verdicts.
Every returned route response also passes the real browser structural validator
and asynchronous document/quote/citation/compilation/synthesis hash verifier.

The route uses an isolated compiled fictional governance snapshot, plus separate
trusted-server document and transmission permissions created by
`apps/web/tests/document-research-fixture.mjs`. The helper is for tests only. It
does not approve real sources or alter repository-approved governance. The runner
checks that production policy files have the same hashes after the suite.

## Coverage

- All three request modes: metadata, document extracts, and synthesis drafts
- Approved passages and exact immutable document/version/quote citations,
  including Unicode UTF-16 and UTF-8 offsets
- Empty corpus, no matching evidence, and unconfigured fulltext access with zero
  provider traffic
- Unknown rights, schema-1 source policy, metadata-only grants, independent pins,
  document content and metadata hashes, and permission expiry
- Reviewed model transmission grants and exact document allowlists; denied
  second budget admission never exposes unreviewed generated text
- Stale, future, and missing dates, contradictory document versions, and critic
  `CONFLICT` or `INSUFFICIENT` verdicts
- Invented citations, invalid offsets, changed quotes, forged generation fields,
  source prompt injection, and untrusted producer URLs
- Provider HTTP failures, truncation, oversized output, missing critic reviews,
  question/history limits, and bounded document retrieval contexts
- Current, zero-event, and complete-coverage claims held even when the mocked
  critic says `SUPPORTED` and the producer labels them historical

## What a pass establishes

A pass establishes these implementation and fail-closed regression expectations
for the synthetic cases. It does **not** establish model accuracy, legal rights to
any live document, official factual truth, currentness, completeness, or absence
of events. Citation hashes and exact quotations establish evidence integrity;
they do not prove semantic entailment.

The formal `answer` array stays empty for document/synthesis modes. Extracts live
under `document_evidence`; accepted generated prose lives under `synthesis.claims`.
Drafts remain `AI_REVIEWED_NOT_FORMALLY_VERIFIED`, with immutable server citations,
held claim IDs/reasons, and no current-status or zero-event authority. Held model
prose must be absent from every serialized response field. A future quality claim
requires a separately reviewed human-labelled holdout and real model evaluation.
