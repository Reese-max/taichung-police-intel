import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "evaluate-govintel.py"
spec = importlib.util.spec_from_file_location("govintel_eval", SCRIPT)
ev = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(ev)


class GoldEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.cases = ev.load_gold()

    def test_seed_manifest_and_cases_validate(self):
        self.assertEqual(self.manifest["dataset_type"], "SYNTHETIC_REGRESSION_SEED")
        self.assertGreaterEqual(len(self.cases), 12)
        self.assertTrue(all(case.get("synthetic") is True for case in self.cases))

    def test_perfect_predictions_score_one_without_hiding_denominators(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        self.assertEqual(report["case_denominator"], len(self.cases))
        self.assertEqual(report["evaluated_cases"], len(self.cases))
        self.assertEqual(report["schema_version"], 2)
        self.assertEqual(len(report["dataset_sha256"]), 64)
        self.assertEqual(report["exact_case_match_rate"], 1.0)
        self.assertEqual(report["event_pair"]["f1"], 1.0)
        self.assertEqual(report["material_change"]["f1"], 1.0)
        self.assertEqual(report["query_ids"]["f1"], 1.0)
        self.assertEqual(report["query_answer_state"]["accuracy"], 1.0)
        self.assertEqual(report["claim_support"]["accuracy"], 1.0)

    def test_false_merge_changes_event_precision(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["event-different-date-001"] = {"same_event": True}
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["event_pair"]["fp"], 1)
        self.assertLess(report["event_pair"]["precision"], 1.0)

    def test_missed_material_change_changes_recall(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["change-time-001"] = {"material_change": False, "fields": []}
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["material_change"]["fn"], 1)
        self.assertLess(report["material_change"]["recall"], 1.0)

    def test_query_metric_counts_extra_and_missing_ids(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["query-source-001"] = {"ids": ["FEED-A", "WRONG"]}
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertGreaterEqual(report["query_ids"]["fp"], 1)
        self.assertGreaterEqual(report["query_ids"]["fn"], 1)

    def test_source_gap_answer_state_is_scored_separately_from_empty_ids(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["query-zero-failed-001"] = {"ids": []}
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["query_ids"]["f1"], 1.0)
        self.assertEqual(report["query_answer_state"]["denominator"], 1)
        self.assertEqual(report["query_answer_state"]["accuracy"], 0.0)

    def test_binary_prediction_must_be_actual_boolean(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["event-different-date-001"] = {"same_event": "false"}
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            ev.evaluate(self.manifest, self.cases, predictions)
        predictions = ev.perfect_predictions(self.cases)
        predictions["change-time-001"] = {"fields": ["start_time"]}
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            ev.evaluate(self.manifest, self.cases, predictions)

    def test_missing_predictions_are_explicit(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions.pop(self.cases[0]["case_id"])
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["missing_prediction_count"], 1)
        self.assertEqual(len(report["missing_prediction_ids"]), 1)

    def test_unknown_prediction_id_fails_closed(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["not-in-gold"] = {"same_event": True}
        with self.assertRaisesRegex(ValueError, "unknown case IDs"):
            ev.evaluate(self.manifest, self.cases, predictions)

    def test_non_synthetic_case_requires_reviewer(self):
        case = {
            "case_id": "historical-1",
            "task": "event_pair",
            "synthetic": False,
            "input": {},
            "expected": {"same_event": True},
        }
        with self.assertRaisesRegex(ValueError, "reviewer"):
            ev.validate_cases([case])

    def test_same_report_has_deterministic_no_regression_deltas(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        first = ev.compare_reports(report, report)
        second = ev.compare_reports(report, report)
        self.assertEqual(first, second)
        self.assertEqual(first["regression_status"], "NO_REGRESSION")
        self.assertEqual(first["regressed_metrics"], [])
        self.assertEqual(first["improved_metrics"], [])
        self.assertTrue(all(row["delta"] == 0 for row in first["score_deltas"].values()))

    def test_undefined_precision_is_reported_as_unscorable(self):
        predictions = ev.perfect_predictions(self.cases)
        for case in self.cases:
            if case["task"] == "query_ids":
                predictions[case["case_id"]] = {"ids": []}
                if "answer_state" in case["expected"]:
                    predictions[case["case_id"]]["answer_state"] = case["expected"]["answer_state"]
        report = ev.evaluate(self.manifest, self.cases, predictions)
        comparison = ev.compare_reports(report, report)
        self.assertEqual(comparison["regression_status"], "UNSCORABLE")
        self.assertIn("query_ids.precision", comparison["unscorable_metrics"])
        self.assertIsNone(comparison["score_deltas"]["query_ids.precision"]["delta"])

    def test_comparison_marks_regression_and_allows_prediction_count_to_change(self):
        baseline = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        predictions = ev.perfect_predictions(self.cases)
        predictions["query-source-001"] = {"ids": ["FEED-A", "FEED-C", "EXTRA"]}
        current = ev.evaluate(self.manifest, self.cases, predictions)

        comparison = ev.compare_reports(current, baseline)
        self.assertEqual(comparison["regression_status"], "REGRESSION")
        self.assertLess(comparison["score_deltas"]["query_ids.precision"]["delta"], 0)
        self.assertEqual(comparison["count_deltas"]["query_ids.predicted_id_denominator"]["delta"], 1)
        self.assertIn("query_ids.precision", comparison["regressed_metrics"])

    def test_comparison_rejects_schema_dataset_and_case_denominator_mismatches(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        mismatches = []
        wrong_schema = json.loads(json.dumps(report))
        wrong_schema["schema_version"] = 1
        mismatches.append(wrong_schema)
        wrong_metric_schema = json.loads(json.dumps(report))
        wrong_metric_schema["event_pair"]["extra_metric"] = 1.0
        mismatches.append(wrong_metric_schema)
        wrong_dataset = json.loads(json.dumps(report))
        wrong_dataset["dataset_id"] = "other-dataset"
        mismatches.append(wrong_dataset)
        wrong_case_count = json.loads(json.dumps(report))
        wrong_case_count["case_denominator"] += 1
        wrong_case_count["evaluated_cases"] += 1
        mismatches.append(wrong_case_count)
        changed_cases = json.loads(json.dumps(self.cases))
        changed_cases[0]["input"]["left"]["title"] = "changed under the same dataset ID"
        changed_dataset = ev.evaluate(self.manifest, changed_cases, ev.perfect_predictions(changed_cases))
        self.assertEqual(changed_dataset["dataset_id"], report["dataset_id"])
        self.assertEqual(changed_dataset["case_denominator"], report["case_denominator"])
        mismatches.append(changed_dataset)

        for baseline in mismatches:
            with self.subTest(baseline=baseline):
                with self.assertRaises(ValueError):
                    ev.compare_reports(report, baseline)

    def test_comparison_rejects_task_denominators_that_shift_between_reports(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        baseline = json.loads(json.dumps(report))
        baseline["event_pair"]["denominator"] -= 1
        baseline["event_pair"]["tn"] -= 1
        baseline["material_change"]["denominator"] += 1
        baseline["material_change"]["tn"] += 1
        with self.assertRaisesRegex(ValueError, "denominators do not match"):
            ev.compare_reports(report, baseline)

    def test_comparison_rejects_partial_reports(self):
        full = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        predictions = ev.perfect_predictions(self.cases)
        predictions.pop("claim-stale-001")
        partial = ev.evaluate(self.manifest, self.cases, predictions)
        with self.assertRaisesRegex(ValueError, "complete, non-empty evaluation"):
            ev.compare_reports(partial, full)


if __name__ == "__main__":
    unittest.main()
