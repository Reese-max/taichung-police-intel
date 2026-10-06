import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/verify-publication-bundle.py"
spec = importlib.util.spec_from_file_location("verify_publication_bundle", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


CATALOG_ROWS = {
    "S-004": {
        "source_id": "S-004",
        "role": "PRIMARY_EVENT",
        "status": "PRODUCTION_ACTIVE",
        "entrypoint": "https://a.gov.tw/list",
    },
    "S-031": {
        "source_id": "S-031",
        "role": "PRIMARY_EVENT",
        "status": "VERIFIED_CANDIDATE",
        "entrypoint": "https://fire.gov.tw/",
        "public_usage_notice": "僅供公共態勢感知，不作派遣或勤務指揮依據。",
        "retention_class": "OFFICIAL_TRANSIENT_METADATA",
    },
}
CATALOG = {"sources": list(CATALOG_ROWS.values()), "promotion_plan": ["S-031"]}
EXPECTED_SOURCES = {"S-004"}


def valid_bundle():
    status = {
        "schema_version": 1,
        "mode": "COMPETITION_DEMO",
        "generated_at": "2026-09-11T08:23:26+08:00",
        "latest_collection_run": {"collection_run_id": "CR-1"},
        "sources": [
            {
                "source_id": "S-004",
                "source_name": "來源",
                "source_url": "https://a.gov.tw/list",
                "source_role": "PRIMARY_EVENT",
                "integration_status": "PRODUCTION_ACTIVE",
            }
        ],
        "candidate_catalog": [
            {
                "source_id": "S-031",
                "source_name": "消防",
                "source_url": "https://fire.gov.tw/",
                "source_role": "PRIMARY_EVENT",
                "integration_status": "VERIFIED_CANDIDATE",
                "promotion_eligible": False,
                "public_usage_notice": "僅供公共態勢感知，不作派遣或勤務指揮依據。",
                "retention_class": "OFFICIAL_TRANSIENT_METADATA",
            }
        ],
    }
    feed = {
        "schema_version": 1,
        "generated_at": "2026-09-11T08:23:26+08:00",
        "collection_run_id": "CR-1",
        "items": [
            {
                "stable_id": "FEED-S-004-1",
                "source_id": "S-004",
                "source_role": "PRIMARY_OFFICIAL",
                "catalog_role": "PRIMARY_EVENT",
                "integration_status": "PRODUCTION_ACTIVE",
                "official_url": "https://a.gov.tw/list/1",
                "content_sha256": "c" * 64,
                "reason_codes": [],
                "eligibility": "HOME_CANDIDATE",
            }
        ],
        "source_summary": {"S-004": {}},
    }
    summary = {
        "schema_version": 1,
        "generated_at": "2026-09-11T08:23:26+08:00",
        "collection_run_id": "CR-1",
        "total_items": 1,
        "eligible_items": 1,
        "source_breakdown": [{"source_id": "S-004", "item_count": 1}],
    }
    csv_rows = [{"stable_id": "FEED-S-004-1"}]
    return status, feed, summary, csv_rows


def bundle_errors(bundle):
    status, feed, summary, csv_rows = bundle
    return module.bundle_errors(status, feed, summary, csv_rows, CATALOG, EXPECTED_SOURCES)


class BundleRoleStatusTests(unittest.TestCase):
    def test_valid_bundle_with_roles_and_candidate_metadata_passes(self):
        self.assertEqual(bundle_errors(valid_bundle()), [])

    def test_source_without_role_or_status_is_rejected(self):
        status, feed, summary, csv_rows = valid_bundle()
        del status["sources"][0]["source_role"]
        del status["sources"][0]["integration_status"]
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("source_role" in error for error in errors))
        self.assertTrue(any("integration_status" in error for error in errors))

    def test_feed_item_role_must_match_catalog(self):
        status, feed, summary, csv_rows = valid_bundle()
        feed["items"][0]["catalog_role"] = "DISCOVERY_ONLY"
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("catalog_role" in error for error in errors))

    def test_candidate_cannot_be_labelled_production(self):
        status, feed, summary, csv_rows = valid_bundle()
        status["candidate_catalog"][0]["integration_status"] = "PRODUCTION_ACTIVE"
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("production" in error.lower() or "integration_status" in error for error in errors))

    def test_candidate_promotion_flag_must_stay_false(self):
        status, feed, summary, csv_rows = valid_bundle()
        status["candidate_catalog"][0]["promotion_eligible"] = True
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("promotion" in error for error in errors))

    def test_fire_candidate_must_carry_usage_notice(self):
        status, feed, summary, csv_rows = valid_bundle()
        del status["candidate_catalog"][0]["public_usage_notice"]
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("usage_notice" in error for error in errors))

    def test_unknown_candidate_source_rejected(self):
        status, feed, summary, csv_rows = valid_bundle()
        status["candidate_catalog"].append(
            {"source_id": "S-999", "source_role": "PRIMARY_EVENT",
             "integration_status": "VERIFIED_CANDIDATE", "promotion_eligible": False,
             "source_url": "https://x.gov.tw/"}
        )
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("S-999" in error for error in errors))

    def test_credentialed_recheck_url_rejected(self):
        status, feed, summary, csv_rows = valid_bundle()
        feed["items"][0]["official_url"] = "https://user:secret@a.gov.tw/list/1"
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("credential" in error or "official_url" in error for error in errors))

    def test_plan_candidates_cannot_be_dropped_silently(self):
        status, feed, summary, csv_rows = valid_bundle()
        status["candidate_catalog"] = []
        errors = bundle_errors((status, feed, summary, csv_rows))
        self.assertTrue(any("promotion plan" in error for error in errors))

    def test_non_object_bundle_members_fail_closed(self):
        for bad in ([], "x"):
            errors = module.bundle_errors(bad, {}, {}, [], CATALOG, EXPECTED_SOURCES)
            self.assertEqual(len(errors), 1)


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

    def test_checked_in_sources_expose_catalog_role_and_integration_status(self):
        status = module.load_json("source-status.json")
        metadata = {row["source_id"]: row for row in module.load_catalog()["sources"]}
        for source in status["sources"]:
            source_id = source["source_id"]
            self.assertEqual(source["source_role"], metadata[source_id]["role"])
            self.assertEqual(source["integration_status"], metadata[source_id]["status"])

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
