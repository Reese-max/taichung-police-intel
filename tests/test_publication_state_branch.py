import functools
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
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
        return module.read_checkpoint(self.work, module.fetch_state_branch(self.work, "publication-state"))

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
        self.assertEqual(module.restore(self.work, "publication-state"), 7)
        self.assertTrue((self.work / module.STATE_PATHS[0]).read_text().startswith("baseline:"))

    def test_missing_remote_file_rejects_entire_restore_without_mixed_writes(self):
        git(self.seed, "checkout", "publication-state")
        git(self.seed, "rm", module.STATE_PATHS[-1])
        git(self.seed, "commit", "-m", "incomplete fixture")
        git(self.seed, "push", "origin", "publication-state")
        self.write_bundle(self.work, "local")
        before = module.read_working_bundle(self.work)
        with self.assertRaisesRegex(RuntimeError, "missing"):
            module.restore(self.work, "publication-state")
        self.assertEqual(before, module.read_working_bundle(self.work))
        self.assertFalse(module.receipt_path(self.work).exists())

    def test_persist_fast_forwards_only_state_branch_and_is_idempotent(self):
        before = self.head("main")
        self.save_pending()
        after = self.head()
        self.assertEqual(self.head("main"), before)
        self.assertNotEqual(after, before)
        module.restore(self.work, "publication-state")
        self.assertFalse(module.persist(self.work, "publication-state", "102", "1", "evening"))
        self.assertEqual(self.head(), after)

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
