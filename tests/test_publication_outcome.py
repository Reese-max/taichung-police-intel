import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("publication_outcome", ROOT / "scripts/publication-outcome.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class OutcomeTests(unittest.TestCase):
    def test_success_is_not_a_live_verification_receipt(self):
        text, code = module.report({"BUILD_RESULT": "success", "DEPLOY_RESULT": "success"})
        self.assertEqual(code, 0)
        self.assertIn("UNVERIFIED_HTTP", text)

    def test_push_failure_is_reported_and_deploy_skip_is_not_success(self):
        text, code = module.report({"BUILD_RESULT": "failure", "DEPLOY_RESULT": "skipped", "PRESERVE": "failure"})
        self.assertEqual(code, 1)
        self.assertIn("| preserve | failure |", text)
        self.assertIn("PUBLICATION_NOT_CONFIRMED", text)

    def test_upload_and_deploy_failures(self):
        for env in [
            {"BUILD_RESULT": "failure", "DEPLOY_RESULT": "skipped", "PAGES_UPLOAD": "failure"},
            {"BUILD_RESULT": "success", "DEPLOY_RESULT": "failure"},
        ]:
            self.assertEqual(module.report(env)[1], 1)

    def test_cancellation_and_unknown_are_not_success(self):
        for state in ["cancelled", "skipped", "", "unexpected"]:
            self.assertEqual(module.report({"BUILD_RESULT": "success", "DEPLOY_RESULT": state})[1], 1)

    def test_artifact_url_requires_successful_upload(self):
        url = "https://github.com/Reese-max/taichung-police-intel/actions/runs/123/artifacts/456"
        self.assertNotIn(url, module.report({"EVIDENCE_URL": url, "EVIDENCE": "failure"})[0])
        self.assertIn(url, module.report({"EVIDENCE_URL": url, "EVIDENCE": "success"})[0])

    def test_no_arbitrary_evidence_link(self):
        for url in [
            "https://evil.test/artifact",
            "http://github.com/actions/runs/1/artifacts/2",
            "https://github.com/actions/runs/1/artifacts/2?token=secret",
        ]:
            self.assertNotIn(url, module.report({"EVIDENCE": "success", "EVIDENCE_URL": url})[0])

    def test_cli_writes_literal_summary_and_preserves_failed_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            env = {
                **os.environ,
                "GITHUB_STEP_SUMMARY": str(summary),
                "BUILD_RESULT": "failure",
                "DEPLOY_RESULT": "skipped",
                "PRESERVE": "failure",
                "EVIDENCE": "success",
                "EVIDENCE_URL": "https://github.com/Reese-max/taichung-police-intel/actions/runs/123/artifacts/456",
            }
            expected, code = module.report(env)
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/publication-outcome.py")],
                env=env, capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(result.returncode, code)
            self.assertEqual(code, 1)
            self.assertEqual(summary.read_text(encoding="utf-8"), expected)
            self.assertIn("`PUBLICATION_NOT_CONFIRMED`", result.stdout)
            self.assertIn("::warning title=Publication not confirmed::", result.stdout)

    def test_cli_treats_shell_metacharacters_as_untrusted_data(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.md"
            sentinel = Path(directory) / "must-not-exist"
            payload = f"`touch {sentinel}` $(touch {sentinel})"
            env = {
                **os.environ,
                "GITHUB_STEP_SUMMARY": str(summary),
                "BUILD_RESULT": "success",
                "DEPLOY_RESULT": "success",
                "PRESERVE": payload,
                "EVIDENCE": "success",
                "EVIDENCE_URL": payload,
            }
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/publication-outcome.py")],
                env=env, capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertFalse(sentinel.exists())
            rendered = summary.read_text(encoding="utf-8")
            self.assertNotIn(payload, rendered)
            self.assertIn("| preserve | unknown |", rendered)
            self.assertIn("No successful evidence-upload receipt", rendered)
            self.assertIn("UNVERIFIED_HTTP", rendered)

    def test_real_workflow_uses_dedicated_state_branch_and_retains_downstream_evidence(self):
        text = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        restore = "publication-state-branch.py restore --branch publication-state"
        collect = "Refresh official-source publication bundle"
        persist = "publication-state-branch.py persist"
        pages_upload = "id: pages_upload"
        evidence = "id: evidence"
        self.assertIn(restore, text)
        self.assertIn(persist, text)
        self.assertLess(text.index(restore), text.index(collect))
        self.assertLess(text.index(persist), text.index(pages_upload))
        self.assertLess(text.index(pages_upload), text.index(evidence))
        self.assertNotIn("git push\n", text)
        self.assertNotIn('git commit -m "data: refresh V1 and V2 publication bundle', text)
        # Restore/persist must ALSO run on code-only pushes, to avoid deploying main's old JSON.
        restore_block = text[text.index("id: restore_state"):text.index(collect)]
        persist_block = text[text.index("id: preserve"):text.index(pages_upload)]
        self.assertNotIn("if:", restore_block)
        self.assertNotIn("if:", persist_block)
        self.assertIn("needs: [build, deploy]", text)
        self.assertIn("if: always()", text[text.index("publication_outcome:"):])
        self.assertIn("python scripts/publication-outcome.py", text)
        self.assertIn("if-no-files-found: error", text)
        self.assertIn("npm run check", text)
        self.assertNotIn("continue-on-error", text)
        self.assertNotIn("--force", text)

    def test_pending_replay_and_public_acknowledgement_are_wired(self):
        text = (ROOT / ".github/workflows/pages.yml").read_text(encoding="utf-8")
        self.assertIn("if: github.ref == 'refs/heads/main'", text)
        self.assertIn("steps.restore_state.outputs.pending_recovery != 'true'", text)
        self.assertIn('if [[ "$PENDING_RECOVERY" == "true" ]]', text)
        self.assertLess(text.index("id: deployment"), text.index("id: acknowledge"))
        self.assertIn("EXPECTED_STATE_COMMIT: ${{ needs.build.outputs.state_commit }}", text)
        self.assertIn("EXPECTED_GENERATION: ${{ needs.build.outputs.generation_id }}", text)
        self.assertIn("PUBLICATION_BASE_URL: ${{ steps.deployment.outputs.page_url }}", text)
        self.assertIn("PUBLIC_VERIFY: ${{ needs.deploy.outputs.public_verify }}", text)

    def test_explicit_failed_public_probe_cannot_report_success(self):
        for status in ("failure", "skipped", "", "unknown"):
            text, code = module.report({"BUILD_RESULT": "success", "DEPLOY_RESULT": "success", "PUBLIC_VERIFY": status})
            self.assertEqual(code, 1)
            self.assertIn("PUBLICATION_NOT_CONFIRMED", text)
        text, code = module.report({"BUILD_RESULT": "success", "DEPLOY_RESULT": "success", "PUBLIC_VERIFY": "success"})
        self.assertEqual(code, 0)
        self.assertIn("PUBLIC_DATA_VERIFIED", text)
        self.assertNotIn("UNVERIFIED_HTTP", text)
        self.assertIn("not attest every frontend asset", text)


if __name__ == "__main__":
    unittest.main()
