# Issue #48 — official document to located facts

The existing `intel_v2/located_facts.py` core now has a bounded live acquisition
entry point in `scripts/located-facts.py live`:

```powershell
python -X utf8 scripts/located-facts.py live `
  --source-id S-028 `
  --requested-url https://data.gov.tw/api/v2/rest/dataset/88147 `
  --fetched-at 2026-09-21T00:00:00+00:00 `
  --rules docs/govintel/official-document-rules.v1.json `
  --output .tmp/official-data-bundle.json `
  --snapshot-output .tmp/official-data.json
```

The command validates the catalog origin before making a request, bounds the
response size, records raw/text hashes and parser version, and emits
`evidence_catalog` plus `public_event_inputs`. HTML text ranges and JSON
Pointers are verified against the exact document version. Facts remain
`FACT_CANDIDATE` or `NEEDS_REVIEW`; no LLM or caller can promote them to truth.

An explicit human review step is available after inspecting the saved snapshot:

```powershell
python -X utf8 scripts/located-facts.py confirm `
  --bundle .tmp/official-data-bundle.json `
  --body .tmp/official-data.json `
  --fact-id FACT-... `
  --reviewer-ref officer-1 `
  --verified-at 2026-09-21T08:00:00+00:00 `
  --output .tmp/official-data-confirmed.json
```

`confirm` rechecks the exact raw/text hash, locator, source value, normalization,
and valid-time source before binding the reviewer reference to the fact,
evidence row, and PublicEvent input. It also rejects a stale bundle receipt or
inconsistent fact/evidence/event linkage before writing the review. The gateway
rejects a `CONFIRMED_OFFICIAL` status without that bound review, so editing a
status, normalized value, or date alone cannot promote a candidate.

`self-check` replays both HTML and JSON adapters offline. The checked-in
`official-document-receipt.v1.json` records one live official data.gov.tw
metadata/API replay separately from the offline fixtures. Raw live bytes are
not treated as a public UI bundle; the receipt keeps their content hashes and
link-only rights decision.

The read-only Query Gateway can load a saved bundle with
`--located-facts-bundle`. It revalidates the document origin, version/hash
bindings, derivation fields, deterministic fact IDs, and bundle receipt before
startup; only explicitly
`CONFIRMED_OFFICIAL` facts enter the server-controlled Answer Evidence Gate.
`FACT_CANDIDATE` and `NEEDS_REVIEW` remain excluded until a separate review
step promotes them, so a caller cannot inject its own evidence catalog. The
source itself must also be `PRODUCTION_ACTIVE` or `AUDITED_EXISTING` in the
server catalog; unpromoted `VERIFIED_CANDIDATE` sources are rejected before
their facts can enter the trusted catalog.

## 2026-09-21 local replay

The live S-028 bundle was confirmed into a temporary test-only output using
`reviewer_ref=test-only-local-check`, then loaded by the local gateway. A
`validate_answer` claim for `dataset:88147:dataset_title` returned `gate_status=PASS`
with evidence `EVID-79778138CAE1F24005C5` and document version
`DOCV-94D57674E75896E14B34`. The same response reported `freshness=STALE`,
which is intentional: locator/evidence confirmation does not make an old
publication current. The temporary confirmed bundle was not copied into the
repository or deployed, and this replay is not a production human-approval or
public-reachability receipt.

## 2026-09-22 live recheck

The same bounded `live` command was rerun read-only against the approved S-028
HTML and JSON URLs. The JSON response remained reproducible: 2 facts and 2
`FACT_CANDIDATE` evidence rows, raw SHA-256
`94d57674e75896e14b34288f6b333e0816d4295d4e20bbbbcf9526bef1102b71`, and
bundle SHA-256
`96e70abd78dc0e2c239a58958066da00c49107df51644d13a81000e3f7aa112a`.

The HTML response changed version: raw SHA-256
`93d1a132b86c81dba37247748ea2bce13a68a29ed1f2fba78f28b4a19f657504`,
document version `DOCV-93D1A132B86C81DBA372`, and bundle SHA-256
`cc2c037e92f93044df7f05cce8794c231f1b71238c087f58094ce486b9313b2b`.
Its single extracted fact remains `NEEDS_REVIEW`; the changed page therefore
does not enter the trusted catalog automatically. This recheck is current
source evidence only, not human review, publication, deployment, or promotion.
