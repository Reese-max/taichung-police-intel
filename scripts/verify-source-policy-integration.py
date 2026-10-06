#!/usr/bin/env python3
"""Prove that source-policy consumers share one catalog-derived binding."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
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
    result = {
        "policy_version": policy["policy_version"],
        "policy_hash": policy["policy_hash"],
        "catalog_hash": policy["catalog_hash"],
        "active_source_ids": sorted(policy["active_source_ids"]),
    }
    if policy.get("schema_version") == 2 or "governance_hash" in policy:
        result.update(governance_hash=policy.get("governance_hash") or policy["governance_binding"]["governance_hash"],
                      retention_policy_hash=policy.get("retention_policy_hash") or policy["governance_binding"]["retention_policy"]["policy_hash"])
    return result


def check_promoted_fixture(old_store: dict[str, Any], old_result: dict[str, Any], as_of: str) -> dict[str, Any]:
    """Run real consumers in a temporary checkout with an explicitly approved fixture."""
    source_policy = load_module("promoted_fixture_policy", "scripts/source-policy.py")
    query_store = load_module("promoted_fixture_store", "scripts/query-store.py")
    query_gateway = load_module("promoted_fixture_gateway", "scripts/query-gateway.py")
    system_health = load_module("promoted_fixture_health", "scripts/system-health.py")
    publication = load_module("promoted_fixture_publication", "scripts/verify-publication-bundle.py")
    collect = load_module("promoted_fixture_collector", "collect.py")
    online = load_module("promoted_fixture_online", "online_collect.py")
    current = source_policy.load_current_policy()
    expected = binding(current)
    feed, _ = query_store.load_json(query_store.DEFAULT_FEED)
    status, _ = query_store.load_json(query_store.DEFAULT_STATUS)
    brief, _ = query_store.load_json(query_store.DEFAULT_BRIEF)
    store = query_store.build_from_paths(query_store.DEFAULT_FEED, query_store.DEFAULT_STATUS, query_store.DEFAULT_BRIEF)
    canonical_artifacts = {name: path.read_bytes() for name, path in (
        ("feed", query_store.DEFAULT_FEED), ("status", query_store.DEFAULT_STATUS),
        ("brief", query_store.DEFAULT_BRIEF))}
    gateway = query_gateway.QueryGateway(snapshot={"store": store, "status": status, "brief": brief,
                                                  "canonical_artifacts": canonical_artifacts},
                                         clock=lambda: query_store.instant(as_of))
    health = system_health.load_current()
    ui = json.loads((ROOT / "apps/web/public/data/source-policy.json").read_text(encoding="utf-8"))
    projections = {"query_store": store["policy"], "query_gateway": gateway.execute("get_source_health", {})["policy"],
                   "system_health": health["policy"], "ui": binding(ui)}
    if any(value != expected for value in projections.values()):
        raise ValueError("promoted fixture consumer policy binding mismatch")
    for name, source_ids in (("collector", set(collect.P0_SOURCES)), ("online_collector", set(online.SOURCE_ROWS)),
                             ("publication_validator", publication.load_expected_sources())):
        if source_ids != set(current["active_source_ids"]):
            raise ValueError(f"promoted fixture {name} source set mismatch")
    if publication.main() != 0:
        raise ValueError("promoted fixture publication bundle rejected")
    ui_check = subprocess.run([
        "node", "--input-type=module", "-e",
        "import {readFileSync} from 'node:fs'; import {validateSourceStatus} from './apps/web/lib/source-status.js';"
        "const read=(n)=>JSON.parse(readFileSync('apps/web/public/data/'+n));"
        "const p=read('source-policy.json'); const s=validateSourceStatus(read('source-status.json'),p);"
        "if(!p.capabilities.find(c=>c.capability_id==='traffic_events')?.supported)throw Error('traffic unsupported');"
        "console.log(JSON.stringify({policy_hash:p.policy_hash,active:s.sources.length}));",
    ], cwd=ROOT, text=True, capture_output=True, check=True)
    if json.loads(ui_check.stdout)["policy_hash"] != current["policy_hash"]:
        raise ValueError("promoted fixture UI policy mismatch")
    clock = query_store.instant(as_of)
    zero = query_store.query_store(store, now=clock, capability_id="traffic_events",
                                   canonical_artifacts=canonical_artifacts)
    if not zero["answerable_no_match"] or zero["query_coverage"]["status"] != "COVERED_BOUNDED_SCOPE":
        raise ValueError("supported complete scheduled-traffic zero was not bounded")
    if not zero["query_coverage"]["coverage_limitations"] or "not all real-world events" not in zero["answer_scope"]:
        raise ValueError("zero lost its bounded-snapshot limitations")
    gap_results = {}
    for label, mutation, expected_status in (
        ("FAILED", {"source_health": "FAILED", "result": "FAILED"}, "PARTIAL"),
        ("PARTIAL", {"window_completeness": "PARTIAL", "result": "PARTIAL"}, "PARTIAL"),
        ("STALE", {"freshness_status": "STALE"}, "STALE"),
    ):
        # Preserve the gap control as a genuine canonical publication change,
        # then derive its whole projection and hashes with the real producer.
        changed_status = copy.deepcopy(status)
        next(row for row in changed_status["sources"] if row["source_id"] == "S-032").update(mutation)
        changed_artifacts = {**canonical_artifacts,
            "status": json.dumps(changed_status, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"}
        changed_hashes = {name: query_store.sha256_bytes(raw) for name, raw in changed_artifacts.items()}
        changed = query_store.build_store(feed, changed_status, brief, changed_hashes)
        result = query_store.query_store(changed, now=clock, source_id="S-004", capability_id="traffic_events",
                                         canonical_artifacts=changed_artifacts)
        coverage = result["query_coverage"]
        if coverage["status"] != expected_status or coverage["can_state_bounded_no_match"] or result["answerable_no_match"]:
            raise ValueError(f"required traffic source gap hidden by source filter: {label}")
        if "S-032" not in coverage.get("missing_required_sources", []) + coverage.get("stale_required_sources", []):
            raise ValueError(f"required traffic source gap missing: {label}")
        gap_results[label] = coverage["status"]
    try:
        query_store.query_store(old_store, now=clock)
    except ValueError as error:
        if "policy mismatch" not in str(error) and "legacy query store" not in str(error):
            raise
    else:
        raise ValueError("live query accepted historical policy after transition")
    replay = query_store.replay_query_store(old_store, policy_hash=old_store["policy"]["policy_hash"],
                                          as_of=as_of, text="議事", limit=2)
    if replay["result"] != old_result or replay["may_answer_current"] or not replay["read_only"]:
        raise ValueError("old publication replay changed result or became current")
    if store["generation_id"] == old_store["generation_id"]:
        raise ValueError("policy transition did not change query generation")
    return {"consumer_count": 7, "policy_hash": current["policy_hash"], "active": len(current["active_source_ids"]),
            "new_generation": store["generation_id"], "old_generation": old_store["generation_id"],
            "historical_replay_equal": True, "live_old_policy_rejected": True, "bounded_traffic_zero": True,
            "required_source_gaps": gap_results, "provider_calls": 0, "source_promotion": "isolated fixture only"}


def reconcile_promoted_candidate_fixture(fixture: Path, active_ids, feed, status, brief) -> None:
    """Keep the copied candidate lane disjoint from a fictional promotion."""
    if fixture.resolve() == ROOT.resolve():
        raise ValueError("candidate fixture reconciliation cannot edit the real checkout")
    active = set(active_ids)
    # The real candidate scope remains fixed. Only this temporary checkout's
    # explicitly promoted catalog needs the corresponding candidate removal.
    scope_path = fixture / "scripts/candidate_publication.py"
    with scope_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# Fictional promotion scope; isolated integration fixture only.\n"
                     "CANDIDATE_PUBLICATION_SOURCE_IDS = tuple(sid for sid in "
                     "CANDIDATE_PUBLICATION_SOURCE_IDS if sid not in " + repr(sorted(active)) + ")\n")
    for document, key in ((status, "candidate_sources"), (feed, "candidate_items")):
        if key in document:
            document[key] = [row for row in document[key] if row["source_id"] not in active]
    if "candidate_source_summary" in feed:
        feed["candidate_source_summary"] = {
            sid: row for sid, row in feed["candidate_source_summary"].items() if sid not in active
        }
    if "candidate_source_context" in brief:
        for key in ("sources", "items"):
            brief["candidate_source_context"][key] = [
                row for row in brief["candidate_source_context"][key] if row["source_id"] not in active
            ]


def run_promoted_fixture(catalog, current, old_store, query_store) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="govintel-policy-transition-") as directory:
        fixture = Path(directory)
        for relative in ("docs/govintel", "scripts", "intel_v2", "apps/web/public/data"):
            shutil.copytree(ROOT / relative, fixture / relative, ignore=shutil.ignore_patterns("__pycache__"))
        for relative in ("collect.py", "online_collect.py", "apps/web/package.json", "apps/web/lib/source-status.js",
                         "apps/web/lib/publication-dates.js"):
            destination = fixture / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, destination)
        promoted_catalog = copy.deepcopy(catalog)
        candidate = next(row for row in promoted_catalog["sources"] if row["source_id"] == "S-032")
        candidate["status"] = "PRODUCTION_ACTIVE"
        # Fictional review exists only in this temporary checkout. A source
        # promotion receipt does not establish real publication rights.
        matrix_path = fixture / "docs/govintel/retention-rights-policy.v1.json"
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        matrix["classes"]["OFFICIAL_METADATA_LINK"].update(rights_status="VERIFIED_METADATA_PERMISSION", review_required=False)
        matrix_path.write_text(json.dumps(matrix, ensure_ascii=False), encoding="utf-8")
        (fixture / "docs/govintel/source-catalog.v2.json").write_text(json.dumps(promoted_catalog, ensure_ascii=False), encoding="utf-8")
        spec = importlib.util.spec_from_file_location("fixture_transition_compiler", fixture / "scripts/source-policy.py")
        policy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy_module)
        promoted = policy_module.compile_governed_policy(promoted_catalog, previous=current, promotions=[{
            "source_id": "S-032", "receipt_id": "integration:explicit-fixture-approval", "reason": "isolated offline fixture only",
        }])
        def write(relative, value):
            (fixture / relative).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        write("docs/govintel/source-catalog.v2.json", promoted_catalog)
        write("docs/govintel/source-policy.approved.json", promoted)
        write("apps/web/public/data/source-policy.json", promoted)
        feed, _ = query_store.load_json(query_store.DEFAULT_FEED)
        status, _ = query_store.load_json(query_store.DEFAULT_STATUS)
        brief, _ = query_store.load_json(query_store.DEFAULT_BRIEF)
        clock = query_store.instant(feed["generated_at"])
        old_result = query_store.replay_query_store(old_store, policy_hash=old_store["policy"]["policy_hash"],
                                                  as_of=clock.isoformat(), text="議事", limit=2)["result"]
        if old_result["result_count"] == 0:
            raise ValueError("historical baseline must retain original nonempty publication rows")
        feed["items"] = []
        feed["source_summary"] = {source_id: {"item_count": 0} for source_id in promoted["active_source_ids"]}
        extra = copy.deepcopy(status["sources"][0])
        extra.update(source_id="S-032", source_name=candidate["name"], source_url=candidate["entrypoint"])
        status["sources"].append(extra)
        status["latest_collection_run"]["status"] = "SUCCEEDED"
        for row in status["sources"]:
            row.update(source_health="PASS", window_completeness="COMPLETE_ZERO", result="NO_NEW_ITEM",
                       freshness_status="FRESH", last_checked_at=feed["generated_at"], last_success_at=feed["generated_at"])
        promoted_rows = {row["source_id"]: row for row in promoted_catalog["sources"]}
        for row in status["sources"]:
            catalog_row = promoted_rows[row["source_id"]]
            row.update(source_role=catalog_row["role"], integration_status=catalog_row["status"])
        status["candidate_catalog"] = [row for row in status.get("candidate_catalog", [])
                                       if row["source_id"] not in promoted["active_source_ids"]]
        reconcile_promoted_candidate_fixture(fixture, promoted["active_source_ids"], feed, status, brief)
        brief.update(publication_status="READY", snapshot_complete=True)
        summary = {"schema_version": 1, "generated_at": feed["generated_at"], "collection_run_id": feed["collection_run_id"],
                   "total_items": 0, "eligible_items": 0, "source_breakdown": [{"source_id": source_id, "item_count": 0}
                   for source_id in promoted["active_source_ids"]]}
        for name, value in (("intelligence-feed", feed), ("source-status", status), ("v2-daily-brief", brief),
                            ("intelligence-summary", summary)):
            value["source_policy"] = binding(promoted)
            write(f"apps/web/public/data/{name}.json", value)
        (fixture / "apps/web/public/data/feed-export.csv").write_text("stable_id\n", encoding="utf-8")
        write("old-store.json", old_store)
        write("old-result.json", old_result)
        completed = subprocess.run([sys.executable, "scripts/verify-source-policy-integration.py", "--fixture-check",
                                    "--old-store", "old-store.json", "--old-result", "old-result.json",
                                    "--as-of", clock.isoformat()], cwd=fixture, text=True, capture_output=True)
        if completed.returncode:
            raise ValueError("promoted fixture check failed: " + (completed.stdout + completed.stderr)[-4000:])
        result = json.loads(completed.stdout.splitlines()[-1])
        if result["policy_hash"] != promoted["policy_hash"]:
            raise ValueError("executed fixture policy differs from compiled candidate")
        return result


def run_checks() -> dict[str, Any]:
    source_policy = load_module("source_policy_integration", "scripts/source-policy.py")
    query_store = load_module("query_store_integration", "scripts/query-store.py")
    query_gateway = load_module("query_gateway_integration", "scripts/query-gateway.py")
    system_health = load_module("system_health_integration", "scripts/system-health.py")
    publication = load_module("publication_bundle_integration", "scripts/verify-publication-bundle.py")
    collect = load_module("collect_integration", "collect.py")
    online = load_module("online_collect_integration", "online_collect.py")

    catalog = source_policy.load_catalog()
    current = source_policy.load_current_policy()
    expected = binding(current)
    projections = {
        "query_store": binding(query_store.load_current_policy()),
        "system_health": binding(system_health.load_current_policy()),
        "ui": None,
    }
    gateway = query_gateway.QueryGateway()
    projections["query_gateway"] = binding(gateway.store["policy"])
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
    consumer_names = [
        *projections,
        "collector",
        "online_collector",
        "publication_validator",
    ]

    old_store = query_store.build_from_paths(query_store.DEFAULT_FEED, query_store.DEFAULT_STATUS, query_store.DEFAULT_BRIEF)
    if old_store["policy"] != expected:
        raise ValueError("query store was not built from current policy")
    query = query_store.query_store(old_store, text="議事", limit=1)
    if query["query_coverage"]["policy_hash"] != expected["policy_hash"]:
        raise ValueError("query coverage was not bound to current policy")
    gateway_response = gateway.execute("get_source_health", {})
    if gateway_response["query_coverage"]["policy_hash"] != expected["policy_hash"]:
        raise ValueError("query gateway response was not bound to current policy")
    unsupported_query = query_store.query_store(old_store, capability_id="traffic_events", limit=1)
    if unsupported_query["query_coverage"]["status"] != "CAPABILITY_NOT_AVAILABLE":
        raise ValueError("unsupported query capability was not explicit")
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
    if partial.get("collection_coverage_status", partial["status"]) != "PARTIAL" or current["active_source_ids"][0] not in partial["missing_required_sources"]:
        raise ValueError(f"required source gap was hidden: {missing}")
    unsupported = source_policy.assess_query(current, "traffic_events", states)
    if unsupported["status"] != "CAPABILITY_NOT_AVAILABLE" or unsupported["can_state_bounded_no_match"]:
        raise ValueError("unsupported capability was presented as a bounded empty result")

    if query["result_count"] != 0 or query["query_coverage"]["status"] != "UNKNOWN" or query["answerable_no_match"]:
        raise ValueError("legacy current policy incorrectly admits formal public rows")
    original_store = json.loads((ROOT / "tests/fixtures/source-policy/publication-metadata-v3.json").read_text(encoding="utf-8"))
    if len(original_store["items"]) != 118:
        raise ValueError("original historical publication was discarded")
    fixture_transition = run_promoted_fixture(catalog, current, original_store, query_store)

    return {
        "active": len(current["active_source_ids"]),
        "policy_version": current["policy_version"],
        "consumer_count": len(consumer_names),
        "consumer_names": consumer_names,
        "promoted_policy_version": promoted["policy_version"],
        "old_replay_generation": old_store["generation_id"],
        "mixed_policy_rejected": True,
        "partial_gap_preserved": True,
        "unsupported_explicit": True,
        "query_coverage_bound": True,
        "fixture_transition": fixture_transition,
    }


def self_check() -> None:
    result = run_checks()
    print(
        "SOURCE_POLICY_INTEGRATION_OK "
        f"active={result['active']} consumers={result['consumer_count']} "
        f"version={result['policy_version']} promoted={result['promoted_policy_version']} "
        f"old_replay=true mixed_rejected={result['mixed_policy_rejected']} "
        f"partial_gap={result['partial_gap_preserved']} unsupported={result['unsupported_explicit']} "
        f"query_coverage={result['query_coverage_bound']} "
        f"promoted_fixture_consumers={result['fixture_transition']['consumer_count']} "
        f"historical_replay_after_transition={result['fixture_transition']['historical_replay_equal']}"
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--fixture-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--old-store", type=Path)
    parser.add_argument("--old-result", type=Path)
    parser.add_argument("--as-of")
    args = parser.parse_args()
    if args.fixture_check:
        if not all((args.old_store, args.old_result, args.as_of)):
            raise SystemExit("fixture check requires old store, result and as-of clock")
        result = check_promoted_fixture(json.loads(args.old_store.read_text(encoding="utf-8")),
                                       json.loads(args.old_result.read_text(encoding="utf-8")), args.as_of)
        print(json.dumps(result, sort_keys=True))
    elif not args.self_check:
        raise SystemExit("--self-check is required")
    else:
        self_check()
