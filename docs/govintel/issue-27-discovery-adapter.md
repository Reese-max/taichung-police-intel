# Issue #27 — Taiwan Intel Dashboard discovery adapter

This checkout has a bounded, read-only consumer for the versioned producer
contract represented by `tests/fixtures/discovery/dashboard-feed.v1.json`.
The fixture is the replayable test input; it does not prove a live producer
run or live canary.

## Boundaries

- `intel_v2/discovery_adapter.py` validates the versioned envelope and fails closed on schema, state, stale, or ordering errors.
- Media remains `DISCOVERY_UNVERIFIED` even with an official match; an official candidate remains only a candidate until a server-controlled official document match is supplied.
- Material changes or a changed server-controlled official match index trigger bounded verification, including late evidence on an unchanged feed. Summary/risk/entity/topic-only changes alone do not. Generation identity includes the entire validated envelope, so changed item content cannot hide behind unchanged IDs/timestamps/hashes.
- As in the existing replay contract, an omitted/empty official index supplies no new evidence; a nonempty changed index triggers rechecking. This input is not a document-revocation protocol.
- Same-source direct/Dashboard paths use `original_source_identity` and do not increase independent-source count.
- Official provenance pointers are retained. Optional `link_official_events()` reuses #24 `fuse_documents()` for read-only PublicEvent previews, without writing canonical documents or events.
- `summarize_canary()` preserves the bounded feed and pre-cap relevance denominators, excluding replayed observations from outcome totals while retaining every run's status. It reports missing denominators/dates and actual calendar coverage.
- `intel_v2/discovery_projection.py` turns each ingest result into the bounded `apps/web/public/data/discovery-signals.json` payload: `confirmed` rows must carry server-controlled official document versions, `pending` rows are capped at five and drop TTL-expired candidates, and a non-current upstream can only project as `HISTORICAL_REPLAY_ONLY` / `CANARY_ONLY` / `GAP_VISIBLE` — never `LIVE`.
- `apps/web/components/DiscoverySignalsPanel.js` renders the two sections visually separated; pending rows keep wording like 「尚未找到官方確認」 and never claim official confirmation. `scripts/discovery-signals.py self-check` replays the contract.


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
- The match fixture is an index, not a normalized document. Event previews
  additionally require server-controlled normalized documents, represented by
  `tests/fixtures/discovery/event-documents.v1.json`. Their document, version,
  source and original HTTPS URL must match the attached official provenance.
  #24 validates and fuses only matched official versions; Dashboard hints never
  become event facts. A supplied existing event is used to link its stable ID,
  never overwritten; existing conflict/split decisions remain visible.
  Exact official-version ownership also preserves manually merged event IDs;
  ambiguous ownership fails closed. Existing event previews retain their history
  and are marked `PARTIAL_LKG`, not a fresh reconfirmation of all prior facts.
  `new_public_event_count` / `existing_event_match_count` count these optional
  projections, not persisted events. `public_event_projection_only=true` marks
  that distinction; canonical/actionable changes remain zero without separate
  downstream receipts.
- Independent-source counts are based on original source identity, so aliases
  or multiple versions from the same source do not increase the count.
- Candidate TTL is anchored to `observed_at`, falling back to `event_time`;
  `generated_at` is not a source-time substitute. If both source timestamps are
  null, the candidate is rejected. An expired candidate cannot be promoted,
  and expiry pruning also runs for same-generation replays.
- The persisted candidate store defaults to 500 rows. On overflow it retains
  the rows with the latest expiry, with candidate ID as the deterministic tie
  breaker. Expired rows are removed before applying the cap.
- Relevance admits only traffic, public safety, fraud prevention, disaster,
  social and public cybersecurity categories. Social items additionally need
  public-service terms; geography must name Taichung or an explicit nationwide
  public-service impact. An LLM summary or risk score cannot supply that gate.

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
python -X utf8 scripts/discovery-signals.py self-check
python -X utf8 -m unittest discover -s tests -p test_discovery_adapter.py -v
python -X utf8 scripts/discovery-adapter.py ingest --feed tests/fixtures/discovery/dashboard-feed.v1.json --official-documents tests/fixtures/discovery/official-matches.json --output out.json

python -X utf8 -m unittest discover -s tests -p test_discovery_projection.py -v

```

Replay the complete media → official versions → PublicEvent preview path
(all URLs/documents in this fixture are synthetic; no network request occurs):

```bash
python -X utf8 scripts/discovery-adapter.py ingest --feed tests/fixtures/discovery/dashboard-feed.v1.json --official-documents tests/fixtures/discovery/event-documents.v1.json --event-documents tests/fixtures/discovery/event-documents.v1.json --now 2026-09-21T01:00:00Z --output out/discovery-replay.json
```

Passing a prior fusion/adapter JSON with `--existing-public-events` demonstrates
linking the same event instead of counting a new one. Event previews are blocked
for expired, PAUSED, RESTORING and DEGRADED candidates. Media stays in the pending
UI with official-version pointers; contradictory official matches display
「官方來源不一致，待人工判定」 without confirming the media claim.

## Canary report

Store consumer `receipt` objects in a JSON array, then run:

```bash
python -X utf8 scripts/discovery-adapter.py canary-report --receipts receipts.json --window-days 14 --output out/discovery-canary.json
```

The window ends on the latest receipt's UTC calendar date and includes 14 dates.
Undated runs remain in the denominator; missing dates or feed denominators
prevent `window_complete`. Failure statuses remain visible even when the calendar
window is complete; that flag measures calendar/denominator coverage only.
Replay runs retain their status but do not double-count candidate observations,
matches or latency. `feed_item_count` is the bounded producer feed denominator;
`relevant_count` is before the consumer cap and `candidate_admitted_count` after.
Totals describe observations across runs, not distinct real-world events. The
report also includes existing/new event previews, no-match/conflict counts,
actionable/tracking/false-positive outcome counts and first-discovery-to-official
confirmation latency (count/total/mean). Downstream outcome counts require
explicit outcome receipts; absence of a match is not itself a false-positive.
`production_verified` remains false: calendar coverage alone cannot prove value.

The deterministic 16-input-date test selects the last 14 dates and proves
56 feed observations → 42 relevant → 14 official matches / 28 no-match outcomes,
with 14 latency samples averaging 120 seconds. These are synthetic fixture
numbers, not a live 14-day report.

## Acceptance evidence

| Criterion | Local mechanism / replay |
| --- | --- |
| Versioned adapter + fixture | `validate_feed()` and `dashboard-feed.v1.json` |
| Schema mismatch fails closed | Version/field/type/order/size checks and adapter tests |
| Media never confirms or enters Top 3/handoff | Separate pending projection; media status stays `DISCOVERY_UNVERIFIED`; no canonical write |
| Official candidates require original-source evidence | Server-controlled official document/version/HTTPS index; no promotion from producer authority alone |
| Direct + Dashboard source count stays one | #24 independent source IDs and merged acquisition paths; existing same-source regression |
| PAUSED / RESTORING / DEGRADED | Replay/canary/gap receipts; no current event preview |
| Deterministic relevance + cap/TTL | Category/geography gates, 50 candidates, 500 retained, 72-hour source-time TTL |
| Canonical versions preserved | Read-only projections; existing documents/events and input results remain unchanged |
| Three-step fixture | Delayed official match → normalized official versions → new/existing stable event ID |
| Negative fixture | No official match remains unverified; TTL removes candidate |
| Conflict fixture | Media remains unverified; official disagreement reaches pending note and #24 conflict preview |
| 14-day report denominators | Window selection, pre/post-cap counts, replay exclusion, missing-data flags and latency tests; live run pending |

This is local fixture replay and focused test evidence. It is not a live
upstream canary, does not grant Dashboard write access, and does not prove
GovIntel Pages publication. No upstream restoration, live verification search,
14-day production canary, canonical persistence, deployment, push or PR was run.

## Local validation

- Discovery tests: 30 → 44; all 44 pass. The 28 existing #24 fusion tests,
  3 discovery Web tests and both CLI self-checks also pass (exit 0).
- TDD red proof: before implementation, 40 discovery tests produced
  `FAILED (failures=12, errors=3)`, including cached late matches and missing
  event-link API. Review findings received additional red/green regressions.
- `node scripts/verify-project.mjs gate0`: exit 0,
  `VERIFY_OK mode=gate0 required=164 specs=4 secrets=0`.
- `npm --prefix apps/web run build`: exit 0; all 11 static pages generated.
- The full Windows Python discovery run was executed: 1548 tests,
  17 failures, 86 errors, 12 skips. These include unchanged checkpoint tests
  requiring unavailable `os.O_NONBLOCK`/`os.mkfifo`, POSIX mode assertions,
  unavailable isolated PDF parser (the user-site install is 6.18.0 versus
  pinned 6.19.0), and cp950 subprocess output.
  The relevant POSIX source/test files match the admitted base byte-for-byte.
  The full suite is **not green**; these unrelated modules were not changed.
- The initial complete Web run was 796/800 passing: PDF parser version,
  dirty-checkout admission and query-store subprocess failures. The pinned
  PDF dependency was installed only under ignored `.runtime/` for verification,
  leaving global packages unchanged. The PDF worker uses `-I`, so `PYTHONPATH`
  cannot supply that dependency; a local virtual environment is required.
- No dedicated lint, coverage ratchet, CRAP or mutation scripts are defined
  in either package manifest; no new quality toolchain was installed.
