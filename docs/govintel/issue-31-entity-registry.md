# #31 — Agency / Location / Named Event entity registry

One canonical, non-person entity registry is the shared identity layer for
#24 PublicEvent fusion, #27 discovery hints, and the optional #29 Python event
gateway. The fusion CLI loads the canonical registry by default; an explicit
`--entity-registry` selects an isolated fixture. Deployed consumer acceptance
remains separate, as described in [shared entity bindings](issue-31-entity-bindings.md).

- Production registry data: `config/entity-registry.v1.json`
- Isolated collision and dated-event fixtures: `tests/fixtures/entity-registry/seeded-collision-coverage.v2.json`
- Resolver and audited manual edits: `scripts/entity-registry.py`
- Pinned cross-component replay: `config/backbone-runtime.v1.json` (`entity` lane, PR #41)

## Record shape

Each row carries a canonical `entity_id`, `canonical_label`, `aliases`,
`kind`, `jurisdiction`, `status`, and provenance (`evidence`, plus
`alias_evidence` when an operator adds an alias). Locations add
`location_type` (`city` / `district` / `road` / `venue`); named events add
`event_type` and a mandatory ISO `event_date`. `registry_version` is a
monotonic content counter for the corresponding registry; production revisions and isolated fixture revisions are separate. It covers audited
manual changes, `updated_at` records the last revision, and `registry_hash` is
the SHA-256 of the canonicalized content.

Every resolution receipt — including `NO_MATCH` and `AMBIGUOUS` — carries
`registry_version` and `registry_hash` (not `updated_at`), so a downstream store
can reject mixed or stale snapshots.

## Matching rules

Two paths, both deterministic:

- **ID lookup** — `resolve_entity_id()` answers `ACTIVE`, `REDIRECT` (with the
  replacement IDs of a retired entity), or `NO_MATCH`.
- **Name lookup** — `resolve()` performs one normalized index lookup over
  `(kind, jurisdiction, event_date, name)`. Canonical labels and aliases share
  that single index, so a label and its alias are indistinguishable to the
  resolver; there is no fuzzy or embedding stage.

Normalization is deterministic only: NFKC, whitespace collapse, `台`→`臺`.
Administrative-suffix forms such as `臺中市西屯區` are explicit per-row aliases,
not a stripping rule. Nothing infers a district and no address is truncated to a
district.

- A name unique inside `(kind, jurisdiction, event_date)` resolves only when the
  caller supplies a jurisdiction or date, or the name is globally unique.
- The same label under a different jurisdiction, or on a different date, stays
  `AMBIGUOUS` with `candidate_ids`; it is never auto-merged.
- Unknown text is `NO_MATCH`. A fuzzy/embedding scorer may propose a candidate
  separately, but no candidate path writes a canonical ID.
- Person entities are rejected at validation time, so no person or case-party
  registry can enter this file.

## Shipped fixtures and their provenance

The six agency rows cite catalogued sources (`official-source-catalog:S-001`,
`S-032`, `S-031`, `S-004`, `S-019`, `S-033`). The city and district rows cite the
`administrative-name` name-derivation label. The two pre-existing road rows,
`location:tc-taiwan-blvd` (臺灣大道) and `location:tc-route-74` (台74線), cite the
`official-road-name` label rather than a dated source citation.

The 16 added rows are kept only in the isolated test fixture; the default runtime registry excludes them. Their dated event occurrences have no verified official schedule and must not become canonical search/fusion facts without review. Every row added for collision coverage is a **seeded fixture**, not a live
source record, and says so in its provenance string:

| Seeded rows | `evidence` |
|---|---|
| city government agency `agency:tc-government` (distinct from the city-hall venue) | `fixture:agency-name` |
| cross-city roads `location:tc-zhongzheng-road`, `location:nt-zhongzheng-road`, `location:tc-zhongshan-road`, `location:tn-zhongshan-road` | `fixture:road-name` |
| venues `location:tc-city-hall`, `location:tc-city-council`, `location:tc-railway-station`, `location:tc-national-theater`, `location:tc-rainbow-village`, `location:tc-intercontinental-baseball`, `location:tc-wuri-fishing-port` | `fixture:venue-name` |
| named-event occurrences `named_event:tc-new-year-eve-2026`, `named_event:tc-new-year-eve-2027`, `named_event:tc-council-regular-2026q4`, `named_event:tc-council-regular-2027q1` | `fixture:named-event-occurrence` |

Promoting any seeded row to a production mapping requires a reviewed dated
official source; until then it must not claim `official-source-catalog`
provenance. `test_shipped_seeded_rows_declare_fixture_provenance` enforces both
halves of that rule.

| Fixture | Rows | Expected result |
|---|---|---|
| Same road name across cities | `location:tc-zhongzheng-road` (臺中市) vs `location:nt-zhongzheng-road` (新北市), `location:tc-zhongshan-road` (臺中市) vs `location:tn-zhongshan-road` (臺南市) | unscoped → `AMBIGUOUS` with both IDs; jurisdiction-scoped → the scoped ID |
| Same event label, different dates | `named_event:tc-new-year-eve-2026` / `-2027`, `named_event:tc-council-regular-2026q4` / `-2027q1` | dated → distinct IDs; undated → `AMBIGUOUS`; unlisted date → `NO_MATCH` |
| Acronym / alias collision | guarded at validation: a name claimed twice inside one `(kind, jurisdiction, date)` raises `alias collision` | fail closed, no silent overwrite |
| `台`/`臺` and administrative suffixes | `台中市警局` → `agency:tc-police`, `台中市西屯區` / `西屯` → `location:tc-xitun` | one deterministic ID |

Agency and district fixtures resolve the shipped aliases `中市警`,
`中市交通局`, `中市消防局`, `中市議會`, `中市研考會`, `研考會`, `中市新聞局`,
all 29 districts plus the city row, and the road and venue names 臺灣大道,
台74線, 臺中市政府, 臺中市議會, 臺中車站, 臺中國家歌劇院, 彩虹眷村,
臺中洲際棒球場, 梧棲漁港. `中正路` and `中山路` resolve only under a
jurisdiction; unscoped they stay `AMBIGUOUS` as shown above. Short forms that
are not shipped aliases — for example `市府` or `臺中警政` — resolve to
`NO_MATCH` by design.

## Manual merge / split / correction

`correct_alias()`, `merge_entities()`, `split_entity()`, and
`revert_manual_change()` each return a new registry with `registry_version + 1`
and an append-only `audit_history` entry hashed from
`sequence/action/operator/at/payload`. Only these four helpers append audit
records; a seeded fixture revision changes content and version without an audit
entry. The input registry is never mutated.

- Original alias evidence is preserved, never deleted. A split must partition
  every original name, and each child keeps only the alias evidence for the
  names it claims.
- Merge and split retire the old ID into `redirects`, so `resolve_entity_id()`
  still answers for historical IDs and the change stays reversible.
- A tampered audit payload fails validation with `audit hash mismatch`.
- Only `CONFIRMED` rows may sit in the canonical registry, so an unreviewed
  alias can never become a canonical mapping.

## Verification

```sh
python -X utf8 -m unittest discover -s tests -p test_entity_registry.py -v
python -X utf8 scripts/entity-registry.py --self-check
python -X utf8 scripts/entity-registry.py --kind agency --text 中市警 --jurisdiction 臺中市
python -X utf8 scripts/entity-registry.py --kind named_event --text 臺中市議會定期會 --jurisdiction 臺中市 --event-date 2026-10-06
python -X utf8 scripts/public-event-fusion.py --self-check
```

`npm test` runs the suite through `apps/web/tests/entity-registry.test.mjs`, so
the registry guard runs on every verification gate and in CI.

## Local acceptance mapping (2026-10-17)

| #31 criterion | Mechanism / regression |
|---|---|
| Core police, government, traffic, fire, council fixture | `CORE_AGENCIES` in `test_entity_registry.py`; government is fixture-only, not a reviewed production alias. |
| District, road, venue normalization | All 29 districts and isolated road/venue fixtures; NFKC and 台/臺 normalization. |
| Three police labels resolve to one ID | `test_core_police_aliases_resolve_to_one_id`. |
| Cross-city roads stay distinct | Scoped road fixtures plus `test_direct_entity_ids_respect_explicit_document_jurisdiction`; existing agency/location/district IDs also respect explicit document scope. |
| Same-name events on different dates stay distinct | Dated named-event fixtures and fusion regressions. |
| #24/#27/#29 share version/hash | `test_default_fusion_cli_discovery_web_and_mcp_share_registry_identity` runs the CLI, discovery adapter, event store and Python Web/MCP gateway. This is synthetic local acceptance. |
| Manual edits preserve audit/evidence | Correction, merge/split chains and reverts retain snapshots and reject tampered history. |
| Unconfirmed aliases never become canonical | Canonical rows require `CONFIRMED`; unresolved labels fail closed and discovery candidates retain `DISCOVERY_UNVERIFIED` / `canonical_write=false`. |

Official per-alias source review, production named-event evidence, deployed
consumer bindings and a live end-to-end receipt remain unverified. These local
checks do not close those acceptance gates.

The relevant suites now contain 127 tests (125 before this change), all passing
with exit 0 under Git Bash:

```sh
python -X utf8 -m unittest discover -s tests -p test_entity_registry.py -v &&
python -X utf8 -m unittest discover -s tests -p test_public_event_fusion.py -v &&
python -X utf8 -m unittest discover -s tests -p test_discovery_adapter.py -v &&
python -X utf8 -m unittest discover -s tests -p test_query_domain.py -v &&
python -X utf8 -m unittest discover -s tests -p test_query_gateway_domain.py -v
```

The two registry/fusion Node wrappers pass all 4 checks, and
`node scripts/verify-project.mjs specs` passes (`secrets=0`). No dedicated lint,
coverage ratchet, CRAP or mutation gate is configured. The full `npm test` sweep
exits 1: Node reports 798/800 passing with two `PDF_PARSER_UNAVAILABLE` failures;
nine Python groups fail, including Windows `os.O_NONBLOCK` incompatibilities;
the migration self-check cannot import the uninstalled `pg` package. Those
unmodified paths remain separate from this issue's passing suites; no full-suite,
web-build or production acceptance is claimed.

## Non-goals

No knowledge graph, no LLM-created canonical entities, no person registry, and
no district-level guessing of an exact address or road.
