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

## Optional model-assisted intent (Web only, disabled by default)

`/public-query/` has an opt-in "AI 解析問題（實驗）" switch. With it on, a typed
question (never a quick action) goes to the Worker's `POST /chat` instead of
`chat_turn` on `/query`. The route needs the matching `release_id` and an explicit
`public_data_only: true` attestation, and applies the same length and personal-data
guard as `/research` before any provider work. MCP and `/query` `chat_turn` are
unchanged and never call a model, so MCP clients keep deterministic results and pay
nothing.

- **Intent step** (`workers/query-gateway/src/chat-assist.js`): one call through the
  existing `/research` provider path (`requestResearchJson`), so every gate of
  `research-mvp.md` applies unchanged: `RESEARCH_ENABLED`, `MINIMAX_API_KEY`,
  `MINIMAX_BILLING_REVIEWED` and a `RESEARCH_ADMISSION` reservation per call. The model
  receives only the question, the fixed tool menu, the public source IDs and the time
  expression names, never publication text. It must return JSON naming one menu tool
  (`search_evidence`, `get_source_health`, `get_current_brief`, `search_events`,
  `query_statistics`), at most a 64-character `q` (search only), an exact snapshot
  `source_id`, and a time expression name. Anything else is `OUTPUT_REJECTED`.
- **Time**: the server resolves the expression on its own clock to explicit
  Asia/Taipei dates with the same windows and labels as
  `intel_v2/conversation.py` `parse_time_window` (a test compares both). The Worker's
  metadata has no time index, so a parsed window is reported in `chat.parser.time_window`
  and treated as an unsupported narrowing filter; the query is not broadened.
- **Fallback**: a disabled provider, denied admission, provider error or rejected
  output falls back to the deterministic rules; `chat.parser` records
  `{kind: "rules", fallback_reason}`.
- **Answer step**: only after a validated intent ran `search_evidence` with results,
  and only when formal admission is `ADMITTED`, the existing metadata selection
  (`selectMetadataAnswer`, shared with `/research`) lets a second admitted call pick
  exact IDs among at most six current trusted rows. The server builds title claims and
  runs the unchanged `answer-evidence-gate/3`; `chat.assisted_answer.answer` holds
  only the controlled renderer's text. Every other outcome is a reason code with an
  empty answer. It supports which publication is relevant, not facts in its title.

Current checked-in governance is `RIGHTS_BLOCKED`, so the answer step stays off in
production even with a key. Activation still needs the owner steps in
`research-mvp.md` and `research-admission.md`, including the browser Access/session
route; nothing here enables a provider or changes any approval.

Known limits:

- The intent step sends the question to the provider whenever the four gates are
  on, even while formal admission is `RIGHTS_BLOCKED` (only publication text is held
  back). Turning `RESEARCH_*` on therefore also starts sending `/chat` questions.
- Every keyword of `q` must appear in the question; a model that rephrases falls
  back to the rules. The model can still omit a time or source the question named:
  the turn then runs without it, and the UI shows the conditions actually applied
  (no time condition is listed), so the omission is visible but not refused.
- The browser accepts an assisted answer only when each line rebuilds from a
  passing gate receipt and matches that reply's own evidence title and locator; it
  cannot detect a Worker that forges all of them consistently.

Offline checks: `node --test --test-concurrency=1 apps/web/tests/chat-assist-worker.test.mjs apps/web/tests/chat-assist-client.test.mjs`
(mocked provider and admission only).
