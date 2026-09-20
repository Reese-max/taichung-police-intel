#!/usr/bin/env python3
"""Transactional generated-data checkpoints; never execute code from the data branch.

A saved checkpoint is PENDING until the public data bytes have been verified.
On the next workflow run a pending checkpoint is replayed before any collection.
This is a data publication receipt, not proof that every frontend asset is current.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener

STATE_BRANCH = "publication-state"
STATE_PATHS = (
    "apps/web/public/data/source-status.json",
    "apps/web/public/data/intelligence-feed.json",
    "apps/web/public/data/intelligence-summary.json",
    "apps/web/public/data/feed-export.csv",
    "apps/web/public/data/v2-daily-brief.json",
    "state/v2-shadow-state.json",
    "state/v2-handoff-state.json",
)
PUBLIC_PATHS = tuple(path for path in STATE_PATHS if path.startswith("apps/web/public/"))
MANIFEST = "state/publication-checkpoint.json"
MAX_FILE_BYTES = 32 * 1024 * 1024
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")


def run(args, cwd, *, check=True, capture=False, env=None):
    return subprocess.run(args, cwd=cwd, check=check, env=env, timeout=90,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def git(repo, *args):
    return run(["git", *args], repo, capture=True).stdout


def validate_branch(branch):
    if branch != STATE_BRANCH:
        raise RuntimeError("only the publication-state branch is writable/readable")


def remote_ref(branch):
    validate_branch(branch)
    return f"refs/remotes/origin/{branch}"


def fetch_state_branch(repo, branch):
    validate_branch(branch)
    # A force-update of the data branch is deliberately not accepted.
    git(repo, "fetch", "origin", f"refs/heads/{branch}:{remote_ref(branch)}")
    return git(repo, "rev-parse", remote_ref(branch)).decode().strip()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def hashes(bundle):
    return {name: digest(bundle[name]) for name in STATE_PATHS}


def generation(bundle):
    return digest(encoded(hashes(bundle)))


def safe_target(repo, relative):
    target = repo / relative
    for part in [target, *target.parents]:
        if part == repo:
            break
        if part.is_symlink():
            raise RuntimeError(f"symlink forbidden in state path: {relative}")
    if target.exists() and not target.is_file():
        raise RuntimeError(f"state path is not a regular file: {relative}")
    return target


def read_working_bundle(repo):
    bundle = {}
    for relative in STATE_PATHS:
        path = safe_target(repo, relative)
        if not path.is_file():
            raise RuntimeError(f"missing required state path: {relative}")
        if path.stat().st_size > MAX_FILE_BYTES:
            raise RuntimeError(f"state path exceeds byte limit: {relative}")
        bundle[relative] = path.read_bytes()
    return bundle


def read_blob(repo, commit, relative, *, optional=False):
    line = git(repo, "ls-tree", commit, "--", relative).decode().strip()
    if not line:
        if optional:
            return None
        raise RuntimeError(f"missing required state path: {relative}")
    mode, kind, oid = line.split("\t", 1)[0].split()
    if mode not in {"100644", "100755"} or kind != "blob":
        raise RuntimeError(f"non-regular state blob: {relative}")
    if int(git(repo, "cat-file", "-s", oid)) > MAX_FILE_BYTES:
        raise RuntimeError(f"state blob exceeds byte limit: {relative}")
    return git(repo, "cat-file", "blob", oid)


def read_checkpoint(repo, commit):
    bundle = {name: read_blob(repo, commit, name) for name in STATE_PATHS}
    raw = read_blob(repo, commit, MANIFEST, optional=True)
    manifest = json.loads(raw) if raw is not None else None
    if manifest is not None:
        if (manifest.get("schema_version") != 1 or
                manifest.get("state") not in {"PENDING_PUBLICATION", "PUBLISHED"} or
                manifest.get("files") != hashes(bundle) or
                manifest.get("generation_id") != generation(bundle)):
            raise RuntimeError("checkpoint manifest/hash mismatch")
        if manifest["state"] == "PUBLISHED":
            proof = manifest.get("http_verification", {})
            expected = {p: hashes(bundle)[p] for p in PUBLIC_PATHS}
            if proof.get("verified_files") != expected or proof.get("generation_id") != generation(bundle):
                raise RuntimeError("published checkpoint lacks matching public-data receipt")
    return bundle, manifest


def receipt_path(repo):
    path = Path(git(repo, "rev-parse", "--git-path", "govintel-restored-checkpoint.json").decode().strip())
    return path if path.is_absolute() else repo / path


def write_receipt(repo, commit, bundle):
    path = receipt_path(repo)
    path.write_bytes(encoded({"schema_version": 1, "branch": STATE_BRANCH,
                              "commit": commit, "generation_id": generation(bundle)}))


def emit_outputs(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")


def restore(repo, branch):
    commit = fetch_state_branch(repo, branch)
    receipt_path(repo).unlink(missing_ok=True)
    # Fetch and validate the ENTIRE pinned checkpoint before touching any path.
    bundle, manifest = read_checkpoint(repo, commit)
    targets = {name: safe_target(repo, name) for name in STATE_PATHS}
    for name, path in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bundle[name])
    # Written last. A crash/partial local write never permits a subsequent persist.
    write_receipt(repo, commit, bundle)
    pending = bool(manifest and manifest["state"] == "PENDING_PUBLICATION")
    emit_outputs(pending_recovery=str(pending).lower(), state_commit=commit,
                 generation_id=generation(bundle))
    print(f"PUBLICATION_STATE_RESTORED commit={commit} files={len(bundle)} pending_recovery={pending}")
    return len(bundle)


def commit_bundle(repo, base, replacements, message):
    """Make a data-only commit using a temporary index, then fast-forward push.

    No checkout/worktree from the state branch is executed. The commit parent is
    the restored baseline, not a newly adopted remote head. Git rejects races.
    """
    with tempfile.TemporaryDirectory(prefix="govintel-state-index-") as directory:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(directory) / "index")}
        run(["git", "read-tree", base], repo, capture=True, env=env)
        for path, value in replacements.items():
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], input=value,
                                  cwd=repo, check=True, capture_output=True, timeout=30).stdout.decode().strip()
            run(["git", "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"],
                repo, capture=True, env=env)
        tree = run(["git", "write-tree"], repo, capture=True, env=env).stdout.decode().strip()
        env.update({"GIT_AUTHOR_NAME": "github-actions[bot]",
                    "GIT_AUTHOR_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com",
                    "GIT_COMMITTER_NAME": "github-actions[bot]",
                    "GIT_COMMITTER_EMAIL": "41898282+github-actions[bot]@users.noreply.github.com"})
        head = run(["git", "commit-tree", tree, "-p", base, "-m", message],
                   repo, capture=True, env=env).stdout.decode().strip()
        git(repo, "push", "origin", f"{head}:refs/heads/{STATE_BRANCH}")
        return head


def persist(repo, branch, run_id, run_attempt, slot):
    validate_branch(branch)
    for value in (run_id, run_attempt, slot):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", value):
            raise RuntimeError("invalid run/attempt/slot identifier")
    bundle = read_working_bundle(repo)
    path = receipt_path(repo)
    if not path.is_file():
        raise RuntimeError("restore receipt required before persist")
    receipt = json.loads(path.read_bytes())
    baseline = receipt.get("commit", "")
    if receipt.get("branch") != branch or not SHA_RE.fullmatch(baseline):
        raise RuntimeError("invalid restore receipt")
    remote = fetch_state_branch(repo, branch)
    previous, manifest = read_checkpoint(repo, remote)
    same = previous == bundle
    # Permit a byte-identical retry, but never rebase old data over newer data.
    if remote != baseline and not (same and manifest is not None):
        raise RuntimeError("state advanced since restore; refusing stale overwrite")
    if manifest is not None and same:
        write_receipt(repo, remote, bundle)
        emit_outputs(state_commit=remote, generation_id=generation(bundle))
        print(f"PUBLICATION_STATE_UNCHANGED commit={remote} state={manifest['state']}")
        return False
    if manifest is not None and manifest["state"] == "PENDING_PUBLICATION":
        raise RuntimeError("pending publication must be replayed before new collection")
    manifest = {"schema_version": 1, "generation_id": generation(bundle),
                "files": hashes(bundle), "state": "PENDING_PUBLICATION",
                "producer_run_id": run_id, "producer_attempt": run_attempt, "slot": slot,
                "code_sha": git(repo, "rev-parse", "HEAD").decode().strip()}
    head = commit_bundle(repo, baseline, {**bundle, MANIFEST: encoded(manifest)},
                         f"data: pending publication {slot} run {run_id}.{run_attempt}")
    write_receipt(repo, head, bundle)
    emit_outputs(state_commit=head, generation_id=generation(bundle))
    print(f"PUBLICATION_STATE_PERSISTED commit={head} state=PENDING_PUBLICATION")
    return True


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_public_files(base_url, bundle, *, attempts=3, delay=2, allow_loopback=False):
    parts = urlsplit(base_url)
    is_public = (parts.scheme == "https" and parts.netloc.lower() == "reese-max.github.io" and
                 parts.path.rstrip("/") == "/taichung-police-intel")
    is_test = (allow_loopback and parts.scheme == "http" and parts.hostname == "127.0.0.1")
    if not (is_public or is_test) or parts.username or parts.password or parts.query or parts.fragment:
        raise RuntimeError("public verification URL is not the approved Pages origin/path")
    opener = build_opener(NoRedirect)
    expected = {p: digest(bundle[p]) for p in PUBLIC_PATHS}
    last_error = "no probe"
    for attempt in range(attempts):
        verified = {}
        try:
            for relative, wanted in expected.items():
                suffix = relative.removeprefix("apps/web/public/")
                with opener.open(base_url.rstrip("/") + "/" + suffix, timeout=15) as response:
                    data = response.read(MAX_FILE_BYTES + 1)
                    if response.status != 200 or len(data) > MAX_FILE_BYTES or digest(data) != wanted:
                        raise RuntimeError(f"public data hash/status mismatch: {suffix}")
                    verified[relative] = digest(data)
            return {"generation_id": generation(bundle), "verified_files": verified,
                    "verified_at": datetime.now(timezone.utc).isoformat(),
                    "base_url": base_url, "attempt": attempt + 1}
        except (OSError, URLError, RuntimeError) as error:
            last_error = str(error)
            if attempt + 1 < attempts:
                time.sleep(delay)
    raise RuntimeError(f"public data verification failed: {last_error}")


def acknowledge(repo, branch, expected_commit, expected_generation, base_url, *, verifier=None):
    validate_branch(branch)
    if not SHA_RE.fullmatch(expected_commit):
        raise RuntimeError("invalid expected commit")
    remote = fetch_state_branch(repo, branch)
    if remote != expected_commit:
        raise RuntimeError("state advanced before acknowledgement")
    bundle, manifest = read_checkpoint(repo, remote)
    if manifest is None or generation(bundle) != expected_generation:
        raise RuntimeError("acknowledgement generation mismatch")
    proof = (verifier or verify_public_files)(base_url, bundle)
    expected_files = {p: digest(bundle[p]) for p in PUBLIC_PATHS}
    if proof.get("generation_id") != expected_generation or proof.get("verified_files") != expected_files:
        raise RuntimeError("public-data verifier returned incomplete proof")
    # Recheck after network I/O. Non-fast-forward push also guards a later race.
    if fetch_state_branch(repo, branch) != remote:
        raise RuntimeError("state advanced during public verification")
    if manifest["state"] != "PUBLISHED":
        manifest = {**manifest, "state": "PUBLISHED", "http_verification": proof}
        remote = commit_bundle(repo, remote, {MANIFEST: encoded(manifest)},
                               f"data: acknowledge public generation {expected_generation[:16]}")
    emit_outputs(state_commit=remote, generation_id=expected_generation, public_verified="true")
    print("PUBLIC_DATA_VERIFIED " + json.dumps(proof, sort_keys=True))
    return proof


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("restore", "persist", "acknowledge"))
    parser.add_argument("--repo", default=".")
    parser.add_argument("--branch", default=STATE_BRANCH)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID", "local"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT", "1"))
    parser.add_argument("--slot", default=os.environ.get("PUBLICATION_SLOT", "code"))
    parser.add_argument("--expected-commit", default=os.environ.get("EXPECTED_STATE_COMMIT", ""))
    parser.add_argument("--expected-generation", default=os.environ.get("EXPECTED_GENERATION", ""))
    parser.add_argument("--base-url", default=os.environ.get("PUBLICATION_BASE_URL", ""))
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    if args.command == "restore":
        restore(repo, args.branch)
    elif args.command == "persist":
        persist(repo, args.branch, args.run_id, args.run_attempt, args.slot)
    else:
        acknowledge(repo, args.branch, args.expected_commit, args.expected_generation, args.base_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
