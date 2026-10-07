import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("free_model_synthetic", ROOT / "scripts/evaluate-free-model-synthetic.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FreeModelSyntheticTests(unittest.TestCase):
    def run_with_predictions(self, predictions, output):
        """Replay a CLI result offline; this does not constitute a model run."""
        events = [
            {"type": "text", "part": {"text": json.dumps(predictions)}},
            {"type": "step_finish", "part": {"reason": "stop", "tokens": {}, "cost": 0}},
        ]
        stdout = "\n".join(json.dumps(event) for event in events)
        result = subprocess.CompletedProcess([], 0, stdout, "")
        with mock.patch.object(sys, "argv", ["diagnostic", "--output", str(output)]), \
             mock.patch.object(module.subprocess, "run", return_value=result) as transport, \
             mock.patch("sys.stdout", new=io.StringIO()):
            exit_code = module.main()
        self.assertEqual(transport.call_count, 1)
        self.assertEqual((output / "private-cli-events.jsonl").read_text(), stdout)
        return exit_code, json.loads((output / "live-model-receipt.json").read_text())

    def test_prompt_sends_only_synthetic_inputs_without_gold_labels(self):
        manifest, cases, prompt, excluded = module.prepare(ROOT / "eval/gold/v1/manifest.json")
        payload = json.loads(prompt.split("Inputs:\n", 1)[1])
        self.assertTrue(all(set(row) == {"case_id", "task", "input"} for row in payload))
        self.assertEqual(len(cases), 12)
        self.assertEqual(len(excluded), 3)
        self.assertIn('"STALE"', module.PROMPT)
        self.assertEqual(manifest["evaluation_scope"], "LIVE_MODEL_SYNTHETIC_DIAGNOSTIC_NOT_PRODUCTION")

    def test_non_synthetic_case_is_refused_before_a_transport_exists(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"dataset_type":"SYNTHETIC_REGRESSION_SEED", "case_file":"cases.jsonl"}))
            (root / "cases.jsonl").write_text(json.dumps({"case_id":"REAL", "task":"claim_support", "synthetic":False, "input":{}}))
            with self.assertRaisesRegex(ValueError, "every case must explicitly be synthetic"):
                module.prepare(root / "manifest.json")

    def test_duplicate_or_malformed_input_ids_are_refused_before_transport_or_output_creation(self):
        _, seed_cases, _, _ = module.prepare(ROOT / "eval/gold/v1/manifest.json")
        for label, case_id in (("duplicate", seed_cases[0]["case_id"]), ("empty", ""),
                               ("blank", " "), ("null", None), ("number", 12), ("array", [])):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = json.loads((ROOT / "eval/gold/v1/manifest.json").read_text())
                manifest["case_file"] = "cases.jsonl"
                cases = [seed_cases[0], dict(seed_cases[0], case_id=case_id)]
                (root / "manifest.json").write_text(json.dumps(manifest))
                (root / "cases.jsonl").write_text("\n".join(json.dumps(case) for case in cases))
                output = root / "diagnostic"
                events = [
                    {"type": "text", "part": {"text": json.dumps([
                        {"case_id": seed_cases[0]["case_id"], "prediction": seed_cases[0]["expected"]}
                    ])}},
                    {"type": "step_finish", "part": {"reason": "stop", "tokens": {}, "cost": 0}},
                ]
                result = subprocess.CompletedProcess([], 0, "\n".join(json.dumps(event) for event in events), "")
                with mock.patch.object(sys, "argv", ["diagnostic", "--manifest", str(root / "manifest.json"), "--output", str(output)]), \
                     mock.patch.object(module.subprocess, "run", return_value=result) as transport, \
                     mock.patch("sys.stdout", new=io.StringIO()):
                    with self.assertRaisesRegex(ValueError, "case_id"):
                        module.main()
                transport.assert_not_called()
                self.assertFalse(output.exists())

    def test_zero_exit_provider_error_and_tool_use_are_not_scored(self):
        for event, reason in (({"type":"error"}, "provider returned an error"), ({"type":"tool_use"}, "unexpected tool use")):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    module.parse_response(json.dumps(event), 0)

    def test_truncated_model_json_is_not_accepted_as_a_complete_response(self):
        events = [{"type":"text", "part":{"text":'[{"case_id":"x","prediction":{}}]'}}, {"type":"step_finish", "part":{"reason":"length"}}]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            module.parse_response("\n".join(json.dumps(event) for event in events), 0)

    def test_provider_stop_with_missing_cases_retains_partial_report_and_exits_nonzero(self):
        _, cases, _, _ = module.prepare(ROOT / "eval/gold/v1/manifest.json")
        predictions = [{"case_id": cases[0]["case_id"], "prediction": cases[0]["expected"]}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "partial"
            exit_code, receipt = self.run_with_predictions(predictions, output)
            report = json.loads((output / "evaluation-report.json").read_text())
            saved = [json.loads(line) for line in (output / "predictions.jsonl").read_text().splitlines()]
        self.assertEqual(exit_code, 1)
        self.assertEqual(receipt["status"], "INCOMPLETE")
        self.assertEqual(receipt["evaluation_status"], "INCOMPLETE_MISSING_PREDICTIONS")
        self.assertEqual(receipt["evaluated_cases"], 1)
        self.assertEqual(receipt["missing_prediction_count"], len(cases) - 1)
        self.assertEqual(receipt["missing_prediction_ids"], [case["case_id"] for case in cases[1:]])
        self.assertEqual(receipt["missing_prediction_ids"], report["missing_prediction_ids"])
        self.assertEqual(saved, predictions)
        self.assertEqual(receipt["official_records_transmitted"], 0)
        self.assertFalse(receipt["production_provider_enabled"])

    def test_complete_diagnostic_can_be_evaluated_with_wrong_answers(self):
        _, cases, _, _ = module.prepare(ROOT / "eval/gold/v1/manifest.json")
        predictions = [{"case_id": case["case_id"], "prediction": case["expected"]} for case in cases]
        predictions[0] = {"case_id": cases[0]["case_id"], "prediction": {"same_event": not cases[0]["expected"]["same_event"]}}
        with tempfile.TemporaryDirectory() as directory:
            exit_code, receipt = self.run_with_predictions(predictions, Path(directory) / "complete")
        self.assertEqual(exit_code, 0)
        self.assertEqual(receipt["status"], "EVALUATED")
        self.assertEqual(receipt["evaluation_status"], "COMPLETE")
        self.assertEqual(receipt["evaluated_cases"], len(cases))
        self.assertEqual(receipt["missing_prediction_count"], 0)
        self.assertEqual(receipt["missing_prediction_ids"], [])
        self.assertLess(receipt["exact_case_matches"], len(cases))

    def test_duplicate_and_extra_prediction_ids_remain_failed_receipts(self):
        _, cases, _, _ = module.prepare(ROOT / "eval/gold/v1/manifest.json")
        predictions = [{"case_id": case["case_id"], "prediction": case["expected"]} for case in cases]
        for label, extra, expected_reason in (
            ("duplicate", predictions[0], "duplicate prediction"),
            ("unknown", {"case_id": "NOT_REQUESTED", "prediction": {}}, "unknown case IDs"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / label
                exit_code, receipt = self.run_with_predictions(predictions + [extra], output)
                self.assertFalse((output / "evaluation-report.json").exists())
            self.assertEqual(exit_code, 1)
            self.assertEqual(receipt["status"], "FAILED")
            self.assertIn(expected_reason, receipt["failure_reason"])


if __name__ == "__main__":
    unittest.main()
