#!/usr/bin/env python3
"""Versioned regression evaluator for GovIntel gold cases.

This harness reports engineering metrics with explicit denominators. A synthetic
starter set validates metric plumbing only; it must never be presented as measured
production accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "eval/gold/v1/manifest.json"


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
    supported = {"event_pair", "material_change", "query_ids", "claim_support"}
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
        if prediction == expected:
            exact_case_matches += 1

    evaluated = len(cases) - len(missing)
    return {
        "schema_version": 2,
        "dataset_id": manifest["dataset_id"],
        "dataset_type": manifest["dataset_type"],
        "dataset_sha256": dataset_fingerprint(manifest, cases),
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
    }


def _report_int(report: dict[str, Any], field: str, label: str) -> int:
    value = report.get(field)
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}.{field} must be a non-negative integer")
    return value


def _require_exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} fields do not match evaluation report schema v2")


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


def validate_evaluation_report(report: dict[str, Any], label: str) -> dict[str, int]:
    """Validate a complete v2 report before it can be used as a baseline."""
    if not isinstance(report, dict):
        raise ValueError(f"{label} must be a JSON object")
    if type(report.get("schema_version")) is not int or report["schema_version"] != 2:
        raise ValueError(f"{label} has an unsupported schema_version")
    report_fields = {
        "schema_version", "dataset_id", "dataset_type", "dataset_sha256",
        "case_denominator", "evaluated_cases", "missing_prediction_count",
        "missing_prediction_ids", "exact_case_match_count", "exact_case_match_rate",
        "event_pair", "material_change", "query_ids", "query_answer_state", "claim_support",
    }
    if "baseline_comparison" in report:
        report_fields.add("baseline_comparison")
    _require_exact_keys(report, report_fields, label)
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

    denominators = {"case_denominator": case_denominator}
    for task in ("event_pair", "material_change"):
        metrics = report.get(task)
        if not isinstance(metrics, dict):
            raise ValueError(f"{label}.{task} must be an object")
        _require_exact_keys(
            metrics,
            {"denominator", "tp", "fp", "fn", "tn", "precision", "recall", "f1"},
            f"{label}.{task}",
        )
        denominator = _report_int(metrics, "denominator", f"{label}.{task}")
        counts = {name: _report_int(metrics, name, f"{label}.{task}") for name in ("tp", "fp", "fn", "tn")}
        if denominator == 0 or sum(counts.values()) != denominator:
            raise ValueError(f"{label}.{task} has an invalid denominator")
        _validate_rate(metrics, "precision", counts["tp"], counts["tp"] + counts["fp"], f"{label}.{task}")
        _validate_rate(metrics, "recall", counts["tp"], counts["tp"] + counts["fn"], f"{label}.{task}")
        precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else None
        recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else None
        expected_f1 = (
            2 * precision * recall / (precision + recall)
            if precision is not None and recall is not None and precision + recall
            else None
        )
        f1 = _report_number(metrics, "f1", f"{label}.{task}")
        if expected_f1 is None and f1 is None:
            pass
        elif expected_f1 is None or f1 is None or not math.isclose(f1, expected_f1, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"{label}.{task}.f1 does not match its counts")
        denominators[task] = denominator

    query = report.get("query_ids")
    if not isinstance(query, dict):
        raise ValueError(f"{label}.query_ids must be an object")
    _require_exact_keys(
        query,
        {
            "case_denominator", "expected_id_denominator", "predicted_id_denominator",
            "tp", "fp", "fn", "precision", "recall", "f1",
        },
        f"{label}.query_ids",
    )
    query_cases = _report_int(query, "case_denominator", f"{label}.query_ids")
    expected_ids = _report_int(query, "expected_id_denominator", f"{label}.query_ids")
    predicted_ids = _report_int(query, "predicted_id_denominator", f"{label}.query_ids")
    query_counts = {name: _report_int(query, name, f"{label}.query_ids") for name in ("tp", "fp", "fn")}
    if query_cases == 0 or expected_ids == 0:
        raise ValueError(f"{label}.query_ids has an empty denominator")
    if (
        query_counts["tp"] + query_counts["fn"] != expected_ids
        or query_counts["tp"] + query_counts["fp"] != predicted_ids
    ):
        raise ValueError(f"{label}.query_ids counts do not match its ID denominators")
    _validate_rate(query, "precision", query_counts["tp"], predicted_ids, f"{label}.query_ids")
    _validate_rate(query, "recall", query_counts["tp"], expected_ids, f"{label}.query_ids")
    precision = query_counts["tp"] / predicted_ids if predicted_ids else None
    recall = query_counts["tp"] / expected_ids
    expected_f1 = (
        2 * precision * recall / (precision + recall)
        if precision is not None and precision + recall
        else None
    )
    f1 = _report_number(query, "f1", f"{label}.query_ids")
    if expected_f1 is None and f1 is None:
        pass
    elif expected_f1 is None or f1 is None or not math.isclose(f1, expected_f1, rel_tol=1e-12, abs_tol=1e-12):
        raise ValueError(f"{label}.query_ids.f1 does not match its counts")
    denominators["query_ids_cases"] = query_cases
    denominators["query_ids_expected_ids"] = expected_ids

    for task in ("query_answer_state", "claim_support"):
        metrics = report.get(task)
        if not isinstance(metrics, dict):
            raise ValueError(f"{label}.{task} must be an object")
        _require_exact_keys(metrics, {"denominator", "correct", "accuracy"}, f"{label}.{task}")
        denominator = _report_int(metrics, "denominator", f"{label}.{task}")
        correct = _report_int(metrics, "correct", f"{label}.{task}")
        if denominator == 0 or correct > denominator:
            raise ValueError(f"{label}.{task} has an invalid denominator or count")
        _validate_rate(metrics, "accuracy", correct, denominator, f"{label}.{task}")
        denominators[task] = denominator

    task_case_total = sum(
        denominators[name]
        for name in ("event_pair", "material_change", "query_ids_cases", "claim_support")
    )
    if task_case_total != case_denominator:
        raise ValueError(f"{label} task denominators do not match case_denominator")

    return denominators


def compare_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
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
        ("exact_case_match_rate", ("exact_case_match_rate",)),
        ("event_pair.precision", ("event_pair", "precision")),
        ("event_pair.recall", ("event_pair", "recall")),
        ("event_pair.f1", ("event_pair", "f1")),
        ("material_change.precision", ("material_change", "precision")),
        ("material_change.recall", ("material_change", "recall")),
        ("material_change.f1", ("material_change", "f1")),
        ("query_ids.precision", ("query_ids", "precision")),
        ("query_ids.recall", ("query_ids", "recall")),
        ("query_ids.f1", ("query_ids", "f1")),
        ("query_answer_state.accuracy", ("query_answer_state", "accuracy")),
        ("claim_support.accuracy", ("claim_support", "accuracy")),
    )
    score_deltas: dict[str, dict[str, Any]] = {}
    regressed: list[str] = []
    improved: list[str] = []
    unchanged: list[str] = []
    unscorable: list[str] = []
    for name, path in score_paths:
        before = baseline
        after = current
        for part in path:
            before = before[part]
            after = after[part]
        delta = None if before is None or after is None else round(float(after) - float(before), 12)
        score_deltas[name] = {"baseline": before, "current": after, "delta": delta, "direction": "higher_is_better"}
        if delta is None:
            unscorable.append(name)
            continue
        if delta < 0:
            regressed.append(name)
        elif delta > 0:
            improved.append(name)
        else:
            unchanged.append(name)

    count_paths = (
        ("exact_case_match_count", ("exact_case_match_count",)),
        *(
            (f"{task}.{count}", (task, count))
            for task in ("event_pair", "material_change")
            for count in ("tp", "fp", "fn", "tn")
        ),
        *((f"query_ids.{count}", ("query_ids", count)) for count in ("tp", "fp", "fn", "predicted_id_denominator")),
        ("query_answer_state.correct", ("query_answer_state", "correct")),
        ("claim_support.correct", ("claim_support", "correct")),
    )
    count_deltas: dict[str, dict[str, int]] = {}
    for name, path in count_paths:
        before = baseline
        after = current
        for part in path:
            before = before[part]
            after = after[part]
        count_deltas[name] = {"baseline": before, "current": after, "delta": after - before}

    return {
        "schema_version": 1,
        "comparison_type": "GOLD_EVALUATION_BASELINE_COMPARISON",
        "dataset_id": current["dataset_id"],
        "dataset_type": current["dataset_type"],
        "case_denominator": current["case_denominator"],
        "regression_status": "REGRESSION" if regressed else "UNSCORABLE" if unscorable else "NO_REGRESSION",
        "interpretation": "NO_REGRESSION means no compared score decreased; it is not an accuracy or quality claim.",
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
        },
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
    comparison = compare_reports(degraded, perfect)
    assert comparison["regression_status"] == "REGRESSION"
    assert comparison["score_deltas"]["event_pair.precision"]["delta"] < 0
    assert "event_pair.precision" in comparison["regressed_metrics"]

    wrong_state = perfect_predictions(cases)
    wrong_state["query-zero-failed-001"] = {"ids": []}
    degraded_state = evaluate(manifest, cases, wrong_state)
    assert degraded_state["query_ids"]["f1"] == 1.0
    assert degraded_state["query_answer_state"]["accuracy"] == 0.0
    state_comparison = compare_reports(degraded_state, perfect)
    assert state_comparison["regression_status"] == "REGRESSION"
    assert state_comparison["score_deltas"]["query_answer_state.accuracy"]["delta"] == -1.0
    print(f"GOLD_EVAL_SELF_CHECK_OK dataset={manifest['dataset_id']} cases={len(cases)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-report", type=Path, help="compare this evaluation against a prior JSON report")
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
    if args.baseline_report:
        baseline = json.loads(args.baseline_report.read_text(encoding="utf-8"))
        report["baseline_comparison"] = compare_reports(report, baseline)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if report["missing_prediction_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
