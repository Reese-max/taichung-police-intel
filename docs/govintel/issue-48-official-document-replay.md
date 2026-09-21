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

`self-check` replays both HTML and JSON adapters offline. The checked-in
`official-document-receipt.v1.json` records one live official data.gov.tw
metadata/API replay separately from the offline fixtures. Raw live bytes are
not treated as a public UI bundle; the receipt keeps their content hashes and
link-only rights decision.
