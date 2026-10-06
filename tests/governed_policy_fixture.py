"""Fictional, isolated rights review for source-bound governance tests only.

The real catalog, rights matrix, approved snapshot and archives are never written.
The production compiler derives schema 2 from copied inputs; no runtime bypass or
hand-built policy hash is used. VERIFIED below means a fictional test permission.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATHS = (
    "scripts/source-policy.py", "scripts/source-governance.py",
    "scripts/retention-policy.py", "scripts/query-store.py", "scripts/query-gateway.py",
    "scripts/answer-gate-runner.mjs", "scripts/query-gateway-stdio.py",
    "intel_v2/__init__.py", "intel_v2/semantics.py", "intel_v2/query_domain.py",
    "intel_v2/located_facts.py", "intel_v2/freshness_policy.py", "intel_v2/public_brief.py",
    "intel_v2/conversation.py", "intel_v2/entity_binding.py", "scripts/entity-registry.py", "config/entity-registry.v1.json",
    "workers/query-gateway/src/index.js", "apps/web/lib/answer-evidence-gate.js",
    "apps/web/lib/publication-dates.js",
    "workers/query-gateway/src/public-brief.js", "workers/query-gateway/src/research.js",
    "workers/query-gateway/src/document-evidence.js", "workers/query-gateway/src/document-research.js",
    "apps/web/public/data/retention-policy-binding.json",
    "apps/web/lib/council-prep.js", "docs/govintel/source-catalog.v2.json",
    "docs/govintel/retention-rights-policy.v1.json", "docs/govintel/source-policy.approved.json",
)


def load_fixture_module(fixture, name, relative):
    spec = importlib.util.spec_from_file_location(name, fixture["root"] / relative)
    if spec is None or spec.loader is None:
        raise ValueError("fixture module unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_governed_policy_fixture(root, *, source_root=ROOT, rights_reviewed=True, domain_source_ids=(), brief_reviewed=False, per_source_review=False):
    """Compile copied canonical inputs into a fictional approved temp snapshot."""
    root, source_root = Path(root).resolve(), Path(source_root).resolve()
    if root.is_relative_to(source_root) or source_root.is_relative_to(root):
        raise ValueError("fixture root must be separate from the source repository")
    if root.exists() and any(root.iterdir()):
        raise ValueError("fixture root must be empty")
    root.mkdir(parents=True, exist_ok=True)
    for relative in RUNTIME_PATHS:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_root / relative, target)
    history = source_root / "docs/govintel/source-policy-history"
    if history.exists():
        shutil.copytree(history, root / "docs/govintel/source-policy-history")
    (root / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    paths = {
        "catalog": root / "docs/govintel/source-catalog.v2.json",
        "rights_matrix": root / "docs/govintel/retention-rights-policy.v1.json",
        "approved": root / "docs/govintel/source-policy.approved.json",
        "public": root / "apps/web/public/data",
    }
    matrix = json.loads(paths["rights_matrix"].read_text(encoding="utf-8"))
    if rights_reviewed:
        # Explicitly fictional permission; retain the exact #39 field whitelist.
        matrix["classes"]["OFFICIAL_METADATA_LINK"].update(
            rights_status="VERIFIED_METADATA_PERMISSION", review_required=False,
        )
        paths["rights_matrix"].write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    catalog = json.loads(paths["catalog"].read_text(encoding="utf-8"))
    if per_source_review:
        matrix["terms_status_values"]["HUMAN_REVIEWED_SOURCE_TERMS"] = "FICTIONAL_OFFLINE_ONLY"
        sid = "S-032"
        row = next(row for row in catalog["sources"] if row["source_id"] == sid)
        now = datetime.now(timezone.utc)
        matrix["source_reviews"] = {sid: {
            "decision": "APPROVE_METADATA_ONLY", "rights_status": "OPEN_DATA_LICENSED",
            "resource_url": row["entrypoint"], "public_fields": ["title", "official_url"],
            "human_review": {"reviewer": "FICTIONAL_TEST_REVIEWER", "role": "FICTIONAL_OFFLINE_ONLY",
                             "reviewed_at": (now - timedelta(seconds=1)).isoformat()},
            "terms_evidence": {"requested_url": "https://fictional.example.test/terms",
                "final_url": "https://fictional.example.test/terms", "http_status": 200,
                "fetched_at": (now - timedelta(seconds=2)).isoformat(),
                "body_sha256": hashlib.sha256(b"FICTIONAL_TERMS_NOT_ACTUAL_PERMISSION").hexdigest(),
                "locator": "FICTIONAL_TEST_ONLY", "license_scope": "METADATA_LINK_ONLY", "license_id": "FICTIONAL_TEST_ONLY"},
            "exceptions_reviewed": True, "attribution": "FICTIONAL_OFFLINE_ONLY",
        }}
        paths["rights_matrix"].write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    promotions = []
    if brief_reviewed:
        if not rights_reviewed:
            raise ValueError("fictional brief review requires explicit reviewed fixture")
        projection = load_fixture_module({"root": root}, "fictional_brief_schema", "intel_v2/public_brief.py")
        fields = set()
        def collect_fields(shape):
            if isinstance(shape, dict):
                fields.update(shape)
                for child in shape.values():
                    collect_fields(child)
            elif isinstance(shape, list):
                collect_fields(shape[0])
        collect_fields(projection._BRIEF)
        matrix["classes"]["DERIVED_SUMMARY"].update(review_required=False, public_fields=sorted(fields))
        domain_source_ids = tuple(set(domain_source_ids) | {r["source_id"] for r in catalog["sources"]
                                                          if r["status"] == "PRODUCTION_ACTIVE"})
    if domain_source_ids:
        if not rights_reviewed:
            raise ValueError("fictional domain review requires explicit reviewed fixture")
        # A separate fictional summary permission preserves typed/domain positive
        # controls. Metadata permission alone never grants this capability.
        matrix["classes"]["FICTIONAL_DOMAIN_SUMMARY"] = {
            **matrix["classes"]["OFFICIAL_METADATA_LINK"],
            "public_projection": "EVIDENCE_BOUND_SUMMARY_ONLY",
        }
        # Exact names for these synthetic domain controls, not a production
        # wildcard or permission to disclose unknown fields/version payloads.
        domain_fields = """public_event_id event_type named_event_id canonical_title event_date start_at end_at
            district_id location_candidates location_ids agency_ids independent_source_ids independent_source_count
            fusion_status verification_status event_status source_state lkg tracked tracking_id changed changed_fields
            conflict_fields uncertain_fields created_at updated_at trust_tier documents source_id source_role
            document_id document_version_id evidence_id official_url evidence_locator version_id observed_at
            published_at effective_at fields tracking version_count comparison_status before after materiality
            source_document_versions affected_handoff_claims statistic_id dataset_id metric period value unit geography
            agency provisional comparison_period official_note entity_registry registry_hash registry_version
            control_start road""".split()
        matrix["classes"]["FICTIONAL_DOMAIN_SUMMARY"]["public_fields"] = sorted(
            set(matrix["classes"]["FICTIONAL_DOMAIN_SUMMARY"]["public_fields"]) | set(domain_fields))
        for sid in domain_source_ids:
            row = next((r for r in catalog["sources"] if r["source_id"] == sid), None)
            if row is None or row["role"] not in {"PRIMARY_EVENT", "PRIMARY_REFERENCE"}:
                raise ValueError("fictional domain source must be a catalog official source")
            if row["status"] != "PRODUCTION_ACTIVE":
                row["status"] = "PRODUCTION_ACTIVE"
                promotions.append({"source_id": sid, "receipt_id": f"FICTIONAL_OFFLINE_ONLY:{sid}",
                                   "reason": "isolated test promotion; no real source activation"})
            matrix["source_classes"][sid] = "FICTIONAL_DOMAIN_SUMMARY"
        paths["catalog"].write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["rights_matrix"].write_text(json.dumps(matrix, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fixture = {"root": root, "paths": paths, "fixture_rights_review": "FICTIONAL_OFFLINE_ONLY"}
    compiler = load_fixture_module(fixture, "fixture_source_policy", "scripts/source-policy.py")
    catalog = compiler.load_catalog()
    previous = json.loads(paths["approved"].read_text(encoding="utf-8"))
    policy = compiler.compile_governed_policy(catalog, previous=previous, promotions=promotions)
    binding = compiler.policy_binding(policy)
    paths["approved"].write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["public"].mkdir(parents=True, exist_ok=True)
    retention = load_fixture_module(fixture, "fixture_retention_binding", "scripts/retention-policy.py")
    (paths["public"] / "retention-policy-binding.json").write_text(
        json.dumps(retention.policy_binding(retention.compile_policy(catalog, matrix)), ensure_ascii=False) + "\n", encoding="utf-8")
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    run = "CR-FICTIONAL-GOVERNED-PUBLICATION"
    source = policy["active_sources"][0]
    sid = source["source_id"]
    feed = {"schema_version": 1, "source_policy": binding, "collection_run_id": run, "generated_at": stamp,
        "items": [{"stable_id": f"WORKER-FIXTURE-{i}", "title": f"測試官方標題 {i}",
            "source_id": sid, "source_role": "PRIMARY_OFFICIAL",
            "official_url": source["approved_origins"][0] + f"/fictional-governance/notices/{i}",
            "published_at": stamp, "data_as_of": stamp, "fetched_at": stamp,
            "source_health": "PASS", "window_completeness": "COMPLETE_WITH_ITEMS", "freshness_status": "FRESH",
            "content_sha256": hashlib.sha256(f"fictional-governance-{i}".encode()).hexdigest()} for i in (1, 2)]}
    status = {"schema_version": 1, "source_policy": binding, "generated_at": stamp,
        "latest_collection_run": {"collection_run_id": run, "finished_at": stamp, "status": "SUCCEEDED"},
        "sources": [{"source_id": source_id, "source_name": f"Fictional fixture {source_id}", "source_health": "PASS",
            "window_completeness": "COMPLETE_WITH_ITEMS" if source_id == sid else "COMPLETE_ZERO",
            "result": "NO_NEW_ITEM", "freshness_status": "FRESH", "data_as_of": stamp,
            "last_checked_at": stamp, "last_success_at": stamp} for source_id in policy["active_source_ids"]]}
    brief = {"schema_version": 1, "source_policy": binding, "generated_at": stamp, "source_status_generated_at": stamp,
        "source_collection_run_id": run, "publication_status": "READY", "snapshot_complete": True}
    publication = {"source-policy.json": policy, "intelligence-feed.json": feed,
        "source-status.json": status, "v2-daily-brief.json": brief}
    for name, value in publication.items():
        (paths["public"] / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    compiler.validate_catalog_binding(policy, catalog)
    assert compiler.load_current_policy() == policy
    fixture.update(policy=policy, binding=binding, catalog=catalog, publication=publication)
    return fixture


def bind_checked_in_publication(fixture, *, source_root=ROOT):
    """Retain real fixture metadata in a separately reviewed fictional checkout.

    The input rows and original fixed clock remain meaningful pagination/stale
    controls; the additional permission and source activation exist only here.
    """
    for name in ("intelligence-feed.json", "source-status.json", "v2-daily-brief.json"):
        value = json.loads((Path(source_root) / "apps/web/public/data" / name).read_text(encoding="utf-8"))
        value["source_policy"] = fixture["binding"]
        if name == "source-status.json":
            existing = {r["source_id"] for r in value["sources"]}
            for row in fixture["publication"][name]["sources"]:
                if row["source_id"] not in existing:
                    added = dict(row, last_checked_at=value["generated_at"], last_success_at=value["generated_at"],
                                 data_as_of=value["generated_at"])
                    value["sources"].append(added)
        (fixture["paths"]["public"] / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        fixture["publication"][name] = value
    return fixture


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--rights-unknown", action="store_true")
    parser.add_argument("--brief-reviewed", action="store_true")
    parser.add_argument("--per-source-review", action="store_true")
    args = parser.parse_args()
    fixture = make_governed_policy_fixture(args.root, rights_reviewed=not args.rights_unknown, brief_reviewed=args.brief_reviewed, per_source_review=args.per_source_review)
    print(json.dumps({"root": str(fixture["root"]), "policy_hash": fixture["policy"]["policy_hash"],
        "fixture_rights_review": fixture["fixture_rights_review"]}))
