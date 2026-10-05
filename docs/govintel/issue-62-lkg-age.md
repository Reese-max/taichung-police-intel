# Saved source snapshot age

The collector's `last_known_good_age` and the source page's「最後已知成功快照距今」measure elapsed hours between `last_known_good.completed_at` and an observation time. The collector records its existing run observation time; the page recomputes the age at viewing time. Neither calculation updates the retained snapshot or any fetch, check, official-record or publication timestamp.

Missing, malformed, timezone-less or future completion times produce `UNKNOWN` with a null age. Legacy snapshots without `completed_at` remain unknown: `last_success_at`, `last_checked_at`, `data_as_of` and `generated_at` do not replace the missing clock. A newly successful fixture collection records `completed_at` using the same existing run completion/observation timestamp; a failed collection preserves the prior LKG unchanged.

Both clock lanes require UTC instants within years 1–9999; a timezone offset that normalizes outside this range is unknown. Completion strings retain all six supported fractional-second digits. The browser observation is a valid Date or an integer millisecond clock, so a one-microsecond future completion is still unknown rather than rounded to zero age.

This duration describes when acquisition succeeded. It does not classify official-data freshness, establish complete coverage or prove that there are no new events. Existing freshness thresholds, source policy, rights admission and public data remain unchanged. Upstream publication cadence remains unverified; the twice-daily collection schedule is not an official publication promise. This bounded source change does not establish the seven-day canary, official evidence query, human holdout or user-task requirements of issue #62.
