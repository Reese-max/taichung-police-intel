#!/usr/bin/env python3
"""Versioned regression evaluator for GovIntel gold cases.

This harness reports engineering metrics with explicit denominators. A synthetic
starter set validates metric plumbing only; it must never be presented as measured
production accuracy.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "eval/gold/v1/manifest.json"
SYNTHETIC_V2_MANIFEST = ROOT / "eval/gold/v2/manifest.json"
SYNTHETIC_V3_MANIFEST = ROOT / "eval/gold/v3/manifest.json"


FALSE_ALERT_REASONS = (
    "already_read_repeat",
    "layout_only_false_alert",
    "repost_false_alert",
    "duplicate_fetch_false_alert",
    "post_cancel_prompt",
)

SUBSTANCE_FALSE_ALERT = {
    "LAYOUT_ONLY": "layout_only_false_alert",
    "MIRROR_REPOST": "repost_false_alert",
    "REPEAT_FETCH": "duplicate_fetch_false_alert",
}

DEDUPE_DECISIONS = ("FORMAT_ONLY", "REPOST_MIRROR", "DUPLICATE_FETCH")

GAP_REASONS = ("CONNECTION_FAILURE", "PARTIAL_COVERAGE", "SOURCE_ITEM_MISSING")

REPORTED_ALREADY_READ = "ALREADY_READ"

def reopen_diagnostics(case: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    """Score one reopen point, keeping every failure mode on its own counter.

    TP/FP/FN count stable update IDs, so one update that matched several tracked
    conditions is still worth one TP. The raw pair count is reported next to it so
    the difference stays visible.
    """

    expected = case["expected"]
    expected_ids = set(expected["update_ids"])
    predicted_ids = set(require_string_list(prediction, "update_ids", case["case_id"]))
    decisions = prediction.get("decision_by_update_id")
    if decisions is None:
        decisions = {}
    if not isinstance(decisions, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in decisions.items()
    ):
        raise ValueError(f"prediction {case['case_id']} decision_by_update_id must be string map")
    gap_kinds = prediction.get("gap_kinds")
    if gap_kinds is None:
        gap_kinds = []
    if not isinstance(gap_kinds, list) or any(not isinstance(item, str) for item in gap_kinds):
        raise ValueError(f"prediction {case['case_id']} gap_kinds must be string array")
    condition_status = prediction.get("condition_status")
    if condition_status is None:
        condition_status = {}
    if not isinstance(condition_status, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in condition_status.items()
    ):
        raise ValueError(f"prediction {case['case_id']} condition_status must be string map")
    multi = prediction.get("multi_condition_update_ids")
    if multi is None:
        multi = []
    if not isinstance(multi, list) or any(not isinstance(item, str) for item in multi):
        raise ValueError(f"prediction {case['case_id']} multi_condition_update_ids must be string array")
    substances = prediction.get("substance_by_update_id")
    if substances is None:
        substances = {}
    if not isinstance(substances, dict) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in substances.items()
    ):
        raise ValueError(f"prediction {case['case_id']} substance_by_update_id must be string map")
    cancelled_conditions = prediction.get("cancelled_condition_by_update_id")
    if cancelled_conditions is None:
        cancelled_conditions = {}
    if not isinstance(cancelled_conditions, dict) or any(
        not isinstance(key, str)
        or not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
        for key, value in cancelled_conditions.items()
    ):
        raise ValueError(
            f"prediction {case['case_id']} cancelled_condition_by_update_id must be string list map"
        )

    false_positives = predicted_ids - expected_ids
    false_negatives = expected_ids - predicted_ids
    counters = {reason: 0 for reason in FALSE_ALERT_REASONS}
    counters["other_false_alert"] = 0
    counters["distinct_correction_wrongly_deduped"] = 0
    counters["missed_while_source_gap"] = 0
    counters["missed_by_matching_rule"] = 0
    counters["missed_without_recorded_reason"] = 0
    for update_id in false_positives:
        substance = substances.get(update_id)
        if substance in SUBSTANCE_FALSE_ALERT:
            # What the update *is* is the deeper defect: a prompt that re-alerts a
            # layout-only republication must not be filed as a read-state repeat.
            counters[SUBSTANCE_FALSE_ALERT[substance]] += 1
            continue
        if decisions.get(update_id) == REPORTED_ALREADY_READ:
            counters["already_read_repeat"] += 1
            continue
        if cancelled_conditions.get(update_id):
            counters["post_cancel_prompt"] += 1
            continue
        counters["other_false_alert"] += 1
    for update_id in false_negatives:
        reason = decisions.get(update_id)
        if reason in DEDUPE_DECISIONS:
            counters["distinct_correction_wrongly_deduped"] += 1
        elif set(expected.get("gap_kinds", [])) & set(GAP_REASONS):
            # The gold gap list is the standard answer for what the station should
            # have known, so a miss during a real acquisition gap is coverage, not
            # a matching defect. Reading the prediction here would let an
            # under-reporting harness excuse its own misses.
            counters["missed_while_source_gap"] += 1
        elif reason:
            counters["missed_by_matching_rule"] += 1
        else:
            counters["missed_without_recorded_reason"] += 1
    return {
        "case_id": case["case_id"],
        "tp": len(expected_ids & predicted_ids),
        "fp": len(false_positives),
        "fn": len(false_negatives),
        "expected_id_count": len(expected_ids),
        "predicted_id_count": len(predicted_ids),
        "multi_condition_prompt_count": len(set(multi) & predicted_ids),
        # Compare the declared set against gold, not gold-intersected-with-the-
        # prediction: intersecting both sides would score a prediction that prompts
        # nothing as a perfect multi-condition match.
        "multi_condition_correct": int(
            "multi_condition_update_ids" in expected
            and sorted(set(multi)) == sorted(set(expected["multi_condition_update_ids"]))
            and set(multi) <= predicted_ids
        ),
        "multi_condition_total": 1 if "multi_condition_update_ids" in expected else 0,
        "source_gap_kinds": sorted(set(gap_kinds)),
        "gold_gap_kinds": sorted(set(expected.get("gap_kinds", []))),
        "observation_status_correct": int(prediction.get("observation_status") == expected["observation_status"]),
        "observation_status_total": 1,
        "gap_kind_correct": int(set(gap_kinds) == set(expected.get("gap_kinds", []))),
        "gap_kind_total": 1,
        "condition_status_correct": sum(
            int(condition_status.get(key) == value)
            for key, value in expected.get("condition_status", {}).items()
        ),
        "condition_status_total": len(expected.get("condition_status", {})),
        "counters": counters,
    }

def aggregate_reopen(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counters: dict[str, int] = {}
    observation_total = observation_correct = 0
    gap_total = gap_correct = 0
    multi_total = multi_correct = 0
    condition_total = condition_correct = 0
    multi_pairs = 0
    source_gap_reopen_points = 0
    tp = fp = fn = 0
    for row in rows:
        tp += row["tp"]
        fp += row["fp"]
        fn += row["fn"]
        multi_pairs += row["multi_condition_prompt_count"]
        observation_total += row["observation_status_total"]
        observation_correct += row["observation_status_correct"]
        gap_total += row["gap_kind_total"]
        gap_correct += row["gap_kind_correct"]
        multi_total += row["multi_condition_total"]
        multi_correct += row["multi_condition_correct"]
        condition_total += row["condition_status_total"]
        condition_correct += row["condition_status_correct"]
        if set(row["source_gap_kinds"]) & set(GAP_REASONS):
            source_gap_reopen_points += 1
        for key, value in row["counters"].items():
            counters[key] = counters.get(key, 0) + value
    expected_ids = sum(row["expected_id_count"] for row in rows)
    predicted_ids = sum(row["predicted_id_count"] for row in rows)
    return {
        "reopen_denominator": len(rows),
        "expected_id_denominator": expected_ids,
        "predicted_id_denominator": predicted_ids,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": safe_ratio(tp, tp + fp),
        "recall": safe_ratio(tp, tp + fn),
        "f1": safe_ratio(2 * tp, 2 * tp + fp + fn),
        # Multi-condition hits are reported next to the deduplicated TP count so a
        # single update cannot inflate the score.
        "multi_condition_prompt_count": multi_pairs,
        "false_alert_breakdown": dict(sorted(counters.items())),
        "source_acquisition_gap": {
            "reopen_point_count": source_gap_reopen_points,
            "note": "來源取得缺漏另列，不併入精確率／召回率分子分母",
        },
        "observation_status_accuracy": safe_ratio(observation_correct, observation_total),
        "gap_kind_accuracy": safe_ratio(gap_correct, gap_total),
        "multi_condition_accuracy": safe_ratio(multi_correct, multi_total),
        "condition_status_accuracy": safe_ratio(condition_correct, condition_total),
    }

def target_assessment(
    manifest: dict[str, Any], metrics: dict[str, Any], missing_prediction_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Score the declared targets against the measured run, or record why not.

    A target whose comparison arm was never run is ``NOT_RUN``, never ``met`` and
    never ``unmet``: an unexecuted comparison cannot fail.
    """

    targets = manifest.get("targets")
    if not isinstance(targets, dict):
        return []
    rows: list[dict[str, Any]] = []
    for name, target in sorted(targets.items()):
        row: dict[str, Any] = {"target": name, "target_value": target}
        if name in {"precision", "recall"} and missing_prediction_ids:
            row.update(
                measured=None, status="NOT_MEASURED",
                partial_measurement=metrics.get(name),
                reason="incomplete evaluation; missing predictions: " + ", ".join(missing_prediction_ids),
            )
            rows.append(row)
            continue
        if name == "precision":
            measured = metrics.get("precision")
            row["measured"] = measured
            row["status"] = "NOT_MEASURED" if measured is None else (
                "MET" if measured >= target else "UNMET"
            )
        elif name == "recall":
            measured = metrics.get("recall")
            row["measured"] = measured
            row["status"] = "NOT_MEASURED" if measured is None else (
                "MET" if measured >= target else "UNMET"
            )
        else:
            # Timing and correctness need arm A, which this round did not run.
            row["measured"] = None
            row["status"] = "NOT_RUN"
            row["reason"] = "needs a comparison arm that was not executed: " + ", ".join(
                sorted(
                    arm_id
                    for arm_id, arm in (manifest.get("arms") or {}).items()
                    if isinstance(arm, dict) and arm.get("status") == "NOT_RUN"
                )
            )
        rows.append(row)
    return rows

def not_run_arms(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """List everything an arm did not measure, with the scope of each gap.

    A replayed arm still has no human timing, so one status field cannot describe
    both. Every entry names the scope it is about.
    """

    arms = manifest.get("arms")
    if not isinstance(arms, dict):
        return []
    pending = []
    for arm_id, arm in sorted(arms.items()):
        if not isinstance(arm, dict):
            continue
        if arm.get("status") == "NOT_RUN":
            pending.append(
                {
                    "arm": arm_id,
                    "method_id": arm.get("method_id"),
                    "scope": "arm",
                    "status": "NOT_RUN",
                }
            )
        if arm.get("human_timing_status") == "NOT_RUN":
            pending.append(
                {
                    "arm": arm_id,
                    "method_id": arm.get("method_id"),
                    "scope": "human_timing",
                    "status": "NOT_RUN",
                }
            )
        if arm.get("method_execution_status") == "NOT_RUN":
            pending.append({
                "arm": arm_id, "method_id": arm.get("method_id"),
                "scope": "full_comparison_method", "status": "NOT_RUN",
                "reason": arm.get("execution_limitation"),
            })
    return pending

SPLIT_VALUES = {"dev", "holdout"}

CONFIRMED_DISCOVERY_STATUS = "VERIFIED_OFFICIAL"

NO_ANSWER_STATES = {"NO_RESULT", "SOURCE_GAP", "SOURCE_FAILED", "NO_ANSWER", "UNANSWERABLE"}

LOWER_IS_BETTER_RATE_METRICS = {"unsupported_claim_rate", "no_result_false_reassurance"}

COMPARED_FIELD_SUFFIXES = ("precision", "recall", "f1", "accuracy", "coverage", "mrr", "rate")

COMPARISON_EXCLUDED_SECTIONS = {"targets", "baseline_comparison", "case_results", "not_run", "reopen_unread_rows"}

def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)

def _validate_history(case: dict[str, Any]) -> None:
    """Annotation edits must leave reviewer/version/history (issue #33 rules)."""

    case_id = case["case_id"]
    label_version = case.get("label_version")
    history = case.get("history")
    if label_version is not None and (
        not isinstance(label_version, int) or isinstance(label_version, bool) or label_version < 1
    ):
        raise ValueError(f"label_version must be a positive integer: {case_id}")
    if history is not None:
        if not isinstance(history, list):
            raise ValueError(f"history must be an array: {case_id}")
        for entry in history:
            if not isinstance(entry, dict):
                raise ValueError(f"history entries must be objects: {case_id}")
            version = entry.get("version")
            if not isinstance(version, int) or isinstance(version, bool):
                raise ValueError(f"history entry requires integer version: {case_id}")
            if not isinstance(entry.get("reviewer"), str) or not entry["reviewer"]:
                raise ValueError(f"history entry requires reviewer: {case_id}")
            if not isinstance(entry.get("note"), str) or not entry["note"]:
                raise ValueError(f"history entry requires note: {case_id}")
    if isinstance(label_version, int) and label_version > 1 and not history:
        raise ValueError(f"label_version > 1 requires history: {case_id}")

def _optional_string_set(prediction: dict[str, Any], field: str, case_id: str) -> set[str]:
    value = prediction.get(field)
    if value is None:
        return set()
    return set(require_string_list(prediction, field, case_id))

def split_metrics(manifest: dict[str, Any], cases: list[dict[str, Any]], missing: list[str], exact_ids: set[str]) -> dict[str, Any]:
    declared = manifest.get("splits")
    if not isinstance(declared, list) or not declared:
        return {}
    missing_ids = set(missing)
    rows: dict[str, Any] = {}
    for split in declared:
        members = [case for case in cases if case.get("split") == split]
        evaluated = [case for case in members if case["case_id"] not in missing_ids]
        exact = [case for case in members if case["case_id"] in exact_ids]
        rows[split] = {
            "case_denominator": len(members),
            "evaluated_cases": len(evaluated),
            "exact_case_match_count": len(exact),
            "exact_case_match_rate": safe_ratio(len(exact), len(members)),
        }
    return rows

_MISSING = object()

def _metric_leaves(report: dict[str, Any]) -> dict[tuple[str, str], Any]:
    """Collect rate-like numeric leaves keyed by (metric_section, field)."""

    leaves: dict[tuple[str, str], Any] = {}
    for section, value in report.items():
        if section in COMPARISON_EXCLUDED_SECTIONS or not isinstance(value, dict):
            continue
        for key, leaf in value.items():
            if isinstance(leaf, dict):
                for inner_key, inner_leaf in leaf.items():
                    if not isinstance(inner_leaf, dict) and isinstance(inner_key, str) and inner_key.endswith(COMPARED_FIELD_SUFFIXES):
                        leaves[(f"{section}.{key}", inner_key)] = inner_leaf
                continue
            if isinstance(key, str) and key.endswith(COMPARED_FIELD_SUFFIXES):
                leaves[(section, key)] = leaf
    return leaves

def compare_report_diagnostics(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Diff a measured report against the previous baseline (issue #33).

    The comparison answers "which metrics improved/regressed and on which fixed
    cases did the run regress". A regression is any compared metric that moved
    the wrong way, any baseline-EXACT case that is no longer exact, any dropped
    case, or a higher missing-prediction count. The gate is PASS/FAIL so a
    model or rules change cannot be merged on the claim that it is better when
    a measured regression exists.
    """

    current_leaves = _metric_leaves(current)
    baseline_leaves = _metric_leaves(baseline)
    metric_deltas: list[dict[str, Any]] = []
    for path in sorted(set(current_leaves) | set(baseline_leaves)):
        metric, field = path
        base_value = baseline_leaves.get(path, _MISSING)
        current_value = current_leaves.get(path, _MISSING)
        row: dict[str, Any] = {
            "metric": metric,
            "field": field,
            "baseline": None if base_value is _MISSING else base_value,
            "current": None if current_value is _MISSING else current_value,
        }
        if base_value is _MISSING:
            row["direction"] = "NEW"
        elif current_value is _MISSING:
            row["direction"] = "DROPPED"
        elif base_value is None and current_value is None:
            continue
        elif base_value is None:
            row["direction"] = "NEW_MEASUREMENT"
        elif current_value is None:
            row["direction"] = "REGRESSED"
        else:
            row["delta"] = current_value - base_value
            lower_better = metric.split(".")[0] in LOWER_IS_BETTER_RATE_METRICS
            better = current_value < base_value if lower_better else current_value > base_value
            worse = current_value > base_value if lower_better else current_value < base_value
            row["direction"] = "IMPROVED" if better else "REGRESSED" if worse else "UNCHANGED"
        metric_deltas.append(row)

    def case_statuses(report: dict[str, Any]) -> dict[str, str]:
        return {
            row["case_id"]: row.get("status", "DIFF")
            for row in report.get("case_results", [])
            if isinstance(row, dict) and isinstance(row.get("case_id"), str)
        }

    current_cases = case_statuses(current)
    baseline_cases = case_statuses(baseline)
    dropped_cases = sorted(set(baseline_cases) - set(current_cases))
    new_cases = sorted(set(current_cases) - set(baseline_cases))
    case_regressions = sorted(
        case_id for case_id in set(baseline_cases) & set(current_cases)
        if baseline_cases[case_id] == "EXACT" and current_cases[case_id] != "EXACT"
    )
    case_improvements = sorted(
        case_id for case_id in set(baseline_cases) & set(current_cases)
        if baseline_cases[case_id] != "EXACT" and current_cases[case_id] == "EXACT"
    )
    missing_delta = int(current.get("missing_prediction_count") or 0) - int(
        baseline.get("missing_prediction_count") or 0
    )

    failures: list[str] = []
    for row in metric_deltas:
        if row["direction"] in {"REGRESSED", "DROPPED"}:
            failures.append(
                f"metric {row['metric']}.{row['field']} {row['direction'].lower()}: "
                f"{row['baseline']} -> {row['current']}"
            )
    failures.extend(f"case {case_id} regressed from EXACT" for case_id in case_regressions)
    failures.extend(f"case {case_id} dropped from evaluation" for case_id in dropped_cases)
    if missing_delta > 0:
        failures.append(
            f"missing predictions increased by {missing_delta}: "
            f"{baseline.get('missing_prediction_count') or 0} -> {current.get('missing_prediction_count') or 0}"
        )
    return {
        "scope": "CROSS_REPORT_DIAGNOSTICS_NO_PROMOTION",
        "baseline_dataset_id": baseline.get("dataset_id"),
        "current_dataset_id": current.get("dataset_id"),
        "metric_deltas": metric_deltas,
        "case_regressions": case_regressions,
        "case_improvements": case_improvements,
        "new_cases": new_cases,
        "dropped_cases": dropped_cases,
        "missing_prediction_delta": missing_delta,
        "regressions_found": bool(failures),
        "gate": {"status": "FAIL" if failures else "PASS", "failures": failures},
    }

def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{number}: row must be an object")
        rows.append(value)
    return rows


def load_gold(manifest_path: Path = DEFAULT_MANIFEST) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema_version = manifest.get("schema_version")
    if schema_version not in {1, 2}:
        raise ValueError("unsupported gold manifest schema")
    cases = read_jsonl(manifest_path.parent / manifest["case_file"])
    validate_cases(cases, manifest)
    if schema_version == 2:
        required_tasks = {"event_pair", "material_change", "query_ids", "claim_support", "discovery_official"}
        task_types = manifest.get("task_types")
        if not isinstance(task_types, list) or not required_tasks.issubset(set(task_types)):
            raise ValueError("v2 manifest is missing required task types")
        if manifest.get("dataset_type") != "SYNTHETIC_REGRESSION_SEED":
            raise ValueError("v2 starter dataset must be explicitly synthetic")
        if manifest.get("label_policy", {}).get("human_reviewed_holdout_included") is not False:
            raise ValueError("synthetic v2 manifest must state that no human-reviewed holdout is included")
        split_policy = manifest.get("split_policy")
        if not isinstance(split_policy, dict) or split_policy.get("human_dev_set") != "not-included" or split_policy.get("human_holdout_set") != "not-included":
            raise ValueError("v2 synthetic manifest must keep human dev and holdout sets explicit and separate")
        required_cases = {
            "change-body-only-001", "change-media-only-001", "query-zero-failed-001",
            "discovery-official-confirmed-001", "discovery-official-mismatch-001",
        }
        present = {case["case_id"] for case in cases}
        if not required_cases.issubset(present):
            raise ValueError("v2 synthetic regression fixtures are incomplete")
        for case in cases:
            if case.get("synthetic") is not True:
                raise ValueError("v2 regression seed may contain synthetic fixtures only")
            expected = case["expected"]
            if case["task"] == "query_ids" and ("ranked_ids" not in expected or "answer_state" not in expected):
                raise ValueError(f"v2 query case requires ranking and answer-state labels: {case['case_id']}")
            if case["task"] == "claim_support" and not expected.get("evidence_ids"):
                raise ValueError(f"v2 claim case requires synthetic evidence IDs: {case['case_id']}")
    return manifest, cases


def validate_cases(cases: list[dict[str, Any]], manifest: dict[str, Any] | None = None) -> None:
    ids: set[str] = set()
    supported = {
        "event_pair",
        "material_change",
        "query_ids",
        "claim_support",
        "reopen_unread",
        "discovery_verification", "discovery_official",
    }
    required_splits: set[str] = set()
    if isinstance(manifest, dict):
        declared = manifest.get("splits")
        if declared is not None:
            if not _is_string_list(declared):
                raise ValueError("manifest splits must be a string array")
            required_splits = set(declared)
    split_counts = {split: 0 for split in required_splits}
    for case in cases:
        case_id = case.get("case_id")
        task = case.get("task")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("case_id missing")
        if case_id in ids:
            raise ValueError(f"duplicate case_id: {case_id}")
        ids.add(case_id)
        if task not in supported:
            raise ValueError(f"unsupported task {task!r} for {case_id}")
        split = case.get("split")
        if split is not None and split not in SPLIT_VALUES:
            raise ValueError(f"invalid split {split!r} for {case_id}")
        if required_splits:
            if split is None:
                raise ValueError(f"manifest declares splits; case missing split: {case_id}")
            if split not in required_splits:
                raise ValueError(f"split {split!r} not declared in manifest splits: {case_id}")
            split_counts[split] += 1
        if case.get("synthetic") is not True:
            reviewer = case.get("reviewer")
            if not isinstance(reviewer, str) or not reviewer:
                raise ValueError(f"non-synthetic case requires reviewer: {case_id}")
        if task == "discovery_official" and type(case.get("expected", {}).get("confirmed")) is not bool:
            raise ValueError(f"discovery_official expected confirmed must be boolean: {case_id}")
        _validate_history(case)
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise ValueError(f"expected object missing: {case_id}")
        if "entity_ids" in expected and not _is_string_list(expected["entity_ids"]):
            raise ValueError(f"expected entity_ids must be string array: {case_id}")
        if "evidence_ids" in expected and not _is_string_list(expected["evidence_ids"]):
            raise ValueError(f"expected evidence_ids must be string array: {case_id}")
        if "trust_tier" in expected and (
            not isinstance(expected["trust_tier"], str) or not expected["trust_tier"]
        ):
            raise ValueError(f"expected trust_tier must be string: {case_id}")
        if task == "event_pair":
            if not isinstance(expected.get("same_event"), bool):
                raise ValueError(f"event_pair expected same_event must be boolean: {case_id}")
            if "canonical_event_id" in expected:
                canonical = expected["canonical_event_id"]
                if expected["same_event"] is not True:
                    raise ValueError(f"canonical_event_id requires same_event=true: {case_id}")
                if not isinstance(canonical, str) or not canonical:
                    raise ValueError(f"canonical_event_id must be string: {case_id}")
        if task == "material_change":
            if not isinstance(expected.get("material_change"), bool):
                raise ValueError(f"material_change expected material_change must be boolean: {case_id}")
            if "fields" in expected:
                if not _is_string_list(expected["fields"]):
                    raise ValueError(f"material_change fields must be string array: {case_id}")
                if expected["material_change"] is False and expected["fields"]:
                    raise ValueError(f"non-material change cannot declare changed fields: {case_id}")
        if task == "discovery_verification":
            status = expected.get("verification_status")
            if not isinstance(status, str) or not status:
                raise ValueError(f"discovery_verification expected verification_status must be string: {case_id}")
            if "official_match_ids" in expected and not _is_string_list(expected["official_match_ids"]):
                raise ValueError(f"official_match_ids must be string array: {case_id}")
        if task == "query_ids":
            expected_ids = expected.get("ids")
            if not isinstance(expected_ids, list) or any(not isinstance(item, str) for item in expected_ids):
                raise ValueError(f"query_ids expected ids must be string array: {case_id}")
            if "answer_state" in expected and not isinstance(expected["answer_state"], str):
                raise ValueError(f"query_ids expected answer_state must be string: {case_id}")
        if task == "claim_support" and not isinstance(expected.get("support_status"), str):
            raise ValueError(f"claim_support expected support_status missing: {case_id}")
        if task == "reopen_unread":
            reopen_ids = expected.get("update_ids")
            if not isinstance(reopen_ids, list) or any(not isinstance(item, str) for item in reopen_ids):
                raise ValueError(f"reopen_unread expected update_ids must be string array: {case_id}")
            if len(set(reopen_ids)) != len(reopen_ids):
                raise ValueError(f"reopen_unread expected update_ids must be unique: {case_id}")
            if not isinstance(expected.get("observation_status"), str):
                raise ValueError(f"reopen_unread expected observation_status missing: {case_id}")
            gap_kinds = expected.get("gap_kinds")
            if not isinstance(gap_kinds, list) or any(not isinstance(item, str) for item in gap_kinds):
                raise ValueError(f"reopen_unread expected gap_kinds must be string array: {case_id}")
            condition_status = expected.get("condition_status")
            if not isinstance(condition_status, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in condition_status.items()
            ):
                raise ValueError(f"reopen_unread expected condition_status must be string map: {case_id}")
        if task == "event_pair" and not isinstance(expected.get("same_event"), bool):
            raise ValueError(f"event_pair expected same_event must be boolean: {case_id}")
        if task == "material_change":
            if not isinstance(expected.get("material_change"), bool):
                raise ValueError(f"material_change expected material_change must be boolean: {case_id}")
            fields = expected.get("fields")
            if not isinstance(fields, list) or any(not isinstance(item, str) or not item for item in fields):
                raise ValueError(f"material_change expected fields must be a string array: {case_id}")
            if len(fields) != len(set(fields)):
                raise ValueError(f"material_change expected fields must be unique: {case_id}")
            if expected["material_change"] != bool(fields):
                raise ValueError(f"material_change label and changed fields disagree: {case_id}")
        if task == "query_ids":
            expected_ids = expected.get("ids")
            if not isinstance(expected_ids, list) or any(not isinstance(item, str) for item in expected_ids):
                raise ValueError(f"query_ids expected ids must be string array: {case_id}")
            if len(expected_ids) != len(set(expected_ids)):
                raise ValueError(f"query_ids expected ids must be unique: {case_id}")
            ranked_ids = expected.get("ranked_ids", expected_ids)
            if not isinstance(ranked_ids, list) or any(not isinstance(item, str) for item in ranked_ids):
                raise ValueError(f"query_ids expected ranked_ids must be string array: {case_id}")
            if len(ranked_ids) != len(set(ranked_ids)) or not set(ranked_ids).issubset(set(expected_ids)):
                raise ValueError(f"query_ids ranked_ids must be unique relevant IDs: {case_id}")
            if "answer_state" in expected and not isinstance(expected["answer_state"], str):
                raise ValueError(f"query_ids expected answer_state must be string: {case_id}")
        if task == "claim_support":
            if not isinstance(expected.get("support_status"), str) or not expected["support_status"]:
                raise ValueError(f"claim_support expected support_status missing: {case_id}")
            if "evidence_ids" in expected and (
                not isinstance(expected["evidence_ids"], list)
                or any(not isinstance(item, str) or not item for item in expected["evidence_ids"])
                or len(expected["evidence_ids"]) != len(set(expected["evidence_ids"]))
            ):
                raise ValueError(f"claim_support expected evidence_ids must be unique strings: {case_id}")
        if task == "discovery_official" and not isinstance(expected.get("confirmed"), bool):
            raise ValueError(f"discovery_official expected confirmed must be boolean: {case_id}")


    empty_splits = sorted(split for split, count in split_counts.items() if count == 0)
    if empty_splits:
        raise ValueError("no cases for declared splits: " + ", ".join(empty_splits))

def index_predictions(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        case_id = row.get("case_id")
        prediction = row.get("prediction")
        if not isinstance(case_id, str) or not isinstance(prediction, dict):
            raise ValueError("prediction rows require case_id + prediction object")
        if case_id in indexed:
            raise ValueError(f"duplicate prediction: {case_id}")
        indexed[case_id] = prediction
    return indexed


def require_bool(prediction: dict[str, Any], field: str, case_id: str) -> bool:
    value = prediction.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"prediction {case_id} field {field} must be boolean")
    return value


def require_string_list(prediction: dict[str, Any], field: str, case_id: str) -> list[str]:
    value = prediction.get(field)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError(f"prediction {case_id} field {field} must be string array")
    if field != "update_ids" and len(value) != len(set(value)):
        raise ValueError(f"prediction {case_id} field {field} must not contain duplicates")
    return value


def safe_ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


def dataset_fingerprint(manifest: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    material = json.dumps(
        {"manifest": manifest, "cases": cases},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def binary_metrics(pairs: list[tuple[bool, bool]]) -> dict[str, Any]:
    tp = sum(1 for expected, predicted in pairs if expected and predicted)
    fp = sum(1 for expected, predicted in pairs if not expected and predicted)
    fn = sum(1 for expected, predicted in pairs if expected and not predicted)
    tn = sum(1 for expected, predicted in pairs if not expected and not predicted)
    precision = safe_ratio(tp, tp + fp)
    recall = safe_ratio(tp, tp + fn)
    f1 = None
    if precision is not None and recall is not None and precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "denominator": len(pairs),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def set_metrics(expected_sets: list[set[str]], predicted_sets: list[set[str]]) -> dict[str, Any]:
    tp = sum(len(e & p) for e, p in zip(expected_sets, predicted_sets))
    fp = sum(len(p - e) for e, p in zip(expected_sets, predicted_sets))
    fn = sum(len(e - p) for e, p in zip(expected_sets, predicted_sets))
    precision = safe_ratio(tp, tp + fp)
    recall = safe_ratio(tp, tp + fn)
    f1 = None
    if precision is not None and recall is not None and precision + recall:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "case_denominator": len(expected_sets),
        "expected_id_denominator": sum(len(s) for s in expected_sets),
        "predicted_id_denominator": sum(len(s) for s in predicted_sets),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def _accuracy(count: int, denominator: int) -> dict[str, Any]:
    return {"denominator": denominator, "correct": count, "accuracy": safe_ratio(count, denominator)}


def evaluate(manifest: dict[str, Any], cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    case_ids = {case["case_id"] for case in cases}
    unknown_predictions = sorted(set(predictions) - case_ids)
    if unknown_predictions:
        raise ValueError("predictions contain unknown case IDs: " + ", ".join(unknown_predictions))

    event_pairs: list[tuple[bool, bool]] = []
    change_pairs: list[tuple[bool, bool]] = []
    change_expected_fields: list[set[str]] = []
    change_predicted_fields: list[set[str]] = []
    discovery_pairs: list[tuple[bool, bool]] = []
    query_expected: list[set[str]] = []
    query_predicted: list[set[str]] = []
    query_exact_matches = 0
    query_rank_expected: list[set[str]] = []
    query_rank_targets: list[list[str]] = []
    query_rank_predictions: list[list[str]] = []
    query_state_total = query_state_correct = 0
    source_gap_total = false_reassurance_count = 0
    claim_total = claim_correct = 0
    unsupported_claim_count = predicted_supported_count = 0
    evidence_expected: list[set[str]] = []
    evidence_predicted: list[set[str]] = []
    evidence_fully_covered = 0
    reopen_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    exact_case_matches = 0
    exact_ids: set[str] = set()
    case_results = []
    canonical_total = canonical_correct = trust_total = trust_correct = 0
    entity_expected, entity_predicted = [], []
    discovery_status_pairs = []
    discovery_status_total = discovery_status_correct = 0
    discovery_match_expected, discovery_match_predicted = [], []
    explicit_ranks = []

    for case in cases:
        case_id = case["case_id"]
        prediction = predictions.get(case_id)
        if prediction is None:
            missing.append(case_id)
            case_results.append({"case_id": case_id, "task": case["task"], "status": "MISSING"})
            continue
        expected = case["expected"]
        task = case["task"]
        if "trust_tier" in expected:
            trust_total += 1
            trust_correct += int(prediction.get("trust_tier") == expected["trust_tier"])
        if "entity_ids" in expected:
            entity_expected.append(set(expected["entity_ids"]))
            entity_predicted.append(_optional_string_set(prediction, "entity_ids", case_id))
        if "evidence_ids" in expected:
            expected_evidence = set(expected["evidence_ids"])
            predicted_evidence = _optional_string_set(prediction, "evidence_ids", case_id)
            evidence_expected.append(expected_evidence)
            evidence_predicted.append(predicted_evidence)
            evidence_fully_covered += int(expected_evidence.issubset(predicted_evidence))
        if task == "event_pair":
            if "canonical_event_id" in expected:
                canonical_total += 1
                canonical_correct += int(prediction.get("canonical_event_id") == expected["canonical_event_id"])

            event_pairs.append((expected["same_event"], require_bool(prediction, "same_event", case_id)))
        elif task == "material_change":
            change_pairs.append((expected["material_change"], require_bool(prediction, "material_change", case_id)))
            predicted_fields = set(require_string_list(prediction, "fields", case_id))
            change_expected_fields.append(set(expected["fields"]))
            change_predicted_fields.append(predicted_fields)
        elif task == "discovery_official":
            discovery_pairs.append((expected["confirmed"], require_bool(prediction, "confirmed", case_id)))
        elif task == "query_ids":
            predicted_ids = require_string_list(prediction, "ids", case_id)
            expected_ids = set(expected["ids"])
            predicted_id_set = set(predicted_ids)
            query_expected.append(expected_ids)
            query_predicted.append(predicted_id_set)
            query_exact_matches += int(predicted_id_set == expected_ids)
            expected_ranked = expected.get("ranked_ids", expected["ids"])
            predicted_ranked = (
                require_string_list(prediction, "ranked_ids", case_id)
                if "ranked_ids" in expected
                else predicted_ids
            )
            if expected_ids:
                query_rank_expected.append(expected_ids)
                query_rank_targets.append(expected_ranked)
                query_rank_predictions.append(predicted_ranked)
            if "ranked_ids" in prediction:
                explicit = require_string_list(prediction, "ranked_ids", case_id)
                explicit_ranks.append(next((1 / position for position, item in enumerate(explicit, 1) if item in expected_ids), 0.0))
            if "answer_state" in expected:
                answer_state = prediction.get("answer_state")
                if not isinstance(answer_state, str) or not answer_state:
                    raise ValueError(f"prediction {case_id} answer_state must be a non-empty string")
                query_state_total += 1
                query_state_correct += int(answer_state == expected["answer_state"])
                if expected["answer_state"] in NO_ANSWER_STATES:
                    source_gap_total += 1
                    false_reassurance_count += int(bool(predicted_ids) or (expected["answer_state"] == "SOURCE_GAP" and answer_state == "NO_RESULTS"))
        elif task == "claim_support":
            support_status = prediction.get("support_status")
            if not isinstance(support_status, str) or not support_status:
                raise ValueError(f"prediction {case_id} support_status must be string")
            claim_total += 1
            claim_correct += int(support_status == expected["support_status"])
            if support_status == "SUPPORTED":
                predicted_supported_count += 1
                unsupported_claim_count += int(expected["support_status"] != "SUPPORTED")
        elif task == "discovery_verification":
            status = prediction.get("verification_status")
            if not isinstance(status, str) or not status:
                raise ValueError(f"prediction {case_id} verification_status must be string")
            discovery_status_pairs.append((expected["verification_status"] == CONFIRMED_DISCOVERY_STATUS, status == CONFIRMED_DISCOVERY_STATUS))
            discovery_status_total += 1
            discovery_status_correct += int(status == expected["verification_status"])
            if "official_match_ids" in expected:
                discovery_match_expected.append(set(expected["official_match_ids"]))
                discovery_match_predicted.append(_optional_string_set(prediction, "official_match_ids", case_id))
        elif task == "reopen_unread":
            reopen_rows.append(reopen_diagnostics(case, prediction))
        exact = all(prediction.get(key) == value for key, value in expected.items()) if task == "reopen_unread" else prediction == expected
        if exact:
            exact_case_matches += 1
            exact_ids.add(case_id)
        case_results.append({"case_id": case_id, "task": task, "status": "EXACT" if exact else "DIFF"})

    event_metrics = binary_metrics(event_pairs)
    event_metrics.update({
        "false_merge_count": event_metrics["fp"],
        "false_merge_denominator": event_metrics["tp"] + event_metrics["fp"],
        "false_merge_rate": safe_ratio(event_metrics["fp"], event_metrics["tp"] + event_metrics["fp"]),
        "missed_merge_count": event_metrics["fn"],
        "missed_merge_denominator": event_metrics["tp"] + event_metrics["fn"],
        "missed_merge_rate": safe_ratio(event_metrics["fn"], event_metrics["tp"] + event_metrics["fn"]),
    })
    change_metrics = binary_metrics(change_pairs)
    change_metrics["changed_fields"] = set_metrics(change_expected_fields, change_predicted_fields)
    discovery_metrics = binary_metrics(discovery_pairs)
    discovery_metrics["confirmation_precision_denominator"] = discovery_metrics["tp"] + discovery_metrics["fp"]
    discovery_metrics["confirmation_precision"] = safe_ratio(
        discovery_metrics["tp"], discovery_metrics["tp"] + discovery_metrics["fp"]
    )

    query_metrics = set_metrics(query_expected, query_predicted)
    query_metrics["exact_id_match_count"] = query_exact_matches
    query_metrics["exact_id_match_accuracy"] = safe_ratio(query_exact_matches, len(query_expected))
    rank_exact = top1_correct = 0
    reciprocal_rank_sum = 0.0
    for expected_ids, target_order, predicted_order in zip(
        query_rank_expected, query_rank_targets, query_rank_predictions
    ):
        rank_exact += int(predicted_order == target_order)
        if predicted_order and predicted_order[0] in expected_ids:
            top1_correct += 1
        reciprocal_rank = next(
            (1 / position for position, item in enumerate(predicted_order, 1) if item in expected_ids),
            0.0,
        )
        reciprocal_rank_sum += reciprocal_rank
    ranking_denominator = len(query_rank_expected)
    ranking_metrics = {
        "case_denominator": ranking_denominator,
        "exact_order_match_count": rank_exact,
        "exact_order_match_accuracy": safe_ratio(rank_exact, ranking_denominator),
        "top1_correct": top1_correct,
        "top1_accuracy": safe_ratio(top1_correct, ranking_denominator),
        "mrr_sum": reciprocal_rank_sum,
        "mean_reciprocal_rank": safe_ratio(reciprocal_rank_sum, ranking_denominator),
    }
    query_metrics["ranking"] = ranking_metrics

    evidence_metrics = set_metrics(evidence_expected, evidence_predicted)
    evidence_metrics["covered_case_count"] = evidence_fully_covered
    evidence_metrics["case_coverage"] = safe_ratio(evidence_fully_covered, len(evidence_expected))
    evidence_metrics["coverage"] = evidence_metrics["recall"]
    evaluated = len(cases) - len(missing)
    return {
        "schema_version": 3,
        "dataset_id": manifest["dataset_id"],
        "dataset_type": manifest["dataset_type"],
        "dataset_sha256": dataset_fingerprint(manifest, cases),
        "evaluation_scope": manifest.get("evaluation_scope", manifest["dataset_type"]),
        "case_denominator": len(cases),
        "evaluated_cases": evaluated,
        "evaluation_status": "INCOMPLETE_MISSING_PREDICTIONS" if missing else ("COMPLETE" if cases else "NOT_RUN_NO_CASES"),
        "missing_prediction_count": len(missing),
        "missing_prediction_ids": missing,
        "exact_case_match_count": exact_case_matches,
        "exact_case_match_rate": safe_ratio(exact_case_matches, len(cases)),
        "evaluated_exact_case_match_rate": safe_ratio(exact_case_matches, evaluated),
        "event_pair": event_metrics,
        "material_change": change_metrics,
        "discovery_official": discovery_metrics,
        "query_ids": query_metrics,
        "query_answer_state": _accuracy(query_state_correct, query_state_total),
        "no_result_false_reassurance": {
            "false_reassurance_count": false_reassurance_count,
            "denominator": source_gap_total,
            "rate": safe_ratio(false_reassurance_count, source_gap_total),
        },
        "claim_support": _accuracy(claim_correct, claim_total),
        "unsupported_claim_rate": {
            "unsupported_claim_count": unsupported_claim_count,
            "denominator": predicted_supported_count,
            "rate": safe_ratio(unsupported_claim_count, predicted_supported_count),
        },
        "evidence_coverage": evidence_metrics,
        "case_results": case_results,
        "split_metrics": split_metrics(manifest, cases, missing, exact_ids),
        "material_change_fields": change_metrics["changed_fields"],
        "canonical_event_id": _accuracy(canonical_correct, canonical_total),
        "entity_ids": set_metrics(entity_expected, entity_predicted),
        "trust_tier": _accuracy(trust_correct, trust_total),
        "query_exact_id_accuracy": _accuracy(query_exact_matches, len(query_expected)),
        "query_ranking": {"case_denominator": len(query_expected), "ranked_denominator": len(explicit_ranks), "mrr": safe_ratio(sum(explicit_ranks), len(explicit_ranks))},
        "discovery_verification": {**binary_metrics(discovery_status_pairs), "confirmed_status": CONFIRMED_DISCOVERY_STATUS, "status_accuracy": _accuracy(discovery_status_correct, discovery_status_total)},
        "discovery_match_ids": set_metrics(discovery_match_expected, discovery_match_predicted),
        "reopen_unread": aggregate_reopen(reopen_rows),
        "reopen_unread_rows": reopen_rows,
        "targets": manifest.get("targets"),
        "target_assessment": target_assessment(manifest, aggregate_reopen(reopen_rows), missing),
        "not_run": not_run_arms(manifest),
    }


def _report_int(report: dict[str, Any], field: str, label: str) -> int:
    value = report.get(field)
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}.{field} must be a non-negative integer")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields do not match evaluation report schema v3")


def _report_number(metrics: dict[str, Any], field: str, label: str) -> float | None:
    value = metrics.get(field)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}.{field} must be a finite number")
    if not 0 <= value <= 1:
        raise ValueError(f"{label}.{field} must be between 0 and 1")
    return float(value)


def _validate_rate(metrics: dict[str, Any], field: str, numerator: int, denominator: int, label: str) -> None:
    value = _report_number(metrics, field, label)
    expected = safe_ratio(numerator, denominator)
    if expected is None and value is None:
        return
    if expected is None or value is None or not math.isclose(value, expected, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label}.{field} does not match its counts")


def _validate_binary_metrics(metrics: dict[str, Any], label: str, extras: set[str] | None = None) -> int:
    extras = extras or set()
    keys = {"denominator", "tp", "fp", "fn", "tn", "precision", "recall", "f1"}
    _require_exact_keys(metrics, keys | extras, label)
    denominator = _report_int(metrics, "denominator", label)
    counts = {name: _report_int(metrics, name, label) for name in ("tp", "fp", "fn", "tn")}
    if sum(counts.values()) != denominator:
        raise ValueError(f"{label} has an invalid denominator")
    _validate_rate(metrics, "precision", counts["tp"], counts["tp"] + counts["fp"], label)
    _validate_rate(metrics, "recall", counts["tp"], counts["tp"] + counts["fn"], label)
    precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else None
    recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else None
    expected_f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    f1 = _report_number(metrics, "f1", label)
    if expected_f1 is None and f1 is None:
        pass
    elif expected_f1 is None or f1 is None or not math.isclose(f1, expected_f1, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label}.f1 does not match its counts")
    return denominator


def _validate_set_metrics(metrics: dict[str, Any], label: str, extras: set[str] | None = None) -> int:
    extras = extras or set()
    keys = {
        "case_denominator", "expected_id_denominator", "predicted_id_denominator",
        "tp", "fp", "fn", "precision", "recall", "f1",
    }
    _require_exact_keys(metrics, keys | extras, label)
    case_denominator = _report_int(metrics, "case_denominator", label)
    expected_ids = _report_int(metrics, "expected_id_denominator", label)
    predicted_ids = _report_int(metrics, "predicted_id_denominator", label)
    tp, fp, fn = (_report_int(metrics, name, label) for name in ("tp", "fp", "fn"))
    if tp + fn != expected_ids or tp + fp != predicted_ids:
        raise ValueError(f"{label} counts do not match its ID denominators")
    _validate_rate(metrics, "precision", tp, predicted_ids, label)
    _validate_rate(metrics, "recall", tp, expected_ids, label)
    precision = tp / predicted_ids if predicted_ids else None
    recall = tp / expected_ids if expected_ids else None
    expected_f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and recall is not None and precision + recall
        else None
    )
    f1 = _report_number(metrics, "f1", label)
    if expected_f1 is None and f1 is None:
        pass
    elif expected_f1 is None or f1 is None or not math.isclose(f1, expected_f1, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label}.f1 does not match its counts")
    return case_denominator


def _validate_accuracy(metrics: dict[str, Any], label: str) -> int:
    _require_exact_keys(metrics, {"denominator", "correct", "accuracy"}, label)
    denominator = _report_int(metrics, "denominator", label)
    correct = _report_int(metrics, "correct", label)
    if correct > denominator:
        raise ValueError(f"{label} has an invalid count")
    _validate_rate(metrics, "accuracy", correct, denominator, label)
    return denominator


def validate_evaluation_report(report: dict[str, Any], label: str) -> dict[str, int]:
    """Validate a complete v3 report before it can be used as a baseline."""
    if not isinstance(report, dict):
        raise ValueError(f"{label} must be a JSON object")
    if type(report.get("schema_version")) is not int or report["schema_version"] != 3:
        raise ValueError(f"{label} has an unsupported schema_version")
    report_fields = {
        "schema_version", "dataset_id", "dataset_type", "dataset_sha256",
        "case_denominator", "evaluated_cases", "missing_prediction_count",
        "missing_prediction_ids", "exact_case_match_count", "exact_case_match_rate",
        "event_pair", "material_change", "discovery_official", "query_ids",
        "query_answer_state", "no_result_false_reassurance", "claim_support",
        "unsupported_claim_rate", "evidence_coverage", "evaluation_scope", "evaluation_status", "evaluated_exact_case_match_rate",
        "reopen_unread", "reopen_unread_rows", "targets", "target_assessment", "not_run", "case_results", "split_metrics",
        "material_change_fields", "canonical_event_id", "entity_ids", "trust_tier", "query_exact_id_accuracy", "query_ranking", "discovery_verification", "discovery_match_ids",
    }
    if "baseline_comparison" in report:
        report_fields.add("baseline_comparison")
    if "promotion_gate" in report:
        report_fields.add("promotion_gate")
    _require_exact_keys(report, report_fields, label)
    if "promotion_gate" in report:
        gate = report["promotion_gate"]
        _require_exact_keys(
            gate,
            {"schema_version", "claim", "status", "eligible", "blocked", "policy_status", "comparison_status", "blockers"},
            f"{label}.promotion_gate",
        )
        if type(gate["schema_version"]) is not int or gate["schema_version"] != 1:
            raise ValueError(f"{label}.promotion_gate has an unsupported schema_version")
        if gate["claim"] != "NEW_MODEL_IS_BETTER":
            raise ValueError(f"{label}.promotion_gate has an unsupported claim")
        if gate["status"] not in {"ELIGIBLE", "NOT_ELIGIBLE", "BLOCKED", "UNDEFINED"}:
            raise ValueError(f"{label}.promotion_gate has an unsupported status")
        if type(gate["eligible"]) is not bool or gate["eligible"] != (gate["status"] == "ELIGIBLE"):
            raise ValueError(f"{label}.promotion_gate eligibility is inconsistent")
        if type(gate["blocked"]) is not bool or gate["blocked"] == gate["eligible"]:
            raise ValueError(f"{label}.promotion_gate blocked flag is inconsistent")
        if not isinstance(gate["policy_status"], str) or not gate["policy_status"]:
            raise ValueError(f"{label}.promotion_gate policy_status must be a non-empty string")
        if gate["comparison_status"] not in {
            "NOT_RUN", "NO_REGRESSION", "REGRESSION", "UNSCORABLE", "INCOMPLETE", "INVALID"
        }:
            raise ValueError(f"{label}.promotion_gate has an unsupported comparison_status")
        if not isinstance(gate["blockers"], list) or any(not isinstance(item, str) for item in gate["blockers"]):
            raise ValueError(f"{label}.promotion_gate blockers must be a string array")
    for field in ("dataset_id", "dataset_type"):
        if not isinstance(report.get(field), str) or not report[field]:
            raise ValueError(f"{label}.{field} must be a non-empty string")
    fingerprint = report.get("dataset_sha256")
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        raise ValueError(f"{label}.dataset_sha256 must be a lowercase SHA-256 digest")

    case_denominator = _report_int(report, "case_denominator", label)
    evaluated_cases = _report_int(report, "evaluated_cases", label)
    missing_count = _report_int(report, "missing_prediction_count", label)
    missing_ids = report.get("missing_prediction_ids")
    if case_denominator == 0 or evaluated_cases != case_denominator or missing_count != 0 or missing_ids != []:
        raise ValueError(f"{label} must contain a complete, non-empty evaluation")

    exact_count = _report_int(report, "exact_case_match_count", label)
    if exact_count > evaluated_cases:
        raise ValueError(f"{label}.exact_case_match_count exceeds evaluated_cases")
    exact_rate = report.get("exact_case_match_rate")
    if isinstance(exact_rate, bool) or not isinstance(exact_rate, (int, float)) or not math.isfinite(exact_rate):
        raise ValueError(f"{label}.exact_case_match_rate must be a finite number")
    if not math.isclose(float(exact_rate), exact_count / evaluated_cases, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label}.exact_case_match_rate does not match its counts")

    event = report["event_pair"]
    event_denominator = _validate_binary_metrics(
        event,
        f"{label}.event_pair",
        {"false_merge_count", "false_merge_denominator", "false_merge_rate", "missed_merge_count", "missed_merge_denominator", "missed_merge_rate"},
    )
    false_merge_denominator = _report_int(event, "false_merge_denominator", f"{label}.event_pair")
    missed_merge_denominator = _report_int(event, "missed_merge_denominator", f"{label}.event_pair")
    false_merge_count = _report_int(event, "false_merge_count", f"{label}.event_pair")
    missed_merge_count = _report_int(event, "missed_merge_count", f"{label}.event_pair")
    if false_merge_count != event["fp"] or false_merge_denominator != event["tp"] + event["fp"]:
        raise ValueError(f"{label}.event_pair false merge counts are inconsistent")
    if missed_merge_count != event["fn"] or missed_merge_denominator != event["tp"] + event["fn"]:
        raise ValueError(f"{label}.event_pair missed merge counts are inconsistent")
    _validate_rate(event, "false_merge_rate", event["fp"], false_merge_denominator, f"{label}.event_pair")
    _validate_rate(event, "missed_merge_rate", event["fn"], missed_merge_denominator, f"{label}.event_pair")

    change = report["material_change"]
    change_denominator = _validate_binary_metrics(
        {key: value for key, value in change.items() if key != "changed_fields"},
        f"{label}.material_change",
    )
    field_denominator = _validate_set_metrics(change.get("changed_fields"), f"{label}.material_change.changed_fields")
    if field_denominator != change_denominator:
        raise ValueError(f"{label}.material_change changed-field case denominator is inconsistent")

    discovery = report["discovery_official"]
    discovery_denominator = _validate_binary_metrics(
        discovery,
        f"{label}.discovery_official",
        {"confirmation_precision_denominator", "confirmation_precision"},
    )
    confirmation_denominator = _report_int(discovery, "confirmation_precision_denominator", f"{label}.discovery_official")
    if confirmation_denominator != discovery["tp"] + discovery["fp"]:
        raise ValueError(f"{label}.discovery_official confirmation denominator is inconsistent")
    _validate_rate(discovery, "confirmation_precision", discovery["tp"], confirmation_denominator, f"{label}.discovery_official")

    query = report["query_ids"]
    query_core = {key: value for key, value in query.items() if key not in {"exact_id_match_count", "exact_id_match_accuracy", "ranking"}}
    query_denominator = _validate_set_metrics(
        query_core,
        f"{label}.query_ids",
        {"case_denominator", "expected_id_denominator", "predicted_id_denominator", "tp", "fp", "fn", "precision", "recall", "f1"},
    )
    exact_id_count = _report_int(query, "exact_id_match_count", f"{label}.query_ids")
    if exact_id_count > query_denominator:
        raise ValueError(f"{label}.query_ids exact ID count exceeds its denominator")
    _validate_rate(query, "exact_id_match_accuracy", exact_id_count, query_denominator, f"{label}.query_ids")
    ranking = query.get("ranking")
    _require_exact_keys(
        ranking,
        {"case_denominator", "exact_order_match_count", "exact_order_match_accuracy", "top1_correct", "top1_accuracy", "mrr_sum", "mean_reciprocal_rank"},
        f"{label}.query_ids.ranking",
    )
    ranking_denominator = _report_int(ranking, "case_denominator", f"{label}.query_ids.ranking")
    exact_order = _report_int(ranking, "exact_order_match_count", f"{label}.query_ids.ranking")
    top1 = _report_int(ranking, "top1_correct", f"{label}.query_ids.ranking")
    if ranking_denominator > query_denominator or exact_order > ranking_denominator or top1 > ranking_denominator:
        raise ValueError(f"{label}.query_ids.ranking counts are inconsistent")
    _validate_rate(ranking, "exact_order_match_accuracy", exact_order, ranking_denominator, f"{label}.query_ids.ranking")
    _validate_rate(ranking, "top1_accuracy", top1, ranking_denominator, f"{label}.query_ids.ranking")
    mrr_sum = ranking.get("mrr_sum")
    if isinstance(mrr_sum, bool) or not isinstance(mrr_sum, (int, float)) or not math.isfinite(mrr_sum):
        raise ValueError(f"{label}.query_ids.ranking.mrr_sum must be finite")
    if not 0 <= mrr_sum <= ranking_denominator:
        raise ValueError(f"{label}.query_ids.ranking.mrr_sum is outside its denominator")
    mrr = _report_number(ranking, "mean_reciprocal_rank", f"{label}.query_ids.ranking")
    expected_mrr = safe_ratio(mrr_sum, ranking_denominator)
    if (mrr is None) != (expected_mrr is None) or (
        mrr is not None and expected_mrr is not None and not math.isclose(mrr, expected_mrr, rel_tol=1e-12, abs_tol=1e-12)
    ):
        raise ValueError(f"{label}.query_ids.ranking MRR does not match its counts")

    state_denominator = _validate_accuracy(report["query_answer_state"], f"{label}.query_answer_state")
    false_reassurance = report["no_result_false_reassurance"]
    _require_exact_keys(false_reassurance, {"false_reassurance_count", "denominator", "rate"}, f"{label}.no_result_false_reassurance")
    no_result_denominator = _report_int(false_reassurance, "denominator", f"{label}.no_result_false_reassurance")
    false_reassurance_count = _report_int(false_reassurance, "false_reassurance_count", f"{label}.no_result_false_reassurance")
    if false_reassurance_count > no_result_denominator or no_result_denominator > state_denominator:
        raise ValueError(f"{label}.no_result_false_reassurance has invalid counts")
    _validate_rate(false_reassurance, "rate", false_reassurance_count, no_result_denominator, f"{label}.no_result_false_reassurance")

    claim_denominator = _validate_accuracy(report["claim_support"], f"{label}.claim_support")
    unsupported = report["unsupported_claim_rate"]
    _require_exact_keys(unsupported, {"unsupported_claim_count", "denominator", "rate"}, f"{label}.unsupported_claim_rate")
    unsupported_count = _report_int(unsupported, "unsupported_claim_count", f"{label}.unsupported_claim_rate")
    predicted_supported = _report_int(unsupported, "denominator", f"{label}.unsupported_claim_rate")
    if unsupported_count > predicted_supported:
        raise ValueError(f"{label}.unsupported_claim_rate has invalid counts")
    _validate_rate(unsupported, "rate", unsupported_count, predicted_supported, f"{label}.unsupported_claim_rate")

    evidence = report["evidence_coverage"]
    evidence_denominator = _validate_set_metrics(
        evidence,
        f"{label}.evidence_coverage",
        {"covered_case_count", "case_coverage", "coverage"},
    )
    covered_cases = _report_int(evidence, "covered_case_count", f"{label}.evidence_coverage")
    if covered_cases > evidence_denominator or evidence_denominator > case_denominator:
        raise ValueError(f"{label}.evidence_coverage has invalid case counts")
    _validate_rate(evidence, "case_coverage", covered_cases, evidence_denominator, f"{label}.evidence_coverage")

    if evidence["coverage"] != evidence["recall"] or report["material_change_fields"] != change["changed_fields"]:
        raise ValueError(f"{label} auxiliary metric binding mismatch: denominators do not match")
    discovery_v1 = _validate_binary_metrics(report["discovery_verification"], f"{label}.discovery_verification", {"confirmed_status", "status_accuracy"})
    for field in ("entity_ids", "discovery_match_ids"):
        if _validate_set_metrics(report[field], f"{label}.{field}") > case_denominator:
            raise ValueError(f"{label}.{field} denominator exceeds the case count")
    for field in ("canonical_event_id", "trust_tier", "query_exact_id_accuracy"):
        if _validate_accuracy(report[field], f"{label}.{field}") > case_denominator:
            raise ValueError(f"{label}.{field} denominator exceeds the case count")
    if report["query_exact_id_accuracy"] != _accuracy(query["exact_id_match_count"], query_denominator):
        raise ValueError(f"{label} query accuracy aliases do not match")
    status_accuracy = report["discovery_verification"]["status_accuracy"]
    if _validate_accuracy(status_accuracy, f"{label}.discovery_verification.status_accuracy") != discovery_v1:
        raise ValueError(f"{label} discovery status denominator mismatch")
    if report["discovery_verification"]["confirmed_status"] != CONFIRMED_DISCOVERY_STATUS:
        raise ValueError(f"{label} unknown discovery confirmation status")
    ranks = report["query_ranking"]
    _require_exact_keys(ranks, {"case_denominator", "ranked_denominator", "mrr"}, f"{label}.query_ranking")
    ranked = _report_int(ranks, "ranked_denominator", label)
    if ranks["case_denominator"] != query_denominator or ranked > query_denominator:
        raise ValueError(f"{label} query ranking denominator mismatch")
    mean = ranks["mrr"]
    if (ranked == 0 and mean is not None) or (ranked and (isinstance(mean, bool) or not isinstance(mean, (int, float)) or not math.isfinite(mean) or not 0 <= mean <= 1)):
        raise ValueError(f"{label} invalid query ranking rate")
    results = report["case_results"]
    if not isinstance(results, list) or len(results) != case_denominator:
        raise ValueError(f"{label} case result denominator mismatch")
    for row in results:
        _require_exact_keys(row, {"case_id", "task", "status"}, f"{label}.case_results")
        if not isinstance(row["case_id"], str) or not row["case_id"] or row["status"] not in {"EXACT", "DIFF", "MISSING"}:
            raise ValueError(f"{label} invalid case result")
    if len({row["case_id"] for row in results}) != case_denominator or sum(row["status"] == "EXACT" for row in results) != report["exact_case_match_count"]:
        raise ValueError(f"{label} case result counts disagree with the aggregate")
    if report["reopen_unread"] != aggregate_reopen(report["reopen_unread_rows"]):
        raise ValueError(f"{label} reopen counters disagree with their rows")
    task_case_total = event_denominator + change_denominator + discovery_denominator + query_denominator + claim_denominator + discovery_v1 + len(report["reopen_unread_rows"])
    if task_case_total != case_denominator:
        raise ValueError(f"{label} task denominators do not match case_denominator")
    return {
        "case_denominator": case_denominator,
        "event_pair": event_denominator,
        "material_change": change_denominator,
        "changed_field_cases": field_denominator,
        "changed_field_ids": change["changed_fields"]["expected_id_denominator"],
        "discovery_official": discovery_denominator,
        "query_ids_cases": query_denominator,
        "query_ids_expected_ids": query["expected_id_denominator"],
        "query_ids_exact_cases": query_denominator,
        "query_ranking_cases": ranking_denominator,
        "query_answer_state": state_denominator,
        "no_result_false_reassurance_cases": no_result_denominator,
        "claim_support": claim_denominator,
        "evidence_cases": evidence_denominator,
        "evidence_expected_ids": evidence["expected_id_denominator"],
    }


def build_promotion_gate(
    dataset_type: str,
    *,
    case_denominator: int,
    evaluated_cases: int,
    missing_prediction_count: int,
    comparison_status: str | None,
) -> dict[str, Any]:
    """Fail closed for a 'new model is better' claim until human labels and policy exist."""
    blockers: list[str] = []
    if case_denominator == 0 or evaluated_cases != case_denominator or missing_prediction_count != 0:
        blockers.append("INCOMPLETE_EVALUATION")

    if comparison_status == "REGRESSION":
        blockers.append("REGRESSION")
    elif comparison_status == "UNSCORABLE":
        blockers.append("UNSCORABLE")
    elif comparison_status == "INCOMPLETE":
        if "INCOMPLETE_EVALUATION" not in blockers:
            blockers.append("INCOMPLETE_EVALUATION")
    elif comparison_status == "INVALID":
        blockers.append("BASELINE_COMPARISON_INVALID")
    elif comparison_status is None:
        blockers.append("BASELINE_COMPARISON_REQUIRED")
    elif comparison_status != "NO_REGRESSION":
        blockers.append("BASELINE_COMPARISON_INVALID")

    if dataset_type == "SYNTHETIC_REGRESSION_SEED":
        blockers.append("SYNTHETIC_ONLY")
        status = "NOT_ELIGIBLE"
    else:
        blockers.append("PROMOTION_POLICY_UNCONFIGURED")
        status = "BLOCKED" if any(
            reason in blockers
            for reason in ("INCOMPLETE_EVALUATION", "REGRESSION", "UNSCORABLE", "BASELINE_COMPARISON_REQUIRED", "BASELINE_COMPARISON_INVALID")
        ) else "UNDEFINED"

    return {
        "schema_version": 1,
        "claim": "NEW_MODEL_IS_BETTER",
        "status": status,
        "eligible": False,
        "blocked": True,
        "policy_status": "UNCONFIGURED",
        "comparison_status": comparison_status or "NOT_RUN",
        "blockers": blockers,
    }


def compare_pinned_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    """Compare two complete reports, rejecting incompatible evaluation scopes."""
    current_denominators = validate_evaluation_report(current, "current report")
    baseline_denominators = validate_evaluation_report(baseline, "baseline report")
    if current["schema_version"] != baseline["schema_version"]:
        raise ValueError("baseline report schema does not match current report")
    for field in ("dataset_id", "dataset_type"):
        if current[field] != baseline[field]:
            raise ValueError(f"baseline report {field} does not match current report")
    if current["dataset_sha256"] != baseline["dataset_sha256"]:
        raise ValueError("baseline report dataset_sha256 does not match current report")
    if current_denominators != baseline_denominators:
        raise ValueError("baseline report denominators do not match current report")

    score_paths = (
        ("exact_case_match_rate", ("exact_case_match_rate",), "higher_is_better"),
        ("event_pair.precision", ("event_pair", "precision"), "higher_is_better"),
        ("event_pair.recall", ("event_pair", "recall"), "higher_is_better"),
        ("event_pair.f1", ("event_pair", "f1"), "higher_is_better"),
        ("event_pair.false_merge_rate", ("event_pair", "false_merge_rate"), "lower_is_better"),
        ("event_pair.missed_merge_rate", ("event_pair", "missed_merge_rate"), "lower_is_better"),
        ("material_change.precision", ("material_change", "precision"), "higher_is_better"),
        ("material_change.recall", ("material_change", "recall"), "higher_is_better"),
        ("material_change.f1", ("material_change", "f1"), "higher_is_better"),
        ("material_change.changed_fields.precision", ("material_change", "changed_fields", "precision"), "higher_is_better"),
        ("material_change.changed_fields.recall", ("material_change", "changed_fields", "recall"), "higher_is_better"),
        ("material_change.changed_fields.f1", ("material_change", "changed_fields", "f1"), "higher_is_better"),
        ("discovery_official.confirmation_precision", ("discovery_official", "confirmation_precision"), "higher_is_better"),
        ("discovery_official.recall", ("discovery_official", "recall"), "higher_is_better"),
        ("discovery_official.f1", ("discovery_official", "f1"), "higher_is_better"),
        ("query_ids.precision", ("query_ids", "precision"), "higher_is_better"),
        ("query_ids.recall", ("query_ids", "recall"), "higher_is_better"),
        ("query_ids.f1", ("query_ids", "f1"), "higher_is_better"),
        ("query_ids.exact_id_match_accuracy", ("query_ids", "exact_id_match_accuracy"), "higher_is_better"),
        ("query_ids.ranking.exact_order_match_accuracy", ("query_ids", "ranking", "exact_order_match_accuracy"), "higher_is_better"),
        ("query_ids.ranking.top1_accuracy", ("query_ids", "ranking", "top1_accuracy"), "higher_is_better"),
        ("query_ids.ranking.mean_reciprocal_rank", ("query_ids", "ranking", "mean_reciprocal_rank"), "higher_is_better"),
        ("query_answer_state.accuracy", ("query_answer_state", "accuracy"), "higher_is_better"),
        ("no_result_false_reassurance.rate", ("no_result_false_reassurance", "rate"), "lower_is_better"),
        ("claim_support.accuracy", ("claim_support", "accuracy"), "higher_is_better"),
        ("unsupported_claim_rate.rate", ("unsupported_claim_rate", "rate"), "lower_is_better"),
        ("evidence_coverage.recall", ("evidence_coverage", "recall"), "higher_is_better"),
        ("evidence_coverage.case_coverage", ("evidence_coverage", "case_coverage"), "higher_is_better"),
    )
    score_deltas: dict[str, dict[str, Any]] = {}
    regressed: list[str] = []
    improved: list[str] = []
    unchanged: list[str] = []
    unscorable: list[str] = []
    for name, path, direction in score_paths:
        before = baseline
        after = current
        for part in path:
            before = before[part]
            after = after[part]
        delta = None if before is None or after is None else round(float(after) - float(before), 12)
        score_deltas[name] = {"baseline": before, "current": after, "delta": delta, "direction": direction}
        if delta is None:
            unscorable.append(name)
            continue
        regressed_score = delta < 0 if direction == "higher_is_better" else delta > 0
        improved_score = delta > 0 if direction == "higher_is_better" else delta < 0
        if regressed_score:
            regressed.append(name)
        elif improved_score:
            improved.append(name)
        else:
            unchanged.append(name)

    count_paths = (
        ("exact_case_match_count", ("exact_case_match_count",)),
        *((f"event_pair.{count}", ("event_pair", count)) for count in ("tp", "fp", "fn", "tn", "false_merge_count", "missed_merge_count")),
        *((f"material_change.{count}", ("material_change", count)) for count in ("tp", "fp", "fn", "tn")),
        *((f"material_change.changed_fields.{count}", ("material_change", "changed_fields", count)) for count in ("tp", "fp", "fn", "expected_id_denominator", "predicted_id_denominator")),
        *((f"discovery_official.{count}", ("discovery_official", count)) for count in ("tp", "fp", "fn", "tn")),
        *((f"query_ids.{count}", ("query_ids", count)) for count in ("tp", "fp", "fn", "expected_id_denominator", "predicted_id_denominator", "exact_id_match_count")),
        ("query_ids.ranking.exact_order_match_count", ("query_ids", "ranking", "exact_order_match_count")),
        ("query_ids.ranking.top1_correct", ("query_ids", "ranking", "top1_correct")),
        ("query_answer_state.correct", ("query_answer_state", "correct")),
        ("no_result_false_reassurance.false_reassurance_count", ("no_result_false_reassurance", "false_reassurance_count")),
        ("claim_support.correct", ("claim_support", "correct")),
        ("unsupported_claim_rate.unsupported_claim_count", ("unsupported_claim_rate", "unsupported_claim_count")),
        ("unsupported_claim_rate.predicted_supported_denominator", ("unsupported_claim_rate", "denominator")),
        ("evidence_coverage.tp", ("evidence_coverage", "tp")),
        ("evidence_coverage.fp", ("evidence_coverage", "fp")),
        ("evidence_coverage.fn", ("evidence_coverage", "fn")),
        ("evidence_coverage.predicted_id_denominator", ("evidence_coverage", "predicted_id_denominator")),
        ("evidence_coverage.covered_case_count", ("evidence_coverage", "covered_case_count")),
    )
    count_deltas: dict[str, dict[str, int | float]] = {}
    for name, path in count_paths:
        before = baseline
        after = current
        for part in path:
            before = before[part]
            after = after[part]
        count_deltas[name] = {"baseline": before, "current": after, "delta": after - before}

    regression_status = "REGRESSION" if regressed else "UNSCORABLE" if unscorable else "NO_REGRESSION"
    promotion_gate = build_promotion_gate(
        current["dataset_type"],
        case_denominator=current["case_denominator"],
        evaluated_cases=current["evaluated_cases"],
        missing_prediction_count=current["missing_prediction_count"],
        comparison_status=regression_status,
    )
    return {
        "schema_version": 2,
        "comparison_type": "GOLD_EVALUATION_BASELINE_COMPARISON",
        "dataset_id": current["dataset_id"],
        "dataset_type": current["dataset_type"],
        "case_denominator": current["case_denominator"],
        "regression_status": regression_status,
        "interpretation": "NO_REGRESSION means no compared score decreased; it is not an accuracy or quality claim.",
        "promotion_gate": promotion_gate,
        "score_deltas": score_deltas,
        "count_deltas": count_deltas,
        "regressed_metrics": regressed,
        "improved_metrics": improved,
        "unchanged_metrics": unchanged,
        "unscorable_metrics": unscorable,
        "denominators": {
            **current_denominators,
            "query_ids_baseline_predicted_ids": baseline["query_ids"]["predicted_id_denominator"],
            "query_ids_current_predicted_ids": current["query_ids"]["predicted_id_denominator"],
            "unsupported_claims_baseline_predicted_supported": baseline["unsupported_claim_rate"]["denominator"],
            "unsupported_claims_current_predicted_supported": current["unsupported_claim_rate"]["denominator"],
            "evidence_baseline_predicted_ids": baseline["evidence_coverage"]["predicted_id_denominator"],
            "evidence_current_predicted_ids": current["evidence_coverage"]["predicted_id_denominator"],
        },
    }


def compare_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    pinned = compare_pinned_reports(current, baseline)
    diagnostics = compare_report_diagnostics(current, baseline)
    result = {**diagnostics, **pinned}
    if diagnostics["regressions_found"]:
        result["regression_status"] = "REGRESSION"
        result["promotion_gate"] = build_promotion_gate(current["dataset_type"], case_denominator=current["case_denominator"], evaluated_cases=current["evaluated_cases"], missing_prediction_count=current["missing_prediction_count"], comparison_status="REGRESSION")
    return result


def perfect_predictions(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {case["case_id"]: deepcopy(case["expected"]) for case in cases}


def self_check(manifest_path: Path = DEFAULT_MANIFEST) -> None:
    manifest, cases = load_gold(manifest_path)
    perfect = evaluate(manifest, cases, perfect_predictions(cases))
    assert perfect["missing_prediction_count"] == 0
    assert perfect["exact_case_match_rate"] == 1.0
    assert perfect["event_pair"]["f1"] == 1.0
    assert perfect["material_change"]["f1"] == 1.0
    assert perfect["query_ids"]["f1"] == 1.0
    assert perfect["query_answer_state"]["accuracy"] == 1.0
    assert perfect["claim_support"]["accuracy"] == 1.0

    wrong = perfect_predictions(cases)
    wrong["event-different-date-001"] = {"same_event": True}
    degraded = evaluate(manifest, cases, wrong)
    assert degraded["event_pair"]["fp"] == 1
    assert degraded["event_pair"]["f1"] < 1.0
    comparison = compare_reports(degraded, perfect)
    assert comparison["regression_status"] == "REGRESSION"
    assert comparison["score_deltas"]["event_pair.precision"]["delta"] < 0
    assert "event_pair.precision" in comparison["regressed_metrics"]

    wrong_state = perfect_predictions(cases)
    wrong_state["query-zero-failed-001"]["answer_state"] = "NO_RESULTS"
    degraded_state = evaluate(manifest, cases, wrong_state)
    assert degraded_state["query_ids"]["f1"] == 1.0
    assert degraded_state["query_answer_state"]["accuracy"] < 1.0
    assert degraded_state["no_result_false_reassurance"]["rate"] == 1.0
    state_comparison = compare_reports(degraded_state, perfect)
    assert state_comparison["regression_status"] == "REGRESSION"
    assert state_comparison["score_deltas"]["query_answer_state.accuracy"]["delta"] < 0
    discovery_false_positive = perfect_predictions(cases)
    if "discovery-official-mismatch-001" in discovery_false_positive:
        discovery_false_positive["discovery-official-mismatch-001"]["confirmed"] = True
        degraded_discovery = evaluate(manifest, cases, discovery_false_positive)
        assert degraded_discovery["discovery_official"]["fp"] == 1
        assert degraded_discovery["discovery_official"]["confirmation_precision"] < 1.0

    unsupported_claim = perfect_predictions(cases)
    unsupported_claim["claim-cause-unsupported-001"]["support_status"] = "SUPPORTED"
    degraded_claim = evaluate(manifest, cases, unsupported_claim)
    assert degraded_claim["unsupported_claim_rate"]["rate"] > 0.0
    print(f"GOLD_EVAL_SELF_CHECK_OK dataset={manifest['dataset_id']} cases={len(cases)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-report", "--baseline", type=Path, help="compare this evaluation against a prior JSON report")
    parser.add_argument(
        "--require-promotion-gate",
        action="store_true",
        help="exit nonzero unless the machine-readable new-model-is-better promotion gate is eligible",
    )
    parser.add_argument("--fail-on-regression", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        if args.require_promotion_gate:
            raise SystemExit("--require-promotion-gate requires a prediction evaluation, not --self-check")
        self_check(args.manifest)
        return 0
    if not args.predictions:
        raise SystemExit("--predictions is required unless --self-check is used")
    manifest, cases = load_gold(args.manifest)
    predictions = index_predictions(read_jsonl(args.predictions))
    report = evaluate(manifest, cases, predictions)
    comparison: dict[str, Any] | None = None
    if args.baseline_report:
        try:
            baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
            comparison = compare_reports(report, baseline)
        except (OSError, ValueError):
            if not args.require_promotion_gate:
                raise
            comparison_status = (
                "INCOMPLETE"
                if report["case_denominator"] == 0
                or report["evaluated_cases"] != report["case_denominator"]
                or report["missing_prediction_count"] != 0
                else "INVALID"
            )
            report["promotion_gate"] = build_promotion_gate(
                manifest["dataset_type"],
                case_denominator=report["case_denominator"],
                evaluated_cases=report["evaluated_cases"],
                missing_prediction_count=report["missing_prediction_count"],
                comparison_status=comparison_status,
            )
        else:
            report["baseline_comparison"] = comparison
            report["promotion_gate"] = comparison["promotion_gate"]
    else:
        report["promotion_gate"] = build_promotion_gate(
            manifest["dataset_type"],
            case_denominator=report["case_denominator"],
            evaluated_cases=report["evaluated_cases"],
            missing_prediction_count=report["missing_prediction_count"],
            comparison_status=None,
        )
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.fail_on_regression and report.get("baseline_comparison", {}).get("gate", {}).get("status") == "FAIL":
        return 3
    if args.require_promotion_gate and report["promotion_gate"]["eligible"] is not True:
        return 3
    return 0 if report["missing_prediction_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
