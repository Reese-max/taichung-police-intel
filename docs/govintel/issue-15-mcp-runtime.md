# Issue #15 — STDIO MCP runtime evidence

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
