#!/usr/bin/env python3
"""Create and review local-first GovIntel correction signals."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.feedback import REASONS, TARGET_TYPES, create_feedback, empty_state, link_regression, review, statistics, validate_state


DEFAULT_STATE = ROOT / "state" / "feedback.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("feedback state must be an object")
    return validate_state(value)


def write_state(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(validate_state(value), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def json_object(value: str, label: str) -> dict:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


def json_refs(value: str | None) -> list[str | dict]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("evidence-refs must be a JSON array")
    return parsed


def command_add(args: argparse.Namespace) -> int:
    state, item, created = create_feedback(
        load_state(args.state),
        target_type=args.target_type,
        target_id=args.target_id,
        target_version=args.target_version,
        reason=args.reason,
        original_output_sha256=args.output_hash,
        corrected_expected_state=json_object(args.corrected_state, "corrected-state") if args.corrected_state else {},
        evidence_refs=json_refs(args.evidence_refs),
        linked_review_id=args.review_id,
        created_at=args.at or now(),
        model_version=args.model_version,
        parser_version=args.parser_version,
        registry_hash=args.registry_hash,
    )
    write_state(args.state, state)
    print(f"FEEDBACK_{'CREATED' if created else 'DEDUPED'} feedback_id={item['feedback_id']} reason={item['reason']}")
    return 0


def command_review(args: argparse.Namespace) -> int:
    state = review(load_state(args.state), args.feedback_id, args.status, reviewer_ref=args.reviewer_ref, decided_at=args.at or now())
    write_state(args.state, state)
    item = state["items"][args.feedback_id]
    fixture = item.get("regression_fixture")
    print(f"FEEDBACK_REVIEWED feedback_id={args.feedback_id} status={item['review_status']} regression={fixture['fixture_id'] if fixture else 'none'}")
    return 0


def command_link(args: argparse.Namespace) -> int:
    state = link_regression(load_state(args.state), args.feedback_id, args.fixture_id, reviewer_ref=args.reviewer_ref, linked_at=args.at or now())
    write_state(args.state, state)
    print(f"FEEDBACK_REGRESSION_LINKED feedback_id={args.feedback_id} fixture_id={args.fixture_id}")
    return 0


def command_stats(args: argparse.Namespace) -> int:
    print(json.dumps(statistics(load_state(args.state)), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def self_check() -> None:
    stamp = "2026-09-21T00:00:00+08:00"
    output_hash = "a" * 64
    state, item, created = create_feedback(
        empty_state(),
        target_type="EVENT",
        target_id="PE-demo",
        target_version="v1",
        reason="FALSE_MERGE",
        original_output_sha256=output_hash,
        corrected_expected_state={"same_event": False},
        evidence_refs=["S-036#fixture"],
        linked_review_id="REVIEW-demo",
        created_at=stamp,
        model_version="model:test",
        parser_version="parser:test",
        registry_hash="registry:test",
    )
    assert created and item["review_status"] == "NEW"
    state, duplicate, created_again = create_feedback(
        state,
        target_type="EVENT",
        target_id="PE-demo",
        target_version="v1",
        reason="FALSE_MERGE",
        original_output_sha256=output_hash,
        corrected_expected_state={"same_event": False},
        evidence_refs=["S-036#fixture"],
        linked_review_id="REVIEW-demo",
        created_at=stamp,
        model_version="model:test",
        parser_version="parser:test",
        registry_hash="registry:test",
    )
    assert not created_again and duplicate["feedback_id"] == item["feedback_id"]
    state = review(state, item["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:test", decided_at=stamp)
    assert state["items"][item["feedback_id"]]["regression_fixture"]["requires_gold_promotion_review"] is True
    summary = statistics(state)
    assert summary["by_reason"]["FALSE_MERGE"] == 1 and summary["by_status"]["ACCEPTED"] == 1
    print(f"FEEDBACK_SELF_CHECK_OK reasons={len(REASONS)} targets={len(TARGET_TYPES)} dedupe=true accepted_regression=true")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = result.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add")
    add.add_argument("--target-type", choices=sorted(TARGET_TYPES), required=True)
    add.add_argument("--target-id", required=True)
    add.add_argument("--target-version", required=True)
    add.add_argument("--reason", choices=sorted(REASONS), required=True)
    add.add_argument("--output-hash", required=True)
    add.add_argument("--corrected-state")
    add.add_argument("--evidence-refs")
    add.add_argument("--review-id")
    add.add_argument("--model-version", default="UNKNOWN")
    add.add_argument("--parser-version", default="UNKNOWN")
    add.add_argument("--registry-hash", default="UNKNOWN")
    add.add_argument("--at")
    add.set_defaults(handler=command_add)

    reviewed = commands.add_parser("review")
    reviewed.add_argument("--feedback-id", required=True)
    reviewed.add_argument("--status", choices=["ACCEPTED", "REJECTED", "DUPLICATE"], required=True)
    reviewed.add_argument("--reviewer-ref", required=True)
    reviewed.add_argument("--at")
    reviewed.set_defaults(handler=command_review)

    linked = commands.add_parser("link-regression")
    linked.add_argument("--feedback-id", required=True)
    linked.add_argument("--fixture-id", required=True)
    linked.add_argument("--reviewer-ref", required=True)
    linked.add_argument("--at")
    linked.set_defaults(handler=command_link)

    commands.add_parser("stats").set_defaults(handler=command_stats)
    commands.add_parser("self-check").set_defaults(handler=lambda _: self_check() or 0)
    return result


if __name__ == "__main__":
    arguments = parser().parse_args()
    raise SystemExit(arguments.handler(arguments))
