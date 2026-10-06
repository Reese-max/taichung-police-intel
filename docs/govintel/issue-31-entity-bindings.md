# Issue 31: shared entity bindings

The canonical registry is `config/entity-registry.v1.json`, interpreted by
`scripts/entity-registry.py`. Agency, Location, and dated NamedEvent labels
resolve only through active IDs or exact approved aliases. Exact IDs respect
kind, jurisdiction, and date. Ambiguous and unknown labels have no canonical
mapping. Manual corrections, merges, splits, redirects, and reverts remain
audited operations; discovery or query code cannot perform them.

## Consumer boundaries

| Consumer | Binding | Limit |
| --- | --- | --- |
| #24 PublicEvent fusion | Binds agency/location labels and dated named-event labels before fusion; emits `entity_registry` version/hash on each event. | A named event must have explicit jurisdiction and date; an official reschedule still needs a stable occurrence ID or reviewed relation. |
| #27 discovery adapter | Annotates radar candidates with approved agency/location IDs and a registry receipt. Same feed generation is rebound when the registry changes. | Candidate IDs are never official evidence, an official match key, or a canonical write. Ambiguous hints remain unresolved. |
| #29 optional Python event gateway | Requires one uniform event-store registry receipt; resolves query aliases against that exact registry for Web and MCP. | A missing binding accepts only literal IDs; a stale binding or unknown alias fails closed. The production Worker and statistics store do not yet use this entity contract. |

The synthetic fixture `tests/fixtures/entity-registry/false-positives.v1.json`
covers two cities sharing a road name, an acronym collision across
jurisdictions, 台/臺 normalization, and two events with the same name on
different dates. It is test data and supplies no production alias evidence.

## Remaining acceptance work

Before closing the issue, review each production alias with an official
document version and locator, add canonical named-event occurrences from
confirmed official evidence, connect any deployed #24/#27/#29 consumers to
the same published registry artifact, and observe a live end-to-end receipt.
The current production Worker exposes metadata tools only, so this branch
does not claim deployed entity-aware event queries.
