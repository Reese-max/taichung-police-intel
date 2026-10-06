import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
        self.manifest, self.cases = ev.load_gold(ev.SYNTHETIC_V2_MANIFEST)
        self.by_id = {case["case_id"]: case for case in self.cases}

    def test_seed_manifest_and_synthetic_case_labels_validate(self):
        self.assertEqual(self.manifest["schema_version"], 2)
        self.assertEqual(self.manifest["dataset_type"], "SYNTHETIC_REGRESSION_SEED")
        self.assertFalse(self.manifest["label_policy"]["human_reviewed_holdout_included"])
        self.assertGreaterEqual(len(self.cases), 19)
        self.assertTrue(all(case.get("synthetic") is True for case in self.cases))

    def test_previous_v1_dataset_remains_loadable_and_separate(self):
        manifest, cases = ev.load_gold(ROOT / "eval" / "gold" / "v1" / "manifest.json")
        self.assertEqual(manifest["dataset_id"], "govintel-gold-v1")
        self.assertGreaterEqual(len(cases), 15)
        report = ev.evaluate(manifest, cases, ev.perfect_predictions(cases))
        self.assertEqual(report["dataset_type"], "SYNTHETIC_REGRESSION_SEED")
        self.assertEqual(report["schema_version"], 3)

    def test_perfect_synthetic_predictions_report_explicit_metric_denominators(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        self.assertEqual(report["case_denominator"], len(self.cases))
        self.assertEqual(report["evaluated_cases"], len(self.cases))
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(len(report["dataset_sha256"]), 64)
        self.assertEqual(report["exact_case_match_rate"], 1.0)
        self.assertEqual(report["event_pair"]["f1"], 1.0)
        self.assertEqual(report["event_pair"]["false_merge_rate"], 0.0)
        self.assertEqual(report["event_pair"]["missed_merge_rate"], 0.0)
        self.assertEqual(report["material_change"]["f1"], 1.0)
        self.assertEqual(report["material_change"]["changed_fields"]["recall"], 1.0)
        self.assertEqual(report["discovery_official"]["confirmation_precision"], 1.0)
        self.assertEqual(report["query_ids"]["f1"], 1.0)
        self.assertEqual(report["query_ids"]["exact_id_match_accuracy"], 1.0)
        self.assertEqual(report["query_ids"]["ranking"]["mean_reciprocal_rank"], 1.0)
        self.assertEqual(report["query_answer_state"]["accuracy"], 1.0)
        self.assertEqual(report["no_result_false_reassurance"]["rate"], 0.0)
        self.assertEqual(report["claim_support"]["accuracy"], 1.0)
        self.assertEqual(report["unsupported_claim_rate"]["rate"], 0.0)
        self.assertEqual(report["evidence_coverage"]["recall"], 1.0)
        self.assertEqual(report["evidence_coverage"]["case_coverage"], 1.0)

    def test_body_only_and_media_only_revisions_are_explicit_synthetic_fixtures(self):
        body = self.by_id["change-body-only-001"]
        media = self.by_id["change-media-only-001"]
        self.assertEqual(body["tags"], ["body-only", "material"])
        self.assertEqual(body["input"]["before"]["title"], body["input"]["after"]["title"])
        self.assertNotEqual(body["input"]["before"]["body"], body["input"]["after"]["body"])
        self.assertEqual(body["expected"]["fields"], ["body"])
        self.assertEqual(media["tags"], ["media-only", "material"])
        self.assertEqual(media["input"]["before"]["body"], media["input"]["after"]["body"])
        self.assertNotEqual(media["input"]["before"]["media"], media["input"]["after"]["media"])
        self.assertEqual(media["expected"]["fields"], ["media"])

    def test_false_merge_and_missed_merge_have_named_counts_and_rates(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["event-different-date-001"]["same_event"] = True
        predictions["event-same-001"]["same_event"] = False
        event = ev.evaluate(self.manifest, self.cases, predictions)["event_pair"]
        self.assertEqual(event["false_merge_count"], 1)
        self.assertEqual(event["false_merge_denominator"], 2)
        self.assertEqual(event["false_merge_rate"], 0.5)
        self.assertEqual(event["missed_merge_count"], 1)
        self.assertEqual(event["missed_merge_denominator"], 2)
        self.assertEqual(event["missed_merge_rate"], 0.5)

    def test_missed_material_change_changes_recall_and_changed_field_recall(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["change-time-001"] = {"material_change": False, "fields": []}
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["material_change"]["fn"], 1)
        self.assertLess(report["material_change"]["recall"], 1.0)
        self.assertLess(report["material_change"]["changed_fields"]["recall"], 1.0)

    def test_changed_field_false_alarm_is_reported_even_if_binary_change_label_is_right(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["change-punctuation-001"]["fields"] = ["body"]
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["material_change"]["fp"], 0)
        self.assertEqual(report["material_change"]["changed_fields"]["fp"], 1)
        self.assertLess(report["material_change"]["changed_fields"]["precision"], 1.0)

    def test_query_id_set_exactness_and_ranking_metrics_are_separate(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["query-source-001"]["ids"] = ["FEED-A", "WRONG"]
        predictions["query-source-001"]["ranked_ids"] = ["WRONG", "FEED-A"]
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertGreaterEqual(report["query_ids"]["fp"], 1)
        self.assertGreaterEqual(report["query_ids"]["fn"], 1)
        self.assertAlmostEqual(report["query_ids"]["exact_id_match_accuracy"], 2 / 3)
        self.assertEqual(report["query_ids"]["ranking"]["top1_accuracy"], 0.5)
        self.assertEqual(report["query_ids"]["ranking"]["mean_reciprocal_rank"], 0.75)

    def test_source_failure_empty_results_do_not_get_false_reassurance_credit(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["query-zero-failed-001"]["answer_state"] = "NO_RESULTS"
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["query_ids"]["f1"], 1.0)
        self.assertLess(report["query_answer_state"]["accuracy"], 1.0)
        self.assertEqual(report["no_result_false_reassurance"]["false_reassurance_count"], 1)
        self.assertEqual(report["no_result_false_reassurance"]["denominator"], 1)
        self.assertEqual(report["no_result_false_reassurance"]["rate"], 1.0)

    def test_unsupported_claim_rate_counts_confident_unsupported_claims(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["claim-cause-unsupported-001"]["support_status"] = "SUPPORTED"
        report = ev.evaluate(self.manifest, self.cases, predictions)
        self.assertEqual(report["unsupported_claim_rate"]["unsupported_claim_count"], 1)
        self.assertEqual(report["unsupported_claim_rate"]["denominator"], 2)
        self.assertEqual(report["unsupported_claim_rate"]["rate"], 0.5)

    def test_evidence_coverage_reports_id_and_case_denominators(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["claim-conflict-001"]["evidence_ids"] = ["synthetic-police-v1#body-1"]
        coverage = ev.evaluate(self.manifest, self.cases, predictions)["evidence_coverage"]
        self.assertEqual(coverage["case_denominator"], 4)
        self.assertEqual(coverage["covered_case_count"], 3)
        self.assertEqual(coverage["expected_id_denominator"], 5)
        self.assertEqual(coverage["tp"], 4)
        self.assertEqual(coverage["recall"], 0.8)
        self.assertEqual(coverage["case_coverage"], 0.75)

    def test_discovery_to_official_false_confirmation_reduces_precision(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["discovery-official-mismatch-001"]["confirmed"] = True
        metrics = ev.evaluate(self.manifest, self.cases, predictions)["discovery_official"]
        self.assertEqual(metrics["fp"], 1)
        self.assertEqual(metrics["confirmation_precision_denominator"], 2)
        self.assertEqual(metrics["confirmation_precision"], 0.5)

    def test_binary_and_changed_field_predictions_are_strictly_typed(self):
        predictions = ev.perfect_predictions(self.cases)
        predictions["event-different-date-001"]["same_event"] = "false"
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            ev.evaluate(self.manifest, self.cases, predictions)
        predictions = ev.perfect_predictions(self.cases)
        predictions["change-time-001"].pop("fields")
        with self.assertRaisesRegex(ValueError, "must be string array"):
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

    def test_non_synthetic_case_requires_human_reviewer(self):
        case = {
            "case_id": "historical-1",
            "task": "event_pair",
            "synthetic": False,
            "input": {},
            "expected": {"same_event": True},
        }
        with self.assertRaisesRegex(ValueError, "reviewer"):
            ev.validate_cases([case])

    def test_identical_complete_reports_compare_deterministically(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        first = ev.compare_reports(report, report)
        second = ev.compare_reports(report, report)
        self.assertEqual(first, second)
        self.assertEqual(first["regression_status"], "NO_REGRESSION")
        self.assertEqual(first["promotion_gate"]["status"], "NOT_ELIGIBLE")
        self.assertFalse(first["promotion_gate"]["eligible"])
        self.assertIn("SYNTHETIC_ONLY", first["promotion_gate"]["blockers"])
        self.assertEqual(first["regressed_metrics"], [])
        self.assertEqual(first["improved_metrics"], [])
        self.assertTrue(all(row["delta"] == 0 for row in first["score_deltas"].values()))

    def test_baseline_comparison_detects_regressions_for_lower_is_better_metrics(self):
        baseline = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        predictions = ev.perfect_predictions(self.cases)
        predictions["query-zero-failed-001"]["answer_state"] = "NO_RESULTS"
        predictions["claim-cause-unsupported-001"]["support_status"] = "SUPPORTED"
        current = ev.evaluate(self.manifest, self.cases, predictions)
        comparison = ev.compare_reports(current, baseline)
        self.assertEqual(comparison["regression_status"], "REGRESSION")
        self.assertEqual(comparison["promotion_gate"]["status"], "NOT_ELIGIBLE")
        self.assertIn("REGRESSION", comparison["promotion_gate"]["blockers"])
        self.assertEqual(comparison["score_deltas"]["no_result_false_reassurance.rate"]["direction"], "lower_is_better")
        self.assertGreater(comparison["score_deltas"]["no_result_false_reassurance.rate"]["delta"], 0)
        self.assertIn("unsupported_claim_rate.rate", comparison["regressed_metrics"])

    def test_undefined_metric_values_are_explicitly_unscorable(self):
        predictions = ev.perfect_predictions(self.cases)
        for case in self.cases:
            if case["task"] == "query_ids":
                predictions[case["case_id"]]["ids"] = []
                predictions[case["case_id"]]["ranked_ids"] = []
        report = ev.evaluate(self.manifest, self.cases, predictions)
        comparison = ev.compare_reports(report, report)
        self.assertEqual(comparison["regression_status"], "UNSCORABLE")
        self.assertIn("UNSCORABLE", comparison["promotion_gate"]["blockers"])
        self.assertIn("query_ids.precision", comparison["unscorable_metrics"])
        self.assertIsNone(comparison["score_deltas"]["query_ids.precision"]["delta"])

        degraded = ev.perfect_predictions(self.cases)
        degraded["event-same-001"]["same_event"] = False
        for case in self.cases:
            if case["task"] == "query_ids":
                degraded[case["case_id"]]["ids"] = []
                degraded[case["case_id"]]["ranked_ids"] = []
        mixed = ev.compare_reports(ev.evaluate(self.manifest, self.cases, degraded), report)
        self.assertEqual(mixed["regression_status"], "REGRESSION")
        self.assertTrue(mixed["unscorable_metrics"])
        self.assertIn("REGRESSION", mixed["promotion_gate"]["blockers"])

    def test_human_reviewed_promotion_gate_stays_undefined_without_policy(self):
        gate = ev.build_promotion_gate(
            "HUMAN_REVIEWED_HOLDOUT",
            case_denominator=10,
            evaluated_cases=10,
            missing_prediction_count=0,
            comparison_status="NO_REGRESSION",
        )
        self.assertEqual(gate["status"], "UNDEFINED")
        self.assertFalse(gate["eligible"])
        self.assertIn("PROMOTION_POLICY_UNCONFIGURED", gate["blockers"])

    def test_cli_require_promotion_gate_rejects_synthetic_and_incomplete_runs(self):
        self_check = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPT), "--self-check", "--require-promotion-gate"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(self_check.returncode, 0)
        self.assertIn("requires a prediction evaluation", self_check.stderr)

        with tempfile.TemporaryDirectory(prefix="govintel-promotion-gate-") as temp_dir:
            temp = Path(temp_dir)
            baseline_predictions = temp / "baseline-predictions.jsonl"
            baseline_report = temp / "baseline-report.json"
            current_predictions = temp / "current-predictions.jsonl"
            current_report = temp / "current-report.json"
            rows = [
                {"case_id": case["case_id"], "prediction": case["expected"]}
                for case in self.cases
            ]
            baseline_predictions.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
            )
            baseline_run = subprocess.run(
                [sys.executable, "-X", "utf8", str(SCRIPT), "--manifest", str(ev.SYNTHETIC_V2_MANIFEST), "--predictions", str(baseline_predictions), "--output", str(baseline_report)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(baseline_run.returncode, 0, baseline_run.stderr)

            current_predictions.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
            )
            no_regression = subprocess.run(
                [
                    sys.executable, "-X", "utf8", str(SCRIPT), "--manifest", str(ev.SYNTHETIC_V2_MANIFEST), "--predictions", str(current_predictions),
                    "--baseline-report", str(baseline_report), "--output", str(current_report),
                    "--require-promotion-gate",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(no_regression.returncode, 3, no_regression.stderr)
            no_regression_report = json.loads(current_report.read_text(encoding="utf-8"))
            self.assertEqual(no_regression_report["baseline_comparison"]["regression_status"], "NO_REGRESSION")
            self.assertEqual(no_regression_report["promotion_gate"]["status"], "NOT_ELIGIBLE")
            self.assertIn("SYNTHETIC_ONLY", no_regression_report["promotion_gate"]["blockers"])

            partial_rows = rows[:-1]
            current_predictions.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in partial_rows), encoding="utf-8"
            )
            incomplete = subprocess.run(
                [
                    sys.executable, "-X", "utf8", str(SCRIPT), "--manifest", str(ev.SYNTHETIC_V2_MANIFEST), "--predictions", str(current_predictions),
                    "--baseline-report", str(baseline_report), "--output", str(current_report),
                    "--require-promotion-gate",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(incomplete.returncode, 3, incomplete.stderr)
            incomplete_report = json.loads(current_report.read_text(encoding="utf-8"))
            self.assertEqual(incomplete_report["missing_prediction_count"], 1)
            self.assertEqual(incomplete_report["promotion_gate"]["status"], "NOT_ELIGIBLE")
            self.assertIn("INCOMPLETE_EVALUATION", incomplete_report["promotion_gate"]["blockers"])

    def test_baseline_rejects_schema_dataset_fingerprint_and_metric_shape_mismatches(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        mismatches = []
        wrong_schema = json.loads(json.dumps(report))
        wrong_schema["schema_version"] = 2
        mismatches.append(wrong_schema)
        wrong_metric_schema = json.loads(json.dumps(report))
        wrong_metric_schema["event_pair"]["extra_metric"] = 1.0
        mismatches.append(wrong_metric_schema)
        wrong_dataset = json.loads(json.dumps(report))
        wrong_dataset["dataset_id"] = "other-dataset"
        mismatches.append(wrong_dataset)
        changed_cases = json.loads(json.dumps(self.cases))
        changed_cases[0]["input"]["left"]["title"] = "changed under the same dataset ID"
        changed_dataset = ev.evaluate(self.manifest, changed_cases, ev.perfect_predictions(changed_cases))
        self.assertEqual(changed_dataset["dataset_id"], report["dataset_id"])
        self.assertEqual(changed_dataset["case_denominator"], report["case_denominator"])
        mismatches.append(changed_dataset)
        for candidate in mismatches:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    ev.compare_reports(report, candidate)

    def test_baseline_rejects_fixed_task_denominator_drift(self):
        report = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        baseline = json.loads(json.dumps(report))
        baseline["event_pair"]["denominator"] -= 1
        baseline["event_pair"]["tn"] -= 1
        baseline["material_change"]["denominator"] += 1
        baseline["material_change"]["tn"] += 1
        baseline["material_change"]["changed_fields"]["case_denominator"] += 1
        with self.assertRaisesRegex(ValueError, "denominators do not match"):
            ev.compare_reports(report, baseline)

    def test_baseline_rejects_incomplete_reports(self):
        full = ev.evaluate(self.manifest, self.cases, ev.perfect_predictions(self.cases))
        predictions = ev.perfect_predictions(self.cases)
        predictions.pop("claim-stale-001")
        partial = ev.evaluate(self.manifest, self.cases, predictions)
        with self.assertRaisesRegex(ValueError, "complete, non-empty evaluation"):
            ev.compare_reports(partial, full)


class RegressionHarnessTests(unittest.TestCase):
    def setUp(self):
        self.manifest, self.cases = ev.load_gold(ev.SYNTHETIC_V3_MANIFEST)
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
        self.assertGreaterEqual(report["unsupported_claim_rate"]["denominator"], 1)

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
        with self.assertRaises(ValueError):
            ev.compare_reports(current, self.perfect)
        comparison = ev.compare_report_diagnostics(current, self.perfect)
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
                "--manifest", str(ev.SYNTHETIC_V3_MANIFEST),
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

    def test_forged_auxiliary_metrics_cannot_pass_the_comparison_gate(self):
        forged = json.loads(json.dumps(self.perfect))
        forged["entity_ids"]["precision"] = float("nan")
        with self.assertRaises(ValueError):
            ev.compare_reports(forged, self.perfect)

    def test_forged_case_result_cannot_hide_an_aggregate_mismatch(self):
        forged = json.loads(json.dumps(self.perfect))
        forged["case_results"][0]["status"] = "DIFF"
        with self.assertRaisesRegex(ValueError, "case result counts"):
            ev.compare_reports(forged, self.perfect)

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
        ev.validate_cases([relabelled], {"splits": ["dev"]})
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
