# Issue #27 — Taiwan Intel Dashboard discovery adapter

This checkout has a bounded, read-only consumer for the versioned producer
contract represented by `tests/fixtures/discovery/dashboard-feed.v1.json`.
The fixture is the replayable test input; it does not prove a live producer
run or live canary.

## Boundaries

- Schema version 1 is validated without inferring fields. Unknown envelope
  fields, invalid state/version/order, duplicate IDs, or malformed items fail
  closed. `load_feed()` reads at most 256 KiB; the envelope may contain at most
  200 items, and one snapshot admits at most 50 relevant candidates. TTL is
  capped at 72 hours; the persisted candidate store is capped at 500 rows.
- Media remains `DISCOVERY_UNVERIFIED` even when an official document matches.
  The match is recorded separately as `official_match_status` with
  server-controlled document provenance. Official-authority rows start as
  `OFFICIAL_CANDIDATE` and require source recheck. Summary, risk, entity, and
  topic hints never serve as official evidence.
- Matched official document/version provenance is attached to candidates for
  downstream resolution. The checked-in match fixture is an index, not the
  full normalized document input required by #24 `fuse_documents()`; this
  adapter does not invent that payload or call fusion. It creates no
  PublicEvent, performs no canonical write, and reports zero
  PublicEvent/canonical promotions.
- Independent-source counts are based on original source identity, so aliases
  or multiple versions from the same source do not increase the count.
- Candidate TTL is anchored to `observed_at`, falling back to `event_time`;
  `generated_at` is not a source-time substitute. If both source timestamps are
  null, the candidate is rejected. An expired candidate cannot be promoted,
  and expiry pruning also runs for same-generation replays.
- The persisted candidate store defaults to 500 rows. On overflow it retains
  the rows with the latest expiry, with candidate ID as the deterministic tie
  breaker. Expired rows are removed before applying the cap.

## Upstream operating states

- `ACTIVE`: official matching can run; outputs remain read-only candidates
  with official provenance pointers.
- `DEGRADED`: candidates carry `upstream_gap=true`; automatic official matching
  is blocked and the receipt reports `DEGRADED_GAP`.
- `RESTORING`: matching is withheld by default. `--canary-mode` enables a
  diagnostic match evaluation while preserving `DISCOVERY_UNVERIFIED` /
  `OFFICIAL_CANDIDATE` status and zero canonical-change count.
- `PAUSED`: matching is withheld by default. `--historical-replay` permits
  historical matching, reported separately; it does not contribute to current
  official-match, new-event, or canonical-change counts.

The two opt-in flags fail closed unless the producer state is respectively
`RESTORING` or `PAUSED`.

## Local replay

```powershell
python -X utf8 scripts/discovery-adapter.py self-check
python -X utf8 -m unittest discover -s tests -p test_discovery_adapter.py -v
python -X utf8 scripts/discovery-adapter.py ingest --feed tests/fixtures/discovery/dashboard-feed.v1.json --official-documents tests/fixtures/discovery/official-matches.json --output out.json
```

This is local fixture replay and focused test evidence. It is not a live
upstream canary, does not grant Dashboard write access, and does not prove
GovIntel Pages publication.
