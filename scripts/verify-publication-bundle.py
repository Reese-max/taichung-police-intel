from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "apps" / "web" / "public" / "data"
EXPECTED_SOURCES = {"S-004", "S-006", "S-007", "S-009", "S-029"}


def load_json(name: str) -> dict:
    path = DATA_DIR / name
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ValueError(f"missing file: {path.relative_to(ROOT)}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {path.relative_to(ROOT)}: {error}") from error


def main() -> int:
    errors: list[str] = []

    try:
        status = load_json("source-status.json")
        feed = load_json("intelligence-feed.json")
        summary = load_json("intelligence-summary.json")
    except ValueError as error:
        print(f"PUBLICATION_BUNDLE_FAIL {error}", file=sys.stderr)
        return 1

    status_run = status.get("latest_collection_run") or {}
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

    status_sources = status.get("sources")
    if not isinstance(status_sources, list):
        errors.append("source-status.json sources must be an array")
        status_sources = []
    status_source_ids = [item.get("source_id") for item in status_sources if isinstance(item, dict)]
    if set(status_source_ids) != EXPECTED_SOURCES or len(status_source_ids) != len(EXPECTED_SOURCES):
        errors.append(f"source-status source IDs invalid: {status_source_ids}")

    source_summary = feed.get("source_summary")
    if not isinstance(source_summary, dict) or set(source_summary) != EXPECTED_SOURCES:
        errors.append(
            f"intelligence-feed source_summary must cover exactly {sorted(EXPECTED_SOURCES)}"
        )

    items = feed.get("items")
    if not isinstance(items, list):
        errors.append("intelligence-feed.json items must be an array")
        items = []

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
        if not item.get("content_sha256"):
            errors.append(f"{stable_id}: missing content_sha256")
        if not isinstance(item.get("reason_codes"), list):
            errors.append(f"{stable_id}: reason_codes must be an array")

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

    csv_path = DATA_DIR / "feed-export.csv"
    try:
        with csv_path.open(encoding="utf-8-sig", newline="") as handle:
            csv_rows = list(csv.DictReader(handle))
    except FileNotFoundError:
        errors.append("missing file: apps/web/public/data/feed-export.csv")
        csv_rows = []

    if len(csv_rows) != len(items):
        errors.append(f"CSV rows={len(csv_rows)} does not match feed items={len(items)}")
    csv_ids = [row.get("stable_id") for row in csv_rows]
    if set(csv_ids) != set(stable_ids):
        errors.append("CSV stable_id set does not match intelligence-feed.json")

    if errors:
        for error in errors:
            print(f"PUBLICATION_BUNDLE_FAIL {error}", file=sys.stderr)
        return 1

    ratio = eligible_count / len(items) if items else 0.0
    if len(items) >= 20 and ratio >= 0.80:
        print(
            "PUBLICATION_BUNDLE_WARN "
            f"high_eligibility_ratio={ratio:.3f} eligible={eligible_count} total={len(items)}"
        )

    run_id = next(iter(present_run_ids))
    print(
        "PUBLICATION_BUNDLE_OK "
        f"run_id={run_id} items={len(items)} eligible={eligible_count} sources={len(status_sources)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
