# Taichung Police Public Intelligence

An evidence-first public-information monitor that helps police policy staff prepare for council questions in five minutes.

The demo compresses official Taichung council and government sources into one workflow: identify a priority issue, inspect source health and intelligence gaps, then jump to the exact official video timestamp. Chinese is the end-user language; the homepage toggle provides the complete English judge path.

## Judge path

1. Read the priority brief on the homepage.
2. Check the five official sources for health, freshness, gaps, and last-known-good.
3. Open the evidence drawer.
4. Click a transcript segment or word to seek the official council video.
5. Review `.kiro/` and [Kiro usage evidence](./docs/KIRO_USAGE.md).

## Status

| Deliverable | Current state |
|---|---|
| Working application | Local static production build passes |
| Public demo | GitHub Pages workflow is ready; public deployment is pending authorization |
| Twice-daily updates | GitHub Actions schedules 06:30 and 18:30 Asia/Taipei |
| Source observability | Five live official-source adapters emit health, completeness, gaps, SHA-256, and last-known-good |
| Evidence navigation | 86 transcript segments and 1,036 word timestamps seek the official HLS video |
| English judge path | Complete homepage, source-monitor, evidence-drawer, control, limitation, and official-source translation path passes browser QA |
| Kiro assets | Four Steering files, three Specs, and three executable Hooks are checked in |
| Kiro live-session proof | Authenticated V3 Spec sessions reviewed the architecture and implemented the English homepage path with executable acceptance tests |
| Submission package | English README, script, checklist, and submission draft are present; URLs and video remain pending |

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

## Public deployment

`.github/workflows/pages.yml` uses GitHub Pages and GitHub Actions:

- a push to `main` builds and deploys the checked-in snapshot;
- `30 22 * * *` UTC refreshes the morning slot at 06:30 Asia/Taipei;
- `30 10 * * *` UTC refreshes the evening slot at 18:30 Asia/Taipei;
- each scheduled run commits only `apps/web/public/data/source-status.json`, then deploys the static export;
- manual dispatch can refresh either slot.

After the repository is public, enable Pages with **Source: GitHub Actions**. The deployed demo and repository URLs will be added to [SUBMISSION.md](./SUBMISSION.md). A workflow file is not deployment evidence; acceptance requires an anonymous HTTPS check.

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

Authenticated Kiro V3 Spec sessions first reviewed all 16 Steering, Spec, and Hook artifacts, then implemented the English primary path, the two-card cap, and the homepage acceptance test. Human browser review rejected the first static-guide-only attempt, found and fixed a drawer initialization defect in the accepted implementation, and reran the full gate. Session IDs, prompts, corrections, Hook truth, and command output are recorded in [docs/KIRO_USAGE.md](./docs/KIRO_USAGE.md).

## Evidence and safety rules

- Official source content is authoritative; derived transcript text is navigation only.
- A collection failure is never presented as zero matching items.
- `source_health` and `window_completeness` remain separate.
- `FAILED` and `NOT_RUN` cannot overwrite last-known-good.
- Every displayed source links to an HTTPS official page or endpoint.
- Public aggregates are allowed; personal and operational police data are out of scope.
- Missing post-meeting evidence remains an explicit gap, not an AI inference.

## Competition package

- [Submission draft](./SUBMISSION.md)
- [Three-minute demo script](./docs/DEMO_SCRIPT.md)
- [Submission checklist](./docs/SUBMISSION_CHECKLIST.md)
- [Kiro usage evidence](./docs/KIRO_USAGE.md)

Official competition deadline: **2026-08-23 23:59 UTC**, which is **2026-08-24 07:59 Asia/Taipei**.

## Costs and third parties

| Component | License / attribution | Cost and rate-limit boundary | Setup |
|---|---|---|---|
| Next.js 16.3.1, React 19.2.8, `pg` 8.23.0 | MIT | No API quota; no paid service required | `npm ci --prefix apps/web` |
| hls.js 1.7.0 | Apache-2.0 | Official HLS host controls media availability | Installed by the same `npm ci` command |
| Beautiful Soup 4.14.3 / jsonschema 4.26.0 | MIT | No external API quota | `python -m pip install -r requirements.txt` |
| requests 2.33.0 / psycopg 3.3.4 | Apache-2.0 / LGPL-3.0-only | Collectors make bounded public reads; PostgreSQL is not required by the demo | Same Python install command |
| GitHub Pages / Actions | GitHub service terms | Uses the account's included allowance and GitHub plan quotas | Enable Pages with GitHub Actions |
| Groq transcript snapshot | Derived navigation text; official council media remains authoritative | No live Groq call or API key in the default demo | Checked-in snapshot |
| Official Taichung sources | Publisher-owned public pages and HLS; linked, not claimed as project-owned | No guaranteed quota; five adapters run twice daily with one bounded retry | No credentials |

Default verification performs no paid operation and no external write. Scheduled refresh writes only its generated status JSON back to the public repository.

## Limitations

- The competition UI demonstrates one complete council-evidence journey, not every police workflow.
- Source freshness can be stale even when the endpoint is healthy; the UI shows both states.
- Some official endpoints provide no usable publication date or only partial date-window coverage.
- Transcript quality is a historical baseline and has not received independent human sign-off.
- The English path translates the product journey and source names; the official Chinese transcript remains Chinese and is explicitly labelled as navigation-only evidence.
- The five source adapters passed local canaries, but the GitHub-hosted schedule and anonymous public URL are not verified until deployment.
- Public repository, public demo, video, and form submission remain pending external actions.

## License and data rights

No repository software license has been selected. Do not assume reuse rights. Official source content remains subject to each publisher's terms; this project preserves provenance and links rather than claiming ownership.
