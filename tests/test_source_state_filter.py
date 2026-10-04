"""Test source_state filter in query_events."""
import sys
from pathlib import Path

from intel_v2 import query_domain


def event(event_id="PE-1", *, source_state="CURRENT"):
    return {
        "schema_version": 1,
        "public_event_id": event_id,
        "event_type": "traffic_control",
        "canonical_title": "大型活動交通管制",
        "event_date": "2026-09-20",
        "start_at": "2026-09-20T16:00:00+08:00",
        "end_at": "2026-09-20T22:00:00+08:00",
        "district_id": "location:west",
        "location_candidates": ["location:west"],
        "location_ids": ["location:west"],
        "agency_ids": ["agency:police"],
        "independent_source_ids": ["S-001", "S-032"],
        "independent_source_count": 2,
        "fusion_status": "CONFIRMED",
        "source_state": source_state,
        "tracked": True,
        "changed": True,
        "linked_document_versions": [
            {
                "document_id": "doc-police",
                "document_version_id": "doc-police:v2",
                "source_id": "S-001",
                "official_url": "https://police.example/event",
            }
        ],
        "version_history": [
            {
                "document_version_id": "doc-police:v1",
                "observed_at": "2026-09-19T10:00:00+08:00",
                "fields": {"start_at": "2026-09-20T18:00:00+08:00"},
            },
            {
                "document_version_id": "doc-police:v2",
                "observed_at": "2026-09-20T10:00:00+08:00",
                "fields": {"start_at": "2026-09-20T16:00:00+08:00"},
                "changed_fields": ["start_at"],
            },
        ],
        "affected_handoff_claims": ["CLAIM-1"],
    }


def test_source_state_filter_independently():
    """source_state should be filterable independently in query_events."""
    store = query_domain.build_event_store([
        {"schema_version": 1, "public_event_id": "PE-1", **event("PE-1", source_state="CURRENT")},
        {"schema_version": 1, "public_event_id": "PE-2", **event("PE-2", source_state="CONFLICT")},
        {"schema_version": 1, "public_event_id": "PE-3", **event("PE-3", source_state="CANDIDATE")},
    ])

    result = query_domain.query_events(store, {"source_state": "CONFLICT", "limit": 5})
    assert result["result_count"] == 1, (
        f"Expected 1 result with source_state=CONFLICT, got {result['result_count']}"
    )
    assert result["results"][0]["public_event_id"] == "PE-2", (
        f"Expected PE-2, got {result['results'][0]['public_event_id']}"
    )

    result = query_domain.query_events(store, {"source_state": "CURRENT", "limit": 5})
    assert result["result_count"] == 1, (
        f"Expected 1 result with source_state=CURRENT, got {result['result_count']}"
    )
    assert result["results"][0]["public_event_id"] == "PE-1", (
        f"Expected PE-1, got {result['results'][0]['public_event_id']}"
    )

    result = query_domain.query_events(store, {"source_state": "CANDIDATE", "limit": 5})
    assert result["result_count"] == 1, (
        f"Expected 1 result with source_state=CANDIDATE, got {result['result_count']}"
    )
    assert result["results"][0]["public_event_id"] == "PE-3", (
        f"Expected PE-3, got {result['results'][0]['public_event_id']}"
    )


if __name__ == "__main__":
    test_source_state_filter_independently()