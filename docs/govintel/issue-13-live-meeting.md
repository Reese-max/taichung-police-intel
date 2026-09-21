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
- Every segment is provisional until a reconciliation receipt binds it to an
  official media/minutes URL and locator.
- Stream/ASR/budget gaps are intervals, not empty-result claims.
- The fixed snapshot and scheduled publication paths do not depend on this
  state machine.

## Replay

```powershell
python -X utf8 scripts/live-meeting.py self-check
python -m unittest discover -s tests -p test_live_meeting.py -q
```

The replay covers interim→final revision, disconnect/reconnect gap,
bookmarking, explicit stop, official reconciliation, and the rule that
reconciled output is `OFFICIAL_RECONCILED`, never `AUTO_PASS`.

The remaining runtime boundary is intentional: an authorized public stream,
provider latency/cost canary, browser live search/seek UI, and real post-event
VOD/minutes availability still require a separately approved runtime test.
