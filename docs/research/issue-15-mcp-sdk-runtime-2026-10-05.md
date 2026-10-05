# Issue 15: bounded STDIO and governed Evidence MCP research

The current follow-up combines the bounded STDIO transport with canonical
artifact provenance and governance admission. The unchanged checked-in legacy
policy authorizes zero formal evidence: coverage stays UNKNOWN, a bounded
no-match cannot be asserted, and brief/answer requests return `RIGHTS_BLOCKED`.
The optional SDK diagnostic observes those refusals first. Positive journeys
require a separate, explicit fictional metadata and derived-summary review
compiled from copied actual inputs; that permission never changes the real
approved snapshot, source catalog, publication or historical receipts.

The transport counts initialize and requests against its per-process budget,
uses correlated numeric protocol errors, and closes an oversized channel
without draining arbitrary input. The SDK diagnostic has a finite read deadline
and preserves source/runtime/policy/receipt hashes. Final integrated-source
validation and native required CI remain necessary; the historical observations
below do not validate a new source head or approve source rights.

## Historical observations preserved from the original research

The official Python MCP SDK 1.30.0 completed seven real STDIO client journeys
against committed source `4a010e52ff8c8ccbbac85e3ebdcd11c7a386fb4f` with
synthetic publication copies in an owned temporary Git archive. That source
includes main `366a457817104b0c6b5a03ea5ca893e4b9e6a4db` and the narrow public
projection repair. Runtime source hashes in each receipt identify the measured
files independently of this subsequently added script and documentation.

The three frozen SDK JSON files remain unchanged after publication. Their
original diagnostic omitted store/policy file hashes because it used incorrect
module paths; the complete source commit/tree still pins those files. The
[supplemental exact-file hashes](issue-15-mcp-runtime-file-hashes-2026-10-05.json)
record the actual gateway/store/policy/projection paths and original receipt
digests. The diagnostic now requires those actual paths.

This is a public research/evidence interface. It is not a police operational
system. No production endpoint, source promotion, human usability improvement,
OAuth implementation, provider call, or private data access is claimed.

## Reproduce with the optional diagnostic

The SDK is a diagnostic dependency, not a new product requirement. Create a
separate environment with Python 3.12+ and `mcp==1.30.0`. Package installation
may need networking; run the actual probe with networking disabled. The script
archives tracked committed source into a temporary directory and deletes the
directory afterward. The default `current-negative` mode observes the unchanged
current publication: zero formal items, UNKNOWN coverage without an answerable
bounded zero, and `RIGHTS_BLOCKED` for a brief or answer. It never modifies the
live checkout's publication artifacts or reads ignored credentials.

Positive journeys now require explicit `--fixture-policy fictional-reviewed`.
The archived current compiler creates schema 2 from copied actual catalog and
rights inputs in a **separate** fictional root. The helper grants fictional
metadata permission and a separately declared derived/domain summary permission
there; metadata permission alone does not authorize a brief. Its two generated
metadata rows use catalog source IDs and approved HTTPS origins. No historical
real feed row is retimed or silently licensed. Before every current-source
positive run, the SDK separately records the unchanged canonical refusal.

```sh
python3 -m venv ../issue15-sdk-env
../issue15-sdk-env/bin/pip install 'mcp==1.30.0'
mkdir -p ../issue15-receipts
issue15_receipt_dir="$(cd ../issue15-receipts && pwd)"
# Linux example; keep checkout, SDK environment and receipts outside /tmp.
bwrap --unshare-net --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
  --bind "$issue15_receipt_dir" "$issue15_receipt_dir" \
  ../issue15-sdk-env/bin/python scripts/probe-evidence-mcp-sdk.py \
  --source-ref HEAD --mode current-negative \
  --output ../issue15-receipts/current-negative.json
# Repeat the same sandbox command with these mode/output arguments:
# --mode journeys --fixture-policy fictional-reviewed \
# --output ../issue15-receipts/fictional-seven-journeys.json
```

Run `--mode private-green --fixture-policy fictional-reviewed` on the repaired
source for a valid reviewed baseline and the two private canary negatives.
The baseline must first succeed; both private cases must return a projection
error without the canary. Unexpected connection closure fails the diagnostic.
Failure fixtures use the current source-state enums: FAILED, DEGRADED/PARTIAL,
and QUARANTINED with an explicit scalar CONFLICT gap. This preserves failure
visibility without accepting malformed source states as a successful journey.
To reproduce the actual pre-repair defect, use `--mode private-red --source-ref
366a457817104b0c6b5a03ea5ca893e4b9e6a4db`. A red-mode result deliberately reports
`CONFIRMED_RED_MAIN_NESTED_FIELD_LEAK`; it is not a passing privacy gate. The
frozen receipts used a separately installed diagnostic environment outside
`/tmp`, with `bwrap --unshare-net`, a read-only checkout and ephemeral `/tmp`.
Write new receipts outside the checkout. Version-2 receipts preserve the
canonical input hashes, original frozen research receipt hashes, actual
compiler/runtime/helper hashes, and the fictional policy binding/rights hash.
The historical red mode changes disposable old publication copies only and
remains distinct from the unchanged current-source control. All existing frozen
JSON receipts remain historical observations, unchanged by this follow-up.

## Frozen real-client results

- [Seven SDK journeys](issue-15-mcp-sdk-seven-journeys-2026-10-05.json): brief,
  bounded search, exact evidence lookup, health and receipt; four failed states;
  unavailable source; publication head switch. All seven passed.
- [Before repair](issue-15-mcp-sdk-private-red-2026-10-05.json): both a nested
  brief private-field canary and a nested source-gap canary reached the official
  client on current pre-repair main.
- [After repair](issue-15-mcp-sdk-private-green-2026-10-05.json): both requests
  return `isError: true`, `PUBLIC_PROJECTION_INVALID`, and no canary marker.
- [Loopback HTTP and shutdown](issue-15-mcp-http-shutdown-2026-10-05.json): two
  allowed HTTP requests followed by 429, then a stopped MCP server while a
  separately served static build retains the index and exact three publication
  artifact bytes. This earlier receipt measures product source `6eb4d74`; the
  later integration does not change gateway or publication projection files.

The synthetic fresh fixture proves contract behavior, not that real checked-in
sources are fresh or approved for promotion. `SOURCE_NOT_AVAILABLE` remains
an explicit `NOT_IN_APPROVED_SNAPSHOT` gap with UNKNOWN freshness; unavailable
evidence never becomes an answerable zero. A head switch uses fresh-server
restart and changed exact publication bytes, without claiming live hot reload.

## STDIO transport bounds follow-up

The earlier frozen receipts above prove their listed research cases; they do
not establish a STDIO request rate limit or a stalled-response deadline. The
independent native-main audit found both gaps. The follow-up transport applies
the same default 60 requests per 60 seconds as the existing HTTP adapter, per
STDIO server process. Initialize and malformed messages count as requests; the initialized
notification does not. Over-limit requests receive a standard numeric JSON-RPC
error with `data.code=RATE_LIMITED`, and normal EOF still closes gracefully.
Operator `--rate-limit` overrides must be positive. This is a process boundary,
not a distributed user/account quota.

STDIO consumes at most 65,537 bytes when checking a request line. An oversized
line gets one `REQUEST_TOO_LARGE` error and the process closes, including when
the caller leaves stdin open without a newline. It does not drain arbitrary
input to find the next message. Application errors use numeric JSON-RPC codes
and preserve the application code in `error.data.code` so an official client
can decode correlated schema and rate errors. Oversized or unparseable input
has no reliable request ID; oversized input closes the channel after its error,
and a client may therefore observe connection closure rather than a correlated
size result. Clients must not reuse that poisoned channel.

The optional official-SDK diagnostic explicitly sets a 15-second client read
timeout and records it in new receipts. That bounds the tested driver's wait;
the existing server answer-gate subprocess has its own 10-second limit. An
external process stop cannot be overridden by server code, and other clients
must configure their own deadlines and handle connection closure. These
changes do not retroactively alter the frozen observations or certify
protected tools, production availability, current-source rights or usability.

## Original acceptance and runtime mapping

| Original acceptance | Evidence and boundary |
| --- | --- |
| 1. Versioned envelope/receipt | The existing gateway `_envelope` and `get_publication_receipt` implement both version-1 response contracts, described in the README query-gateway section; SDK verifies both schema versions while publication hashes, generation, source-health, freshness and gaps are preserved. |
| 2. At most five read-only tasks | Actual SDK `tools/list` has exactly the five current task-level tools, all read-only/non-destructive annotations; transport has no write, shell or arbitrary upstream tool. |
| 3. Exact canonical deterministic projection | SDK repeated search returns identical selected data; receipt artifact hashes equal the synthetic committed-schema artifact bytes; exact selectors and ordered cursor replay are checked. |
| 4. Official evidence locator | SDK selected evidence has an official HTTPS locator and canonical reference; existing gateway tests reject missing/unverified provenance rather than relabeling it verified. |
| 5. Failure states survive | Separate SDK FAILED, PARTIAL, CONFLICT and STALE journeys expose status and gaps, with bounded no-match disabled; missing source remains explicit unavailable/UNKNOWN. |
| 6. Closed public fields | New recursive versioned brief/source projection rejects unexpected nested keys and non-scalar leaf values. Both real SDK red/green canaries and nine unit negative shapes cover the leak; real tracking/profile/attachment provenance remains valid. |
| 7. No argument injection | Four actual SDK calls reject arbitrary URL, path, SQL and over-limit arguments; existing transport/input tests cover malformed and oversized messages. |
| 8. Caps and rate bounds | SDK observes result cap, different cursor page and truncation; HTTP receipt records actual 429 after the configured two-request limit. |
| 9. Static publication independence | Gateway shutdown leaves the separately served built static index and artifact bytes available; gateway changes do not alter collectors, scheduler or build outputs. |
| 10. Future authorization | Conditional future gate: current anonymous local public-only pilot has no protected tool. OAuth audience/scope/token-passthrough testing remains required if auth is later introduced. |
| 11. Old head cannot claim current | New server generation rejects the old expected generation and cursor; receipts retain exact old and new publication hashes. |
| 12. Research/operational boundary | This document and the gateway contract explicitly limit the interface to public evidence research. |

Runtime requirements 1–5 use the actual official-SDK journeys above, requirement
6 uses SDK result/cursor caps plus actual HTTP 429, and requirement 7 uses the
shutdown/static receipt plus the full repository gate. Requirement 8 is the
conditional future-auth gate. Third-party ChatGPT/Claude host usability and
copy/paste step reduction were **NOT_RUN**; the original issue places those
measurements after this research and no improvement percentage is claimed.

At source `6eb4d74`, the required full repository gate passed 127 required files,
419 web tests and the Next build, and the Python suite passed 687 tests with one
optional PostgreSQL skip. The final PR must also pass the full gate and hosted
required `verify` check on its exact integrated source head; these historical
counts are not a substitute for that final validation.
