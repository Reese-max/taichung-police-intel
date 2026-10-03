# Gold evaluation data and metric definitions

`v1` is preserved as the original 15-case synthetic seed. The default evaluator uses `v2`, a versioned synthetic-only fixture set. Neither version contains human-reviewed labels or provides real-world accuracy evidence. Historical or public-source cases must remain in a separate dataset and require a named human reviewer before they can be evaluated as gold.

Run the deterministic starter evaluation with:

```sh
python -X utf8 scripts/evaluate-govintel.py --predictions path/to/predictions.jsonl --output report.json
```

The report records a SHA-256 identity for the manifest and cases. Baseline comparison accepts only complete reports for the same dataset identity, content, schema, and fixed expected denominators. A synthetic `NO_REGRESSION` result means only that none of the compared synthetic scores decreased; it is not an accuracy or quality claim.

## Regression comparison and promotion decisions

The evaluator emits a machine-readable `promotion_gate` alongside the baseline comparison. It does not define a model-promotion threshold, and the default CI command does not enforce this gate. Pass `--require-promotion-gate` to make the CLI exit nonzero unless the report says the claim is eligible.

The comparison's `regression_status` is:

- `REGRESSION` if any scorable compared metric worsens. This status takes precedence when another metric is unscorable; inspect both `regressed_metrics` and `unscorable_metrics`.
- `UNSCORABLE` if no regression is found but at least one compared metric is undefined (`null`). Treat this as unresolved, not as a pass.
- `NO_REGRESSION` only if all compared metrics are scorable and none worsens. This is a synthetic-fixture result, not evidence that a model is better.

For the current synthetic-only datasets, `promotion_gate.status` is always `NOT_ELIGIBLE`, including when `regression_status` is `NO_REGRESSION`. A `REGRESSION` or `UNSCORABLE` comparison adds a corresponding blocker; incomplete evaluations are blocked as `INCOMPLETE_EVALUATION`. Reports for other dataset types remain `BLOCKED` or `UNDEFINED` until a promotion policy is configured. No real-world thresholds are configured here.

Baseline comparison rejects incomplete, empty, or incompatible reports. With `--require-promotion-gate`, the CLI emits the blocked gate and exits with code 3 for synthetic, regressed, unscorable, incomplete, invalid, or baseline-free evaluations. Without that flag, a complete prediction file can exit successfully even when its comparison is `REGRESSION` or `UNSCORABLE`; the default CI does not enforce the promotion gate. Neither this synthetic seed nor `NO_REGRESSION` or `improved_metrics` may be used to claim that a new model is better or to justify merging on that basis. Such a claim remains undefined until a separately versioned, human-reviewed holdout and an explicitly approved promotion policy exist.

## Metric definitions

- `event_pair.false_merge_rate` is false same-event predictions divided by all predicted same-event pairs. `missed_merge_rate` is missed true same-event pairs divided by all expected same-event pairs. The report also includes their counts and denominators, plus pair precision/recall/F1.
- `material_change.changed_fields` scores field labels (`start_time`, `location`, `body`, `media`, and other explicit fixture fields) with micro precision/recall/F1 and expected/predicted ID denominators. The binary material-change score remains separate.
- `discovery_official.confirmation_precision` is correct confirmations divided by all predicted confirmations. The fixtures include a matching official record and a date-mismatched record.
- `query_ids` reports micro ID precision/recall/F1 and exact result-set accuracy. `query_ids.ranking` reports exact ordered-list accuracy, top-1 relevance accuracy, and mean reciprocal rank over queries with at least one expected relevant ID.
- `unsupported_claim_rate` is the fraction of claims predicted `SUPPORTED` whose expected support status is not `SUPPORTED`; its denominator is the number of claims predicted `SUPPORTED`.
- `evidence_coverage.recall` is expected evidence IDs matched divided by all expected evidence IDs. `case_coverage` is claims with every expected evidence ID present divided by claims with expected evidence IDs.
- `no_result_false_reassurance.rate` is source-gap queries incorrectly labelled `NO_RESULTS` divided by all expected `SOURCE_GAP` queries. Empty IDs do not make a failed source look like a healthy no-result answer.

Undefined rates are `null` with a zero denominator and make baseline comparison `UNSCORABLE` unless another metric already regressed. Predictions must be JSONL objects keyed by known `case_id`; material-change cases include `material_change` and `fields`, query cases include `ids` and `ranked_ids`, claim cases with evidence labels include `support_status` and `evidence_ids`, and discovery-to-official cases include `confirmed`.
