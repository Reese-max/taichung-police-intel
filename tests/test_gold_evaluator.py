import importlib.util
import json
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

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


class RegressionHarnessTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.cases = ev.load_gold()
        self.perfect = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))

    def test_manifest_declares_splits_and_every_case_is_split(self):
        self.assertEqual(sorted(self.manifest.get("splits", [])), ["dev", "holdout"])
        self.assertTrue(all(case.get("split") in {"dev", "holdout"} for case in self.cases))
        for split in ("dev", "holdout"):
            self.assertGreaterEqual(self.perfect["split_metrics"][split]["case_denominator"], 1)
            self.assertIn("exact_case_match_rate", self.perfect["split_metrics"][split])

    def test_case_missing_split_is_rejected_when_manifest_declares_splits(self):
        case = {
            "case_id": "unsplit-1",
            "task": "event_pair",
            "synthetic": True,
            "input": {},
            "expected": {"same_event": True},
        }
        with self.assertRaisesRegex(ValueError, "split"):
            ev.validate_cases([case], self.manifest)

    def test_invalid_split_value_is_rejected(self):
        case = {
            "case_id": "bad-split-1",
            "task": "event_pair",
            "synthetic": True,
            "split": "train",
            "input": {},
            "expected": {"same_event": True},
        }
        with self.assertRaisesRegex(ValueError, "split"):
            ev.validate_cases([case], self.manifest)

    def test_required_fixture_kinds_are_present(self):
        tags = {tag for case in self.cases for tag in case.get("tags", [])}
        for required in ("media-only", "source-failure", "official-conflict", "body-only", "stale"):
            self.assertIn(required, tags, f"missing fixture kind: {required}")
        false_merge_fixtures = [
            case for case in self.cases
            if case["task"] == "event_pair" and case["expected"]["same_event"] is False
        ]
        self.assertGreaterEqual(len(false_merge_fixtures), 1)

    def test_discovery_verification_is_scored_with_denominators(self):
        discovery = [case for case in self.cases if case["task"] == "discovery_verification"]
        self.assertGreaterEqual(len(discovery), 4)
        metric = self.perfect["discovery_verification"]
        self.assertEqual(metric["denominator"], len(discovery))
        self.assertEqual(metric["f1"], 1.0)
        self.assertEqual(metric["status_accuracy"]["accuracy"], 1.0)
        wrong = ev.perfect_predictions(self.cases)
        confirmed_case = next(
            case for case in discovery
            if case["expected"]["verification_status"] == "VERIFIED_OFFICIAL"
        )
        wrong[confirmed_case["case_id"]] = {"verification_status": "NO_OFFICIAL_MATCH"}
        report = ev.evaluate(self.manifest, self.cases, wrong)
        self.assertEqual(report["discovery_verification"]["fn"], 1)
        self.assertLess(report["discovery_verification"]["recall"], 1.0)

    def test_discovery_verification_requires_status_string(self):
        case = {
            "case_id": "discovery-bad-1",
            "task": "discovery_verification",
            "synthetic": True,
            "split": "dev",
            "input": {},
            "expected": {"confirmed": True},
        }
        with self.assertRaisesRegex(ValueError, "verification_status"):
            ev.validate_cases([case], self.manifest)

    def test_material_change_field_level_metrics(self):
        self.assertEqual(self.perfect["material_change_fields"]["f1"], 1.0)
        wrong = ev.perfect_predictions(self.cases)
        wrong["change-time-001"] = {"material_change": True, "fields": ["wrong_field"]}
        report = ev.evaluate(self.manifest, self.cases, wrong)
        self.assertLess(report["material_change_fields"]["precision"], 1.0)
        self.assertGreaterEqual(report["material_change_fields"]["expected_id_denominator"], 1)

    def test_canonical_event_id_and_entity_ids_are_scored(self):
        self.assertEqual(self.perfect["canonical_event_id"]["accuracy"], 1.0)
        self.assertEqual(self.perfect["entity_ids"]["f1"], 1.0)
        wrong = ev.perfect_predictions(self.cases)
        wrong["event-same-001"] = {
            "same_event": True,
            "canonical_event_id": "EVT-WRONG",
            "entity_ids": [],
        }
        report = ev.evaluate(self.manifest, self.cases, wrong)
        self.assertLess(report["canonical_event_id"]["accuracy"], 1.0)
        self.assertGreaterEqual(report["entity_ids"]["fn"], 1)

    def test_unsupported_claim_rate_counts_predicted_supported_mismatch(self):
        self.assertEqual(self.perfect["unsupported_claim_rate"]["rate"], 0.0)
        wrong = ev.perfect_predictions(self.cases)
        wrong["claim-cause-unsupported-001"] = {"support_status": "SUPPORTED"}
        report = ev.evaluate(self.manifest, self.cases, wrong)
        self.assertEqual(report["unsupported_claim_rate"]["unsupported_claim_count"], 1)
        self.assertGreater(report["unsupported_claim_rate"]["rate"], 0.0)
        self.assertGreaterEqual(report["unsupported_claim_rate"]["predicted_supported_denominator"], 1)

    def test_evidence_coverage_reports_denominators(self):
        self.assertEqual(self.perfect["evidence_coverage"]["coverage"], 1.0)
        self.assertGreaterEqual(self.perfect["evidence_coverage"]["expected_id_denominator"], 1)
        wrong = ev.perfect_predictions(self.cases)
        wrong["claim-time-supported-001"] = {"support_status": "SUPPORTED", "evidence_ids": []}
        report = ev.evaluate(self.manifest, self.cases, wrong)
        self.assertLess(report["evidence_coverage"]["coverage"], 1.0)

    def test_no_result_false_reassurance_is_counted(self):
        self.assertEqual(self.perfect["no_result_false_reassurance"]["rate"], 0.0)
        wrong = ev.perfect_predictions(self.cases)
        wrong["query-zero-failed-001"] = {"ids": ["EVENT-1"], "answer_state": "OK"}
        report = ev.evaluate(self.manifest, self.cases, wrong)
        self.assertEqual(report["no_result_false_reassurance"]["false_reassurance_count"], 1)
        self.assertEqual(report["no_result_false_reassurance"]["rate"], 1.0)

    def test_query_ranking_scores_ranked_ids(self):
        wrong = ev.perfect_predictions(self.cases)
        wrong["query-source-001"] = {
            "ids": ["FEED-A", "FEED-C"],
            "ranked_ids": ["DECOY", "FEED-A", "FEED-C"],
        }
        report = ev.evaluate(self.manifest, self.cases, wrong)
        metric = report["query_ranking"]
        self.assertEqual(metric["case_denominator"], 3)
        self.assertEqual(metric["ranked_denominator"], 1)
        self.assertEqual(metric["mrr"], 0.5)

    def test_query_exact_id_accuracy(self):
        self.assertEqual(self.perfect["query_exact_id_accuracy"]["accuracy"], 1.0)
        wrong = ev.perfect_predictions(self.cases)
        wrong["query-source-001"] = {"ids": ["FEED-A"]}
        report = ev.evaluate(self.manifest, self.cases, wrong)
        self.assertLess(report["query_exact_id_accuracy"]["accuracy"], 1.0)

    def test_trust_tier_accuracy(self):
        self.assertGreaterEqual(self.perfect["trust_tier"]["denominator"], 1)
        self.assertEqual(self.perfect["trust_tier"]["accuracy"], 1.0)

    def test_case_results_expose_every_case(self):
        results = {row["case_id"]: row for row in self.perfect["case_results"]}
        self.assertEqual(len(results), len(self.cases))
        self.assertTrue(all(row["status"] == "EXACT" for row in results.values()))

    def test_report_serialization_is_deterministic(self):
        predictions = ev.perfect_predictions(self.cases)
        first = json.dumps(ev.evaluate(self.manifest, self.cases, predictions), ensure_ascii=False, sort_keys=True)
        second = json.dumps(ev.evaluate(self.manifest, self.cases, predictions), ensure_ascii=False, sort_keys=True)
        self.assertEqual(first, second)

    def test_baseline_compare_flags_metric_and_case_regressions(self):
        degraded = ev.perfect_predictions(self.cases)
        degraded["event-different-date-001"] = {"same_event": True}
        current = ev.evaluate(self.manifest, self.cases, degraded)
        comparison = ev.compare_reports(current, self.perfect)
        self.assertTrue(comparison["regressions_found"])
        self.assertIn("event-different-date-001", comparison["case_regressions"])
        regressed_metrics = {
            row["metric"] for row in comparison["metric_deltas"] if row["direction"] == "REGRESSED"
        }
        self.assertIn("event_pair", regressed_metrics)
        self.assertEqual(comparison["gate"]["status"], "FAIL")
        self.assertTrue(comparison["gate"]["failures"])

    def test_baseline_compare_passes_identical_reports(self):
        comparison = ev.compare_reports(self.perfect, self.perfect)
        self.assertFalse(comparison["regressions_found"])
        self.assertEqual(comparison["gate"]["status"], "PASS")
        self.assertEqual(comparison["gate"]["failures"], [])

    def test_baseline_compare_treats_dropped_case_as_regression(self):
        subset_cases = self.cases[1:]
        dropped_id = self.cases[0]["case_id"]
        subset_predictions = ev.perfect_predictions(subset_cases)
        current = ev.evaluate(self.manifest, subset_cases, subset_predictions)
        comparison = ev.compare_reports(current, self.perfect)
        self.assertTrue(comparison["regressions_found"])
        self.assertIn(dropped_id, comparison["dropped_cases"])
        self.assertEqual(comparison["gate"]["status"], "FAIL")

    def test_fail_on_regression_cli_exit_code(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            degraded = ev.perfect_predictions(self.cases)
            degraded["event-different-date-001"] = {"same_event": True}
            predictions_path = tmp / "predictions.jsonl"
            predictions_path.write_text(
                "".join(
                    json.dumps({"case_id": key, "prediction": value}, ensure_ascii=False) + "\n"
                    for key, value in degraded.items()
                ),
                encoding="utf-8",
            )
            baseline_path = tmp / "baseline.json"
            baseline_path.write_text(json.dumps(self.perfect, ensure_ascii=False), encoding="utf-8")
            output_path = tmp / "report.json"
            argv = [
                "evaluate-govintel.py",
                "--predictions", str(predictions_path),
                "--baseline", str(baseline_path),
                "--output", str(output_path),
                "--fail-on-regression",
            ]
            with mock.patch.object(sys, "argv", argv):
                exit_code = ev.main()
            self.assertEqual(exit_code, 3)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(report["baseline_comparison"]["gate"]["status"], "FAIL")

    def test_label_version_requires_history(self):
        base_case = {
            "case_id": "historical-1",
            "task": "event_pair",
            "synthetic": False,
            "reviewer": "annotator-a",
            "split": "dev",
            "input": {},
            "expected": {"same_event": True},
        }
        relabelled = {
            **base_case,
            "label_version": 2,
            "history": [
                {"version": 1, "reviewer": "annotator-a", "note": "initial label"},
                {"version": 2, "reviewer": "annotator-b", "note": "relabelled after adjudication"},
            ],
        }
        ev.validate_cases([relabelled], self.manifest)
        with self.assertRaisesRegex(ValueError, "history"):
            ev.validate_cases([{**base_case, "label_version": 2}], self.manifest)
        bad_entry = {
            **base_case,
            "label_version": 2,
            "history": [{"version": 1, "note": "missing reviewer"}],
        }
        with self.assertRaisesRegex(ValueError, "reviewer"):
            ev.validate_cases([bad_entry], self.manifest)


if __name__ == "__main__":
    unittest.main()
