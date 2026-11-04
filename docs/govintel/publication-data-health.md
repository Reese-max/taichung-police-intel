# Publication data health

The outcome job downloads `publication-evidence-<run>-<attempt>` into a separate
data directory. It never executes or restores code from that artifact.

`publication-outcome.py --data-evidence <directory>` verifies the eight checkpoint
files against the build's SHA256 `GENERATION_ID` and checks the system receipt's
SHA256 against the post-checkpoint build output, before it re-derives collection
and canonical-validation stages using the checked-out source policy and compares
them with the retained `system-health.json` stages, including policy, collection
identity, timestamps, source warnings, and counts. Missing, malformed, mismatched,
or duplicate receipts remain `UNKNOWN` and cause the reporting command to fail.
The workflow always sets `DATA_EVIDENCE_REQUIRED=true`, including failure paths.

The runtime receipt keeps these results separate:

* `deployment_status` and `public_data_verified`: Pages action and exact public
  bytes/checkpoint verification. They do not establish source completeness.
* `data_status`: source collection and canonical data quality. Successful steps
  cannot upgrade `PARTIAL`, `STALE`, `UNKNOWN`, or `BLOCKED` source receipts.
* `data_receipt.source_gaps`: per-source coverage and freshness warnings. The
  operator summary retains the corresponding next actions.
* `phase_results`: actual CI outcomes. A replay can use bound data while
  `COLLECT=skipped`; it does not become a new collection.
* `query_readiness` / `production_verified`: existing formal rights admission.
  Data receipt verification cannot change `RIGHTS_BLOCKED` into admission.

A valid PARTIAL snapshot can therefore have verified deployment and an
operationally successful workflow while the publication lane remains PARTIAL.
Invalid receipt binding fails reporting without claiming the site is offline.
Legacy library callers without required evidence retain their CI stage view,
but `data_status` remains UNKNOWN and no data receipt is verified.

Offline regression: `python -X utf8 -B -m unittest -v
tests.test_publication_data_receipt tests.test_publication_outcome
tests.test_publication_alert tests.test_system_health`.
