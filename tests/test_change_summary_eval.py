from __future__ import annotations

import json
import unittest
from pathlib import Path

from intel_v2.change_summary import DisabledProvider, ScriptedProvider
from intel_v2.change_summary_eval import evaluate, validate_cases

ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = ROOT / "eval/change-summary/v1"
CASES = [json.loads(line) for line in (CASE_DIR / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
SCRIPTED = json.loads((CASE_DIR / "scripted-responses.json").read_text(encoding="utf-8"))


def replay(responses):
    return evaluate(CASES, lambda case: ScriptedProvider(responses.get(case["case_id"], '{"statements":[]}')))


class ChangeSummaryEvalTest(unittest.TestCase):
    def test_seed_cases_are_valid_and_synthetic(self):
        validate_cases(CASES)
        self.assertEqual(len(CASES), 8)
        self.assertTrue(any(case["should_refuse"] for case in CASES))

    def test_disabled_provider_reports_not_run_without_metrics(self):
        report = evaluate(CASES, lambda case: DisabledProvider())
        self.assertEqual(report["evaluation_status"], "NOT_RUN")
        self.assertIsNone(report["metrics"])
        self.assertEqual(report["official_records_transmitted"], 0)

    def test_scripted_replay_is_a_harness_self_test_only(self):
        report = replay(SCRIPTED)
        self.assertEqual(report["evaluation_status"], "EVALUATED")
        self.assertEqual(report["evaluation_scope"], "SYNTHETIC_DIAGNOSTIC_NOT_HOLDOUT")
        self.assertEqual(report["ai_disclosure"]["provider"], "SCRIPTED_FIXTURE")
        self.assertEqual(report["metrics"]["cases_correct"], 8)

    def test_missing_answers_and_false_refusals_are_counted(self):
        report = replay({**SCRIPTED, "cs-time-001": json.dumps({"statements": []})})
        self.assertEqual(report["metrics"]["false_refusals"], 1)
        self.assertEqual(report["metrics"]["change_cases_covered"], 5)

    def test_fabricated_quote_is_rejected_and_counted_by_code(self):
        bad = json.dumps(
            {"statements": [{"claim_type": "TIME", "text": "管制改為 18:00 開始。",
                             "evidence": [{"side": "after", "quote": "自18:00開始"}]}]},
            ensure_ascii=False,
        )
        report = replay({**SCRIPTED, "cs-time-001": bad})
        self.assertEqual(report["metrics"]["rejected_statements_by_code"], {"QUOTE_NOT_IN_SOURCE": 1})
        self.assertEqual(report["metrics"]["change_cases_covered"], 5)

    def test_obeyed_injection_is_verified_text_but_flagged_by_forbidden_value(self):
        obeyed = json.dumps(
            {"statements": [{"claim_type": "STATUS", "text": "活動已取消。",
                             "evidence": [{"side": "after", "quote": "活動已取消"}]}]},
            ensure_ascii=False,
        )
        report = replay({**SCRIPTED, "cs-injection-001": obeyed})
        row = next(r for r in report["cases"] if r["case_id"] == "cs-injection-001")
        self.assertFalse(row["correct"])
        self.assertEqual(row["forbidden_hits"], ["已取消"])
        self.assertEqual(report["metrics"]["forbidden_value_cases"], 1)

    def test_refusal_case_that_gets_a_summary_is_incorrect(self):
        stmt = json.dumps(
            {"statements": [{"claim_type": "TEXT_CHANGE", "text": "句號被刪除。",
                             "evidence": [{"side": "before", "quote": "舉行。"}]}]},
            ensure_ascii=False,
        )
        report = replay({**SCRIPTED, "cs-format-only-001": stmt})
        row = next(r for r in report["cases"] if r["case_id"] == "cs-format-only-001")
        self.assertFalse(row["correct"])

    def test_non_synthetic_or_duplicate_cases_are_rejected_before_transport(self):
        with self.assertRaises(ValueError):
            validate_cases([{**CASES[0], "synthetic": False}])
        with self.assertRaises(ValueError):
            validate_cases([CASES[0], CASES[0]])
        with self.assertRaises(ValueError):
            validate_cases([{**CASES[6], "must_contain": ["x"]}])


if __name__ == "__main__":
    unittest.main()
