import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("free_model_synthetic", ROOT / "scripts/evaluate-free-model-synthetic.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class FreeModelSyntheticTests(unittest.TestCase):
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

    def test_zero_exit_provider_error_and_tool_use_are_not_scored(self):
        for event, reason in (({"type":"error"}, "provider returned an error"), ({"type":"tool_use"}, "unexpected tool use")):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ValueError, reason):
                    module.parse_response(json.dumps(event), 0)

    def test_truncated_model_json_is_not_accepted_as_a_complete_response(self):
        events = [{"type":"text", "part":{"text":'[{"case_id":"x","prediction":{}}]'}}, {"type":"step_finish", "part":{"reason":"length"}}]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            module.parse_response("\n".join(json.dumps(event) for event in events), 0)


if __name__ == "__main__":
    unittest.main()
