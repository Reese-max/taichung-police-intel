import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
        cls.baseline = sp.load_current_policy()

    def good_states(self, policy=None):
        policy = policy or self.baseline
        return {
            source_id: {
                "source_health": "PASS",
                "window_completeness": "COMPLETE_WITH_ITEMS",
                # A run that reported anything else is incomplete scope, so
                # coverage cannot license a bounded no-match on it.
                "result": "NEW_ITEMS",
                "freshness": "RECENT",
            }
            for source_id in policy["active_source_ids"]
        }

    def test_freshness_normalization_is_stable(self):
        for raw, expected in {
            " fresh ": "FRESH",
            "recent": "RECENT",
            " stale ": "STALE",
            "VERY_STALE": "VERY_STALE",
            "NO_DATA": "NO_DATA",
            None: "UNKNOWN",
            "": "UNKNOWN",
        }.items():
            with self.subTest(raw=raw):
                self.assertEqual(sp.normalize_freshness(raw), expected)

    def test_active_set_is_derived_from_catalog_not_parallel_list(self):
        expected = sorted(row["source_id"] for row in self.catalog["sources"] if row["status"] == "PRODUCTION_ACTIVE")
        self.assertEqual(self.baseline["active_source_ids"], expected)
        self.assertEqual([row["source_id"] for row in self.baseline["active_sources"]], expected)

    def test_collector_inventory_matches_active_catalog_rows(self):
        from collect import P0_SOURCES
        from online_collect import COLLECTORS

        expected = {
            row["source_id"]: (row["name"], row["entrypoint"])
            for row in self.catalog["sources"]
            if row["status"] == "PRODUCTION_ACTIVE"
        }
        self.assertEqual(P0_SOURCES, expected)
        self.assertTrue(set(expected) <= set(COLLECTORS))
        self.assertEqual(self.baseline["catalog_hash"], sp.digest(self.catalog))

    def test_policy_build_is_deterministic(self):
        second = sp.compile_policy(copy.deepcopy(self.catalog), bootstrap=True)
        self.assertEqual(self.baseline, second)
        sp.validate_policy(second)

    def test_bootstrap_requires_explicit_mode(self):
        with self.assertRaisesRegex(ValueError, "bootstrap must be explicit"):
            sp.compile_policy(self.catalog)

    def test_approved_snapshot_rejects_catalog_drift_or_missing_anchor(self):
        changed = copy.deepcopy(self.catalog)
        next(row for row in changed["sources"] if row["source_id"] == "S-032")["status"] = "PRODUCTION_ACTIVE"
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "changed-catalog.json"
            catalog_path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "policy/catalog binding mismatch"):
                sp.load_current_policy(catalog_path=catalog_path)
            with self.assertRaises(FileNotFoundError):
                sp.load_current_policy(policy_path=Path(directory) / "missing-policy.json")

    def test_cli_requires_previous_policy_and_exact_transition_receipt(self):
        promoted_catalog = copy.deepcopy(self.catalog)
        next(row for row in promoted_catalog["sources"] if row["source_id"] == "S-032")["status"] = "PRODUCTION_ACTIVE"
        with tempfile.TemporaryDirectory() as directory:
            catalog_path = Path(directory) / "catalog.json"
            receipts_path = Path(directory) / "receipts.json"
            output_path = Path(directory) / "proposed-policy.json"
            catalog_path.write_text(json.dumps(promoted_catalog), encoding="utf-8")
            output_path.write_text("approved-old-snapshot\n", encoding="utf-8")
            command = [sys.executable, "-X", "utf8", str(SCRIPT), "--catalog", str(catalog_path),
                       "--output", str(output_path)]
            denied = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
            self.assertNotEqual(denied.returncode, 0)
            self.assertIn("promotion receipts", denied.stderr)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "approved-old-snapshot\n")
            receipts_path.write_text(json.dumps([{
                "source_id": "S-032", "receipt_id": "review:22-canary",
                "reason": "approved candidate promotion fixture",
            }]), encoding="utf-8")
            allowed = subprocess.run(command + ["--promotions", str(receipts_path)],
                                     cwd=ROOT, text=True, capture_output=True)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            policy = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(policy["policy_version"], self.baseline["policy_version"] + 1)
            self.assertIn("S-032", policy["active_source_ids"])
            self.assertEqual(policy["transition"]["promoted"][0]["source_id"], "S-032")

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
        self.assertEqual(capability["collection_coverage_status"], "COVERED_BOUNDED_SCOPE")
        self.assertEqual(capability["status"], "UNKNOWN")
        self.assertFalse(capability["can_state_bounded_no_match"])
        states = self.good_states()
        removed = self.baseline["active_source_ids"][0]
        states.pop(removed)
        degraded = sp.assess_query(self.baseline, "publication_metadata", states)
        self.assertEqual(degraded["collection_coverage_status"], "PARTIAL")
        self.assertEqual(degraded["status"], "UNKNOWN")
        self.assertIn(removed, degraded["missing_required_sources"])
        self.assertFalse(degraded["can_state_bounded_no_match"])

    def test_partial_run_result_cannot_license_a_bounded_no_match(self):
        states = self.good_states()
        target = self.baseline["active_source_ids"][0]
        states[target]["result"] = "PARTIAL"
        result = sp.assess_query(self.baseline, "publication_metadata", states)
        self.assertEqual(result["collection_coverage_status"], "PARTIAL")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn(target, result["missing_required_sources"])
        self.assertFalse(result["can_state_bounded_no_match"])

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
        self.assertEqual(partial["collection_coverage_status"], "PARTIAL")
        self.assertEqual(partial["status"], "UNKNOWN")
        self.assertIn(target, partial["missing_required_sources"])
        self.assertIn(target, partial["stale_required_sources"])
        states[target]["window_completeness"] = "COMPLETE_WITH_ITEMS"
        stale = sp.assess_query(self.baseline, "publication_metadata", states)
        self.assertEqual(stale["collection_coverage_status"], "STALE")
        self.assertEqual(stale["status"], "UNKNOWN")
        self.assertFalse(stale["can_state_bounded_no_match"])

    def test_very_stale_no_data_and_missing_freshness_fail_closed(self):
        for freshness in ("VERY_STALE", "NO_DATA", None):
            with self.subTest(freshness=freshness):
                states = self.good_states()
                target = self.baseline["active_source_ids"][0]
                if freshness is None:
                    states[target].pop("freshness")
                else:
                    states[target]["freshness_status"] = freshness
                    states[target].pop("freshness")
                result = sp.assess_query(self.baseline, "publication_metadata", states)
                self.assertEqual(result["collection_coverage_status"], "STALE" if freshness == "VERY_STALE" else "PARTIAL")
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertFalse(result["can_state_bounded_no_match"])
                if freshness == "VERY_STALE":
                    self.assertIn(target, result["stale_required_sources"])
                else:
                    self.assertIn(target, result["missing_required_sources"])

    def test_unsupported_query_never_returns_fake_empty(self):
        result = sp.assess_query(self.baseline, "traffic_events", self.good_states())
        self.assertEqual(result["status"], "CAPABILITY_NOT_AVAILABLE")
        self.assertFalse(result["can_state_bounded_no_match"])
        unknown = sp.assess_query(self.baseline, "not-a-capability", self.good_states())
        self.assertEqual(unknown["status"], "CAPABILITY_NOT_AVAILABLE")

    def test_policy_only_without_runtime_states_cannot_assert_no_match(self):
        result = sp.assess_query(self.baseline, "publication_metadata")
        self.assertEqual(result["collection_coverage_status"], "POLICY_ONLY")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertFalse(result["can_state_bounded_no_match"])
        self.assertEqual(result["missing_required_sources"], self.baseline["active_source_ids"])

    def test_tampered_policy_hash_fails_closed(self):
        tampered = copy.deepcopy(self.baseline)
        tampered["active_source_ids"] = tampered["active_source_ids"][:-1]
        with self.assertRaisesRegex(ValueError, "hash"):
            sp.validate_policy(tampered)

    def test_recomputed_tampered_projection_fails_closed(self):
        tampered = copy.deepcopy(self.baseline)
        tampered["active_source_ids"] = tampered["active_source_ids"][:-1]
        tampered["policy_hash"] = sp.digest({key: value for key, value in tampered.items() if key != "policy_hash"})
        with self.assertRaisesRegex(ValueError, "active_sources"):
            sp.validate_policy(tampered)

    def test_recomputed_capability_and_candidate_forgery_fail_catalog_binding(self):
        states = self.good_states()
        for capability_id, required, optional in (
            ("publication_metadata", self.baseline["active_source_ids"][:-1], []),
            ("traffic_events", ["S-032"], []),
        ):
            with self.subTest(capability=capability_id):
                tampered = copy.deepcopy(self.baseline)
                if capability_id == "traffic_events":
                    tampered["active_source_ids"].append("S-032")
                    candidate = next(row for row in self.catalog["sources"] if row["source_id"] == "S-032")
                    tampered["active_sources"].append(sp._project_source({**candidate, "status": "PRODUCTION_ACTIVE"}))
                capability = next(row for row in tampered["capabilities"] if row["capability_id"] == capability_id)
                capability["required_sources"] = required
                capability["candidate_or_optional_sources"] = optional
                capability["supported"] = True
                tampered["policy_hash"] = sp.digest({key: value for key, value in tampered.items() if key != "policy_hash"})
                sp.validate_policy(tampered)
                with self.assertRaisesRegex(ValueError, "approved catalog-bound snapshot"):
                    sp.assess_query(tampered, capability_id, states)

    def test_malformed_projection_types_fail_closed(self):
        cases = []
        for key, value in (("active_source_ids", [None]), ("capabilities", [None])):
            tampered = copy.deepcopy(self.baseline)
            tampered[key] = value
            tampered["policy_hash"] = sp.digest({name: item for name, item in tampered.items() if name != "policy_hash"})
            cases.append(tampered)
        tampered = copy.deepcopy(self.baseline)
        tampered["capabilities"][0]["required_sources"] = [None]
        tampered["policy_hash"] = sp.digest({name: item for name, item in tampered.items() if name != "policy_hash"})
        cases.append(tampered)
        for policy in cases:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    sp.validate_policy(policy)


if __name__ == "__main__":
    unittest.main()
