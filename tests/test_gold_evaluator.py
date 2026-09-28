import importlib.util
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


if __name__ == "__main__":
    unittest.main()
