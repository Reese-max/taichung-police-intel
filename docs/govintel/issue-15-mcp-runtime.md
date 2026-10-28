# Issue #15 — STDIO MCP runtime evidence

The subsequent [2026-10-05 official-SDK isolated replay](../research/issue-15-mcp-sdk-runtime-2026-10-05.md)
adds seven compatible-client journeys and actual nested private-field red/green
receipts. The dated probes below remain historical evidence.

## 2026-09-22 current-checkout probe

At checkout `c9a8be520107437b1260566457cde9f8fb7da561`, a read-only
stdlib-based client process exercised `scripts/query-gateway-stdio.py` with the
full lifecycle sequence:

1. `initialize` using protocol version `2025-06-18`;
2. `notifications/initialized`;
3. `tools/list`;
4. `tools/call` for `get_current_brief`.

Observed result:

```json
{"call_is_error":false,"protocol_version":"2025-06-18","publication_hash_present":true,"response_ids":[1,2,3],"returncode":0,"stderr_empty":true,"tool_count":5,"tool_names":["search_evidence","get_current_brief","get_publication_receipt","get_source_health","validate_answer"]}
```

The existing `tests/test_query_gateway_stdio.py` also passed 4/4, including
invalid JSON, non-object JSON, and oversized request-line rejection. The server
returned only the bounded read-only tool set and did not emit protocol logs to
stdout/stderr.

This is a local process/protocol receipt, not proof of a third-party MCP SDK,
remote Streamable HTTP, anonymous public service, production deployment, or
client-side preservation of warnings. Those remain separate #15/#29 gates.

## 2026-09-22 loopback HTTP probe

The same checkout was started with `scripts/query-gateway.py --port 0
--allow-origin http://probe.test`. A standard-library HTTP client then called
`/mcp` for `initialize`, `tools/list`, and `tools/call(get_current_brief)`.
All three returned HTTP 200, protocol version `2025-06-18`, five tools, and a
non-empty publication hash. Repeating `tools/list` with
`Origin: https://untrusted.test` returned HTTP 403 with
`ORIGIN_NOT_ALLOWED`.

This confirms the local Streamable HTTP boundary and Origin rejection only;
the server was bound to `127.0.0.1` and was terminated after the probe. It is
not an anonymous Pages or production MCP endpoint receipt.

## Web Worker and stdio MCP parity (issue #29)

Production Web traffic reaches the Cloudflare Worker (`workers/query-gateway`),
while stdio MCP clients run the Python Gateway. `apps/web/tests/gateway-runtime-parity.test.mjs`
loads one fictional governed publication into both and requires the same
typed answers for `search_evidence` (keyword, miss, source filter, paging),
`get_source_health`, `get_publication_receipt` and `get_current_brief`, with
and without a failed source: identical canonical IDs, order, freshness,
source gaps, query coverage, result rows and receipts, ignoring only query IDs,
the query clock, the Worker's release block and the receipt's `issued_at` and
`server_version`. The same cursor string comes from
both and leads to the same next page, and invalid arguments fail with the same
error code.

Aligning the two surfaced these differences in the Worker, now fixed to match
the Python Gateway: `get_source_health` accepted a non-string `source_id`,
rejected a `null` one (Python reads it as absent) and listed every source for
an empty one (Python lists none); `validate_answer` skipped the generation
check for a falsy `expected_generation`; results published at the same instant
were ordered by locale collation rather than code point; and search cursors
were encoded with unsorted keys. The Python Gateway, for its part, stated
`collection_coverage_status` only for rights-blocked queries, while the Worker
and the release verifier expect it beside every formal query status. The
ordering is by code point, so IDs beyond the BMP sort as Python sorts them.

Both runtimes still read an empty `source_id` as "no filter" for
`search_evidence` but as an exact filter for `get_source_health`; that is the
Python Gateway's existing contract and is kept identical rather than changed.

Not covered, and known to differ:

- `chat_turn` natural-language parsing: the Worker uses its own bounded rules,
  Python uses `intel_v2/conversation.py`.
- Keyword case folding: the Worker lower-cases with `toLocaleLowerCase()`,
  Python with `casefold()`, so a few non-CJK letters (for example `ß` against
  `SS`) can match differently and give different cursor filter hashes.
- A failed index rebuild: the Python Gateway marks the formal status `STALE`
  and keeps the earlier collection status; the Worker marks the collection
  status `PARTIAL` and keeps any rights-blocked formal status.
