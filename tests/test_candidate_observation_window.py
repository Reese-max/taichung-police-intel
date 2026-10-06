import importlib.util
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-candidate-observation-window.py"
spec = importlib.util.spec_from_file_location("candidate_observation_window", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)

CATALOG = json.loads((ROOT / "docs" / "govintel" / "source-catalog.v2.json").read_text(encoding="utf-8"))
PROMOTION_IDS = tuple(CATALOG["promotion_plan"])
WORKFLOW = ROOT / ".github" / "workflows" / "candidate-source-observation.yml"


def report(day: date, *, source_ids=PROMOTION_IDS, failed_count=0, notice=True):
    rows = []
    for source_id in source_ids:
        row = {
            "source_id": source_id,
            "integration_status": "CANDIDATE",
            "promotion_eligible": False,
            "coverage_independently_verified": False,
            "source_health": "PASS",
            "collector_window_claim": "PARTIAL" if source_id == "S-031" else "COMPLETE_WITH_ITEMS",
            "manifest_sha256": "a" * 64,
            "schema_contract": {"status": "NO_DRIFT", "review_required": False},
        }
        if source_id == "S-031" and notice:
            row["public_usage_notice"] = "僅供公共態勢感知，不作派遣或勤務指揮依據。"
            row["retention_class"] = "OFFICIAL_TRANSIENT_METADATA"
        rows.append(row)
    return {
        "observed_at": f"{day.isoformat()}T10:00:00+08:00",
        "status": "FAILED" if failed_count else "OBSERVED",
        "failed_count": failed_count,
        "sources": rows,
    }


class CandidateObservationWindowTests(unittest.TestCase):
    def test_future_days_cannot_complete_a_window(self):
        start = date(2026, 9, 15)
        result = module.validate_reports([
            (f"{day}.json", report(day))
            for day in (start + timedelta(days=offset) for offset in range(7))
        ], as_of=datetime(2026, 9, 20, 12, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("future observation" in reason for reason in result["reasons"]))
        self.assertFalse(result["sources"]["S-033"]["window_complete"])

    def test_success_days_are_separate_from_partial_coverage_and_guardrails(self):
        start = date(2026, 9, 15)
        result = module.validate_reports([
            (f"{day}.json", report(day, notice=False))
            for day in (start + timedelta(days=offset) for offset in range(7))
        ])
        fire = result["sources"]["S-031"]
        self.assertEqual(len(fire["successful_transport_days"]), 7)
        self.assertEqual(len(fire["partial_collection_days"]), 7)
        self.assertEqual(len(fire["missing_guardrail_days"]), 7)
        self.assertEqual(fire["valid_observed_days"], [])
        self.assertFalse(fire["coverage_independently_verified"])
        self.assertFalse(fire["promotion_eligible"])

    def test_a_source_cannot_borrow_another_report_day(self):
        observed = report(date(2026, 9, 15), source_ids=("S-033",))
        observed["sources"][0]["observed_at"] = "2026-09-14T10:00:00+08:00"
        result = module.validate_reports([("clock-mismatch.json", observed)], required_days=1, required_source_ids=("S-033",))
        self.assertFalse(result["sources"]["S-033"]["window_complete"])
        self.assertTrue(any("clock does not match" in reason for reason in result["reasons"]))

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
        self.assertTrue(any("not a successful observation" in reason for reason in result["reasons"]))
        self.assertTrue(any("missing observation days" in reason for reason in result["reasons"]))

    def test_independent_source_reports_for_each_day_pass(self):
        start = date(2026, 9, 15)
        reports = [
            (f"{day}-{source_id}.json", report(day, source_ids=(source_id,)))
            for day in (start + timedelta(days=offset) for offset in range(7))
            for source_id in PROMOTION_IDS
        ]
        result = module.validate_reports(reports)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["source_ids"], sorted(PROMOTION_IDS))
        self.assertTrue(all(row["window_complete"] for row in result["sources"].values()))

    def test_failed_source_does_not_hide_healthy_source(self):
        start = date(2026, 9, 15)
        reports = [
            (
                f"{day}-{source_id}.json",
                report(day, source_ids=(source_id,), failed_count=int(offset == 3 and source_id == "S-001")),
            )
            for offset in range(7)
            for day in (start + timedelta(days=offset),)
            for source_id in PROMOTION_IDS
        ]
        result = module.validate_reports(reports)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["sources"]["S-001"]["window_complete"])
        self.assertTrue(result["sources"]["S-031"]["window_complete"])

    def test_duplicate_source_day_blocks(self):
        day = date(2026, 9, 15)
        result = module.validate_reports([
            ("first.json", report(day, source_ids=("S-032",))),
            ("repeat.json", report(day, source_ids=("S-032",))),
        ], required_days=1)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["sources"]["S-032"]["window_complete"])

    def test_fire_rows_without_usage_notice_block(self):
        start = date(2026, 9, 15)
        result = module.validate_reports([
            (f"{day}.json", report(day, source_ids=("S-031",), notice=False))
            for day in (start + timedelta(days=offset) for offset in range(7))
        ])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["sources"]["S-031"]["window_complete"])
        self.assertTrue(any("usage notice" in reason for reason in result["reasons"]))

    def test_source_inventory_change_blocks(self):
        start = date(2026, 9, 15)
        result = module.validate_reports([
            ("first.json", report(start)),
            ("second.json", report(start + timedelta(days=1), source_ids=("S-001", "S-032"))),
        ])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any("source inventory changed" in reason for reason in result["reasons"]))

    def test_unobserved_promotion_candidate_blocks_window(self):
        start = date(2026, 9, 15)
        observed = tuple(source_id for source_id in PROMOTION_IDS if source_id != "S-019")
        result = module.validate_reports([
            (f"{day}.json", report(day, source_ids=observed))
            for day in (start + timedelta(days=offset) for offset in range(7))
        ])
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("S-019", result["sources"])
        self.assertFalse(result["sources"]["S-019"]["window_complete"])
        self.assertEqual(result["sources"]["S-019"]["valid_observed_days"], [])
        self.assertTrue(
            any("S-019" in reason and "valid observed days" in reason for reason in result["reasons"])
        )

    def test_empty_or_unknown_promotion_plan_fails_closed(self):
        import tempfile

        for plan in ([], ["S-999"]):
            catalog = {"sources": CATALOG["sources"], "promotion_plan": plan}
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", encoding="utf-8", delete=False
            ) as handle:
                json.dump(catalog, handle)
            try:
                with mock.patch.object(module, "CATALOG", Path(handle.name)):
                    with self.assertRaises(ValueError):
                        module.promotion_source_ids()
            finally:
                Path(handle.name).unlink(missing_ok=True)

    def test_observation_workflow_covers_every_promotion_candidate(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        matrix = re.search(r"^\s*source:\s*\[([^\]]+)\]", workflow, re.M)
        self.assertIsNotNone(matrix, "candidate observation workflow must declare a source matrix")
        scheduled = {item.strip() for item in matrix.group(1).split(",") if item.strip()}
        missing = sorted(set(PROMOTION_IDS) - scheduled)
        self.assertEqual(missing, [], "daily candidate observation misses promotion sources")


if __name__ == "__main__":
    unittest.main()
