# Offline document evidence foundation

## Status: fixture-tested, server route integrated; real rights still blocked

`workers/query-gateway/src/document-evidence.js` is a permission-gated text-document retrieval and extractive evidence-compilation module. The server-only `document-research.js` bridge now connects it to the existing research route for explicit documents/synthesis modes. Each request builds a fresh store from a bounded trusted inline bundle and independent pins; handles never persist or cross requests. See [document research integration](document-research.md). No real source permissions, governance changes, credentials, storage resources, uploads, live network calls, deployments, or publications have been added or performed.

The repository's schema-1 source policy still admits **zero formal records**. It cannot instantiate this store. The existing `formalAdmission` function is unchanged. The metadata-only MiniMax selection/gating behavior is preserved; the fixed bounded provider transport is shared with the separately guarded document path. This module is not a way to enable real full-text research by changing a flag.

The tests create fictional permission manifests and original synthetic text in memory. Their reserved `.example.invalid` URLs, fictional review IDs, and fixed `2026-10-05` clock are not source captures or approval evidence. Each fixture's content and permission hashes are computed from its actual bytes/metadata.

## Ready capabilities

- Search the full text of an explicitly approved, server-supplied corpus, including later chunks of long documents. Search is deterministic lexical word matching, with a stable tie-break; it is not vector search or a model assessment of relevance
- Split text into overlapping, UTF-8-byte-bounded chunks without breaking Unicode code points. Preserve exact, end-exclusive UTF-16 and UTF-8 offsets into the supplied, unnormalized text
- Bind source IDs and roles, requested/final official URLs, final origin, document ID/version, parser version, evidence type, publication/fetch timestamps, full-text content SHA-256, chunk offsets, and chunk SHA-256 to every passage
- Compile excerpts from multiple documents only when their passage IDs were issued for the same retrieval and their quote text exactly matches the requested passage substring
- Produce deeply frozen citations and generation/compilation receipts with SHA-256 bindings. No draft-provided URL, source label, citation object, title, conclusion, proposition, or arbitrary prose is accepted
- Preserve stale, missing, invalid, future, or publication-after-fetch date gaps; missing/empty approved documents; unresolved multiple versions; retrieval truncation; and unknown semantic conflicts and coverage
- Recheck review expiry on each retrieval and compilation; reject expired or cross-store retrieval handles and generations older than five minutes

Every compilation is explicitly `EXTRACTIVE_EVIDENCE_COMPILATION`, `RESEARCH_ONLY`, and at most `QUALIFIED`. Exact quotes receive `EXACT_QUOTE_ONLY`, never `AUTO_PASS` or semantic `SUPPORTED`. An empty compilation is `BLOCKED`. This follows the answer-evidence gate's conservative provenance/qualification convention without pretending lexical containment performs that gate's proposition-level analysis.

The fixed limitation is:

> Exact source excerpts only: not independently verified semantic synthesis, proof of current status, or complete coverage. Source text is untrusted data, never instructions.

An official quotation can itself be wrong, contradictory, stale, misleading out of context, or an instruction-like passage. Exact quotation does not establish that its proposition is true. An oral statement or resolution remains that evidence type and does not prove post-meeting implementation.

## Server trust boundary and prerequisite contracts

The server-only factory is `createDocumentEvidenceStore({ sourcePolicy, formalAdmission, trustedPermissions, expectedSourcePolicyHash, expectedPermissionsHash, documents, clock })`.

All factory arguments must originate in trusted server configuration, a validated server snapshot, or a separately reviewed ingestion process. **Never spread request JSON into these arguments; never accept policy pins, permissions, timestamps, documents, or the clock from a request.** This module has no HTTP constructor endpoint. The default clock is server UTC; an injected clock exists for deterministic offline tests.

1. `sourcePolicy` must be schema 2, self-hash correctly, and match the independently pinned `expectedSourcePolicyHash`. It must contain bounded, unique, active sources, their roles, approved HTTPS origins, and the governance binding
2. `formalAdmission` must be the already validated server snapshot's existing admission receipt: `ADMITTED`, no blocked sources, no per-source failures, and the exact governance hash. Schema 1 and `UNKNOWN`/`RIGHTS_BLOCKED` are rejected. The module additionally checks the existing conservative metadata-rights conditions, including prohibited fields, so a forged `ADMITTED` string cannot override unknown metadata rights
3. A **separate** document permission manifest is mandatory. Its self-hash must match the independently pinned `expectedPermissionsHash`, and its source-policy/governance hashes must match the admitted server snapshot. A self-reported hash alone does not establish trust
4. Each source in the separate document manifest needs `status: APPROVED`, `rights_status: VERIFIED_DOCUMENT_PERMISSION` or `OPEN_DATA_LICENSED`, `review_required: false`, an explicit review ID/time, and individually explicit boolean `full_text_allowed: true`, `excerpt_allowed: true`, and `derived_usage_allowed: true`
5. Permission schema 1 requires `model_transmission_allowed: false` and provides **no permission to transmit text to a model**. The optional schema 2 requires separately explicit reviewed MiniMax transmission approval, exact destination/model/purpose, public/nonpersonal confirmation, and an exact allowlist of document-metadata hashes; see the integration document. Each source must then explicitly have `model_transmission_allowed: true`. The resulting public compilation still carries `model_transmission_allowed: false`: it is a display artifact, never a transport authorization capability
6. Every allowed document's exact metadata is listed in `approved_documents`. Supplied metadata must exactly match that server-controlled list and the SHA-256 of the supplied UTF-8 text. Permission to use a source is not permission to inject an arbitrary document at the same origin

The existing metadata permission fields remain `full_text_allowed: false` and `excerpt_allowed: false`; they are never edited to bypass `formalAdmission`. The new offline manifest represents an additional permission boundary. It does not change what the production metadata gateway may publish.

Permission manifest fields are closed:

- `schema_version: 1` (extracts only) or `2` (separately reviewed transmission), `policy_id`, positive integer `policy_version`, `policy_hash`
- `source_policy_hash`, `governance_hash`, `reviewed_at`, `expires_at`
- `sources`: the exact per-source rights fields described above
- `approved_documents`: exact document metadata without the text
- Schema 2 only: `transmission_policy`, whose exact closed fields are documented in the integration document

`policy_hash` is SHA-256 over canonical JSON of all remaining manifest fields, with object keys sorted and array order retained. The module exports `canonicalDocumentJson` and `documentSha256` for fixture construction and receipt inspection. These utilities do not approve anything.

Document fields are closed:

- `schema_version: 1`, `document_id`, `document_version`, `source_id`
- `evidence_type`: `WRITTEN_OFFICIAL`, `ORAL_OFFICIAL`, or `RESOLUTION`
- `requested_url`, `official_url`, `origin`
- `published_at`, `fetched_at`
- `content_sha256`, `parser_version`, `text`

IDs/versions are bounded opaque identifiers, never filesystem paths. URLs must be canonical HTTPS URLs at the policy-approved origins; credentials, fragments, non-default ports, IP literals, noncanonical traversal, encoded path separators/traversal, and unsafe paths are rejected. Both requested and final URLs must be approved. URL strings are provenance only: the module never dereferences them.

`content_sha256` is the hash of the exact supplied UTF-8 text. This foundation does no normalization, extraction, OCR, URL fetching, redirect handling, file reading, or private upload processing. The companion offline PDF adapter below preserves a separate original-byte hash and page map; the foundation's text offsets remain text offsets, never PDF byte offsets.

## Request and draft contracts

`await store.retrieve({ query })` accepts only a query. It does not accept documents, rights, URLs, custom bounds, date overrides, or freshness assertions. It returns bounded passages with a generation receipt. The whole corpus is checked before any passage is released; a corrupt supplied document fails the store rather than silently disappearing.

`await store.compileDraft(retrieval, candidate)` accepts the exact immutable retrieval handle issued by the same store. A serialized/copied/forged retrieval object is rejected, even if its hash looks valid. The research HTTP adapter creates and uses handles within a single request, without new persistent storage. No client can supply a serialized evidence context. Any future multi-request reuse would need authenticated, bounded server-side lifetime/storage ownership instead of trusting a client's reconstructed evidence object.

A candidate has exactly:

```json
{
  "schema_version": 1,
  "generation_id": "DOCGEN-<issued SHA-256>",
  "excerpts": [
    {
      "passage_id": "PASSAGE-<issued SHA-256>",
      "start_utf16": 0,
      "end_utf16": 12,
      "quote": "exact substring"
    }
  ]
}
```

`start_utf16` and `end_utf16` are integer, end-exclusive offsets within that passage. The module computes the final document-relative quote offsets and citation itself. The example demonstrates field names only; its illustrative quote and offsets are not a valid fixture.

Unknown or unreturned passage IDs, wrong generation IDs, duplicate excerpts, nonexact or normalized quotes, invalid offsets, extra output fields, and arbitrary propositions reject the entire compilation. The caller cannot provide a conclusion such as “all sources confirm completion.” Prompt-injection strings remain quoted data. A renderer must display them as escaped text, never HTML or executable instructions; this foundation module does not render text or define a provider prompt; the optional route bridge and evidence UI are described separately.

## Hard bounds

All values are exported in `DOCUMENT_EVIDENCE_LIMITS` and cannot be overridden by requests:

| Resource | Maximum |
| --- | ---: |
| Documents / permission-listed documents / active sources | 16 each |
| Text per document | 64 KiB UTF-8 |
| Total text corpus | 256 KiB UTF-8 |
| Chunks | 512 |
| Text per chunk / overlap | 1,024 / 128 UTF-8 bytes |
| Returned passages / compiled excerpts | 8 each |
| Serialized retrieval / compilation | 16 KiB each |
| Query | 512 UTF-8 bytes, 16 distinct word terms |
| Serialized draft | 16 KiB |
| Individual quote | 1,024 UTF-8 bytes |
| Serialized source policy / permission manifest | 64 KiB each |
| Retrieval-handle age for compilation | 5 minutes |

Context size may reduce passage count below eight; truncation is explicit. Compilation can fail its independent envelope bound instead of returning partial evidence. Too many query terms are rejected rather than silently dropped. Overlap helps ordinary boundary-spanning terms, but lexical matching is not guaranteed phrase-complete for arbitrary long tokens or complex multilingual queries.

## Temporal and completeness semantics

Review timestamps must be valid canonical UTC instants and currently valid. Document times can be `null` or bounded strings. Only exact UTC ISO timestamps with seconds and optional three-digit milliseconds are interpreted. Missing, malformed, ambiguous/timezone-less, or noncanonical document dates create gaps, not inferred dates. Invalid review dates reject permissions.

Publication and fetch are separate. Fetching now does not make an old publication current. The fixed conservative audit windows flag publication older than 30 days and fetch older than seven days; being within those windows still does not prove current effect or latest-version status. Multiple opaque versions remain unresolved rather than being ordered by labels or silently collapsed.

Every result always has `can_assert_current: false`, `can_state_zero_events: false`, `source_health: NOT_ASSESSED`, and `window_completeness: UNKNOWN`. Missing documents, empty text, no lexical matches, and limited/truncated context never become “no events” or “complete coverage.” Semantic disagreements between excerpts are not automatically detected or resolved.

## Cryptographic binding, with honest limits

- The policy and permission pins anchor this store to server-reviewed configuration
- Each document's content hash is verified; a metadata hash binds IDs, version, URLs, origin, dates, and parser
- Passage IDs hash their citation metadata, exact offsets, and passage-text hash
- The generation hash binds validator version, both policy hashes, corpus metadata/content hashes, query hash, server evaluation time, returned passages, and all coverage/date gaps
- Each compiled citation adds generation ID, exact quote offsets and hash, then hashes the complete citation
- The compilation hash binds the complete output, draft hash, generation, timestamps, citations, gaps, and limitations

SHA-256 is integrity linkage, not a signature, license determination, remote publisher attestation, or semantic verification. An attacker who replaces both data and an untrusted hash can recompute hashes. Independent pins, trusted server handles, and the adapter's separately reviewed authentication/admission service are required. An existing store is an immutable permission snapshot. The adapter creates it afresh for every request and rechecks expiry after admission and before provider dispatch, after compilation hashes, and before response release, including error fallbacks. An already running request cannot learn a changed external configuration automatically; a live revocation design must stop in-flight work or provide a trusted revocation check, rather than rely solely on expiry.

## Remaining work before any live feature

1. Independently review real source-specific full-text storage, excerpt display, derived use, expiry/revocation, and any model transmission permissions; authorize and pin the distinct server permission artifact through the real workflow
2. Implement trusted public-document ingestion with original-byte capture, bounded parsing, provenance, meaningful binary-document locators, version handling and retention. The current route only consumes preapproved inline text; it does not acquire documents
3. Complete live authentication/budget-service review, provider audience/billing approval, secure owner-entered credentials, and production configuration. A key alone cannot enable document research
4. Review privacy, live revocation during in-flight work, and source licensing; validate real provider behavior and deployed end-to-end operation under separately authorized conditions
5. Free-form draft synthesis now has exact citation validation and a separate AI critic reading original passages. This is explicitly AI-reviewed, not independent/formal semantic verification. Regex guards plus AI review cannot guarantee arbitrary semantic claims are safe or correct; human verification remains required
6. Obtain separate deployment authorization. No fixture permissions are promoted into real governance

No real permission validity, provider quality, live document acquisition, end-to-end deployed behavior, or independent semantic synthesis is established by the fixture suite.

## Offline PDF bytes and citation replay

`intel_v2/document_pdf.py` accepts already-held PDF **bytes**, extracts every page
with exactly `pypdf==6.19.0` in a child process, and returns `{text, receipt}`.
It does not call or change collectors. `collect_download_list` already retrieves
attachments; S-006's existing `attachment_revision_evidence` reads the first page
for a labelled revision date. Those collector outputs and their legacy binary
hash fields are unchanged by this increment.

The receipt has `schema_version: 1`, `extractor_version: document-pdf/1`, parser
name/version, original `binary_sha256` and byte count, page count, exact text
`content_sha256` and byte count, fixed `page_separator: "\n\f\n"`, page map,
gaps, and `receipt_sha256`. The receipt hash uses the same sorted-key canonical
JSON convention as document evidence, excluding only `receipt_sha256` itself.
Page numbers start at one. Every page has exact UTF-8 and UTF-16 end-exclusive
start/end ranges into the joined text, a page-text hash, and `TEXT` or
`NO_EXTRACTABLE_TEXT` status. Separators lie **outside** the page ranges. Text is
not normalized or silently trimmed; the parser's exact plain-mode output is
preserved. Document metadata uses `parser_version: pdf-text-v1:pypdf-6.19.0`.

Python and JavaScript classify blank text using the same closed 30-code-point
set: `U+0009..000D`, `U+001C..001F`, `U+0020`, `U+0085`, `U+00A0`, `U+1680`,
`U+2000..200A`, `U+2028`, `U+2029`, `U+202F`, `U+205F`, `U+3000`, `U+FEFF`.
They do not use language-dependent `strip()` / `trim()` for this decision. All
other characters remain significant data, including combining characters and
U+200B; this classification does not establish semantic support or legibility.
It never removes characters, normalizes text, or changes content/binary hashes.

Blank or scanned pages create `PDF_PAGE_NO_EXTRACTABLE_TEXT` gaps in mixed PDFs.
If **every** page has no nonblank extracted text, extraction and replay admission
reject with `PDF_NO_EXTRACTABLE_TEXT`; joined separators cannot make an empty
document usable. This code cannot distinguish an intentionally blank page from
a scanned image or other unreadable content, and does not perform OCR. A page
extraction error rejects the whole document; no partial extraction is returned.
All encrypted PDFs (including empty-password envelopes), malformed PDFs, wrong
or missing parser versions, and oversized PDFs reject explicitly. Empty PDFs
and PDFs over 32 pages reject. An individual decompressed page may consume memory
before its text-size check; timeout and output caps are not an OS memory sandbox.

| Additional offline bound | Maximum |
| --- | ---: |
| Original bytes per PDF / all PDFs | 8 MiB / 32 MiB |
| Pages per PDF | 32 |
| Parser child duration / stdout / stderr | 10 seconds / 512 KiB / 16 KiB |
| Receipt JSON / PDF replay JSON | 16 KiB each |
| Fixture CLI outer duration / stdout / stderr | 12 seconds / 512 KiB / 16 KiB |

The existing 16-document, 64-KiB-per-text-document and 256-KiB-text-corpus limits
still apply. Caps reject the complete operation; they never silently truncate.
The child command is fixed, uses the existing Python interpreter, and has no
shell or document-supplied arguments. Source instructions remain inert text.

`createDocumentPdfReplay({evidenceConfig, pdfs, expectedExtractionPins})` in
`scripts/replay-document-pdf.mjs` is an offline adapter, not an HTTP constructor
or a second permission schema. Each PDF supplies `{metadata, binary, extraction}`.
`evidenceConfig` supplies the **unchanged** existing trusted store configuration
(without `documents`). Each independent extraction pin contains the exact
`document_binding_sha256`, `binary_sha256`, and `receipt_sha256`. All three must
come from separately trusted offline configuration. The adapter checks the
original bytes, text, complete page map, receipt hash, parser/version and pins,
then calls the existing permission-gated store. Missing, stale, unadmitted,
unapproved or differently pinned metadata still rejects. It never re-signs a
permission manifest to accommodate newly extracted text.

Retrieval uses the existing immutable store handle. Compilation returns
`{compilation, pdf_replay}`. The original compilation and its existing citation
hashes remain intact. The separate replay binds `compilation_sha256` and every
`citation_sha256` / `document_binding_sha256` to the original binary hash,
extraction receipt hash and quote's page-relative and document-relative ranges
in both encodings. `replay_sha256` binds the whole replay. A quote crossing the
separator lists only intersected `TEXT` pages and adds
`PDF_QUOTE_INCLUDES_PAGE_SEPARATOR`; the separator is not attributed to a PDF
page. Blank-only quotes reject, even when they occur in a matching passage. A
quote containing blank-page text plus readable text keeps the exact quote but
adds `PDF_QUOTE_INCLUDES_UNEXTRACTABLE_PAGE_TEXT`; blank-page ranges never appear
as supporting page locators. Unreadable-page gaps remain explicit even when the
quote is on a readable page. Renderers must display the replay's
`text_completeness: INCOMPLETE` and page gaps for mixed PDFs. With no known page
gaps it says `EXTRACTED_TEXT_ONLY`, never that all visual contents were recovered.
Raw retrieval handles are search context, not final PDF citation replay output.
These integrity hashes are not publisher signatures, proof of extraction
accuracy or grants of real-source rights.

`tests/fixtures/document-pdf` contains only original synthetic PDFs, deterministic
fixed expected hashes/page maps and separately fixed fictional trust/pin JSON.
Its deliberately invoked generator creates synthetic fixture approvals only;
acceptance and the runner do **not** regenerate expected data or grant permissions.
The small synthetic CID font is a parser fixture, not a publication font. The
expected page maps must be checked against the real pinned parser before claiming
PDF parser acceptance; Node receipt replay tests alone do not establish it.

```sh
python -B -m unittest tests.test_document_pdf
node --test --test-concurrency=1 apps/web/tests/document-pdf.test.mjs
node scripts/replay-document-pdf.mjs --python <existing-trusted-python-with-pypdf-6.19.0>
```

The Node test's real-parser integration uses `GOVINTEL_PDF_PYTHON`, then `PYTHON`,
then `python`. Set it to the existing pinned interpreter; no acceptance test
silently skips or substitutes another parser. Original and review fixtures cover
real Unicode whitespace/mixed/32-page extraction, pure blank and image-only
documents, a failed page after readable text, genuinely empty-password encryption,
and corrupted/over-limit inputs. They are parsed by the actual child process, in addition
to the independent fixed-receipt mutation and permission tests. The standalone
Python test and fixture generator load the bytes module directly, without loading
unrelated package semantics or their platform timezone-data dependency.
The deliberate generator's `--review-only` mode creates the seven additional
review originals and their separate `review-expected.json`; it never rewrites the
original six PDFs, `expected.json`, or `trust.json`. Node controlled-child tests
check its fixed outer timeout and stdout/stderr caps, including exact-cap and
cap+1 cases. Receipt tests distinguish exact byte-cap contract rejection from
cap+1 byte rejection; legal closed 32-page receipts are smaller than that envelope.
Multi-page excerpts exercise a successful replay below the 16-KiB cap and complete
rejection above it. No limit is exposed as a request override.

The CLI reads only its closed self-authored fixture allowlist and fixed fictional
trust JSON. It quotes the answer found only on page two and prints extraction,
the existing evidence compilation and PDF replay JSON. It accepts no arbitrary
file, URL, registry, upload, permission override or parser version override.
There are no network/provider calls, installation, credentials, publication or
live-source admission in this flow. Integrating real PDF ingestion and rights
review remains separate work; this adapter is not wired to the research route.

## Executable acceptance

```sh
node --test apps/web/tests/document-evidence.test.mjs
```

The suite covers the fictional full-text-to-cross-document-excerpt flow, Unicode offsets, all hash layers, immutable citations, rights and formal-admission failures, pinned policy mismatch, poisoned documents and metadata, unsafe URLs and path traversal, invented IDs and quotes, unsupported propositions, oversize inputs, expiry/replay, timestamp gaps, unresolved versions/conflicts, empty/no-match behavior, data-only prompt injection, and zero network calls. A structural test confirms the evidence module performs no network calls and the generic provider helper does not instantiate document stores. Separate route and gold tests exercise the optional bridge with mocked provider responses and isolated fictional governance.
