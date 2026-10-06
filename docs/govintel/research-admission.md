# GovIntel research admission service

## Delivery status and boundary

This is a separate, offline-tested Cloudflare Worker and Durable Object implementation. It is **disabled by default**. No Worker was deployed, no Durable Object namespace or service binding was created, no Access application or persistent credentials were created, and no live JWT, JWKS, or provider API was used during implementation/testing. The checked-in Wrangler file is a review-only example, not an enabled deployment configuration.

The existing query gateway's `RESEARCH_ADMISSION` contract is supported without changing the provider adapter. The service verifies an already-issued Cloudflare Access application JWT and reserves budget; it does not implement browser login or grant new access.

**Browser authentication remains a deployment blocker.** At the time this service was added, `apps/web/lib/research-client.js` sent research requests with `credentials: "omit"`. Adding this JWT service alone does not make the UI authenticate. Activation needs a separately reviewed same-origin authenticated proxy, or explicit frontend/Worker credential and CORS wiring appropriate to the actual origins. Cross-origin cookie behavior, Access protection of every reachable gateway route, preview endpoints, CSRF controls, origin checks, and the browser login flow must be reviewed and tested together. Client-provided identity headers are never sufficient. This change does not modify those boundaries or create public resources.

## Fixed request and response

Only `POST /authorize` with no query string and `Content-Type: application/json` is accepted. The request body must contain exactly these five fields and values:

```json
{
  "operation": "govintel-research",
  "provider": "MiniMax",
  "model": "MiniMax-M2.7",
  "max_completion_tokens": 1024,
  "max_input_tokens": 16384
}
```

The gateway forwards the `CF-Access-Jwt-Assertion` header. No question, previous messages, source text, source URLs, provider API key, client identity, prices, or budget values belong in this body. Unknown fields, changed model/provider, and smaller or larger token reservations fail closed. Bodies are limited to 1,024 bytes with a bounded read deadline.

Success is HTTP 200 with `{"allowed":true}` **after the reservation transaction commits**. Every error returns `{"allowed":false,"code":"..."}` with a non-success HTTP status. Responses include `Cache-Control: no-store`. There is no health, refund, retry, reconciliation, or reusable authorization-token endpoint. The query gateway must call admission separately before every provider request and must never reuse a previous positive response.

## Authentication and public-key fetch

Owner-controlled configuration pins one exact HTTPS issuer on `<team>.cloudflareaccess.com`, one application audience, and a nonempty explicit allowlist of Access `sub` identifiers. Wildcard subjects are rejected. The service ignores email, cookies, bearer-header substitutes, `X-User-Id`, request-body subjects, and other asserted identity headers.

Verification uses Web Crypto `RSASSA-PKCS1-v1_5` with SHA-256 (RS256). It requires the configured issuer, matching string/array audience, an allowlisted subject, and an unexpired integer `exp`. Optional `nbf` and `iat` must be integer dates already reached and earlier than expiration. No clock-skew grace is applied. Claims are checked before expensive work, after signature verification, and again inside the transaction. Forged signatures, unexpected algorithms, unrecognized key IDs, alternate header keys, `jku`, `jwk`, `x5u`, and critical-header extensions are rejected.

Keys come only from the configured issuer's exact `/cdn-cgi/access/certs` endpoint. The unverified JWT never chooses a network destination. Fetches use HTTPS, `redirect: "error"`, omit credentials, and send no token or subject. The reply must be successful JSON, must not be redirected or have an unexpected final URL, and is bounded to 32 KiB and three seconds. The key set supports up to eight distinct public signing keys with RS256, RSA 2048–4096-bit moduli and exponent 65537; private parameters, duplicate IDs, weaker keys and algorithm confusion are rejected.

A per-object in-memory JWKS cache lasts at most five minutes. Concurrent refreshes coalesce. Unknown IDs cannot trigger extra fetches while a valid cache exists, so newly rotated IDs may temporarily fail closed until refresh. Failed refreshes have a five-second backoff; expired keys are never used as a network-failure fallback. This bounds one object's key fetches but is not a substitute for gateway rate limiting or abuse protection.

## Atomic reservations and persistence

Every request is dispatched to one fixed Durable Object name, `govintel-research-global-v1`, in one shared namespace. User IDs, dates, releases, request URLs, and environment inputs cannot select another object. All deployments intended to share this budget must use that same namespace; deploying independent namespaces would create independent budgets.

The object authenticates requests and then performs its entire ledger read/check/write inside `storage.transaction`. The closure uses only transactional storage, trusted configuration, verified claims, and the server clock. It has no provider/network call or other external side effect, so Cloudflare may safely replay it when resolving contention.

Every accepted request permanently reserves, for that UTC calendar day:

- One call against both the user's and global call caps
- 17,408 tokens (16,384 input + 1,024 completion) against both token caps
- The conservative, owner-priced cost against the global USD spending cap

The ledger holds the UTC date, global counters, and per-user counters keyed by SHA-256 of issuer, audience and subject. It stores no raw JWT, subject, email, question or source content. These digests are pseudonymous identifiers, not anonymous data. No application logging is performed; the example also disables Worker observability. Review infrastructure, Access and gateway logging independently before activation.

Counters persist across object eviction/restart. The first accepted request of a later UTC date replaces the active-day counters atomically; earlier-day counters are not retained by this application. A clock rollback cannot reopen an older budget. Corrupt ledger state and storage failures deny access rather than resetting counters. Same-day policy edits do not reset usage. At most 100 distinct subject digests can be recorded per day; changing the allowlist does not erase previously recorded subjects, and excess new subjects fail closed until the next day. Ledger counters must agree with the sum of all users or access is denied.

There is no refund for unused output capacity, a provider failure, a timeout, cancellation, a rejected model response, or a lost admission response. Repeating the request consumes a new reservation. That conservative overcount is intentional. The provider adapter must not retry a provider call under the same reservation. This cannot prevent an authenticated user from consuming their own allowance; call and token quotas remain the upper bounds.

## Required owner configuration

The example `workers/research-admission/wrangler.example.jsonc` has `workers_dev: false`, `preview_urls: false`, no routes, observability disabled, empty identity configuration, zero caps/rates, and `ADMISSION_ENABLED: "false"`. Merely copying this example does not authorize enabling service access, billing or deployment.

All the following are required before admission can be enabled:

| Variable | Meaning |
| --- | --- |
| `ADMISSION_ENABLED` | Exact string `true` only after authorized activation |
| `RESEARCH_BILLING_REVIEWED` | Exact string `true` after owner review of prices, billing and limits |
| `ACCESS_TEAM_DOMAIN` | Exact `https://<team>.cloudflareaccess.com`, no path, port or trailing slash |
| `ACCESS_AUD` | The intended Access application's audience tag |
| `ACCESS_ALLOWED_SUBJECTS` | JSON array of 1–100 explicit, unique Access subject IDs |
| `RESEARCH_DAILY_USER_CALLS` | Per-subject UTC-day call ceiling |
| `RESEARCH_DAILY_GLOBAL_CALLS` | Shared UTC-day call ceiling |
| `RESEARCH_DAILY_USER_TOKENS` | Per-subject ceiling on reserved input + completion tokens |
| `RESEARCH_DAILY_GLOBAL_TOKENS` | Shared ceiling on reserved input + completion tokens |
| `RESEARCH_INPUT_USD_MICROS_PER_MILLION_TOKENS` | Conservative input price, in millionths of USD per million tokens |
| `RESEARCH_OUTPUT_USD_MICROS_PER_MILLION_TOKENS` | Conservative completion price, in millionths of USD per million tokens |
| `RESEARCH_DAILY_GLOBAL_USD_MICROS` | Shared estimated spending ceiling in millionths of USD |

Every cap and rate must be a positive decimal safe-integer string. Zero, missing, fractional, exponent-form, negative or unsafe values deny admission. No free-tier, exchange-rate, cached-token discount, or MiniMax price is assumed. A budget smaller than one full reservation is valid policy and simply admits zero requests.

Reservation cost in microdollars is calculated with exact integer arithmetic:

```text
ceil((16384 × input_rate + 1024 × completion_rate) / 1000000)
```

The owner must choose rates that safely cover the actual configured model, account, reasoning-token accounting and any relevant charges. Input byte limits in the gateway are deliberately used as a conservative token allowance here, but activation still requires verifying that the actual provider tokenizer, prompt envelope and output/reasoning cap cannot exceed the reservation. Provider-side billing limits should remain enabled where available.

**This is a hard cap on reserved calls/tokens and owner-priced estimates, not a provider invoice guarantee.** Provider prices can change, other clients may use the account, service/platform charges may apply, and a request admitted before midnight may be billed after midnight. A UTC reservation window is not necessarily the provider's billing window. No provider-billing integration or account-wide spending lock is claimed.

## Activation checklist (requires separate authorization)

1. Verify the intended browser-to-gateway authentication route, Access policy/audience, approved subjects, origin/CORS/CSRF controls and all alternate public gateway endpoints
2. Obtain owner-approved budgets and conservative prices; verify actual provider token accounting and review applicable billing/terms
3. Review the dedicated service deployment, fixed shared Durable Object namespace, access to that namespace, migration, log settings, and gateway service binding named `RESEARCH_ADMISSION`; retain no public routes for admission
4. Complete staging verification of the real Workers/Durable Object runtime, genuine Access JWTs/key rotation, persistence, concurrency, UTC rollover and denied requests with no provider call
5. Only after approval enable the dedicated admission flag and the independently gated provider settings; rollback is to disable provider/admission, never delete/reset the active ledger

No steps in this checklist were executed against a live account by this change. A deployed Worker alone would not establish that the browser flow or account-wide spending guard is operational.

## Offline verification

From repository root:

```sh
node --test apps/web/tests/research-admission.test.mjs
```

Tests create ephemeral synthetic RSA keys and JWTs locally, use fake JWKS responses, and use a serialized, commit/rollback storage model. They never call a real JWKS or MiniMax endpoint and never create persistent authentication credentials. Coverage includes auth forgery/malformed claims and config, subject isolation, each separate cap, 100 concurrent requests, storage persistence across handler replacement, rollover/rollback, key-cache behavior, cancellation and body bounds, logging hygiene, and a direct integration with the existing research provider adapter.

These are executable offline tests, **not a Cloudflare edge-runtime or deployed authentication validation**. The transaction model checks application behavior against the documented storage contract; it does not substitute for the separately authorized staging test above.

## Official references

- [Cloudflare: Validate Access JWTs](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/) — configured issuer/audience and the Access signing-key endpoint
- [Cloudflare: SQLite-backed Durable Object storage](https://developers.cloudflare.com/durable-objects/api/sqlite-storage-api/#transaction) — atomic storage transactions and transactional method usage

Reviewed on 2026-10-05. The conservative bounds, failure behavior and owner configuration rules above are application design choices, not claims that Cloudflare sets those particular defaults.
