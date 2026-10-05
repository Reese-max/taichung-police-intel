"""Catalog-derived governance. Hashes bind reviewed inputs, not legal approval."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
RETENTION_PATH = ROOT / "docs/govintel/retention-rights-policy.v1.json"
FRESHNESS_PATH = ROOT / "intel_v2/freshness_policy.py"
FORMAL_RIGHTS = {"VERIFIED_METADATA_PERMISSION", "OPEN_DATA_LICENSED"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise ValueError("governance input module unavailable")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def compile_governance(catalog):
    retention = module("source_governance_retention", ROOT / "scripts/retention-policy.py").compile_policy(
        catalog=catalog, policy=json.loads(RETENTION_PATH.read_text(encoding="utf-8")))
    freshness = module("source_governance_freshness", FRESHNESS_PATH)
    refs = {
        "retention_policy": {"path": "docs/govintel/retention-rights-policy.v1.json",
            "file_sha256": hashlib.sha256(RETENTION_PATH.read_bytes()).hexdigest(),
            "policy_version": retention["policy_version"], "policy_hash": retention["policy_hash"]},
        "freshness_rules": {"path": "intel_v2/freshness_policy.py",
            "file_sha256": hashlib.sha256(FRESHNESS_PATH.read_bytes()).hexdigest()},
        "catalog_hash": digest(catalog),
        "prohibited_public_fields": retention["prohibited_public_fields"],
        "formal_rights_statuses": sorted(FORMAL_RIGHTS),
        "query_snapshot_max_age_seconds": freshness.QUERY_SNAPSHOT_MAX_AGE_SECONDS,
        "derived_summary_policy": {**retention["classes"]["DERIVED_SUMMARY"],
            "policy_ref": {"policy_hash": retention["policy_hash"],
                           "file_sha256": hashlib.sha256(RETENTION_PATH.read_bytes()).hexdigest()}},
    }
    sources = []
    for row in sorted(catalog["sources"], key=lambda r: r["source_id"]):
        sid = row["source_id"]
        rights = retention["source_policies"][sid]
        url = urlsplit(row["entrypoint"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("governance origin must be credential-free HTTPS")
        thresholds = freshness.SOURCE_FRESHNESS_POLICY.get(sid)
        sources.append({
            "source_id": sid, "integration_status": row["status"],
            "original_source_identity": {"source_id": sid, "name": row["name"],
                "authority": row["authority"], "entrypoint": row["entrypoint"]},
            "approved_origins": [f"https://{url.netloc}"],
            "geographic_scope": {"status": "UNKNOWN", "declared_scope": None,
                "may_infer_citywide_coverage": False},
            "temporal_semantics": {"status": "UNKNOWN", "valid_time_semantics": None,
                "fetched_at_is_observation_only": True, "may_infer_event_valid_time": False,
                "catalog_collection_rule": row.get("collection_rule"), "catalog_evidence_rule": row.get("evidence_rule")},
            "freshness_policy": {"rule_ref": refs["freshness_rules"],
                "fresh_hours": thresholds[0] if thresholds else None,
                "stale_hours": thresholds[1] if thresholds else None,
                "rule_status": "EXISTING_CONTROLLED_RULE" if thresholds else "UNKNOWN",
                "query_snapshot_max_age_seconds": freshness.QUERY_SNAPSHOT_MAX_AGE_SECONDS,
                "cadence_class": row.get("cadence_class"),
                "collection_recency_is_not_event_or_world_completeness": True},
            "rights_retention_public_policy_refs": {**refs["retention_policy"], **rights,
                "raw_retention_window": retention["retention_windows"][rights["raw_retention_class"]],
                "canonical_retention_window": retention["retention_windows"][rights["canonical_retention_class"]],
                "may_claim_open_permission": False},
        })
    core = {**refs, "sources": sources}
    return {**core, "governance_hash": digest(core)}


def admission(policy):
    """Policy-owned formal admission; a legacy policy cannot authorize current use."""
    if policy.get("schema_version") != 2:
        return {"status": "UNKNOWN", "reason": "APPROVED_GOVERNANCE_MISSING",
                "governance_hash": None, "blocked_sources": list(policy["active_source_ids"]),
                "per_source": {sid: "APPROVED_GOVERNANCE_MISSING" for sid in policy["active_source_ids"]}}
    per_source = {}
    prohibited = set(policy["governance_binding"]["prohibited_public_fields"])
    for row in policy["active_sources"]:
        rights = row["rights_retention_public_policy_refs"]
        if rights["rights_status"] not in FORMAL_RIGHTS or rights["review_required"] is not False:
            per_source[row["source_id"]] = "RIGHTS_UNKNOWN_OR_UNREVIEWED"
        elif rights["full_text_allowed"] or rights["excerpt_allowed"] or set(rights["public_fields"]) & prohibited:
            per_source[row["source_id"]] = "PUBLIC_FIELDS_PROHIBITED"
    return {"status": "RIGHTS_BLOCKED" if per_source else "ADMITTED", "reason": None,
            "governance_hash": policy["governance_binding"]["governance_hash"],
            "blocked_sources": sorted(per_source), "per_source": per_source}


def brief_admission(policy, brief):
    """A metadata permission never grants publication of derived event facts."""
    if admission(policy)["status"] != "ADMITTED":
        return False
    summary = policy["governance_binding"]["derived_summary_policy"]
    if (summary["review_required"] is not False or summary["rights_status"] not in FORMAL_RIGHTS | {"PROJECT_CONTROLLED"}
            or summary["public_projection"] != "EVIDENCE_BOUND_SUMMARY_ONLY"):
        return False
    allowed = set(summary["public_fields"])
    def fields_allowed(value):
        if isinstance(value, dict):
            return set(value) <= allowed and all(fields_allowed(child) for child in value.values())
        if isinstance(value, list):
            return all(fields_allowed(child) for child in value)
        return True
    if not fields_allowed(brief):
        return False
    sources = {row["source_id"]: row for row in policy["active_sources"]}
    for key in ("priority_items", "tracking_items", "other_changes"):
        for row in brief.get(key) or []:
            source = sources.get(row.get("source_id"))
            if not source or source["rights_retention_public_policy_refs"]["public_projection"] != "EVIDENCE_BOUND_SUMMARY_ONLY":
                return False
            parsed = urlsplit(row.get("official_url") or "")
            if f"{parsed.scheme}://{parsed.netloc}" not in source["approved_origins"]:
                return False
    return True
