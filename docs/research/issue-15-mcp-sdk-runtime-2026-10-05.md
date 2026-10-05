# Issue 15: isolated public Evidence MCP research receipt

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
archives tracked committed source into a temporary directory, changes only
synthetic copies there, and deletes the directory afterward. It never modifies
the live checkout's publication artifacts or reads ignored credentials.

```sh
python3 -m venv ../issue15-sdk-env
../issue15-sdk-env/bin/pip install 'mcp==1.30.0'
mkdir -p receipts
# Linux example; choose an existing writable receipt directory.
bwrap --unshare-net --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp \
  --bind "$PWD/receipts" "$PWD/receipts" \
  ../issue15-sdk-env/bin/python scripts/probe-evidence-mcp-sdk.py \
  --source-ref HEAD --mode journeys --output receipts/seven-journeys.json
```

Run `--mode private-green` on the repaired source for the two negative cases.
To reproduce the actual pre-repair defect, use `--mode private-red --source-ref
366a457817104b0c6b5a03ea5ca893e4b9e6a4db`. A red-mode result deliberately reports
`CONFIRMED_RED_MAIN_NESTED_FIELD_LEAK`; it is not a passing privacy gate. The
frozen receipts used a separately installed diagnostic environment outside
`/tmp`, with `bwrap --unshare-net`, a read-only checkout and ephemeral `/tmp`.

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
