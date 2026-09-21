# Issue #47 — current-checkout runtime receipt

## Local replay

At `2026-09-21` the current checkout `f8468e0` was started with the existing
read-only Query Gateway and Web app:

```text
python -X utf8 scripts/query-gateway.py --port 8788 --allow-origin http://localhost:3001
NEXT_PUBLIC_QUERY_GATEWAY_URL=http://127.0.0.1:8788/query PORT=3001 HOSTNAME=127.0.0.1 npm --prefix apps/web run dev
```

Browser replay at `http://localhost:3001/`:

- Typed `警察` into **Ask GovIntel** and submitted the query.
- The page returned `STALE` / `publication_metadata`, showed the same
  publication hash `00f0d175205729da139a4a79d18c814cd7428e1219844bc635aec7ea095fc905`,
  and preserved the metadata-only rights boundary.
- **目前 Brief** returned `READY` while the page continued to display the
  snapshot as `STALE`; zero results were not presented as proof of no events.
- A result's official HTML link opened in a separate browser tab at
  `https://yishi.tccc.gov.tw/proposals/7ccb1247-bb63-4b10-a282-a663af54d130`.

The existing `python -X utf8 scripts/verify-current-checkout.py --self-check`
receipt remains the machine-readable HTTP/MCP parity gate. The browser replay
is supplementary UI evidence and does not write canonical state.

## Evidence boundary

| Dimension | State |
|---|---|
| `IMPLEMENTED` | YES — current-checkout HTTP receipt and Web Query Gateway panel |
| `CORE_TESTED` | YES — full gate `VERIFY_OK mode=full required=93 specs=4 secrets=0` |
| `INTEGRATED` | YES — local Web → Gateway → Query Store → official locator replay |
| `LIVE_SOURCE_TESTED` | NO — checked-in snapshot only for this candidate replay |
| `DEPLOYMENT_VERIFIED` | NO — no Pages/public HTTPS/hash receipt |
| `USER_VALIDATED` | NO — no task-based human evaluation |

The local CORS origin is part of the development command only. No merge,
deployment, production write, or issue closure was performed.
