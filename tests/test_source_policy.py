import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "source-policy.py"
spec = importlib.util.spec_from_file_location("source_policy", SCRIPT)
sp = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(sp)


class SourcePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = sp.load_catalog()
        cls.baseline = sp.compile_policy(cls.catalog)

    def good_states(self, policy=None):
        policy = policy or self.baseline
        return {
            source_id: {
                "source_health": "PASS",
                "window_completeness": "COMPLETE_WITH_ITEMS",
                "freshness": "RECENT",
            }
            for source_id in policy["active_source_ids"]
        }

    def test_active_set_is_derived_from_catalog_not_parallel_list(self):
        expected = sorted(row["source_id"] for row in self.catalog["sources"] if row["status"] == "PRODUCTION_ACTIVE")
        self.assertEqual(self.baseline["active_source_ids"], expected)
        self.assertEqual([row["source_id"] for row in self.baseline["active_sources"]], expected)
        self.assertEqual(self.baseline["catalog_hash"], sp.digest(self.catalog))

    def test_policy_build_is_deterministic(self):
        second = sp.compile_policy(copy.deepcopy(self.catalog))
        self.assertEqual(self.baseline, second)
        sp.validate_policy(second)

    def test_candidate_does_not_enter_verified_active_set(self):
        candidates = {row["source_id"] for row in self.catalog["sources"] if row["status"] != "PRODUCTION_ACTIVE"}
        self.assertFalse(candidates & set(self.baseline["active_source_ids"]))
        traffic = next(row for row in self.baseline["capabilities"] if row["capability_id"] == "traffic_events")
        self.assertFalse(traffic["supported"])
        self.assertIn("S-032", traffic["candidate_or_optional_sources"])

    def test_promotion_requires_exact_receipt_and_enables_capability(self):
        promoted_catalog = copy.deepcopy(self.catalog)
        next(row for row in promoted_catalog["sources"] if row["source_id"] == "S-032")["status"] = "PRODUCTION_ACTIVE"
        with self.assertRaisesRegex(ValueError, "promotion receipts"):
            sp.compile_policy(promoted_catalog, previous=self.baseline)
        policy = sp.compile_policy(
            promoted_catalog,
            previous=self.baseline,
            promotions=[{"source_id": "S-032", "receipt_id": "review:22-canary", "reason": "approved candidate promotion fixture"}],
        )
        self.assertEqual(policy["policy_version"], self.baseline["policy_version"] + 1)
        self.assertIn("S-032", policy["active_source_ids"])
        traffic = next(row for row in policy["capabilities"] if row["capability_id"] == "traffic_events")
        self.assertTrue(traffic["supported"])
        self.assertEqual(traffic["required_sources"], ["S-032"])

    def test_runtime_cannot_make_policy_green_by_dropping_source(self):
        capability = sp.assess_query(self.baseline, "publication_metadata", self.good_states())
        self.assertEqual(capability["status"], "COVERED_BOUNDED_SCOPE")
        states = self.good_states()
        removed = self.baseline["active_source_ids"][0]
        states.pop(removed)
        degraded = sp.assess_query(self.baseline, "publication_metadata", states)
        self.assertEqual(degraded["status"], "PARTIAL")
        self.assertIn(removed, degraded["missing_required_sources"])
        self.assertFalse(degraded["can_state_bounded_no_match"])

    def test_retirement_requires_explicit_receipt(self):
        retired_catalog = copy.deepcopy(self.catalog)
        source_id = self.baseline["active_source_ids"][0]
        next(row for row in retired_catalog["sources"] if row["source_id"] == source_id)["status"] = "AUDITED_EXISTING"
        with self.assertRaisesRegex(ValueError, "retirement receipts"):
            sp.compile_policy(retired_catalog, previous=self.baseline)
        policy = sp.compile_policy(
            retired_catalog,
            previous=self.baseline,
            retirements=[{"source_id": source_id, "receipt_id": "retire:test", "reason": "explicit retirement fixture with coverage loss"}],
        )
        self.assertNotIn(source_id, policy["active_source_ids"])
        self.assertEqual(policy["transition"]["retired"][0]["source_id"], source_id)

    def test_partial_beats_stale_but_stale_is_reported_when_complete(self):
        states = self.good_states()
        target = self.baseline["active_source_ids"][0]
        states[target]["window_completeness"] = "PARTIAL"
        states[target]["freshness"] = "STALE"
        partial = sp.assess_query(self.baseline, "publication_metadata", states)
        self.assertEqual(partial["status"], "PARTIAL")
        self.assertIn(target, partial["missing_required_sources"])
        self.assertIn(target, partial["stale_required_sources"])
        states[target]["window_completeness"] = "COMPLETE_WITH_ITEMS"
        stale = sp.assess_query(self.baseline, "publication_metadata", states)
        self.assertEqual(stale["status"], "STALE")
        self.assertFalse(stale["can_state_bounded_no_match"])

    def test_unsupported_query_never_returns_fake_empty(self):
        result = sp.assess_query(self.baseline, "traffic_events", self.good_states())
        self.assertEqual(result["status"], "CAPABILITY_NOT_AVAILABLE")
        self.assertFalse(result["can_state_bounded_no_match"])
        unknown = sp.assess_query(self.baseline, "not-a-capability", self.good_states())
        self.assertEqual(unknown["status"], "CAPABILITY_NOT_AVAILABLE")

    def test_policy_only_without_runtime_states_cannot_assert_no_match(self):
        result = sp.assess_query(self.baseline, "publication_metadata")
        self.assertEqual(result["status"], "POLICY_ONLY")
        self.assertFalse(result["can_state_bounded_no_match"])
        self.assertEqual(result["missing_required_sources"], self.baseline["active_source_ids"])

    def test_tampered_policy_hash_fails_closed(self):
        tampered = copy.deepcopy(self.baseline)
        tampered["active_source_ids"] = tampered["active_source_ids"][:-1]
        with self.assertRaisesRegex(ValueError, "hash"):
            sp.validate_policy(tampered)


if __name__ == "__main__":
    unittest.main()
