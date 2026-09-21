import importlib.util
from datetime import date, timedelta
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-candidate-observation-window.py"
spec = importlib.util.spec_from_file_location("candidate_observation_window", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def report(day: date, *, source_ids=("S-001", "S-031"), failed_count=0):
    rows = []
    for source_id in source_ids:
        rows.append({
            "source_id": source_id,
            "integration_status": "CANDIDATE",
            "promotion_eligible": False,
            "coverage_independently_verified": False,
            "source_health": "PASS",
            "collector_window_claim": "PARTIAL" if source_id == "S-031" else "COMPLETE_WITH_ITEMS",
            "manifest_sha256": "a" * 64,
            "schema_contract": {"status": "NO_DRIFT", "review_required": False},
        })
    return {
        "observed_at": f"{day.isoformat()}T10:00:00+08:00",
        "status": "FAILED" if failed_count else "OBSERVED",
        "failed_count": failed_count,
        "sources": rows,
    }


class CandidateObservationWindowTests(unittest.TestCase):
    def test_seven_continuous_days_pass_without_promotion(self):
        start = date(2026, 9, 15)
        result = module.validate_reports([
            (f"{day}.json", report(day))
            for day in (start + timedelta(days=offset) for offset in range(7))
        ])
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["promotion_eligible"])
        self.assertTrue(result["sources"]["S-031"]["window_complete"])

    def test_missing_day_and_failed_report_block(self):
        start = date(2026, 9, 15)
        days = [start + timedelta(days=offset) for offset in range(8) if offset != 3]
        reports = [(f"{day}.json", report(day)) for day in days]
        reports[0] = (reports[0][0], report(days[0], failed_count=1))
        result = module.validate_reports(reports)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("not an all-source observation" in reason for reason in result["reasons"]))
        self.assertTrue(any("missing observation days" in reason for reason in result["reasons"]))

    def test_source_inventory_change_blocks(self):
        start = date(2026, 9, 15)
        result = module.validate_reports([
            ("first.json", report(start)),
            ("second.json", report(start + timedelta(days=1), source_ids=("S-001", "S-032"))),
        ])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("source inventory changed" in reason for reason in result["reasons"]))


if __name__ == "__main__":
    unittest.main()
