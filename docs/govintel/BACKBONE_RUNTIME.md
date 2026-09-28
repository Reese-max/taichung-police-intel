# Historical pinned GovIntel backbone replay

This harness replays eight immutable implementation commits selected on 2026-09-17 for #20, #22, #30, #31, #33, #24, #32 and #35. It does not verify current main, replace the single-checkout integration test, merge any PR, deploy the site, or close those issues. Run `npm run verify:current-checkout` separately for current code; its receipt labels this historical replay as a separate lane.

## Reproduce

```sh
python -m pip install -r requirements.txt
python -X utf8 scripts/verify-backbone-runtime.py --prepare
```

Run from a checkout with read access to the same public repository. The manifest pins every component to a full commit SHA. Preparation only fetches commits and creates detached local worktrees under the ignored `.runtime-checkouts/` directory. It never pushes branches, merges PRs or changes repository settings.

The optional workflow runs on relevant PR changes or manual dispatch. It has `contents: read`, no retained GitHub credentials, no deployment, no production state writes and no cron. Existing required CI remains unchanged. Its separate job preserves logs and machine-readable reports even on failure.

The manifest must contain exactly the eight named components and their declared suite patterns. Missing components or suites fail before a PASS is possible. Reused pinned worktrees must match their SHA and have no tracked changes or untracked files before test discovery. Each invocation writes to a fresh `runtime-evidence/backbone/<run-id>/` directory, so an earlier successful trace cannot be mixed with a later failed report. The workflow also runs `tests/test_backbone_runtime.py` for these invariants.

## What is executed

1. Actual Python test suites in eight exact implementation snapshots, rejecting zero-test runs and any failing exit code.
2. Real checked-in publication metadata → query index → bounded query, preserving source gaps and without declaring deployed freshness.
3. A clearly synthetic integration scenario: registry resolves agency/district IDs → three official-labelled fixture documents fuse → one time change produces conflict without changing event ID → all fixture sources converge → typed evidence gate supports time but rejects unsupported cause, stale/current misuse, cross-generation catalog and embedded caller evidence.
4. The existing evaluation module scores six literal contract expectations against the actual fusion/gate outputs. No perfect_predictions helper supplies answers; these are synthetic engineering assertions, not human-labelled or production accuracy.
5. Health consumes the actual query snapshot and leaves missing deploy/HTTP receipts UNKNOWN; an injected query failure cannot rewrite the publication state.

## Outputs

Each `runtime-evidence/backbone/<run-id>/` contains manifest, per-suite log/exit/count/hash, real-snapshot-query, synthetic integration documents/versions/receipts/predictions, health state and an overall runtime report.

A PASS means only these pinned tests and synthetic interfaces executed successfully. It does not attest the code currently in main, a real data pipeline, a browser or MCP server, public deployment, or field usefulness. Free-form prose is not validated by the typed evidence gate.

## Historical limits and current gates

- The pinned #20 branch snapshot is historical; current protected-main publication and real MORNING/EVENING Pages HTTP/data-hash evidence must be checked independently.
- #22: one real observation at 2026-09-17 03:35 +08:00 had S-001 ConnectionError, S-019 63 parsed rows and S-032 10 parsed rows with PARTIAL coverage. Run 35141396165 correctly failed; this harness does not replace that result with a fixture success.
- #22 production wiring/pagination/candidate observation period; #24 persistent event workflow/manual merge/split; #33 human-labelled holdout; #29/#15 query consumers; and complete #35 live receipt wiring remain separate acceptance criteria.

Current-checkout and production gates remain authoritative. Update a pin only after rechecking the relevant code and repeating the same scenarios; never relabel an older result as validation of a newer head.
