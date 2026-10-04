#!/usr/bin/env python3
"""Publish bounded synthetic query projections using the existing Query Domain.

This is a saved replay artifact, never a source collector or live query service.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from intel_v2 import query_domain


def build_replay(session: dict) -> dict:
    if session.get("synthetic") is not True or session.get("schema_version") != 1:
        raise ValueError("Only an explicitly synthetic, versioned session can be published")
    updates = session["updates"]
    for update in updates:
        if update["rights"] != "SYNTHETIC_NO_REAL_ROAD":
            raise ValueError("Replay rights must be explicitly synthetic")
        if query_domain.digest(update["payload"]) != update["content_sha256"]:
            raise ValueError("Replay payload hash mismatch")
    snapshots = {}
    for point in session["reopen_points"]:
        as_of = point["reopened_at"]
        seen = [row for row in updates if row["acquired_at"] <= as_of and row["origin"] == "OFFICIAL"]
        docs = {}
        for row in sorted(seen, key=lambda row: (row["acquired_at"], row["update_id"])):
            docs.setdefault(row["document_id"], []).append(row)
        events, metadata = [], {}
        for document_id, versions in docs.items():
            latest = versions[-1]
            payload = latest["payload"]
            event_id = f"PE-SYN-{document_id}"
            history = []
            previous_fields = None
            for version in versions:
                fields = {key: version["payload"].get(key) for key in (
                    "road", "district", "status", "effective_from", "effective_to"
                )}
                fields["daily"] = f'{version["payload"]["daily"]["start"]}–{version["payload"]["daily"]["end"]}'
                if fields == previous_fields:
                    continue  # Repeat retrieval and formatting cannot create material revisions.
                history.append({
                    "document_version_id": version["update_id"],
                    "observed_at": version["acquired_at"],
                    "published_at": version["published_at"],
                    "effective_at": version["payload"].get("effective_from"),
                    "fields": fields,
                })
                previous_fields = fields
            event = {
                "schema_version": 1, "public_event_id": event_id,
                "canonical_title": payload["text"], "event_type": "traffic_control",
                "district_id": payload["district"], "location_candidates": [payload["district"]],
                "location_ids": [payload["district"]], "agency_ids": [],
                "independent_source_ids": [latest["source_id"]], "independent_source_count": 1,
                "fusion_status": "CANDIDATE", "source_state": "SYNTHETIC",
                "event_status": payload["status"], "start_at": payload.get("effective_from"),
                "end_at": payload.get("effective_to"), "created_at": versions[0]["acquired_at"],
                "updated_at": latest["acquired_at"], "version_history": history,
                "linked_document_versions": [{
                    "document_id": document_id, "document_version_id": row["document_version_id"],
                    "source_id": latest["source_id"],
                    "official_url": f'https://synthetic.invalid/{document_id}/{row["document_version_id"]}',
                    "evidence_locator": f'fixture:{session["scenario_id"]}#{row["document_version_id"]}',
                } for row in history],
            }
            events.append(event)
            metadata[event_id] = {
                "road": payload["road"], "daily": payload["daily"],
                "published_at": latest["published_at"], "acquired_at": latest["acquired_at"],
                "content_sha256": latest["content_sha256"], "version_history": history,
                "source_version": len(history), "origin": "SYNTHETIC",
                "document_id": document_id,
                "payloads": [{"update_id": row["update_id"], "payload": row["payload"],
                              "content_sha256": row["content_sha256"]} for row in versions],
            }
        store = query_domain.build_event_store(events, generated_at=as_of)
        projected = query_domain.query_events(store, {"limit": 100})["results"]
        for event in projected:
            event.update(metadata[event["public_event_id"]])
            event["comparison"] = query_domain.compare_event_versions(store, event["public_event_id"])
        collections = [row for row in session["collections"] if row["observed_at"] <= as_of]
        latest_by_source = {}
        for row in collections:
            latest_by_source[row["source_id"]] = row
        gaps = []
        for source_id, row in latest_by_source.items():
            if source_id in row.get("unreachable_source_ids", []):
                gaps.append({"source_id": source_id, "status": "FAILED", "reason": "合成來源連線失敗；保留最後取得版本，不能推定解除"})
            elif source_id in row.get("partial_source_ids", []):
                gaps.append({"source_id": source_id, "status": "PARTIAL", "reason": "合成来源涵蓋不完整，零結果不能推定無事件"})
            if row.get("snapshot_complete") and source_id == "S-SYN-ROAD":
                acquired = {u["update_id"] for u in seen if u["source_id"] == source_id}
                if "UPD-005" in acquired and "UPD-005" not in row["visible_update_ids"]:
                    gaps.append({"source_id": source_id, "status": "PENDING_UPDATE", "reason": "已取得的延期公告不在後續快照；保留舊版本並標待更新"})
        snapshots[point["reopen_id"]] = {
            "as_of": as_of, "note": point["note"], "events": projected,
            "generation_id": store["generation_id"], "store_sha256": store["store_sha256"],
            "status": "PARTIAL" if gaps else "SAVED_SYNTHETIC_SNAPSHOT",
            "snapshot_complete": not gaps, "source_gaps": gaps,
        }
    result = {
        "schema_version": 1, "mode": "SYNTHETIC_REPLAY", "namespace": "demo:commute",
        "data_cutoff": session["data_cutoff"], "notice": session["notice"],
        "scenario_id": session["scenario_id"], "source_fixture_sha256": query_domain.digest(session),
        "projection_version": "public-query-replay/v1", "query_domain_version": hashlib.sha256(
            (ROOT / "intel_v2/query_domain.py").read_bytes()).hexdigest(),
        "default_snapshot": "R3", "snapshots": snapshots,
        "capabilities": {"D1": "CAPABILITY_NOT_AVAILABLE", "D2": "CAPABILITY_NOT_AVAILABLE"},
    }
    result["bundle_sha256"] = query_domain.digest(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=ROOT / "eval/gold/v6/commute-reopen-v1/session.json")
    parser.add_argument("--output", type=Path, default=ROOT / "apps/web/public/data/public-query-replay.json")
    args = parser.parse_args()
    bundle = build_replay(json.loads(args.fixture.read_text(encoding="utf-8")))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f'PUBLIC_QUERY_REPLAY_OK snapshots={len(bundle["snapshots"])} sha256={bundle["bundle_sha256"]}')


if __name__ == "__main__":
    main()
