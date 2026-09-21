# Issue #47 — current-checkout runtime receipt

## Executable candidate lane

The current checkout now has one bounded candidate verifier:

```bash
npm ci
python -X utf8 scripts/verify-current-checkout.py
```

The verifier imports query store, source policy, health, and publication verifiers
from this checkout, builds the static export, serves it over loopback, and writes
the actual `code_sha`, dependency-lock hash, canonical artifact hashes, query
generation, policy hash, capabilities, logs, screenshots, and request trace into
`runtime-evidence/current-checkout/<timestamp>/receipt.json`.

The latest local replay passed `23/23` checks, including:

- static page and canonical bytes over HTTP;
- query generation/policy/hash identity and bounded query results;
- the local read-only MCP tool list and hash-bound `get_publication_receipt` projection;
- foreign generation rejection, source-unavailable distinction, and capability unavailability;
- mixed-generation cutover rejection while the previous index remains readable;
- query service down (`503`) while static publication remains readable;
- real Chrome input, stale-status and publication-hash display, refresh generation stability, filtered archive results, and an official HTML link opening in a new browser page;
- a temporary sabotaged checkout failing its own module-binding check.

Loopback HTTP proves only the candidate query/static lane. It is recorded as a
query-index success; formal `deployment` and `public_http_verification` stages
remain `UNKNOWN` until a real deployment and anonymous public hash receipt exist.

The companion Python contract suite has `24/24` tests. The verifier is local-only,
uses a preserved checked-in snapshot, and does not write canonical or production
state.

## Evidence boundary

| Dimension | State |
|---|---|
| `IMPLEMENTED` | YES — one build/start/verify entry, receipt, static fallback, browser lane and sabotage lane |
| `CORE_TESTED` | YES — 24/24 current-checkout tests and full project gate |
| `INTEGRATED` | YES — local Web → same-origin Gateway → Query Store → official locator replay |
| `LIVE_SOURCE_TESTED` | NO — checked-in snapshot only for this candidate replay |
| `DEPLOYMENT_VERIFIED` | NO — no Pages/public HTTPS/hash receipt |
| `USER_VALIDATED` | NO — no task-based human evaluation |

No merge, deployment, production write, or issue closure was performed. #20 still
requires normal review/merge, scheduled MORNING/EVENING runs, and anonymous
public hash verification.
