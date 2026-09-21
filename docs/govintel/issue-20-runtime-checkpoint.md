# Issue #20: checkpoint replay and public-data verification

Status: implemented in PR #26; not merged or production-verified. Scope is the first backbone issue, not completion of #22/#30–#39.

## Changes

- Restore all configured publication-state files from one pinned commit or fail before writing any file. Symlinks and oversized blobs are rejected. The local restore receipt is written last. A workflow-explicit one-time legacy bootstrap may seed only the newly added handoff file from the checked-out schema-compatible baseline; all other missing files still fail closed.
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

35 tests passed: 17 checkpoint/Git/HTTP/CLI tests, 14 outcome/health tests, and 4 publication-bundle tests. Real temporary bare Git repositories exercise stale-writer rejection, missing blobs, idempotence, symlinks, pending replay and main-ref invariance. Loopback HTTP tests exercise matching bytes and HTTP 200 with old bytes. Loopback is test-only and has no CLI switch.

The workflow's final `publication_outcome` job now also writes and retains
`runtime-evidence/publication-health.json`. It binds the durable generation and
state commit, separates collection/canonical-validation/deployment/public-HTTP
outcomes, and leaves unavailable query/MCP stages explicit. This is wiring and
machine-readable failure evidence; it is not a production or natural-schedule
receipt until the workflow is merged and actually runs.

Local checkout is a focused source copy obtained through the connected GitHub reader; container DNS cannot resolve GitHub. This is not a claim of a local full-repository build. Full-repository CI must separately pass on the exact pushed head. Existing regression checks remain enabled.

## Remaining acceptance

- Required PR review/approval and merge under the existing branch rules.
- Real MORNING and EVENING scheduled executions, public HTTPS/data hashes, and actual Pages upload/deploy failure drills.
- Pending checkpoints intentionally block fresh collection until successfully replayed. A permanently invalid checkpoint requires an explicit operator repair; there is no silent reset/delete/skip path.
- Later source/schema changes must version this configured state-file contract; no candidate source is promoted by this patch.

No production write or deployment was performed by the local tests. #20 remains open until real environment evidence is available.
