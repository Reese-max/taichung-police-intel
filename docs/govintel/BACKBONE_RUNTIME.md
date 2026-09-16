# GovIntel backbone runtime evidence

This harness verifies the existing implementation PRs for #20, #22, #30, #31, #33, #24, #32 and #35. It does not replace or merge those PRs, bypass their reviews, deploy the site or close the Issues.

## Reproduce

```sh
python -m pip install -r requirements.txt
python scripts/verify-backbone-runtime.py --prepare
```

Run from a checkout of this test PR with read access to the same public repository. The manifest pins every component to a full commit SHA. Preparation only fetches commits and creates detached local worktrees. It never pushes branches, merges PRs or changes repository settings.

The workflow has contents:read, no retained GitHub credentials, no deployment, no production state writes and no cron. Existing required CI remains unchanged. The extra runtime job preserves logs and machine-readable reports even on failure.

## What is executed

1. Actual Python test suites in eight exact implementation snapshots, rejecting zero-test runs and any failing exit code.
2. Real checked-in publication metadata → query index → bounded query, preserving source gaps and without declaring deployed freshness.
3. A clearly synthetic integration scenario: registry resolves agency/district IDs → three official-labelled fixture documents fuse → one time change produces conflict without changing event ID → all fixture sources converge → typed evidence gate supports time but rejects unsupported cause, stale/current misuse, cross-generation catalog and embedded caller evidence.
4. The existing evaluation module scores six literal contract expectations against the actual fusion/gate outputs. No perfect_predictions helper supplies answers; these are synthetic engineering assertions, not human-labelled or production accuracy.
5. Health consumes the actual query snapshot and leaves missing deploy/HTTP receipts UNKNOWN; an injected query failure cannot rewrite the publication state.

## Outputs

`runtime-evidence/backbone/` contains manifest, per-suite log/exit/count/hash, real-snapshot-query, synthetic integration documents/versions/receipts/predictions, health state and an overall runtime report.

A PASS means these pinned tests and synthetic interfaces executed successfully. It does not mean the eight PRs have been merged, a real data pipeline is fully wired, a browser or MCP server is running, or a field user has verified usefulness. Free-form prose is not validated by the typed evidence gate.

## Existing real-environment blockers

- #20: required reviewer approval/merge plus real MORNING/EVENING runs and formal Pages HTTP/data-hash checks remain necessary.
- #22: one real observation at 2026-09-17 03:35 +08:00 had S-001 ConnectionError, S-019 63 parsed rows and S-032 10 parsed rows with PARTIAL coverage. Run 35141396165 correctly failed; this harness does not replace that result with a fixture success.
- #22 production wiring/pagination/candidate observation period; #24 persistent event workflow/manual merge/split; #33 human-labelled holdout; #29/#15 query consumers; and complete #35 live receipt wiring remain separate acceptance criteria.

All implementation PRs and their normal review rules remain authoritative. Update a pin only after rechecking the relevant code and repeat the same scenarios; never relabel an older result as validation of a newer head.
