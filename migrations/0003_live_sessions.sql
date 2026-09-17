-- Issue #13: bounded opt-in live-meeting sessions, provisional ASR segments,
-- visible gap intervals, WATCH bookmarks, and post-event reconciliation receipts.
-- Mirrors the versioned contract in apps/web/lib/live-session.js (schema_version 1).
-- Live ASR text is derived navigation only: provisional segments may never
-- satisfy the formal evidence contract until a receipt confirms them against
-- official post-event sources.

CREATE TABLE live_sessions (
    session_id TEXT PRIMARY KEY CHECK (session_id ~ '^[A-Za-z0-9][A-Za-z0-9-]{3,63}$'),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    status TEXT NOT NULL CHECK (
        status IN ('PREPARING', 'LIVE', 'DEGRADED', 'ENDED', 'RECONCILING', 'RECONCILED', 'FAILED')
    ),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    stream_url TEXT NOT NULL CHECK (stream_url ~ '^https://'),
    official_page_url TEXT NOT NULL CHECK (official_page_url ~ '^https://'),
    meeting_id TEXT,
    meeting_title TEXT,
    agenda_url TEXT CHECK (agenda_url IS NULL OR agenda_url ~ '^https://'),
    asr_provider TEXT NOT NULL CHECK (btrim(asr_provider) <> ''),
    asr_model TEXT NOT NULL CHECK (btrim(asr_model) <> ''),
    asr_model_version TEXT,
    asr_language TEXT,
    seek_kind TEXT NOT NULL CHECK (seek_kind IN ('STREAM_TIME', 'NONE')),
    seek_base_url TEXT CHECK (seek_base_url IS NULL OR seek_base_url ~ '^https://'),
    seek_offset_seconds DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (seek_offset_seconds >= 0),
    requested_by TEXT NOT NULL CHECK (btrim(requested_by) <> ''),
    requested_at TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    ingest_started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    end_reason TEXT,
    max_duration_seconds INTEGER NOT NULL CHECK (max_duration_seconds BETWEEN 1 AND 21600),
    max_provider_seconds INTEGER CHECK (max_provider_seconds IS NULL OR max_provider_seconds > 0),
    provider_call_count INTEGER NOT NULL DEFAULT 0 CHECK (provider_call_count >= 0),
    provider_seconds_used DOUBLE PRECISION NOT NULL DEFAULT 0 CHECK (provider_seconds_used >= 0),
    supersedes_session_id TEXT REFERENCES live_sessions(session_id),
    revision_no INTEGER NOT NULL DEFAULT 0 CHECK (revision_no >= 0),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CHECK (ended_at IS NULL OR ended_at >= started_at),
    CHECK (seek_kind <> 'STREAM_TIME' OR seek_base_url IS NOT NULL),
    CHECK (supersedes_session_id IS NULL OR supersedes_session_id <> session_id)
);

CREATE TABLE live_segments (
    segment_id TEXT PRIMARY KEY CHECK (btrim(segment_id) <> ''),
    session_id TEXT NOT NULL REFERENCES live_sessions(session_id),
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    t_start_seconds DOUBLE PRECISION NOT NULL CHECK (t_start_seconds >= 0),
    t_end_seconds DOUBLE PRECISION NOT NULL,
    current_revision INTEGER NOT NULL CHECK (current_revision > 0),
    language TEXT,
    speaker_label TEXT,
    -- JS shape is {value, kind:"PROVIDER_REPORTED"}; the kind column keeps the
    -- "provider-reported, not truth probability" contract explicit.
    provider_confidence DOUBLE PRECISION CHECK (
        provider_confidence IS NULL OR provider_confidence BETWEEN 0 AND 1
    ),
    provider_confidence_kind TEXT CHECK (
        provider_confidence_kind IS NULL OR provider_confidence_kind = 'PROVIDER_REPORTED'
    ),
    is_partial BOOLEAN NOT NULL DEFAULT false,
    -- Always provisional: never ORAL_OFFICIAL/AUTO_PASS before reconciliation.
    evidence_state TEXT NOT NULL CHECK (evidence_state = 'PROVISIONAL'),
    content_label TEXT NOT NULL CHECK (content_label = 'LIVE_ASR_PROVISIONAL'),
    verification_status TEXT NOT NULL CHECK (verification_status = 'PROVISIONAL'),
    received_at TIMESTAMPTZ NOT NULL,
    finalized_at TIMESTAMPTZ,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    UNIQUE (session_id, sequence),
    CHECK (t_end_seconds > t_start_seconds),
    CHECK ((provider_confidence IS NULL) = (provider_confidence_kind IS NULL))
);

CREATE TABLE live_segment_revisions (
    segment_id TEXT NOT NULL REFERENCES live_segments(segment_id),
    revision_no INTEGER NOT NULL CHECK (revision_no > 0),
    kind TEXT NOT NULL CHECK (kind IN ('INTERIM', 'FINAL')),
    text TEXT NOT NULL CHECK (btrim(text) <> ''),
    speaker_label TEXT,
    provider_confidence DOUBLE PRECISION CHECK (
        provider_confidence IS NULL OR provider_confidence BETWEEN 0 AND 1
    ),
    provider_confidence_kind TEXT CHECK (
        provider_confidence_kind IS NULL OR provider_confidence_kind = 'PROVIDER_REPORTED'
    ),
    received_at TIMESTAMPTZ NOT NULL,
    finalized_at TIMESTAMPTZ,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY (segment_id, revision_no),
    CHECK ((provider_confidence IS NULL) = (provider_confidence_kind IS NULL))
);

CREATE TABLE live_gap_intervals (
    gap_id TEXT PRIMARY KEY CHECK (btrim(gap_id) <> ''),
    session_id TEXT NOT NULL REFERENCES live_sessions(session_id),
    reason TEXT NOT NULL CHECK (
        reason IN (
            'STREAM_DISCONNECT', 'ASR_OUTAGE', 'PROVIDER_TIMEOUT', 'RATE_LIMIT',
            'BUDGET_STOP', 'MAX_DURATION_REACHED', 'SESSION_CRASH'
        )
    ),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    t_start_seconds DOUBLE PRECISION NOT NULL CHECK (t_start_seconds >= 0),
    t_end_seconds DOUBLE PRECISION,
    open BOOLEAN NOT NULL DEFAULT true,
    detail TEXT,
    CHECK (ended_at IS NULL OR ended_at > started_at),
    CHECK (t_end_seconds IS NULL OR t_end_seconds > t_start_seconds),
    CHECK (open = (ended_at IS NULL))
);

-- At most one open gap per session.
CREATE UNIQUE INDEX live_gap_intervals_one_open
    ON live_gap_intervals (session_id)
    WHERE open;

CREATE TABLE live_bookmarks (
    bookmark_id TEXT PRIMARY KEY CHECK (bookmark_id ~ '^BMK-[0-9A-F]{12}$'),
    session_id TEXT NOT NULL REFERENCES live_sessions(session_id),
    kind TEXT NOT NULL CHECK (kind = 'WATCH'),
    segment_ids TEXT[] NOT NULL CHECK (array_length(segment_ids, 1) >= 1),
    t_start_seconds DOUBLE PRECISION NOT NULL,
    t_end_seconds DOUBLE PRECISION NOT NULL,
    segment_hashes JSONB NOT NULL,
    session_revision INTEGER NOT NULL,
    reason_code TEXT NOT NULL CHECK (
        reason_code IN ('MANUAL_WATCH', 'PROFILE_MATCH', 'AGENDA_TERM', 'FOLLOW_UP')
    ),
    profile_id TEXT,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (t_end_seconds > t_start_seconds)
);

CREATE TABLE reconciliation_receipts (
    receipt_id TEXT PRIMARY KEY CHECK (btrim(receipt_id) <> ''),
    receipt_no INTEGER NOT NULL CHECK (receipt_no > 0),
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    session_id TEXT NOT NULL REFERENCES live_sessions(session_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    outcome TEXT NOT NULL CHECK (outcome IN ('COMPLETE', 'PENDING_SOURCES')),
    reconciled_at TIMESTAMPTZ NOT NULL,
    reconciler_version TEXT NOT NULL CHECK (btrim(reconciler_version) <> ''),
    parser_version TEXT NOT NULL CHECK (btrim(parser_version) <> ''),
    asr_provider TEXT NOT NULL,
    asr_model TEXT NOT NULL,
    asr_model_version TEXT,
    post_event_sources JSONB NOT NULL DEFAULT '[]'::jsonb,
    session_content_sha256 TEXT NOT NULL CHECK (session_content_sha256 ~ '^[0-9a-f]{64}$'),
    supersedes_receipt_id TEXT REFERENCES reconciliation_receipts(receipt_id),
    stale BOOLEAN NOT NULL DEFAULT false,
    stale_reason TEXT CHECK (stale_reason IS NULL OR stale_reason = 'SOURCE_REVISED'),
    stale_marked_at TIMESTAMPTZ,
    UNIQUE (session_id, receipt_no),
    CHECK (supersedes_receipt_id IS NULL OR supersedes_receipt_id <> receipt_id),
    CHECK (stale = (stale_reason IS NOT NULL))
);

CREATE TABLE reconciliation_entries (
    entry_id TEXT PRIMARY KEY CHECK (btrim(entry_id) <> ''),
    receipt_id TEXT NOT NULL REFERENCES reconciliation_receipts(receipt_id),
    bookmark_id TEXT NOT NULL REFERENCES live_bookmarks(bookmark_id),
    segment_ids TEXT[] NOT NULL CHECK (array_length(segment_ids, 1) >= 1),
    status TEXT NOT NULL CHECK (
        status IN (
            'CONFIRMED_BY_OFFICIAL_MEDIA', 'CONFIRMED_BY_MINUTES',
            'SUPERSEDED_TRANSCRIPT', 'UNRESOLVED',
            'SOURCE_NOT_YET_AVAILABLE', 'DROPPED_FALSE_POSITIVE'
        )
    ),
    provisional_t_start_seconds DOUBLE PRECISION NOT NULL,
    provisional_t_end_seconds DOUBLE PRECISION NOT NULL,
    before_transcript_sha256 TEXT NOT NULL CHECK (before_transcript_sha256 ~ '^[0-9a-f]{64}$'),
    after_transcript_sha256 TEXT CHECK (
        after_transcript_sha256 IS NULL OR after_transcript_sha256 ~ '^[0-9a-f]{64}$'
    ),
    official_locator TEXT,
    official_url TEXT CHECK (official_url IS NULL OR official_url ~ '^https://'),
    source_kind TEXT CHECK (source_kind IS NULL OR source_kind IN ('VOD', 'MINUTES', 'AGENDA')),
    note TEXT,
    reconciled_at TIMESTAMPTZ NOT NULL,
    CHECK (provisional_t_end_seconds > provisional_t_start_seconds),
    -- Confirmed/superseded entries must cite an official locator; dropped and
    -- unresolved entries must not fabricate one.
    CHECK (
        (status IN ('CONFIRMED_BY_OFFICIAL_MEDIA', 'CONFIRMED_BY_MINUTES', 'SUPERSEDED_TRANSCRIPT')
            AND official_locator IS NOT NULL AND official_url IS NOT NULL)
        OR (status IN ('UNRESOLVED', 'SOURCE_NOT_YET_AVAILABLE', 'DROPPED_FALSE_POSITIVE'))
    ),
    CHECK (
        status <> 'SUPERSEDED_TRANSCRIPT'
        OR (after_transcript_sha256 IS NOT NULL
            AND after_transcript_sha256 <> before_transcript_sha256)
    )
);

CREATE INDEX live_segments_session_time_idx ON live_segments (session_id, t_start_seconds);
CREATE INDEX live_gap_intervals_session_idx ON live_gap_intervals (session_id, t_start_seconds);
CREATE INDEX reconciliation_entries_receipt_idx ON reconciliation_entries (receipt_id);
