-- D1: ingestion, provenance, validation, and twice-daily run contract.
-- Apply with a transaction-capable migration runner or psql --single-transaction.

CREATE TABLE sources (
    source_id TEXT PRIMARY KEY CHECK (source_id ~ '^S-[0-9]{3}$'),
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    evidence_role TEXT NOT NULL CHECK (
        evidence_role IN ('PRIMARY_OFFICIAL', 'SECONDARY_OFFICIAL', 'DISCOVERY_ONLY')
    ),
    product_role TEXT NOT NULL CHECK (
        product_role IN (
            'PREP_CORE', 'TREND_SIGNAL', 'POLICY_UPSTREAM',
            'ANALYTIC_EVIDENCE', 'DISCOVERY_ONLY', 'CONTEXT_ONLY'
        )
    ),
    integration_status TEXT NOT NULL CHECK (
        integration_status IN ('ACTIVE', 'FIXTURE_ONLY', 'LIMITED', 'DEFERRED', 'GAP')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE collection_runs (
    collection_run_id TEXT PRIMARY KEY CHECK (btrim(collection_run_id) <> ''),
    slot_date DATE NOT NULL,
    slot TEXT NOT NULL CHECK (slot IN ('MORNING', 'EVENING', 'MANUAL')),
    timezone TEXT NOT NULL DEFAULT 'Asia/Taipei' CHECK (timezone = 'Asia/Taipei'),
    scheduled_for TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('RUNNING', 'SUCCEEDED', 'PARTIAL', 'FAILED')),
    CHECK (started_at >= scheduled_for),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((status = 'RUNNING') = (finished_at IS NULL))
);

CREATE UNIQUE INDEX collection_runs_scheduled_slot_once
    ON collection_runs (slot_date, slot)
    WHERE slot IN ('MORNING', 'EVENING');

CREATE TABLE source_runs (
    source_run_id TEXT PRIMARY KEY CHECK (btrim(source_run_id) <> ''),
    collection_run_id TEXT NOT NULL REFERENCES collection_runs(collection_run_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    attempted_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL,
    source_health TEXT NOT NULL CHECK (
        source_health IN ('PASS', 'DEGRADED', 'FAILED', 'QUARANTINED', 'NOT_RUN')
    ),
    window_start TIMESTAMPTZ NOT NULL,
    window_end TIMESTAMPTZ NOT NULL,
    window_completeness TEXT NOT NULL CHECK (
        window_completeness IN ('COMPLETE_WITH_ITEMS', 'COMPLETE_ZERO', 'PARTIAL', 'NOT_RUN')
    ),
    result TEXT NOT NULL CHECK (
        result IN ('NEW_ITEMS', 'NO_NEW_ITEM', 'PARTIAL', 'FAILED', 'NOT_RUN')
    ),
    item_count INTEGER CHECK (item_count >= 0),
    change_count INTEGER CHECK (change_count >= 0),
    http_status INTEGER CHECK (http_status BETWEEN 100 AND 599),
    latency_ms INTEGER CHECK (latency_ms >= 0),
    manifest_sha256 TEXT CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    previous_successful_source_run_id TEXT,
    error_code TEXT,
    error_message TEXT,
    UNIQUE (collection_run_id, source_id),
    UNIQUE (source_run_id, source_id),
    FOREIGN KEY (previous_successful_source_run_id, source_id)
        REFERENCES source_runs(source_run_id, source_id),
    CHECK (completed_at >= attempted_at),
    CHECK (window_end > window_start),
    CHECK (
        (result IN ('NEW_ITEMS', 'NO_NEW_ITEM', 'PARTIAL')
            AND item_count IS NOT NULL AND change_count IS NOT NULL)
        OR (result IN ('FAILED', 'NOT_RUN')
            AND item_count IS NULL AND change_count IS NULL)
    ),
    CHECK (
        result <> 'NO_NEW_ITEM'
        OR (change_count = 0 AND window_completeness IN ('COMPLETE_WITH_ITEMS', 'COMPLETE_ZERO'))
    ),
    CHECK (
        result <> 'NEW_ITEMS'
        OR (change_count > 0 AND window_completeness = 'COMPLETE_WITH_ITEMS')
    ),
    CHECK (
        result <> 'PARTIAL'
        OR window_completeness = 'PARTIAL'
    ),
    CHECK (
        result NOT IN ('NEW_ITEMS', 'NO_NEW_ITEM', 'PARTIAL')
        OR (source_health IN ('PASS', 'DEGRADED') AND manifest_sha256 IS NOT NULL)
    ),
    CHECK (
        result <> 'FAILED'
        OR (source_health IN ('FAILED', 'QUARANTINED') AND error_code IS NOT NULL)
    ),
    CHECK (
        result <> 'NOT_RUN'
        OR (source_health = 'NOT_RUN' AND window_completeness = 'NOT_RUN' AND error_code IS NOT NULL)
    )
);

CREATE TABLE raw_items (
    raw_item_id TEXT PRIMARY KEY CHECK (btrim(raw_item_id) <> ''),
    source_run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    stable_key TEXT NOT NULL CHECK (btrim(stable_key) <> ''),
    version_no INTEGER NOT NULL CHECK (version_no > 0),
    requested_url TEXT NOT NULL CHECK (requested_url ~ '^https?://'),
    final_url TEXT NOT NULL CHECK (final_url ~ '^https?://'),
    published_at TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    parser_version TEXT NOT NULL CHECK (btrim(parser_version) <> ''),
    snapshot_locator TEXT NOT NULL CHECK (btrim(snapshot_locator) <> ''),
    supersedes_raw_item_id TEXT REFERENCES raw_items(raw_item_id),
    is_current BOOLEAN NOT NULL DEFAULT true,
    FOREIGN KEY (source_run_id, source_id)
        REFERENCES source_runs(source_run_id, source_id),
    UNIQUE (source_id, stable_key, version_no),
    UNIQUE (source_id, stable_key, content_sha256),
    CHECK (supersedes_raw_item_id IS NULL OR supersedes_raw_item_id <> raw_item_id)
);

CREATE UNIQUE INDEX raw_items_one_current_version
    ON raw_items (source_id, stable_key)
    WHERE is_current;

CREATE TABLE events (
    event_id TEXT PRIMARY KEY CHECK (btrim(event_id) <> ''),
    raw_item_id TEXT NOT NULL REFERENCES raw_items(raw_item_id),
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'AGENDA', 'PROPOSAL', 'RESOLUTION', 'ORAL_ANSWER', 'WRITTEN_REPLY',
            'PROJECT_REPORT', 'POLICY_STAGE', 'PROCUREMENT_STAGE', 'STATISTIC_RELEASE'
        )
    ),
    change_type TEXT NOT NULL CHECK (
        change_type IN ('NEW', 'REVISED', 'REMOVED', 'DEADLINE_CHANGED', 'STATUS_CHANGED')
    ),
    date_status TEXT NOT NULL CHECK (date_status IN ('KNOWN', 'UNVERIFIED_DATE')),
    occurred_at TIMESTAMPTZ,
    detected_at TIMESTAMPTZ NOT NULL,
    previous_event_id TEXT REFERENCES events(event_id),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK ((date_status = 'KNOWN') = (occurred_at IS NOT NULL)),
    CHECK (previous_event_id IS NULL OR previous_event_id <> event_id)
);

CREATE TABLE validation_runs (
    validation_run_id TEXT PRIMARY KEY CHECK (btrim(validation_run_id) <> ''),
    run_role TEXT NOT NULL CHECK (run_role IN ('PRODUCER', 'VERIFIER')),
    model TEXT NOT NULL CHECK (btrim(model) <> ''),
    prompt_version TEXT NOT NULL CHECK (btrim(prompt_version) <> ''),
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
    decision TEXT NOT NULL CHECK (
        decision IN ('AUTO_PASS', 'AUTO_RETRY', 'AI_DISAGREEMENT', 'CLAIM_REJECTED', 'QUARANTINED')
    ),
    reason_codes TEXT[] NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ NOT NULL,
    CHECK (finished_at >= started_at)
);

CREATE TABLE claims (
    claim_id TEXT PRIMARY KEY CHECK (btrim(claim_id) <> ''),
    event_id TEXT NOT NULL REFERENCES events(event_id),
    claim_type TEXT NOT NULL CHECK (
        claim_type IN ('FACT', 'STATUS', 'DEADLINE', 'NEXT_STEP', 'AI_SYNTHESIS')
    ),
    claim_text TEXT NOT NULL CHECK (btrim(claim_text) <> ''),
    verification_status TEXT NOT NULL CHECK (
        verification_status IN (
            'AUTO_PASS', 'AUTO_RETRY', 'AI_DISAGREEMENT', 'CLAIM_REJECTED', 'QUARANTINED'
        )
    ),
    producer_run_id TEXT NOT NULL REFERENCES validation_runs(validation_run_id),
    verifier_run_id TEXT NOT NULL REFERENCES validation_runs(validation_run_id),
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (producer_run_id <> verifier_run_id)
);

CREATE TABLE evidence (
    evidence_id TEXT PRIMARY KEY CHECK (btrim(evidence_id) <> ''),
    claim_id TEXT NOT NULL REFERENCES claims(claim_id),
    raw_item_id TEXT NOT NULL REFERENCES raw_items(raw_item_id),
    evidence_type TEXT NOT NULL CHECK (
        evidence_type IN (
            'OFFICIAL_TEXT', 'OFFICIAL_DOCUMENT', 'OFFICIAL_AUDIO', 'GROQ_ASR_DERIVATIVE'
        )
    ),
    locator_type TEXT NOT NULL CHECK (
        locator_type IN ('WEB_FRAGMENT', 'PDF_PAGE', 'AUDIO_TIMESTAMP', 'ASR_SEGMENT')
    ),
    locator TEXT NOT NULL CHECK (btrim(locator) <> ''),
    original_url TEXT NOT NULL CHECK (original_url ~ '^https?://'),
    content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (claim_id, raw_item_id, locator)
);

CREATE TABLE gaps (
    gap_id TEXT PRIMARY KEY CHECK (gap_id ~ '^GAP-[0-9]{3}$'),
    source_id TEXT REFERENCES sources(source_id),
    related_event_id TEXT REFERENCES events(event_id),
    title TEXT NOT NULL CHECK (btrim(title) <> ''),
    status TEXT NOT NULL CHECK (
        status IN ('OPEN', 'UNVERIFIED_AFTER_MEETING', 'RESOLVED', 'NOT_AVAILABLE')
    ),
    detail TEXT NOT NULL CHECK (btrim(detail) <> ''),
    first_observed_at TIMESTAMPTZ NOT NULL,
    last_checked_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    CHECK (last_checked_at >= first_observed_at),
    CHECK ((status = 'RESOLVED') = (resolved_at IS NOT NULL))
);

CREATE INDEX source_runs_source_time_idx ON source_runs (source_id, completed_at DESC);
CREATE INDEX events_detected_at_idx ON events (detected_at DESC);
CREATE INDEX claims_status_idx ON claims (verification_status, created_at DESC);
