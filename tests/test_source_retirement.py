"""Owner-approved retirement preserves replay and never invents a collection."""
import csv
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
RETIRED = {"S-019", "S-029", "S-033", "S-034"}


class SourceRetirementTests(unittest.TestCase):
    def test_exact_retirement_and_current_publication_projection(self):
        spec = importlib.util.spec_from_file_location("retirement_policy", ROOT / "scripts/source-policy.py")
        policy_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy_module)
        policy = policy_module.load_current_policy()
        catalog = policy_module.load_catalog()
        self.assertEqual(policy["active_source_ids"], ["S-004", "S-006", "S-007", "S-009"])
        self.assertFalse(RETIRED & {row["source_id"] for row in catalog["sources"]})
        self.assertFalse(RETIRED & set(catalog["promotion_plan"]))
        receipts = json.loads((ROOT / "docs/govintel/source-retirement-receipts-20261117.json").read_text(encoding="utf-8"))
        self.assertEqual([row["source_id"] for row in receipts], ["S-029"])
        self.assertIn("Owner decision 2026-11-17", receipts[0]["reason"])
        previous = policy_module.load_approved_history(policy["parent_policy_hash"])
        self.assertEqual(policy_module.compile_governed_policy(catalog, previous=previous, retirements=receipts), policy)
        self.assertEqual(policy_module.load_approved_history(policy["policy_hash"]), policy)
        with self.assertRaisesRegex(ValueError, "exact retirement receipts"):
            policy_module.compile_governed_policy(catalog, previous=previous)

        public = ROOT / "apps/web/public/data"
        documents = {name: json.loads((public / (name + ".json")).read_text(encoding="utf-8"))
                     for name in ("intelligence-feed", "source-status", "intelligence-summary", "v2-daily-brief")}
        for name, document in documents.items():
            with self.subTest(artifact=name):
                self.assertEqual(document["source_policy"], policy_module.policy_binding(policy))
                self.assertEqual(document["generated_at"], "2026-09-11T08:23:26+08:00")
        historical = json.loads((ROOT / "tests/fixtures/source-policy/publication-metadata-v3.json").read_text(encoding="utf-8"))
        self.assertEqual(len(historical["items"]), 118)
        feed = documents["intelligence-feed"]
        self.assertEqual(len(feed["items"]), 116)
        self.assertFalse(RETIRED & {row["source_id"] for row in feed["items"]})
        self.assertEqual(feed["collection_run_id"], "CR-DEMO-20260911-MORNING-SCHEDULE")
        self.assertEqual({row["source_id"] for row in documents["source-status"]["sources"]}, set(policy["active_source_ids"]))
        with (public / "feed-export.csv").open(encoding="utf-8-sig", newline="") as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual({row["stable_id"] for row in exported}, {row["stable_id"] for row in feed["items"]})


if __name__ == "__main__":
    unittest.main()
