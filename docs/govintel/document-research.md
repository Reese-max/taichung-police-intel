# Permission-gated document research route

## Implemented and verified offline

The existing `POST /research` route accepts optional `mode: metadata | documents | synthesis`, defaulting to metadata. Metadata selection and the unchanged formal answer gate retain their prior behavior. Document modes use the same validated publication release and existing formal admission as mandatory prerequisites, then a separate server-owned document permission/bundle contract.

The actual repository schema-1 governance admits zero formal records. All three modes therefore remain rights-blocked against real checked-in sources. The document bundle/pins are not present in production configuration. No real permission, secret, paid call, resource, storage grant, push, or deployment is introduced by this code.

The implementation runs a fresh store and all retrieval/compilation handles entirely within one request. There are no uploaded documents, client-supplied policies, arbitrary URL requests, KV/R2 resources, durable contexts, or persistence.

## Trusted environment contract

The following are server configuration only, never request fields:

- `RESEARCH_DOCUMENT_BUNDLE`: a JSON object with exactly `{schema_version: 1, permissions, documents}`; serialized maximum 384 KiB
- `RESEARCH_DOCUMENT_PERMISSION_HASH`: independent SHA-256 pin for the complete permission manifest, excluding its own hash field
- `RESEARCH_DOCUMENT_SOURCE_POLICY_HASH`: independent SHA-256 pin matching the already validated snapshot source policy

The source policy and `formalAdmission` receipt come exclusively from the real server snapshot. The route checks `ADMITTED` before reading the bundle, including before accessing any document text. Missing bundle or pins returns `DOCUMENTS_UNCONFIGURED`. Schema-1 governance, unknown or prohibited source rights, hash mismatches, malformed contracts and expired reviews fail closed. There is no metadata-title fallback pretending to answer a full-text request.

Document identity, origin/URL restrictions, hashes, offsets, rights fields, and byte/count limits are specified in [the evidence foundation contract](document-evidence.md). Each document must match an exact server-approved metadata entry and full UTF-8 text hash. `content_sha256` describes the supplied text, not an unparsed PDF's binary bytes.

### Additional permission for model transmission

Schema-1 permissions remain valid for deterministic document extracts and explicitly prohibit model transmission. Schema 2 preserves all existing source/excerpt/derived-use checks, requires `model_transmission_allowed: true` for each included source, and adds this exact top-level `transmission_policy`:

```json
{
  "provider": "MiniMax",
  "endpoint": "https://api.minimax.io/v1/chat/completions",
  "model": "MiniMax-M2.7",
  "purpose": "GOVINTEL_DOCUMENT_SYNTHESIS_AND_CRITIQUE",
  "review_required": false,
  "review_id": "an-explicit-reviewed-identifier",
  "reviewed_at": "2026-10-05T09:00:00Z",
  "public_nonpersonal_confirmed": true,
  "approved_document_bindings": ["<SHA256 of canonical document metadata without text>"]
}
```

This example documents a schema, not an approval. Actual reviewed dates and document bindings are required. Every retrieved passage sent to either producer or critic must have its exact document-metadata hash in this list. Destination, model, purpose, review status and public/nonpersonal confirmation are exact typed values; unknown/implicit rights cannot authorize a call. This contract is independently pinned together with the entire permission manifest.

The browser's public-data checkbox is required for each question, but does not grant document rights or authorize a destination. Existing provider activation, owner-entered secret, billing/audience review and authenticated atomic budget admission remain separate requirements.

## Mode behavior and evidence display

`documents` performs deterministic bounded full-text lexical retrieval and exact excerpt compilation. It calls neither the model nor the budget admission service. Success is `EXTRACTS_READY`, with `answer: []`, an immutable `document_evidence` compilation, and source links/dates. Source `title` is explicitly the document identifier; it is not an invented document title. Source `evidence_id` is `DOC-<document_binding_sha256>`, preventing collisions between sources that use the same document ID/version.

`synthesis` first builds the same safe extract result. Only with explicit schema-2 transmission permission and all provider gates ready does it make up to two independently budgeted model calls:

1. A producer receives the question, previous user questions, original retrieved passages, provenance dates/types and gaps. It may propose at most three short claims with exact source quotations
2. Deterministic validation checks the closed schema, claim IDs, quote offsets/content, and actual issued passage IDs. Invented citations, extra prose/URLs, conflicting duplicate quotes and invalid offsets are rejected before a second reservation
3. A separate critic call receives the same original retrieved passages and all proposed claims. It assesses support, insufficiency and contradictions across the full retrieved set, including passages the producer did not cite
4. Every claim needs exactly one critic verdict. `CONFLICT` and `INSUFFICIENT` claims are held; their generated text is not returned. A conservative deterministic text guard also holds current/latest status, zero-event, complete-coverage, implementation and safety assertions even if the critic says `SUPPORTED`
5. Accepted claims are recompiled against exact server evidence. Their quoted citation objects exactly match the returned `document_evidence` excerpts. The result is `SYNTHESIS_DRAFT`, never `ANSWER_READY`, formal `SUPPORTED`, or `AUTO_PASS`

The critic is a separate run of the same bounded MiniMax model, not an independent factual authority. Exact citations plus a model agreement do not guarantee entailment or truth. Regex guards are conservative hardening, not a complete semantic classifier; paraphrases and arbitrary semantic errors can evade them. All visible drafts remain `AI_REVIEWED_NOT_FORMALLY_VERIFIED`, `RESEARCH_ONLY`, and require human verification.

Historical dates, missing/stale/future timestamp gaps and unresolved versions remain visible. `can_assert_current` and `can_state_zero_events` are always false. An oral response or resolution does not prove implementation. No matching passage never means no real-world event occurred.

### Producer and critic schemas

The producer returns exactly:

```json
{
  "claims": [{
    "claim_id": "C1",
    "text": "A bounded historical or undated draft statement",
    "temporal_scope": "HISTORICAL_OR_UNDATED",
    "citations": [{
      "passage_id": "PASSAGE-<issued hash>",
      "start_utf16": 0,
      "end_utf16": 12,
      "quote": "exact substring"
    }]
  }]
}
```

There may be zero to three unique IDs from C1/C2/C3. Each claim text is at most 512 UTF-8 bytes, with one or two quotes each at most 512 UTF-8 bytes. Offsets refer to original passage UTF-16 indices and are end-exclusive. The illustrative text/offsets above are not a valid fixture.

The critic returns exactly `{reviews:[{claim_id,verdict}]}`, with one review for each proposed claim and verdict `SUPPORTED`, `INSUFFICIENT`, or `CONFLICT`. Unknown/duplicate/missing IDs, extra fields, malformed/truncated responses, or tool calls reject the draft. No critic rationale or provider thinking is exposed.

### Public synthesis shape

`answer` remains empty. Draft prose lives only in `synthesis.claims`:

```text
synthesis = {
  schema_version: 1,
  semantic_verification: AI_REVIEWED_NOT_FORMALLY_VERIFIED,
  publication_tier: RESEARCH_ONLY,
  gate_status: QUALIFIED | BLOCKED,
  claims: [{ claim_id, text, temporal_scope, citations: [{ quote, citation }] }],
  held_claims: [{ claim_id, reason_code }],
  can_assert_current: false,
  can_state_zero_events: false,
  limitation,
  receipt
}
```

Every accepted `{quote,citation}` matches an exact entry in `document_evidence.excerpts`. The receipt binds generation, both permission/source-policy pins, corpus hash, distinct producer/critic run IDs, fixed model/prompt versions, hashes of both structured outputs, exact compilation hash, claims hash and server timestamp, then hashes that receipt. Hashes are integrity linkage under trusted server pins, not signatures or independent semantic proof.

If all claims are held, status stays `EXTRACTS_READY`, reason is `NO_SUPPORTED_CLAIMS`, and a `BLOCKED` synthesis object contains empty claims and held IDs/reasons only; its receipt binds the returned original extract compilation. A provider or schema failure returns safe extracts without a synthesis object. Missing/unusable documents return empty `METADATA_ONLY` with a specific reason, never title-only synthesis.

## Bounds, transmission truth and expiry

- One bounded inline bundle, 16 documents, 64 KiB/document, 256 KiB text corpus; existing evidence caps still apply
- At most eight retrieved passages; conservative envelope reservation leaves room for exact quote/citation compilation and exposes truncation
- Questions in document/synthesis modes are limited to 512 UTF-8 bytes, approximately 170 Chinese characters; violations return `DOCUMENT_QUERY_TOO_LARGE`. Metadata retains its prior 512-character limit
- The current question drives lexical retrieval. Up to four prior user questions are passed in full to the model within existing context limits; no question or history string is silently truncated. An elliptical question with no lexical match can return no evidence rather than fabricate a continuation
- Each provider request has a 16 KiB serialized input cap, 1,024 completion-token cap, 32 KiB response cap and 15-second admission-plus-provider timeout. Two calls therefore have a maximum 30-second sequential provider budget, with client cancellation applying to both. No retry or alternate provider/model fallback exists
- Each call independently reserves the conservative input/output allowance through the existing admission service. Neither question nor document text goes to admission
- The final adapter response is capped at 48 KiB. Each compilation independently remains at most 16 KiB

`provider_transmission_attempted` is tracked at the actual model fetch dispatch boundary. It is false for document-only retrieval and pre-provider blocks; it becomes true immediately before a permitted model request is invoked, even if the transport later fails. It records an attempt, not verified receipt by the provider. It does not reset after a failed second call.

A second reservation denial is `REVIEW_ADMISSION_DENIED`; other second-stage transport/input-budget failures use `SYNTHESIS_REVIEW_FAILED`. Earlier sent data is never described as “nothing sent.” Raw provider bodies, credentials and internal exceptions are not reflected.

Permission expiry is checked before retrieval/compilation, after asynchronous hashing, before each model phase, again after admission immediately before fetch dispatch, and before every response release including cached-extract error fallbacks. Expired rights yield an empty response, preserving truthful transmission state. Configuration is snapshotted per request; live revocation of an in-flight snapshot requires additional reviewed infrastructure.

Public compilation artifacts always retain `model_transmission_allowed: false`. Only a live server-store permission assertion can authorize the narrowly approved provider dispatch; receiving or copying a compilation never grants transport permission.

## Offline acceptance and remaining live work

```sh
node --test apps/web/tests/document-evidence.test.mjs apps/web/tests/document-research.test.mjs apps/web/tests/research-gold.test.mjs apps/web/tests/research-worker.test.mjs
```

Fixtures exercise actual `executeResearch` route logic, isolated compiled metadata governance, synthetic permission manifests/documents, exact Unicode citations, original-passage critic binding, per-call budgets, schema/quote forgery, temporal/completeness holds, permission expiry races, cross-source identities, client-response validation and truthful partial-transmission failures. Mock critics test control flow; these tests do not measure real semantic quality.

Still required before live use: independent real-source/document/transmission permission review; trusted ingestion and retention; authenticated admission service and approved budget; secure owner-entered credentials; revocation/privacy/legal review; live-provider and end-to-end deployment validation; separate deployment authorization. No fixture permission is evidence that these gates are satisfied.
