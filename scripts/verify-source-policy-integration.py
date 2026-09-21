#!/usr/bin/env python3
"""Prove that source-policy consumers share one catalog-derived binding."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE_POLICY = ROOT / "scripts/source-policy.py"


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"module unavailable: {relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def binding(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "catalog_hash": policy["catalog_hash"],
        "active_source_ids": sorted(policy["active_source_ids"]),
    }


def run_checks() -> dict[str, Any]:
    source_policy = load_module("source_policy_integration", "scripts/source-policy.py")
    query_store = load_module("query_store_integration", "scripts/query-store.py")
    system_health = load_module("system_health_integration", "scripts/system-health.py")
    publication = load_module("publication_bundle_integration", "scripts/verify-publication-bundle.py")
    collect = load_module("collect_integration", "collect.py")
    online = load_module("online_collect_integration", "online_collect.py")

    catalog = source_policy.load_catalog()
    current = source_policy.compile_policy(catalog)
    expected = binding(current)
    projections = {
        "query_store": binding(query_store.load_current_policy()),
        "system_health": binding(system_health.load_current_policy()),
        "ui": None,
    }
    ui_policy = json.loads((ROOT / "apps/web/public/data/source-policy.json").read_text(encoding="utf-8"))
    projections["ui"] = {key: ui_policy[key] for key in expected}
    if any(value != expected for value in projections.values()):
        raise ValueError("source policy binding differs between consumers")
    if set(collect.P0_SOURCES) != set(expected["active_source_ids"]):
        raise ValueError("collector active source set differs from policy")
    if set(online.SOURCE_ROWS) != set(expected["active_source_ids"]):
        raise ValueError("online collector source set differs from policy")
    if publication.load_expected_sources() != set(expected["active_source_ids"]):
        raise ValueError("publication validator source set differs from policy")

    old_store = query_store.build_from_paths(query_store.DEFAULT_FEED, query_store.DEFAULT_STATUS, query_store.DEFAULT_BRIEF)
    if old_store["policy"] != expected:
        raise ValueError("query store was not built from current policy")
    replay = query_store.build_from_paths(query_store.DEFAULT_FEED, query_store.DEFAULT_STATUS, query_store.DEFAULT_BRIEF)
    if replay["generation_id"] != old_store["generation_id"] or replay["policy"] != old_store["policy"]:
        raise ValueError("old publication replay is not deterministic")

    promoted_catalog = copy.deepcopy(catalog)
    candidate = next(row for row in promoted_catalog["sources"] if row["source_id"] == "S-032")
    candidate["status"] = "PRODUCTION_ACTIVE"
    promoted = source_policy.compile_policy(
        promoted_catalog,
        previous=current,
        promotions=[{"source_id": "S-032", "receipt_id": "integration:approved-s032", "reason": "fixture promotion receipt"}],
    )
    if promoted["policy_version"] != current["policy_version"] + 1 or promoted["policy_hash"] == current["policy_hash"]:
        raise ValueError("approved candidate did not produce a new policy")
    if set(current["active_source_ids"]) == set(promoted["active_source_ids"]):
        raise ValueError("candidate promotion did not change active set")

    mixed = copy.deepcopy(old_store)
    mixed["policy"] = binding(promoted)
    mixed["projection_sha256"] = query_store.sha256_bytes(
        query_store.canonical_json({key: value for key, value in mixed.items() if key != "projection_sha256"})
    )
    try:
        query_store.validate_store(mixed)
    except ValueError as error:
        if "policy mismatch" not in str(error):
            raise
    else:
        raise ValueError("mixed policy artifact was accepted")

    states = {
        source_id: {"source_health": "PASS", "window_completeness": "COMPLETE_WITH_ITEMS", "freshness": "RECENT"}
        for source_id in current["active_source_ids"]
    }
    missing = states.pop(current["active_source_ids"][0])
    partial = source_policy.assess_query(current, "publication_metadata", states)
    if partial["status"] != "PARTIAL" or current["active_source_ids"][0] not in partial["missing_required_sources"]:
        raise ValueError(f"required source gap was hidden: {missing}")
    unsupported = source_policy.assess_query(current, "traffic_events", states)
    if unsupported["status"] != "CAPABILITY_NOT_AVAILABLE" or unsupported["can_state_bounded_no_match"]:
        raise ValueError("unsupported capability was presented as a bounded empty result")

    return {
        "active": len(current["active_source_ids"]),
        "policy_version": current["policy_version"],
        "consumer_count": len(projections) + 2,
        "promoted_policy_version": promoted["policy_version"],
        "old_replay_generation": old_store["generation_id"],
        "mixed_policy_rejected": True,
        "partial_gap_preserved": True,
        "unsupported_explicit": True,
    }


def self_check() -> None:
    result = run_checks()
    print(
        "SOURCE_POLICY_INTEGRATION_OK "
        f"active={result['active']} consumers={result['consumer_count']} "
        f"version={result['policy_version']} promoted={result['promoted_policy_version']} "
        f"old_replay=true mixed_rejected={result['mixed_policy_rejected']} "
        f"partial_gap={result['partial_gap_preserved']} unsupported={result['unsupported_explicit']}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if not args.self_check:
        raise SystemExit("--self-check is required")
    self_check()
