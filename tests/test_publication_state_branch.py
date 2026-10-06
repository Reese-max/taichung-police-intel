import functools
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/publication-state-branch.py"
spec = importlib.util.spec_from_file_location("publication_state_branch", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def git(cwd, *args, check=True, capture=True):
    return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=capture,
                          text=capture, timeout=15)


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class PublicationStateBranchTests(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ)
        self.env.start()
        os.environ.pop("GITHUB_OUTPUT", None)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.work = self.root / "work"
        git(self.root, "init", "--bare", str(self.remote))
        git(self.root, "init", "-b", "main", str(self.seed))
        git(self.seed, "config", "user.name", "test")
        git(self.seed, "config", "user.email", "test@example.test")
        self.write_bundle(self.seed, "baseline")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "baseline")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "origin", "main")
        git(self.seed, "branch", "publication-state")
        git(self.seed, "push", "origin", "publication-state")
        git(self.root, "clone", "-b", "main", str(self.remote), str(self.work))

    def write_bundle(self, repo, value):
        for relative in module.STATE_PATHS:
            target = repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{value}:{relative}\n", encoding="utf-8")

    def head(self, branch="publication-state"):
        return git(self.remote, "rev-parse", f"refs/heads/{branch}").stdout.strip()

    def checkpoint(self):
        bundle, manifest, _ = module.read_checkpoint(self.work, module.fetch_state_branch(self.work, "publication-state"))
        return bundle, manifest

    def save_pending(self):
        module.restore(self.work, "publication-state")
        self.write_bundle(self.work, "candidate")
        module.persist(self.work, "publication-state", "101", "1", "evening")

    def symlink_or_skip(self, link, target):
        try:
            link.symlink_to(target)
        except NotImplementedError:
            self.skipTest("symlinks are unavailable on this platform")
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is unavailable")
            raise

    def serve(self, bundle):
        directory = self.root / "public"
        directory.mkdir(exist_ok=True)
        for relative in module.PUBLIC_PATHS:
            target = directory / relative.removeprefix("apps/web/public/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(bundle[relative])
        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(directory)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)
        return f"http://127.0.0.1:{server.server_port}", directory

    def test_restore_replaces_local_generated_files_from_state_branch(self):
        self.write_bundle(self.work, "stale")
        self.assertEqual(module.restore(self.work, "publication-state"), len(module.STATE_PATHS))
        self.assertTrue((self.work / module.STATE_PATHS[0]).read_text().startswith("baseline:"))

    def test_missing_remote_file_rejects_entire_restore_without_mixed_writes(self):
        git(self.seed, "checkout", "publication-state")
        git(self.seed, "rm", module.STATE_PATHS[0])
        git(self.seed, "commit", "-m", "incomplete fixture")
        git(self.seed, "push", "origin", "publication-state")
        self.write_bundle(self.work, "local")
        before = module.read_working_bundle(self.work)
        with self.assertRaisesRegex(RuntimeError, "missing"):
            module.restore(self.work, "publication-state")
        self.assertEqual(before, module.read_working_bundle(self.work))
        self.assertFalse(module.receipt_path(self.work).exists())

    def test_legacy_remote_bootstraps_new_handoff_path_then_persists_it(self):
        git(self.seed, "checkout", "publication-state")
        git(self.seed, "rm", module.STATE_PATHS[-1])
        git(self.seed, "commit", "-m", "legacy state fixture")
        git(self.seed, "push", "origin", "publication-state")
        expected = (self.work / module.STATE_PATHS[-1]).read_bytes()

        for relative in module.STATE_PATHS[:-1]:
            (self.work / relative).write_text("stale\n", encoding="utf-8")
        self.assertEqual(
            module.restore(self.work, "publication-state", allow_legacy_bootstrap=True),
            len(module.STATE_PATHS),
        )
        self.assertEqual((self.work / module.STATE_PATHS[-1]).read_bytes(), expected)
        receipt = json.loads(module.receipt_path(self.work).read_bytes())
        self.assertTrue(receipt["legacy_bootstrap"])
        module.persist(self.work, "publication-state", "legacy", "1", "evening")
        self.assertIsNotNone(module.read_blob(self.work, self.head(), module.STATE_PATHS[-1]))

    def test_persist_fast_forwards_only_state_branch_and_is_idempotent(self):
        before = self.head("main")
        self.save_pending()
        after = self.head()
        self.assertEqual(self.head("main"), before)
        self.assertNotEqual(after, before)
        module.restore(self.work, "publication-state")
        self.assertFalse(module.persist(self.work, "publication-state", "102", "1", "evening"))
        self.assertEqual(self.head(), after)

    def test_rejected_state_push_preserves_last_known_good_and_generated_evidence(self):
        main_before = self.head("main")
        state_before = self.head("publication-state")
        baseline_bundle = module.read_working_bundle(self.work)
        module.write_receipt(self.work, state_before, baseline_bundle)
        self.write_bundle(self.work, "collected-but-not-persisted")
        real_git = module.git
        rejected_pushes = []

        def reject_push(repo, *args):
            if args and args[0] == "push":
                rejected_pushes.append(args)
                raise subprocess.CalledProcessError(
                    1, ["git", *args], stderr=b"simulated protected ref rejection"
                )
            return real_git(repo, *args)

        with (
            mock.patch.object(module, "fetch_state_branch", return_value=state_before),
            mock.patch.object(module, "read_checkpoint", return_value=(baseline_bundle, None, False)),
            mock.patch.object(module, "git", side_effect=reject_push),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                module.persist(self.work, "publication-state", "103", "1", "morning")

        self.assertEqual(len(rejected_pushes), 1)
        self.assertEqual(rejected_pushes[0][:2], ("push", "origin"))
        self.assertRegex(
            rejected_pushes[0][2],
            r"\A[0-9a-f]{40}:refs/heads/publication-state\Z",
        )
        self.assertEqual(self.head("main"), main_before)
        self.assertEqual(self.head("publication-state"), state_before)
        self.assertTrue(
            (self.work / module.STATE_PATHS[0]).read_text(encoding="utf-8").startswith(
                "collected-but-not-persisted:"
            )
        )

    def test_persist_fails_closed_when_state_bundle_is_incomplete(self):
        module.restore(self.work, "publication-state")
        (self.work / module.STATE_PATHS[-1]).unlink()
        with self.assertRaisesRegex(RuntimeError, "missing"):
            module.persist(self.work, "publication-state", "1", "1", "morning")

    def test_persist_requires_prior_restore_receipt(self):
        with self.assertRaisesRegex(RuntimeError, "restore receipt"):
            module.persist(self.work, "publication-state", "1", "1", "morning")

    def test_stale_worker_cannot_overwrite_newer_remote(self):
        module.restore(self.work, "publication-state")
        other = self.root / "other"
        git(self.root, "clone", "-b", "main", str(self.remote), str(other))
        module.restore(other, "publication-state")
        self.write_bundle(other, "newer")
        module.persist(other, "publication-state", "2", "1", "evening")
        newer = self.head()
        self.write_bundle(self.work, "older")
        with self.assertRaisesRegex(RuntimeError, "advanced"):
            module.persist(self.work, "publication-state", "1", "1", "morning")
        self.assertEqual(self.head(), newer)

    def test_data_branch_is_not_an_arbitrary_main_write_switch(self):
        before = self.head("main")
        for branch in ("main", "refs/heads/main", "--upload-pack=evil", "../main"):
            with self.assertRaises(RuntimeError):
                module.restore(self.work, branch)
            with self.assertRaises(RuntimeError):
                module.persist(self.work, branch, "1", "1", "morning")
        self.assertEqual(self.head("main"), before)

    def test_symlink_destination_rejected_before_restore(self):
        victim = self.root / "victim"
        victim.write_text("do-not-touch")
        first = self.work / module.STATE_PATHS[0]
        first.unlink()
        self.symlink_or_skip(first, victim)
        with self.assertRaisesRegex(RuntimeError, "symlink"):
            module.restore(self.work, "publication-state")
        self.assertEqual(victim.read_text(), "do-not-touch")

    def test_remote_symlink_is_not_accepted_as_a_state_blob(self):
        git(self.seed, "checkout", "publication-state")
        path = self.seed / module.STATE_PATHS[-1]
        path.unlink()
        self.symlink_or_skip(path, "/etc/passwd")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "bad blob fixture")
        git(self.seed, "push", "origin", "publication-state")
        with self.assertRaisesRegex(RuntimeError, "non-regular"):
            module.restore(self.work, "publication-state")

    def test_pending_checkpoint_cannot_be_overwritten_by_another_collection(self):
        self.save_pending()
        before = self.head()
        self.write_bundle(self.work, "would-lose-unpublished-change")
        with self.assertRaisesRegex(RuntimeError, "pending publication"):
            module.persist(self.work, "publication-state", "102", "1", "morning")
        self.assertEqual(self.head(), before)

    def run_pages_schema_step(self, *, pending, interrupted=False):
        if not shutil.which("bash") or not shutil.which("timeout"):
            self.skipTest("the Pages shell requires bash and timeout")
        from scripts import schema_drift

        module.restore(self.work, "publication-state")
        state_path = self.work / "state/schema-drift-state.json"
        state_path.write_text(json.dumps(schema_drift.empty_state()) + "\n", encoding="utf-8")
        if pending:
            module.persist(self.work, "publication-state", "schema-seed", "1", "code")
        output = self.root / "restore-output"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            module.restore(self.work, "publication-state")
        # Non-pending legacy fixture state is deliberately just opaque bytes.
        if not pending:
            state_path.write_text(json.dumps(schema_drift.empty_state()) + "\n", encoding="utf-8")
        before = module.read_working_bundle(self.work)
        checkpoint = self.head()

        workflow = (SCRIPT.parents[1] / ".github/workflows/pages.yml").read_text()
        step = workflow.split("        id: schema_drift\n", 1)[1].split("\n      - name:", 1)[0]
        self.assertIn("PENDING_RECOVERY: ${{ steps.restore_state.outputs.pending_recovery }}", step)
        script = textwrap.dedent(step.split("        run: |\n", 1)[1])
        restore = dict(line.split("=", 1) for line in output.read_text().splitlines())
        self.assertEqual(restore["pending_recovery"], str(pending).lower())

        observed_at = "2026-10-05T00:00:00+00:00"
        sample = {"success": True, "data": {"data": [{
            "proceedingsId": "SYNTHETIC-SCHEMA-PROBE", "date": "2026-10-05T00:00:00",
            "speaker": "synthetic", "content": "synthetic schema observation",
        }], "totalPages": 1, "totalCount": 1}}
        observations = self.root / "synthetic-schema-observations.json"
        observations.write_text(json.dumps([{
            "source_id": "S-007", "body_text": json.dumps(sample), "http_status": 200,
            "content_type": "application/json", "observed_at": observed_at,
        }]), encoding="utf-8")
        receipt_path = self.work / "apps/web/public/data/schema-drift.json"
        receipt_path.write_text('{"from_old_run":true}\n', encoding="utf-8")
        calls_path = self.root / "schema-calls.jsonl"
        bin_dir = self.root / "schema-test-bin"
        bin_dir.mkdir()
        python = bin_dir / "python"
        # Replace only external live input. Execute the actual schema CLI and
        # the actual workflow shell, including its interrupted-probe fallback.
        python.write_text(f"#!{sys.executable}\n" + textwrap.dedent('''
            import json, os, sys
            from pathlib import Path
            args = sys.argv[1:]
            with Path(os.environ["SCHEMA_REPLAY_CALLS"]).open("a") as handle:
                handle.write(json.dumps(args) + "\\n")
            if "--live" in args:
                if os.environ["SCHEMA_REPLAY_INTERRUPT"] == "1":
                    raise SystemExit(124)
                index = args.index("--live")
                args[index:index + 1] = ["--input", os.environ["SCHEMA_REPLAY_INPUT"]]
            args[args.index("scripts/schema_drift.py")] = os.environ["SCHEMA_REPLAY_SCRIPT"]
            os.execv(sys.executable, [sys.executable, *args])
        '''), encoding="utf-8")
        python.chmod(0o755)
        result = subprocess.run(["bash", "-c", script], cwd=self.work, capture_output=True,
                                text=True, timeout=20, env={
            **os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "PENDING_RECOVERY": restore["pending_recovery"],
            "SCHEMA_REPLAY_INPUT": str(observations),
            "SCHEMA_REPLAY_SCRIPT": str(SCRIPT.with_name("schema_drift.py")),
            "SCHEMA_REPLAY_CALLS": str(calls_path),
            "SCHEMA_REPLAY_INTERRUPT": "1" if interrupted else "0",
        })
        self.assertEqual(result.returncode, 124 if interrupted else 0, result.stdout + result.stderr)
        calls = [json.loads(line) for line in calls_path.read_text().splitlines()]
        self.assertIn("--live", calls[0])
        for call in calls:
            self.assertEqual("--preserve-state" in call, pending)
        receipt = json.loads(receipt_path.read_text())
        self.assertNotIn("from_old_run", receipt)
        self.assertTrue(receipt["generated_at"])
        if interrupted:
            self.assertEqual(len(calls), 2)
            self.assertIn("--live-interrupted-receipt", calls[1])
            self.assertEqual(receipt["overall"], "BLOCKED")
        else:
            source = next(row for row in receipt["sources"] if row["source_id"] == "S-007")
            self.assertEqual(source["observed_at"], observed_at)
            self.assertEqual(source["status"], "NO_DRIFT")
        return before, checkpoint

    def test_pending_pages_schema_probe_refreshes_receipt_without_changing_checkpoint(self):
        before, checkpoint = self.run_pages_schema_step(pending=True)
        self.assertEqual(module.read_working_bundle(self.work), before)
        self.assertFalse(module.persist(self.work, "publication-state", "replay", "1", "code"))
        self.assertEqual(self.head(), checkpoint)

    def test_interrupted_pending_pages_probe_keeps_state_and_fresh_failure_receipt(self):
        before, checkpoint = self.run_pages_schema_step(pending=True, interrupted=True)
        self.assertEqual(module.read_working_bundle(self.work), before)
        self.assertFalse(module.persist(self.work, "publication-state", "replay", "1", "code"))
        self.assertEqual(self.head(), checkpoint)

    def test_nonpending_pages_schema_probe_advances_durable_state(self):
        before, checkpoint = self.run_pages_schema_step(pending=False)
        after = module.read_working_bundle(self.work)
        self.assertNotEqual(after["state/schema-drift-state.json"], before["state/schema-drift-state.json"])
        for path in module.STATE_PATHS:
            if path != "state/schema-drift-state.json":
                self.assertEqual(after[path], before[path])
        self.assertTrue(module.persist(self.work, "publication-state", "probe", "1", "code"))
        self.assertNotEqual(self.head(), checkpoint)

    def test_restore_exposes_pending_recovery_without_changing_data_time(self):
        self.save_pending()
        old, _ = self.checkpoint()
        output = self.root / "github-output"
        with mock.patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}):
            module.restore(self.work, "publication-state")
        self.assertIn("pending_recovery=true", output.read_text())
        self.assertEqual(old, module.read_working_bundle(self.work))

    def test_manifest_tampering_rejected(self):
        self.save_pending()
        git(self.seed, "fetch", "origin")
        git(self.seed, "checkout", "-B", "publication-state", "origin/publication-state")
        (self.seed / module.STATE_PATHS[0]).write_text("changed without manifest")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "tamper fixture")
        git(self.seed, "push", "origin", "publication-state")
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            module.restore(self.work, "publication-state")

    def test_real_http_success_acknowledges_then_allows_next_generation(self):
        self.save_pending()
        bundle, marker = self.checkpoint()
        base, _ = self.serve(bundle)
        def verify(url, data):
            return module.verify_public_files(url, data, attempts=1, allow_loopback=True)
        proof = module.acknowledge(self.work, "publication-state", self.head(),
                                   marker["generation_id"], base, verifier=verify)
        self.assertEqual(len(proof["verified_files"]), 5)
        _, published = self.checkpoint()
        self.assertEqual(published["state"], "PUBLISHED")
        module.restore(self.work, "publication-state")
        self.write_bundle(self.work, "next-generation")
        self.assertTrue(module.persist(self.work, "publication-state", "103", "1", "morning"))

    def test_http_200_with_old_bytes_does_not_acknowledge(self):
        self.save_pending()
        bundle, marker = self.checkpoint()
        base, directory = self.serve(bundle)
        (directory / "data/v2-daily-brief.json").write_text("old-site-version")
        before = self.head()
        def verify(url, data):
            return module.verify_public_files(url, data, attempts=1, allow_loopback=True)
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            module.acknowledge(self.work, "publication-state", before,
                               marker["generation_id"], base, verifier=verify)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.checkpoint()[1]["state"], "PENDING_PUBLICATION")

    def test_retry_of_exact_acknowledged_pending_commit_rechecks_real_http_without_new_commit(self):
        self.save_pending()
        bundle, marker = self.checkpoint()
        pending = self.head()
        base, directory = self.serve(bundle)
        def verify(url, data):
            return module.verify_public_files(url, data, attempts=1, allow_loopback=True)
        module.acknowledge(self.work, "publication-state", pending, marker["generation_id"], base, verifier=verify)
        published = self.head()
        self.assertNotEqual(pending, published)
        proof = module.acknowledge(self.work, "publication-state", pending, marker["generation_id"], base, verifier=verify)
        self.assertEqual(len(proof["verified_files"]), 5)
        self.assertEqual(self.head(), published)
        (directory / "data/v2-daily-brief.json").write_text("actual later HTTP mismatch")
        with self.assertRaisesRegex(RuntimeError, "verification failed"):
            module.acknowledge(self.work, "publication-state", pending, marker["generation_id"], base, verifier=verify)
        self.assertEqual(self.head(), published)

    def test_acknowledgement_retry_rejects_later_generation_or_changed_producer(self):
        self.save_pending()
        bundle, marker = self.checkpoint()
        pending = self.head()
        base, _ = self.serve(bundle)
        verify = lambda url, data: module.verify_public_files(url, data, attempts=1, allow_loopback=True)
        module.acknowledge(self.work, "publication-state", pending, marker["generation_id"], base, verifier=verify)
        module.restore(self.work, "publication-state")
        self.write_bundle(self.work, "newer generation")
        module.persist(self.work, "publication-state", "102", "1", "evening")
        newer = self.head()
        with mock.patch.object(module, "verify_public_files") as probe, self.assertRaisesRegex(RuntimeError, "advanced"):
            module.acknowledge(self.work, "publication-state", pending, marker["generation_id"], base)
        probe.assert_not_called()
        self.assertEqual(self.head(), newer)

    def test_acknowledgement_retry_rejects_a_published_child_with_changed_metadata(self):
        self.save_pending()
        bundle, marker = self.checkpoint()
        pending = self.head()
        proof = {"generation_id": marker["generation_id"], "verified_files": {p: module.digest(bundle[p]) for p in module.PUBLIC_PATHS}}
        changed = {**marker, "state": "PUBLISHED", "http_verification": proof, "producer_run_id": "someone-else"}
        child = module.commit_bundle(self.work, pending, {module.MANIFEST: module.encoded(changed)}, "altered producer fixture")
        with mock.patch.object(module, "verify_public_files") as probe, self.assertRaisesRegex(RuntimeError, "advanced"):
            module.acknowledge(self.work, "publication-state", pending, marker["generation_id"], "https://invalid")
        probe.assert_not_called()
        self.assertEqual(self.head(), child)

    def test_acknowledgement_retry_rejects_unrelated_paths_in_the_direct_child(self):
        self.save_pending()
        bundle, marker = self.checkpoint()
        pending = self.head()
        proof = {"generation_id": marker["generation_id"], "verified_files": {p: module.digest(bundle[p]) for p in module.PUBLIC_PATHS}}
        child = module.commit_bundle(self.work, pending, {
            module.MANIFEST: module.encoded({**marker, "state": "PUBLISHED", "http_verification": proof}),
            "unrelated-file.txt": b"different transaction",
        }, "unrelated changes fixture")
        with mock.patch.object(module, "verify_public_files") as probe, self.assertRaisesRegex(RuntimeError, "advanced"):
            module.acknowledge(self.work, "publication-state", pending, marker["generation_id"], "https://invalid")
        probe.assert_not_called()
        self.assertEqual(self.head(), child)

    def test_public_probe_forbids_noncanonical_origin_and_loopback_by_default(self):
        bundle = module.read_working_bundle(self.work)
        for url in ("http://127.0.0.1:1", "https://evil.test", "https://reese-max.github.io/other",
                    "https://reese-max.github.io/taichung-police-intel/?token=secret"):
            with self.assertRaisesRegex(RuntimeError, "approved"):
                module.verify_public_files(url, bundle)

    def test_wrong_generation_and_missing_proof_never_acknowledge(self):
        self.save_pending()
        before = self.head()
        bundle, marker = self.checkpoint()
        with self.assertRaisesRegex(RuntimeError, "generation"):
            module.acknowledge(self.work, "publication-state", before, "wrong", "https://invalid")
        with self.assertRaisesRegex(RuntimeError, "incomplete proof"):
            module.acknowledge(self.work, "publication-state", before, marker["generation_id"],
                               "https://invalid", verifier=lambda *_: {})
        self.assertEqual(self.head(), before)

    def test_actual_cli_restores_and_persists_without_main_mutation(self):
        before = self.head("main")
        for action in ("restore", "persist"):
            result = subprocess.run([os.sys.executable, str(SCRIPT), action, "--repo", str(self.work)],
                                    capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.head("main"), before)
        self.assertEqual(self.checkpoint()[1]["state"], "PENDING_PUBLICATION")


if __name__ == "__main__":
    unittest.main()
