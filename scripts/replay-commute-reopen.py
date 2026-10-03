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
DEFAULT_CASES = ROOT / "eval/gold/v6/commute-reopen-v1/cases.jsonl"

# One entry per comparison arm. Each is a real decision of the current
# implementation, not a stored answer: `--self-check` proves every arm differs
# from the full v6 arm, so the acceptance cannot pass on frozen fixtures.
ARMS: dict[str, dict[str, Any]] = {
    "C_v6": {},
    "C_RULES_ONLY": {"semantic_matching": False},
    "B_v6": {"track_read_state": False},
}
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
        "condition_status_accuracy": metrics["condition_status_accuracy"],
    }


def self_check() -> int:
    scenario = replay_module.load_scenario()
    manifest, cases = load_gold_cases()

    baseline = run_arm("C_v6", ARMS["C_v6"], scenario, manifest, cases)
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

    for arm, overrides in ARMS.items():
        if arm == "C_v6":
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
        if run["receipt"] == baseline["receipt"]:
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
    parser.add_argument("--scenario", type=Path, default=replay_module.SCENARIO_PATH)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--arm", choices=sorted(ARMS), default="C_v6")
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
    run = run_arm(args.arm, ARMS[args.arm], scenario, manifest, cases)
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
        print(json.dumps({"arm": args.arm, "summary": summarise(run)}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())