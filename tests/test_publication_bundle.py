import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-publication-bundle.py"
spec = importlib.util.spec_from_file_location("verify_publication_bundle", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


class PublicationBundleTests(unittest.TestCase):
    def test_source_rows_are_not_silently_filtered(self):
        with self.assertRaisesRegex(ValueError, "only objects"):
            module.source_ids([{"source_id": "S-004"}, None])

    def test_source_ids_must_be_nonempty_strings(self):
        with self.assertRaisesRegex(ValueError, "non-empty strings"):
            module.source_ids([{"source_id": ""}])

    def test_validator_uses_current_source_policy(self):
        policy_path = Path(__file__).resolve().parents[1] / "scripts/source-policy.py"
        policy_spec = importlib.util.spec_from_file_location("source_policy", policy_path)
        policy = importlib.util.module_from_spec(policy_spec)
        assert policy_spec and policy_spec.loader
        policy_spec.loader.exec_module(policy)
        expected = {
            row["source_id"]
            for row in policy.load_catalog()["sources"]
            if row["status"] == "PRODUCTION_ACTIVE"
        }
        self.assertEqual(module.load_expected_sources(), expected)

    def test_checked_in_bundle_passes(self):
        self.assertEqual(module.main(), 0)

    def test_candidate_lane_is_separate_and_must_remain_ineligible(self):
        from scripts.candidate_publication import load_candidate_publication_sources

        sources = load_candidate_publication_sources()
        active_ids = module.load_expected_sources()
        self.assertFalse(set(sources) & active_ids)
        status_rows = []
        candidate_items = []
        candidate_summary = {}
        for index, (source_id, source) in enumerate(sources.items()):
            status_rows.append({
                "source_id": source_id,
                "source_name": source["name"],
                "source_url": source["entrypoint"],
                "source_health": "PASS",
                "window_completeness": "COMPLETE_WITH_ITEMS",
                "integration_status": "CANDIDATE",
                "promotion_eligible": False,
            })
            candidate_items.append({
                "stable_id": f"FEED-{source_id}-{index}",
                "stable_key": str(index),
                "source_id": source_id,
                "official_url": source["entrypoint"],
                "eligibility": "INELIGIBLE_CANDIDATE",
                "change_type": "CANDIDATE_OBSERVATION",
                "integration_status": "CANDIDATE",
                "promotion_eligible": False,
            })
            candidate_summary[source_id] = {
                "health": "PASS",
                "item_count": 1,
                "integration_status": "CANDIDATE",
                "promotion_eligible": False,
            }
        status = {"candidate_sources": status_rows}
        feed = {
            "items": [],
            "candidate_items": candidate_items,
            "candidate_source_summary": candidate_summary,
        }
        module.validate_candidate_lane(status, feed, active_ids=active_ids, required=True)

        forged = {**feed, "items": [candidate_items[0]]}
        with self.assertRaisesRegex(ValueError, "leaked into active feed"):
            module.validate_candidate_lane(status, forged, active_ids=active_ids, required=True)
        promoted = {**status, "candidate_sources": [
            {**row, "promotion_eligible": True} if row["source_id"] == "S-032" else row
            for row in status_rows
        ]}
        with self.assertRaisesRegex(ValueError, "CANDIDATE-only"):
            module.validate_candidate_lane(promoted, feed, active_ids=active_ids, required=True)
        with self.assertRaisesRegex(ValueError, "candidate_sources must be an array"):
            module.validate_candidate_lane({}, {"items": [], "candidate_items": []}, active_ids=active_ids, required=True)


if __name__ == "__main__":
    unittest.main()
