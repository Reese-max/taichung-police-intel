-- D4: bounded request budget ledger for detail rechecks.
-- The ledger only records admission decisions (attempt timestamps, backoff
-- window, retry deadline).  It never stores document content, so a deferred
-- or blocked recheck stays inspectable without re-crawling the source.

ALTER TABLE detail_recheck_state
    ADD COLUMN budget_state JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(budget_state) = 'object');

CREATE INDEX detail_recheck_pending_retry_idx
    ON detail_recheck_state (source_id, stable_key)
    WHERE budget_state->>'deferred_until' IS NOT NULL
       OR budget_state->>'deadline_at' IS NOT NULL;
