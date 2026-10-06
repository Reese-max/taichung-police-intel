# #13 Live meeting contract

`intel_v2/live_meeting.py` and `scripts/live-meeting.py` provide the bounded,
local-first state machine for a future public meeting session. It is not a live
provider adapter and does not auto-start ASR.

## Safety boundary

- `start` requires an allowlisted official source and HTTPS stream URL.
- Transport is `PREPARING` unless the caller explicitly enables it and supplies
  an explicit bounded budget plus provider authorization state.
- Speaker labels are unverified tokens (`SPEAKER_1`, `UNKNOWN`, or
  `UNVERIFIED`); the state machine never turns them into names.
- Every segment is provisional (`LIVE_ASR_PROVISIONAL`) until a reconciliation
  receipt binds it to an official media/minutes URL and locator.
- Optional role highlighting is computed from the versioned public profile
  catalog, hash-bound to the segment text, and copied to a bookmark only from
  a selected segment; it is navigation metadata, not a formal fact.
- Stream/ASR/budget gaps are intervals, not empty-result claims; `timeline`
  renders them as visible `GAP` entries with `display_empty=false`.
- `search`/`locator` keep live navigation bounded and provisional; a locator
  only becomes an actionable `STREAM_TIME` seek when the session was started
  with a verified allowlisted seek contract (`--seek-base-url`), otherwise it
  degrades to `TIME_TEXT` and never fabricates a deep link.
- `fail`/`resume` cover the crash/restart cycle; recorded state is preserved
  and the timeline is never silently stitched.
- `reconcile` receipts record the provisional hash, official locator, document
  version, ASR contract, and reconciler identity; a superseding receipt marks
  the previous one via `superseded_by`, and `mark-stale` flags a receipt whose
  recorded official source hash no longer matches the observed source. A stale
  receipt drops the session back to `RECONCILING` and is excluded from
  `formal_candidates`.
- The fixed snapshot and scheduled publication paths do not depend on this
  state machine.

## Replay

```powershell
python -X utf8 scripts/live-meeting.py self-check
python -m unittest discover -s tests -p "test_live_meeting*.py" -q
```

The replay covers interim→final revision, disconnect/reconnect gap,
bookmarking, deterministic profile highlighting, serialized restart,
cross-type overlap rejection, explicit stop, crash/fail resume, bounded
provisional search, time-text locator degradation, stream-time seek
contracts, receipt supersession and staleness, official reconciliation, and
the rule that reconciled output is `OFFICIAL_RECONCILED`, never `AUTO_PASS`.

Executable scenario fixtures for the required edge cases live under
`tests/fixtures/live-meeting/scenario-*.json` and are replayed by
`tests/test_live_meeting_scenarios.py`; canonical emitted states
(`session-live.json`, `session-reconciled.json`) are shared with the web view
contract tests in `apps/web/tests/live-session-view.test.mjs`, which project
the same session JSON through `apps/web/lib/live-session-view.js`.

The remaining runtime boundary is intentional: an authorized public stream,
provider latency/cost canary, browser live search/seek UI, and real post-event
VOD/minutes availability still require a separately approved runtime test.

## PR #51 prototype preservation

The separate JavaScript prototype is retained only in `tests/support/live-session-prototype.mjs` with its unique 50-case regression suite. Its alternative storage schema is an unapplied design reference at `docs/govintel/prototypes/live-session-storage.sql`; the production migration sequence continues to use the canonical ingestion/detail-recheck schema. Production and workspace views consume the canonical Python session contract and browser-safe `live-session-view.js`. No provider transport, formal evidence writer, or second production live backend is enabled by this integration.
