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
- Two daily GitHub Actions refreshes and GitHub Pages deployment configuration; one natural scheduled EVENING run completed successfully on 2026-08-23.
- Deterministic project, Spec, workflow, data-contract, secret, and build verification passes locally.

## Kiro usage

Kiro Steering defines the police user, public-data boundary, technology constraints, and evidence policy. Three Kiro Specs define executable requirements for source provenance, the five-minute homepage, and council evidence. Three Hooks invoke the same repository verifier on relevant lifecycle events.

Authenticated Kiro CLI V3 sessions reviewed all 16 Steering, Spec, and Hook artifacts and implemented a bounded product task: the English primary path, shared evidence contract, central-policy cap, and homepage acceptance test. Entrant-directed review rejected an insufficient first pass, fixed a drawer initialization defect found by browser QA, and reran the complete gate. The three retained current-workspace sessions selected Auto, exposed only `qdev::auto` rather than a routed base-model name, and recorded 13.006002 credits. Session IDs, prompts, corrections, Hook truth, model disclosure, and exact results are retained in [docs/KIRO_USAGE.md](./docs/KIRO_USAGE.md).

## URLs

- **Public repository:** `https://github.com/Reese-max/taichung-police-intel`
- **Working demo:** `https://reese-max.github.io/taichung-police-intel`
- **Demo video (2:43, complete English chapter captions):** `https://reese-max.github.io/taichung-police-intel/demo-video.mp4`

## Judge test

```bash
npm ci --prefix apps/web
python -m pip install -r requirements.txt
npm run check
```

Then open the public demo, review the source monitor, open the evidence drawer, search for `警察局`, and click a transcript timestamp.

No test credentials, API key, database, payment, or private account are required for the judge path.

## Third-party and AI assistance disclosure

- Direct JavaScript dependencies: Next.js, React, React DOM, `pg`, and hls.js. Direct Python dependencies: Beautiful Soup, jsonschema, requests, and psycopg.
- Hosting and automation: GitHub Pages and GitHub-maintained Actions for checkout, language setup, Pages configuration, artifact upload, and deployment. The legacy standalone ASR demo loads hls.js from jsDelivr; the primary Next.js demo does not.
- Data and media: five named official Taichung sources (`S-004`, `S-006`, `S-007`, `S-009`, `S-029`) plus the `S-010` official council page, minutes, and HLS video. Publisher rights remain with the original agencies.
- Historical ASR: a checked-in Groq `whisper-large-v3` snapshot is used only as derived navigation text. The default demo makes no live Groq call; an optional rerun requires user-supplied FFmpeg/ffprobe plus `GROQ_API_KEY` and is subject to provider quotas.
- Development assistance: Kiro CLI V3 was the core competition workflow. OpenAI Codex performed entrant-directed independent QA and submission-document preparation. OpenHands-authored repository commits and gstack `browse.exe` QA evidence are retained. None of these tools is a deployed runtime dependency.
- Repository rights: no software license has been selected, so reuse rights must not be assumed. Exact licenses, costs, setup boundaries, and source links are listed in [README.md](./README.md).

## Honest boundaries

The demo contains one complete council-preparation journey. It does not access police internal systems, infer missing post-meeting implementation status, or claim that derived ASR text is authoritative. The official page, video, and source-status evidence remain the basis for verification.

## Entrant details

- **Entrant or team name:** `PENDING_ENTRANT_NAME`
- **Member contribution:** `PENDING_CONTRIBUTION_STATEMENT`
