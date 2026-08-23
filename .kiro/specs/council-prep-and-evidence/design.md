# Council preparation and evidence design

## Current reusable slice

`apps/web` already proves official HLS playback, searchable Groq ASR segments, word grouping, and timestamp seeking for one council example. Reuse it; do not rewrite it while building the missing claim/API path.

## Flow

```text
agenda / questioning order
  -> historical question, oral answer, proposal, and project-report links
  -> Generator: preparation candidates
  -> Verifier: type, scope, and original locator checks
  -> council preparation view
  -> evidence drawer / official source
```

## Deterministic checks

- Validate evidence ID, official URL, content hash, evidence type, page/paragraph/timestamp locator, ASR time continuity, and supported coverage window.
- Never treat a resolution, ASR transcript, or oral answer as a written implementation update.

## Generator

The generator produces preparation points only from selected evidence IDs and labels synthesis separately from source text.

## Verifier

The verifier reads official evidence, checks evidence type and wording scope, and rejects broken locators or any claim that fills `GAP-001` from model knowledge.

## Failure states

Use `CLAIM_REJECTED`, `TRANSCRIPT_PENDING`, `TRANSCRIPT_DISPUTED`, `VALIDATION_PENDING`, `AI_DISAGREEMENT`, `QUARANTINED`, and `UNVERIFIED_AFTER_MEETING` without silent fallback to generated facts.

## Retry limit

One verifier or transcript retry is allowed. A second disagreement suppresses the derived claim while preserving official links and the last `AUTO_PASS` evidence.

## Acceptance command

```bash
node --test apps/web/tests/council-prep.test.mjs
npm run check
```

The future council-prep test must cover the full fixture chain, timestamp navigation data, evidence types, and `GAP-001` behavior.
