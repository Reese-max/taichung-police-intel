import importlib.util
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-source-policy-integration.py"
spec = importlib.util.spec_from_file_location("source_policy_integration_check", SCRIPT)
check = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(check)


class SourcePolicyIntegrationTests(unittest.TestCase):
    def test_real_transition_with_nonempty_candidate_publication(self):
        from scripts.candidate_publication import load_candidate_publication_sources
        policy = check.load_module("candidate_transition_policy", "scripts/source-policy.py")
        store = check.load_module("candidate_transition_store", "scripts/query-store.py")
        original_load = store.load_json
        sources = load_candidate_publication_sources()
        rows = [{"source_id": sid, "source_name": row["name"], "source_url": row["entrypoint"],
                 "source_health": "PASS", "window_completeness": "COMPLETE_WITH_ITEMS",
                 "integration_status": "CANDIDATE", "promotion_eligible": False} for sid, row in sources.items()]
        items = [{"source_id": sid, "stable_id": sid + "-fixture", "stable_key": "fixture",
                  "official_url": row["entrypoint"], "eligibility": "INELIGIBLE_CANDIDATE",
                  "change_type": "CANDIDATE_OBSERVATION", "integration_status": "CANDIDATE",
                  "promotion_eligible": False} for sid, row in sources.items()]
        def with_candidates(path):
            value, digest = original_load(path)
            if Path(path).name == "source-status.json":
                value["candidate_sources"] = copy.deepcopy(rows)
            elif Path(path).name == "intelligence-feed.json":
                value["candidate_items"] = copy.deepcopy(items)
                value["candidate_source_summary"] = {sid: {"health": "PASS", "item_count": 1,
                    "integration_status": "CANDIDATE", "promotion_eligible": False} for sid in sources}
            return value, digest
        paths = [ROOT / "scripts/candidate_publication.py", ROOT / "docs/govintel/source-catalog.v2.json",
                 ROOT / "docs/govintel/source-policy.approved.json", store.DEFAULT_FEED, store.DEFAULT_STATUS]
        before = {path: path.read_bytes() for path in paths}
        old = json.loads((ROOT / "tests/fixtures/source-policy/publication-metadata-v3.json").read_text())
        with mock.patch.object(store, "load_json", side_effect=with_candidates):
            result = check.run_promoted_fixture(policy.load_catalog(), policy.load_current_policy(), old, store)
        self.assertTrue(result["historical_replay_equal"])
        self.assertEqual(result["consumer_count"], 7)
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_fictional_promotion_preserves_the_remaining_candidate_lane(self):
        source = ROOT / "scripts/candidate_publication.py"
        original = source.read_bytes()
        rows = [{"source_id": sid, "marker": sid} for sid in ("S-001", "S-019", "S-032")]
        status = {"candidate_sources": copy.deepcopy(rows)}
        feed = {"candidate_items": copy.deepcopy(rows),
                "candidate_source_summary": {row["source_id"]: {"item_count": 1} for row in rows}}
        brief = {"candidate_source_context": {"sources": copy.deepcopy(rows), "items": copy.deepcopy(rows),
                                              "promotion_eligible": False}}
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory)
            (fixture / "scripts").mkdir()
            (fixture / "scripts/candidate_publication.py").write_bytes(original)
            check.reconcile_promoted_candidate_fixture(fixture, ["S-032"], feed, status, brief)
            scope = {}
            exec(compile((fixture / "scripts/candidate_publication.py").read_text(), str(source), "exec"),
                 {"__file__": str(fixture / "scripts/candidate_publication.py")}, scope)
            self.assertEqual(scope["CANDIDATE_PUBLICATION_SOURCE_IDS"], ("S-001", "S-019"))
        self.assertEqual(status["candidate_sources"], rows[:2])
        self.assertEqual(feed["candidate_items"], rows[:2])
        self.assertEqual(feed["candidate_source_summary"], {sid: {"item_count": 1} for sid in ("S-001", "S-019")})
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(brief["candidate_source_context"], {"sources": rows[:2], "items": rows[:2],
                                                            "promotion_eligible": False})

    def test_candidate_reconciliation_refuses_the_real_checkout(self):
        with self.assertRaisesRegex(ValueError, "real checkout"):
            check.reconcile_promoted_candidate_fixture(ROOT, ["S-032"], {}, {}, {})

    def test_all_consumers_and_replay_share_current_policy(self):
        result = check.run_checks()
        self.assertEqual(result["consumer_count"], 7)
        self.assertEqual(
            result["consumer_names"],
            [
                "query_store",
                "system_health",
                "ui",
                "query_gateway",
                "collector",
                "online_collector",
                "publication_validator",
            ],
        )
        self.assertTrue(result["mixed_policy_rejected"])
        self.assertTrue(result["partial_gap_preserved"])
        self.assertTrue(result["unsupported_explicit"])
        self.assertTrue(result["query_coverage_bound"])
        self.assertEqual(result["promoted_policy_version"], result["policy_version"] + 1)
        fixture = result["fixture_transition"]
        self.assertEqual(fixture["consumer_count"], 7)
        self.assertEqual(fixture["active"], result["active"] + 1)
        self.assertNotEqual(fixture["new_generation"], fixture["old_generation"])
        self.assertTrue(fixture["historical_replay_equal"])
        self.assertTrue(fixture["live_old_policy_rejected"])
        self.assertTrue(fixture["bounded_traffic_zero"])
        self.assertEqual(fixture["required_source_gaps"], {"FAILED": "PARTIAL", "PARTIAL": "PARTIAL", "STALE": "STALE"})
        self.assertEqual(fixture["provider_calls"], 0)


if __name__ == "__main__":
    unittest.main()
