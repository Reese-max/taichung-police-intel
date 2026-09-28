"""Fail-closed contract for the historical pinned replay lane."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify-backbone-runtime.py"
SPEC = importlib.util.spec_from_file_location("historical_backbone_runtime", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class PinnedReplayContractTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(module.MANIFEST.read_text(encoding="utf-8"))

    def test_complete_manifest_is_accepted(self):
        module.validate_manifest(self.manifest)

    def test_removed_component_cannot_pass(self):
        altered = copy.deepcopy(self.manifest)
        altered["components"] = altered["components"][1:]
        with self.assertRaisesRegex(AssertionError, "complete component set"):
            module.validate_manifest(altered)

    def test_removed_suite_cannot_pass(self):
        altered = copy.deepcopy(self.manifest)
        collector = next(row for row in altered["components"] if row["name"] == "collector")
        collector["suites"] = collector["suites"][:1]
        with self.assertRaisesRegex(AssertionError, "complete suite set"):
            module.validate_manifest(altered)

    def test_duplicate_component_cannot_pass(self):
        altered = copy.deepcopy(self.manifest)
        altered["components"][-1] = copy.deepcopy(altered["components"][0])
        with self.assertRaisesRegex(AssertionError, "complete component set"):
            module.validate_manifest(altered)

    def test_historical_scope_cannot_claim_production(self):
        altered = copy.deepcopy(self.manifest)
        altered["production_verified"] = True
        with self.assertRaisesRegex(AssertionError, "production boundary"):
            module.validate_manifest(altered)

    def test_each_run_has_a_fresh_evidence_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = module.new_run_directory(root, "run-one")
            (old / "synthetic-integration.json").write_text("old", encoding="utf-8")
            new = module.new_run_directory(root, "run-two")
            self.assertFalse((new / "synthetic-integration.json").exists())
            self.assertEqual((old / "synthetic-integration.json").read_text(encoding="utf-8"), "old")
            with self.assertRaises(FileExistsError):
                module.new_run_directory(root, "run-two")


if __name__ == "__main__":
    unittest.main()
