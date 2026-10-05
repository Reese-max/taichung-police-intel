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

# Reopen defects, each counted on its own so one number never hides another
# (issue #108 acceptance). A prompted update is attributed by what it *is*, not
# only by the decision the station recorded: a policy that promotes a layout-only
# republication still labels the prompt PROMPTED.
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
# Reasons that mean the update was suppressed as a duplicate of something already
# known. Missing an expected update for one of these is a wrongly deduped
# substantive correction, not a coverage gap.
DEDUPE_DECISIONS = ("FORMAT_ONLY", "REPOST_MIRROR", "DUPLICATE_FETCH")
GAP_REASONS = ("CONNECTION_FAILURE", "PARTIAL_COVERAGE", "SOURCE_ITEM_MISSING")
REPORTED_ALREADY_READ = "ALREADY_READ"
SPLIT_VALUES = {"dev", "holdout"}
CONFIRMED_DISCOVERY_STATUS = "VERIFIED_OFFICIAL"
# Query answer states whose truthful outcome is "there is no supported answer".
# Returning result IDs for one of these is false reassurance (issue #33).
NO_ANSWER_STATES = {"NO_RESULT", "SOURCE_GAP", "SOURCE_FAILED", "NO_ANSWER", "UNANSWERABLE"}
# Report sections whose "rate" improves as it falls — they count defects.
LOWER_IS_BETTER_RATE_METRICS = {"unsupported_claim_rate", "no_result_false_reassurance"}
COMPARED_FIELD_SUFFIXES = ("precision", "recall", "f1", "accuracy", "coverage", "mrr", "rate")
# Report sections excluded from baseline metric deltas.
COMPARISON_EXCLUDED_SECTIONS = {"targets", "baseline_comparison", "case_results", "not_run", "reopen_unread_rows"}


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
    validate_cases(cases, manifest)
    return manifest, cases


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


def validate_cases(cases: list[dict[str, Any]], manifest: dict[str, Any] | None = None) -> None:
    ids: set[str] = set()
    supported = {
        "event_pair",
        "material_change",
        "query_ids",
        "claim_support",
        "reopen_unread",
        "discovery_verification",
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
    query_exact_total = query_exact_correct = 0
    query_reciprocal_ranks: list[float] = []
    claim_total = claim_correct = 0
    predicted_supported = unsupported_claims = 0
    canonical_total = canonical_correct = 0
    entity_expected: list[set[str]] = []
    entity_predicted: list[set[str]] = []
    field_expected: list[set[str]] = []
    field_predicted: list[set[str]] = []
    evidence_case_total = evidence_full_cases = 0
    evidence_expected_total = evidence_covered_total = 0
    no_answer_total = false_reassurance = 0
    trust_total = trust_correct = 0
    discovery_pairs: list[tuple[bool, bool]] = []
    discovery_status_total = discovery_status_correct = 0
    discovery_match_expected: list[set[str]] = []
    discovery_match_predicted: list[set[str]] = []
    reopen_rows: list[dict[str, Any]] = []
    missing: list[str] = []
    case_results: list[dict[str, Any]] = []
    exact_ids: set[str] = set()
    exact_case_matches = 0

    for case in cases:
        case_id = case["case_id"]
        task = case["task"]
        prediction = predictions.get(case_id)
        if prediction is None:
            missing.append(case_id)
            case_results.append({"case_id": case_id, "task": task, "status": "MISSING"})
            continue
        expected = case["expected"]
        if "trust_tier" in expected:
            trust_total += 1
            trust_correct += int(prediction.get("trust_tier") == expected["trust_tier"])
        if "entity_ids" in expected:
            entity_expected.append(set(expected["entity_ids"]))
            entity_predicted.append(_optional_string_set(prediction, "entity_ids", case_id))
        if "evidence_ids" in expected:
            expected_evidence = set(expected["evidence_ids"])
            covered = expected_evidence & _optional_string_set(prediction, "evidence_ids", case_id)
            evidence_case_total += 1
            evidence_expected_total += len(expected_evidence)
            evidence_covered_total += len(covered)
            evidence_full_cases += int(covered == expected_evidence)
        if task == "event_pair":
            event_pairs.append((expected["same_event"], require_bool(prediction, "same_event", case_id)))
            if "canonical_event_id" in expected:
                canonical_total += 1
                canonical_correct += int(prediction.get("canonical_event_id") == expected["canonical_event_id"])
        elif task == "material_change":
            change_pairs.append((expected["material_change"], require_bool(prediction, "material_change", case_id)))
            if "fields" in expected:
                field_expected.append(set(expected["fields"]))
                field_predicted.append(_optional_string_set(prediction, "fields", case_id))
        elif task == "query_ids":
            predicted_ids = require_string_list(prediction, "ids", case_id)
            query_expected.append(set(expected["ids"]))
            query_predicted.append(set(predicted_ids))
            query_exact_total += 1
            query_exact_correct += int(set(predicted_ids) == set(expected["ids"]))
            ranked = prediction.get("ranked_ids")
            if ranked is not None:
                ranked_ids = require_string_list(prediction, "ranked_ids", case_id)
                expected_id_set = set(expected["ids"])
                reciprocal = 0.0
                for index, item in enumerate(ranked_ids):
                    if item in expected_id_set:
                        reciprocal = 1.0 / (index + 1)
                        break
                query_reciprocal_ranks.append(reciprocal)
            if "answer_state" in expected:
                query_state_total += 1
                query_state_correct += int(prediction.get("answer_state") == expected["answer_state"])
                if expected["answer_state"] in NO_ANSWER_STATES:
                    no_answer_total += 1
                    false_reassurance += int(len(predicted_ids) > 0)
        elif task == "claim_support":
            support_status = prediction.get("support_status")
            if not isinstance(support_status, str) or not support_status:
                raise ValueError(f"prediction {case_id} support_status must be string")
            claim_total += 1
            claim_correct += int(support_status == expected["support_status"])
            if support_status == "SUPPORTED":
                predicted_supported += 1
                unsupported_claims += int(expected["support_status"] != "SUPPORTED")
        elif task == "discovery_verification":
            predicted_status = prediction.get("verification_status")
            if not isinstance(predicted_status, str) or not predicted_status:
                raise ValueError(f"prediction {case_id} verification_status must be string")
            discovery_pairs.append((
                expected["verification_status"] == CONFIRMED_DISCOVERY_STATUS,
                predicted_status == CONFIRMED_DISCOVERY_STATUS,
            ))
            discovery_status_total += 1
            discovery_status_correct += int(predicted_status == expected["verification_status"])
            if "official_match_ids" in expected:
                discovery_match_expected.append(set(expected["official_match_ids"]))
                discovery_match_predicted.append(_optional_string_set(prediction, "official_match_ids", case_id))
        elif task == "reopen_unread":
            reopen_rows.append(reopen_diagnostics(case, prediction))
        if task == "reopen_unread":
            # A reopen prediction legitimately carries per-update diagnostics the
            # gold never enumerates, so compare the fields the gold declares.
            exact = all(prediction.get(key) == value for key, value in expected.items())
        else:
            exact = prediction == expected
        if exact:
            exact_ids.add(case_id)
            exact_case_matches += 1
        case_results.append({"case_id": case_id, "task": task, "status": "EXACT" if exact else "DIFF"})

    evaluated = len(cases) - len(missing)
    event_metric = binary_metrics(event_pairs)
    # Named per issue #33: an FP is a false merge, an FN is a missed merge.
    event_metric["false_merge_count"] = event_metric["fp"]
    event_metric["missed_merge_count"] = event_metric["fn"]
    reopen_metric = aggregate_reopen(reopen_rows)
    return {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "dataset_type": manifest["dataset_type"],
        "evaluation_scope": manifest.get("evaluation_scope", manifest["dataset_type"]),
        "case_denominator": len(cases),
        "evaluated_cases": evaluated,
        "evaluation_status": "INCOMPLETE_MISSING_PREDICTIONS" if missing else (
            "COMPLETE" if cases else "NOT_RUN_NO_CASES"
        ),
        "missing_prediction_count": len(missing),
        "missing_prediction_ids": missing,
        "exact_case_match_count": exact_case_matches,
        "exact_case_match_rate": safe_ratio(exact_case_matches, len(cases)),
        "evaluated_exact_case_match_rate": safe_ratio(exact_case_matches, evaluated),
        "split_metrics": split_metrics(manifest, cases, missing, exact_ids),
        "case_results": case_results,
        "event_pair": event_metric,
        "material_change": binary_metrics(change_pairs),
        "material_change_fields": set_metrics(field_expected, field_predicted),
        "canonical_event_id": {
            "denominator": canonical_total,
            "correct": canonical_correct,
            "accuracy": safe_ratio(canonical_correct, canonical_total),
        },
        "entity_ids": set_metrics(entity_expected, entity_predicted),
        "query_ids": set_metrics(query_expected, query_predicted),
        "query_exact_id_accuracy": {
            "denominator": query_exact_total,
            "correct": query_exact_correct,
            "accuracy": safe_ratio(query_exact_correct, query_exact_total),
        },
        "query_ranking": {
            "case_denominator": query_exact_total,
            "ranked_denominator": len(query_reciprocal_ranks),
            "mrr": (sum(query_reciprocal_ranks) / len(query_reciprocal_ranks)) if query_reciprocal_ranks else None,
        },
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
        "unsupported_claim_rate": {
            "predicted_supported_denominator": predicted_supported,
            "unsupported_claim_count": unsupported_claims,
            "rate": safe_ratio(unsupported_claims, predicted_supported),
        },
        "evidence_coverage": {
            "case_denominator": evidence_case_total,
            "expected_id_denominator": evidence_expected_total,
            "covered_id_count": evidence_covered_total,
            "coverage": safe_ratio(evidence_covered_total, evidence_expected_total),
            "full_coverage_case_count": evidence_full_cases,
            "full_coverage_case_rate": safe_ratio(evidence_full_cases, evidence_case_total),
        },
        "no_result_false_reassurance": {
            "denominator": no_answer_total,
            "false_reassurance_count": false_reassurance,
            "rate": safe_ratio(false_reassurance, no_answer_total),
        },
        "trust_tier": {
            "denominator": trust_total,
            "correct": trust_correct,
            "accuracy": safe_ratio(trust_correct, trust_total),
        },
        "discovery_verification": {
            **binary_metrics(discovery_pairs),
            "confirmed_status": CONFIRMED_DISCOVERY_STATUS,
            "status_accuracy": {
                "denominator": discovery_status_total,
                "correct": discovery_status_correct,
                "accuracy": safe_ratio(discovery_status_correct, discovery_status_total),
            },
        },
        "discovery_match_ids": set_metrics(discovery_match_expected, discovery_match_predicted),
        "reopen_unread": reopen_metric,
        "reopen_unread_rows": reopen_rows,
        "targets": manifest.get("targets"),
        "target_assessment": target_assessment(manifest, reopen_metric, missing),
        "not_run": not_run_arms(manifest),
    }


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


def compare_reports(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
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


def perfect_predictions(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {case["case_id"]: dict(case["expected"]) for case in cases}


def self_check() -> None:
    manifest, cases = load_gold()
    perfect = evaluate(manifest, cases, perfect_predictions(cases))
    assert perfect["missing_prediction_count"] == 0
    assert perfect["exact_case_match_rate"] == 1.0
    assert perfect["event_pair"]["f1"] == 1.0
    assert perfect["event_pair"]["false_merge_count"] == 0
    assert perfect["event_pair"]["missed_merge_count"] == 0
    assert perfect["material_change"]["f1"] == 1.0
    assert perfect["query_ids"]["f1"] == 1.0
    assert perfect["query_answer_state"]["accuracy"] == 1.0
    assert perfect["claim_support"]["accuracy"] == 1.0
    assert perfect["discovery_verification"]["f1"] == 1.0
    assert perfect["canonical_event_id"]["accuracy"] == 1.0
    assert perfect["entity_ids"]["f1"] == 1.0
    assert perfect["material_change_fields"]["f1"] == 1.0
    assert perfect["unsupported_claim_rate"]["rate"] == 0.0
    assert perfect["evidence_coverage"]["coverage"] == 1.0
    assert perfect["no_result_false_reassurance"]["rate"] == 0.0
    assert perfect["trust_tier"]["accuracy"] == 1.0
    for split in manifest.get("splits", []):
        assert perfect["split_metrics"][split]["case_denominator"] >= 1

    wrong = perfect_predictions(cases)
    wrong["event-different-date-001"] = {"same_event": True}
    degraded = evaluate(manifest, cases, wrong)
    assert degraded["event_pair"]["fp"] == 1
    assert degraded["event_pair"]["false_merge_count"] == 1
    assert degraded["event_pair"]["f1"] < 1.0

    wrong_state = perfect_predictions(cases)
    wrong_state["query-zero-failed-001"] = {"ids": []}
    degraded_state = evaluate(manifest, cases, wrong_state)
    assert degraded_state["query_ids"]["f1"] == 1.0
    assert degraded_state["query_answer_state"]["accuracy"] == 0.0

    identical = compare_reports(perfect, perfect)
    assert identical["gate"]["status"] == "PASS"
    regressed = compare_reports(degraded, perfect)
    assert regressed["regressions_found"] is True
    assert regressed["gate"]["status"] == "FAIL"
    assert "event-different-date-001" in regressed["case_regressions"]
    print(f"GOLD_EVAL_SELF_CHECK_OK dataset={manifest['dataset_id']} cases={len(cases)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--predictions", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--fail-on-regression", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        return 0
    if not args.predictions:
        raise SystemExit("--predictions is required unless --self-check is used")
    if args.fail_on_regression and not args.baseline:
        raise SystemExit("--fail-on-regression requires --baseline")
    manifest, cases = load_gold(args.manifest)
    predictions = index_predictions(read_jsonl(args.predictions))
    report = evaluate(manifest, cases, predictions)
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        report["baseline_comparison"] = compare_reports(report, baseline)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.fail_on_regression and report["baseline_comparison"]["gate"]["status"] == "FAIL":
        return 3
    return 0 if report["missing_prediction_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
