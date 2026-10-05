"""Existing collector freshness rules, shared without changing thresholds."""

SOURCE_FRESHNESS_POLICY: dict[str, tuple[float, float]] = {
    "S-004": (24 * 45, 24 * 90),
    "S-006": (24 * 14, 24 * 30),
    "S-007": (24 * 90, 24 * 180),
    "S-009": (24 * 14, 24 * 60),
    "S-029": (24 * 45, 24 * 90),
    "S-001": (13, 24),
    "S-019": (36, 72),
    "S-032": (13, 24),
}
QUERY_SNAPSHOT_MAX_AGE_SECONDS = 16 * 60 * 60
