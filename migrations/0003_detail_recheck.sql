-- D3: bounded, opt-in detail-page recheck state.
-- The row is a scheduler cursor; raw bytes remain in snapshot_blobs and the
-- classification keeps the exact before/after hashes for downstream review.

CREATE TABLE detail_recheck_state (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    stable_key TEXT NOT NULL CHECK (btrim(stable_key) <> ''),
    requested_url TEXT NOT NULL CHECK (requested_url ~ '^https://'),
    last_checked_at TIMESTAMPTZ,
    next_check_at TIMESTAMPTZ NOT NULL,
    etag TEXT,
    last_modified TEXT,
    document_version_id TEXT,
    body_sha256 TEXT CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
    normalized_text_sha256 TEXT CHECK (normalized_text_sha256 ~ '^[0-9a-f]{64}$'),
    attachments JSONB NOT NULL DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'BASELINE' CHECK (
        status IN (
            'BASELINE', 'UNCHANGED', 'MATERIAL_CHANGE', 'ATTACHMENT_CHANGED',
            'PRESENTATION_ONLY', 'NOT_MODIFIED', 'DEFERRED', 'UNAVAILABLE'
        )
    ),
    review_required BOOLEAN NOT NULL DEFAULT false,
    preserve_last_known_good BOOLEAN NOT NULL DEFAULT true,
    event_cancelled BOOLEAN NOT NULL DEFAULT false,
    changed_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    last_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_snapshot_id TEXT REFERENCES source_snapshots(snapshot_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, stable_key),
    CHECK (
        (document_version_id IS NULL)
        = (body_sha256 IS NULL AND normalized_text_sha256 IS NULL)
    )
);

CREATE INDEX detail_recheck_due_idx
    ON detail_recheck_state (next_check_at, source_id, stable_key);
