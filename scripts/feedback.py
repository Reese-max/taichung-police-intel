#!/usr/bin/env python3
"""Create and review local-first GovIntel correction signals."""
from __future__ import annotations

import argparse
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from intel_v2.feedback import PROMOTION_TARGETS, REASONS, ROUTE_FIELDS, TARGET_TYPES, create_feedback, empty_state, link_regression, normalize_state, review, statistics, validate_state


DEFAULT_STATE = ROOT / "state" / "feedback.json"


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_state(path: Path) -> dict:
    if not path.exists():
        return empty_state()
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("feedback state must be an object")
    return normalize_state(value)


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
    routes = ",".join(fixture["promotion_targets"]) if fixture else "none"
    approval = fixture["requires_human_approval"] if fixture else False
    print(
        f"FEEDBACK_REVIEWED feedback_id={args.feedback_id} status={item['review_status']} "
        f"regression={fixture['fixture_id'] if fixture else 'none'} "
        f"routes={routes} human_approval={approval}"
    )
    return 0


def command_link(args: argparse.Namespace) -> int:
    state = link_regression(load_state(args.state), args.feedback_id, args.fixture_id, reviewer_ref=args.reviewer_ref, linked_at=args.at or now())
    write_state(args.state, state)
    print(f"FEEDBACK_REGRESSION_LINKED feedback_id={args.feedback_id} fixture_id={args.fixture_id}")
    return 0


def command_stats(args: argparse.Namespace) -> int:
    print(json.dumps(statistics(load_state(args.state)), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def assert_fails_closed(state, expected_message: str) -> None:
    try:
        normalize_state(state)
    except ValueError as error:
        assert expected_message in str(error), f"expected {expected_message!r} in {error}"
    else:  # pragma: no cover - every load-time guard must fail closed
        raise AssertionError(f"state must fail closed with: {expected_message}")


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
    accepted_fixture = state["items"][item["feedback_id"]]["regression_fixture"]
    assert accepted_fixture["requires_gold_promotion_review"] is True
    assert accepted_fixture["promotion_targets"] == ["ENTITY_REGISTRY", "EVENT_FUSION"]
    assert accepted_fixture["requires_human_approval"] is True and accepted_fixture["mutates_verified_truth"] is True
    state, irrelevant, _ = create_feedback(
        state,
        target_type="EVENT",
        target_id="PE-verified",
        target_version="v1",
        reason="NOT_RELEVANT",
        original_output_sha256=output_hash,
        corrected_expected_state={"relevance": "NOT_RELEVANT"},
        evidence_refs=["S-036#fixture"],
        linked_review_id="REVIEW-demo",
        created_at=stamp,
        model_version="model:test",
        parser_version="parser:test",
        registry_hash="registry:test",
    )
    try:
        create_feedback(
            state,
            target_type="EVENT",
            target_id="PE-verified",
            target_version="v1",
            reason="NOT_RELEVANT",
            original_output_sha256=output_hash,
            corrected_expected_state={"delete_event": True},
            created_at=stamp,
            model_version="model:test",
            parser_version="parser:test",
            registry_hash="registry:test",
        )
    except ValueError as error:
        assert "relevance-only" in str(error)
    else:  # pragma: no cover - the guard must fail closed
        raise AssertionError("NOT_RELEVANT feedback must not be able to retract verified truth")
    state = review(state, irrelevant["feedback_id"], "ACCEPTED", reviewer_ref="reviewer:test", decided_at=stamp)
    relevance_fixture = state["items"][irrelevant["feedback_id"]]["regression_fixture"]
    assert relevance_fixture["effect_scope"] == "RELEVANCE_ONLY"
    assert relevance_fixture["mutates_verified_truth"] is False
    assert relevance_fixture["promotion_targets"] == ["RELEVANCE_EVALUATION"]
    tampered_route = copy.deepcopy(state)
    tampered_route["items"][irrelevant["feedback_id"]]["regression_fixture"]["promotion_targets"] = ["ENTITY_REGISTRY"]
    try:
        validate_state(tampered_route)
    except ValueError as error:
        assert "promotion_targets" in str(error)
    else:  # pragma: no cover - the load-time guard must fail closed
        raise AssertionError("a rewritten promotion route must fail closed on load")
    tampered_truth = copy.deepcopy(state)
    tampered_truth["items"][irrelevant["feedback_id"]]["corrected_expected_state"] = {"delete_event": True}
    try:
        validate_state(tampered_truth)
    except ValueError as error:
        assert "relevance-only" in str(error)
    else:  # pragma: no cover - the load-time guard must fail closed
        raise AssertionError("a relevance-only record must not regain a truth assertion on load")
    tampered_expected = copy.deepcopy(state)
    tampered_expected["items"][item["feedback_id"]]["regression_fixture"]["expected"] = {"delete_event": True}
    assert_fails_closed(tampered_expected, "target/expected must match")
    tampered_kind = copy.deepcopy(state)
    tampered_kind["items"][item["feedback_id"]]["regression_fixture"]["fixture_kind"] = "LINKED_REGRESSION"
    assert_fails_closed(tampered_kind, "unsupported fields")
    smuggled_flag = copy.deepcopy(state)
    smuggled_flag["items"][item["feedback_id"]]["regression_fixture"]["auto_apply"] = True
    assert_fails_closed(smuggled_flag, "unsupported fields")
    relabelled = copy.deepcopy(state)
    relabelled_fixture = relabelled["items"][item["feedback_id"]]["regression_fixture"]
    relabelled_fixture["fixture_kind"] = "LINKED_REGRESSION"
    relabelled_fixture["fixture_id"] = "GOLD-1"
    del relabelled_fixture["target"]
    del relabelled_fixture["expected"]
    assert_fails_closed(relabelled, "unsupported fields")
    linked_state = link_regression(state, item["feedback_id"], "gold-1", reviewer_ref="reviewer:test", linked_at=stamp)
    del linked_state["items"][item["feedback_id"]]["regression_fixture"]["linked_at"]
    assert_fails_closed(linked_state, "missing required fields: linked_at")
    tampered_gold_flag = copy.deepcopy(state)
    tampered_gold_flag["items"][item["feedback_id"]]["regression_fixture"]["requires_gold_promotion_review"] = False
    assert_fails_closed(tampered_gold_flag, "requires_gold_promotion_review")
    downgraded = copy.deepcopy(state)
    downgraded["items"][item["feedback_id"]]["review_status"] = "REJECTED"
    downgraded["items"][item["feedback_id"]]["decision"] = {
        "status": "REJECTED",
        "reviewer_ref": "reviewer:test",
        "decided_at": stamp,
    }
    assert_fails_closed(downgraded, "only accepted feedback")
    mislabelled = copy.deepcopy(state)
    mislabelled["items"][item["feedback_id"]]["regression_fixture"]["reason"] = "NOT_RELEVANT"
    assert_fails_closed(mislabelled, "reason must match")
    unknown_kind = copy.deepcopy(state)
    unknown_kind["items"][item["feedback_id"]]["regression_fixture"]["fixture_kind"] = "FEEDBACK_REGRESSION_V2"
    assert_fails_closed(unknown_kind, "fixture_kind")
    tampered_fixture_id = copy.deepcopy(state)
    tampered_fixture_id["items"][item["feedback_id"]]["regression_fixture"]["fixture_id"] = "GOLD-1"
    assert_fails_closed(tampered_fixture_id, "fixture_id must be derived")
    tampered_fixture_owner = copy.deepcopy(state)
    tampered_fixture_owner["items"][item["feedback_id"]]["regression_fixture"]["feedback_id"] = "FEEDBACK-" + "0" * 20
    assert_fails_closed(tampered_fixture_owner, "feedback_id must match")
    tampered_content = copy.deepcopy(state)
    tampered_content["items"][item["feedback_id"]]["corrected_expected_state"] = {"delete_event": True}
    tampered_content["items"][item["feedback_id"]]["regression_fixture"]["expected"] = {"delete_event": True}
    assert_fails_closed(tampered_content, "fingerprint does not match")
    tampered_bool = copy.deepcopy(state)
    tampered_bool["items"][item["feedback_id"]]["regression_fixture"]["requires_human_approval"] = 1
    assert_fails_closed(tampered_bool, "requires_human_approval")
    partial_route = copy.deepcopy(state)
    for field in ("effect_scope", "requires_human_approval"):
        del partial_route["items"][item["feedback_id"]]["regression_fixture"][field]
    assert_fails_closed(partial_route, "route fields are incomplete")
    legacy_route = copy.deepcopy(state)
    for field in ROUTE_FIELDS:
        del legacy_route["items"][item["feedback_id"]]["regression_fixture"][field]
    upgraded = normalize_state(legacy_route)
    assert upgraded["items"][item["feedback_id"]]["regression_fixture"]["promotion_targets"] == [
        "ENTITY_REGISTRY",
        "EVENT_FUSION",
    ]
    assert "promotion_targets" not in legacy_route["items"][item["feedback_id"]]["regression_fixture"]
    stripped_marker = copy.deepcopy(state)
    del stripped_marker["items"][item["feedback_id"]]["regression_fixture"]["decided_at"]
    assert_fails_closed(stripped_marker, "missing required fields: decided_at")
    unbackfilled = copy.deepcopy(state)
    for field in ROUTE_FIELDS:
        del unbackfilled["items"][item["feedback_id"]]["regression_fixture"][field]
    unbackfilled["items"][item["feedback_id"]]["reason"] = "BOGUS_REASON"
    assert_fails_closed(unbackfilled, "reason/status is invalid")
    try:
        create_feedback(
            state,
            target_type="EVENT",
            target_id="PE-verified",
            target_version="v1",
            reason="NOT_RELEVANT",
            original_output_sha256=output_hash,
            corrected_expected_state={"relevance": ({"delete_event": True},)},
            created_at=stamp,
            model_version="model:test",
            parser_version="parser:test",
            registry_hash="registry:test",
        )
    except ValueError as error:
        assert "JSON-compatible" in str(error)
    else:  # pragma: no cover - relevance payloads must stay JSON round-trippable
        raise AssertionError("a relevance payload must not use a container JSON cannot store")
    dismissed_state, dismissed, _ = create_feedback(
        state,
        target_type="EVENT",
        target_id="PE-dismissed",
        target_version="v1",
        reason="NOT_RELEVANT",
        original_output_sha256="d" * 64,
        corrected_expected_state={"relevance": "RELEVANT"},
        created_at=stamp,
        model_version="model:test",
        parser_version="parser:test",
        registry_hash="registry:test",
    )
    rejected_state = review(dismissed_state, dismissed["feedback_id"], "REJECTED", reviewer_ref="reviewer:test", decided_at=stamp)
    rejected_summary = statistics(rejected_state)
    assert rejected_summary["accepted"] == 2
    assert rejected_summary["by_reason"]["NOT_RELEVANT"] == 2
    assert rejected_summary["by_route"]["RELEVANCE_EVALUATION"] == 1
    assert rejected_summary["by_route"]["ENTITY_REGISTRY"] == 1
    summary = statistics(state)
    assert summary["by_reason"]["FALSE_MERGE"] == 1 and summary["by_status"]["ACCEPTED"] == 2
    assert summary["accepted"] == 2
    assert summary["by_route"]["ENTITY_REGISTRY"] == 1 and summary["by_route"]["RELEVANCE_EVALUATION"] == 1
    assert summary["by_effect_scope"] == {"RELEVANCE_ONLY": 1, "TRUTH_CORRECTION": 1}
    print(
        f"FEEDBACK_SELF_CHECK_OK reasons={len(REASONS)} targets={len(TARGET_TYPES)} dedupe=true "
        f"accepted_regression=true routes={len(PROMOTION_TARGETS)} relevance_only=true "
        "lifecycles=FALSE_MERGE,NOT_RELEVANT"
    )


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
