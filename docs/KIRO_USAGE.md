# Kiro Usage Evidence

## Current status

**Authenticated and verified on 2026-08-23.** Kiro CLI `kiro-cli-chat 2.17.0` was authenticated with Builder ID. The account email is intentionally redacted from this public evidence.

| Field | Evidence |
|---|---|
| Implementation session | `sess_3168ba8f-9bca-4967-9eb7-5640b6a58f31` — `Five-minute homepage English path` |
| Architecture-review session | `sess_f8e0bb4d-ae6f-4c09-82c8-b361346821e3` — `Auditable Competition Evidence Report` |
| Supporting review session | `sess_fb15f607-371b-453d-9f9f-8c1e1119b85f` — `Patch spec docs for static topology` |
| Session type | Local Kiro CLI V3 Spec session |
| Invocation | `kiro-cli chat --v3 --mode spec` |
| Repository role | Bounded architecture review followed by bounded product implementation |
| Product files changed by Kiro | `apps/web/app/page.js`, `apps/web/lib/homepage-data.js`, `apps/web/tests/homepage.test.mjs` |

## Meaningful use

Kiro reviewed all 16 competition-control artifacts:

- four files under `.kiro/steering/`;
- nine `requirements.md`, `design.md`, and `tasks.md` files under the three `.kiro/specs/` directories;
- three executable JSON definitions under `.kiro/hooks/`.

The primary review found a real specification drift: the source-ingestion Spec still treated Render and PostgreSQL as competition production dependencies, while the implemented competition path was already a static GitHub Pages design. The supporting review inspected the repository workflow, Next.js static routes, source-status contract, and roadmap and proposed the smaller deployment topology.

Human review corrected two assumptions before accepting that proposal:

1. The checked-in status JSON stores the latest run; MORNING and EVENING acceptance is proven by two distinct workflow runs and Git history, not by claiming both slots exist in one JSON file.
2. The competition JSON stores official URLs, manifests, counts, gaps, and last-known-good metadata. Replayable raw response bytes remain a post-competition PostgreSQL concern.

That review drove a minimal alignment of these existing artifacts:

- `.kiro/steering/tech.md`;
- `.kiro/specs/source-ingestion-and-provenance/requirements.md`;
- `.kiro/specs/source-ingestion-and-provenance/design.md`;
- `.kiro/specs/source-ingestion-and-provenance/tasks.md`.

The deduplication/versioning task and the independent producer/verifier task deliberately remain unchecked. Enabling public Pages and proving two scheduled runs also remains an external acceptance step.

The implementation session then read the homepage Steering and the complete `five-minute-homepage` Spec. Its first pass only extended the static English guide. Human review rejected that as insufficient because it did not create a real English operating path. In the resumed session Kiro implemented:

- an accessible Traditional Chinese / English toggle and document language update;
- a shared evidence manifest so both language paths use `S-010` and the same official URLs;
- translated primary-journey, source-monitor, evidence-drawer, playback, search, and limitation labels;
- a pure two-card limiter for central-policy items;
- `apps/web/tests/homepage.test.mjs` as an executable acceptance test.

Kiro deliberately left `tasks.md` unchecked for human acceptance. It also reported that no Hook fired during this implementation session. The checked-in Hook definitions remain part of the design, but this document does not present them as observed lifecycle evidence.

## Prompts retained

Initial bounded prompt:

```text
Create auditable Kiro competition evidence for this repository. Work in read-only review mode: do not edit, create, delete, or move any file. Read every file under .kiro/steering, every requirements.md, design.md, and tasks.md under the three existing .kiro/specs, and all three JSON files under .kiro/hooks. Then run npm run check:specs. Return the artifacts read, concrete Steering decisions, Spec requirements and acceptance commands, Hook triggers and commands, the command result, and remaining competition blockers.
```

Final verification prompt in the same session:

```text
Re-read the updated .kiro/steering/tech.md and the three files under .kiro/specs/source-ingestion-and-provenance. Verify the GitHub Actions -> versioned source-status.json -> Next.js static export -> GitHub Pages topology, PostgreSQL as post-competition, and the unfinished deduplication and producer/verifier tasks as unchecked. Then run exactly npm run check:specs and report its exit code and exact VERIFY line. Do not edit files or run any other command.
```

Bounded implementation prompt:

```text
Implement one bounded competition task using Kiro V3 Spec mode. Read .kiro/steering and the five-minute-homepage requirements, design, and tasks. Complete the English primary path while preserving Traditional Chinese, shared evidence IDs and official URLs, source health/gaps/LKG, drawer controls, limitations, and a hard cap of two central-policy cards. Add apps/web/tests/homepage.test.mjs. Edit only the allowed homepage files, use existing dependencies, do not use network, git, commit, push, deploy, or external writes, and run node --test apps/web/tests/homepage.test.mjs, npm run check:specs, and npm run check with one-time approvals only. Human review remains authoritative.
```

## Executable results

The initial non-interactive attempt could read the repository but could not cross Kiro's command-approval boundary. The same session was resumed interactively. A one-time approval was granted only for the acceptance command; permanent trust was not enabled. Kiro first used `cd /d`, which is invalid PowerShell syntax, received exit code `1`, corrected it to `Set-Location`, and reran the same acceptance command.

```text
Command: npm run check:specs
Exit code: 0
VERIFY_OK mode=specs required=39 specs=3 secrets=0
```

The implementation session reported:

```text
node --test apps/web/tests/homepage.test.mjs
51/51 pass, exit 0

npm run check:specs
VERIFY_OK mode=specs required=39 specs=3 secrets=0

npm run check
VERIFY_OK mode=full required=39 specs=3 secrets=0
```

Human browser review then found one real integration defect outside Kiro's pure-data tests: if evidence JSON loaded before the drawer existed, the HLS initialization effect returned on a null `videoRef` and never reran. Human review fixed the effect at the shared initialization point, made playback status language-independent, added English translations for the five competition source names, and styled the toggle. Independent acceptance verified:

- official HLS playlist HTTP 200 and media-segment HTTP 206;
- player `readyState` and localized ready status in both languages;
- the same `S-010` official URL and Chinese transcript boundary;
- mobile, tablet, and desktop layouts with no console errors;
- homepage tasks checked only after review.

Final independent commands:

```text
node --test apps/web/tests/homepage.test.mjs
53/53 pass, exit 0

npm run check:specs
VERIFY_OK mode=specs required=39 specs=3 secrets=0

npm run check
VERIFY_OK mode=full required=39 specs=3 secrets=0
```

This is evidence of authenticated, bounded Kiro use with retained implementation and executable gates. It is not evidence of a public deployment or an observed Hook lifecycle run. The demo video should show the implementation session ID, checked tasks, the final `VERIFY_OK` line, and this limitation.



## Kiro V3 roadmap re-execution session (2026-08-23)

**Session ID:** `sess_6f4c60c3-1bae-4ffb-be76-0743fe4736da`
**CLI:** Kiro CLI 2.19.1
**Model:** `claude-sonnet-4.5`
**Mode:** Spec; effort: `high`

**Phase 1:** Read-only audit of Gates 0-7. File reads succeeded. In non-interactive V3, shell commands and file writes were denied because interactive tool approval was unavailable; those denied actions did not execute.

**Phase 2:** Resumed the same session interactively. Only the five approved local compliance files were edited through explicit `fs_write` approvals: `.gitignore` (Chinese document exclusions), `README.md` (link corrections), `docs/SUBMISSION_CHECKLIST.md` (credentials statement and English boundary), `SUBMISSION.md` (local-state qualifications), `docs/KIRO_USAGE.md` (this session record).

**Hook truth:** No Hook lifecycle run was observed; this session must not be represented as a Hook firing.

**External actions:** None. No commit, push, deployment, video upload, or form submission occurred.

**Scope truth:** Gates 0-7 were reviewed; post-competition items remained `POST_COMPETITION` and were not implemented.


## Kiro Auto full local rerun (2026-08-23)

**Session ID:** `sess_70b43b5e-b8dc-494c-937f-45755c91d5d1`
**CLI:** Kiro CLI `2.19.1`, V3
**Requested model:** `auto` — Auto did not disclose the underlying routed model.

**Scope:** Independently re-read the competition rules and checklist, both local roadmap and implementation-plan files, all Steering, Spec, and Hook artifacts. Independently executed the complete local gates and the publication dry-run rather than accepting prior Codex results.

**Shell trust:** The first shell attempts were denied because the wrong non-interactive trust tool name was supplied. Later `run_command` was explicitly trusted and subsequent commands proceeded normally. This is an operational note, not a project failure.

**Rate-limit event:** A service-side HTTP 429 `CREDIT_CONSUMPTION_RATE_EXCEEDED` with a 300,000 ms retry interval was received mid-session. After the cooldown elapsed the same session resumed without data loss. This is an operational note, not a project failure.

**Acceptance evidence:**

```text
npm run check
Exit code: 0
75 passed, 1 skipped (PostgreSQL test skipped — TEST_DATABASE_URL absent)
Static build succeeded
VERIFY_OK mode=full required=39 specs=3 secrets=0
```

**Publication dry-run evidence:**

```text
git status: no HEAD, zero remotes
git add --dry-run: exit 0
Candidate files: 74
Total size: 3,711,779 bytes
Largest file: 2,693,320 bytes
Distinct PENDING labels: 5
Internal planning files intentionally ignored: 5
Root .kiro/ intentionally included as required competition evidence
```

**Hook observation:** No Hook firing was observed during this session.

**External actions:** None. No commit, push, repo creation, deployment, video upload, form submission, network action, or other external write occurred.

**Remaining external gate:** Requires entrant/team name, contribution statement, explicit public repository/push authorization, and GitHub Pages authorization. Video and submission form follow after the live URL exists.

**Final marker:** `KIRO_AUTO_FULL_LOCAL_RERUN_ACCEPTED`
