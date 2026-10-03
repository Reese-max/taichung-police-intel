# Issue #20: checkpoint replay and public-data verification

Status: merged and production-verified on `main@562141e396c693e115b3fce10594c434c401a58e`; the remaining scoped deliverable is the deterministic failure-path test coverage in PR #100. Scope is the first backbone issue, not completion of #22/#30–#39.

## Changes

- Restore all configured publication-state files from one pinned commit or fail before writing any file. Symlinks and oversized blobs are rejected. The local restore receipt is written last. A workflow-explicit one-time legacy bootstrap may seed only newly added schema-compatible state paths (`state/schema-drift-state.json` and `state/v2-handoff-state.json`) from the checked-out baseline; all other missing files still fail closed.
- Persist requires the restored baseline; an intervening writer is not silently adopted. Normal fast-forward push rejects later races. No main write, force push, policy bypass, or code execution from the data branch.
- A data-only `state/publication-checkpoint.json` records generation hashes and PENDING_PUBLICATION. Existing code files inherited when the data branch was created remain untouched; they are never executed.
- A pending bundle is replayed on the next run without recollection or advancing V2 state. An unsuccessful upload/deploy/HTTP probe cannot consume its changes.
- Code-only builds also restore durable data rather than deploying main's older checked-in JSON.
- After deployment, all five public data files must match the exact generation before acknowledging PUBLISHED. The internal V2 state files are not HTTP resources. Probe uses the README's fixed Pages origin/path, rejects redirects and arbitrary URLs, and has bounded time/byte/retry limits.
- Public verification receipts attest data bytes, not all frontend assets, source freshness, or natural scheduled-run success.

## Actual local verification

Python 3.13.5, Git 2.47.3. Command:

```sh
python -m unittest discover -s tests -p 'test_publication_*.py' -v
```

41 tests passed: 19 checkpoint/Git/HTTP/CLI tests, 18 outcome/health tests, and 4 publication-bundle tests. Real temporary bare Git repositories exercise stale-writer rejection, missing blobs, idempotence, symlinks, pending replay and main-ref invariance. Loopback HTTP tests exercise matching bytes and HTTP 200 with old bytes. Loopback is test-only and has no CLI switch.

The workflow's final `publication_outcome` job now also writes and retains
`runtime-evidence/publication-health.json`. It binds the durable generation and
state commit, separates collection/canonical-validation/deployment/public-HTTP
outcomes, and leaves unavailable query/MCP stages explicit. This is wiring and
machine-readable failure evidence; it is not a production or natural-schedule
receipt until the workflow is merged and actually runs.

Local checkout is a focused source copy obtained through the connected GitHub reader; container DNS cannot resolve GitHub. This is not a claim of a local full-repository build. Full-repository CI must separately pass on the exact pushed head. Existing regression checks remain enabled.

## Remaining acceptance

- Required PR review/approval and merge under the existing branch rules for PR #100's test coverage.
- A natural-schedule production run where the Pages **artifact-upload** step itself fails has not occurred; that reporting path is covered only by deterministic tests. The deploy/public-verification failure path has a production receipt (below).
- Pending checkpoints intentionally block fresh collection until successfully replayed. A permanently invalid checkpoint requires an explicit operator repair; there is no silent reset/delete/skip path.
- Later source/schema changes must version this configured state-file contract; no candidate source is promoted by this patch.

## Production evidence (2026-10-03 recheck, `main@562141e`)

- EVENING scheduled successes: runs `36744214186` (2026-09-30, `PUBLIC_DATA_VERIFIED`) and `36896609394` (2026-10-01).
- MORNING scheduled successes: runs `36801047512` (2026-10-01), `36952506907` (2026-10-02), `37085607707` (2026-10-03).
- Real failure drill, run `37033001280` (created 2026-10-02T16:18Z — the `30 10 * * *` UTC cron's evening slot, delayed; its collection ran 2026-10-03T00:18+08:00 and produced `CR-DEMO-20261003-EVENING-SCHEDULE`): build succeeded, the deploy job's public-byte/hash acknowledgement step failed (`public_verify=failure`), and the cross-job `publication_outcome` finalizer failed the run with `PUBLICATION_NOT_CONFIRMED`, a per-phase outcome table, and the retained evidence-artifact link. No success or zero-new-events claim was emitted.
- Retry safety: the failed run left checkpoint `e5114e63` as `PENDING_PUBLICATION`; the next scheduled run `37085607707` (created 2026-10-03T01:18Z — the `30 22 * * *` UTC cron's morning slot) restored `pending_recovery=true`, replayed the exact pending bundle without recollection or diff advancement — so the served `collection_run_id` keeps the original `CR-DEMO-20261003-EVENING-SCHEDULE` — deployed, and acknowledged `PUBLISHED` after public-byte verification at `2026-10-03T01:39:24Z`.
- Anonymous HTTPS check (2026-10-03): the five public data files served from `https://reese-max.github.io/taichung-police-intel/` match the acknowledged checkpoint's `verified_files` SHA-256 set byte-for-byte; the feed carries `collection_run_id=CR-DEMO-20261003-EVENING-SCHEDULE` and `generated_at=2026-10-03T00:18:54+08:00` from the replayed collection.
