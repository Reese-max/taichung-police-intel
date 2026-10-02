"""Isolated Pages observation lane for Issue #22 candidate sources."""
from __future__ import annotations

import importlib.util
from datetime import date, datetime
from pathlib import Path

from scripts.candidate_publication import load_candidate_publication_sources


ROOT = Path(__file__).resolve().parents[1]


def _bounded_session(source_url: str):
    canary_path = ROOT / "scripts" / "candidate-runtime-canary.py"
    spec = importlib.util.spec_from_file_location("issue22_candidate_runtime", canary_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate runtime transport is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.BoundedSession(source_url)


def collect_pages_candidate_lane(
    collector: dict,
    window_start: date,
    window_end: date,
    prior_status: dict | None,
    prior_feed: dict | None,
    now: datetime,
    next_at: str,
    *,
    session_factory=None,
) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Collect S-001/S-019/S-032 independently from the production-active lane."""
    catalog_sources = load_candidate_publication_sources()
    source_configs = collector["NEWS_LIST_SOURCES"]
    configured = {
        source_id: config
        for source_id, config in source_configs.items()
        if config.get("pages_candidate") is True
    }
    if set(configured) != set(catalog_sources):
        raise ValueError("Pages candidate collector scope does not match the approved Issue #22 scope")
    session_factory = session_factory or _bounded_session
    prior_sources = {
        item["source_id"]: item
        for item in (prior_status or {}).get("candidate_sources", [])
        if isinstance(item, dict) and item.get("source_id")
    }
    prior_items = [
        item for item in (prior_feed or {}).get("candidate_items", [])
        if isinstance(item, dict)
    ]
    source_status: list[dict] = []
    feed_items: list[dict] = []
    source_summary: dict[str, dict] = {}

    for source_id, config in configured.items():
        source_name = config["name"]
        source_url = config["list_url"]
        source_run_id = f"SR-CAND-{now:%Y%m%d}-{source_id[2:]}"
        previous = prior_sources.get(source_id, {})
        previous_lkg = previous.get("last_known_good")
        source_previous_items = [item for item in prior_items if item.get("source_id") == source_id]
        existing = {
            item["stable_key"]: {"content_sha256": item["content_sha256"]}
            for item in source_previous_items
            if item.get("stable_key") and item.get("content_sha256")
        }
        session = None
        error = None
        collected = None
        try:
            session = session_factory(source_url)
            collected = collector["collect_source"](
                session,
                source_id,
                window_start,
                window_end,
                existing,
                max_details=1,
            )
            if collected.get("source_health") not in {"PASS", "DEGRADED"}:
                raise ValueError("candidate collector did not return a usable source health")
        except Exception as caught:
            error = caught
        finally:
            close = getattr(session, "close", None) if session is not None else None
            if callable(close):
                close()

        checked_at = collector["timestamp"](now)
        if error is not None:
            freshness = collector["freshness_status"](
                previous.get("data_as_of"), now, *collector["SOURCE_FRESHNESS_POLICY"].get(source_id, (13, 24))
            )
            record = {
                "source_id": source_id,
                "source_name": source_name,
                "source_url": source_url,
                "current_source_run_id": source_run_id,
                "integration_status": "CANDIDATE",
                "promotion_eligible": False,
                "source_health": "FAILED",
                "window_completeness": "PARTIAL",
                "result": "FAILED",
                "window_item_count": None,
                "snapshot_item_count": previous.get("snapshot_item_count"),
                "snapshot_count": None,
                "manifest_sha256": None,
                "pagination": previous.get("pagination"),
                "data_as_of": previous.get("data_as_of"),
                "last_checked_at": checked_at,
                "last_success_at": previous.get("last_success_at"),
                "next_update_at": next_at,
                "last_known_good": previous_lkg,
                "freshness_status": freshness,
                "intelligence_gaps": collector["gap_reasons"](
                    {
                        "source_health": "FAILED",
                        "window_completeness": "PARTIAL",
                        "last_success_at": previous.get("last_success_at"),
                        "data_as_of": previous.get("data_as_of"),
                    },
                    previous_lkg,
                    freshness,
                ),
                "error_code": type(error).__name__.upper()[:64],
            }
            for prior_item in source_previous_items:
                feed_items.append({
                    **prior_item,
                    "change_type": "LKG",
                    "source_health": "FAILED",
                    "eligibility": "INELIGIBLE_CANDIDATE_SOURCE_FAILED",
                    "fetched_at": checked_at,
                    "integration_status": "CANDIDATE",
                    "promotion_eligible": False,
                })
        else:
            assert collected is not None
            collected_items = collected.get("items", [])
            dates = [item["published_at"] for item in collected_items if item.get("published_at")]
            data_as_of = max(dates, default=previous.get("data_as_of"))
            freshness = collector["freshness_status"](
                data_as_of, now, *collector["SOURCE_FRESHNESS_POLICY"].get(source_id, (13, 24))
            )
            completeness = collected["window_completeness"]
            complete = completeness in {"COMPLETE_ZERO", "COMPLETE_WITH_ITEMS"}
            result = collector["result_for"](completeness, collected.get("window_item_count") or 0)
            candidate_lkg = {
                "source_run_id": source_run_id,
                "completed_at": checked_at,
                "manifest_sha256": collected["manifest_sha256"],
                "snapshot_item_count": collected["snapshot_item_count"],
                "snapshot_count": len(collected["snapshots"]),
            } if complete else previous_lkg
            last_success_at = checked_at if complete else previous.get("last_success_at")
            record = {
                "source_id": source_id,
                "source_name": source_name,
                "source_url": source_url,
                "current_source_run_id": source_run_id,
                "integration_status": "CANDIDATE",
                "promotion_eligible": False,
                "source_health": collected["source_health"],
                "window_completeness": completeness,
                "result": result,
                "window_item_count": collected["window_item_count"],
                "snapshot_item_count": collected["snapshot_item_count"],
                "snapshot_count": len(collected["snapshots"]),
                "manifest_sha256": collected["manifest_sha256"],
                "pagination": collected.get("pagination"),
                "data_as_of": data_as_of,
                "last_checked_at": checked_at,
                "last_success_at": last_success_at,
                "next_update_at": next_at,
                "last_known_good": candidate_lkg,
                "freshness_status": freshness,
                "intelligence_gaps": collector["gap_reasons"](
                    {
                        "source_health": collected["source_health"],
                        "window_completeness": completeness,
                        "last_success_at": last_success_at,
                        "data_as_of": data_as_of,
                    },
                    candidate_lkg,
                    freshness,
                ),
            }
            window_items = [
                item for item in collected_items
                if item.get("published_at")
                and window_start <= date.fromisoformat(item["published_at"][:10]) <= window_end
            ]
            previous_hashes = {item.get("content_sha256") for item in source_previous_items}
            for item in window_items:
                projected = collector["project_feed_item"](
                    item=item,
                    source_id=source_id,
                    source_name=source_name,
                    source_url=source_url,
                    freshness=freshness,
                    source_health=record["source_health"],
                    window_completeness=completeness,
                    data_as_of=data_as_of,
                    fetched_at=checked_at,
                    previous_sha256s=previous_hashes,
                )
                projected.update({
                    "stable_key": item["stable_key"],
                    "change_type": "CANDIDATE_OBSERVATION",
                    "eligibility": "INELIGIBLE_CANDIDATE",
                    "integration_status": "CANDIDATE",
                    "promotion_eligible": False,
                })
                feed_items.append(projected)

        record["intelligence_gaps"] = list(record.get("intelligence_gaps") or [])
        if record["freshness_status"] == "NO_DATA" and "NO_DATA_AS_OF" not in record["intelligence_gaps"]:
            record["intelligence_gaps"].append("NO_DATA_AS_OF")
        source_status.append(record)
        source_summary[source_id] = {
            "health": record["source_health"],
            "freshness": record["freshness_status"],
            "window_completeness": record["window_completeness"],
            "item_count": sum(item.get("source_id") == source_id for item in feed_items),
            "integration_status": "CANDIDATE",
            "promotion_eligible": False,
        }

    unique_items: dict[str, dict] = {}
    for item in feed_items:
        unique_items.setdefault(item["stable_id"], item)
    return source_status, list(unique_items.values()), source_summary
