# GovIntel AI｜跨機關公共事件整合、異動辨識與交班支援平台

The former competition prototype name was **Taichung Police Public Intelligence**; this repository now documents the current GovIntel AI product.

An evidence-first public-information monitor that helps police policy staff prepare for council questions in five minutes.

The demo compresses official Taichung council and government sources into one workflow: identify a priority issue, inspect source health and intelligence gaps, then jump to the exact official video timestamp. Chinese is the end-user language; the homepage toggle provides the complete English judge path.

## Current GovIntel state (2026-09-21)

| Area | State | Evidence boundary |
|---|---|---|
| Five-source council publication baseline | `PRODUCTION_ACTIVE` in the repository path | Checked-in source/status contracts pass; current public deployment is not verified here |
| Shared source policy, query coverage, official document replay, Dashboard discovery adapter | `IMPLEMENTED_NOT_PRODUCTION` | Local deterministic tests and receipts; no scheduled activation or public deployment claim |
| S-001/S-019/S-032/S-033 source expansion | `CANDIDATE_CANARY` | List-first adapters and fixtures exist; live seven-day promotion evidence remains open under #14/#22 |
| PublicEvent fusion and NPA source matrix | `IMPLEMENTED_NOT_PRODUCTION` | Conservative core and offline fixtures; no live collector/UI write path |
| Twinkle/public API overlay and full cross-agency real-time coverage | `DESIGN_ONLY` | Strategy and candidate metadata only; no automatic promotion |
| Scheduled publication merge, natural MORNING/EVENING proof, current anonymous hash check | `BLOCKED` | #20 still needs normal review/merge and real production evidence |
| Human task evaluation, adoption, award, and official submission receipt | `NOT_RUN` / `UNVERIFIED` | No scores or institutional adoption claims are populated |

The current judge entry is [docs/govintel/competition-2026/README.md](./docs/govintel/competition-2026/README.md). Historical Kiro/prototype receipts remain below and are labelled as historical.

## Judge path

1. Read the priority brief on the homepage.
2. Check the five official sources for health, freshness, gaps, and last-known-good.
3. Open the evidence drawer.
4. Click a transcript segment or word to seek the official council video.
5. Review `.kiro/` and [Kiro usage evidence](./docs/KIRO_USAGE.md).

## Historical prototype receipts (not current deployment evidence)

| Deliverable | Current state |
|---|---|
| Working application | Local static production build passes |
| Public demo | [https://reese-max.github.io/taichung-police-intel](https://reese-max.github.io/taichung-police-intel) has a historical anonymous HTTP 200 receipt from 2026-08-23; current public bytes are not verified by this checkout |
| Demo video | [2:43 English-captioned MP4](https://reese-max.github.io/taichung-police-intel/demo-video.mp4) — verified anonymously in Chrome 2026-08-24 |
| Twice-daily updates | GitHub Actions schedules 06:30 and 18:30 Asia/Taipei; a current successful schedule pair is not proven here |
| Source observability | Five official-source adapters emit health, completeness, gaps, SHA-256, and last-known-good; the checked-in snapshot remains subject to its recorded freshness |
| Evidence navigation | 86 transcript segments and 1,036 word timestamps seek the official HLS video |
| English judge path | Complete homepage, source-monitor, evidence-drawer, control, limitation, and official-source translation path passes browser QA |
| Kiro assets | Four Steering files, three Specs, and three executable Hooks are checked in |
| Kiro live-session proof | Authenticated V3 Spec sessions reviewed the architecture and implemented the English homepage path with executable acceptance tests |
| Kiro model and usage | The three retained current-workspace sessions used Auto (`qdev::auto` in local records) and consumed 13.006002 credits; Auto did not disclose its routed base model |
| Submission package | English README, script, checklist, submission draft, and captioned video are present; entrant details and form receipt remain pending |

## Problem and users

Police policy and council-liaison staff must monitor scattered official pages, proposals, reports, meeting records, and videos. Finding what changed can take one to two hours, and a summary without a source locator is difficult to trust under questioning.

This competition version focuses on one real task: preparing for a council question. It uses public information only and excludes internal duty data, emergency dispatch, 110 calls, case-level criminal data, personal data, and operational command functions.

## What works

- A focused council-preparation brief for a police policy user.
- Five live official-source adapters with isolated failure handling.
- Source health kept separate from date-window completeness.
- Intelligence-gap reasons instead of silently turning collection failure into zero results.
- Last-known-good retained when a later source fetch fails.
- Official URLs, collection time, data-as-of time, raw snapshot count, and SHA-256 manifest.
- An evidence drawer with official HLS playback, searchable transcript segments, and word-level timestamp navigation.
- A Traditional Chinese / English toggle covering the primary journey while preserving the official Chinese transcript as labelled navigation text.
- A static-export deployment path that needs no paid database or application server.

## Architecture

```text
Official Taichung council and government sources
                |
                v
      Python collector (source-isolated)
                |
                v
health + completeness + gaps + SHA-256 + last-known-good
                |
                v
 apps/web/public/data/source-status.json
                |
                v
   Next.js static export -> GitHub Pages

GitHub Actions: 06:30 and 18:30 Asia/Taipei
```

The repository also contains PostgreSQL migrations for the post-competition durable-history path. The public competition demo deliberately does not require that infrastructure.

## Setup

Requirements:

- Node.js 20 or newer
- Python 3.11 or newer
- Git only for the scheduled evidence commit
- Kiro CLI V3 only for reproducing the Kiro workflow

Install dependencies:

```bash
npm ci --prefix apps/web
python -m pip install -r requirements.txt
```

No API key, login, database, or paid service is required to run the checked-in demo.

## Run

Development server:

```bash
npm --prefix apps/web run dev
```

Open `http://localhost:3000`.

Refresh the public-source status and build the same static artifact used by GitHub Pages:

```bash
python online_collect.py --slot evening --demo-output apps/web/public/data/source-status.json --trigger manual
npm --prefix apps/web run build
python -m http.server 8000 --directory apps/web/out
```

Open `http://localhost:8000`. The live refresh performs read-only requests to the listed official public sources.

Read-only Query Gateway (Slice 1):

```bash
python scripts/query-gateway.py --port 8788 --allow-origin http://localhost:3000
```

Set `NEXT_PUBLIC_QUERY_GATEWAY_URL=http://127.0.0.1:8788/query` when starting the Web app to enable **Ask GovIntel**. The Gateway and MCP adapter share the same four typed operations: `search_evidence`, `get_current_brief`, `get_source_health`, and `validate_answer`. `validate_answer` accepts only structured claims, builds its evidence catalog from the canonical snapshot, and returns the shared gate receipt plus controlled final text; callers cannot provide evidence, freshness, or trust fields. It reads only the checked-in canonical snapshot, rejects arbitrary URL/SQL/path arguments, applies a process-local request cap, and reports stale/partial/unknown states instead of converting them to zero events. `search_events`, `get_event`, version comparison, and statistics remain explicitly unavailable until their canonical stores are ready.

Every query response also carries the catalog-derived `query_coverage` projection: policy version/hash, supported capability, required and covered sources, collection completeness, missing or stale sources, coverage limitations, and whether a bounded no-match statement is allowed. A healthy index is not treated as complete coverage of the requested world.

Current-checkout integration receipt:

```bash
npm ci
npm run verify:current-checkout
```

This builds the static site, starts an ephemeral loopback server, records the actual code SHA, lockfile hash, publication generation/hash and source-policy binding, and verifies HTTP/static fallback, Web/MCP parity, browser interaction, mixed-generation rejection, and sabotage detection. It writes a machine-readable receipt under `runtime-evidence/current-checkout/`. `--mode core` omits the site/browser lane. It is a candidate-version receipt, not production deployment or public-reachability evidence.

Rights/retention defaults are compiled from [retention-rights-policy.v1.json](./docs/govintel/retention-rights-policy.v1.json). Rights remain `UNKNOWN` until reviewed; outward Query Gateway data is metadata/link-only and never a legal permission or full-text archive.

Local-first handoff state:

```bash
python scripts/handoff-state.py watch --identity S-009:FEED-S-009-example
python scripts/handoff-state.py confirm
python scripts/handoff-state.py export --format markdown --output output/handoff.md
python scripts/handoff-state.py self-check
```

The command uses the durable `state/v2-handoff-state.json`, makes repeated watch adds idempotent, preserves confirmed handoff versions, and reopens only affected watches as `NEEDS_REVIEW` when a source version changes. The static Web page is read-only; write operations currently use this local CLI and never store private notes or operational police fields.

The saved [system-health.json](./apps/web/public/data/system-health.json) exposes the publication, query, and discovery lanes plus stage-level outcomes. `STALE` and `UNKNOWN` are intentional evidence states; they are not deployment or public-reachability claims.

Source contract drift is fail-closed. `scripts/schema_drift.py` covers the three HTML candidate lists, council JSON APIs, and data.gov.tw JSON/CSV resources. A missing required field, changed type, failed HTML identity, missing pagination marker, or HTTP error becomes a drift receipt and preserves the last-known-good fingerprint; additive fields are recorded as compatible. The receipt is visible in the system-health discovery lane and Review Inbox. Running it without an observed input intentionally produces `UNKNOWN`, not a fabricated healthy result.

Schema replay uses the small registry in `scripts/migration_replay.py` (`1→2→3`). `dry-run` emits affected/error counts and hashes; `apply` writes only after all objects pass and saves a migration receipt; `replay` reuses `intel_v2` semantics so deterministic IDs and `FIRST_SEEN` wording remain stable. Watch/handoff inputs are copied unchanged and hash-bound. Query Store remains rebuildable from a pinned canonical generation with `python scripts/query-store.py build --feed ... --status ... --brief ...`.

Review Inbox uses `intel_v2/review.py` and `scripts/review-inbox.py` for deterministic fingerprints, deduplication, claim/decision audit, source-version reopening, and bounded homepage projection. It accepts schema-drift candidates without auto-resolving missing or failed sources; the first version is local-first and does not add RBAC or notifications.

The bounded PublicEvent core is runnable with `python scripts/public-event-fusion.py --self-check`. It only fuses explicitly official normalized documents, keeps document versions separate, carries forward partial/LKG events, and requires a confirmed event plus exact geography/period/source before adding `BACKGROUND_ONLY` context. It is not yet wired to live collectors or a write-capable public UI.

The NPA source matrix is maintained in [npa-source-inventory.v1.json](./docs/govintel/npa-source-inventory.v1.json), with Batch 1 fixture adapters and explicit `PRIMARY_EVENT` / `PRIMARY_REFERENCE` / `ENRICHMENT` / `EXCLUDE_OR_AGGREGATE_ONLY` roles. Run `python scripts/npa-source-inventory.py --self-check` to verify the inventory. Candidate sources are not production or deployment evidence; personal case-level sources remain blocked from the public canonical feed. Details are in [issue-28-npa-source-matrix.md](./docs/govintel/issue-28-npa-source-matrix.md).

Correction feedback is local-first and review-gated in [issue-37-feedback-loop.md](./docs/govintel/issue-37-feedback-loop.md). `python scripts/feedback.py self-check` covers all nine feedback reasons, dedupe, trace hashes, privacy boundaries, accepted regression links, and the rule that feedback never directly changes production truth.

The catalog-derived source policy has a cross-consumer receipt: `python scripts/verify-source-policy-integration.py --self-check` checks collector, publication, Query Store, Health and Web bindings, approved candidate promotion, old replay, mixed-policy rejection, and bounded coverage gaps. It does not claim live source coverage or deployment.

Official document conversion is bounded by `intel_v2/located_facts.py` and `scripts/located-facts.py`: approved catalog origin → immutable raw/text hashes → HTML text-range or JSON Pointer locator → `FACT_CANDIDATE` / `NEEDS_REVIEW` fact and evidence projections. A locator/hash mismatch fails closed; the adapter does not promote candidates to verified truth or infer missing dates.
The live HTML/JSON replay receipt is [official-document-receipt.v1.json](./docs/govintel/official-document-receipt.v1.json); it records hashes and review status, not deployment or human approval.
The read-only Taiwan Intel Dashboard discovery consumer is replayable with `python scripts/discovery-adapter.py self-check`; media remains unverified until a server-controlled official match, and it never writes canonical events.

## Public deployment

`.github/workflows/pages.yml` uses GitHub Pages and GitHub Actions:

- a push to `main` builds and deploys the checked-in snapshot;
- `30 22 * * *` UTC refreshes the morning slot at 06:30 Asia/Taipei;
- `30 10 * * *` UTC refreshes the evening slot at 18:30 Asia/Taipei;
- each scheduled run commits only `apps/web/public/data/source-status.json`, then deploys the static export;
- manual dispatch can refresh either slot.

After the repository is public, enable Pages with **Source: GitHub Actions**. The deployed demo and repository URLs are recorded in [SUBMISSION.md](./SUBMISSION.md). A workflow file is not deployment evidence; acceptance requires an anonymous HTTPS check.

Repository: `https://github.com/Reese-max/taichung-police-intel`
Demo: `https://reese-max.github.io/taichung-police-intel` (historical HTTP 200 receipt from 2026-08-23; initial run 32631305048 conclusion=success). This branch does not contain a current anonymous version/hash receipt, so the workflow configuration is not deployment evidence.

## Verification

```bash
# Repository shape, Kiro artifacts, workflow, demo-state contract, and secret scan
npm run check:gate0

# Kiro Spec completeness
npm run check:specs

# Web tests, Python contracts, collector self-checks, and migration self-check
npm test

# Full gate including the production static build
npm run check
```

```bash
python scripts/schema_drift.py --self-check
```

Expected final line:

```text
VERIFY_OK mode=full ... secrets=0
```

The static artifact must also contain `out/index.html`, `out/api/health.json`, and `out/api/status.json`.

## Kiro workflow

- `.kiro/steering/` defines product, technology, repository structure, and evidence/safety boundaries.
- `.kiro/specs/` contains requirements, design, tasks, failure states, retry limits, and executable acceptance commands for three vertical slices.
- `.kiro/hooks/` routes save, Spec-ready, and task-finish events to the checked-in deterministic verifier.
- The verifier deliberately fails if required Kiro artifacts or acceptance contracts are missing.

Authenticated Kiro V3 sessions first reviewed all 16 Steering, Spec, and Hook artifacts, then implemented the English primary path, the two-card cap, and the homepage acceptance test. Entrant-directed browser review rejected the first static-guide-only attempt, found and fixed a drawer initialization defect in the accepted implementation, and reran the full gate. Session IDs, prompts, corrections, Hook truth, model disclosure, credits, and command output are recorded in [docs/KIRO_USAGE.md](./docs/KIRO_USAGE.md).

## Evidence and safety rules

- Official source content is authoritative; derived transcript text is navigation only.
- A collection failure is never presented as zero matching items.
- `source_health` and `window_completeness` remain separate.
- `FAILED` and `NOT_RUN` cannot overwrite last-known-good.
- Every displayed source links to an HTTPS official page or endpoint.
- Public aggregates are allowed; personal and operational police data are out of scope.
- Missing post-meeting evidence remains an explicit gap, not an AI inference.
- Answer drafts pass `apps/web/lib/answer-evidence-gate.js` before release: every factual claim needs exact official evidence (locator + document version), conflicting official sources surface as `CONFLICT` instead of a merged answer, stale sources cannot back current wording, and media-derived records never verify a claim. The shared gate emits one receipt with the publication hash and validator version for both Web Chat and MCP; the read-only `validate_answer` route binds that receipt to the server-controlled catalog and emits no free-text fallback.

## Historical Kiro competition package (2026-08)

The following files describe the earlier Kiro competition submission path. They
are preserved for provenance and are not the current GovIntel release status.

- [Submission draft](./SUBMISSION.md)
- [Three-minute demo script](./docs/DEMO_SCRIPT.md)
- [Submission checklist](./docs/SUBMISSION_CHECKLIST.md)
- [Kiro usage evidence](./docs/KIRO_USAGE.md)
- [Official submission form](https://forms.gle/xBLjk9nKMqbi2zie9)

Official competition deadline: **2026-08-23 23:59 UTC**, which is **2026-08-24 07:59 Asia/Taipei**.

## Costs and third parties

| Component | License / attribution | Cost and rate-limit boundary | Setup |
|---|---|---|---|
| Next.js 16.3.1, React / React DOM 19.2.8, `pg` 8.23.0 | MIT | No API quota; no paid service required | `npm ci --prefix apps/web` |
| hls.js 1.7.0 | Apache-2.0 | Official HLS host controls media availability | Installed by the same `npm ci` command |
| Beautiful Soup 4.14.3 / jsonschema 4.26.0 | MIT | No external API quota | `python -m pip install -r requirements.txt` |
| requests 2.33.0 / psycopg 3.3.4 | Apache-2.0 / LGPL-3.0-only | Collectors make bounded public reads; PostgreSQL is not required by the demo | Same Python install command |
| GitHub Pages / Actions | GitHub service terms; workflow uses GitHub-maintained checkout, setup, Pages, artifact, and deploy actions | Uses the account's included allowance and GitHub plan quotas | Enable Pages with GitHub Actions |
| Kiro CLI V3 | Kiro service terms; core AI development workflow | Account-plan credits; the demo has no Kiro runtime dependency | Builder ID is needed only to reproduce the development sessions |
| OpenAI Codex, OpenHands, and gstack `browse.exe` | Entrant-directed development, repository automation, documentation, and QA assistance | Account/tool-plan dependent; none is required to run or judge the demo | Development-only tools |
| Groq `whisper-large-v3` transcript snapshot | Derived navigation text; official council media remains authoritative | No live Groq call or API key in the default demo; live rerun is provider-quota limited | Checked-in snapshot; optional rerun uses `GROQ_API_KEY` |
| FFmpeg / ffprobe | User-supplied executable; license depends on the selected build | No runtime or judge-path dependency | Needed only for the optional Groq ASR rerun |
| jsDelivr `hls.js@1` | Apache-2.0 library delivered by jsDelivr | CDN availability applies only to the legacy standalone `asr-timestamp-demo.html` | Not used by the primary Next.js demo |
| Official Taichung sources | Publisher-owned public pages, APIs, records, and HLS; linked, not claimed as project-owned | No guaranteed quota; five adapters run twice daily with one bounded retry | No credentials |

Default verification performs no paid operation and no external write. Scheduled refresh writes only its generated status JSON back to the public repository.

The five scheduled inputs are `S-004` council agendas, `S-006` questioning-order tables, `S-007` meeting records, `S-009` proposals, and `S-029` city-government council project reports. The evidence path also uses `S-010`, the official Taichung City Council page, minutes, and HLS video. Exact official URLs remain visible in the checked-in status JSON and the UI.

The retained current-workspace Kiro records show Auto as `qdev::auto`: 10.254967 credits for implementation, 1.736521 for architecture review, and 1.014514 for supporting review, totalling 13.006002 credits. Auto did not identify its routed base model, so this project does not invent one. OpenHands-authored commits remain visible in public Git history, and Codex performed entrant-directed independent QA and submission-document preparation. None of these tools is part of the deployed runtime.

## Limitations

- The competition UI demonstrates one complete council-evidence journey, not every police workflow.
- Source freshness can be stale even when the endpoint is healthy; the UI shows both states.
- Some official endpoints provide no usable publication date or only partial date-window coverage.
- The migration registry currently covers the JSON durable-object/replay contract; PostgreSQL DDL evolution and live raw-snapshot backfill still require a database-backed run and receipt.
- Transcript quality is a historical baseline and has not received independent human sign-off.
- The English path translates the product journey and source names; the official Chinese transcript remains Chinese and is explicitly labelled as navigation-only evidence.
- The official `S-010` HLS CDN can fail in some Chrome sessions with `ERR_CONTENT_DECODING_FAILED`. A fatal media error or ten-second metadata timeout now preserves the transcript and provenance while showing a prominent link to the official council video. The local 2:43 product-demo MP4 is deliberately not substituted because it does not share the official evidence timeline.
- The five source adapters passed local canaries and one GitHub-hosted scheduled EVENING run succeeded on 2026-08-23. A completed post-deployment MORNING plus EVENING pair has not yet been observed.
- The public repository, demo, and captioned video have historical anonymous verification receipts; current deployment/version/hash status remains unverified in this checkout. Entrant details and form submission remain pending.

## License and data rights

No repository software license has been selected. Do not assume reuse rights. Official source content remains subject to each publisher's terms; this project preserves provenance and links rather than claiming ownership.
