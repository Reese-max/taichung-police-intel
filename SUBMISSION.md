# Submission Draft

## Project

**Name:** Taichung Police Public Intelligence

**One sentence:** An evidence-first public-information monitor that helps police policy staff prepare for council questions in five minutes.

**Category fit:** Public-sector productivity, situational awareness, and trustworthy AI-assisted information synthesis.

## Problem

Police policy and council-liaison staff must monitor scattered official pages, proposals, reports, meeting records, and videos. The work is slow, and an unsupported summary is difficult to trust when a supervisor or council member asks for the source.

## Solution

The application presents one priority council issue, exposes the health and known gaps of five official data sources, preserves last-known-good when collection fails, and lets the user jump from a transcript word to the exact timestamp in the official council video.

## Why it matters

- Reduces a one-to-two-hour public-information scan to a focused five-minute workflow.
- Keeps every decision-support item connected to official evidence.
- Makes missing or stale information visible instead of producing false certainty.
- Uses only public information and excludes personal, case-level, dispatch, and internal operational data.

## What is working

- Next.js static production build passes locally.
- Five live official-source collectors.
- Health, completeness, intelligence-gap, SHA-256, and last-known-good status.
- Evidence drawer with searchable segments and word-level video seeking.
- Complete English judge path with official Chinese source names translated and the Chinese transcript clearly labelled as navigation-only.
- Two daily GitHub Actions refreshes and GitHub Pages deployment configuration.
- Deterministic project, Spec, workflow, data-contract, secret, and build verification passes locally.

## Kiro usage

Kiro Steering defines the police user, public-data boundary, technology constraints, and evidence policy. Three Kiro Specs define executable requirements for source provenance, the five-minute homepage, and council evidence. Three Hooks invoke the same repository verifier on relevant lifecycle events.

Authenticated Kiro CLI V3 Spec sessions reviewed all 16 Steering, Spec, and Hook artifacts and implemented a bounded product task: the English primary path, shared evidence contract, central-policy cap, and homepage acceptance test. Human review rejected an insufficient first pass, fixed a drawer initialization defect found by browser QA, and reran the complete gate. The session IDs, prompts, corrections, Hook truth, and exact results are retained in [docs/KIRO_USAGE.md](./docs/KIRO_USAGE.md).

## URLs

- **Public repository:** `PENDING_PUBLIC_REPOSITORY_URL`
- **Working demo:** `PENDING_PUBLIC_DEMO_URL`
- **Demo video (three minutes maximum):** `PENDING_VIDEO_URL`

## Judge test

```bash
npm ci --prefix apps/web
python -m pip install -r requirements.txt
npm run check
```

Then open the public demo, review the source monitor, open the evidence drawer, search for `警察局`, and click a transcript timestamp.

## Honest boundaries

The demo contains one complete council-preparation journey. It does not access police internal systems, infer missing post-meeting implementation status, or claim that derived ASR text is authoritative. The official page, video, and source-status evidence remain the basis for verification.

## Entrant details

- **Entrant or team name:** `PENDING_ENTRANT_NAME`
- **Member contribution:** `PENDING_CONTRIBUTION_STATEMENT`
