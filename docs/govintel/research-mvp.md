# GovIntel research MVP (offline implementation)

Base: `bd0152b0585dc888e9576892166afaff766c1fec` (main, verified 2026-10-05). This change is a disabled, secret-free integration, not a production AI launch.

## Scope and honest behavior

- `/research/` adds an accessible, memory-only question/follow-up UI. It keeps up to four previous user questions, supports Cancel and Reset, and requires a public/non-personal input attestation. No uploads, notifications, local storage, conversation persistence or autonomous background work.
- `POST /research` uses the existing Worker-owned, policy-filtered metadata snapshot. It requires the matching Pages/Worker release. The search is bounded lexical title/committee/source-ID search, not full-text, semantic retrieval, exhaustive research or proof that an event occurred.
- Current repository governance has no formally admitted source records. The real snapshot must return `METADATA_ONLY / RIGHTS_BLOCKED`, empty sources and empty answers, without a provider call. No approved policy, rights matrix, archive or publication input was changed.
- If separately approved metadata is available, metadata links and dates may be displayed with explicit freshness/coverage gaps. Dates remain separate: publication, data-as-of and fetch. An empty list never means no event happened.
- The optional MiniMax adapter selects exact IDs from at most six server-provided metadata records. Unknown/duplicate IDs, prose, tool calls and truncated responses are rejected. It cannot introduce new evidence or free-form factual answers. Selected title assertions still pass the unchanged `answer-evidence-gate/3` and controlled renderer. This supports identifying relevant publications only; it does not support conclusions about facts embedded in their titles.

## Disabled-by-default activation gates

All gates must be satisfied before any provider request:

1. Source rights/governance must be reviewed through the existing independent workflow. Never edit approval fixtures to activate real data.
2. `RESEARCH_ENABLED` must explicitly be `true`. The checked-in Wrangler default is `false`.
3. The owner must enter `MINIMAX_API_KEY` directly into the Worker secret facility through a secure handoff. Do not put a real key in chat, files, tests, frontend variables or Git. This patch contains no credential. Previously exposed keys should be replaced by their owner.
4. The owner must separately review the intended MiniMax plan, deployment audience and spending limit before `MINIMAX_BILLING_REVIEWED=true`. No automatic provider/model, plan, endpoint or billing fallback exists.
5. The service in `workers/research-admission/` implements the trusted `RESEARCH_ADMISSION` contract with cryptographic Access verification and atomic global/per-user budget reservations. It must still be reviewed, configured and wired before activation. It receives only an access assertion plus `{operation,provider,model,max_completion_tokens,max_input_tokens}`. It must verify the assertion cryptographically, enforce the authorized audience, and atomically reserve one call within an owner-approved global budget. It returns `{allowed:true}` only after these checks. Missing, denied or malformed admission fails closed. This patch does not deploy that service or treat a browser checkbox, Origin, or rate-limit counter as authentication/budget control. The browser Access/session route also remains to be configured; see `research-admission.md`.

Enabling a key alone is insufficient. Public multi-user hosting and live validation remain unapproved/unverified. The existing gateway's local rate counter is not an accurate cross-region spending budget.

## Provider contract

Fixed URL: `https://api.minimax.io/v1/chat/completions`; fixed model: `MiniMax-M2.7`. The browser never calls MiniMax. Provider requests are non-streaming, prohibit redirects/tools, allow one attempt only, and cap completion tokens at 1,024 (including model thinking). Question length is 512 characters, history has at most four user turns, sources at most six, serialized provider input at most 16 KiB (reserved conservatively as 16,384 input tokens), response bytes at most 32 KiB, and admission plus provider work has a 15-second abort deadline. Body reads additionally have a 5-second deadline and terminate on cancellation. Provider body/errors, credentials and thinking are never reflected in the UI or logged.

The input screen and conservative pattern checks are guardrails, not guaranteed anonymization. Only public, non-personal research is in scope. Production privacy/security review is still required before enabling transmission.

Official references checked 2026-10-05:
- API format and supported models: https://platform.minimax.io/docs/api-reference/text-openai-api
- Plan guidance: https://platform.minimax.io/docs/token-plan/faq — individual interactive developer use; pay-as-you-go is recommended for production. Subscription Keys and standard API Keys differ, and purchased Credits may cover subscription overflow. Do not call the plan unlimited or assume a public service is covered.

## Offline verification

New provider fixtures are explicitly fictional and synthetic. They do not demonstrate real API reachability, quota, provider output quality, current billing eligibility, authentication deployment or live publication.

Run `node --test apps/web/tests/research-worker.test.mjs apps/web/tests/research-client.test.mjs` plus the unchanged project gates. `tests/governed_policy_fixture.py` only copies the new Worker import into its isolated offline fixture; no fictional permission is promoted into repository governance.

For browser QA, mock the research/release endpoint with clearly marked offline data. Test mobile and desktop, invalid/missing release, blocked rights, metadata dates and gaps, malformed payload, unsupported/invented citation, cancel/reset/unmount/repeated submit, and ensure no provider request occurs.

## Additional offline foundations

- `document-evidence.md`: pinned server-only permissions, bounded full-text passage retrieval and exact cross-document excerpt compilation. Not imported into the live route. This is extractive research, not independently verified semantic synthesis.
- `research-admission.md`: actual JWT verification and atomic call/token/estimated-spend reservations, with 27 adversarial offline tests and disabled owner-configured deployment example.
- `s038-candidate/README.md`: candidate-only national police news CSV parser and hash-only historical baseline. It is not registered, scheduled or exported to the public feed.
- `research-integration-plan.md`: proposed consolidation with pending PR #148 and preservation of current governance versus #123. Those branches have not been merged or overwritten.
- `scripts/research-browser-e2e.mjs`: dedicated Chromium CI for consent, follow-ups, citations, dates, gaps, mobile layout, duplicate submission, cancel/reset, stale results and navigation. It uses synthetic responses only and never calls MiniMax.
