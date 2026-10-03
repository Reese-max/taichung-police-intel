#!/usr/bin/env python3
"""Replay the v6 commute-road session and score it against the gold cases.

Fingerprint: ``govintel/v6/commute-reopen-replay-evaluation/v1``

Single command for the whole A-stage acceptance::

    python -X utf8 scripts/replay-commute-reopen.py --self-check

The receipt carries the code/data/policy/parser/model/prompt versions, the data
hash, the replay clock and the publication receipt, so a reader can tell which
input produced which numbers. Scoring reuses ``scripts/evaluate-govintel.py``:
this script never implements a second set of metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util

EVAL_SCRIPT = ROOT / "scripts" / "evaluate-govintel.py"
_spec = importlib.util.spec_from_file_location("govintel_eval", EVAL_SCRIPT)
assert _spec and _spec.loader
ev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ev)

from intel_v2 import commute_replay as replay_module  # noqa: E402

DEFAULT_MANIFEST = ROOT / "eval/gold/v6/commute-reopen-v1/manifest.json"
DEFAULT_SCENARIO = ROOT / "eval/gold/v6/commute-reopen-v1/session.json"

def load_arms(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Read each comparison arm's policy from the published manifest.

    The arm definition is data, not code: whatever the manifest declares is what
    ``--arm`` runs, so a published method ID can never drift from the measurement.
    """

    arms = manifest.get("arms")
    if not isinstance(arms, dict) or not arms:
        raise SystemExit("v6 gold manifest declares no arms")
    resolved: dict[str, dict[str, Any]] = {}
    for arm_id, arm in sorted(arms.items()):
        if not isinstance(arm, dict):
            raise SystemExit(f"arm {arm_id} must be an object")
        overrides = arm.get("policy_overrides", {})
        if not isinstance(overrides, dict):
            raise SystemExit(f"arm {arm_id} policy_overrides must be an object")
        # Fail closed on a policy the current implementation cannot express.
        replay_module.policy_from_mapping(overrides)
        resolved[arm_id] = overrides
    return resolved
# Probes that must each break a different rule. They are not scored arms; they
# exist so a review can see the matching and dedupe decisions are live. Each probe
# declares the defect it must produce: a prompt defect adds a false alert, while a
# trust defect misreports the station state instead.
PROBES: dict[str, dict[str, Any]] = {
    "PROBE_MIRROR_AS_EVIDENCE": {
        "overrides": {"mirror_is_independent_evidence": True},
        "must_break": "false_alert",
    },
    "PROBE_FORMATTING_AS_SUBSTANTIVE": {
        "overrides": {"formatting_is_substantive": True},
        "must_break": "false_alert",
    },
    "PROBE_DEDUPE_OFF": {
        "overrides": {"dedupe_repeat_fetches": False},
        "must_break": "false_alert",
    },
    "PROBE_ONE_ALERT_PER_DOCUMENT": {
        "overrides": {"dedupe_scope": "document"},
        "must_break": "wrongly_deduped_update",
    },
    "PROBE_PROMPT_AFTER_CANCEL": {
        "overrides": {"honour_condition_cancel": False},
        "must_break": "false_alert",
    },
    "PROBE_AUTO_LIFT_ON_TRACKED_END": {
        "overrides": {"auto_lift_on_tracked_end": True},
        "must_break": "observation_status",
    },
}


def load_gold_cases(manifest_path: Path = DEFAULT_MANIFEST) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        raise SystemExit("unsupported v6 gold manifest schema")
    cases = [json.loads(line) for line in (manifest_path.parent / manifest["case_file"]).read_text(encoding="utf-8").splitlines() if line.strip()]
    ev.validate_cases(cases)
    return manifest, cases


def run_arm(
    arm: str,
    overrides: dict[str, Any],
    scenario: dict[str, Any],
    manifest: dict[str, Any],
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    policy = replay_module.policy_from_mapping(overrides)
    receipt = replay_module.replay(scenario, policy)
    rows = replay_module.prediction_rows(receipt)
    predictions = {row["case_id"]: row["prediction"] for row in rows}
    report = ev.evaluate(manifest, cases, predictions)
    return {"arm": arm, "policy": policy.to_dict(), "receipt": receipt, "report": report}


def perfect(report: dict[str, Any]) -> bool:
    metrics = report["reopen_unread"]
    return (
        metrics["tp"] == metrics["expected_id_denominator"]
        and metrics["fp"] == 0
        and metrics["fn"] == 0
        and metrics["precision"] == 1.0
        and metrics["recall"] == 1.0
        and metrics["observation_status_accuracy"] == 1.0
        and metrics["gap_kind_accuracy"] == 1.0
        and metrics["multi_condition_accuracy"] == 1.0
        and metrics["condition_status_accuracy"] == 1.0
    )


def summarise(run: dict[str, Any]) -> dict[str, Any]:
    metrics = run["report"]["reopen_unread"]
    return {
        "arm": run["arm"],
        "tp": metrics["tp"],
        "fp": metrics["fp"],
        "fn": metrics["fn"],
        "precision": metrics["precision"],
        "recall": metrics["recall"],
        "false_alert_breakdown": metrics["false_alert_breakdown"],
        "multi_condition_prompt_count": metrics["multi_condition_prompt_count"],
        "observation_status_accuracy": metrics["observation_status_accuracy"],
        "gap_kind_accuracy": metrics["gap_kind_accuracy"],
        "multi_condition_accuracy": metrics["multi_condition_accuracy"],
        "condition_status_accuracy": metrics["condition_status_accuracy"],
    }


def self_check() -> int:
    scenario = replay_module.load_scenario(DEFAULT_SCENARIO)
    manifest, cases = load_gold_cases()
    arms = load_arms(manifest)
    for required in ("C_v6", "C_RULES_ONLY", "B_v6"):
        if required not in arms:
            raise SystemExit(f"SELF_CHECK_FAIL manifest does not declare arm {required}")

    baseline = run_arm("C_v6", arms["C_v6"], scenario, manifest, cases)
    if not perfect(baseline["report"]):
        raise SystemExit(f"SELF_CHECK_FAIL full v6 arm does not reproduce gold: {summarise(baseline)}")

    receipt = baseline["receipt"]
    for key in ("fingerprint", "data_hash", "data_cutoff", "clock", "versions", "publication_receipt"):
        if not receipt.get(key):
            raise SystemExit(f"SELF_CHECK_FAIL receipt missing {key}")
    versions = receipt["versions"]
    for key in ("policy_version", "parser_version", "model_version", "prompt_hash"):
        if key not in versions:
            raise SystemExit(f"SELF_CHECK_FAIL receipt versions missing {key}")
    if versions["model_version"] is not None or versions["prompt_hash"] is not None:
        raise SystemExit("SELF_CHECK_FAIL offline replay must not claim a model call")
    if receipt["cost_scope"]["model_requests"] != 0:
        raise SystemExit("SELF_CHECK_FAIL offline replay must not issue model requests")

    lines = [summarise(baseline)]

    for arm, overrides in arms.items():
        if arm == "C_v6":
            continue
        if manifest["arms"][arm].get("status") != "REPLAYED":
            continue
        run = run_arm(arm, overrides, scenario, manifest, cases)
        summary = summarise(run)
        if perfect(run["report"]):
            raise SystemExit(f"SELF_CHECK_FAIL {arm} reproduced the full arm; matching is not live")
        if summary["fp"] <= 0 and summary["fn"] <= 0:
            raise SystemExit(f"SELF_CHECK_FAIL {arm} produced no measurable difference")
        lines.append(summary)

    for probe, spec in PROBES.items():
        run = run_arm(probe, spec["overrides"], scenario, manifest, cases)
        summary = summarise(run)
        if run["receipt"]["reopen_points"] == baseline["receipt"]["reopen_points"]:
            raise SystemExit(f"SELF_CHECK_FAIL {probe} changed nothing")
        if spec["must_break"] == "false_alert" and summary["fp"] <= 0:
            raise SystemExit(f"SELF_CHECK_FAIL {probe} did not add a false alert")
        if spec["must_break"] == "observation_status" and summary["observation_status_accuracy"] == 1.0:
            raise SystemExit(f"SELF_CHECK_FAIL {probe} still reported the gold station state")
        if spec["must_break"] == "wrongly_deduped_update" and (
            summary["fn"] <= 0
            or summary["false_alert_breakdown"]["distinct_correction_wrongly_deduped"] <= 0
        ):
            raise SystemExit(f"SELF_CHECK_FAIL {probe} did not wrongly dedupe a substantive correction")
        lines.append(summary)

    later_cancel = json.loads(json.dumps(scenario))
    for condition in later_cancel["conditions"]:
        if condition["condition_id"] == "C-ROAD-AB":
            condition["cancelled_at"] = "2026-10-11T00:00:00+08:00"
    later_run = run_arm("PROBE_CANCEL_MOVED_LATER", {}, later_cancel, manifest, cases)
    if summarise(later_run)["fp"] <= 0:
        raise SystemExit("SELF_CHECK_FAIL moving the cancel time did not change the prompt set")
    if later_run["receipt"]["reopen_points"][-1] == baseline["receipt"]["reopen_points"][-1]:
        raise SystemExit("SELF_CHECK_FAIL the cancel decision is not read from the live scenario")
    lines.append(summarise(later_run))

    # Each false-alert counter must be reachable from real replay output, not only
    # from a hand-built prediction row.
    expected_false_alerts = {
        "PROBE_FORMATTING_AS_SUBSTANTIVE": "layout_only_false_alert",
        "PROBE_MIRROR_AS_EVIDENCE": "repost_false_alert",
        "PROBE_DEDUPE_OFF": "duplicate_fetch_false_alert",
        "PROBE_PROMPT_AFTER_CANCEL": "post_cancel_prompt",
    }
    for probe, counter in expected_false_alerts.items():
        metrics = run_arm(probe, PROBES[probe]["overrides"], scenario, manifest, cases)["report"][
            "reopen_unread"
        ]
        if metrics["false_alert_breakdown"].get(counter, 0) <= 0:
            raise SystemExit(f"SELF_CHECK_FAIL {probe} never reached the {counter} counter")
        if metrics["false_alert_breakdown"]["other_false_alert"] != 0:
            raise SystemExit(f"SELF_CHECK_FAIL {probe} still leaks into other_false_alert")

    wider = json.loads(json.dumps(scenario))
    for index in range(3, 6):
        template = json.loads(json.dumps(scenario["conditions"][0]))
        template["condition_id"] = f"C-CLONE-{index:02d}"
        template["cancelled_at"] = None
        wider["conditions"].append(template)
    wider_receipt = replay_module.replay(wider)
    if wider_receipt["cost_scope"]["tracked_conditions"] <= receipt["cost_scope"]["tracked_conditions"]:
        raise SystemExit("SELF_CHECK_FAIL the shared-announcement probe did not add conditions")
    if wider_receipt["cost_scope"]["model_requests"] != 0:
        raise SystemExit("SELF_CHECK_FAIL model requests must not scale with condition count")

    print(
        "COMMUTE_REOPEN_SELF_CHECK_OK "
        f"scenario={receipt['scenario_id']} data_hash={receipt['data_hash'][:12]} "
        f"reopen_points={receipt['clock']['reopen_count']} "
        f"tp={lines[0]['tp']} fp={lines[0]['fp']} fn={lines[0]['fn']}"
    )
    for line in lines[1:]:
        print(
            f"  arm={line['arm']} tp={line['tp']} fp={line['fp']} fn={line['fn']} "
            f"precision={line['precision']} recall={line['recall']}"
        )
    print(
        "  model_requests=0 for "
        f"{wider_receipt['cost_scope']['tracked_conditions']} tracked conditions "
        f"and {wider_receipt['cost_scope']['effective_updates']} effective updates"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--arm")
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        return self_check()
    scenario_path = args.scenario if args.scenario.is_absolute() else ROOT / args.scenario
    manifest_path = args.manifest if args.manifest.is_absolute() else ROOT / args.manifest
    scenario = replay_module.load_scenario(scenario_path)
    manifest, cases = load_gold_cases(manifest_path)
    arms = load_arms(manifest)
    arm = args.arm or "C_v6"
    if arm not in arms:
        raise SystemExit(f"unknown arm {arm}; the manifest declares {sorted(arms)}")
    if manifest["arms"][arm].get("status") != "REPLAYED":
        raise SystemExit(f"arm {arm} is {manifest['arms'][arm].get('status')}; nothing to replay")
    run = run_arm(arm, arms[arm], scenario, manifest, cases)
    if args.predictions:
        target = args.predictions if args.predictions.is_absolute() else ROOT / args.predictions
        target.parent.mkdir(parents=True, exist_ok=True)
        rows = replay_module.prediction_rows(run["receipt"])
        target.write_text(
            "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
    if args.output:
        target = args.output if args.output.is_absolute() else ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(run, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    else:
        print(json.dumps({"arm": arm, "summary": summarise(run)}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())