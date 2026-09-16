import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "publication-state-branch.py"
spec = importlib.util.spec_from_file_location("publication_state_branch", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def git(cwd: Path, *args: str, check: bool = True, capture: bool = False):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        text=capture,
    )


class PublicationStateBranchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        self.seed = root / "seed"
        self.work = root / "work"

        git(root, "init", "--bare", str(self.remote))
        git(root, "init", "-b", "main", str(self.seed))
        git(self.seed, "config", "user.name", "test")
        git(self.seed, "config", "user.email", "test@example.com")
        for relative in module.STATE_PATHS:
            path = self.seed / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"baseline:{relative}\n", encoding="utf-8")
        git(self.seed, "add", ".")
        git(self.seed, "commit", "-m", "baseline")
        git(self.seed, "remote", "add", "origin", str(self.remote))
        git(self.seed, "push", "origin", "main")
        git(self.seed, "branch", "publication-state")
        git(self.seed, "push", "origin", "publication-state")

        git(root, "clone", "-b", "main", str(self.remote), str(self.work))
        git(self.work, "config", "user.name", "test")
        git(self.work, "config", "user.email", "test@example.com")

    def tearDown(self):
        self.tmp.cleanup()

    def test_restore_replaces_local_generated_files_from_state_branch(self):
        target = self.work / module.STATE_PATHS[0]
        target.write_text("local-stale\n", encoding="utf-8")
        restored = module.restore(self.work, "publication-state")
        self.assertEqual(restored, len(module.STATE_PATHS))
        self.assertTrue(target.read_text(encoding="utf-8").startswith("baseline:"))

    def test_persist_fast_forwards_only_state_branch_and_is_idempotent(self):
        module.restore(self.work, "publication-state")
        target = self.work / module.STATE_PATHS[0]
        target.write_text("new-publication-state\n", encoding="utf-8")

        main_before = git(self.remote, "rev-parse", "refs/heads/main", capture=True).stdout.strip()
        state_before = git(self.remote, "rev-parse", "refs/heads/publication-state", capture=True).stdout.strip()

        changed = module.persist(self.work, "publication-state", "123", "1", "morning")
        self.assertTrue(changed)

        main_after = git(self.remote, "rev-parse", "refs/heads/main", capture=True).stdout.strip()
        state_after = git(self.remote, "rev-parse", "refs/heads/publication-state", capture=True).stdout.strip()
        self.assertEqual(main_before, main_after)
        self.assertNotEqual(state_before, state_after)

        stored = git(
            self.remote,
            "show",
            f"refs/heads/publication-state:{module.STATE_PATHS[0]}",
            capture=True,
        ).stdout
        self.assertEqual(stored, "new-publication-state\n")

        # Restore the just-persisted generation and persist again: no duplicate commit.
        module.restore(self.work, "publication-state")
        self.assertFalse(module.persist(self.work, "publication-state", "124", "1", "morning"))
        state_final = git(self.remote, "rev-parse", "refs/heads/publication-state", capture=True).stdout.strip()
        self.assertEqual(state_after, state_final)

    def test_persist_fails_closed_when_state_bundle_is_incomplete(self):
        module.restore(self.work, "publication-state")
        (self.work / module.STATE_PATHS[-1]).unlink()
        with self.assertRaisesRegex(RuntimeError, "missing"):
            module.persist(self.work, "publication-state", "123", "1", "evening")


if __name__ == "__main__":
    unittest.main()
