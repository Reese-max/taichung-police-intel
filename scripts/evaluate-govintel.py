#!/usr/bin/env python3
"""Versioned regression evaluator for GovIntel gold cases.

This harness reports engineering metrics with explicit denominators.  A synthetic
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
        if not isinstance(case.get("expected"), dict):
            raise ValueError(f"expected object missing: {case_id}")


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


def evaluate(manifest: dict[str, Any], cases: list[dict[str, Any]], predictions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    case_ids = {case["case_id"] for case in cases}
    unknown_predictions = sorted(set(predictions) - case_ids)
    if unknown_predictions:
        raise ValueError("predictions contain unknown case IDs: " + ", ".join(unknown_predictions))

    event_pairs: list[tuple[bool, bool]] = []
    change_pairs: list[tuple[bool, bool]] = []
    query_expected: list[set[str]] = []
    query_predicted: list[set[str]] = []
    claim_total = claim_correct = 0
    missing: list[str] = []
    exact_case_matches = 0

    for case in cases:
        prediction = predictions.get(case["case_id"])
        if prediction is None:
            missing.append(case["case_id"])
            continue
        expected = case["expected"]
        task = case["task"]
        if task == "event_pair":
            event_pairs.append((bool(expected.get("same_event")), bool(prediction.get("same_event"))))
        elif task == "material_change":
            change_pairs.append((bool(expected.get("material_change")), bool(prediction.get("material_change"))))
        elif task == "query_ids":
            query_expected.append(set(expected.get("ids") or []))
            query_predicted.append(set(prediction.get("ids") or []))
        elif task == "claim_support":
            claim_total += 1
            claim_correct += int(prediction.get("support_status") == expected.get("support_status"))
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
        "claim_support": {
            "denominator": claim_total,
            "correct": claim_correct,
            "accuracy": safe_ratio(claim_correct, claim_total),
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
    assert perfect["claim_support"]["accuracy"] == 1.0

    wrong = perfect_predictions(cases)
    wrong["event-different-date-001"] = {"same_event": True}
    degraded = evaluate(manifest, cases, wrong)
    assert degraded["event_pair"]["fp"] == 1
    assert degraded["event_pair"]["f1"] < 1.0
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
