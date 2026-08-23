---
inclusion: always
---

# Technology

## Implemented now

- Next.js 16, React 19, and hls.js for the evidence-drawer vertical slice.
- Python 3.11 scripts using requests and Beautiful Soup for public-source audits and canaries.
- JSON Schema plus deterministic Python routing for traceability, value, duplicate, retry, and quarantine decisions.
- Node.js standard-library verification in `scripts/verify-project.mjs`.

## Competition target

- Python collectors with one scheduler.
- Versioned `source-status.json` for public health, gaps, SHA-256, and last-known-good.
- Next.js static Route Handlers and static export.
- GitHub Actions at 06:30 and 18:30 `Asia/Taipei`, deployed through GitHub Pages.
- An OpenAI-compatible producer adapter and an independent verifier role.

## Post-competition options

- PostgreSQL for durable raw items, events, claims, evidence, validation runs, and gaps.
- A separate API service only after the static end-to-end path passes and measured demand requires it.
- Docker Compose only when the database or separate service becomes necessary.

## Constraints

- Prefer standard-library or already-installed dependencies.
- Do not add vector search, an agent platform, or a workflow orchestrator for the hackathon path.
- Fixed snapshot mode must work without API keys or paid services.
- Live ASR uses Groq `whisper-large-v3` only when explicitly invoked with a configured key.
- Generator and verifier runs must have separate run IDs; deterministic checks take precedence over model agreement.

## Required commands

```bash
npm run check:gate0
npm run check:specs
npm test
npm run check
```
