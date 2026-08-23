# Source ingestion and provenance requirements

## Goal

Create a reproducible P0 ingestion path for official council sources without losing provenance or rewriting failures as empty results.

## Requirements

### R1 — Raw provenance

WHEN an official item is fetched THE SYSTEM SHALL preserve its source ID, requested URL, final URL, fetched time, raw content SHA-256, and parser version before deriving claims.

### R2 — Window truth

WHEN a collection window completes THE SYSTEM SHALL record source health and window completeness as separate fields.

WHEN a fetch or parse fails THE SYSTEM SHALL retain an explicit failure state and the last successful snapshot instead of reporting zero items.

WHEN any P0 source run finishes THE SYSTEM SHALL expose current source health, window completeness, `FRESH`／`STALE`／`VERY_STALE`／`NO_DATA`, data-as-of time, next update time, and the last-known-good snapshot independently through the public status API.

WHEN a structurally valid source snapshot has `PASS` or `DEGRADED` health and a manifest THE SYSTEM SHALL retain it as last-known-good even if date coverage remains `PARTIAL`, while still exposing `WINDOW_PARTIAL`.

WHEN a source fails, has a partial window, has no last-known-good snapshot, or serves stale data THE SYSTEM SHALL emit a machine-readable intelligence-gap reason without replacing the formal domain gap `GAP-001`.

### R3 — P0 scope

WHEN the hackathon collector runs THE SYSTEM SHALL cover S-004, S-006, S-007, and S-009 plus a fixed verified S-029 fixture before any new source is added.

### R4 — Deduplication and versions

WHEN identical source bytes or stable item keys reappear THE SYSTEM SHALL suppress exact duplicates while retaining observation history and version relationships.

### R5 — Independent validation

WHEN a claim is generated THE SYSTEM SHALL assign distinct producer and verifier run IDs and require the verifier to read the bound original evidence.

WHEN deterministic or verifier checks disagree THE SYSTEM SHALL retry at most once and then quarantine the item without replacing the last `AUTO_PASS` snapshot.

### R6 — Twice-daily freshness

AT 06:30 and 18:30 `Asia/Taipei` THE SYSTEM SHALL create at most one scheduled `collection_run` per slot and one `source_run` per attempted source.

WHEN a scheduled source run finishes THE SYSTEM SHALL record scheduled, attempted, and completed times plus `NEW_ITEMS`, `NO_NEW_ITEM`, `PARTIAL`, `FAILED`, or `NOT_RUN` without treating an incomplete or failed fetch as an empty window.

### R7 — Change events

WHEN a stable item changes THE SYSTEM SHALL emit `NEW`, `REVISED`, `REMOVED`, `DEADLINE_CHANGED`, or `STATUS_CHANGED` and preserve the prior raw item or event relationship.

### R8 — Acceptance

WHEN a fixed fixture is collected twice THE SYSTEM SHALL produce identical normalized manifests, no duplicate current items, and isolated failure evidence for a deliberately broken source.

WHEN the deliberately broken source had a prior complete success THE SYSTEM SHALL keep serving the same last-known-good manifest, mark current health as failed, and expose `SOURCE_FAILED` while the other P0 sources finish normally.

WHEN the D1 contract is tested THE SYSTEM SHALL reject missing URLs, hashes, evidence locators, duplicate scheduled slots, and identical producer/verifier run IDs.

WHEN D2 is accepted in competition production THE SYSTEM SHALL expose anonymous HTTPS static health and status endpoints, retain official URLs, manifest SHA-256, counts, intelligence gaps, and last-known-good in versioned `source-status.json`, and prove distinct Taipei `MORNING` and `EVENING` GitHub Actions runs without duplicate collection runs. PostgreSQL and replayable raw bytes remain the post-competition durable-history path and are not required for competition acceptance.
