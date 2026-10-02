"""Shared policy for the Issue #22 CANDIDATE-only Pages observation lane."""
from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
# Keep this issue's publication scope bounded. S-031 and S-033 remain observation
# candidates in the canary workflow but are intentionally not part of the Pages lane.
CANDIDATE_PUBLICATION_SOURCE_IDS = ("S-001", "S-019", "S-032")
CANDIDATE_CATALOG_STATUSES = {"AUDITED_EXISTING", "VERIFIED_CANDIDATE"}


def load_candidate_publication_sources() -> dict[str, dict]:
    """Return only the explicit Issue #22 scope after checking the source catalog."""
    policy_path = ROOT / "scripts" / "source-policy.py"
    spec = importlib.util.spec_from_file_location("issue22_source_policy", policy_path)
    if spec is None or spec.loader is None:
        raise ValueError("source policy module is unavailable")
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    catalog = policy.load_catalog()
    rows = {row["source_id"]: row for row in catalog["sources"]}
    missing = set(CANDIDATE_PUBLICATION_SOURCE_IDS) - set(rows)
    if missing:
        raise ValueError(f"candidate publication IDs are absent from source catalog: {sorted(missing)}")
    active = {row["source_id"] for row in catalog["sources"] if row["status"] == "PRODUCTION_ACTIVE"}
    invalid = {
        source_id
        for source_id in CANDIDATE_PUBLICATION_SOURCE_IDS
        if rows[source_id]["status"] not in CANDIDATE_CATALOG_STATUSES or source_id in active
    }
    if invalid:
        raise ValueError(f"candidate publication scope is active or unapproved: {sorted(invalid)}")
    return {source_id: rows[source_id] for source_id in CANDIDATE_PUBLICATION_SOURCE_IDS}
