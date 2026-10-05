# Issue #47 — current-checkout runtime receipt

## Executable candidate lane

The current checkout now has one bounded candidate verifier:

```bash
python -X utf8 scripts/verify-current-checkout.py
```

Full verification runs `npm ci` for both the repository and `apps/web` before
the build, even when either `node_modules` directory already exists. The
receipt's dependency-lock hash therefore corresponds to freshly installed
dependencies. A failed install fails the candidate; its log stays in the
evidence directory. Use `--chrome-path` to select an installed Chromium-based
browser when Playwright's default executable is unavailable.

The verifier imports query store, source policy, health, and publication verifiers
from this checkout, builds the static export, serves it over loopback, and writes
the actual `code_sha`, dependency-lock hash, canonical artifact hashes, query
generation, policy hash, capabilities, logs, screenshots, and request trace into
`runtime-evidence/current-checkout/<timestamp>/receipt.json`.

The latest local replay passed `24/24` checks, including:

- static page and canonical bytes over HTTP;
- query generation/policy/hash identity and bounded query results;
- the local read-only HTTP and STDIO MCP tool lists, full lifecycle handshake, and hash-bound `get_publication_receipt` projection;
- foreign generation rejection, source-unavailable distinction, and capability unavailability;
- mixed-generation cutover rejection while the previous index remains readable;
- query service down (`503`) while static publication remains readable;
- real Chrome input, stale-status and publication-hash display, refresh generation stability, filtered archive results, and an official HTML link opening in a new browser page;
- a temporary sabotaged checkout failing its own module-binding check.

Loopback HTTP proves only the candidate query/static lane. It is recorded as a
query-index success; formal `deployment` and `public_http_verification` stages
remain `UNKNOWN` until a real deployment and anonymous public hash receipt exist.

The companion Python contract suite has `28/28` tests. The verifier is local-only,
uses a preserved checked-in snapshot, and does not write canonical or production
state.

## 2026-09-22 replay after workflow guard

Commit `9a6ea6f65ddeb92b0470503148bd52536f044e07` passed the full verifier with
candidate `rc-9a6ea6f65dde-20260921T232927Z` and `24/24` checks. The receipt is
`runtime-evidence/current-checkout/20260921T232927Z/receipt.json`; it records
`code_sha` equal to that commit, browser `13/13`, STDIO/HTTP MCP lifecycle,
foreign-generation rejection, query-down static fallback, and sabotage
detection. Its `production_verified` field remains `false` by design.

## 2026-09-22 final local replay

After the source-policy receipt and Next workspace-root fixes, commit
`40e28aa42b40e3f1a0cc1c7de9c0318aeac7fbf4` passed the full verifier with
candidate `rc-40e28aa42b40-20260921T234108Z` and `24/24` checks. The receipt is
`runtime-evidence/current-checkout/20260921T234108Z/receipt.json`; the local
candidate remains loopback-only and `production_verified=false`.

## 2026-09-22 replay after timezone-bound event filters

Commit `06d62e2014e17522c011fa3569a1ab30cbf351bf` passed the full verifier with
candidate `rc-06d62e2014e1-20260922T021923Z` and `24/24` checks. The receipt is
`runtime-evidence/current-checkout/20260922T021923Z/receipt.json`; it records
browser `13/13`, STDIO/HTTP MCP lifecycle, foreign-generation rejection,
query-down static fallback, and sabotage detection. Its `production_verified`
field remains `false` by design.

## Evidence boundary

| Dimension | State |
|---|---|
| `IMPLEMENTED` | YES — one build/start/verify entry, receipt, static fallback, browser lane and sabotage lane |
| `CORE_TESTED` | YES — 24/24 current-checkout tests and full project gate |
| `INTEGRATED` | YES — local Web → same-origin Gateway → Query Store → official locator replay |
| `LIVE_SOURCE_TESTED` | NO — checked-in snapshot only for this candidate replay |
| `DEPLOYMENT_VERIFIED` | NO — no Pages/public HTTPS/hash receipt |
| `USER_VALIDATED` | NO — no task-based human evaluation |

No merge, deployment, production write, or issue closure was performed. #20 still
requires normal review/merge, scheduled MORNING/EVENING runs, and anonymous
public hash verification.

## Current source-governance boundary (2026-10-05)

The current approved schema-1 snapshot has no reviewed governance, so the current
formal query projection contains zero items with admission `UNKNOWN`. Zero rows
do not authorize a bounded no-match answer, and current brief/answer requests
fail `RIGHTS_BLOCKED`; read-only provenance and five source-health rows remain
available. The candidate manifest and verifier receipt expose the exact admission.
A successful check of this refusal does not claim live rights or production approval.

Positive runtime tests compile schema 2 with the actual compiler in a separate
temporary root copied from the tested checkout. Only that copy receives explicitly
fictional metadata permission, with the exact #39 whitelist, source-bound URLs and
matching feed/status/brief governance hashes. They retain the original nonempty
query, stale-status, mixed-generation, static-byte, MCP, sabotage, browser-binding
and no-false-zero assertions. No clock change promotes an unreviewed source.
The historical #118/PR #46 pinned replay and its recorded receipts remain separate.
Dirty tracked worktrees still fail the candidate receipt even when individual
checks pass. Focused contract success does not replace the full build/browser
lanes, deployment verification or a real source-rights review.
