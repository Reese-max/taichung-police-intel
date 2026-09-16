#!/usr/bin/env python3
"""Build and query a deterministic read-only projection of GovIntel publications.

The query store is never canonical truth.  Every projected row carries the IDs and
hashes needed to return to the checked-in publication artifacts, and the whole store
can be rebuilt from those artifacts at any time.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = ROOT / "apps/web/public/data/intelligence-feed.json"
DEFAULT_STATUS = ROOT / "apps/web/public/data/source-status.json"
DEFAULT_BRIEF = ROOT / "apps/web/public/data/v2-daily-brief.json"
DEFAULT_OUTPUT = ROOT / "apps/web/public/data/query-store.json"
SCHEMA_VERSION = 1


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> tuple[Any, str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), sha256_bytes(raw)


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def project_feed_item(item: dict[str, Any], feed_hash: str) -> dict[str, Any]:
    stable_id = _string(item.get("stable_id"))
    title = _string(item.get("title"))
    source_id = _string(item.get("source_id"))
    if not stable_id or not title or not source_id:
        raise ValueError("feed item missing stable_id/title/source_id")
    official_url = _string(item.get("official_url"))
    if official_url is not None and not official_url.startswith("https://"):
        raise ValueError(f"non-HTTPS official_url for {stable_id}")
    return {
        "record_type": "publication_item",
        "canonical_id": stable_id,
        "title": title,
        "source_id": source_id,
        "official_url": official_url,
        "published_at": item.get("published_at"),
        "data_as_of": item.get("data_as_of"),
        "fetched_at": item.get("fetched_at"),
        "change_type": item.get("change_type"),
        "freshness_status": item.get("freshness_status"),
        "source_health": item.get("source_health"),
        "window_completeness": item.get("window_completeness"),
        "committee": item.get("committee") or "",
        "next_milestone": item.get("next_milestone"),
        "evidence_count": int(item.get("evidence_count") or 0),
        "content_sha256": item.get("content_sha256"),
        "trust_tier": "CANONICAL_PUBLICATION",
        "canonical_ref": {
            "artifact": "intelligence-feed.json",
            "artifact_sha256": feed_hash,
            "stable_id": stable_id,
        },
    }


def project_source(source: dict[str, Any], status_hash: str) -> dict[str, Any]:
    source_id = _string(source.get("source_id"))
    if not source_id:
        raise ValueError("source status row missing source_id")
    return {
        "source_id": source_id,
        "name": source.get("name"),
        "source_health": source.get("source_health"),
        "window_completeness": source.get("window_completeness"),
        "result": source.get("result"),
        "last_checked_at": source.get("last_checked_at"),
        "data_as_of": source.get("data_as_of"),
        "freshness_status": source.get("freshness_status"),
        "canonical_ref": {
            "artifact": "source-status.json",
            "artifact_sha256": status_hash,
            "source_id": source_id,
        },
    }


def build_store(feed: dict[str, Any], status: dict[str, Any], brief: dict[str, Any], hashes: dict[str, str]) -> dict[str, Any]:
    feed_items = feed.get("items")
    sources = status.get("sources")
    if not isinstance(feed_items, list) or not isinstance(sources, list):
        raise ValueError("canonical feed/status arrays are missing")

    projected = [project_feed_item(item, hashes["feed"]) for item in feed_items if isinstance(item, dict)]
    projected.sort(key=lambda row: (row["canonical_id"], row["source_id"]))
    if len({row["canonical_id"] for row in projected}) != len(projected):
        raise ValueError("duplicate canonical_id in publication projection")

    source_rows = [project_source(source, hashes["status"]) for source in sources if isinstance(source, dict)]
    source_rows.sort(key=lambda row: row["source_id"])
    if len({row["source_id"] for row in source_rows}) != len(source_rows):
        raise ValueError("duplicate source_id in source-status projection")

    generation_material = {
        "schema_version": SCHEMA_VERSION,
        "feed_sha256": hashes["feed"],
        "status_sha256": hashes["status"],
        "brief_sha256": hashes["brief"],
    }
    generation_id = sha256_bytes(canonical_json(generation_material))

    return {
        "schema_version": SCHEMA_VERSION,
        "generation_id": generation_id,
        "generated_from": {
            "collection_run_id": feed.get("collection_run_id"),
            "feed_generated_at": feed.get("generated_at"),
            "status_generated_at": status.get("generated_at"),
            "brief_generated_at": brief.get("generated_at"),
            "feed_sha256": hashes["feed"],
            "status_sha256": hashes["status"],
            "brief_sha256": hashes["brief"],
        },
        "counts": {
            "publication_items": len(projected),
            "sources": len(source_rows),
        },
        "items": projected,
        "sources": source_rows,
    }


def build_from_paths(feed_path: Path, status_path: Path, brief_path: Path) -> dict[str, Any]:
    feed, feed_hash = load_json(feed_path)
    status, status_hash = load_json(status_path)
    brief, brief_hash = load_json(brief_path)
    if not all(isinstance(value, dict) for value in (feed, status, brief)):
        raise ValueError("canonical artifacts must be JSON objects")
    return build_store(
        feed,
        status,
        brief,
        {"feed": feed_hash, "status": status_hash, "brief": brief_hash},
    )


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        tmp = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def query_store(
    store: dict[str, Any],
    *,
    text: str | None = None,
    source_id: str | None = None,
    change_type: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    rows = store.get("items")
    if not isinstance(rows, list):
        raise ValueError("query store items missing")
    needle = text.casefold().strip() if text else None
    selected = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if source_id and row.get("source_id") != source_id:
            continue
        if change_type and row.get("change_type") != change_type:
            continue
        if needle:
            haystack = " ".join(
                str(row.get(field) or "") for field in ("title", "committee", "source_id", "canonical_id")
            ).casefold()
            if needle not in haystack:
                continue
        selected.append(row)
    selected.sort(key=lambda row: (row.get("published_at") or "", row["canonical_id"]), reverse=True)
    total = len(selected)
    results = selected[:limit]
    return {
        "schema_version": 1,
        "query_generation_id": store.get("generation_id"),
        "total_matches": total,
        "result_count": len(results),
        "truncated": total > len(results),
        "results": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build")
    build.add_argument("--feed", type=Path, default=DEFAULT_FEED)
    build.add_argument("--status", type=Path, default=DEFAULT_STATUS)
    build.add_argument("--brief", type=Path, default=DEFAULT_BRIEF)
    build.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)

    query = sub.add_parser("query")
    query.add_argument("--store", type=Path, default=DEFAULT_OUTPUT)
    query.add_argument("--q")
    query.add_argument("--source-id")
    query.add_argument("--change-type")
    query.add_argument("--limit", type=int, default=20)

    sub.add_parser("self-check")
    return parser.parse_args()


def self_check() -> None:
    first = build_from_paths(DEFAULT_FEED, DEFAULT_STATUS, DEFAULT_BRIEF)
    second = build_from_paths(DEFAULT_FEED, DEFAULT_STATUS, DEFAULT_BRIEF)
    assert first["generation_id"] == second["generation_id"]
    assert [row["canonical_id"] for row in first["items"]] == [row["canonical_id"] for row in second["items"]]
    result = query_store(first, source_id="S-004", limit=5)
    assert result["query_generation_id"] == first["generation_id"]
    assert all(row["source_id"] == "S-004" for row in result["results"])
    print(
        "QUERY_STORE_SELF_CHECK_OK "
        f"generation={first['generation_id'][:12]} items={first['counts']['publication_items']} sources={first['counts']['sources']}"
    )


def main() -> int:
    args = parse_args()
    if args.command == "build":
        store = build_from_paths(args.feed, args.status, args.brief)
        atomic_write_json(args.output, store)
        print(
            "QUERY_STORE_BUILT "
            f"generation={store['generation_id']} items={store['counts']['publication_items']} output={args.output}"
        )
    elif args.command == "query":
        store, _ = load_json(args.store)
        result = query_store(
            store,
            text=args.q,
            source_id=args.source_id,
            change_type=args.change_type,
            limit=args.limit,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        self_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
