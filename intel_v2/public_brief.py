"""Versioned, closed public projection of the existing canonical V2 brief."""
from __future__ import annotations

import math
from typing import Any


PUBLIC_BRIEF_PROJECTION_VERSION = 1


def _scalars(names: str) -> dict[str, None]:
    return dict.fromkeys(names.split())


# These are the fields emitted by the existing brief, role-profile and
# handoff/detail-recheck projections. None means a JSON scalar, not an
# unrestricted object. Every exposed object has its own closed field list.
_ATTACHMENT = _scalars("attachment_id content_sha256 etag last_modified")
_VERSION = {
    **_scalars("version normalized_sha256 official_url document_version_id body_sha256 "
               "normalized_text_sha256 published_at source_modified_at effective_at "
               "expires_at observed_at last_checked_at"),
    "attachments": [_ATTACHMENT],
}
_AFFECTED_CLAIM = _scalars("brief_id brief_version claim_id source_version "
                           "source_document_version evidence_locator")
_INVALIDATION = {
    **_scalars("invalidation_id event_id reason detected_at resolved_in_handoff_id"),
    "changed_fields": [None],
    "before": _VERSION,
    "after": _VERSION,
    "affected_claims": [_AFFECTED_CLAIM],
}
_PROFILE_RELEVANCE = {
    **_scalars("profile_id profile_version profile_hash ranking_policy_version label score"),
    "reason_codes": [None],
}
_ITEM = {
    **_scalars("event_id identity watch_id stable_key source_id source_name change_type "
               "headline what_changed why_it_matters recommended_action deadline "
               "temporal_basis time_basis date_status detected_at source_version "
               "source_sha256 source_document_version official_url verification_status "
               "evidence_status publication_tier tracking_id watch_status reason_code "
               "last_reviewed_version last_handoff_id source_health source_freshness"),
    "affected_roles": [None],
    "changed_fields": [None],
    "source_gaps": [None],
    "profile_relevance": _PROFILE_RELEVANCE,
    "invalidation": _INVALIDATION,
}
_BRIEF = {
    **_scalars("schema_version mode generator_version generated_at source_collection_run_id "
               "source_status_generated_at publication_status snapshot_complete status_message"),
    "overview": _scalars("archive_total current_change_count legacy_home_candidate_count "
                           "other_change_count priority_count tracking_count tracking_total"),
    "priority_items": [_ITEM],
    "tracking_items": [_ITEM],
    "other_changes": [_ITEM],
    "source_health": _scalars("status pass_count stale_count failed_count gap_count"),
}
_SOURCE = {
    **_scalars("source_id source_name source_url source_health window_completeness result "
               "freshness_status data_as_of last_checked_at last_success_at "
               "current_source_run_id manifest_sha256 data_as_of_basis data_as_of_scope"),
    "data_as_of_evidence": _scalars("date_basis document_revision_at official_url content_sha256 page_number"),
    "intelligence_gaps": [None],
}


def _closed_value(value: Any, shape: Any) -> Any:
    if value is None:
        return None
    if isinstance(shape, dict):
        if not isinstance(value, dict) or set(value) - set(shape):
            raise ValueError("canonical brief violates the closed public projection")
        return {key: _closed_value(item, shape[key]) for key, item in value.items()}
    if isinstance(shape, list):
        if not isinstance(value, list):
            raise ValueError("canonical brief violates the closed public projection")
        return [_closed_value(item, shape[0]) for item in value]
    if type(value) not in (str, bool, int, float) or (
        isinstance(value, float) and not math.isfinite(value)
    ):
        raise ValueError("canonical brief violates the closed public projection")
    return value


def project_public_brief(brief: dict[str, Any]) -> dict[str, Any]:
    """Keep the existing root slice; reject unknown nested fields and objects.

    Fields outside the established MCP root slice stay unexposed. Within that
    slice, schema drift fails the whole brief call rather than leaking a new
    field or silently discarding canonical provenance.
    """
    return _closed_value({key: brief.get(key) for key in _BRIEF}, _BRIEF)


def project_public_source(row: dict[str, Any]) -> dict[str, Any]:
    """Keep the existing source slice while closing its nested gap values."""
    return _closed_value({key: row[key] for key in _SOURCE if key in row}, _SOURCE)
