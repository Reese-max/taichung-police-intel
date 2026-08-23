---
inclusion: always
---

# Evidence and safety

## Trust boundary

Only public, authorized sources may enter this repository. Never add credentials, private endpoints, internal police data, personal data, unlicensed media, or copied session tokens.

## Evidence contract

Every publishable fact must retain, when applicable:

- source ID and source role;
- official requested and final URL;
- publication and fetch time;
- original content SHA-256 and parser version;
- page, paragraph, attachment, or video timestamp locator;
- derivation type and verification status;
- producer/verifier run IDs, models, prompt version, and reason codes.

## Deterministic gates

- Keep `source_health` separate from `window_completeness`.
- A failed or unavailable source is never converted to zero items.
- Only `AUTO_PASS` claims may enter a formal product view.
- Exact duplicates are suppressed deterministically before model review.
- The verifier reads original evidence, not only the producer summary.
- One disagreement may retry once; another disagreement becomes `QUARANTINED`.

## Required labels

- `ORAL_OFFICIAL`: an official's spoken answer in a meeting or official video.
- `WRITTEN_OFFICIAL`: a written response whose publisher and addressee are identifiable.
- `RESOLUTION`: a council review opinion or resolution, not an agency response.
- `GROQ_ASR`: derived navigation text; formal citation returns to official media.
- `UNVERIFIED_AFTER_MEETING`: no systematic official post-meeting implementation evidence exists.

## External actions

Do not push, deploy, submit forms, spend money, or publish data without explicit user authorization. A Hook or agent may run local deterministic checks but may not perform external writes.
