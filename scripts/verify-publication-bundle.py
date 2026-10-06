from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "apps" / "web" / "public" / "data"
SOURCE_POLICY = ROOT / "scripts" / "source-policy.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_source_policy():
    spec = importlib.util.spec_from_file_location("publication_source_policy", SOURCE_POLICY)
    if spec is None or spec.loader is None:
        raise ValueError("source policy module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.load_current_policy()


def load_expected_sources() -> set[str]:
    _, policy = load_source_policy()
    expected = set(policy["active_source_ids"])
    if not expected:
        raise ValueError("source policy has no active sources")
    return expected


def load_policy_module():
    module, _ = load_source_policy()
    return module


def load_catalog() -> dict:
    """Return the canonical catalog truth each published label must match."""
    return load_policy_module().load_catalog()


def load_json(name: str) -> dict:
    path = DATA_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"missing file: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {path.relative_to(ROOT)}: {error}") from error


def source_ids(sources: object) -> list[str]:
    if not isinstance(sources, list) or any(not isinstance(source, dict) for source in sources):
        raise ValueError("source-status sources must contain only objects")
    ids = [source.get("source_id") for source in sources]
    if any(not isinstance(source_id, str) or not source_id for source_id in ids):
        raise ValueError("source-status source_id must be non-empty strings")
    return ids


def _credentialed_url(value: object) -> bool:
    if not isinstance(value, str):
        return True
    parsed = urlsplit(value)
    return parsed.scheme != "https" or parsed.username is not None or parsed.password is not None

def validate_candidate_lane(status: dict, feed: dict, *, active_ids: set[str], required: bool) -> None:
    """Validate the isolated candidate lane without widening active feed contracts."""
    lane_present = (
        "candidate_sources" in status
        or "candidate_items" in feed
        or "candidate_source_summary" in feed
    )
    if not lane_present and not required:
        return
    from scripts.candidate_publication import load_candidate_publication_sources
    from intel_v2.located_facts import validate_document_url

    expected = set(load_candidate_publication_sources())
    if expected & active_ids:
        raise ValueError("candidate publication IDs overlap active source policy")
    candidate_sources = status.get("candidate_sources")
    if not isinstance(candidate_sources, list):
        raise ValueError("source-status candidate_sources must be an array")
    ids = source_ids(candidate_sources)
    if set(ids) != expected or len(ids) != len(expected):
        raise ValueError(f"source-status candidate IDs invalid: {ids}")
    by_id = {row["source_id"]: row for row in candidate_sources}
    for source_id, row in by_id.items():
        if row.get("integration_status") != "CANDIDATE" or row.get("promotion_eligible") is not False:
            raise ValueError(f"{source_id}: source status is not CANDIDATE-only")
        if row.get("source_health") not in {"PASS", "DEGRADED", "FAILED"}:
            raise ValueError(f"{source_id}: invalid candidate source health")
        if row.get("window_completeness") not in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS", "PARTIAL"}:
            raise ValueError(f"{source_id}: invalid candidate window completeness")
        if not str(row.get("source_url") or "").startswith("https://"):
            raise ValueError(f"{source_id}: candidate source URL must be HTTPS")
        try:
            validate_document_url(source_id, row["source_url"])
        except ValueError as error:
            raise ValueError(f"{source_id}: candidate source URL is outside its catalog origin") from error

    candidate_items = feed.get("candidate_items")
    if not isinstance(candidate_items, list):
        raise ValueError("intelligence-feed candidate_items must be an array")
    active_items = feed.get("items") or []
    if any(isinstance(item, dict) and item.get("source_id") in expected for item in active_items):
        raise ValueError("candidate item leaked into active feed items")
    candidate_ids = []
    for item in candidate_items:
        if not isinstance(item, dict):
            raise ValueError("candidate feed contains a non-object item")
        source_id = item.get("source_id")
        if source_id not in expected:
            raise ValueError(f"candidate feed contains source outside scope: {source_id}")
        candidate_ids.append(source_id)
        if item.get("integration_status") != "CANDIDATE" or item.get("promotion_eligible") is not False:
            raise ValueError(f"{item.get('stable_id')}: candidate item is missing its CANDIDATE guard")
        if item.get("eligibility") not in {"INELIGIBLE_CANDIDATE", "INELIGIBLE_CANDIDATE_SOURCE_FAILED"}:
            raise ValueError(f"{item.get('stable_id')}: candidate item has an active eligibility")
        if item.get("change_type") not in {"CANDIDATE_OBSERVATION", "LKG"}:
            raise ValueError(f"{item.get('stable_id')}: candidate item has an active change type")
        if not item.get("stable_id") or not item.get("stable_key"):
            raise ValueError("candidate feed item is missing a stable identity")
        if not str(item.get("official_url") or "").startswith("https://"):
            raise ValueError(f"{item.get('stable_id')}: candidate official URL must be HTTPS")
        try:
            validate_document_url(source_id, item["official_url"])
        except ValueError as error:
            raise ValueError(f"{item.get('stable_id')}: candidate link is outside its catalog origin") from error

    candidate_summary = feed.get("candidate_source_summary")
    if not isinstance(candidate_summary, dict) or set(candidate_summary) != expected:
        raise ValueError(f"candidate_source_summary must cover exactly {sorted(expected)}")
    for source_id in expected:
        row = candidate_summary[source_id]
        if (
            not isinstance(row, dict)
            or row.get("integration_status") != "CANDIDATE"
            or row.get("promotion_eligible") is not False
        ):
            raise ValueError(f"{source_id}: candidate source summary is not CANDIDATE-only")
        item_count = sum(candidate_id == source_id for candidate_id in candidate_ids)
        if row.get("item_count") != item_count:
            raise ValueError(f"{source_id}: candidate item count does not match candidate_items")
        if row.get("health") != by_id[source_id].get("source_health"):
            raise ValueError(f"{source_id}: candidate health summary mismatch")


def bundle_errors(
    status: dict,
    feed: dict,
    summary: dict,
    csv_rows: list[dict],
    catalog: dict,
    expected_sources: set[str],
    *, require_candidate_lane: bool = False,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(status, dict):
        return ["source-status.json must be an object"]
    if not isinstance(feed, dict):
        return ["intelligence-feed.json must be an object"]
    if not isinstance(summary, dict):
        return ["intelligence-summary.json must be an object"]
    catalog_rows = {
        row["source_id"]: row
        for row in catalog.get("sources", [])
        if isinstance(row, dict) and row.get("source_id")
    }

    status_run = status.get("latest_collection_run") or {}
    module, policy = load_source_policy()
    if policy["schema_version"] == 2:
        expected_binding = module.policy_binding(policy)
        try:
            brief = load_json("v2-daily-brief.json")
        except ValueError as error:
            errors.append(str(error))
            brief = {}
        if any(value.get("source_policy") != expected_binding for value in (status, feed, summary, brief)):
            errors.append("publication artifacts have mixed or missing governed policy bindings")
    run_ids = {
        "source-status.json": status_run.get("collection_run_id"),
        "intelligence-feed.json": feed.get("collection_run_id"),
        "intelligence-summary.json": summary.get("collection_run_id"),
    }
    present_run_ids = {value for value in run_ids.values() if value}
    if len(present_run_ids) != 1 or any(value is None for value in run_ids.values()):
        errors.append(f"collection_run_id mismatch: {run_ids}")

    generated_times = {
        "source-status.json": status.get("generated_at"),
        "intelligence-feed.json": feed.get("generated_at"),
        "intelligence-summary.json": summary.get("generated_at"),
    }
    present_generated_times = {value for value in generated_times.values() if value}
    if len(present_generated_times) != 1 or any(value is None for value in generated_times.values()):
        errors.append(f"generated_at mismatch: {generated_times}")

    if status.get("schema_version") != 1 or status.get("mode") != "COMPETITION_DEMO":
        errors.append("source-status.json must use schema_version=1 and mode=COMPETITION_DEMO")
    if feed.get("schema_version") != 1:
        errors.append("intelligence-feed.json must use schema_version=1")
    if summary.get("schema_version") != 1:
        errors.append("intelligence-summary.json must use schema_version=1")

    try:
        validate_candidate_lane(status, feed, active_ids=expected_sources, required=require_candidate_lane)
    except (OSError, ValueError) as error:
        errors.append(str(error))

    status_sources = status.get("sources")
    try:
        status_source_ids = source_ids(status_sources)
    except ValueError as error:
        errors.append(str(error))
        status_sources = []
        status_source_ids = []
    if set(status_source_ids) != expected_sources or len(status_source_ids) != len(expected_sources):
        errors.append(f"source-status source IDs invalid: {status_source_ids}")

    for source in status_sources:
        if not isinstance(source, dict):
            continue
        source_id = source.get("source_id") or "unknown"
        catalog_row = catalog_rows.get(source_id)
        if catalog_row is None:
            errors.append(f"{source_id}: source is not in the source catalog")
            continue
        if source.get("source_role") != catalog_row.get("role"):
            errors.append(
                f"{source_id}: source_role {source.get('source_role')!r} "
                f"does not match catalog role {catalog_row.get('role')!r}"
            )
        if source.get("integration_status") != catalog_row.get("status"):
            errors.append(
                f"{source_id}: integration_status {source.get('integration_status')!r} "
                f"does not match catalog status {catalog_row.get('status')!r}"
            )
        if catalog_row.get("status") != "PRODUCTION_ACTIVE":
            errors.append(f"{source_id}: non-production catalog source cannot appear as a production source")
        if _credentialed_url(source.get("source_url")):
            errors.append(f"{source_id}: source_url must be a credential-free HTTPS URL")

    candidate_catalog = status.get("candidate_catalog")
    if not isinstance(candidate_catalog, list):
        errors.append("source-status.json must carry a candidate_catalog array")
        candidate_catalog = []
    expected_candidates = {
        source_id
        for source_id in (catalog.get("promotion_plan") or [])
        if catalog_rows.get(source_id, {}).get("status") != "PRODUCTION_ACTIVE"
    }
    published_candidates = {
        candidate["source_id"]
        for candidate in candidate_catalog
        if isinstance(candidate, dict) and isinstance(candidate.get("source_id"), str)
    }
    if published_candidates != expected_candidates:
        errors.append(
            f"candidate_catalog must cover exactly the promotion plan {sorted(expected_candidates)}"
        )
    for candidate in candidate_catalog:
        if not isinstance(candidate, dict):
            errors.append("candidate_catalog entries must be objects")
            continue
        candidate_id = candidate.get("source_id") or "unknown"
        catalog_row = catalog_rows.get(candidate_id)
        if catalog_row is None:
            errors.append(f"{candidate_id}: candidate is not in the source catalog")
            continue
        if candidate_id in expected_sources:
            errors.append(f"{candidate_id}: production source must not appear as a candidate")
        if candidate.get("source_role") != catalog_row.get("role"):
            errors.append(
                f"{candidate_id}: candidate source_role {candidate.get('source_role')!r} "
                f"does not match catalog role {catalog_row.get('role')!r}"
            )
        if candidate.get("integration_status") != catalog_row.get("status"):
            errors.append(
                f"{candidate_id}: candidate integration_status {candidate.get('integration_status')!r} "
                f"does not match catalog status {catalog_row.get('status')!r}"
            )
        if catalog_row.get("status") == "PRODUCTION_ACTIVE" or candidate.get("integration_status") == "PRODUCTION_ACTIVE":
            errors.append(f"{candidate_id}: a source that has not passed canary cannot be labelled production")
        if candidate.get("promotion_eligible") is not False:
            errors.append(f"{candidate_id}: candidate promotion_eligible must be false until the window verifier passes")
        for field in ("public_usage_notice", "retention_class"):
            if catalog_row.get(field) and candidate.get(field) != catalog_row[field]:
                errors.append(f"{candidate_id}: candidate must carry the catalog {field}")
        if _credentialed_url(candidate.get("source_url")):
            errors.append(f"{candidate_id}: candidate source_url must be a credential-free HTTPS URL")

    source_summary = feed.get("source_summary")
    if not isinstance(source_summary, dict) or set(source_summary) != expected_sources:
        errors.append(
            f"intelligence-feed source_summary must cover exactly {sorted(expected_sources)}"
        )

    items = feed.get("items")
    if not isinstance(items, list):
        errors.append("intelligence-feed.json items must be an array")
        items = []

    feed_source_ids = {
        item.get("source_id")
        for item in items
        if isinstance(item, dict) and isinstance(item.get("source_id"), str)
    }
    unknown_feed_sources = sorted(feed_source_ids - expected_sources)
    if unknown_feed_sources:
        errors.append(f"feed contains sources outside active policy: {unknown_feed_sources}")

    stable_ids = [item.get("stable_id") for item in items if isinstance(item, dict)]
    missing_stable_ids = sum(not stable_id for stable_id in stable_ids)
    if missing_stable_ids:
        errors.append(f"feed contains {missing_stable_ids} item(s) without stable_id")
    duplicate_stable_ids = sorted(
        stable_id for stable_id in set(stable_ids) if stable_id and stable_ids.count(stable_id) > 1
    )
    if duplicate_stable_ids:
        errors.append(f"duplicate stable_id values: {duplicate_stable_ids[:10]}")

    for item in items:
        if not isinstance(item, dict):
            errors.append("feed contains a non-object item")
            continue
        stable_id = item.get("stable_id") or "unknown"
        if not str(item.get("official_url") or "").startswith("https://"):
            errors.append(f"{stable_id}: official_url must be HTTPS")
        elif _credentialed_url(item.get("official_url")):
            errors.append(f"{stable_id}: official_url must not carry credentials")
        if not item.get("content_sha256"):
            errors.append(f"{stable_id}: missing content_sha256")
        if not isinstance(item.get("reason_codes"), list):
            errors.append(f"{stable_id}: reason_codes must be an array")
        item_catalog_row = catalog_rows.get(item.get("source_id"))
        if item_catalog_row is not None:
            if item.get("catalog_role") != item_catalog_row.get("role"):
                errors.append(
                    f"{stable_id}: item catalog_role {item.get('catalog_role')!r} "
                    f"does not match catalog role {item_catalog_row.get('role')!r}"
                )
            if item.get("integration_status") != item_catalog_row.get("status"):
                errors.append(
                    f"{stable_id}: item integration_status {item.get('integration_status')!r} "
                    f"does not match catalog status {item_catalog_row.get('status')!r}"
                )

    eligible_count = sum(
        isinstance(item, dict) and item.get("eligibility") == "HOME_CANDIDATE"
        for item in items
    )
    if summary.get("total_items") != len(items):
        errors.append(
            f"summary total_items={summary.get('total_items')} does not match feed items={len(items)}"
        )
    if summary.get("eligible_items") != eligible_count:
        errors.append(
            "summary eligible_items="
            f"{summary.get('eligible_items')} does not match feed HOME_CANDIDATE={eligible_count}"
        )

    source_breakdown = summary.get("source_breakdown")
    if isinstance(source_breakdown, list):
        breakdown_total = sum(
            item.get("item_count", 0)
            for item in source_breakdown
            if isinstance(item, dict) and isinstance(item.get("item_count", 0), int)
        )
        if breakdown_total != len(items):
            errors.append(
                f"summary source_breakdown total={breakdown_total} does not match feed items={len(items)}"
            )
    else:
        errors.append("summary source_breakdown must be an array")

    if len(csv_rows) != len(items):
        errors.append(f"CSV rows={len(csv_rows)} does not match feed items={len(items)}")
    csv_ids = [row.get("stable_id") for row in csv_rows]
    if set(csv_ids) != set(stable_ids):
        errors.append("CSV stable_id set does not match intelligence-feed.json")

    return errors


def main(*, require_candidate_lane: bool = False) -> int:
    try:
        expected_sources = load_expected_sources()
        catalog = load_catalog()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"PUBLICATION_BUNDLE_FAIL {error}", file=sys.stderr)
        return 1

    try:
        status = load_json("source-status.json")
        feed = load_json("intelligence-feed.json")
        summary = load_json("intelligence-summary.json")
    except ValueError as error:
        print(f"PUBLICATION_BUNDLE_FAIL {error}", file=sys.stderr)
        return 1

    csv_path = DATA_DIR / "feed-export.csv"
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
    except FileNotFoundError:
        print("PUBLICATION_BUNDLE_FAIL missing file: apps/web/public/data/feed-export.csv", file=sys.stderr)
        return 1

    errors = bundle_errors(status, feed, summary, csv_rows, catalog, expected_sources, require_candidate_lane=require_candidate_lane)
    if errors:
        for error in errors:
            print(f"PUBLICATION_BUNDLE_FAIL {error}", file=sys.stderr)
        return 1

    items = feed.get("items") or []
    status_sources = status.get("sources") or []
    eligible_count = sum(
        isinstance(item, dict) and item.get("eligibility") == "HOME_CANDIDATE"
        for item in items
    )
    ratio = eligible_count / len(items) if items else 0.0
    if len(items) >= 20 and ratio >= 0.80:
        print(
            "PUBLICATION_BUNDLE_WARN "
            f"high_eligibility_ratio={ratio:.3f} eligible={eligible_count} total={len(items)}"
        )

    run_id = (status.get("latest_collection_run") or {}).get("collection_run_id")
    print(
        "PUBLICATION_BUNDLE_OK "
        f"run_id={run_id} items={len(items)} eligible={eligible_count} sources={len(status_sources)}"
    )
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--require-candidate-lane", action="store_true")
    args = parser.parse_args()
    raise SystemExit(main(require_candidate_lane=args.require_candidate_lane))
