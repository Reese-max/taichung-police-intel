# Research integration and activation checklist

This document records a staged integration plan. It does not authorize source use, production activation, paid calls, credentials, merging, or deployment.

## One user-facing conversation entry

The implemented single research entry is `/research/`, with `/ask/` as a client-side replacement redirect and an accessible fallback link. `/public-query/` remains the structured read-only filter interface. Avoid merging a second top-level conversation navigation entry from PR #148 unchanged.

PR #148 currently proposes `/ask/`, a Python `chat_turn` resolver for structured event/statistics queries, and session-scoped selected conditions. Its Worker change explicitly reports `chat_turn` unavailable. The current research MVP instead uses the existing Workers metadata index and a disabled MiniMax ID selector. These are different backend capabilities, not interchangeable implementations.

Before incorporating #148:
1. Review its diff against current main rather than restoring old Worker or governance code from its historical base.
2. Reuse only the independently tested intent/condition-resolution logic after the corresponding event/statistics capabilities are admitted and available on the deployed backend.
3. Keep the existing `/ask/` compatibility alias and one research page. Do not replace it with the second interface or advertise unsupported event/statistics capabilities.
4. Preserve release binding, source permissions, per-claim evidence checks, cancellation and explicit data-use confirmation for every provider-bound request.
5. Test legacy and new navigation, back/forward, interrupted requests and stale responses together before merge. Never persist complete transcripts merely to reuse PR #148's condition storage.

## Governance and PR #123

PR #123's retention/rights work overlaps the Worker and retention matrix. Main already has enforced public projection and formal-admission checks. Reconcile the specific missing archive controls against current main; do not replace current policy bindings, copy historical approvals, or treat an old draft PR as source permission.

The current schema-1 policy still yields zero admitted formal records. Metadata permission does not imply full-text, quotation, derivative-answer or model-transmission permission. A separately reviewed source-specific manifest must cover every intended use before a document can enter the research evidence pipeline.

## Offline components versus live activation

- Metadata research: implemented, disabled by default; selects exact IDs and renders gated metadata assertions only.
- Authentication/budget service: implemented with adversarial offline tests in `workers/research-admission/`. Deployment needs owner-approved Access configuration, audience/subject scope and explicit budget limits. There is no anonymous paid fallback.
- Document evidence: the permission-pinned store is now connected to the existing `/research` endpoint in `documents` and `synthesis` modes through `document-research.js`. Its bounded inline corpus and independent permission/source-policy pins come only from server configuration; no new upload/storage endpoint exists. Synthetic test permission cannot promote real sources.
- Model synthesis: an optional MiniMax producer plus a separate original-passage critic is implemented behind explicit transmission permission and per-call admission. Exact citations and conservative temporal/coverage guards are checked; outputs remain `SYNTHESIS_DRAFT`, `RESEARCH_ONLY`, and `AI_REVIEWED_NOT_FORMALLY_VERIFIED`. Conflicting/insufficient claims are withheld. A 45-case synthetic gold suite checks route/UI/hash contracts; it does not establish real-model accuracy or formal semantic verification. Authorized ingestion, deployed access, live evaluation and human source review remain necessary.
- Browser QA: a dedicated offline research-page Chromium check is included in CI. Passing this check does not validate a real model, credentials, plan limits or deployed authentication.

## Before any live request

The owner must securely supply the provider credential and approve the actual plan/audience/spending arrangement. Review source licenses and personal-data constraints independently. Deploy the authenticated admission/budget service and verify fail-closed behavior before enabling the provider. Run an explicitly authorized, capped smoke test, then separately review any merge or deployment request. No automatic pay-as-you-go switch or provider fallback is allowed.
