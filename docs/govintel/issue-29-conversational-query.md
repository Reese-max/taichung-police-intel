# Issue #29 — shared conversational queries

`/public-query/` now includes Ask GovIntel: eight quick actions, a natural-language
question/follow-up field, and a clear-context action. Configure
`NEXT_PUBLIC_QUERY_GATEWAY_URL` as documented in the README. Every turn calls
`chat_turn` through the existing release-bound query client. The browser performs
no retrieval or intent parsing, calls no model, and sends no transcript. Context
exists only in page memory and is cleared on reload or navigation.

The Python Gateway already provides all five typed primitives through HTTP and
MCP. Its optional event/statistics stores still require validation and source
admission; this change does not grant source permissions or populate those stores.
The Worker currently provides publication metadata, source health and the canonical
brief. Event/statistics requests there remain explicitly unavailable. The existing
`/research/` journey and `/ask/` redirect are preserved.

New event searches replace stored filters with their actual resolved request;
explicit narrowing carries only those filters forward. A natural-language reset
also clears prior selections when the next response asks for clarification.
Version comparisons order observations by UTC instant and compare the selected
endpoints' fields, rather than carrying a previous transition's change labels.
The conversation envelope carries the primitive's selected IDs, trust counts,
result counts and truncation, alongside the outer turn receipt and inner query receipt.

| Acceptance criterion | Implementation / regression evidence |
| --- | --- |
| Versioned QueryRequest / EvidenceEnvelope | Existing schema version 1 planner and Gateway envelopes; Web rejects incompatible versions and mixed publication receipts. |
| Five core primitives | Reuses `QueryGateway.execute` and `intel_v2.query_domain`; existing domain suites exercise events, event, compare, evidence and statistics. |
| Deterministic IDs/order | Existing canonical filters/cursor binding; repeated-query and transport-parity fixtures. |
| Shared Web / MCP retrieval | Web calls `chat_turn`; `dispatch_mcp` calls the same Gateway. Worker HTTP/MCP chat parity also remains covered. |
| Event / geography / time context | Existing A-scenario fixture plus fresh-query/reset regressions; Web sends only bounded context. |
| Failure / partial / stale / conflict disclosure | Visible status, freshness, gaps, notices and separate trust sections; B-scenario and Worker failure fixtures. |
| Discovery stays unverified | Separate UNVERIFIED section; existing C-scenario fixture and full-match discovery counts. |
| Statistics scope and provenance | Period, unit, geography, provisional/final state, dataset/source and update time table; existing D-scenario fixture. |
| Read-only tools | Existing typed tool/argument allowlists; no SQL, URL, filesystem or write tool added. |
| Caps / pagination / truncation | Existing bounded primitives and generation/filter-bound cursors; chat exposes truncation and prompts narrowing. Public index pagination remains available. |
| Gateway outage preserves static UI | Chat error is local to the panel; existing current-checkout fallback checks and browser smoke. |
| Exact official evidence | Existing canonical document/evidence/version locators, safe HTTPS links, and E-scenario version fixture. |
| Publication hash / query receipt | Web validates binding before rendering; visible receipt includes query ID, publication hash, generation and resolved conditions. |
| Zero results vs failed coverage | Existing bounded/unbounded no-match fixtures; UI retains the server's statement and source gaps. |

Focused regression command (Git Bash):

```bash
node --test --test-concurrency=1 apps/web/tests/chat-answer.test.mjs apps/web/tests/chat-turn-worker.test.mjs apps/web/tests/query-release-client.test.mjs && python -X utf8 -B -m unittest discover -s tests -p 'test_query_domain.py' -v && python -X utf8 -B -m unittest discover -s tests -p 'test_query_gateway_domain.py' -v && python -X utf8 -B -m unittest discover -s tests -p 'test_query_gateway_stdio.py' -v
```

The focused suite passes 81 tests (70 existing plus 11 regressions). An offline
Chrome smoke against the Python fixture Gateway also exercises district/time
narrowing, mixed-tier ordinal identity, version changes, exact evidence links,
statistics provenance, reload reset, a 390px viewport and a simulated 503 outage.

Full validation is **not green**. In an isolated Ubuntu WSL checkout of the same
candidate, `npm run check` completed with exit 1: `web-tests` has two existing PDF
parser timeout cases, and `news-list-checkpoint-tests` has 2-second CLI timeouts.
The production build and current-checkout core receipt (5/5) passed. The CLI
timeouts reproduce on admitted base `c629f82`; its PDF suite passes, while the
candidate's isolated PDF suite still times out, so that cause remains unresolved.
Windows full validation was stopped after POSIX-only failures and the existing
60-second query-store bridge limit; all 45 underlying query-store tests pass,
and the original base also takes longer than that limit. These gates were not
modified. The repository has no separate lint, coverage ratchet, CRAP or mutation
script; no additional quality tooling was installed.

These are local implementation/fixture checks. Production deployment, live source
coverage, a 14-day canary and human efficiency measurements remain unverified.
