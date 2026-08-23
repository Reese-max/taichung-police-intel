-- D2 production: deduplicated source bytes plus per-run observations.

CREATE TABLE snapshot_blobs (
    content_sha256 TEXT PRIMARY KEY CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    content_type TEXT NOT NULL CHECK (btrim(content_type) <> ''),
    byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
    body BYTEA NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CHECK (octet_length(body) = byte_count)
);

CREATE TABLE source_snapshots (
    snapshot_id TEXT PRIMARY KEY CHECK (btrim(snapshot_id) <> ''),
    source_run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    purpose TEXT NOT NULL CHECK (purpose IN ('LIST', 'API', 'ATTACHMENT', 'DETAIL')),
    requested_url TEXT NOT NULL CHECK (requested_url ~ '^https?://'),
    final_url TEXT NOT NULL CHECK (final_url ~ '^https?://'),
    http_status INTEGER NOT NULL CHECK (http_status BETWEEN 100 AND 599),
    fetched_at TIMESTAMPTZ NOT NULL,
    content_sha256 TEXT NOT NULL REFERENCES snapshot_blobs(content_sha256),
    FOREIGN KEY (source_run_id, source_id)
        REFERENCES source_runs(source_run_id, source_id),
    UNIQUE (source_run_id, requested_url, content_sha256)
);

CREATE INDEX source_snapshots_source_time_idx
    ON source_snapshots (source_id, fetched_at DESC);
