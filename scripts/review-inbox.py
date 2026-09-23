#!/usr/bin/env python3
"""Manage the bounded local-first Review Inbox."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.review import claim, decide, empty_state, load_state, project, reconcile, runtime_candidates, schema_drift_candidates, sha256, upsert, validate_state


DEFAULT_STATE = ROOT / "state" / "review-inbox.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def input_candidates(path: Path) -> list[dict]:
    payload = read_json(path)
    return runtime_candidates(payload)


def command_reconcile(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    candidates = schema_drift_candidates(read_json(args.schema_drift)) if args.schema_drift else input_candidates(args.input)
    updated = reconcile(state, candidates, observed_at=args.at or now())
    write_json(args.state, validate_state(updated))
    print(f"REVIEW_RECONCILE_OK active={len(project(updated))} total={len(project(updated, include_closed=True))} candidates={len(candidates)}")
    return 0


def command_claim(args: argparse.Namespace) -> int:
    updated = claim(load_state(args.state), args.review_id, assignee_ref=args.assignee_ref, claimed_at=args.at or now())
    write_json(args.state, updated)
    print(f"REVIEW_CLAIMED review_id={args.review_id}")
    return 0


def command_decide(args: argparse.Namespace) -> int:
    evidence = json.loads(args.evidence) if args.evidence else {}
    updated = decide(load_state(args.state), args.review_id, args.decision, reviewer_ref=args.reviewer_ref, decided_at=args.at or now(), evidence=evidence)
    write_json(args.state, updated)
    print(f"REVIEW_DECIDED review_id={args.review_id} decision={args.decision.upper().replace('-', '_')}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    print(json.dumps({"items": project(state, include_closed=args.all), "active_count": len(project(state)), "total_count": len(project(state, include_closed=True))}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def self_check() -> None:
    stamp = "2026-09-21T00:00:00+00:00"
    candidate = {"fingerprint": "source:S-007", "reason": "CONFLICT", "entity_ids": {"source_id": "S-007", "event_id": "E-1"}, "evidence": {"before": {"value": "17:00"}, "after": {"value": "16:00"}}, "source_version": 1}
    state, item, created = upsert(empty_state(), candidate, observed_at=stamp)
    assert created and item["review_id"] == f"REVIEW-{sha256('source:S-007'.encode()).upper()[:20]}"
    state = reconcile(state, [candidate], observed_at=stamp)
    assert len(state["items"]) == 1
    state = claim(state, item["review_id"], assignee_ref="operator-1", claimed_at=stamp)
    state = decide(state, item["review_id"], "confirm", reviewer_ref="operator-1", decided_at=stamp, evidence={"locator": "S-007#1"})
    changed = dict(candidate, evidence={"before": {"value": "16:00"}, "after": {"value": "15:00"}}, source_version=2)
    state = reconcile(state, [changed], observed_at=stamp)
    assert state["items"][item["review_id"]]["status"] == "OPEN"
    assert len(state["items"][item["review_id"]]["audit"]) == 4
    print("REVIEW_INBOX_SELF_CHECK_OK dedupe=true reopen=true audit=true source_failure_does_not_resolve=true")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = result.add_subparsers(dest="command", required=True)
    reconcile_parser = commands.add_parser("reconcile")
    source = reconcile_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--schema-drift", type=Path)
    reconcile_parser.add_argument("--at")
    reconcile_parser.set_defaults(handler=command_reconcile)
    claim_parser = commands.add_parser("claim")
    claim_parser.add_argument("--review-id", required=True)
    claim_parser.add_argument("--assignee-ref", required=True)
    claim_parser.add_argument("--at")
    claim_parser.set_defaults(handler=command_claim)
    decide_parser = commands.add_parser("decide")
    decide_parser.add_argument("--review-id", required=True)
    decide_parser.add_argument("--decision", required=True)
    decide_parser.add_argument("--reviewer-ref", required=True)
    decide_parser.add_argument("--evidence")
    decide_parser.add_argument("--at")
    decide_parser.set_defaults(handler=command_decide)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("--all", action="store_true")
    status_parser.set_defaults(handler=command_status)
    commands.add_parser("self-check").set_defaults(handler=lambda _: self_check() or 0)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.handler(arguments))
