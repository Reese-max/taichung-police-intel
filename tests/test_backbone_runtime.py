"""Fail-closed contract for the historical pinned replay lane."""
from __future__ import annotations

import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


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

    def test_rejected_manifest_still_writes_fail_report(self):
        altered = copy.deepcopy(self.manifest)
        altered["components"] = altered["components"][1:]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = root / "invalid-manifest.json"
            manifest.write_text(json.dumps(altered), encoding="utf-8")
            output = root / "evidence"
            with patch.object(module, "MANIFEST", manifest), patch.object(
                sys, "argv", [str(SCRIPT), "--output", str(output), "--components", str(root / "components")]
            ), redirect_stdout(io.StringIO()):
                self.assertEqual(module.main(), 1)
            reports = list(output.glob("*/report.json"))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "FAIL")
            self.assertEqual(report["error_type"], "AssertionError")
            self.assertFalse(report["production_verified"])

    def test_malformed_or_missing_manifest_still_writes_fail_report(self):
        for label, contents, error_type in (
            ("malformed", "{bad json", "JSONDecodeError"),
            ("missing", None, "FileNotFoundError"),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest = root / "manifest.json"
                if contents is not None:
                    manifest.write_text(contents, encoding="utf-8")
                output = root / "evidence"
                with patch.object(module, "MANIFEST", manifest), patch.object(
                    sys, "argv", [str(SCRIPT), "--output", str(output)]
                ), redirect_stdout(io.StringIO()):
                    self.assertEqual(module.main(), 1)
                reports = list(output.glob("*/report.json"))
                self.assertEqual(len(reports), 1)
                report = json.loads(reports[0].read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(report["error_type"], error_type)
                self.assertIsNone(report["manifest_sha256"])
                self.assertFalse(report["production_verified"])

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

    def test_reused_checkout_rejects_untracked_test(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def git(*args):
                result = subprocess.run(
                    ["git", *args], cwd=root, capture_output=True, text=True,
                    timeout=15, check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            git("init")
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            git("add", "tracked.txt")
            git("-c", "user.name=fixture", "-c", "user.email=fixture@example.test", "commit", "-m", "fixture")
            commit = git("rev-parse", "HEAD")
            module.verify_checkout(root, commit, "fixture")
            (root / "tests").mkdir()
            (root / "tests" / "test_hijack.py").write_text("raise AssertionError('untracked')\n", encoding="utf-8")
            with self.assertRaisesRegex(AssertionError, "dirty or untracked checkout"):
                module.verify_checkout(root, commit, "fixture")


if __name__ == "__main__":
    unittest.main()
