"""The public rights packet binds evidence; it cannot grant missing permissions."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKET = ROOT / "docs/govintel/competition-2026/work-rights-review.v1.json"
MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg", ".mp4", ".webm", ".pdf", ".docx"}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


class WorkRightsTests(unittest.TestCase):
    def setUp(self):
        self.packet = json.loads(PACKET.read_text(encoding="utf-8"))

    def test_every_reference_binds_fixed_git_bytes(self):
        self.assertEqual(self.packet["schema_version"], 1)
        for ref in self.packet["evidence"].values():
            with self.subTest(path=ref["path"]):
                self.assertEqual(ref["code_sha"], self.packet["versions"]["audit_base"]["code_sha"])
                self.assertNotIn("..", Path(ref["path"]).parts)
                # The recorded SHA is provenance; unchanged checkout blobs also work in shallow CI.
                data = git("show", f'HEAD:{ref["path"]}')
                self.assertEqual(hashlib.sha256(data).hexdigest(), ref["sha256"])

    def test_versions_separate_historical_acceptance_from_unverified_increment(self):
        versions = self.packet["versions"]
        for version in versions.values():
            self.assertRegex(version["code_sha"], r"^[0-9a-f]{40}$")
            self.assertRegex(version["tree_sha"], r"^[0-9a-f]{40}$")
        delta = json.loads((ROOT / self.packet["evidence"]["historical_delta"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(versions["prototype"]["code_sha"], delta["old_sha"])
        self.assertEqual(versions["accepted_release"]["code_sha"], delta["new_sha"])
        ci = json.loads((ROOT / self.packet["evidence"]["accepted_ci"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(versions["accepted_release"]["tree_sha"], ci["merged_tree"])
        self.assertEqual(versions["audit_base"]["code_sha"], "c629f82a8b16825d5d89c204c7035dcba500a9ac")
        self.assertIs(versions["audit_base"]["production_acceptance_verified"], False)
        self.assertIsNone(self.packet["award_review"]["official_modification_percentage"])
        self.assertEqual(self.packet["award_review"]["status"], "UNKNOWN")
        self.assertEqual(self.packet["award_review"]["organizer_determination"], "PENDING")

    def test_all_rights_categories_and_official_uses_remain_unapproved(self):
        self.assertEqual(self.packet["status"], "REVIEW_REQUIRED")
        self.assertIs(self.packet["issue_closable"], False)
        categories = self.packet["rights_inventory"]
        self.assertEqual(set(categories), {"project_code", "contributions", "dependencies", "source_data",
                                          "model_outputs", "display_assets"})
        for row in categories.values():
            self.assertEqual(row["status"], "NOT_VERIFIED")
            self.assertIsNone(row["rights_holder"])
            self.assertEqual(row["consent_receipts"], [])
            self.assertTrue(row["missing_inputs"])
            self.assertTrue(row["evidence_ids"])
            self.assertTrue(set(row["evidence_ids"]) <= self.packet["evidence"].keys())
        consent = self.packet["official_use_consent"]
        self.assertEqual(set(consent["uses"]), {"competition_submission", "official_promotion", "project_closeout"})
        self.assertTrue(all(value == "NOT_VERIFIED" for value in consent["uses"].values()))
        self.assertEqual(consent["public_receipt_ids"], [])
        self.assertIs(consent["private_originals_included"], False)
        self.assertEqual(set(self.packet["closure_blockers"]), {
            "AWARD_HISTORY_UNKNOWN", "OWNER_AND_CONTRIBUTOR_CONSENT_MISSING", "SOURCE_RIGHTS_UNREVIEWED",
            "PYTHON_TRANSITIVE_INVENTORY_MISSING", "SHIPPING_NOTICES_UNVERIFIED",
            "MODEL_OUTPUT_TERMS_UNVERIFIED", "DISPLAY_ASSET_RIGHTS_UNVERIFIED", "OFFICIAL_USE_CONSENT_MISSING"})

    def test_source_inventory_reuses_canonical_rights_and_formal_admission(self):
        spec = importlib.util.spec_from_file_location("work_rights_policy", ROOT / "scripts/source-policy.py")
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        catalog = policy.load_catalog()
        governance = policy.governance_module().compile_governance(catalog)
        sources = self.packet["source_rights_binding"]
        self.assertEqual(sources["retention_policy_hash"], governance["retention_policy"]["policy_hash"])
        approved = policy.load_current_policy()
        self.assertEqual(sources["approved_policy_binding"], policy.policy_binding(approved))
        self.assertEqual(sources["formal_admission"], policy.formal_admission(approved))
        review = json.loads((ROOT / self.packet["evidence"]["source_review"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(sorted(sources["source_ids"]), sorted(row["source_id"] for row in catalog["sources"]))
        self.assertEqual(sorted(sources["source_ids"]), sorted(row["source_id"] for row in review["source_reviews"]))
        self.assertIs(sources["model_transmission_allowed"], False)
        self.assertTrue(all(row["review_required"] and not row["model_transmission_allowed"]
                            for row in review["source_reviews"]))

    def test_assets_and_model_outputs_are_enumerated_without_claiming_ownership(self):
        paths = git("ls-tree", "-r", "--name-only", "-z", "HEAD").decode().split("\0")
        assets = sorted(path for path in paths if Path(path).suffix.lower() in MEDIA)
        refs = self.packet["evidence"]
        self.assertEqual(sorted(refs[key]["path"] for key in self.packet["asset_evidence_ids"]), assets)
        outputs = sorted(path for path in paths if path.endswith("/predictions.jsonl")
                         and "model-diagnostic-20261006/" in path)
        self.assertEqual(sorted(refs[key]["path"] for key in self.packet["model_output_evidence_ids"]), outputs)
        dependencies = json.loads((ROOT / refs["dependency_inventory"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual(self.packet["dependency_scope"]["inventory_code_sha"], dependencies["code_sha"])
        self.assertEqual(self.packet["dependency_scope"]["declared_entry_count"], len(dependencies["dependencies"]))
        self.assertEqual(self.packet["dependency_scope"]["python_transitive_inventory"], "NOT_VERIFIED")
        self.assertEqual(self.packet["dependency_scope"]["shipping_notices"], "NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
