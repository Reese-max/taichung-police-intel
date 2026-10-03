#!/usr/bin/env python3
"""Versioned regression evaluator for GovIntel gold cases.

This harness reports engineering metrics with explicit denominators. A synthetic
starter set validates metric plumbing only; it must never be presented as measured
production accuracy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "eval/gold/v1/manifest.json"
DEFAULT_SCENARIO = ROOT / "eval/gold/v6/commute-reopen-v1/session.json"

# Disposition reasons a station prompt can be wrong for. Each is counted on its
# own so one number never hides another (issue #108 acceptance).
FALSE_ALERT_REASONS = ("ALREADY_READ", "FORMAT_ONLY", "REPOST_MIRROR", "POST_CANCEL")
DEDUPE_REASONS = ("FORMAT_ONLY", "REPOST_MIRROR", "DUPLICATE_FETCH")
GAP_REASONS = ("CONNECTION_FAILURE", "PARTIAL_COVERAGE", "SOURCE_ITEM_MISSING")


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
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported gold manifest schema")
    cases = read_jsonl(manifest_path.parent / manifest["case_file"])
    validate_cases(cases)
    return manifest, cases


def validate_cases(cases: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    supported = {"event_pair", "material_change", "query_ids", "claim_support", "reopen_unread"}
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
        if case.get("synthetic") is not True:
            reviewer = case.get("reviewer")
            if not isinstance(reviewer, str) or not reviewer:
                raise ValueError(f"non-synthetic case requires reviewer: {case_id}")
        expected = case.get("expected")
        if not isinstance(expected, dict):
            raise ValueError(f"expected object missing: {case_id}")
        if task == "event_pair" and not isinstance(expected.get("same_event"), bool):
            raise ValueError(f"event_pair expected same_event must be boolean: {case_id}")
        if task == "material_change" and not isinstance(expected.get("material_change"), bool):
            raise ValueError(f"material_change expected material_change must be boolean: {case_id}")
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
    return value


def safe_ratio(num: int, den: int) -> float | None:
    return None if den == 0 else num / den


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

    false_positives = predicted_ids - expected_ids
    false_negatives = expected_ids - predicted_ids
    counters = {reason: 0 for reason in FALSE_ALERT_REASONS}
    counters["other_false_alert"] = 0
    counters["distinct_correction_wrongly_deduped"] = 0
    counters["missed_while_source_gap"] = 0
    counters["missed_without_recorded_reason"] = 0
    for update_id in false_positives:
        reason = decisions.get(update_id, "UNRECORDED")
        counters[reason if reason in counters else "other_false_alert"] += 1
    for update_id in false_negatives:
        reason = decisions.get(update_id)
        if reason in DEDUPE_REASONS:
            counters["distinct_correction_wrongly_deduped"] += 1
        elif set(gap_kinds) & set(GAP_REASONS):
            counters["missed_while_source_gap"] += 1
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
        "source_gap_kinds": sorted(set(gap_kinds)),
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
        "condition_status_accuracy": safe_ratio(condition_correct, condition_total),
    }


def not_run_arms(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    arms = manifest.get("arms")
    if not isinstance(arms, dict):
        return []
    pending = []
    for arm_id, arm in sorted(arms.items()):
        if not isinstance(arm, dict) or arm.get("status") != "NOT_RUN":
            continue
        pending.append(
            {
                "arm": arm_id,
                "method_id": arm.get("method_id"),
                "status": "NOT_RUN",
            }
        )
    return pending


def evaluate(manifest: dict[str, Any], cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    case_ids = {case["case_id"] for case in cases}
    unknown_predictions = sorted(set(predictions) - case_ids)
    if unknown_predictions:
        raise ValueError("predictions contain unknown case IDs: " + ", ".join(unknown_predictions))

    event_pairs: list[tuple[bool, bool]] = []
    change_pairs: list[tuple[bool, bool]] = []
    query_expected: list[set[str]] = []
    query_predicted: list[set[str]] = []
    query_state_total = query_state_correct = 0
    claim_total = claim_correct = 0
    reopen_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    exact_case_matches = 0

    for case in cases:
        case_id = case["case_id"]
        prediction = predictions.get(case_id)
        if prediction is None:
            missing.append(case_id)
            continue
        expected = case["expected"]
        task = case["task"]
        if task == "event_pair":
            event_pairs.append((expected["same_event"], require_bool(prediction, "same_event", case_id)))
        elif task == "material_change":
            change_pairs.append((expected["material_change"], require_bool(prediction, "material_change", case_id)))
        elif task == "query_ids":
            predicted_ids = require_string_list(prediction, "ids", case_id)
            query_expected.append(set(expected["ids"]))
            query_predicted.append(set(predicted_ids))
            if "answer_state" in expected:
                query_state_total += 1
                query_state_correct += int(prediction.get("answer_state") == expected["answer_state"])
        elif task == "claim_support":
            support_status = prediction.get("support_status")
            if not isinstance(support_status, str) or not support_status:
                raise ValueError(f"prediction {case_id} support_status must be string")
            claim_total += 1
            claim_correct += int(support_status == expected["support_status"])
        elif task == "reopen_unread":
            reopen_rows.append(reopen_diagnostics(case, prediction))
        if prediction == expected:
            exact_case_matches += 1

    evaluated = len(cases) - len(missing)
    return {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "dataset_type": manifest["dataset_type"],
        "case_denominator": len(cases),
        "evaluated_cases": evaluated,
        "missing_prediction_count": len(missing),
        "missing_prediction_ids": missing,
        "exact_case_match_count": exact_case_matches,
        "exact_case_match_rate": safe_ratio(exact_case_matches, evaluated),
        "event_pair": binary_metrics(event_pairs),
        "material_change": binary_metrics(change_pairs),
        "query_ids": set_metrics(query_expected, query_predicted),
        "query_answer_state": {
            "denominator": query_state_total,
            "correct": query_state_correct,
            "accuracy": safe_ratio(query_state_correct, query_state_total),
        },
        "claim_support": {
            "denominator": claim_total,
            "correct": claim_correct,
            "accuracy": safe_ratio(claim_correct, claim_total),
        },
        "reopen_unread": aggregate_reopen(reopen_rows),
        "reopen_unread_rows": reopen_rows,
        "targets": manifest.get("targets"),
        "not_run": not_run_arms(manifest),
    }


def perfect_predictions(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {case["case_id"]: dict(case["expected"]) for case in cases}


def self_check() -> None:
    manifest, cases = load_gold()
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

    wrong_state = perfect_predictions(cases)
    wrong_state["query-zero-failed-001"] = {"ids": []}
    degraded_state = evaluate(manifest, cases, wrong_state)
    assert degraded_state["query_ids"]["f1"] == 1.0
    assert degraded_state["query_answer_state"]["accuracy"] == 0.0
    print(f"GOLD_EVAL_SELF_CHECK_OK dataset={manifest['dataset_id']} cases={len(cases)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        return 0
    if not args.predictions:
        raise SystemExit("--predictions is required unless --self-check is used")
    manifest, cases = load_gold(args.manifest)
    predictions = index_predictions(read_jsonl(args.predictions))
    report = evaluate(manifest, cases, predictions)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if report["missing_prediction_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
