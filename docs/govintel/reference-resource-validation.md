# Local reference resource contract validator

`reference-resource-validator.py` is a generic **local CSV + supplied plan** checker. It does not fetch, unzip, verify publisher identity, grant rights or promote sources. `reference-csv-audit.py` is a separate strict contract for the actually acquired S036/CTX165 schemas, exact description row, event-time quality and ROC-period/duplicate audit. JSON S028/CTXPOP originals must not be fed into either CSV checker.

The generic script came from an actual `grok 1.0.44` CLI partial candidate. That run was stopped at the approximately ten-minute wall-time cap (exit 143) before its own tests/docs/final result. It was independently repaired and tested locally: do not describe it as Grok-completed verification or a human review.

## Generic plan

Supply `schema_version:1`, supported `source_id`, exact `schema`, nonempty `id_fields`, `declared_sha256`, integer `expected_row_count`, and `period_evidence` with a field and exact value. Optional `numeric_fields` check finite decimal strings. Local CSV is bounded to 8 MiB. Unsupported JSON/ZIP/HTML, wrong hash/schema/rows/period, missing/duplicate IDs, empty resources, explicit failed acquisition HTTP and invalid CSV fail the contract. Header-only files never prove a period or zero-event completeness.

```sh
python scripts/reference-resource-validator.py --plan /path/to/plan.json \
  --resource /path/to/local.csv --out /path/to/receipt.json
```

Exit 0 means only `LOCAL_SUPPLIED_PLAN_CONTRACT_ONLY` passed. Expected rows/period originate in the caller's plan and are not independently authenticated official totals. `business_scope_completeness=NOT_VERIFIED`, `promotion=false`, `production_active=false`, `rights_decision=PENDING` remain true for all results. Exit 2 means invalid plan or contract incomplete; a blocked source still produces its JSON receipt when the plan is valid.

### Coordinate evidence

POINT_X/Y names and a literal `authority=AUTHORITATIVE_METADATA` in a plan do not prove an official CRS. The script preserves supported candidate EPSG/units/axis information separately, may perform a requested numeric diagnostic against that candidate, but always keeps official `coordinate_reference_system=UNKNOWN`, `crs_authenticity=NOT_VERIFIED`, `coordinate_validation=NOT_RUN` where coordinates exist. Requested coordinate contracts cannot become complete without actual independently verified official metadata. Projected coordinate diagnostics check finite numbers only, not projection transformation or geospatial accuracy. No distance or jurisdiction inference is allowed.

### Absent original (D2)

Use an absent `--resource`, null SHA/expected row count, metadata hash/status and `acquisition.status=BLOCKED` with the historical original 403. Rows remain null, not zero; no coordinate or phone/address validation ran. The date and source of the failed original must remain the historical observation, separate from today's successful metadata reread.

[Actual loop receipts](reference-source-loop-20261007.json) separately document D2 metadata/current original blocker; S036/CTX165 full byte acquisitions with quality issues; and S028/CTXPOP all current declared JSON resources with period gaps/duplicates. None is official use approval or semantic completeness proof.

Tests use `FICTIONAL_OFFLINE_ONLY` fixtures. Independently reproduced eight failures across nine initial review tests, repaired them, then extended to twelve passing tests; S036/CTX165 strict audit has eight additional tests, and metadata ID parser has eighteen tests (including three red-to-green regressions).
