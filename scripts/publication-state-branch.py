#!/usr/bin/env python3
"""Restore and persist generated publication state on a dedicated branch.

This deliberately keeps generated scheduled state off protected ``main``. Code changes
still go through the normal PR + required-check path; the ``publication-state`` branch
contains only a replayable copy of generated publication artifacts used between runs.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

STATE_BRANCH = os.environ.get("PUBLICATION_STATE_BRANCH", "publication-state")
STATE_PATHS = (
    "apps/web/public/data/source-status.json",
    "apps/web/public/data/intelligence-feed.json",
    "apps/web/public/data/intelligence-summary.json",
    "apps/web/public/data/feed-export.csv",
    "apps/web/public/data/v2-daily-brief.json",
    "state/v2-shadow-state.json",
)


def run(args: list[str], cwd: Path, *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def remote_ref(branch: str) -> str:
    return f"refs/remotes/origin/{branch}"


def fetch_state_branch(repo: Path, branch: str) -> None:
    result = run(
        ["git", "fetch", "origin", f"refs/heads/{branch}:{remote_ref(branch)}"],
        repo,
        check=False,
        capture=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"publication state branch {branch!r} is unavailable: {detail}")


def restore(repo: Path, branch: str) -> int:
    fetch_state_branch(repo, branch)
    restored = 0
    for relative in STATE_PATHS:
        result = run(
            ["git", "show", f"{remote_ref(branch)}:{relative}"],
            repo,
            check=False,
            capture=True,
        )
        if result.returncode != 0:
            continue
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(result.stdout)
        restored += 1
    if restored == 0:
        raise RuntimeError("publication state branch contained none of the expected state paths")
    print(f"PUBLICATION_STATE_RESTORED branch={branch} files={restored}")
    return restored


def persist(repo: Path, branch: str, run_id: str, run_attempt: str, slot: str) -> bool:
    missing = [relative for relative in STATE_PATHS if not (repo / relative).is_file()]
    if missing:
        raise RuntimeError("cannot persist incomplete publication state; missing: " + ", ".join(missing))

    fetch_state_branch(repo, branch)
    with tempfile.TemporaryDirectory(prefix="govintel-publication-state-") as tmp:
        worktree = Path(tmp)
        run(["git", "worktree", "add", "--detach", str(worktree), remote_ref(branch)], repo)
        try:
            for relative in STATE_PATHS:
                src = repo / relative
                dst = worktree / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

            run(["git", "config", "user.name", "github-actions[bot]"], worktree)
            run(
                ["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"],
                worktree,
            )
            run(["git", "add", "--", *STATE_PATHS], worktree)
            changed = run(["git", "diff", "--cached", "--quiet"], worktree, check=False).returncode != 0
            if not changed:
                print(f"PUBLICATION_STATE_UNCHANGED branch={branch} run_id={run_id}")
                return False

            message = f"data: publication state {slot} run {run_id}.{run_attempt}"
            run(["git", "commit", "-m", message], worktree)
            run(["git", "push", "origin", f"HEAD:refs/heads/{branch}"], worktree)
            head = run(["git", "rev-parse", "HEAD"], worktree, capture=True).stdout.decode().strip()
            print(
                "PUBLICATION_STATE_PERSISTED "
                f"branch={branch} run_id={run_id} attempt={run_attempt} slot={slot} commit={head}"
            )
            return True
        finally:
            run(["git", "worktree", "remove", "--force", str(worktree)], repo, check=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("restore", "persist"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--branch", default=STATE_BRANCH)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    parser.add_argument("--slot", default=os.environ.get("PUBLICATION_SLOT", "unknown"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = Path(args.repo).resolve()
    if args.command == "restore":
        restore(repo, args.branch)
    else:
        persist(repo, args.branch, args.run_id, args.run_attempt, args.slot)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
