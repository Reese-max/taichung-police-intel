#!/usr/bin/env python3
"""Version-bound, rebuildable metadata index; not a verification or truth store."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FEED = ROOT / "apps/web/public/data/intelligence-feed.json"
DEFAULT_STATUS = ROOT / "apps/web/public/data/source-status.json"
DEFAULT_BRIEF = ROOT / "apps/web/public/data/v2-daily-brief.json"
DEFAULT_OUTPUT = ROOT / "apps/web/public/data/query-store.json"
SCHEMA_VERSION = 2
PROJECTION_VERSION = "publication-metadata-v2"
EXPECTED_SOURCES = frozenset(("S-004", "S-006", "S-007", "S-009", "S-029"))
MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 10000
MAX_AGE_SECONDS = 16 * 60 * 60
HASH = re.compile(r"[a-f0-9]{64}\Z")
CHANGES = frozenset(("NEW", "REVISED", "STATUS_CHANGED", "DEADLINE_CHANGED", "CONFIRMED", "UNCHANGED", "LKG", "REMOVED"))


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_json(path: Path) -> tuple[Any, str]:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("artifact exceeds byte limit")
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), sha256_bytes(raw)


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a timezone-aware string")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid timestamp") from error
    if stamp.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return stamp.astimezone(timezone.utc)


def project_feed_item(item: dict[str, Any], feed_hash: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("invalid feed row; refusing silent omission")
    stable_id, title, source_id = (_string(item.get(k)) for k in ("stable_id", "title", "source_id"))
    if not stable_id or not title or not source_id:
        raise ValueError("feed item missing stable_id/title/source_id")
    if len(title) > 2000 or len(stable_id) > 256:
        raise ValueError("feed row exceeds field limit")
    official_url = _string(item.get("official_url"))
    if official_url is not None:
        parts = urlsplit(official_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise ValueError(f"invalid HTTPS official_url for {stable_id}")
    count = item.get("evidence_count", 0)
    if type(count) is not int or count < 0:
        raise ValueError("evidence_count must be a nonnegative integer")
    content_hash = item.get("content_sha256")
    if not isinstance(content_hash, str) or not HASH.fullmatch(content_hash):
        raise ValueError("missing content_sha256")
    for key in ("published_at", "data_as_of", "fetched_at"):
        if item.get(key) is not None:
            instant(item[key])
    return {
        "record_type": "publication_item", "canonical_id": stable_id, "title": title,
        "source_id": source_id, "official_url": official_url,
        **{key: item.get(key) for key in ("published_at", "data_as_of", "fetched_at", "change_type",
                                        "freshness_status", "source_health", "window_completeness")},
        "committee": str(item.get("committee") or ""),
        "next_milestone": item.get("next_milestone"),
        "evidence_count": count, "content_sha256": content_hash,
        "trust_tier": "CANONICAL_PUBLICATION",
        "canonical_ref": {"artifact": "intelligence-feed.json", "artifact_sha256": feed_hash, "stable_id": stable_id},
    }


def project_source(source: dict[str, Any], status_hash: str) -> dict[str, Any]:
    if not isinstance(source, dict):
        raise ValueError("invalid source row; refusing silent omission")
    sid, name = _string(source.get("source_id")), _string(source.get("source_name"))
    if not sid:
        raise ValueError("source status row missing source_id")
    if not name:
        raise ValueError(f"source status row missing source_name: {sid}")
    return {
        "source_id": sid, "name": name,
        **{key: source.get(key) for key in ("source_health", "window_completeness", "result",
                                          "last_checked_at", "data_as_of", "freshness_status")},
        "canonical_ref": {"artifact": "source-status.json", "artifact_sha256": status_hash, "source_id": sid},
    }


def build_store(feed: dict[str, Any], status: dict[str, Any], brief: dict[str, Any], hashes: dict[str, str]) -> dict[str, Any]:
    if not all(isinstance(value, dict) for value in (feed, status, brief, hashes)):
        raise ValueError("canonical artifacts must be objects")
    if any(type(d.get("schema_version")) is not int or d["schema_version"] != 1 for d in (feed, status, brief)):
        raise ValueError("unsupported canonical schema_version")
    if set(hashes) != {"feed", "status", "brief"} or any(not isinstance(v, str) or not HASH.fullmatch(v) for v in hashes.values()):
        raise ValueError("invalid canonical artifact hashes")
    run = _string(feed.get("collection_run_id"))
    status_run = status.get("latest_collection_run")
    if not isinstance(status_run, dict):
        raise ValueError("missing collection run")
    if (not run or run != status_run.get("collection_run_id") or run != brief.get("source_collection_run_id") or
            feed.get("generated_at") != status.get("generated_at") or
            status.get("generated_at") != brief.get("source_status_generated_at")):
        raise ValueError("cross-generation publication artifacts")
    for d in (feed, status, brief):
        instant(d.get("generated_at"))
    rows, sources = feed.get("items"), status.get("sources")
    if not isinstance(rows, list) or not isinstance(sources, list) or len(rows) > MAX_ROWS:
        raise ValueError("invalid or unbounded canonical arrays")
    projected = [project_feed_item(item, hashes["feed"]) for item in rows]
    projected.sort(key=lambda r: r["canonical_id"])
    if len({r["canonical_id"] for r in projected}) != len(projected):
        raise ValueError("duplicate canonical_id in publication projection")
    source_rows = [project_source(s, hashes["status"]) for s in sources]
    source_rows.sort(key=lambda r: r["source_id"])
    source_ids = {s["source_id"] for s in source_rows}
    if len(source_ids) != len(source_rows) or source_ids != EXPECTED_SOURCES:
        raise ValueError("source coverage must match the approved P0 set exactly")
    if any(row["source_id"] not in source_ids for row in projected):
        raise ValueError("feed references unknown source")
    material = {"schema_version": SCHEMA_VERSION, "projection_version": PROJECTION_VERSION, "artifact_hashes": hashes}
    store = {
        "schema_version": SCHEMA_VERSION, "projection_version": PROJECTION_VERSION,
        "generation_id": sha256_bytes(canonical_json(material)),
        "capabilities": ["publication_metadata", "source_health"],
        "generated_from": {"collection_run_id": run,
                           "feed_generated_at": feed["generated_at"], "status_generated_at": status["generated_at"],
                           "brief_generated_at": brief["generated_at"],
                           "feed_sha256": hashes["feed"], "status_sha256": hashes["status"], "brief_sha256": hashes["brief"],
                           "collection_status": status_run.get("status"), "publication_status": brief.get("publication_status"),
                           "snapshot_complete": brief.get("snapshot_complete")},
        "counts": {"publication_items": len(projected), "sources": len(source_rows)},
        "items": projected, "sources": source_rows,
    }
    store["projection_sha256"] = sha256_bytes(canonical_json(store))
    return store


def validate_store(store):
    if not isinstance(store, dict) or store.get("schema_version") != SCHEMA_VERSION or store.get("projection_version") != PROJECTION_VERSION:
        raise ValueError("unsupported query store; rebuild from canonical artifacts")
    supplied = store.get("projection_sha256")
    actual = sha256_bytes(canonical_json({k: v for k, v in store.items() if k != "projection_sha256"}))
    if supplied != actual:
        raise ValueError("query projection hash mismatch")
    if not isinstance(store.get("items"), list) or not isinstance(store.get("sources"), list):
        raise ValueError("query store arrays missing")
    if {s.get("source_id") for s in store["sources"]} != EXPECTED_SOURCES or len(store["sources"]) != len(EXPECTED_SOURCES):
        raise ValueError("query store source coverage mismatch")
    if len(store["items"]) > MAX_ROWS:
        raise ValueError("query store item budget exceeded")


def build_from_paths(feed_path: Path, status_path: Path, brief_path: Path) -> dict[str, Any]:
    feed, feed_hash = load_json(feed_path)
    status, status_hash = load_json(status_path)
    brief, brief_hash = load_json(brief_path)
    return build_store(feed, status, brief, {"feed": feed_hash, "status": status_hash, "brief": brief_hash})


def atomic_write_json(path: Path, value: Any) -> None:
    validate_store(value)
    payload = canonical_json(value) + b"\n"
    if len(payload) > MAX_BYTES:
        raise ValueError("query store byte budget exceeded")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
            tmp = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def assess_scope(store, source_id, now):
    selected = [s for s in store["sources"] if not source_id or s["source_id"] == source_id]
    gaps = []
    if not selected:
        return "SOURCE_NOT_AVAILABLE", [{"source_id": source_id, "reason": "NOT_IN_APPROVED_SNAPSHOT"}]
    meta = store["generated_from"]
    if meta.get("collection_status") != "SUCCEEDED" or meta.get("publication_status") != "READY" or meta.get("snapshot_complete") is not True:
        gaps.append({"source_id": None, "reason": "INCOMPLETE_PUBLICATION"})
    try:
        stamps = [instant(meta[k]) for k in ("feed_generated_at", "status_generated_at", "brief_generated_at")]
        if any(t > now for t in stamps):
            gaps.append({"source_id": None, "reason": "FUTURE_PUBLICATION_TIME"})
        elif (now - min(stamps)).total_seconds() > MAX_AGE_SECONDS:
            gaps.append({"source_id": None, "reason": "STALE_SNAPSHOT"})
    except ValueError:
        gaps.append({"source_id": None, "reason": "UNKNOWN_PUBLICATION_TIME"})
    for source in selected:
        sid = source["source_id"]
        if (source.get("source_health") != "PASS" or source.get("window_completeness") not in ("COMPLETE_ZERO", "COMPLETE_WITH_ITEMS") or
                source.get("result") not in ("NEW_ITEMS", "NO_NEW_ITEM")):
            gaps.append({"source_id": sid, "reason": "SOURCE_INCOMPLETE", "source_health": source.get("source_health")})
        try:
            checked = instant(source.get("last_checked_at"))
            if checked > now:
                gaps.append({"source_id": sid, "reason": "FUTURE_CHECK_TIME"})
            elif (now - checked).total_seconds() > MAX_AGE_SECONDS:
                gaps.append({"source_id": sid, "reason": "STALE_SOURCE_CHECK"})
        except ValueError:
            gaps.append({"source_id": sid, "reason": "UNKNOWN_CHECK_TIME"})
    if any("UNKNOWN" in g["reason"] or "FUTURE" in g["reason"] for g in gaps):
        return "UNKNOWN", gaps
    if any("INCOMPLETE" in g["reason"] for g in gaps):
        return "PARTIAL", gaps
    return ("STALE" if gaps else "SNAPSHOT_RECENT"), gaps


def query_store(store: dict[str, Any], *, text=None, source_id=None, change_type=None,
                limit=20, cursor=None, expected_generation=None, now=None) -> dict[str, Any]:
    validate_store(store)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    for name, value, length in (("text", text, 512), ("source_id", source_id, 64), ("change_type", change_type, 64)):
        if value is not None and (not isinstance(value, str) or len(value) > length):
            raise ValueError(f"invalid {name}")
    if change_type and change_type not in CHANGES:
        raise ValueError("unknown change_type")
    if expected_generation is not None and expected_generation != store["generation_id"]:
        raise ValueError("query generation mismatch; retry against the requested snapshot")
    now = datetime.now(timezone.utc) if now is None else now
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("query clock must be timezone-aware")
    now = now.astimezone(timezone.utc)
    needle = text.casefold().strip() if text else None
    filter_hash = sha256_bytes(canonical_json([needle, source_id, change_type]))
    offset = 0
    if cursor is not None:
        try:
            if not isinstance(cursor, str) or len(cursor) > 1024:
                raise ValueError("cursor length/type")
            token = json.loads(base64.b64decode(cursor.encode(), altchars=b"-_", validate=True))
            if token.get("generation") != store["generation_id"] or token.get("filters") != filter_hash:
                raise ValueError("generation/filter mismatch")
            offset = token["offset"]
            if type(offset) is not int or not 0 <= offset <= MAX_ROWS:
                raise ValueError("offset")
        except (ValueError, TypeError, KeyError, AttributeError) as error:
            raise ValueError("invalid cursor: generation/filter/offset mismatch") from error
    selected = []
    for row in store["items"]:
        if source_id and row["source_id"] != source_id:
            continue
        if change_type and row["change_type"] != change_type:
            continue
        haystack = " ".join(str(row.get(k) or "") for k in ("title", "committee", "source_id", "canonical_id")).casefold()
        if needle and needle not in haystack:
            continue
        selected.append(row)
    def order(row):
        published = row.get("published_at")
        stamp = instant(published).timestamp() if published else float("-inf")
        return (-stamp, row["canonical_id"])
    selected.sort(key=order)
    total = len(selected)
    if offset > total:
        raise ValueError("cursor offset exceeds result set")
    results = selected[offset:offset + limit]
    has_more = offset + len(results) < total
    next_cursor = None
    if has_more:
        next_cursor = base64.urlsafe_b64encode(canonical_json({"generation": store["generation_id"],
                    "filters": filter_hash, "offset": offset + len(results)})).decode()
    assessment, gaps = assess_scope(store, source_id, now)
    return {
        "schema_version": 2, "query_generation_id": store["generation_id"],
        "canonical_artifact_hashes": {k: store["generated_from"][f"{k}_sha256"] for k in ("feed", "status", "brief")},
        "publication_deployment_verified": False,
        "data_status": assessment, "source_gaps": gaps,
        "source_status": [s for s in store["sources"] if not source_id or s["source_id"] == source_id],
        "answerable_no_match": total == 0 and not gaps,
        "answer_scope": "Matching publication metadata in this indexed snapshot; not all real-world events.",
        "queried_at": now.isoformat(), "search_scope": "TITLE_COMMITTEE_SOURCE_ID_ONLY",
        "total_matches": total, "result_count": len(results), "offset": offset,
        "truncated": total > len(results), "has_more": has_more, "next_cursor": next_cursor,
        "results": results,
    }


def parse_args():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    for name, default in (("feed", DEFAULT_FEED), ("status", DEFAULT_STATUS), ("brief", DEFAULT_BRIEF), ("output", DEFAULT_OUTPUT)):
        build.add_argument(f"--{name}", type=Path, default=default)
    query = sub.add_parser("query")
    query.add_argument("--store", type=Path, default=DEFAULT_OUTPUT)
    query.add_argument("--q")
    query.add_argument("--source-id")
    query.add_argument("--change-type")
    query.add_argument("--limit", type=int, default=20)
    query.add_argument("--cursor")
    query.add_argument("--expected-generation")
    sub.add_parser("self-check")
    return parser.parse_args()


def self_check():
    first = build_from_paths(DEFAULT_FEED, DEFAULT_STATUS, DEFAULT_BRIEF)
    second = build_from_paths(DEFAULT_FEED, DEFAULT_STATUS, DEFAULT_BRIEF)
    assert first == second
    result = query_store(first, source_id="S-004", limit=5)
    assert result["query_generation_id"] == first["generation_id"]
    assert all(r["source_id"] == "S-004" for r in result["results"])
    assert result["publication_deployment_verified"] is False
    print(f"QUERY_STORE_SELF_CHECK_OK generation={first['generation_id'][:12]} items={len(first['items'])} sources={len(first['sources'])}")


def main():
    args = parse_args()
    if args.command == "build":
        store = build_from_paths(args.feed, args.status, args.brief)
        atomic_write_json(args.output, store)
        print(f"QUERY_STORE_BUILT generation={store['generation_id']} items={len(store['items'])} output={args.output}")
    elif args.command == "query":
        store, _ = load_json(args.store)
        result = query_store(store, text=args.q, source_id=args.source_id, change_type=args.change_type,
                             limit=args.limit, cursor=args.cursor, expected_generation=args.expected_generation)
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        self_check()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
